"""Admin-gated `/content` slash group that edits `content_texts` DB overrides
and refreshes the live resolver so narrative copy changes apply without a
restart.

Slash-only mirror of `config_commands.register_config_commands`: an
`app_commands.Group` named `content` on `bot.tree`, admin-gated via
`app_is_admin`, ephemeral replies, refresh-after-write.
"""
from discord import app_commands

from n3x_bot.admin import app_is_admin
from n3x_bot.config import Settings
from n3x_bot.content import CONTENT_KEYS
from n3x_bot.storage.base import StatsRepository

# Template keys are `.format(...)`-ed at their read-sites (welcome.py,
# bot._announce_records) with exactly these named placeholders. An override with
# a wrong/missing/positional/malformed placeholder would raise at the read-site,
# where it is silently swallowed — so validate on write. Keys absent here carry
# no placeholders and are never `.format`-ed, so they need no validation.
REQUIRED_PLACEHOLDERS: dict[str, frozenset[str]] = {
    "welcome_dm": frozenset({"mention"}),
    "record_lucky": frozenset({"user", "name", "cost"}),
    "record_unlucky": frozenset({"user", "name", "cost"}),
}


def register_content_commands(bot, repo: StatsRepository, settings: Settings) -> None:
    if bot.tree.get_command("content") is not None:
        return

    content_group = app_commands.Group(
        name="content", description="Narrative Texte (Admin).")

    _key_choices = [app_commands.Choice(name=k, value=k)
                    for k in sorted(CONTENT_KEYS)]

    async def _require_admin(interaction) -> bool:
        if app_is_admin(interaction, settings):
            return True
        await interaction.response.send_message(
            "❌ Keine Berechtigung.", ephemeral=True)
        return False

    @content_group.command(name="list",
                           description="Listet alle Content-Schlüssel.")
    async def list_cmd(interaction):
        if not await _require_admin(interaction):
            return
        overrides = await repo.all_content_texts()
        lines = []
        for key in sorted(CONTENT_KEYS):
            line = f"`{key}`"
            if key in overrides:
                line = f"{line} (Override)"
            lines.append(line)
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @content_group.command(name="show",
                           description="Zeigt den effektiven Text eines Schlüssels.")
    @app_commands.describe(key="Schlüssel")
    @app_commands.choices(key=_key_choices)
    async def show_cmd(interaction, key: str):
        if not await _require_admin(interaction):
            return
        await interaction.response.send_message(
            f"```\n{bot.content_texts.get(key)}\n```", ephemeral=True)

    @content_group.command(name="set",
                           description="Setzt den Text eines Schlüssels.")
    @app_commands.describe(key="Schlüssel", value="Neuer Text")
    @app_commands.choices(key=_key_choices)
    async def set_cmd(interaction, key: str, value: str):
        if not await _require_admin(interaction):
            return
        required = REQUIRED_PLACEHOLDERS.get(key)
        if required is not None:
            try:
                value.format(**{p: "" for p in required})
            except (KeyError, IndexError, ValueError):
                allowed = ", ".join(f"{{{p}}}" for p in sorted(required))
                await interaction.response.send_message(
                    f"❌ Ungültige Platzhalter. Erlaubt für `{key}`: {allowed}",
                    ephemeral=True)
                return
        await repo.set_content_text(key, value)
        await bot.content_texts.refresh(repo)
        await interaction.response.send_message(
            f"✅ `{key}` gesetzt.", ephemeral=True)

    @content_group.command(name="reset",
                           description="Setzt den Text eines Schlüssels zurück.")
    @app_commands.describe(key="Schlüssel")
    @app_commands.choices(key=_key_choices)
    async def reset_cmd(interaction, key: str):
        if not await _require_admin(interaction):
            return
        await repo.delete_content_text(key)
        await bot.content_texts.refresh(repo)
        await interaction.response.send_message(
            f"✅ Override `{key}` entfernt.", ephemeral=True)

    bot.tree.add_command(content_group)
