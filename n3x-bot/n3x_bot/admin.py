"""Role-gated admin CRUD for stats and messages.

Discord-free async helpers (`admin_*`) do the real work through `repo`, so
they're unit-testable in isolation (mirroring `build_output`). The `/admin ...`
slash group is a thin wrapper that gates on `is_admin` and delegates to these
helpers, wired via `register_admin_commands`.
"""
import re

from discord import app_commands

from n3x_bot.config import Settings
from n3x_bot.models import Message, Stat
from n3x_bot.storage.base import StatsRepository

# Stat keys that collide with existing bot commands. Creating a stat under one
# of these writes a row but never registers a usable `!<key>` command (the
# existing command shadows it via `bot.get_command`), so refuse them up front.
RESERVED_STAT_KEYS = frozenset({"stat", "del", "admin", "rank", "help"})

# A stat key becomes a Discord app-command name (`app_commands.Command(name=key)`),
# which must be 1-32 chars of lowercase letters, digits, dash or underscore.
# Validate up front so an invalid key can't write an orphan DB row that never
# gets a usable command (the Command constructor would otherwise raise AFTER the
# row is persisted, leaving it unrecoverable except by archive/rm).
_STAT_KEY_RE = re.compile(r"[a-z0-9_-]{1,32}")


def _validate_stat_key(key: str) -> None:
    if not _STAT_KEY_RE.fullmatch(key):
        raise ValueError(
            f"Ungültiger Stat-Key {key!r}: nur Kleinbuchstaben, Ziffern, "
            "'-' und '_', 1-32 Zeichen erlaubt")


def is_admin(member, settings: Settings) -> bool:
    return bool(settings.admin_role_id) and any(
        r.id == settings.admin_role_id for r in getattr(member, "roles", []))


def app_is_admin(interaction, settings: Settings) -> bool:
    return bool(settings.admin_role_id) and any(
        r.id == settings.admin_role_id
        for r in getattr(interaction.user, "roles", []))


async def _resolve_message_id(repo: StatsRepository, name: str) -> int:
    for message in await repo.list_messages(include_archived=True):
        if message.name == name:
            return message.id
    raise ValueError(f"no message named {name!r}")


# ── stat helpers ─────────────────────────────────────────────────────────────

async def admin_create_stat(bot, repo: StatsRepository, settings: Settings,
                            key: str, name: str, targeted: bool = False,
                            message_name: str | None = None) -> Stat:
    if key in RESERVED_STAT_KEYS:
        raise ValueError(f"stat key {key!r} is reserved")
    _validate_stat_key(key)
    existing = await repo.get_stat(key)
    if existing is not None and existing.archived_at is None:
        raise ValueError(f"stat {key!r} already exists")
    message_id = await _resolve_message_id(repo, message_name) if message_name else None

    if existing is not None:
        # Archived key: reactivate in place instead of dead-ending. `targeted`
        # is preserved (the repo has no toggle; consistent with the no-toggle
        # rule in `admin_edit_stat`), so the live command matches the DB row.
        await repo.update_stat(key, name=name)
        await repo.set_stat_message(key, message_id)
        await repo.unarchive_stat(key)
        stat = await repo.get_stat(key)
        targeted = stat.targeted
    else:
        stat = await repo.create_stat(key, name, message_id=message_id, targeted=targeted)

    # Deferred to break the bot <-> admin import cycle: bot.py imports this
    # module at top level, so admin.py must not import bot at module scope.
    import n3x_bot.bot as botmod
    if targeted:
        botmod._add_targeted_stat_command(bot, repo, settings, key)
    else:
        botmod._add_stat_command(bot, repo, settings, key)
    await botmod.resync_stat_commands(bot)
    return stat


async def admin_edit_stat(bot, repo: StatsRepository, settings: Settings,
                          key: str, name: str | None = None,
                          message_name: str | None = None) -> Stat:
    if await repo.get_stat(key) is None:
        raise ValueError(f"stat {key!r} does not exist")
    if name is None and message_name is None:
        raise ValueError("nothing to edit: provide a name and/or a message")
    if name is not None:
        await repo.update_stat(key, name=name)
    if message_name is not None:
        await repo.set_stat_message(key, await _resolve_message_id(repo, message_name))
    return await repo.get_stat(key)


async def admin_archive_stat(bot, repo: StatsRepository, settings: Settings,
                             key: str) -> None:
    await repo.archive_stat(key)
    import n3x_bot.bot as botmod
    bot.tree.remove_command(key)
    await botmod.resync_stat_commands(bot)


async def admin_delete_stat(bot, repo: StatsRepository, settings: Settings,
                            key: str) -> None:
    await repo.delete_stat(key)
    import n3x_bot.bot as botmod
    bot.tree.remove_command(key)
    await botmod.resync_stat_commands(bot)


async def admin_list_stats(repo: StatsRepository,
                           include_archived: bool = False) -> list[Stat]:
    return await repo.list_stats(include_archived=include_archived)


# ── message helpers ──────────────────────────────────────────────────────────

async def admin_create_message(repo: StatsRepository, name: str,
                               template: str) -> Message:
    for message in await repo.list_messages(include_archived=True):
        if message.name == name:
            raise ValueError(f"message {name!r} already exists")
    return await repo.create_message(name, template)


async def admin_edit_message(repo: StatsRepository, message_id: int,
                             name: str | None = None,
                             template: str | None = None) -> Message:
    return await repo.update_message(message_id, name=name, template=template)


