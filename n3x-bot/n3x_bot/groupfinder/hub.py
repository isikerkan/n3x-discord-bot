"""The hub: the one Group Finder channel everyone can see, where members pick
their timezone. Also `/timezone`, which does the same for any IANA zone.

Members create zones themselves: picking a timezone that has no channel yet
creates one; picking one whose clock matches an existing channel (Zurich when
Berlin exists) joins that channel. One channel per clock, one zone per member.

The hub message is tracked in `channel_messages` and self-heals like the base
timer overview: reposted only when Discord says it is gone (`NotFound`).
"""
import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from n3x_bot.activity import now_local
from n3x_bot.groupfinder import provision, zones
from n3x_bot.groupfinder.zones import ACTIVE, SELECT_LIMIT

log = logging.getLogger("N3X-Bot")

HUB_MESSAGE_KEY = "gf_hub"
# A message carries at most 5 action rows: up to 4 zone selects (100 zones)
# plus one row for the "Remove my timezone" button.
MAX_SELECTS = 4
ZONE_SELECT_ID = "n3x:gf:zone:{index}"
LEAVE_ZONE_ID = "n3x:gf:zone:leave"


def build_hub_embed(channel_ids: list[int]) -> discord.Embed:
    description = (
        "Plan group activities with players around the world — every group "
        "search shows up in **your** local time.\n"
        "\n"
        "**1. Pick your timezone below** (not listed? use `/timezone` with "
        "any city). You get access to your timezone channel — the bot creates "
        "it if it does not exist yet. Timezones with the same clock share one "
        "channel, named after its UTC offset; the name changes with "
        "summer/winter time, you stay in it.\n"
        "**2. Start a search there with `/lfg`** — a title, how many players, "
        "a date and a few possible start times.\n"
        "**3. Vote for the times that work for you.** As soon as one time has "
        "enough votes it becomes the start time and everyone who picked it "
        "gets pinged.\n"
        "**4. You get a DM 15 minutes before the start.**\n"
        "\n"
        "Every search appears in every timezone channel — same group, your "
        "local time. You can change your timezone at any time, or remove it "
        "with the button below.")
    if channel_ids:
        description += "\n\n**Timezone channels:** " + " ".join(
            f"<#{cid}>" for cid in channel_ids)
    return discord.Embed(title="🌍 Group Finder", description=description,
                         color=discord.Color.blurple())


def hub_options(active: list[str], now: datetime) -> list[str]:
    """What the hub offers: the popular zones plus every zone that has a
    channel, sorted by their current offset so the list reads west to east."""
    offered = list(dict.fromkeys([*zones.POPULAR_ZONES, *active]))

    def _key(zone):
        return (now.astimezone(ZoneInfo(zone)).utcoffset(), zone)
    return sorted(offered, key=_key)[:SELECT_LIMIT * MAX_SELECTS]


class ZoneSelect(discord.ui.Select):
    def __init__(self, repo, settings, index: int, zone_ids=None, now=None):
        self.repo = repo
        self.settings = settings
        # The router instance registered on startup has no zones; Discord needs
        # at least one option, and routing only uses the custom_id anyway.
        options = [discord.SelectOption(
            label=z, value=z, description=zones.offset_label(z, now))
            for z in zone_ids or []]
        options = options or [discord.SelectOption(label="—", value="—")]
        super().__init__(custom_id=ZONE_SELECT_ID.format(index=index),
                         placeholder="Pick your timezone",
                         min_values=1, max_values=1, options=options)

    async def callback(self, interaction):
        # Creating a role and a channel can exceed Discord's 3 seconds.
        await interaction.response.defer()
        reply = await join_zone(interaction.client, self.repo, self.settings,
                                interaction.user, self.values[0])
        # Re-render the hub: otherwise the member's Discord keeps showing the
        # pick as selected, and choosing the same entry again sends nothing.
        try:
            await interaction.edit_original_response(
                view=await current_view(self.repo, self.settings))
        except Exception:
            log.exception("group finder hub: resetting the select failed")
        await interaction.followup.send(reply, ephemeral=True)


class LeaveZoneButton(discord.ui.Button):
    def __init__(self, repo, settings):
        super().__init__(label="Remove my timezone",
                         style=discord.ButtonStyle.secondary,
                         custom_id=LEAVE_ZONE_ID)
        self.repo = repo
        self.settings = settings

    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True)
        reply = await leave_zone(self.repo, interaction.user)
        await interaction.followup.send(reply, ephemeral=True)


class HubView(discord.ui.View):
    """Persistent. With `zone_ids=None` it is the startup router: all five
    custom_ids, so a click on any select of the live hub message is routed."""

    def __init__(self, repo, settings, zone_ids=None, now=None):
        super().__init__(timeout=None)
        if zone_ids is None:
            for i in range(MAX_SELECTS):
                self.add_item(ZoneSelect(repo, settings, i))
        else:
            now = now or datetime.now(timezone.utc)
            chunks = [zone_ids[i:i + SELECT_LIMIT]
                      for i in range(0, len(zone_ids), SELECT_LIMIT)][:MAX_SELECTS]
            for i, chunk in enumerate(chunks):
                self.add_item(ZoneSelect(repo, settings, i, chunk, now))
        self.add_item(LeaveZoneButton(repo, settings))


def _zone_lock(bot) -> asyncio.Lock:
    lock = getattr(bot, "_gf_zone_lock", None)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        bot._gf_zone_lock = lock
    return lock


