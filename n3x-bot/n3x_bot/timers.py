"""Role-gated per-map base timers with a self-editing overview embed.

Timers are persisted to the repo (fixes v3 B12 in-memory loss), tz-aware
(B6), and the overview loop starts guarded (B4). The pure/logic helpers take
`now` as an argument so they're deterministic in tests; the 1s loop supplies
`datetime.now(ZoneInfo(settings.timezone))`.

The countdown is rendered bot-side to the SECOND, which means the overview
message is repainted once per second while any timer runs. Two things keep that
off Discord's rate limiter:

  * `update_timer_overview` skips the edit entirely when the rendered
    description is byte-identical to the last one it wrote, so an idle overview
    ("No active base timers.") costs zero API calls no matter how long the loop
    runs.
  * the 🔄 reload control is seeded once per message per process instead of on
    every pass — otherwise the loop would burn a second request every tick.

An active timer therefore costs ~1 edit/s (Discord allows roughly 5 per 5s on
the message-edit bucket). discord.py's HTTPClient absorbs any 429 by sleeping,
and `tasks.loop` waits for a slow body before scheduling the next tick, so the
worst case is a countdown that lags rather than a crash or a ban.
"""
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import tasks

from n3x_bot.config import Settings
from n3x_bot.storage.base import StatsRepository

OVERVIEW_TITLE = "🛰️ BASE TIMER OVERVIEW"
NO_TIMERS_TEXT = "No active base timers."
EXPIRED_TEXT = "expired"


def format_countdown(remaining: timedelta) -> str:
    """Render a remaining duration as `M:SS` / `MM:SS`, or `H:MM:SS` past an hour.

    Rounds UP so a timer started for 30 minutes reads "30:00" on the first
    paint (truncating would show 29:59) and only reaches 00:00 at the end.
    """
    total = math.ceil(remaining.total_seconds())
    if total <= 0:
        return EXPIRED_TEXT
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def build_timer_overview_embed(timers: dict[str, datetime],
                               now: datetime) -> discord.Embed:
    embed = discord.Embed(title=OVERVIEW_TITLE, color=discord.Color.blue())
    if not timers:
        embed.description = NO_TIMERS_TEXT
        embed.color = discord.Color.red()
        return embed
    ordered = sorted(timers.items(), key=lambda kv: kv[1])
    lines = []
    for map_name, end_time in ordered:
        # Rendered bot-side (not a <t:unix:R> stamp) because Discord's relative
        # timestamps collapse to a single unit -- "in 24 minutes" -- and can
        # never show the seconds this overview is expected to tick.
        countdown = format_countdown(end_time - now)
        if countdown == EXPIRED_TEXT:
            lines.append(f"📍 **{map_name}** — {EXPIRED_TEXT}")
        else:
            lines.append(f"📍 **{map_name}** — {countdown} remaining")
    embed.description = "\n".join(lines)
    return embed


def has_base_timer_role(member, settings: Settings) -> bool:
    return any(r.id in settings.base_timer_role_ids
               for r in getattr(member, "roles", None) or [])


async def start_base_timer(repo: StatsRepository, settings: Settings,
                           map_name: str, minutes: int,
                           now: datetime) -> datetime:
    if map_name not in settings.allowed_maps_list:
        raise ValueError(f"Invalid map: {map_name}")
    end_time = now + timedelta(minutes=minutes)
    await repo.set_base_timer(map_name, end_time)
    return end_time


async def update_timer_overview(bot, repo: StatsRepository, settings: Settings,
                                now: datetime) -> None:
    # List first, then purge only when something actually expired: at one tick
    # per second an unconditional purge would open a write transaction every
    # second for nothing.
    timers = await repo.list_base_timers()
    expired = [m for m, end_time in timers.items() if end_time <= now]
    if expired:
        await repo.purge_expired_base_timers(now)
        timers = {m: e for m, e in timers.items() if m not in expired}
    embed = build_timer_overview_embed(timers, now)
    channel = bot.get_channel(settings.timer_overview_channel_id)
    if channel is None:
        return
    # Skip the API call when the paint would be a no-op. Guards the idle case:
    # with no timers the description never changes, so the loop goes quiet.
    if getattr(bot, "_timer_overview_last", None) == embed.description:
        return
    try:
        msg = await channel.fetch_message(settings.timer_overview_message_id)
        await msg.edit(content=None, embed=embed)
        bot._timer_overview_last = embed.description
        # Seed the 🔄 reload control ONCE per message per process (Discord
        # dedups the bot's own reaction, but the request still costs a slot on
        # the rate limiter every tick). A user clicking it forces a refresh.
        seeded = getattr(bot, "_timer_overview_reaction_seeded", None)
        if seeded != settings.timer_overview_message_id:
            try:
                await msg.add_reaction("🔄")
                bot._timer_overview_reaction_seeded = \
                    settings.timer_overview_message_id
            except Exception:
                pass
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
        @bot.tree.command(name="base", description="Starts a base timer.")
        @app_commands.describe(map="Map", minutes="Duration in minutes")
        @app_commands.autocomplete(map=_allowed_maps_autocomplete)
        async def base_cmd(interaction, map: str, minutes: int):
            if not has_base_timer_role(interaction.user, bot.runtime_config):
                await interaction.response.send_message(
                    "❌ No permission.", ephemeral=True)
                return
            allowed = bot.runtime_config.allowed_maps_list
            if map not in allowed:
                await interaction.response.send_message(
                    f"❌ Invalid map. Allowed maps: {', '.join(allowed)}",
                    ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            now = datetime.now(ZoneInfo(settings.timezone))
            await start_base_timer(repo, bot.runtime_config, map, minutes, now)
            await update_timer_overview(bot, repo, bot.runtime_config, now)
            # No success message — the overview embed IS the feedback. Delete the
            # deferred ephemeral so no "thinking…" placeholder lingers.
            try:
                await interaction.delete_original_response()
            except Exception:
                pass

    if bot.tree.get_command("basestop") is None:
        @bot.tree.command(name="basestop", description="Stops a base timer.")
        @app_commands.describe(map="Map")
        @app_commands.autocomplete(map=_active_maps_autocomplete)
        async def basestop_cmd(interaction, map: str):
            if not has_base_timer_role(interaction.user, bot.runtime_config):
                await interaction.response.send_message(
                    "❌ No permission.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            now = datetime.now(ZoneInfo(settings.timezone))
            if await repo.remove_base_timer(map):
                await update_timer_overview(bot, repo, bot.runtime_config, now)
                # No success message — overview is the feedback.
                try:
                    await interaction.delete_original_response()
                except Exception:
                    pass
            else:
                # Keep the failure feedback so the user knows nothing happened.
                await interaction.followup.send(
                    f"❌ No active timer for map {map}.", ephemeral=True)


def start_timer_overview_loop(bot, repo: StatsRepository,
                              settings: Settings) -> tasks.Loop:
    existing = getattr(bot, "_timer_overview_loop", None)
    if isinstance(existing, tasks.Loop) and existing.is_running():
        return existing

    # 1s so the rendered countdown ticks per second; update_timer_overview
    # no-ops when the text is unchanged, so an idle overview sends nothing.
    @tasks.loop(seconds=1)
    async def _timer_overview_loop():
        await update_timer_overview(
            bot, repo, bot.runtime_config, datetime.now(ZoneInfo(settings.timezone)))

    bot._timer_overview_loop = _timer_overview_loop
    if not _timer_overview_loop.is_running():
        _timer_overview_loop.start()
    return _timer_overview_loop
