"""Zone lifecycle on the Discord side: category, zone roles and channels, the
hub channel, and deactivation when a channel disappears.

The database is the source of truth for what exists; Discord objects are
looked up by their tracked ids. Every function here is idempotent — running it
twice never creates a second category, role or channel.
"""
import logging
from datetime import datetime

import discord

from n3x_bot.groupfinder import zones
from n3x_bot.groupfinder.zones import ACTIVE, DEACTIVATED

log = logging.getLogger("N3X-Bot")

CATEGORY_KEY = "category_id"
HUB_CHANNEL_KEY = "hub_channel_id"
_REASON = "Group Finder"


def _admin_roles(guild, settings) -> list:
    return [r for r in (guild.get_role(rid) for rid in settings.admin_role_ids)
            if r is not None]


def _bot_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True, send_messages=True, embed_links=True,
        read_message_history=True, add_reactions=True, manage_messages=True)


def _admin_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True, read_message_history=True, send_messages=True,
        use_application_commands=True)


def zone_overwrites(guild, role, settings) -> dict:
    """Only the zone role (and admins) can see a zone channel. Members interact
    through selects, buttons and slash commands, which need no send permission,
    so they cannot post — the channel stays a clean list of group searches."""
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        role: discord.PermissionOverwrite(
            view_channel=True, read_message_history=True,
            use_application_commands=True, send_messages=False),
    }
    for admin in _admin_roles(guild, settings):
        overwrites[admin] = _admin_overwrite()
    overwrites[guild.me] = _bot_overwrite()
    return overwrites


def hub_overwrites(guild, settings) -> dict:
    """Everyone sees the hub (it is where members pick their zone), nobody but
    the bot and admins posts in it."""
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True, read_message_history=True,
            use_application_commands=True, send_messages=False),
    }
    for admin in _admin_roles(guild, settings):
        overwrites[admin] = _admin_overwrite()
    overwrites[guild.me] = _bot_overwrite()
    return overwrites


async def ensure_category(guild, repo):
    """The tracked Group Finder category, created if missing."""
    raw = await repo.gf_get_setting(CATEGORY_KEY)
    category = guild.get_channel(int(raw)) if raw else None
    if category is not None:
        return category
    category = await guild.create_category(zones.CATEGORY_NAME, reason=_REASON)
    await repo.gf_set_setting(CATEGORY_KEY, str(category.id))
    return category


async def ensure_hub_channel(guild, repo, settings):
    """The tracked hub channel, created in the category if missing.

    At cutover the legacy `group-finder` channel is adopted instead (it is
    tracked before setup ever runs), so this only creates a hub on a fresh
    server or after the hub was deleted by hand.
    """
    raw = await repo.gf_get_setting(HUB_CHANNEL_KEY)
    hub = guild.get_channel(int(raw)) if raw else None
    if hub is not None:
        return hub
    category = await ensure_category(guild, repo)
    hub = await guild.create_text_channel(
        zones.HUB_CHANNEL_NAME, category=category,
        overwrites=hub_overwrites(guild, settings),
        topic="Group Finder — pick your timezone here.", reason=_REASON)
    await repo.gf_set_setting(HUB_CHANNEL_KEY, str(hub.id))
    return hub


