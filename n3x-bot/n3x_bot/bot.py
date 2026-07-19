import asyncio
import logging
from datetime import datetime, time
from io import BytesIO
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from n3x_bot.admin import (  # noqa: F401  (admin_* re-exported for tests)
    is_admin, app_is_admin,
    admin_create_stat, admin_edit_stat, admin_archive_stat,
    admin_delete_stat, admin_list_stats,
    admin_create_message, admin_edit_message, admin_archive_message,
    admin_delete_message, admin_list_messages,
    register_admin_commands,
)
from n3x_bot.activity import (
    register_activity,
    record_message_activity,
    handle_voice_state_update,
    announce_voice_change,
    handle_activity_reaction,
    flush_voice_times,
    now_local,
)
from n3x_bot.achievements import (
    register_achievement_commands, check_achievements,
    post_overview, OverviewView, sync_all_achievements,
)
from n3x_bot.cards import announce_achievements
from n3x_bot.config import Settings
from n3x_bot.config_commands import register_config_commands
from n3x_bot.achievement_commands import register_achievement_def_commands
from n3x_bot.achievement_defs import AchievementDefs
from n3x_bot.colors import ColorConfig
from n3x_bot.content import ContentTexts
from n3x_bot.content_commands import register_content_commands
from n3x_bot.format import format_number
from n3x_bot.charts import render_gate_history_chart
from n3x_bot.gates import (
    build_gate_embed, parse_gate_message, changed_records, GATE_NAMES,
    parse_de_date, resolve_drop_emoji, GATE_DROP_REACTION_ITEMS,
    DROP_NOTHING_EMOJI, _DROP_LABELS,
)
from n3x_bot.kodex import (
    register_kodex_commands, send_kodex_dm, handle_kodex_confirmation,
)
from n3x_bot.models import render_output
from n3x_bot.nicknames import enforce_nick
from n3x_bot.runtime_config import RuntimeConfig
from n3x_bot.storage.base import GATE_TYPES, StatsRepository
from n3x_bot.timers import (
    register_timer_commands, start_timer_overview_loop, update_timer_overview,
)
from n3x_bot.welcome import register_welcome_commands, send_welcome_card

log = logging.getLogger("N3X-Bot")

HOME_KEY = "home"
GATE_STAT_CHUNK_LIMIT = 1900


async def build_output(repo: StatsRepository, stat_key: str,
                       discord_id: int, display_name: str) -> str:
    # Track & display the INVOKER's own count (user_count), not the global
    # total, and mention them via {user} so the message names who triggered it.
    user_count, total = await repo.record_use(discord_id, display_name, stat_key)
    stat = await repo.get_stat(stat_key)
    message = None
    if stat.message_id is not None:
        message = await repo.get_message(stat.message_id)
    return render_output(stat, message, f"<@{discord_id}>", user_count)


async def build_target_output(repo: StatsRepository, stat_key: str,
                              invoker_id: int, invoker_display: str,
                              target_id: int, target_display: str) -> str:
    """Render output for a targeted stat (e.g. `!smart @user`).

    The invoker's own `user_stats` is updated via `record_use` (as today),
    while the target's separate counter is updated via `record_target_use`.
    The rendered count reflects the TARGET's count, not the invoker's.
    """
    await repo.record_use(invoker_id, invoker_display, stat_key)
    count = await repo.record_target_use(target_id, stat_key)
    stat = await repo.get_stat(stat_key)
    message = None
    if stat.message_id is not None:
        message = await repo.get_message(stat.message_id)
    return render_output(stat, message, f"<@{invoker_id}>", count,
                         target_display=target_display)


def build_bot(settings: Settings, repo: StatsRepository) -> commands.Bot:
    intents = discord.Intents.default()
    intents.members = True
    intents.guilds = True
    intents.message_content = True

    bot = commands.Bot(command_prefix=settings.command_prefix,
                       intents=intents, case_insensitive=True)
    bot.n3x_settings = settings
    bot.n3x_repo = repo
    bot.runtime_config = RuntimeConfig(settings)
    bot.content_texts = ContentTexts()
    bot.colors = ColorConfig()
    bot.achievement_defs = AchievementDefs()
    bot._rank_last_posts = {}
    bot._target_last_posts = {}
    bot._gate_embed_msg_id = None
    bot._pending_gate = {}
    bot._verlauf_msgs = {}
    bot._milestone_cards = {}
    # Single-guild bot (N3X): voice_join_times is keyed by member.id alone.
    # voice_lock serialises the flush task against the leave/move handler so
    # they can't double-count or leave a phantom session behind.
    bot.voice_join_times = {}
    bot.voice_lock = asyncio.Lock()
    bot._overview_state = None
    bot._timer_overview_loop = None
    # One-shot guard: the stale-guild-command clear runs once per process, not
    # on every on_ready (which re-fires on each gateway reconnect). Self-heals
    # on a real restart. Set True only after a successful clear (see on_ready).
    bot._stale_guild_commands_cleared = False

    _wire_events(bot, settings, repo)
    register_gate_commands(bot, repo, settings)
    register_admin_commands(bot, repo, settings)
    register_config_commands(bot, repo, settings)
    register_content_commands(bot, repo, settings)
    register_activity(bot, repo, settings)
    register_achievement_commands(bot, repo, settings)
    register_achievement_def_commands(bot, repo, settings)
    register_overview_and_sync_commands(bot, repo, settings)
    register_kodex_commands(bot, repo, settings)
    register_welcome_commands(bot, settings)
    register_timer_commands(bot, repo, settings)
    return bot


def register_overview_and_sync_commands(bot, repo: StatsRepository,
                                        settings: Settings) -> None:
    if bot.tree.get_command("overview") is None:
        @bot.tree.command(name="overview",
                          description="Postet die Achievement-Übersicht.")
        async def overview(interaction):
            await interaction.response.defer(ephemeral=True)
            await post_overview(bot, repo, settings)
            await interaction.followup.send("Übersicht aktualisiert.",
                                             ephemeral=True)

    if bot.tree.get_command("sync_achievements") is None:
        @bot.tree.command(name="sync_achievements",
                          description="Synchronisiert alle Achievements (Admin).")
        async def sync_achievements(interaction):
            if not is_admin(interaction.user, settings):
                await interaction.response.send_message(
                    "❌ Keine Berechtigung.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            summary = await sync_all_achievements(
                repo, defs=bot.achievement_defs.all())
            await interaction.followup.send(
                f"✅ Sync: {summary['users_processed']} Nutzer, "
                f"{summary['achievements_added']} neue Achievements.",
                ephemeral=True)


async def _send_or_update(bot, repo, settings, stat_key: str, text: str):
    channel = bot.get_channel(bot.runtime_config.reminder_channel_id)
    if channel is None:
        return
    last = await repo.get_last_post(stat_key)
    if last is not None:
        old_id, _ = last
        try:
            old = await channel.fetch_message(old_id)
            await old.delete()
        except Exception:
            pass
    new_msg = await channel.send(text)
    await repo.set_last_post(stat_key, new_msg.id, channel.id)


