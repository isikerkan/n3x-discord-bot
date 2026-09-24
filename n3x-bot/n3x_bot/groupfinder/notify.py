"""Group Finder DMs: the mandatory 15-minute reminder and the cancel notice.

Every DM is claimed in the database *before* it is sent (gf_reminders). A crash
between claim and send can lose one DM, but no DM is ever sent twice — across
restarts, loop retries and concurrent triggers alike. A DM that fails (closed
DMs, unknown user) is recorded as FAILED and not retried; there is no fallback
ping in the channel.
"""
import logging
import math
from datetime import datetime, timedelta

from n3x_bot.groupfinder import events, zones

log = logging.getLogger("N3X-Bot")

REMINDER = "REMINDER_15M"
CANCEL_NOTICE = "CANCELLED"
REMINDER_BEFORE = timedelta(minutes=15)


def reminder_due(event: dict, now: datetime) -> bool:
    """From 15 minutes before the start until the start. Also true when the
    bot comes back after the 15-minute mark, as long as it has not started."""
    start = event["scheduled_at"]
    return (event["status"] in (events.SCHEDULED, events.CLOSED)
            and start is not None and start - REMINDER_BEFORE <= now < start)


def _minutes_left(start: datetime, now: datetime) -> str:
    minutes = max(1, math.ceil((start - now).total_seconds() / 60))
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


async def _jump_url(bot, repo, event_id: int, zone: str) -> str | None:
    """Link to the event message in the channel of the member's clock (any
    channel as a fallback, e.g. when theirs was removed in the meantime)."""
    rows = await repo.gf_get_event_messages(event_id)
    rows.sort(key=lambda r: not (zone and zones.same_clock(r["zone"], zone)))
    for row in rows:
        channel = bot.get_channel(row["channel_id"])
        guild = getattr(channel, "guild", None)
        if guild is not None:
            return (f"https://discord.com/channels/{guild.id}/"
                    f"{row['channel_id']}/{row['message_id']}")
    return None


async def _send_dm(bot, user_id: int, text: str) -> bool:
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        await user.send(text)
        return True
    except Exception:
        # Forbidden (DMs closed), NotFound, HTTP errors: recorded, not retried.
        log.info("group finder: DM to %s failed", user_id)
        return False


async def _deliver(bot, repo, event_id: int, user_id: int, kind: str, text: str,
                   now: datetime) -> bool | None:
    """Claim, send, record. None when someone else already claimed it."""
    if not await repo.gf_claim_reminder(event_id, user_id, kind, now):
        return None
    ok = await _send_dm(bot, user_id, text)
    await repo.gf_mark_reminder(event_id, user_id, kind,
                                "SENT" if ok else "FAILED", now)
    return ok


async def send_due_reminders(bot, repo, event_id: int, now: datetime) -> int:
    """Send the reminder to every participant who has not had it yet. Called
    by the lifecycle tick and right after a join, so a member joining inside
    the 15-minute window is reminded at once. Returns how many were sent."""
    event = await repo.gf_get_event(event_id)
    if event is None or not reminder_due(event, now):
        return 0
    sent = 0
    for member in await repo.gf_get_participants(event_id):
        uid = member["discord_id"]
        text = (f"🚀 **{event['title']}** starts in "
                f"{_minutes_left(event['scheduled_at'], now)}!")
        link = await _jump_url(bot, repo, event_id, member["zone"])
        if link:
            text += f"\n{link}"
        if await _deliver(bot, repo, event_id, uid, REMINDER, text, now):
            sent += 1
    return sent


async def send_cancel_notices(bot, repo, event: dict, people, actor_id: int,
                              now: datetime) -> int:
    """Tell the participants (as they were before cancelling) that the group
    search is off. Whoever cancelled it is not told about their own action."""
    text = f"❌ **{event['title']}** was cancelled."
    sent = 0
    for uid, _zone in people:
        if uid == actor_id:
            continue
        if await _deliver(bot, repo, event["id"], uid, CANCEL_NOTICE, text, now):
            sent += 1
    return sent