async def admin_archive_message(repo: StatsRepository, message_id: int) -> None:
    await repo.archive_message(message_id)


async def admin_delete_message(repo: StatsRepository, message_id: int) -> None:
    await repo.delete_message(message_id)


async def admin_list_messages(repo: StatsRepository,
                              include_archived: bool = False) -> list[Message]:
    return await repo.list_messages(include_archived=include_archived)


# ── command wiring ───────────────────────────────────────────────────────────

def register_admin_commands(bot, repo: StatsRepository, settings: Settings) -> None:
    _register_slash_commands(bot, repo, settings)


def _register_slash_commands(bot, repo: StatsRepository, settings: Settings) -> None:
    admin_g = app_commands.Group(name="admin", description="Admin CRUD")
    stat_g = app_commands.Group(name="stat", description="Stat CRUD", parent=admin_g)
    msg_g = app_commands.Group(name="msg", description="Message CRUD", parent=admin_g)

    @stat_g.command(name="add")
    async def slash_stat_add(interaction, key: str, name: str,
                             targeted: bool = False, message: str | None = None):
        if not is_admin(interaction.user, settings):
            await interaction.response.send_message("❌ Keine Berechtigung.",
                                                    ephemeral=True)
            return
        await admin_create_stat(bot, repo, settings, key, name,
                                targeted=targeted, message_name=message)
        await interaction.response.send_message(f"✅ Stat `{key}` erstellt.",
                                                ephemeral=True)

    @stat_g.command(name="edit")
    async def slash_stat_edit(interaction, key: str, name: str | None = None,
                              message: str | None = None):
        if not is_admin(interaction.user, settings):
            await interaction.response.send_message("❌ Keine Berechtigung.",
                                                    ephemeral=True)
            return
        await admin_edit_stat(bot, repo, settings, key, name=name,
                              message_name=message)
        await interaction.response.send_message(f"✅ Stat `{key}` aktualisiert.",
                                                ephemeral=True)

    @stat_g.command(name="archive")
    async def slash_stat_archive(interaction, key: str):
        if not is_admin(interaction.user, settings):
            await interaction.response.send_message("❌ Keine Berechtigung.",
                                                    ephemeral=True)
            return
        await admin_archive_stat(bot, repo, settings, key)
        await interaction.response.send_message(f"✅ Stat `{key}` archiviert.",
                                                ephemeral=True)

    @stat_g.command(name="rm")
    async def slash_stat_rm(interaction, key: str):
        if not is_admin(interaction.user, settings):
            await interaction.response.send_message("❌ Keine Berechtigung.",
                                                    ephemeral=True)
            return
        await admin_delete_stat(bot, repo, settings, key)
        await interaction.response.send_message(f"✅ Stat `{key}` gelöscht.",
                                                ephemeral=True)

    @stat_g.command(name="list")
    async def slash_stat_list(interaction):
        if not is_admin(interaction.user, settings):
            await interaction.response.send_message("❌ Keine Berechtigung.",
                                                    ephemeral=True)
            return
        stats = await admin_list_stats(repo)
        text = "\n".join(f"`{s.key}` — {s.name}" for s in stats) or "Keine Stats."
        await interaction.response.send_message(text, ephemeral=True)

    @msg_g.command(name="add")
    async def slash_msg_add(interaction, name: str, template: str):
        if not is_admin(interaction.user, settings):
            await interaction.response.send_message("❌ Keine Berechtigung.",
                                                    ephemeral=True)
            return
        await admin_create_message(repo, name, template)
        await interaction.response.send_message(f"✅ Nachricht `{name}` erstellt.",
                                                ephemeral=True)

    @msg_g.command(name="edit")
    async def slash_msg_edit(interaction, message_id: int, name: str | None = None,
                             template: str | None = None):
        if not is_admin(interaction.user, settings):
            await interaction.response.send_message("❌ Keine Berechtigung.",
                                                    ephemeral=True)
            return
        await admin_edit_message(repo, message_id, name=name, template=template)
        await interaction.response.send_message(
            f"✅ Nachricht `{message_id}` aktualisiert.", ephemeral=True)

    @msg_g.command(name="archive")
    async def slash_msg_archive(interaction, message_id: int):
        if not is_admin(interaction.user, settings):
            await interaction.response.send_message("❌ Keine Berechtigung.",
                                                    ephemeral=True)
            return
        await admin_archive_message(repo, message_id)
        await interaction.response.send_message(
            f"✅ Nachricht `{message_id}` archiviert.", ephemeral=True)

    @msg_g.command(name="rm")
    async def slash_msg_rm(interaction, message_id: int):
        if not is_admin(interaction.user, settings):
            await interaction.response.send_message("❌ Keine Berechtigung.",
                                                    ephemeral=True)
            return
        await admin_delete_message(repo, message_id)
        await interaction.response.send_message(
            f"✅ Nachricht `{message_id}` gelöscht.", ephemeral=True)

    @msg_g.command(name="list")
    async def slash_msg_list(interaction):
        if not is_admin(interaction.user, settings):
            await interaction.response.send_message("❌ Keine Berechtigung.",
                                                    ephemeral=True)
            return
        messages = await admin_list_messages(repo)
        text = "\n".join(f"`{m.id}` {m.name} — {m.template}" for m in messages) \
            or "Keine Nachrichten."
        await interaction.response.send_message(text, ephemeral=True)

    bot.tree.add_command(admin_g)
