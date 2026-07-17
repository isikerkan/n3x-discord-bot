"""Admin-gated `/achievement` slash group that writes the `achievement_defs`
table and refreshes the live resolver so definition edits apply without a
restart.

Phase 2a built the `achievement_defs` table, the repo CRUD methods and the
total-replacement resolver `bot.achievement_defs` (`AchievementDefs`). This
module adds the WRITE surface as a slash-only `app_commands.Group` on
`bot.tree`, mirroring `n3x_bot/config_commands.py`.

The group is read-only: `list` (overview + code-vs-DB drift hint) and `show`
(one definition's detail) off the live `bot.achievement_defs` resolver. The
write subcommands (`set`/`reset`/`reset-all`) were removed.
"""
from discord import app_commands

from n3x_bot.achievements import ACHIEVEMENTS
from n3x_bot.admin import app_is_admin
from n3x_bot.config import Settings
from n3x_bot.storage.base import StatsRepository


def register_achievement_def_commands(bot, repo: StatsRepository,
                                      settings: Settings) -> None:
    if bot.tree.get_command("achievement") is not None:
        return

    group = app_commands.Group(
        name="achievement", description="Achievement-Definitionen (Admin).")

    async def _require_admin(interaction) -> bool:
        if app_is_admin(interaction, settings):
            return True
        await interaction.response.send_message(
            "❌ Keine Berechtigung.", ephemeral=True)
        return False

    async def _id_autocomplete(interaction, current: str
                               ) -> list[app_commands.Choice[str]]:
        needle = current.lower()
        choices: list[app_commands.Choice[str]] = []
        for a in bot.achievement_defs.all():
            if needle in a.id.lower():
                choices.append(app_commands.Choice(
                    name=f"{a.id} — {a.title}"[:100], value=a.id))
            if len(choices) >= 25:
                break
        return choices

    @group.command(name="list",
                   description="Zeigt die Anzahl der Achievement-Definitionen.")
    async def list_defs(interaction):
        if not await _require_admin(interaction):
            return
        total = bot.achievement_defs.total
        rows = await repo.all_achievement_defs()
        if not rows:
            text = (f"**{total}** Achievements (Code-Defaults aktiv — "
                    "0 DB-Overrides).")
        else:
            text = f"**{total}** Achievements ({len(rows)} DB-Overrides)."
            db_ids = {row["id"] for row in rows}
            missing = [a.id for a in ACHIEVEMENTS if a.id not in db_ids]
            if missing:
                text += (f"\n⚠️ {len(missing)} neue Code-Achievements nicht in "
                         "der DB (reset-all übernimmt sie).")
        await interaction.response.send_message(text, ephemeral=True)

    @group.command(name="show",
                   description="Zeigt Details einer Achievement-Definition.")
    @app_commands.describe(id="ID der Achievement-Definition")
    async def show(interaction, id: str):
        if not await _require_admin(interaction):
            return
        ach = bot.achievement_defs.by_id(id)
        if ach is None:
            await interaction.response.send_message(
                f"❌ Unbekannte ID `{id}`.", ephemeral=True)
            return
        lines = [
            f"**`{ach.id}`** — {ach.title}",
            f"Kategorie: `{ach.category}`",
            f"Metrik: `{ach.metric}`",
            f"Threshold: `{ach.threshold}`",
            f"Secret: `{ach.secret}`",
            f"Farbe: `{ach.color}`",
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @show.autocomplete("id")
    async def _show_id_ac(interaction, current: str):
        return await _id_autocomplete(interaction, current)

    bot.tree.add_command(group)
