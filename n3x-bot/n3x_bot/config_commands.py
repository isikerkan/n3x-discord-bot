"""Admin-gated `/config` slash group that writes `runtime_config` DB overrides
and refreshes the live resolver so changes apply without a restart.

Phase 1 built the `runtime_config` table + the `RuntimeConfig` resolver; this
module adds the write surface as a slash-only `app_commands.Group` on `bot.tree`.
Every write goes through `repo.set_runtime_config` (or `delete_runtime_config`)
and then `await bot.runtime_config.refresh(repo)`.

Channel/role targets use native channel/role options; the callback reads only
`.id` and writes `str(obj.id)`.
"""
import discord
from discord import app_commands

from n3x_bot import cards
from n3x_bot.admin import app_is_admin
from n3x_bot.config import Settings, parse_duration
from n3x_bot.runtime_config import OVERRIDABLE_KEYS
from n3x_bot.storage.base import StatsRepository

CHANNEL_PURPOSES: dict[str, str] = {
    "welcome": "welcome_channel_id",
    "reminder": "reminder_channel_id",
    "gate_input": "gate_input_channel_id",
    "gate_stats": "gate_stats_channel_id",
    "gate_chart": "gate_chart_channel_id",
    "command_list": "command_list_channel_id",
    "milestone": "milestone_channel_id",
    "overview": "overview_channel_id",
    "kodex_check": "kodex_check_channel_id",
    "timer_overview": "timer_overview_channel_id",
    "voice_log": "voice_log_channel_id",
    "event_reminder": "event_reminder_channel_id",
}
ROLE_PURPOSES: dict[str, str] = {
    "target": "target_role_id",
    "gate_delete": "gate_delete_role_id",
    "stat_override": "stat_override_role_id",
    "base_timer": "base_timer_role_id",
    "event": "event_role_id",
}
MESSAGE_PURPOSES: dict[str, str] = {
    "timer_overview": "timer_overview_message_id",
}