async def _send_ephemeral(bot, settings, store: dict, track_key: str, text: str):
    """Send/update an ephemeral output without touching `stat_last_post`.

    Some display keys are not real stat rows (e.g. `rank_<user_id>`, or a
    per-target key like `smart_<member_id>` for a targeted stat command),
    so they cannot go through `_send_or_update` / `repo.set_last_post` —
    `stat_last_post` is FK-bound to `stats` and would raise `KeyError` for
    an unknown key. Instead, track the last posted message id in-memory in
    `store` for the "delete previous, send new" behavior, since these
    displays don't need DB persistence.
    """
    channel = bot.get_channel(bot.runtime_config.reminder_channel_id)
    if channel is None:
        return
    old_id = store.get(track_key)
    if old_id is not None:
        try:
            old = await channel.fetch_message(old_id)
            await old.delete()
        except Exception:
            pass
    new_msg = await channel.send(text)
    store[track_key] = new_msg.id


async def _send_rank(bot, settings, rank_key: str, text: str):
    await _send_ephemeral(bot, settings, bot._rank_last_posts, rank_key, text)


async def register_stat_commands(bot, repo: StatsRepository, settings: Settings):
    for stat in await repo.list_stats():
        # Guard per-stat: a single invalid key (e.g. an orphan row from the v3
        # flatfile→SQLite migration) makes app_commands.Command(name=key) raise.
        # Skip+log the bad key so it can't abort on_ready and take down startup.
        try:
            if stat.targeted:
                _add_targeted_stat_command(bot, repo, settings, stat.key)
            else:
                _add_stat_command(bot, repo, settings, stat.key)
        except Exception:
            log.exception("failed to register stat command for key %r; skipping",
                          stat.key)

    if bot.tree.get_command("rank") is None:
        async def _rank_cmd(interaction: discord.Interaction):
            data = await repo.get_user_stats(interaction.user.id)
            mention = f"<@{interaction.user.id}>"
            if not data:
                text = (f"📊 **Command-Ranking von {mention}**\n\n"
                        "Du hast bisher noch keine Befehle genutzt!")
            else:
                ordered = sorted(data.items(), key=lambda x: x[1], reverse=True)
                emojis = ["🥇", "🥈", "🥉"]
                text = f"📊 **Command-Ranking von {mention}**\n\n"
                for i, (cmd, count) in enumerate(ordered):
                    pref = emojis[i] if i < 3 else f"{i+1}."
                    text += f"{pref} /{cmd:<10} {count}\n"
            # Mention pill without a self-ping on every rank check.
            await interaction.response.send_message(
                text, allowed_mentions=discord.AllowedMentions.none())

        bot.tree.add_command(app_commands.Command(
            name="rank", description="Zeigt dein Command-Ranking.",
            callback=_rank_cmd))


def _add_stat_command(bot, repo, settings, key: str):
    if bot.tree.get_command(key) is not None:
        return

    @app_commands.checks.cooldown(1, 20)
    async def _cmd(interaction: discord.Interaction):
        text = await build_output(repo, key, interaction.user.id,
                                  interaction.user.display_name)
        await interaction.response.send_message(text)

    bot.tree.add_command(app_commands.Command(
        name=key, description=f"Zählt {key}.", callback=_cmd))


def _add_targeted_stat_command(bot, repo, settings, key: str):
    """Register a targeted stat command as a slash app command.

    Ordinary targeted stats (e.g. `smart`, `crash`) take an explicit
    `member: discord.Member` option. `HOME_KEY` is special-cased: it has
    a fixed target (`settings.julez_id`) and takes no argument. If
    `settings.julez_id` is unset (0/falsy), `home` is skipped entirely
    rather than registering a broken command with no valid target.
    """
    if bot.tree.get_command(key) is not None:
        return

    if key == HOME_KEY:
        if not settings.julez_id:
            return

        @app_commands.checks.cooldown(1, 20)
        async def _home_cmd(interaction: discord.Interaction):
            text = await build_target_output(
                repo, key, interaction.user.id, interaction.user.display_name,
                settings.julez_id, f"<@{settings.julez_id}>")
            await interaction.response.send_message(text)

        bot.tree.add_command(app_commands.Command(
            name=key, description=f"Zählt {key}.", callback=_home_cmd))
        return

    @app_commands.checks.cooldown(1, 20)
    async def _tcmd(interaction: discord.Interaction, member: discord.Member):
        text = await build_target_output(
            repo, key, interaction.user.id, interaction.user.display_name,
            member.id, member.mention)
        await interaction.response.send_message(text)

    bot.tree.add_command(app_commands.Command(
        name=key, description=f"Zählt {key}.", callback=_tcmd))


# ── gate tracker ─────────────────────────────────────────────────────────────

GATE_STATS_KEY = "gate_stats"
GATE_INPUT_HELP_KEY = "gate_input_help"


def build_gate_input_help() -> discord.Embed:
    """Build the deterministic German Anleitung for entering gate costs.

    Pure/deterministic: covers all seven gates (including the newer
    Delta/Epsilon/Zeta/Kappa drop gates) plus the retained v3 hints. Posted as
    a self-editing embed in the gate-input channel via `update_gate_input_help`.
    """
    description = (
        "Bitte trage deine Gate-Kosten so ein: `<gate> <kosten>`\n"
        "\n"
        "**Beispiele:**\n"
        "`a 58.000`  – Alpha Gate\n"
        "`b 93.800`  – Beta Gate\n"
        "`c 139.500` – Gamma Gate\n"
        "`d 250.000` – Delta Gate (Laser)\n"
        "`e 46.700`  – Epsilon Gate (LF4)\n"
        "`z 66.600`  – Zeta Gate (Havoc)\n"
        "`k 62.900`  – Kappa Gate (Hercules & LF4-U)\n"
        "\n"
        "⚠️ Bitte immer den exakten Wert eintragen!\n"
        "\n"
        "**Hinweise:**\n"
        "• Punkte im Betrag sind optional (58000 oder 58.000)\n"
        "• Alpha, Beta & Gamma werden sofort eingetragen "
        "(✅ = gespeichert · ⏳ = Duplikat innerhalb 30 s)\n"
        "• Delta, Epsilon, Zeta & Kappa: reagiere auf deine eigene Nachricht "
        "mit dem Drop-Icon, das du erhalten hast (oder ❌ für keinen Drop)\n"
        "• Der Bot trägt den Drop dann ein und entfernt anschließend deine "
        "Nachricht (kein ✅)\n"
        "• Nur eine Eingabe pro Nachricht!"
    )
    return discord.Embed(
        title="📝 Anleitung: Gate-Kosten eintragen",
        description=description,
        color=discord.Color.blue(),
    )


async def update_gate_input_help(bot, repo: StatsRepository, settings: Settings):
    """Refresh (or first-post) the self-editing gate-input Anleitung.

    Mirrors `update_gate_stats_embed`: the last-posted message id is persisted
    via the `channel_messages` store under `GATE_INPUT_HELP_KEY` so the help is
    edited in place across restarts. Best-effort; never raises.
    """
    if not bot.runtime_config.gate_input_channel_id:
        return
    channel = bot.get_channel(bot.runtime_config.gate_input_channel_id)
    if channel is None:
        return
    embed = build_gate_input_help()

    stored = await repo.get_channel_message(GATE_INPUT_HELP_KEY)
    if stored is not None:
        try:
            msg = await channel.fetch_message(stored[0])
            await msg.edit(embed=embed)
            return
        except Exception:
            pass

    new_msg = await channel.send(embed=embed)
    await repo.set_channel_message(GATE_INPUT_HELP_KEY, new_msg.id, channel.id)


