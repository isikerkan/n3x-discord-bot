"""Group Finder embeds. Pure: event data in, embed out, for one zone.

Every zone channel renders the same event; only the local times differ. No
foreign timezone is ever shown.
"""
import math
from datetime import datetime
from zoneinfo import ZoneInfo

import discord

from n3x_bot.groupfinder import events

_CLOCKS = ("🕛", "🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚")


def clock(local: datetime) -> str:
    return _CLOCKS[local.hour % 12]


def day_label(local: datetime) -> str:
    """`Sat, 26.09.`"""
    return local.strftime("%a, %d.%m.")


def time_label(local: datetime) -> str:
    return local.strftime("%H:%M")


def slot_label(slot: datetime, tz: ZoneInfo, header_day) -> str:
    """`19:00`, or `Sun, 27.09. 02:00` when the time falls on another day in
    this zone than the event's first time (e.g. 23:00 Berlin = 02:00 Karachi)."""
    local = slot.astimezone(tz)
    if local.date() == header_day:
        return time_label(local)
    return f"{day_label(local)} {time_label(local)}"


def countdown(start: datetime, now: datetime) -> str:
    """`Starting in 2h 15m` — rounded up to steps so the text, and with it the
    message, only changes every 15 min (> 1 h), 5 min (> 15 min) or 1 min."""
    minutes = math.ceil((start - now).total_seconds() / 60)
    if minutes <= 0:
        return "Starting now"
    step = 15 if minutes > 60 else 5 if minutes > 15 else 1
    minutes = math.ceil(minutes / step) * step
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"Starting in {hours}h {rest}m"
    return f"Starting in {hours}h" if hours else f"Starting in {rest}m"


def started_ago(start: datetime, now: datetime) -> str:
    """`Started 5m ago`, in 5-minute steps: per-minute text would mean an
    hour of edits in every zone channel for nothing."""
    minutes = max(0, int((now - start).total_seconds() // 60)) // 5 * 5
    return "Started just now" if minutes == 0 else f"Started {minutes}m ago"


def _mentions(people) -> str:
    return " ".join(f"<@{uid}>" for uid, _zone in people)


def build_event_embed(event: dict, zone: str, *, counts: dict,
                      people: list, now: datetime) -> discord.Embed:
    tz = ZoneInfo(zone)
    status = event["status"]
    if status in events.FIXED_STATUSES:
        return _fixed_embed(event, tz, people, now)
    if status == events.NO_TIME_FOUND:
        return discord.Embed(title=f"❌ {event['title']}",
                             description="No time found.",
                             color=discord.Color.dark_grey())
    return _voting_embed(event, tz, counts, people)


def _voting_embed(event, tz, counts, people) -> discord.Embed:
    first = event["slots"][0].astimezone(tz)
    lines = [f"{day_label(first)} · {event['min_players']}–"
             f"{event['max_players']} players", ""]
    for slot in event["slots"]:
        n = counts.get(slot, 0)
        lines.append(f"{clock(slot.astimezone(tz))} "
                     f"{slot_label(slot, tz, first.date())} — "
                     f"{n} vote{'' if n == 1 else 's'}")
    lines += ["", f"👥 {len(people)} participant{'' if len(people) == 1 else 's'}"]
    if people:
        lines.append(_mentions(people))
    return discord.Embed(title=f"🔎 {event['title']}",
                         description="\n".join(lines),
                         color=discord.Color.blurple())


def _fixed_embed(event, tz, people, now) -> discord.Embed:
    start = event["scheduled_at"]
    local = start.astimezone(tz)
    if event["status"] == events.STARTED:
        return discord.Embed(title=f"🔵 {event['title']}",
                             description=started_ago(start, now),
                             color=discord.Color.blue())
    lines = [f"🟢 {countdown(start, now)}",
             f"{day_label(local)} · {time_label(local)}", "",
             f"👥 {len(people)}/{event['max_players']} participants"]
    if people:
        lines.append(_mentions(people))
    if event["status"] == events.CLOSED:
        full = len(people) >= event["max_players"]
        lines += ["", "🔒 Group is full." if full else "🔒 Joining is closed."]
    return discord.Embed(title=f"🎉 {event['title']}",
                         description="\n".join(lines),
                         color=discord.Color.green())
