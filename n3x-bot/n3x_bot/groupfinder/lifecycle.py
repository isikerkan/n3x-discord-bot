"""The Group Finder background loop: one tick per minute drives the countdown,
the state machine and the cleanup of every event.

Everything it needs is in the database — deadlines, statuses, which messages
exist — so after a restart the first tick simply catches up. The tick body is
guarded: a failing event (or a Postgres restart) is logged and the loop goes
on, the lesson from the base-timer outage of 2026-09-22.
"""
import logging
from datetime import datetime, timezone

from discord.ext import tasks

from n3x_bot.groupfinder import events, notify, sync

log = logging.getLogger("N3X-Bot")
UTC = timezone.utc

# Every status whose messages may still be in the channels.
_TRACKED = (*events.ACTIVE_STATUSES, events.EXPIRED, events.NO_TIME_FOUND,
            events.CANCELLED)


async def process_event(bot, repo, settings, event: dict, now: datetime) -> None:
    event = await events.advance(repo, event, now)
    if events.needs_cleanup(event, now):
        if await sync.remove_event_messages(bot, repo, event["id"]):
            await repo.gf_update_event(event["id"], cleaned_at=now)
        return
    # Re-render: the countdown text, a closed Join button, "Started", "No time
    # found" — and the one-time "time found" announcement if it is still due.
    # sync_event skips every channel whose render did not change.
    await sync.sync_event(bot, repo, settings, event["id"], now)
    await notify.send_due_reminders(bot, repo, event["id"], now)


async def tick(bot, repo, settings, now: datetime) -> None:
    for event in await repo.gf_events_with_status(list(_TRACKED),
                                                  uncleaned_only=True):
        try:
            await process_event(bot, repo, settings, event, now)
        except Exception:
            log.exception("group finder: lifecycle of event %s failed",
                          event["id"])


def start_lifecycle_loop(bot, repo, settings) -> tasks.Loop:
    existing = getattr(bot, "_gf_lifecycle_loop", None)
    if isinstance(existing, tasks.Loop) and existing.is_running():
        return existing

    @tasks.loop(minutes=1)
    async def _gf_lifecycle_loop():
        try:
            await tick(bot, repo, settings, datetime.now(UTC))
        except Exception:
            log.exception("group finder: lifecycle tick failed")

    bot._gf_lifecycle_loop = _gf_lifecycle_loop
    if not _gf_lifecycle_loop.is_running():
        _gf_lifecycle_loop.start()
    return _gf_lifecycle_loop
