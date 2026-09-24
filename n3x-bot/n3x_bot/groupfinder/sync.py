"""Keep every zone channel's view of an event in line with the one event row.

`sync_event` renders the event once per active zone and edits (or posts) the
message in that zone's channel. A zone activated after the event was created
simply has no message yet and gets one. Messages of zones that are gone are
forgotten. Per event the work is serialized, and an edit is skipped when the
rendered result is identical to what was last written — votes from several
members in quick succession then cost one edit per changed channel, not one
per click per channel.
"""
import asyncio
import logging
from datetime import datetime

import discord

from n3x_bot.groupfinder import events, provision, render, views, zones

log = logging.getLogger("N3X-Bot")


def _event_lock(bot, event_id: int) -> asyncio.Lock:
    locks = getattr(bot, "_gf_event_locks", None)
    if not isinstance(locks, dict):
        locks = {}
        bot._gf_event_locks = locks
    return locks.setdefault(event_id, asyncio.Lock())


def _rendered(bot) -> dict:
    cache = getattr(bot, "_gf_rendered", None)
    if not isinstance(cache, dict):
        cache = {}
        bot._gf_rendered = cache
    return cache


TIME_FOUND = "✅ **Time found!**"


def _signature(embed, view, content=None):
    return (content, embed.title, embed.description,
            repr(view.to_components()) if view is not None else None)


def _content(event) -> str | None:
    """The line above the embed. Once the time was announced it stays as a
    plain `Time found!` — the mentions of the announcement are dropped on the
    next edit (edits never notify), the embed keeps listing everyone."""
    if (event["status"] in (events.SCHEDULED, events.CLOSED)
            and event["time_found_notified_at"] is not None):
        return TIME_FOUND
    return None


def _mentions(people) -> str:
    return " ".join(f"<@{uid}>" for uid, _zone in people)


async def sync_event(bot, repo, settings, event_id: int, now: datetime) -> None:
    async with _event_lock(bot, event_id):
        event = await repo.gf_get_event(event_id)
        if (event is None or event["cleaned_at"] is not None
                or event["status"] in (events.EXPIRED, events.CANCELLED)):
            return      # finished: its messages are being / have been removed
        if (event["status"] in events.FIXED_STATUSES
                and event["time_found_notified_at"] is None):
            # Claim first, then post: a crash in between loses the ping at
            # most — it can never be sent twice.
            if await repo.gf_claim_time_found(event_id, now):
                if event["status"] != events.STARTED:   # too late to be useful
                    await _announce(bot, repo, settings, event, now)
                    return
            event = await repo.gf_get_event(event_id)
        votes = await repo.gf_get_votes(event_id)
        counts = events.vote_counts(event, votes)
        people = await events.participants(repo, event)
        existing = {m["zone"]: m for m in await repo.gf_get_event_messages(event_id)}
        cache = _rendered(bot)
        active = await provision.active_zones(repo)
        content = _content(event)
        for zone_row in active:
            zone = zone_row["zone"]
            channel = bot.get_channel(zone_row["channel_id"])
            if channel is None:
                continue
            embed = render.build_event_embed(event, zone, counts=counts,
                                             people=people, now=now)
            view = views.view_for(repo, settings, event, zone, now)
            signature = _signature(embed, view, content)
            row = existing.get(zone)
            if row is not None and row["channel_id"] == channel.id:
                if cache.get(row["message_id"]) == signature:
                    continue
                try:
                    message = await channel.fetch_message(row["message_id"])
                    await message.edit(content=content, embed=embed, view=view)
                    cache[row["message_id"]] = signature
                    continue
                except discord.NotFound:
                    pass            # deleted -> repost below
                except Exception:
                    log.exception("group finder: editing event %s in %s failed",
                                  event_id, zone)
                    continue        # never repost on anything but NotFound
            try:
                # a plain (re)post never pings anyone
                message = await channel.send(
                    content=content, embed=embed, view=view,
                    allowed_mentions=discord.AllowedMentions.none())
            except Exception:
                log.exception("group finder: posting event %s in %s failed",
                              event_id, zone)
                continue
            await repo.gf_set_event_message(event_id, zone, channel.id,
                                            message.id, now)
            cache[message.id] = signature
        active_zones = {z["zone"] for z in active}
        for zone in existing:
            if zone not in active_zones:
                await repo.gf_delete_event_message(event_id, zone)