# ── command-list channel ─────────────────────────────────────────────────────

COMMAND_LIST_KEY = "command_list"

# Curated short German blurbs keyed by command `qualified_name`. The map DRIVES
# the description column of the embed; an unmapped command renders name-only.
_COMMAND_DESCRIPTIONS: dict[str, str] = {
    "rank": "Zeigt dein persönliches Command-Ranking.",
    "erfolge": "Zeigt deine freigeschalteten Achievements.",
    "activity": "Zeigt die Aktivitätsstatistik.",
    "overview": "Postet die Achievement-Übersicht.",
    "sync_achievements": "Synchronisiert alle Achievements (Admin).",
    "stat": "Zeigt die Gate-Statistik.",
    "del": "Löscht einen Gate-Eintrag (Rolle erforderlich).",
    "gate verlauf": "Zeigt den Gate-Kostenverlauf als Diagramm.",
    "base": "Startet einen Base-Timer.",
    "basestop": "Stoppt einen Base-Timer.",
    "kodex": "Sendet den Kodex an alle Mitglieder (Admin).",
    "kodex_check": "Prüft die Kodex-Bestätigungen (Admin).",
    "sync_welcome": "Sendet Willkommenskarten nach (Admin).",
    "config": "Laufzeit-Konfiguration (Admin).",
    "content": "Verwaltet Textbausteine (Admin).",
    "admin": "Admin-Verwaltung (Stats & Nachrichten).",
    "achievement": "Achievement-Definitionen verwalten (Admin).",
}


# Ordered command categories: key -> (emoji, German label). Order is the
# display order of the embed fields.
_COMMAND_CATEGORIES: list[tuple[str, str, str]] = [
    ("gates", "📊", "Gates"),
    ("achievements", "🏆", "Achievements"),
    ("activity", "🎙️", "Aktivität"),
    ("timers", "⏱️", "Base-Timer"),
    ("fun", "🎮", "Fun & Zähler"),
    ("admin", "⚙️", "Admin & Verwaltung"),
]
# Top-level command name -> category key. Anything unmapped (the dynamic
# per-stat counter commands) falls into "fun".
_TOP_LEVEL_CATEGORY: dict[str, str] = {
    "stat": "gates", "del": "gates", "gate": "gates",
    "erfolge": "achievements", "overview": "achievements",
    "achievement": "achievements",
    "activity": "activity",
    "base": "timers", "basestop": "timers",
    "rank": "fun",
    # Admin-gated management + operational commands — hidden behind the
    # admin-only reveal button, not shown on the public list.
    "admin": "admin", "config": "admin", "content": "admin",
    "sync_achievements": "admin", "sync_welcome": "admin",
    "kodex": "admin", "kodex_check": "admin",
}
# Per-command line emoji (top-level qualified name). Falls back to the category
# emoji so every line carries one.
_COMMAND_EMOJI: dict[str, str] = {
    "stat": "📈", "del": "🗑️", "gate": "📉",
    "erfolge": "🎖️", "overview": "🏅", "sync_achievements": "🔄",
    "achievement": "🧩", "activity": "📊", "base": "▶️", "basestop": "⏹️",
    "kodex": "📜", "kodex_check": "✅", "sync_welcome": "👋", "rank": "🥇",
    "admin": "🛠️", "config": "⚙️", "content": "📝",
}
# Categories that are admin-only. These are hidden from the public command-list
# message and revealed only via the ephemeral "Admin-Befehle" button (gated on
# an admin role). Everything else is public.
_ADMIN_CATEGORY_KEYS: set[str] = {"admin"}
# Fixed custom_id for the persistent admin-reveal button (survives restarts).
COMMAND_LIST_ADMIN_BUTTON_ID = "n3x:cmdlist:admin"


def _bucket_commands(bot) -> dict[str, list[tuple[str, str]]]:
    """Tree-walk the live registry into ``category key -> [(qname, line)]``.

    Recurses every ``app_commands.Group`` so `admin > stat > add` renders
    `/admin stat add`; dynamic per-stat counters fall into 🎮 Fun. Each line
    carries a per-command emoji and the curated `_COMMAND_DESCRIPTIONS` blurb.
    """
    cat_emoji_by_key = {key: emoji for key, emoji, _ in _COMMAND_CATEGORIES}
    buckets: dict[str, list[tuple[str, str]]] = {c[0]: [] for c in _COMMAND_CATEGORIES}

    def _emit(cmd, cat, emoji):
        desc = _COMMAND_DESCRIPTIONS.get(cmd.qualified_name, "")
        line = f"{emoji} `/{cmd.qualified_name}`"
        if desc:
            line += f" — {desc}"
        buckets[cat].append((cmd.qualified_name, line))
        if isinstance(cmd, app_commands.Group):
            for sub in sorted(cmd.commands, key=lambda c: c.name):
                _emit(sub, cat, emoji)

    for top in bot.tree.get_commands():
        cat = _TOP_LEVEL_CATEGORY.get(top.name, "fun")
        emoji = _COMMAND_EMOJI.get(top.name, cat_emoji_by_key[cat])
        _emit(top, cat, emoji)
    return buckets


def _render_command_embed(bot, title: str, category_keys, color, footer=None
                          ) -> discord.Embed:
    """Deterministic grouped embed for the given category keys (in display order).

    One embed field per non-empty category (emoji + German label), sorted lines,
    chunked to the 1024-char field limit.
    """
    buckets = _bucket_commands(bot)
    embed = discord.Embed(title=title, color=color)
    for key, cat_emoji, label in _COMMAND_CATEGORIES:
        if key not in category_keys:
            continue
        entries = sorted(buckets.get(key, []))
        if not entries:
            continue
        lines = [line for _, line in entries]
        chunks = _chunk_gate_lines(lines, limit=1024)
        for i, chunk in enumerate(chunks):
            name = f"{cat_emoji} {label}" if i == 0 else "​"
            embed.add_field(name=name, value=chunk, inline=False)
    if footer:
        embed.set_footer(text=footer)
    return embed


def build_command_list(bot) -> discord.Embed:
    """PUBLIC command-list embed — every category EXCEPT the admin block.

    Admin/config/content are hidden here; a footer points to the ephemeral
    "Admin-Befehle" button, which only admins can use.
    """
    public_keys = [k for k, _, _ in _COMMAND_CATEGORIES
                   if k not in _ADMIN_CATEGORY_KEYS]
    return _render_command_embed(
        bot, "📋 Befehlsübersicht", public_keys, discord.Color.blurple(),
        footer="🔧 Admin-Befehle: Button unten (nur für Admins sichtbar).")


def build_admin_command_list(bot) -> discord.Embed:
    """ADMIN-only command-list embed — solely the admin categories."""
    return _render_command_embed(
        bot, "⚙️ Admin-Befehle", _ADMIN_CATEGORY_KEYS, discord.Color.dark_grey())