async def assign_member_zone(repo, member, zone: str, now: datetime):
    """Give `member` the role of the active zone `zone` and remove every other
    zone role (one zone per member). Returns `(result, zone_row)` with result
    one of `set`, `unchanged`, `inactive`, `missing_role`."""
    row = await repo.gf_get_zone(zone)
    if row is None or row["status"] != ACTIVE:
        return "inactive", None
    guild = member.guild
    role = guild.get_role(row["role_id"]) if row["role_id"] else None
    if role is None:
        return "missing_role", row
    other_ids = {z["role_id"] for z in await repo.gf_all_zones()
                 if z["role_id"] and z["role_id"] != role.id}
    held = {r.id for r in getattr(member, "roles", [])}
    stale = [r for r in member.roles if r.id in other_ids]
    if stale:
        await member.remove_roles(*stale, reason="Group Finder timezone change")
    already = role.id in held and not stale
    if role.id not in held:
        await member.add_roles(role, reason="Group Finder timezone")
    await repo.gf_set_member_zone(member.id, zone, now)
    return ("unchanged" if already else "set"), row


async def leave_zone(repo, member) -> str:
    """Take away every zone role the member holds and forget their zone. They
    see no timezone channel any more (admins still see all of them)."""
    zone_role_ids = {z["role_id"] for z in await repo.gf_all_zones()
                     if z["role_id"]}
    held = [r for r in getattr(member, "roles", []) if r.id in zone_role_ids]
    had = await repo.gf_clear_member_zone(member.id)
    if held:
        await member.remove_roles(*held, reason="Group Finder timezone removed")
    if not held and had is None:
        return "ℹ️ You have no timezone set."
    return ("✅ Your timezone is removed — you no longer see a timezone channel. "
            "Pick one again here any time.")


async def current_view(repo, settings):
    """The hub view as it should look right now."""
    now = datetime.now(timezone.utc)
    active = [z["zone"] for z in await provision.active_zones(repo)]
    return HubView(repo, settings, hub_options(active, now), now)


async def join_zone(bot, repo, settings, member, chosen: str) -> str:
    """A member picks a timezone: find or create its channel, give the role,
    and return the reply for the member."""
    if not zones.is_valid_zone(chosen):
        return f"❌ `{chosen}` is not a timezone. Try a city, e.g. `Europe/Berlin`."
    now = now_local(settings)
    async with _zone_lock(bot):
        outcome, channel_zone = await provision.activate_zone(
            member.guild, repo, settings, chosen, now)
        result, row = await assign_member_zone(repo, member, channel_zone, now)
    if outcome in ("created", "reactivated"):
        # A new channel: list it in the hub and show every running search there.
        from n3x_bot.groupfinder import sync
        await update_hub(bot, repo, settings)
        await sync.sync_all(bot, repo, settings, datetime.now(timezone.utc))
    if result == "missing_role":
        return "❌ This timezone is misconfigured — please tell an admin."
    if result == "inactive":
        return "❌ That did not work — please try again."
    channel = f"<#{row['channel_id']}>"
    if result == "unchanged":
        return f"✅ You are already in {channel}."
    reply = f"✅ Your timezone is now **{chosen}** — group searches are in {channel}."
    if channel_zone != chosen:
        reply += f"\n{chosen} has the same clock as {channel_zone}, so you share its channel."
    if outcome == "created":
        reply += "\nThis channel is new — you are the first one here."
    return reply


def _hub_lock(bot) -> asyncio.Lock:
    lock = getattr(bot, "_gf_hub_lock", None)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        bot._gf_hub_lock = lock
    return lock


async def update_hub(bot, repo, settings) -> None:
    """Post or refresh the hub message. Serialized, so two zone changes at once
    cannot each post a replacement."""
    async with _hub_lock(bot):
        raw = await repo.gf_get_setting(provision.HUB_CHANNEL_KEY)
        channel = bot.get_channel(int(raw)) if raw else None
        if channel is None:
            return
        rows = await provision.active_zones(repo)
        embed = build_hub_embed([z["channel_id"] for z in rows])
        view = await current_view(repo, settings)
        stored = await repo.get_channel_message(HUB_MESSAGE_KEY)
        if stored is not None and stored[1] == channel.id:
            try:
                message = await channel.fetch_message(stored[0])
                await message.edit(content=None, embed=embed, view=view)
                return
            except discord.NotFound:
                pass  # deleted -> post a replacement below
            except Exception:
                log.exception("group finder hub: refresh failed")
                return  # never repost on anything but NotFound
        try:
            message = await channel.send(embed=embed, view=view)
        except Exception:
            log.exception("group finder hub: posting failed")
            return
        await repo.set_channel_message(HUB_MESSAGE_KEY, message.id, channel.id)


def register_timezone_command(bot, repo, settings) -> None:
    if bot.tree.get_command("timezone") is not None:
        return

    async def _any_zone(interaction, current: str):
        return [app_commands.Choice(name=z, value=z)
                for z in zones.search_zones(current, zones.all_zones())]

    @bot.tree.command(name="timezone",
                      description="Set your Group Finder timezone.")
    @app_commands.describe(zone="Your timezone — type your city")
    @app_commands.autocomplete(zone=_any_zone)
    async def timezone_cmd(interaction, zone: str):
        await interaction.response.defer(ephemeral=True)
        reply = await join_zone(bot, repo, settings, interaction.user, zone)
        await interaction.followup.send(reply, ephemeral=True)
