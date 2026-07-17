"""Role-gated per-map base timers with a self-editing overview embed.

Timers are persisted to the repo (fixes v3 B12 in-memory loss), tz-aware
(B6), and the overview loop starts guarded (B4). The pure/logic helpers take
`now` as an argument so they're deterministic in tests; the 30s loop supplies
`datetime.now(ZoneInfo(settings.timezone))`.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import tasks

from n3x_bot.config import Settings
from n3x_bot.storage.base import StatsRepository


def build_timer_overview_embed(timers: dict[str, datetime],
                               now: datetime) -> discord.Embed:
    embed = discord.Embed(title="🛰️ BASE TIMER ÜBERSICHT",
                          color=discord.Color.blue())
    if not timers:
        embed.description = "Keine aktiven Base Timer."
        embed.color = discord.Color.red()
        return embed
    ordered = sorted(timers.items(), key=lambda kv: kv[1])
    lines = []
    for map_name, end_time in ordered:
        # Discord relative timestamp: the client renders <t:unix:R> as a live
        # countdown ("in 24 Minuten") that ticks without any bot edits — the
        # 30s loop only purges expired rows; it never needs to repaint numbers.
        unix = int(end_time.timestamp())
        lines.append(f"📍 **{map_name}** — endet <t:{unix}:R>")
    embed.description = "\n".join(lines)
    return embed


def has_base_timer_role(member, settings: Settings) -> bool:
    return bool(settings.base_timer_role_id) and any(
        r.id == settings.base_timer_role_id
        for r in getattr(member, "roles", []))


async def start_base_timer(repo: StatsRepository, settings: Settings,
                           map_name: str, minutes: int,
                           now: datetime) -> datetime:
    if map_name not in settings.allowed_maps_list:
        raise ValueError(f"Ungültige Map: {map_name}")
    end_time = now + timedelta(minutes=minutes)
    await repo.set_base_timer(map_name, end_time)
    return end_time


async def update_timer_overview(bot, repo: StatsRepository, settings: Settings,
                                now: datetime) -> None:
    await repo.purge_expired_base_timers(now)
    timers = await repo.list_base_timers()
    embed = build_timer_overview_embed(timers, now)
    channel = bot.get_channel(settings.timer_overview_channel_id)
    if channel is None:
        return
    try:
        msg = await channel.fetch_message(settings.timer_overview_message_id)
        await msg.edit(content=None, embed=embed)
    except Exception:
        pass


def register_timer_commands(bot, repo: StatsRepository,
                            settings: Settings) -> None:
    async def _allowed_maps_autocomplete(interaction, current: str):
        needle = current.lower()
        return [app_commands.Choice(name=m, value=m)
                for m in bot.runtime_config.allowed_maps_list
                if needle in m.lower()][:25]

    async def _active_maps_autocomplete(interaction, current: str):
        needle = current.lower()
        active = await repo.list_base_timers()
        return [app_commands.Choice(name=m, value=m)
                for m in active if needle in m.lower()][:25]

    if bot.tree.get_command("base") is None:
        @bot.tree.command(name="base", description="Startet einen Base-Timer.")
        @app_commands.describe(map="Map", zeit="Dauer in Minuten")
        @app_commands.autocomplete(map=_allowed_maps_autocomplete)
        async def base_cmd(interaction, map: str, zeit: int):
            if not has_base_timer_role(interaction.user, bot.runtime_config):
                await interaction.response.send_message(
                    "❌ Keine Berechtigung.", ephemeral=True)
                return
            allowed = bot.runtime_config.allowed_maps_list
            if map not in allowed:
                await interaction.response.send_message(
                    f"❌ Ungültige Map. Erlaubte Maps: {', '.join(allowed)}",
                    ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            now = datetime.now(ZoneInfo(settings.timezone))
            await start_base_timer(repo, bot.runtime_config, map, zeit, now)
            await update_timer_overview(bot, repo, bot.runtime_config, now)
            await interaction.followup.send(
                f"✅ Timer für {map} gestartet ({zeit} Min).", ephemeral=True)

    if bot.tree.get_command("basestop") is None:
        @bot.tree.command(name="basestop", description="Stoppt einen Base-Timer.")
        @app_commands.describe(map="Map")
        @app_commands.autocomplete(map=_active_maps_autocomplete)
        async def basestop_cmd(interaction, map: str):
            if not has_base_timer_role(interaction.user, bot.runtime_config):
                await interaction.response.send_message(
                    "❌ Keine Berechtigung.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            now = datetime.now(ZoneInfo(settings.timezone))
            if await repo.remove_base_timer(map):
                await update_timer_overview(bot, repo, bot.runtime_config, now)
                await interaction.followup.send(
                    f"✅ Timer für {map} gestoppt.", ephemeral=True)
            else:
                await interaction.followup.send(
                    f"❌ Kein aktiver Timer für Map {map}.", ephemeral=True)


def start_timer_overview_loop(bot, repo: StatsRepository,
                              settings: Settings) -> tasks.Loop:
    existing = getattr(bot, "_timer_overview_loop", None)
    if isinstance(existing, tasks.Loop) and existing.is_running():
        return existing

    @tasks.loop(seconds=30)
    async def _timer_overview_loop():
        await update_timer_overview(
            bot, repo, bot.runtime_config, datetime.now(ZoneInfo(settings.timezone)))

    bot._timer_overview_loop = _timer_overview_loop
    if not _timer_overview_loop.is_running():
        _timer_overview_loop.start()
    return _timer_overview_loop
