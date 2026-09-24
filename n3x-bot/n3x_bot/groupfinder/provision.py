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
# "plus" (gf-utc+2) until Discord is seen altering such a name, then "words"
# (gf-utc-plus-2) for good: a stripped "+" would leave gf-utc2, which reads as
# either sign.
NAME_STYLE_KEY = "channel_name_style"
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


async def _words(repo) -> bool:
    return await repo.gf_get_setting(NAME_STYLE_KEY) == "words"


async def _accepted(repo, requested: str, actual: str | None) -> bool:
    """Did Discord keep `requested`? If it altered a name containing `+`,
    switch to the words spelling from now on."""
    if actual is None or actual == requested or "+" not in requested:
        return True
    await repo.gf_set_setting(NAME_STYLE_KEY, "words")
    log.info("group finder: Discord altered %r to %r, using words from now on",
             requested, actual)
    return False


async def _names_for(repo, rows: list[dict], now: datetime) -> dict:
    return zones.zone_names(rows, now, words=await _words(repo))


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
    # Name it after its offset right now, among the channels that stay active.
    others = [r for r in rows if r["zone"] in live]
    me = {"zone": target, "created_at": row["created_at"] if row else now}
    channel_name, role_name = (await _names_for(repo, [*others, me], now))[target]
    role = guild.get_role(row["role_id"]) if row and row["role_id"] else None
    if role is None:
        role = await guild.create_role(name=role_name, mentionable=False,
                                       reason=_REASON)
    channel = await guild.create_text_channel(
        channel_name, category=category,
        overwrites=zone_overwrites(guild, role, settings),
        topic=(f"Group Finder · {target} and every timezone with the same "
               "clock. The name shows the current UTC offset and changes with "
               "summer/winter time."),
        reason=_REASON)
    if not await _accepted(repo, channel_name, getattr(channel, "name", None)):
        words_name = (await _names_for(repo, [*others, me], now))[target][0]
        channel = await channel.edit(name=words_name, reason=_REASON) or channel
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


async def sync_zone_names(bot, repo, now: datetime) -> int:
    """Rename channels and roles to their current offset. Nothing happens
    except at a DST switch or when a shared offset appears or disappears.
    Returns how many channels were renamed."""
    active = [r for r in await repo.gf_all_zones()
              if r["status"] == ACTIVE and r["channel_id"]]
    if not active:
        return 0
    names = await _names_for(repo, active, now)
    renamed = 0
    for row in active:
        channel = bot.get_channel(row["channel_id"])
        if channel is None:
            continue
        channel_name, role_name = names[row["zone"]]
        if channel.name != channel_name:
            try:
                updated = await channel.edit(name=channel_name, reason=_REASON)
                actual = getattr(updated or channel, "name", channel_name)
                if not await _accepted(repo, channel_name, actual):
                    words_name = (await _names_for(repo, active, now))[row["zone"]][0]
                    await channel.edit(name=words_name, reason=_REASON)
                renamed += 1
            except Exception:
                log.exception("group finder: renaming %s failed", row["zone"])
        guild = getattr(channel, "guild", None)
        role = guild.get_role(row["role_id"]) if guild and row["role_id"] else None
        if role is not None and role.name != role_name:
            try:
                await role.edit(name=role_name, reason=_REASON)
            except Exception:
                log.exception("group finder: renaming role of %s failed",
                              row["zone"])
    return renamed


def schedule_name_sync(bot, repo, now: datetime) -> None:
    """Run sync_zone_names in the background, never two at once. Discord
    allows 2 renames per channel per 10 minutes and discord.py *waits* on a
    rate limit — inside the lifecycle tick that would stall countdowns and
    reminders for up to 10 minutes."""
    import asyncio
    task = getattr(bot, "_gf_name_task", None)
    if isinstance(task, asyncio.Task) and not task.done():
        return

    async def _run():
        try:
            await sync_zone_names(bot, repo, now)
        except Exception:
            log.exception("group finder: name sync failed")
    bot._gf_name_task = asyncio.ensure_future(_run())
