"""Admin-gated `/achievement` slash group that writes the `achievement_defs`
table and refreshes the live resolver so definition edits apply without a
restart.

Phase 2a built the `achievement_defs` table, the repo CRUD methods and the
total-replacement resolver `bot.achievement_defs` (`AchievementDefs`). This
module adds the WRITE surface as a slash-only `app_commands.Group` on
`bot.tree`, mirroring `n3x_bot/config_commands.py`.

Because the resolver is total-replacement, on an EMPTY table `set` must FIRST
seed all code defaults (`ACHIEVEMENTS`) and THEN upsert the given def, so one
edit never shadows the other 82. On a NON-empty table it is a plain upsert.
"""
from dataclasses import asdict

from discord import app_commands

from n3x_bot.achievements import ACHIEVEMENTS
from n3x_bot.admin import app_is_admin
from n3x_bot.cards import _parse_hex_color
from n3x_bot.config import Settings
from n3x_bot.storage.base import StatsRepository

_CODE_DEFAULTS_BY_ID = {a.id: a for a in ACHIEVEMENTS}


async def _seed_defaults_if_empty(repo) -> None:
    if await repo.all_achievement_defs():
        return
    # Atomic seed: one all-or-nothing write, so a mid-loop failure can never
    # leave a partial table that the total-replacement resolver would surface
    # as silently missing achievements.
    await repo.replace_achievement_defs([asdict(a) for a in ACHIEVEMENTS])


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

    @group.command(name="set",
                   description="Legt eine Achievement-Definition an oder ändert sie.")
    @app_commands.describe(id="ID", title="Titel", threshold="Schwellenwert (≥ 1)",
                           category="Kategorie", metric="Metrik",
                           secret="Versteckt?", color="Hex-Farbe, z.B. #AABBCC")
    async def set_def(interaction, id: str, title: str, threshold: int,
                      category: str, metric: str, secret: bool = False,
                      color: str | None = None):
        if not await _require_admin(interaction):
            return
        if color is not None and _parse_hex_color(color) is None:
            await interaction.response.send_message(
                f"❌ Ungültige Farbe `{color}`. Format: #AABBCC.", ephemeral=True)
            return
        if threshold < 1:
            await interaction.response.send_message(
                "❌ Threshold muss ≥ 1 sein.", ephemeral=True)
            return
        await _seed_defaults_if_empty(repo)
        await repo.set_achievement_def(
            id, category=category, metric=metric, threshold=threshold,
            title=title, secret=secret, color=color)
        await bot.achievement_defs.refresh(repo)
        await interaction.response.send_message(
            f"✅ `{id}` gespeichert.", ephemeral=True)

    @group.command(name="reset",
                   description="Setzt eine Achievement-Definition zurück.")
    @app_commands.describe(id="ID der Achievement-Definition")
    async def reset(interaction, id: str):
        if not await _require_admin(interaction):
            return
        code_default = _CODE_DEFAULTS_BY_ID.get(id)
        if code_default is not None:
            # A code-default id: revert any override to its original values
            # while keeping the row (seeded-table consistency).
            rows = await repo.all_achievement_defs()
            if not rows:
                # Empty table -> resolver already serves code defaults; writing
                # a lone row would shadow the other 82 under total-replacement.
                await interaction.response.send_message(
                    f"✅ `{id}` ist bereits Standard.", ephemeral=True)
                return
            await repo.set_achievement_def(
                id, category=code_default.category, metric=code_default.metric,
                threshold=code_default.threshold, title=code_default.title,
                secret=code_default.secret, color=code_default.color)
            await bot.achievement_defs.refresh(repo)
            await interaction.response.send_message(
                f"✅ `{id}` auf Standard zurückgesetzt.", ephemeral=True)
            return
        # A custom (non-code) id: delete the row outright.
        deleted = await repo.delete_achievement_def(id)
        if not deleted:
            await interaction.response.send_message(
                f"❌ Unbekannte ID `{id}`.", ephemeral=True)
            return
        await bot.achievement_defs.refresh(repo)
        await interaction.response.send_message(
            f"✅ `{id}` zurückgesetzt.", ephemeral=True)

    @reset.autocomplete("id")
    async def _reset_id_ac(interaction, current: str):
        return await _id_autocomplete(interaction, current)

    @group.command(name="reset-all",
                   description="Löscht alle Achievement-Definitionen.")
    async def reset_all(interaction):
        if not await _require_admin(interaction):
            return
        # Atomic wipe: one all-or-nothing transaction rather than per-row
        # deletes, so the resolver never sees a half-emptied table.
        await repo.replace_achievement_defs([])
        await bot.achievement_defs.refresh(repo)
        await interaction.response.send_message(
            "✅ Alle Definitionen zurückgesetzt (Code-Defaults aktiv).",
            ephemeral=True)

    bot.tree.add_command(group)