async def activate_zone(guild, repo, settings, zone: str,
                        now: datetime) -> tuple[str, str]:
    """Make sure a channel exists for `zone`'s clock.

    Returns `(outcome, channel_zone)`:
      * `exists`      — `zone` itself already has an active channel
      * `covered`     — another zone with the same clock has one (Zurich when
                        Berlin exists); `channel_zone` is that zone
      * `created`     — a new role and channel were made for `zone`
      * `reactivated` — a deactivated zone with this clock got its channel
                        back, reusing its role instead of creating a duplicate
    Raises ValueError for an unknown IANA id.
    """
    if not zones.is_valid_zone(zone):
        raise ValueError(f"Unknown timezone: {zone}")
    rows = await repo.gf_all_zones()
    live = [r["zone"] for r in rows
            if r["status"] == ACTIVE and r["channel_id"]
            and guild.get_channel(r["channel_id"]) is not None]
    hit = zone if zone in live else zones.find_same_clock(zone, live)
    if hit is not None:
        return ("exists" if hit == zone else "covered"), hit
    tracked = [r["zone"] for r in rows]
    target = zone if zone in tracked else (zones.find_same_clock(zone, tracked)
                                           or zone)
    row = await repo.gf_get_zone(target)
    category = await ensure_category(guild, repo)
    role = guild.get_role(row["role_id"]) if row and row["role_id"] else None
    if role is None:
        role = await guild.create_role(name=zones.role_name(target),
                                       mentionable=False, reason=_REASON)
    channel = await guild.create_text_channel(
        zones.channel_name(target), category=category,
        overwrites=zone_overwrites(guild, role, settings),
        topic=f"Group Finder · {target} and every timezone with the same clock",
        reason=_REASON)
    await repo.gf_save_zone(target, role_id=role.id, channel_id=channel.id,
                            status=ACTIVE, now=now)
    return ("created" if row is None else "reactivated"), target


async def delete_zone(guild, repo, zone: str, now: datetime) -> bool:
    """Delete the zone's channel and role (the explicit admin command).

    The row is marked DEACTIVATED with both ids cleared *before* the Discord
    objects go, so the channel-delete listener finds no zone for the channel
    and does not process it a second time. Member assignments are dropped.
    Returns False for an unknown zone.
    """
    row = await repo.gf_get_zone(zone)
    if row is None:
        return False
    await repo.gf_save_zone(zone, role_id=None, channel_id=None,
                            status=DEACTIVATED, now=now)
    await repo.gf_clear_zone_members(zone)
    channel = guild.get_channel(row["channel_id"]) if row["channel_id"] else None
    if channel is not None:
        try:
            await channel.delete(reason=_REASON)
        except discord.NotFound:
            pass
    role = guild.get_role(row["role_id"]) if row["role_id"] else None
    if role is not None:
        try:
            await role.delete(reason=_REASON)
        except discord.NotFound:
            pass
    return True


async def deactivate_for_deleted_channel(repo, channel_id: int,
                                         now: datetime) -> str | None:
    """A channel was deleted by hand: if it was an active zone channel, mark
    the zone DEACTIVATED (never recreate it). The role id is kept so a later
    re-activation reuses the role. Returns the zone, or None.

    Also forgets a deleted category or hub, so setup recreates them.
    """
    for key in (CATEGORY_KEY, HUB_CHANNEL_KEY):
        if await repo.gf_get_setting(key) == str(channel_id):
            await repo.gf_set_setting(key, None)
    row = await repo.gf_zone_by_channel(channel_id)
    if row is None or row["status"] != ACTIVE:
        return None
    await repo.gf_save_zone(row["zone"], role_id=row["role_id"], channel_id=None,
                            status=DEACTIVATED, now=now)
    log.info("group finder: zone %s deactivated (channel deleted)", row["zone"])
    return row["zone"]


async def reconcile_zones(bot, repo, now: datetime) -> list[str]:
    """On startup: channels deleted while the bot was offline never produced a
    delete event. Deactivate every ACTIVE zone whose channel is gone."""
    gone = []
    for row in await repo.gf_all_zones():
        if row["status"] != ACTIVE:
            continue
        if not row["channel_id"] or bot.get_channel(row["channel_id"]) is None:
            await repo.gf_save_zone(row["zone"], role_id=row["role_id"],
                                    channel_id=None, status=DEACTIVATED, now=now)
            gone.append(row["zone"])
    return gone


async def active_zones(repo) -> list[dict]:
    return [z for z in await repo.gf_all_zones() if z["status"] == ACTIVE]
