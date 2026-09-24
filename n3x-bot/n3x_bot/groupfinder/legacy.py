"""Cutover from the single-channel LFG: adopt its channel as the hub and
import the LFGs that are still running.

Both steps are idempotent and safe to run on every start.

The hub is adopted right away, so members can pick a zone. The running LFGs
are imported only once at least one zone is active: before that there is no
channel to show them in, and deleting the old messages would make them vanish.
"""
import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import discord

from n3x_bot.groupfinder import events, provision

log = logging.getLogger("N3X-Bot")
UTC = timezone.utc

LEGACY_RUNNING = ("OPEN", "CONFIRMED", "FULL")
MIGRATED = "MIGRATED"
_ALL_GF_STATUSES = (*events.ACTIVE_STATUSES, events.EXPIRED,
                    events.NO_TIME_FOUND, events.CANCELLED)


def legacy_slot(event_date: str, hhmm: str, tz: ZoneInfo) -> datetime:
    """The old LFG stored a wall-clock date and `HH:MM` in the bot's zone.
    There is nobody to ask about an ambiguous DST time any more, so the first
    occurrence (fold=0) is taken."""
    day = date.fromisoformat(event_date)
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute,
                    tzinfo=tz, fold=0).astimezone(UTC)


async def _running_legacy(repo) -> list[dict]:
    return [r for r in await repo.all_active_lfgs()
            if r["status"] in LEGACY_RUNNING]


async def adopt_legacy_hub(bot, repo, settings) -> bool:
    """Turn the old `group-finder` channel into the hub: move it into the Group
    Finder category with hub permissions and delete the bot's own old posts
    there (the German guide, finished LFGs). Messages written by members are
    left alone, and so are running LFGs — the import removes those once they
    are shown in the zone channels. Returns whether the hub was adopted."""
    if await repo.gf_get_setting(provision.HUB_CHANNEL_KEY):
        return False
    config = getattr(bot, "runtime_config", None) or settings
    legacy_id = getattr(config, "lfg_channel_id", 0)
    channel = bot.get_channel(legacy_id) if legacy_id else None
    if channel is None:
        return False
    guild = channel.guild
    category = await provision.ensure_category(guild, repo)
    await channel.edit(category=category,
                       overwrites=provision.hub_overwrites(guild, settings),
                       topic="Group Finder — pick your timezone here.",
                       reason="Group Finder hub")
    keep = {r["message_id"] for r in await _running_legacy(repo) if r["message_id"]}
    me = bot.user.id
    async for message in channel.history(limit=500):
        if message.author.id == me and message.id not in keep:
            try:
                await message.delete()
            except discord.NotFound:
                pass
            except Exception:
                log.exception("group finder: deleting old post %s failed",
                              message.id)
    await repo.gf_set_setting(provision.HUB_CHANNEL_KEY, str(channel.id))
    log.info("group finder: adopted #%s as the hub", getattr(channel, "name", legacy_id))
    return True


async def _import(repo, row: dict, zone: str, now: datetime) -> int:
    tz = ZoneInfo(zone)
    slots = [legacy_slot(row["event_date"], t, tz) for t in row["start_times"]]
    event_id = await repo.gf_create_event(
        creator_id=row["creator_id"], title=row["title"],
        min_players=row["min_players"], max_players=row["max_players"],
        origin_zone=zone, slots=slots, status=events.VOTING,
        created_at=row["created_at"] or now,
        cleanup_at=max(slots) + events.CLEANUP_AFTER, legacy_lfg_id=row["id"])
    # Availability carries over as votes; the old system had no zones, so the
    # voters are placed in the bot's zone (the one the old times were read in).
    per_user: dict[int, list[datetime]] = {}
    for hhmm, ids in (await repo.get_lfg_availability(row["id"])).items():
        for uid in ids:
            per_user.setdefault(uid, []).append(legacy_slot(row["event_date"], hhmm, tz))
    for uid, uslots in per_user.items():
        await repo.gf_set_votes(event_id, uid, uslots, zone, now)
    if row["status"] in ("CONFIRMED", "FULL") and row["confirmed_time"]:
        start = legacy_slot(row["event_date"], row["confirmed_time"], tz)
        roster = [(uid, zone) for uid in
                  await repo.get_lfg_participants(row["id"])][:row["max_players"]]
        closed = (len(roster) >= row["max_players"]
                  or start - events.VOTE_CLOSE <= now)
        await repo.gf_schedule_event(
            event_id, starts_at=start,
            status=events.CLOSED if closed else events.SCHEDULED,
            cleanup_at=start + events.CLEANUP_AFTER,
            closed_at=now if closed else None, participants=roster,
            joined_at=now, expect_status=events.VOTING)
        # The old system never pinged anyone; a "time found" ping now, for a
        # time that was fixed long ago, would come out of nowhere.
        await repo.gf_claim_time_found(event_id, now)
    return event_id


async def _delete_legacy_message(bot, row: dict) -> None:
    channel = bot.get_channel(row["channel_id"]) if row["message_id"] else None
    if channel is None:
        return
    try:
        await (await channel.fetch_message(row["message_id"])).delete()
    except discord.NotFound:
        pass
    except Exception:
        log.exception("group finder: deleting legacy LFG %s message failed",
                      row["id"])


async def migrate_legacy_lfgs(bot, repo, settings, now: datetime) -> list[int]:
    """Import every running legacy LFG. Only once a zone is active (see the
    module docstring). Idempotent twice over: imported rows are marked
    MIGRATED, and a row whose event already exists (a crash between import
    and marking) is not imported again. Returns the new event ids."""
    running = await _running_legacy(repo)
    if not running or not await provision.active_zones(repo):
        return []
    zone = settings.timezone
    imported = {e["legacy_lfg_id"]: e["id"]
                for e in await repo.gf_events_with_status(list(_ALL_GF_STATUSES))
                if e["legacy_lfg_id"]}
    created = []
    for row in running:
        if row["id"] not in imported:
            created.append(await _import(repo, row, zone, now))
        await _delete_legacy_message(bot, row)
        await repo.set_lfg_status(row["id"], MIGRATED)
    if created:
        log.info("group finder: migrated %d legacy LFG(s)", len(created))
    return created