async def _announce(bot, repo, settings, event, now) -> None:
    """The one-time "time found" notification. Discord only notifies on
    mentions in the content of a newly sent message, so the event message is
    deleted and posted again in every zone channel.

    The text is the same in every channel and mentions every participant, but
    each message only *allows* the mentions of that channel's clock. Everyone
    sees the full list, and each member is notified exactly once — admins who
    see every zone channel are not pinged once per channel. Matching is by
    clock, not by name: a member stored as Europe/Berlin is pinged in the
    Europe/Zurich channel when that is the one their clock maps to.
    """
    event_id = event["id"]
    people = await events.participants(repo, event)
    counts = events.vote_counts(event, await repo.gf_get_votes(event_id))
    content = f"{TIME_FOUND} {_mentions(people)}".strip()
    existing = {m["zone"]: m for m in await repo.gf_get_event_messages(event_id)}
    cache = _rendered(bot)
    active = await provision.active_zones(repo)
    for zone_row in active:
        zone = zone_row["zone"]
        channel = bot.get_channel(zone_row["channel_id"])
        if channel is None:
            continue
        old = existing.get(zone)
        if old is not None and old["channel_id"] == channel.id:
            try:
                await (await channel.fetch_message(old["message_id"])).delete()
            except discord.NotFound:
                pass
            except Exception:
                log.exception("group finder: removing pre-announcement message "
                              "of event %s in %s failed", event_id, zone)
            cache.pop(old["message_id"], None)
        embed = render.build_event_embed(event, zone, counts=counts,
                                         people=people, now=now)
        view = views.view_for(repo, settings, event, zone, now)
        allowed = discord.AllowedMentions(
            everyone=False, roles=False, replied_user=False,
            users=[discord.Object(id=uid) for uid, z in people
                   if z and zones.same_clock(z, zone)])
        try:
            message = await channel.send(content=content, embed=embed,
                                         view=view, allowed_mentions=allowed)
        except Exception:
            log.exception("group finder: announcing event %s in %s failed",
                          event_id, zone)
            continue
        await repo.gf_set_event_message(event_id, zone, channel.id, message.id,
                                        now)
        cache[message.id] = _signature(embed, view, content)
    active_zones = {z["zone"] for z in active}
    for zone in existing:
        if zone not in active_zones:
            await repo.gf_delete_event_message(event_id, zone)


async def remove_event_messages(bot, repo, event_id: int) -> bool:
    """Delete the event's message in every zone channel. A message that is
    already gone, or whose channel is gone, counts as removed. Returns True
    once nothing is left; on any other error the row is kept and the next
    lifecycle tick tries again."""
    async with _event_lock(bot, event_id):
        cache = _rendered(bot)
        all_gone = True
        for row in await repo.gf_get_event_messages(event_id):
            channel = bot.get_channel(row["channel_id"])
            if channel is not None:
                try:
                    message = await channel.fetch_message(row["message_id"])
                    await message.delete()
                except discord.NotFound:
                    pass
                except Exception:
                    log.exception("group finder: removing event %s in %s failed",
                                  event_id, row["zone"])
                    all_gone = False
                    continue
            await repo.gf_delete_event_message(event_id, row["zone"])
            cache.pop(row["message_id"], None)
        return all_gone


async def sync_all(bot, repo, settings, now: datetime) -> None:
    """Every active event — after a zone was added, and on startup."""
    for event in await repo.gf_events_with_status(list(events.ACTIVE_STATUSES)):
        try:
            await sync_event(bot, repo, settings, event["id"], now)
        except Exception:
            log.exception("group finder: syncing event %s failed", event["id"])
