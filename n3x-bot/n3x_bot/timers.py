"""Role-gated per-map base timers with a self-editing overview embed.

Timers are persisted to the repo (fixes v3 B12 in-memory loss), tz-aware
(B6), and the overview loop starts guarded (B4). The pure/logic helpers take
`now` as an argument so they're deterministic in tests; the 30s loop supplies
`datetime.now(ZoneInfo(settings.timezone))`.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

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
        remaining = max(0, int((end_time - now).total_seconds() // 60))
        lines.append(f"📍 **{map_name}** — {remaining} Min")
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
    if bot.get_command("base") is None:
        async def _base_cmd(ctx, map_name: str, zeit: int):
            if not has_base_timer_role(ctx.author, settings):
                await ctx.send("❌ Keine Berechtigung.", delete_after=5)
                return
            now = datetime.now(ZoneInfo(settings.timezone))
            try:
                await start_base_timer(repo, settings, map_name, zeit, now)
            except ValueError:
                await ctx.send(
                    f"❌ Ungültige Map. Erlaubte Maps: "
                    f"{', '.join(settings.allowed_maps_list)}", delete_after=10)
                return
            await update_timer_overview(bot, repo, settings, now)
            await ctx.send(f"✅ Timer für {map_name} gestartet ({zeit} Min).",
                           delete_after=5)
        bot.add_command(commands.Command(_base_cmd, name="base"))

    if bot.get_command("basestop") is None:
        async def _basestop_cmd(ctx, map_name: str):
            if not has_base_timer_role(ctx.author, settings):
                await ctx.send("❌ Keine Berechtigung.", delete_after=5)
                return
            now = datetime.now(ZoneInfo(settings.timezone))
            if await repo.remove_base_timer(map_name):
                await update_timer_overview(bot, repo, settings, now)
                await ctx.send(f"✅ Timer für {map_name} gestoppt.",
                               delete_after=5)
            else:
                await ctx.send(f"❌ Kein aktiver Timer für Map {map_name}.",
                               delete_after=5)
        bot.add_command(commands.Command(_basestop_cmd, name="basestop"))


def start_timer_overview_loop(bot, repo: StatsRepository,
                              settings: Settings) -> tasks.Loop:
    existing = getattr(bot, "_timer_overview_loop", None)
    if isinstance(existing, tasks.Loop) and existing.is_running():
        return existing

    @tasks.loop(seconds=30)
    async def _timer_overview_loop():
        await update_timer_overview(
            bot, repo, settings, datetime.now(ZoneInfo(settings.timezone)))

    bot._timer_overview_loop = _timer_overview_loop
    if not _timer_overview_loop.is_running():
        _timer_overview_loop.start()
    return _timer_overview_loop
