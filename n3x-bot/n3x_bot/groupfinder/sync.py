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

from n3x_bot.groupfinder import events, provision, render, views

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


def _signature(embed, view):
    return (embed.title, embed.description,
            repr(view.to_components()) if view is not None else None)


async def sync_event(bot, repo, settings, event_id: int, now: datetime) -> None:
    async with _event_lock(bot, event_id):
        event = await repo.gf_get_event(event_id)
        if event is None:
            return
        votes = await repo.gf_get_votes(event_id)
        counts = events.vote_counts(event, votes)
        people = await events.participants(repo, event)
        existing = {m["zone"]: m for m in await repo.gf_get_event_messages(event_id)}
        cache = _rendered(bot)
        active = await provision.active_zones(repo)
        for zone_row in active:
            zone = zone_row["zone"]
            channel = bot.get_channel(zone_row["channel_id"])
            if channel is None:
                continue
            embed = render.build_event_embed(event, zone, counts=counts,
                                             people=people, now=now)
            view = views.view_for(repo, settings, event, zone, now)
            signature = _signature(embed, view)
            row = existing.get(zone)
            if row is not None and row["channel_id"] == channel.id:
                if cache.get(row["message_id"]) == signature:
                    continue
                try:
                    message = await channel.fetch_message(row["message_id"])
                    await message.edit(content=None, embed=embed, view=view)
                    cache[row["message_id"]] = signature
                    continue
                except discord.NotFound:
                    pass            # deleted -> repost below
                except Exception:
                    log.exception("group finder: editing event %s in %s failed",
                                  event_id, zone)
                    continue        # never repost on anything but NotFound
            try:
                message = await channel.send(embed=embed, view=view)
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


async def sync_all(bot, repo, settings, now: datetime) -> None:
    """Every active event — after a zone was added, and on startup."""
    for event in await repo.gf_events_with_status(list(events.ACTIVE_STATUSES)):
        try:
            await sync_event(bot, repo, settings, event["id"], now)
        except Exception:
            log.exception("group finder: syncing event %s failed", event["id"])