def register_config_commands(bot, repo: StatsRepository, settings: Settings) -> None:
    if bot.tree.get_command("config") is not None:
        return

    config_group = app_commands.Group(
        name="config", description="Laufzeit-Konfiguration (Admin).")

    async def _require_admin(interaction) -> bool:
        if app_is_admin(interaction, settings):
            return True
        await interaction.response.send_message(
            "❌ Keine Berechtigung.", ephemeral=True)
        return False

    async def _write(interaction, key: str, value: str) -> None:
        await repo.set_runtime_config(key, value)
        await bot.runtime_config.refresh(repo)
        await interaction.response.send_message(
            f"✅ `{key}` gesetzt.", ephemeral=True)

    @config_group.command(name="channel",
                          description="Setzt einen Kanal für einen bestimmten Zweck.")
    @app_commands.describe(purpose="Zweck des Kanals", channel="Kanal")
    @app_commands.choices(purpose=[app_commands.Choice(name=k, value=k)
                                   for k in CHANNEL_PURPOSES])
    async def channel(interaction, purpose: str, channel: discord.abc.GuildChannel):
        if not await _require_admin(interaction):
            return
        await _write(interaction, CHANNEL_PURPOSES[purpose], str(channel.id))

    @config_group.command(name="role",
                          description="Setzt eine Rolle für einen bestimmten Zweck.")
    @app_commands.describe(purpose="Zweck der Rolle", role="Rolle")
    @app_commands.choices(purpose=[app_commands.Choice(name=k, value=k)
                                   for k in ROLE_PURPOSES])
    async def role(interaction, purpose: str, role: discord.Role):
        if not await _require_admin(interaction):
            return
        await _write(interaction, ROLE_PURPOSES[purpose], str(role.id))

    @config_group.command(name="message",
                          description="Setzt eine Nachrichten-ID für einen Zweck.")
    @app_commands.describe(purpose="Zweck", message_id="Nachrichten-ID")
    @app_commands.choices(purpose=[app_commands.Choice(name=k, value=k)
                                   for k in MESSAGE_PURPOSES])
    async def message(interaction, purpose: str, message_id: str):
        if not await _require_admin(interaction):
            return
        if not message_id.isdigit():
            await interaction.response.send_message(
                f"❌ Ungültige ID `{message_id}`.", ephemeral=True)
            return
        await _write(interaction, MESSAGE_PURPOSES[purpose], message_id)

    @config_group.command(name="gate-rewards",
                          description="Setzt die Gate-Belohnungen.")
    @app_commands.describe(value="Wert im Format a:1,b:2")
    async def gate_rewards(interaction, value: str):
        if not await _require_admin(interaction):
            return
        await _write(interaction, "gate_rewards", value)

    @config_group.command(name="allowed-maps",
                          description="Setzt die erlaubten Maps.")
    @app_commands.describe(value="Wert im Format 1-1,2-2,3-3")
    async def allowed_maps(interaction, value: str):
        if not await _require_admin(interaction):
            return
        await _write(interaction, "allowed_maps", value)

    @config_group.command(name="voice-roles",
                          description="Setzt die Voice-Achievement-Rollen.")
    @app_commands.describe(value="Wert im Format x:1,y:2")
    async def voice_roles(interaction, value: str):
        if not await _require_admin(interaction):
            return
        await _write(interaction, "voice_achievement_roles", value)

    @config_group.command(name="reminder-time",
                          description="Setzt die Reminder-Uhrzeit.")
    @app_commands.describe(value="Uhrzeit im Format HH:MM")
    async def reminder_time(interaction, value: str):
        if not await _require_admin(interaction):
            return
        await _write(interaction, "reminder_time", value)

    @config_group.command(name="gate-delete-delay",
                          description="Setzt die Löschverzögerung der Gate-Nachricht.")
    @app_commands.describe(value="Dauer, z.B. 30s, 1m, 5m, 2h, 90")
    async def gate_delete_delay(interaction, value: str):
        if not await _require_admin(interaction):
            return
        try:
            parse_duration(value)
        except ValueError:
            await interaction.response.send_message(
                "❌ Ungültige Dauer. Beispiele: 30s, 1m, 5m, 2h, 90",
                ephemeral=True)
            return
        await _write(interaction, "gate_message_delete_delay", value)

    @config_group.command(name="show",
                          description="Zeigt die aktuelle Konfiguration.")
    async def show(interaction):
        if not await _require_admin(interaction):
            return
        overrides = await repo.all_runtime_config()
        lines = []
        for key in sorted(OVERRIDABLE_KEYS):
            if key in overrides:
                lines.append(f"`{key}` = `{overrides[key]}` (Override)")
            else:
                lines.append(f"`{key}` = `{getattr(settings, key)}`")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @config_group.command(name="reset",
                          description="Setzt einen Override zurück.")
    @app_commands.describe(key="Zurückzusetzender Schlüssel")
    @app_commands.choices(key=[app_commands.Choice(name=k, value=k)
                               for k in sorted(OVERRIDABLE_KEYS)])
    async def reset(interaction, key: str):
        if not await _require_admin(interaction):
            return
        await repo.delete_runtime_config(key)
        await bot.runtime_config.refresh(repo)
        await interaction.response.send_message(
            f"✅ Override `{key}` entfernt.", ephemeral=True)

    @config_group.command(name="tier-color",
                          description="Setzt die Farbe einer Gate-Tier-Stufe.")
    @app_commands.describe(name="Tier-Name (Substring, z.B. gold)",
                           hex="Farbe als #RRGGBB")
    async def tier_color(interaction, name: str, hex: str):
        if not await _require_admin(interaction):
            return
        if cards._parse_hex_color(hex) is None:
            await interaction.response.send_message(
                f"❌ Ungültige Farbe `{hex}`. Format: #RRGGBB", ephemeral=True)
            return
        await repo.set_color_config(f"tier:{name.lower()}", hex)
        await bot.colors.refresh(repo)
        await interaction.response.send_message(
            f"✅ Tier-Farbe `{name.lower()}` gesetzt.", ephemeral=True)

    @config_group.command(name="category-color",
                          description="Setzt die Farbe einer Achievement-Kategorie.")
    @app_commands.describe(name="Kategorie-Name (z.B. voice)",
                           hex="Farbe als #RRGGBB")
    async def category_color(interaction, name: str, hex: str):
        if not await _require_admin(interaction):
            return
        if cards._parse_hex_color(hex) is None:
            await interaction.response.send_message(
                f"❌ Ungültige Farbe `{hex}`. Format: #RRGGBB", ephemeral=True)
            return
        await repo.set_color_config(f"category:{name.lower()}", hex)
        await bot.colors.refresh(repo)
        await interaction.response.send_message(
            f"✅ Kategorie-Farbe `{name.lower()}` gesetzt.", ephemeral=True)

    @config_group.command(name="color-reset",
                          description="Setzt eine Farb-Override zurück.")
    @app_commands.describe(key="Voller Schlüssel, z.B. tier:gold")
    async def color_reset(interaction, key: str):
        if not await _require_admin(interaction):
            return
        await repo.delete_color_config(key)
        await bot.colors.refresh(repo)
        await interaction.response.send_message(
            f"✅ Farb-Override `{key}` entfernt.", ephemeral=True)

    bot.tree.add_command(config_group)