class CommandListView(discord.ui.View):
    """Persistent view on the public command-list message.

    A single "Admin-Befehle" button reveals the hidden admin block, but only to
    users holding an admin role (`app_is_admin`). The reveal is ephemeral, so a
    non-admin never sees the admin commands even in a shared channel.
    ``bot.add_view(CommandListView(bot, settings))`` in `on_ready` re-attaches
    the callback after a restart.
    """

    def __init__(self, bot, settings: Settings):
        super().__init__(timeout=None)
        self.bot = bot
        self.settings = settings

    @discord.ui.button(label="🔧 Admin-Befehle", style=discord.ButtonStyle.secondary,
                       custom_id=COMMAND_LIST_ADMIN_BUTTON_ID)
    async def admin(self, interaction, button):
        if not app_is_admin(interaction, self.settings):
            await interaction.response.send_message(
                "❌ Diese Befehle sind nur für Admins sichtbar.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=build_admin_command_list(self.bot), ephemeral=True)


async def update_command_list(bot, repo: StatsRepository, settings: Settings):
    """Refresh (or first-post) the self-editing German command-list embed.

    Mirrors `update_gate_input_help`: the last-posted message id is persisted via
    the `channel_messages` store under `COMMAND_LIST_KEY` so the list is edited in
    place across restarts. Best-effort; never raises.
    """
    if not bot.runtime_config.command_list_channel_id:
        return
    channel = bot.get_channel(bot.runtime_config.command_list_channel_id)
    if channel is None:
        return
    embed = build_command_list(bot)
    view = CommandListView(bot, settings)

    stored = await repo.get_channel_message(COMMAND_LIST_KEY)
    if stored is not None:
        try:
            msg = await channel.fetch_message(stored[0])
            await msg.edit(embed=embed, view=view)
            return
        except Exception:
            pass

    new_msg = await channel.send(embed=embed, view=view)
    await repo.set_channel_message(COMMAND_LIST_KEY, new_msg.id, channel.id)


async def update_gate_stats_embed(bot, repo: StatsRepository, settings: Settings):
    """Refresh (or first-post) the live gate-stats embed.

    The last-posted message id is persisted via the `channel_messages` store
    under `GATE_STATS_KEY` so the embed is edited in place across restarts
    (it can't use `set_last_post`, which is FK-bound to a real `stats` row and
    "gate" isn't one — it would KeyError). `bot._gate_embed_msg_id` stays as an
    in-run fast-path cache, written alongside the persisted store.
    """
    if not bot.runtime_config.gate_stats_channel_id:
        return
    channel = bot.get_channel(bot.runtime_config.gate_stats_channel_id)
    if channel is None:
        return
    totals = await repo.gate_totals()
    rewards = bot.runtime_config.gate_rewards_map()
    delta = await repo.delta_stats()
    epsilon = await repo.gate_drop_stats("e")
    zeta = await repo.gate_drop_stats("z")
    kappa = await repo.gate_drop_stats("k")
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    embed = build_gate_embed(totals, rewards, now_str, delta,
                             epsilon=epsilon, zeta=zeta, kappa=kappa)

    # Resolve the target message id: in-run cache first (fast path), else the
    # persisted store (survives restart).
    target_id = bot._gate_embed_msg_id
    if target_id is None:
        stored = await repo.get_channel_message(GATE_STATS_KEY)
        if stored is not None:
            target_id = stored[0]

    if target_id is not None:
        try:
            msg = await channel.fetch_message(target_id)
            await msg.edit(embed=embed)
            # Seed/ensure the 🔄 reload control on the EXISTING message too
            # (idempotent — Discord dedups the bot's own reaction).
            try:
                await msg.add_reaction("🔄")
            except Exception:
                pass
            bot._gate_embed_msg_id = target_id
            return
        except Exception:
            pass

    new_msg = await channel.send(embed=embed)
    bot._gate_embed_msg_id = new_msg.id
    await repo.set_channel_message(GATE_STATS_KEY, new_msg.id, channel.id)
    # Seed the 🔄 reload control so users can force an immediate refresh.
    try:
        await new_msg.add_reaction("🔄")
    except Exception:
        pass


async def update_gate_chart(bot, repo: StatsRepository, settings: Settings,
                            gate_type: str) -> None:
    """Refresh (or first-post) the live per-gate history chart IMAGE.

    Mirrors `update_gate_stats_embed`, but the tracked message is an
    ATTACHMENT: it is edited via `msg.edit(attachments=[discord.File(...)])`
    and (re)posted via `channel.send(file=discord.File(...))`. The last-posted
    message id is persisted via the `channel_messages` store under
    `f"gate_chart_{gate_type}"` so the chart is edited in place across restarts.
    Best-effort; never raises.
    """
    if not bot.runtime_config.gate_chart_channel_id:
        return
    channel = bot.get_channel(bot.runtime_config.gate_chart_channel_id)
    if channel is None:
        return
    try:
        png = render_gate_history_chart(
            gate_type, await repo.list_gate_entries(gate_type), now_local(settings))
        filename = f"verlauf_{gate_type}.png"
        key = f"gate_chart_{gate_type}"

        stored = await repo.get_channel_message(key)
        if stored is not None:
            try:
                msg = await channel.fetch_message(stored[0])
                await msg.edit(attachments=[discord.File(BytesIO(png),
                                                         filename=filename)])
                return
            except Exception:
                pass

        new_msg = await channel.send(file=discord.File(BytesIO(png),
                                                       filename=filename))
        await repo.set_channel_message(key, new_msg.id, channel.id)
    except Exception:
        log.exception("gate-chart update failed")


async def update_all_gate_charts(bot, repo: StatsRepository,
                                 settings: Settings) -> None:
    """Refresh every gate's live chart. One gate failing (module-global
    `update_gate_chart` may be monkeypatched) never stops the others."""
    for gate_type in GATE_TYPES:
        try:
            await update_gate_chart(bot, repo, settings, gate_type)
        except Exception:
            pass


def _chunk_gate_lines(lines: list[str], limit: int = GATE_STAT_CHUNK_LIMIT) -> list[str]:
    chunks = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 2 > limit:
            chunks.append(current)
            current = ""
        current += line + "\n"
    chunks.append(current)
    return chunks


GATE_CHOICES = [app_commands.Choice(name=GATE_NAMES[g], value=g)
                for g in GATE_TYPES]


def build_gate_stat_embeds(gate_type: str, costs: list[int]) -> list[discord.Embed]:
    title = f"📊 {gate_type.upper()} Gate"
    lines = [f"{i}. {format_number(cost)}" for i, cost in enumerate(costs, 1)]
    embeds = []
    for i, chunk in enumerate(_chunk_gate_lines(lines)):
        embeds.append(discord.Embed(
            title=title if i == 0 else f"{title} (Fortsetzung)",
            description=chunk, color=discord.Color.green()))
    return embeds


async def _handle_gate_stat(ctx, repo: StatsRepository, settings: Settings, gate_type: str):
    gtype = gate_type.lower()
    if gtype not in GATE_TYPES:
        await ctx.send("Ungültiger Gate-Typ. Bitte nutze a, b, c, d, e, z oder k.", delete_after=5)
        return
    costs = await repo.list_gate_costs(gtype)
    if not costs:
        await ctx.send(f"Noch keine Daten für {gate_type.upper()} Gate vorhanden.", delete_after=5)
        return
    for embed in build_gate_stat_embeds(gtype, costs):
        await ctx.send(embed=embed)


async def apply_gate_delete(bot, repo: StatsRepository, settings: Settings,
                            gate_type: str, index: int) -> bool:
    if await repo.delete_gate_entry(gate_type.lower(), index):
        await update_gate_stats_embed(bot, repo, settings)
        return True
    return False


def register_gate_commands(bot, repo: StatsRepository, settings: Settings):
    if bot.tree.get_command("stat") is None:
        @bot.tree.command(name="stat",
                          description="Zeigt die erfassten Kosten eines Gates.")
        @app_commands.describe(gate="Welches Gate?")
        @app_commands.choices(gate=GATE_CHOICES)
        async def gate_stat(interaction, gate: str):
            gtype = gate.lower()
            costs = await repo.list_gate_costs(gtype)
            if not costs:
                await interaction.response.send_message(
                    f"Noch keine Daten für {gtype.upper()} Gate vorhanden.")
                return
            embeds = build_gate_stat_embeds(gtype, costs)
            await interaction.response.send_message(embed=embeds[0])
            for extra in embeds[1:]:
                await interaction.followup.send(embed=extra)

    if bot.tree.get_command("del") is None:
        @bot.tree.command(name="del",
                          description="Löscht einen Gate-Eintrag (nur mit Berechtigung).")
        @app_commands.describe(gate="Welches Gate?",
                               index="Nummer des zu löschenden Eintrags")
        @app_commands.choices(gate=GATE_CHOICES)
        async def gate_del(interaction, gate: str, index: int):
            await interaction.response.defer(ephemeral=True)
            roles = getattr(interaction.user, "roles", [])
            has_role = any(r.id in bot.runtime_config.gate_delete_role_ids
                           for r in roles)
            if not has_role:
                await interaction.followup.send(
                    "❌ Keine Berechtigung.", ephemeral=True)
                return
            gtype = gate.lower()
            if await apply_gate_delete(bot, repo, settings, gtype, index):
                await interaction.followup.send(
                    f"✅ Eintrag {index} für {gtype.upper()} gelöscht.",
                    ephemeral=True)
            else:
                await interaction.followup.send(
                    f"❌ Eintrag {index} nicht gefunden.", ephemeral=True)

    if bot.tree.get_command("gate") is None:
        gate_group = app_commands.Group(name="gate",
                                        description="Gate-Auswertungen.")

        @gate_group.command(name="verlauf",
                            description="Zeigt den Preisverlauf eines Gates als Diagramm.")
        @app_commands.describe(gate="Welches Gate?",
                               von="Startdatum (TT.MM.JJJJ)",
                               bis="Enddatum (TT.MM.JJJJ)")
        @app_commands.choices(gate=GATE_CHOICES)
        async def gate_verlauf(interaction, gate: str, von: str | None = None,
                               bis: str | None = None):
            gtype = gate.lower()
            von_d = bis_d = None
            for raw, is_von in ((von, True), (bis, False)):
                if raw is None:
                    continue
                parsed = parse_de_date(raw)
                if parsed is None:
                    await interaction.response.send_message(
                        "❌ Ungültiges Datum. Nutze TT.MM.JJJJ oder JJJJ-MM-TT.",
                        ephemeral=True)
                    return
                if is_von:
                    von_d = parsed
                else:
                    bis_d = parsed
            tz = ZoneInfo(settings.timezone)
            since = (datetime.combine(von_d, time(0, 0, 0), tzinfo=tz)
                     if von_d is not None else None)
            until = (datetime.combine(bis_d, time(23, 59, 59, 999999), tzinfo=tz)
                     if bis_d is not None else None)
            await interaction.response.defer()
            entries = await repo.list_gate_entries(gtype, since, until)
            png = render_gate_history_chart(gtype, entries,
                                            now_local(settings), von_d, bis_d)
            msg = await interaction.followup.send(
                file=discord.File(BytesIO(png), filename=f"verlauf_{gtype}.png"))
            try:
                await msg.add_reaction("❌")
            except Exception:
                pass
            bot._verlauf_msgs[msg.id] = interaction.user.id

        bot.tree.add_command(gate_group)


def _emoji_key(emoji) -> str:
    """Normalized options-map key for a reaction emoji.

    Custom emoji (truthy ``.id``) key off the id, so an animated ``<a:name:id>``
    seed still matches a reaction payload whose ``PartialEmoji`` renders
    ``<:name:id>`` — the gateway may omit ``animated``, and ``PartialEmoji``
    then defaults it to False, dropping the ``a`` prefix. Unicode emoji (plain
    ``str``, no ``.id``) key off their own value.
    """
    emoji_id = getattr(emoji, "id", None)
    return str(emoji_id) if emoji_id else str(emoji)


async def handle_gate_input_message(bot, repo: StatsRepository, settings: Settings, message):
    """Parse a message in the configured gate-input channel and react.

    ✅ on a freshly-recorded entry, ⏳ if it was rejected as a duplicate
    within the dedup window, ❌ if the message doesn't parse as a gate entry
    at all (and isn't a bot command, which is left alone).
    """
    parsed = parse_gate_message(message.content)
    if parsed is None:
        if not message.content.startswith(settings.command_prefix):
            try:
                await message.add_reaction("❌")
            except Exception:
                pass
        return
    gate_type, cost = parsed
    if gate_type in GATE_DROP_REACTION_ITEMS:
        items = GATE_DROP_REACTION_ITEMS[gate_type]
        options = {}
        reactions = []
        for item in items:
            emoji = resolve_drop_emoji(message.guild, item)
            options[_emoji_key(emoji)] = item
            reactions.append(emoji)
        options[DROP_NOTHING_EMOJI] = None
        reactions.append(DROP_NOTHING_EMOJI)
        bot._pending_gate[message.id] = {
            "cost": cost, "user_id": message.author.id,
            "username": message.author.name, "gate_type": gate_type,
            "options": options}
        # Persist the pending entry so a restart/deploy doesn't drop an
        # in-flight confirmation. Guarded: a persist failure must not abort the
        # in-memory seeding (which still works for a live click this session).
        try:
            await repo.set_gate_pending(
                message.id, channel_id=message.channel.id, gate_type=gate_type,
                cost=cost, user_id=message.author.id,
                username=message.author.name, options=options)
        except Exception:
            pass
        try:
            for emoji in reactions:
                await message.add_reaction(emoji)
        except Exception:
            pass
        return
    before = await repo.gate_record(gate_type)
    inserted = await repo.add_gate_entry(gate_type, cost, message.author.id, message.author.name)
    try:
        await message.add_reaction("✅" if inserted else "⏳")
    except Exception:
        pass
    if inserted:
        after = await repo.gate_record(gate_type)
        await _announce_records(bot, settings, gate_type,
                                changed_records(before, after), after)
        await update_gate_stats_embed(bot, repo, settings)
        try:
            await update_gate_chart(bot, repo, settings, gate_type)
        except Exception:
            pass
        newly = (await check_achievements(repo, message.author.id, f"gate_{gate_type}",
                                          defs=bot.achievement_defs.all())
                 + await check_achievements(repo, message.author.id, "gate_total",
                                            defs=bot.achievement_defs.all())
                 + await check_achievements(repo, message.author.id, "gate_cost_total",
                                            defs=bot.achievement_defs.all()))
        if newly:
            try:
                await announce_achievements(bot, settings, message.author, newly)
            except Exception:
                pass
        try:
            await message.delete(delay=bot.runtime_config.gate_delete_delay_seconds)
        except Exception:
            pass
        try:
            confirm = await message.channel.send(
                f"✅ {message.author.mention} Dein Wert für {GATE_NAMES[gate_type]} "
                f"({format_number(cost)}) wurde registriert.")
            await confirm.delete(delay=bot.runtime_config.gate_delete_delay_seconds)
        except Exception:
            pass


async def load_pending_gate(bot, repo: StatsRepository) -> None:
    """Repopulate ``bot._pending_gate`` from the persisted gate_pending rows.

    Called on startup so a drop click after a restart/deploy uses the fast
    in-memory confirmation path again (the DB fallback in
    ``handle_gate_drop_confirmation`` covers any click that races the load).
    """
    rows = await repo.all_gate_pending()
    bot._pending_gate = {
        r["message_id"]: {"cost": r["cost"], "user_id": r["user_id"],
                          "username": r["username"], "gate_type": r["gate_type"],
                          "options": r["options"]}
        for r in rows}


async def backfill_gate_input(bot, repo: StatsRepository, settings: Settings,
                              limit: int = 200) -> int:
    """Process gate-input messages the bot missed while offline.

    Scans the recent gate-input channel history and routes any parseable,
    not-yet-handled message through the normal `handle_gate_input_message`
    workflow (store a/b/c, re-seed the drop-picker for d/e/z/k). A message the
    bot has ALREADY reacted to (`reaction.me`) — ✅/⏳ for a/b/c, drop-icons for
    d/e/z/k — is treated as handled and skipped, so nothing is double-stored.
    Returns the number of messages (re)processed. Best-effort; never raises.
    """
    channel_id = bot.runtime_config.gate_input_channel_id
    if not channel_id:
        return 0
    channel = bot.get_channel(channel_id)
    if channel is None:
        return 0
    processed = 0
    try:
        async for message in channel.history(limit=limit):
            if getattr(message.author, "bot", False):
                continue
            parsed = parse_gate_message(message.content)
            if parsed is None:
                continue
            gate_type = parsed[0]
            if gate_type in GATE_DROP_REACTION_ITEMS:
                # Drop gate (d/e/z/k): a confirmed one is deleted, so any that
                # survive are awaiting a click and MUST have a pending row for a
                # click to register. Skip only if one already exists; otherwise
                # re-seed (handle_gate_input_message re-adds the reactions
                # idempotently and rewrites the pending row — it does NOT store
                # until the user clicks, so this never double-counts).
                if await repo.get_gate_pending(message.id) is not None:
                    continue
            else:
                # a/b/c store immediately; the bot's own ✅/⏳ reaction marks it
                # done. No reaction → posted while the bot was offline.
                if any(getattr(r, "me", False) for r in message.reactions):
                    continue
            try:
                await handle_gate_input_message(bot, repo, settings, message)
                processed += 1
            except Exception:
                log.exception("backfill: failed on message %s", message.id)
    except Exception:
        log.exception("gate-input backfill scan failed")
    if processed:
        log.info("gate-input backfill: processed %d missed message(s)", processed)
    return processed


async def _resolve_member(bot, payload, user_id: int):
    """Resolve a guild Member for `user_id` from a reaction payload.

    When the payload's reactor IS the target (self-confirm) this returns them;
    with the stat-override role the clicker differs, so we look the target up in
    the guild (cache, then fetch). Returns None if unresolvable.
    """
    guild = getattr(getattr(payload, "member", None), "guild", None)
    if guild is None:
        guild = getattr(bot.get_channel(payload.channel_id), "guild", None)
    if guild is None:
        return None
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except Exception:
            member = None
    return member


def _has_stat_override(payload, runtime_config) -> bool:
    """True if the reactor holds the configured stat-override role.

    Lets a moderator confirm another member's gate-drop reaction. Reads roles
    off `payload.member` (present on guild reaction events); returns False when
    no override role is configured or the member/roles are unavailable.
    """
    ids = runtime_config.stat_override_role_ids
    if not ids:
        return False
    member = getattr(payload, "member", None)
    return any(r.id in ids for r in getattr(member, "roles", None) or [])


async def handle_gate_drop_confirmation(bot, repo: StatsRepository,
                                        settings: Settings, payload) -> None:
    """Store a pending d/e/z/k gate on the author's single drop-icon click.

    Clicking a drop-icon reaction stores exactly that item dropped=True; ❌
    stores no drop. Only the message author may confirm their own gate; any
    other reactor (incl. the bot's own seeding reactions) is ignored. On a
    successful store the user's message is deleted and the usual
    post-processing runs.
    """
    pending = bot._pending_gate.get(payload.message_id)
    from_db = False
    if pending is None:
        # Restart/deploy wiped the in-memory dict: fall back to the persisted
        # gate_pending row so a post-restart click still records the gate.
        try:
            row = await repo.get_gate_pending(payload.message_id)
        except Exception:
            row = None
        if row is None:
            return
        pending = {"cost": row["cost"], "user_id": row["user_id"],
                   "username": row["username"], "gate_type": row["gate_type"],
                   "options": row["options"]}
        from_db = True
    options = pending["options"]
    key = _emoji_key(payload.emoji)
    if key not in options:
        return
    # The author may confirm their own gate; a member holding the configured
    # override role may confirm ANYONE's (the entry is still stored under the
    # original author from `pending`). The bot's own seed reactions are neither.
    if payload.user_id != pending["user_id"] and \
            not _has_stat_override(payload, bot.runtime_config):
        return
    # Claim the pending entry ATOMICALLY before any await: a redelivered event
    # or two quick clicks would otherwise both pass the guards above and each
    # store the gate (double gate_entries row on a suspending SQL backend).
    # pop() has no await, so only one dispatch wins; the rest are clean no-ops.
    # On the DB-fallback (restart) path there is no concurrent in-memory entry;
    # the delete_gate_pending below is the single-store gate for that case.
    if not from_db:
        pending = bot._pending_gate.pop(payload.message_id, None)
        if pending is None:
            return
    chosen = pending["options"][key]
    gate_type = pending["gate_type"]
    cost = pending["cost"]
    user_id = pending["user_id"]
    username = pending["username"]
    before = await repo.gate_record(gate_type)
    if gate_type == "d":
        inserted = await repo.add_gate_entry(
            "d", cost, user_id, username, laser_dropped=(chosen == "laser"))
    elif gate_type == "e":
        inserted = await repo.add_gate_entry(
            "e", cost, user_id, username, drops={"lf4": chosen == "lf4"})
    elif gate_type == "z":
        inserted = await repo.add_gate_entry(
            "z", cost, user_id, username, drops={"havoc": chosen == "havoc"})
    else:
        inserted = await repo.add_gate_entry(
            "k", cost, user_id, username,
            drops={"hercules": chosen == "hercules",
                   "lf4u": chosen == "lf4u"})
    # The pending confirmation is resolved (stored or dedup-rejected): drop its
    # persisted row so it isn't re-loaded on a later restart. Guarded so a
    # delete failure never breaks the store/announce flow.
    try:
        await repo.delete_gate_pending(payload.message_id)
    except Exception:
        pass
    if inserted:
        try:
            channel = bot.get_channel(payload.channel_id)
            msg = await channel.fetch_message(payload.message_id)
            await msg.delete(delay=bot.runtime_config.gate_delete_delay_seconds)
        except Exception:
            pass
        try:
            outcome = _DROP_LABELS[chosen] if chosen else "kein Drop"
            confirm = await bot.get_channel(payload.channel_id).send(
                f"✅ <@{user_id}> Dein Wert für {GATE_NAMES[gate_type]} "
                f"({format_number(cost)}) wurde registriert — {outcome}.")
            await confirm.delete(delay=bot.runtime_config.gate_delete_delay_seconds)
        except Exception:
            pass
        after = await repo.gate_record(gate_type)
        await _announce_records(bot, settings, gate_type,
                                changed_records(before, after), after)
        await update_gate_stats_embed(bot, repo, settings)
        try:
            await update_gate_chart(bot, repo, settings, gate_type)
        except Exception:
            pass
        # Resolve the ORIGINAL author's member — with the stat-override role a
        # different member may have clicked, but the entry (and its achievement
        # card) belong to the author, not the clicker.
        member = await _resolve_member(bot, payload, user_id)
        newly = (await check_achievements(repo, user_id, f"gate_{gate_type}",
                                          defs=bot.achievement_defs.all())
                 + await check_achievements(repo, user_id, "gate_total",
                                            defs=bot.achievement_defs.all())
                 + await check_achievements(repo, user_id, "gate_cost_total",
                                            defs=bot.achievement_defs.all()))
        if newly and member is not None:
            try:
                await announce_achievements(bot, settings, member, newly)
            except Exception:
                pass
    else:
        # Dedup rejection: mirror the a/b/c ⏳ feedback so the author sees the
        # duplicate was ignored. Keep the message (no delete) and leave the
        # seed reactions in place.
        try:
            channel = bot.get_channel(payload.channel_id)
            msg = await channel.fetch_message(payload.message_id)
            await msg.add_reaction("⏳")
        except Exception:
            pass


async def handle_verlauf_removal(bot, payload) -> None:
    """Delete a posted `/gate verlauf` chart when its original invoker reacts ❌.

    Only the invoker who ran the command may remove the chart; a ❌ from anyone
    else (including the bot's own seed reaction) is ignored. Best-effort: any
    Discord error while fetching/deleting is swallowed and the tracker is only
    popped on a successful delete.
    """
    if str(payload.emoji) != "❌":
        return
    if payload.message_id not in bot._verlauf_msgs:
        return
    if payload.user_id != bot._verlauf_msgs[payload.message_id]:
        return
    try:
        channel = bot.get_channel(payload.channel_id)
        msg = await channel.fetch_message(payload.message_id)
        await msg.delete()
    except Exception:
        return
    bot._verlauf_msgs.pop(payload.message_id, None)


RELOAD_EMOJI = "🔄"


async def handle_reload_reaction(bot, repo: StatsRepository, settings: Settings,
                                 payload) -> None:
    """🔄 on the live gate-stats or base-timer overview → refresh it now.

    Lets a user force an immediate re-render instead of waiting for the 30s
    loop / next gate entry. The bot's own seed reaction is ignored; the user's
    reaction is removed afterwards so it can be clicked again. Best-effort.
    """
    if str(payload.emoji) != RELOAD_EMOJI:
        return
    if bot.user is not None and payload.user_id == bot.user.id:
        return
    rc = bot.runtime_config
    handled = False
    if bot._gate_embed_msg_id is not None and \
            payload.message_id == bot._gate_embed_msg_id:
        await update_gate_stats_embed(bot, repo, settings)
        handled = True
    elif rc.timer_overview_message_id and \
            payload.message_id == rc.timer_overview_message_id:
        await update_timer_overview(bot, repo, rc, now_local(settings))
        handled = True
    if not handled:
        return
    try:
        channel = bot.get_channel(payload.channel_id)
        msg = await channel.fetch_message(payload.message_id)
        member = getattr(payload, "member", None)
        if member is None and channel is not None and channel.guild is not None:
            member = channel.guild.get_member(payload.user_id)
        if member is not None:
            await msg.remove_reaction(payload.emoji, member)
    except Exception:
        pass


async def _announce_records(bot, settings: Settings, gate_type: str,
                            changed: set[str], record: dict) -> None:
    if not changed or not bot.runtime_config.milestone_channel_id:
        return
    channel = bot.get_channel(bot.runtime_config.milestone_channel_id)
    if channel is None:
        return
    name = GATE_NAMES.get(gate_type, gate_type.upper())
    # First entry / single holder: both records move to the SAME user. Announce
    # only once (the Glückspilz) instead of two messages naming the same person.
    if ("min" in changed and "max" in changed
            and record["min_user"] == record["max_user"]):
        changed = {"min"}
    try:
        if "min" in changed:
            await channel.send(bot.content_texts.get("record_lucky").format(
                user=record["min_user"], name=name,
                cost=format_number(record["min_cost"])))
        if "max" in changed:
            await channel.send(bot.content_texts.get("record_unlucky").format(
                user=record["max_user"], name=name,
                cost=format_number(record["max_cost"])))
    except Exception:
        pass


async def resync_stat_commands(bot) -> None:
    await sync_commands_to_guilds(bot)


async def sync_commands_to_guilds(bot) -> None:
    if not bot.guilds:
        return
    for guild in bot.guilds:
        try:
            bot.tree.clear_commands(guild=guild)
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
        except Exception:
            log.exception("failed to sync commands to guild %s",
                          getattr(guild, "id", guild))
    # Empty Discord's published GLOBAL scope so previously-published global
    # commands (e.g. the historically-published global /admin) don't double-list
    # alongside the guild-scoped copies — WITHOUT destroying the in-memory global
    # tree. clear_commands(guild=None) wipes the in-memory global command set
    # permanently, so a later re-sync (admin CRUD via resync_stat_commands) would
    # copy an almost-empty tree into each guild and blow every command away until
    # a full restart. Snapshot the global commands, publish an empty global
    # scope, then RE-ADD them so future copy_global_to still sees the full set.
    try:
        saved = list(bot.tree.get_commands())
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
        for cmd in saved:
            bot.tree.add_command(cmd)
    except Exception:
        log.exception("failed to reset global command scope")


def reminder_loop_time(runtime_config, settings: Settings) -> time:
    """The daily event-reminder fire time as a TZ-AWARE `time`.

    discord.py's `tasks.loop(time=...)` treats a NAIVE time as UTC, so a bare
    `time(19, 30)` fired at 19:30 UTC (= 20:30/21:30 Europe/Berlin — the "1h
    late" bug). Attaching `tzinfo` makes it fire at that local wall-clock time.
    """
    h, m = runtime_config.reminder_hm()
    return time(hour=h, minute=m, tzinfo=ZoneInfo(settings.timezone))


def _wire_events(bot, settings: Settings, repo: StatsRepository):
    @tasks.loop(time=reminder_loop_time(bot.runtime_config, settings))
    async def event_reminder_task():
        weekday = now_local(settings).weekday()
        channel = bot.get_channel(bot.runtime_config.reminder_channel_id)
        if channel is None:
            return
        if weekday == 2:
            await channel.send(bot.content_texts.get("reminder_aceball"))
        elif weekday == 4:
            await channel.send(bot.content_texts.get("reminder_invasion"))

    @tasks.loop(minutes=5)
    async def voice_flush_task():
        await flush_voice_times(bot, repo, now_local(settings))

    @bot.event
    async def on_ready():
        log.info("Bot eingeloggt als %s", bot.user)
        try:
            await bot.runtime_config.refresh(repo)
        except Exception:
            log.exception("runtime_config refresh failed; using .env base")
        try:
            await bot.content_texts.refresh(repo)
        except Exception:
            log.exception("content_texts refresh failed; using defaults")
        try:
            await bot.colors.refresh(repo)
        except Exception:
            log.exception("colors refresh failed; using defaults")
        try:
            await bot.achievement_defs.refresh(repo)
        except Exception:
            log.exception("achievement_defs refresh failed; using defaults")
        try:
            bot.add_view(OverviewView(bot, repo, settings))
        except Exception:
            log.exception("overview view registration failed")
        try:
            bot.add_view(CommandListView(bot, settings))
        except Exception:
            log.exception("command-list view registration failed")
        try:
            await load_pending_gate(bot, repo)
        except Exception:
            log.exception("gate_pending load failed; in-flight confirms may be lost")
        try:
            await backfill_gate_input(bot, repo, settings)
        except Exception:
            log.exception("gate-input backfill failed")
        await register_stat_commands(bot, repo, settings)
        for guild in bot.guilds:
            try:
                members = [m async for m in guild.fetch_members(limit=None)]
            except Exception:
                members = guild.members
            for m in members:
                if not m.bot:
                    await repo.upsert_user(m.id, m.display_name)
                await enforce_nick(m, bot.runtime_config)
        if not event_reminder_task.is_running():
            event_reminder_task.start()
        for guild in bot.guilds:
            for channel in guild.voice_channels:
                for m in channel.members:
                    if not m.bot:
                        # Idempotent across reconnects: setdefault so a repeat
                        # on_ready doesn't overwrite an existing join time and
                        # drop un-flushed voice seconds.
                        bot.voice_join_times.setdefault(m.id, now_local(settings))
        if not voice_flush_task.is_running():
            voice_flush_task.start()
        start_timer_overview_loop(bot, repo, settings)
        if bot.runtime_config.gate_stats_channel_id:
            await update_gate_stats_embed(bot, repo, settings)
        try:
            await update_all_gate_charts(bot, repo, settings)
        except Exception:
            log.exception("gate-chart update failed")
        try:
            await update_gate_input_help(bot, repo, settings)
        except Exception:
            log.exception("gate-input help update failed")
        try:
            await update_command_list(bot, repo, settings)
        except Exception:
            log.exception("command-list update failed")
        # Publish app commands GUILD-SCOPED ONLY (instant availability on this
        # single-guild bot). We deliberately do NOT run a standalone global
        # bot.tree.sync() here: the tree is still populated globally in-memory
        # by the register_* calls, but publishing it both globally AND
        # guild-scoped would double-list every command in the picker.
        # sync_commands_to_guilds copies the global tree into each guild and,
        # once, empties the published global scope.
        # One-shot per process: on_ready re-fires on every gateway reconnect,
        # but the guild sync only needs to happen once. Guarding this (like
        # event_reminder_task/voice_join_times above) avoids a per-guild
        # clear+sync HTTP round-trip — and its guild-command rate-limit risk —
        # on every reconnect forever. Gated on bot.guilds so an empty-guilds
        # first ready retries on a later ready; the flag is set only AFTER the
        # sync so a transient failure retries too.
        if bot.guilds and not bot._stale_guild_commands_cleared:
            await sync_commands_to_guilds(bot)
            bot._stale_guild_commands_cleared = True

    @bot.tree.error
    async def on_app_command_error(interaction, error):
        original = getattr(error, "original", error)
        if isinstance(error, discord.app_commands.CommandNotFound) or isinstance(
                original, discord.app_commands.CommandNotFound):
            # Phantom/expired guild command — the interaction is already gone,
            # so answering it would just raise NotFound (10062). Ignore quietly.
            return
        if isinstance(original, (ValueError, KeyError)):
            msg = f"❌ {original}"
        else:
            msg = "❌ Ein Fehler ist aufgetreten."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            log.exception("failed to send app-command error response")

    @bot.event
    async def on_message(message):
        if message.author == bot.user:
            return
        author = message.author
        if not getattr(author, "bot", False) and getattr(author, "id", None) is not None:
            newly = await record_message_activity(bot, repo, settings, author.id,
                                                  now_local(settings))
            if newly:
                try:
                    await announce_achievements(bot, settings, author, newly)
                except Exception:
                    pass
        if bot.runtime_config.gate_input_channel_id and message.channel.id == bot.runtime_config.gate_input_channel_id:
            await handle_gate_input_message(bot, repo, settings, message)

    @bot.event
    async def on_voice_state_update(member, before, after):
        try:
            await announce_voice_change(bot, member, before, after)
        except Exception:
            pass
        await handle_voice_state_update(bot, repo, settings, member, before, after,
                                        now_local(settings))

    @bot.event
    async def on_raw_reaction_add(payload):
        # Both handlers are independent best-effort side-channels; wrap each so a
        # failure in one (e.g. the activity counter) can't skip the other (the
        # overview nav), and vice versa.
        try:
            await handle_activity_reaction(bot, repo, settings, payload)
        except Exception:
            pass
        try:
            await handle_gate_drop_confirmation(bot, repo, settings, payload)
        except Exception:
            pass
        try:
            await handle_kodex_confirmation(bot, repo, payload)
        except Exception:
            pass
        try:
            await handle_verlauf_removal(bot, payload)
        except Exception:
            pass
        try:
            await handle_reload_reaction(bot, repo, settings, payload)
        except Exception:
            pass

    @bot.event
    async def on_member_update(before, after):
        if before.roles != after.roles or before.display_name != after.display_name:
            await enforce_nick(after, bot.runtime_config)

    @bot.event
    async def on_member_join(member):
        if not member.bot:
            await repo.upsert_user(member.id, member.display_name)
        try:
            await send_welcome_card(bot, settings, member)
        except Exception:
            pass
        try:
            await send_kodex_dm(bot, repo, member)
        except Exception:
            pass
        await asyncio.sleep(5)
        await enforce_nick(member, bot.runtime_config)

    @bot.event
    async def on_member_remove(member):
        if await repo.get_user(member.id) is not None:
            await repo.archive_user(member.id)
