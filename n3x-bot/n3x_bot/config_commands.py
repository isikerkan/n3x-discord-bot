"""Admin-gated `!config` prefix commands that write `runtime_config` DB
overrides and refresh the live resolver so changes apply without a restart.

Phase 1 built the `runtime_config` table + the `RuntimeConfig` resolver; this
module adds the write surface. Every write goes through `repo.set_runtime_config`
(or `delete_runtime_config`) and then `await bot.runtime_config.refresh(repo)`.

The channel/role pickers post a small View holding a `ChannelSelect`/`RoleSelect`;
the select is author-locked to the invoking admin.
"""
import discord

from n3x_bot.admin import is_admin
from n3x_bot.config import Settings
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
}
ROLE_PURPOSES: dict[str, str] = {
    "target": "target_role_id",
    "gate_delete": "gate_delete_role_id",
    "base_timer": "base_timer_role_id",
}
MESSAGE_PURPOSES: dict[str, str] = {
    "timer_overview": "timer_overview_message_id",
}


class ChannelConfigView(discord.ui.View):
    """Posts a ChannelSelect; on pick writes `<key> = str(channel.id)` and
    refreshes the live resolver. Author-locked to the invoking admin."""

    def __init__(self, repo, bot, key: str, author_id: int):
        super().__init__(timeout=120)
        self.repo = repo
        self.bot = bot
        self.key = key
        self.author_id = author_id
        select = discord.ui.ChannelSelect(placeholder="Kanal wählen…")
        select.callback = self._on_select
        self._select = select
        self.add_item(select)

    async def _on_select(self, interaction) -> None:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Nicht für dich.",
                                                    ephemeral=True)
            return
        channel = self._select.values[0]
        await self.repo.set_runtime_config(self.key, str(channel.id))
        await self.bot.runtime_config.refresh(self.repo)
        await interaction.response.send_message(
            f"✅ `{self.key}` = `{channel.id}`.", ephemeral=True)


class RoleConfigView(discord.ui.View):
    """Posts a RoleSelect; on pick writes `<key> = str(role.id)` and refreshes
    the live resolver. Author-locked to the invoking admin."""

    def __init__(self, repo, bot, key: str, author_id: int):
        super().__init__(timeout=120)
        self.repo = repo
        self.bot = bot
        self.key = key
        self.author_id = author_id
        select = discord.ui.RoleSelect(placeholder="Rolle wählen…")
        select.callback = self._on_select
        self._select = select
        self.add_item(select)

    async def _on_select(self, interaction) -> None:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Nicht für dich.",
                                                    ephemeral=True)
            return
        role = self._select.values[0]
        await self.repo.set_runtime_config(self.key, str(role.id))
        await self.bot.runtime_config.refresh(self.repo)
        await interaction.response.send_message(
            f"✅ `{self.key}` = `{role.id}`.", ephemeral=True)


def register_config_commands(bot, repo: StatsRepository, settings: Settings) -> None:
    if bot.get_command("config") is not None:
        return

    @bot.group(name="config", invoke_without_command=True)
    async def config(ctx):
        await ctx.send(
            "Nutze `!config channel|role|message|gate-rewards|allowed-maps|"
            "voice-roles|reminder-time|show|reset ...`.", delete_after=5)

    @config.command(name="channel")
    async def channel(ctx, purpose):
        if not is_admin(ctx.author, settings):
            await ctx.send("❌ Keine Berechtigung.", delete_after=5)
            return
        if purpose not in CHANNEL_PURPOSES:
            await ctx.send(f"❌ Unbekannter Zweck `{purpose}`.", delete_after=5)
            return
        view = ChannelConfigView(repo, bot, CHANNEL_PURPOSES[purpose], ctx.author.id)
        await ctx.send(f"Kanal für `{purpose}` wählen:", view=view)

    @config.command(name="role")
    async def role(ctx, purpose):
        if not is_admin(ctx.author, settings):
            await ctx.send("❌ Keine Berechtigung.", delete_after=5)
            return
        if purpose not in ROLE_PURPOSES:
            await ctx.send(f"❌ Unbekannter Zweck `{purpose}`.", delete_after=5)
            return
        view = RoleConfigView(repo, bot, ROLE_PURPOSES[purpose], ctx.author.id)
        await ctx.send(f"Rolle für `{purpose}` wählen:", view=view)

    @config.command(name="message")
    async def message(ctx, purpose, message_id: str):
        if not is_admin(ctx.author, settings):
            await ctx.send("❌ Keine Berechtigung.", delete_after=5)
            return
        if purpose not in MESSAGE_PURPOSES:
            await ctx.send(f"❌ Unbekannter Zweck `{purpose}`.", delete_after=5)
            return
        if not message_id.isdigit():
            await ctx.send(f"❌ Ungültige ID `{message_id}`.", delete_after=5)
            return
        await repo.set_runtime_config(MESSAGE_PURPOSES[purpose], message_id)
        await bot.runtime_config.refresh(repo)
        await ctx.send(f"✅ `{MESSAGE_PURPOSES[purpose]}` = `{message_id}`.",
                       delete_after=5)

    async def _set_content(ctx, key: str, value: str):
        if not is_admin(ctx.author, settings):
            await ctx.send("❌ Keine Berechtigung.", delete_after=5)
            return
        await repo.set_runtime_config(key, value)
        await bot.runtime_config.refresh(repo)
        await ctx.send(f"✅ `{key}` gesetzt.", delete_after=5)

    @config.command(name="gate-rewards")
    async def gate_rewards(ctx, value: str):
        await _set_content(ctx, "gate_rewards", value)

    @config.command(name="allowed-maps")
    async def allowed_maps(ctx, value: str):
        await _set_content(ctx, "allowed_maps", value)

    @config.command(name="voice-roles")
    async def voice_roles(ctx, value: str):
        await _set_content(ctx, "voice_achievement_roles", value)

    @config.command(name="reminder-time")
    async def reminder_time(ctx, value: str):
        await _set_content(ctx, "reminder_time", value)

    @config.command(name="show")
    async def show(ctx):
        if not is_admin(ctx.author, settings):
            await ctx.send("❌ Keine Berechtigung.", delete_after=5)
            return
        overrides = await repo.all_runtime_config()
        chunk = ""
        for key in sorted(OVERRIDABLE_KEYS):
            if key in overrides:
                line = f"`{key}` = `{overrides[key]}` (Override)"
            else:
                line = f"`{key}` = `{getattr(settings, key)}`"
            if len(chunk) + len(line) + 1 > 1900:
                await ctx.send(chunk)
                chunk = ""
            chunk = f"{chunk}\n{line}" if chunk else line
        if chunk:
            await ctx.send(chunk)

    @config.command(name="reset")
    async def reset(ctx, key):
        if not is_admin(ctx.author, settings):
            await ctx.send("❌ Keine Berechtigung.", delete_after=5)
            return
        if key not in OVERRIDABLE_KEYS:
            await ctx.send(f"❌ `{key}` ist nicht zurücksetzbar.", delete_after=5)
            return
        await repo.delete_runtime_config(key)
        await bot.runtime_config.refresh(repo)
        await ctx.send(f"✅ Override `{key}` entfernt.", delete_after=5)
