from datetime import date, datetime
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands

from n3x_bot.config import Settings
from n3x_bot.storage.base import StatsRepository


# ── pure logic ─────────────────────────────────────────────────────────────

def elapsed_seconds(join_dt: datetime, leave_dt: datetime) -> int:
    return int((leave_dt - join_dt).total_seconds())


def next_streak(prev: dict | None, today: date) -> dict:
    t = today.isoformat()
    if prev is None:
        return {"current_streak": 1, "last_active_date": t, "max_streak": 1}
    if prev["last_active_date"] == t:
        return prev
    last = date.fromisoformat(prev["last_active_date"])
    delta = (today - last).days
    if delta == 1:
        cur = prev["current_streak"] + 1
        mx = max(prev["max_streak"], cur)
        return {"current_streak": cur, "last_active_date": t, "max_streak": mx}
    return {"current_streak": 1, "last_active_date": t, "max_streak": prev["max_streak"]}


def is_night(dt: datetime) -> bool:
    return 0 <= dt.hour < 5


def next_night(prev: dict | None, today: date) -> dict | None:
    t = today.isoformat()
    if prev is None:
        return {"night_count": 1, "last_night_date": t}
    if prev["last_night_date"] == t:
        return prev
    return {"night_count": prev["night_count"] + 1, "last_night_date": t}


def now_local(settings: Settings) -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


def today_local(settings: Settings) -> date:
    return now_local(settings).date()


# ── event helpers ──────────────────────────────────────────────────────────

async def record_message_activity(repo: StatsRepository, settings: Settings,
                                   member_id: int, now: datetime) -> None:
    await repo.add_activity(member_id, "messages", 1)
    today = now.date()
    prev = await repo.get_streak(member_id)
    new = next_streak(prev, today)
    if new != prev:
        await repo.set_streak(member_id, new["current_streak"],
                              new["last_active_date"], new["max_streak"])
    if is_night(now):
        prev_n = await repo.get_night(member_id)
        new_n = next_night(prev_n, today)
        if new_n is not None and new_n != prev_n:
            await repo.set_night(member_id, new_n["night_count"],
                                 new_n["last_night_date"])


async def handle_voice_state_update(bot, repo: StatsRepository, settings: Settings,
                                    member, before, after, now: datetime) -> None:
    if getattr(member, "bot", False):
        return
    times = bot.voice_join_times
    b = before.channel
    a = after.channel
    if b is None and a is not None:
        times[member.id] = now
    elif b is not None and a is None:
        join = times.pop(member.id, None)
        if join is not None:
            secs = elapsed_seconds(join, now)
            if secs > 0:
                await repo.add_activity(member.id, "voice_seconds", secs)
    elif b is not None and a is not None and b.id != a.id:
        join = times.pop(member.id, None)
        if join is not None:
            secs = elapsed_seconds(join, now)
            if secs > 0:
                await repo.add_activity(member.id, "voice_seconds", secs)
        times[member.id] = now


async def handle_activity_reaction(bot, repo: StatsRepository, settings: Settings,
                                   payload) -> None:
    member = getattr(payload, "member", None)
    if member is None or getattr(member, "bot", False):
        return
    if payload.channel_id in (settings.gate_input_channel_id,
                              settings.gate_stats_channel_id):
        return
    await repo.add_activity(payload.user_id, "reactions", 1)


def _format_voice(vsecs: int) -> str:
    return f"{vsecs // 3600}h {vsecs % 3600 // 60}m"


def register_activity(bot, repo: StatsRepository, settings: Settings) -> None:
    if bot.get_command("activity") is not None:
        return

    async def _activity_cmd(ctx, member: discord.Member = None):
        target = member or ctx.author
        msgs = await repo.get_activity(target.id, "messages")
        reacts = await repo.get_activity(target.id, "reactions")
        vsecs = await repo.get_activity(target.id, "voice_seconds")
        streak = await repo.get_streak(target.id) or {"current_streak": 0, "max_streak": 0}
        night = await repo.get_night(target.id) or {"night_count": 0}

        embed = discord.Embed(
            title=f"📊 Aktivität von {target.display_name}",
            color=discord.Color.blurple())
        embed.add_field(name="💬 Nachrichten", value=str(msgs))
        embed.add_field(name="👍 Reaktionen", value=str(reacts))
        embed.add_field(name="🎙️ Voice", value=_format_voice(vsecs))
        embed.add_field(name="🔥 Streak",
                        value=f"{streak['current_streak']} (Max {streak['max_streak']})")
        embed.add_field(name="🌙 Nachtaktiv", value=str(night["night_count"]))
        await ctx.send(embed=embed)

    bot.add_command(commands.Command(_activity_cmd, name="activity"))
