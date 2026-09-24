"""The hub: the one Group Finder channel everyone can see, where members pick
their zone. Also `/timezone`, which does the same thing as the hub select.

The hub message is tracked in `channel_messages` and self-heals like the base
timer overview: reposted only when Discord says it is gone (`NotFound`).
"""
import asyncio
import logging
from datetime import datetime

import discord
from discord import app_commands

from n3x_bot.activity import now_local
from n3x_bot.groupfinder import provision, zones
from n3x_bot.groupfinder.zones import ACTIVE, SELECT_LIMIT

log = logging.getLogger("N3X-Bot")

HUB_MESSAGE_KEY = "gf_hub"
# A message carries at most 5 action rows, one select each -> 125 zones.
MAX_SELECTS = 5
ZONE_SELECT_ID = "n3x:gf:zone:{index}"


def build_hub_embed(active: list[str]) -> discord.Embed:
    description = (
        "Plan group activities with players around the world — every group "
        "search shows up in **your** local time.\n"
        "\n"
        "**1. Pick your timezone below.** You get access to your timezone "
        "channel; that is where group searches appear.\n"
        "**2. Start a search there with `/lfg`** — a title, how many players, "
        "a date and a few possible start times.\n"
        "**3. Vote for the times that work for you.** As soon as one time has "
        "enough votes it becomes the start time and everyone who picked it "
        "gets pinged.\n"
        "**4. You get a DM 15 minutes before the start.**\n"
        "\n"
        "Every search appears in every timezone channel — same group, your "
        "local time. You can change your timezone at any time here or with "
        "`/timezone`.")
    if not active:
        description += "\n\n_No timezones are set up yet — ask an admin._"
    return discord.Embed(title="🌍 Group Finder", description=description,
                         color=discord.Color.blurple())


class ZoneSelect(discord.ui.Select):
    def __init__(self, repo, settings, index: int, zone_ids=None):
        self.repo = repo
        self.settings = settings
        # The router instance registered on startup has no zones; Discord needs
        # at least one option, and routing only uses the custom_id anyway.
        options = [discord.SelectOption(label=z, value=z) for z in zone_ids or []]
        options = options or [discord.SelectOption(label="—", value="—")]
        super().__init__(custom_id=ZONE_SELECT_ID.format(index=index),
                         placeholder="Pick your timezone",
                         min_values=1, max_values=1, options=options)

    async def callback(self, interaction):
        result, zone_row = await assign_member_zone(
            self.repo, interaction.user, self.values[0],
            now_local(self.settings))
        await interaction.response.send_message(
            _assign_reply(result, self.values[0], zone_row), ephemeral=True)


class HubView(discord.ui.View):
    """Persistent. With `zone_ids=None` it is the startup router: all five
    custom_ids, so a click on any select of the live hub message is routed."""

    def __init__(self, repo, settings, zone_ids=None):
        super().__init__(timeout=None)
        if zone_ids is None:
            for i in range(MAX_SELECTS):
                self.add_item(ZoneSelect(repo, settings, i))
            return
        chunks = [zone_ids[i:i + SELECT_LIMIT]
                  for i in range(0, len(zone_ids), SELECT_LIMIT)][:MAX_SELECTS]
        for i, chunk in enumerate(chunks):
            self.add_item(ZoneSelect(repo, settings, i, chunk))


def _assign_reply(result: str, zone: str, zone_row) -> str:
    if result == "inactive":
        return f"❌ {zone} is not available."
    if result == "missing_role":
        return "❌ This timezone is misconfigured — please tell an admin."
    channel = f"<#{zone_row['channel_id']}>" if zone_row else "your channel"
    if result == "unchanged":
        return f"✅ Your timezone already is **{zone}** — see {channel}."
    return f"✅ Your timezone is now **{zone}** — group searches are in {channel}."


async def assign_member_zone(repo, member, zone: str, now: datetime):
    """Give `member` the role of `zone` and remove every other zone role (one
    zone per member). Returns `(result, zone_row)` with result one of `set`,
    `unchanged`, `inactive`, `missing_role`."""
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
        active = [z["zone"] for z in await provision.active_zones(repo)]
        embed = build_hub_embed(active)
        view = HubView(repo, settings, active) if active else None
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

    async def _active_autocomplete(interaction, current: str):
        pool = [z["zone"] for z in await provision.active_zones(repo)]
        return [app_commands.Choice(name=z, value=z)
                for z in zones.search_zones(current, pool)]

    @bot.tree.command(name="timezone",
                      description="Set your Group Finder timezone.")
    @app_commands.describe(zone="Your timezone")
    @app_commands.autocomplete(zone=_active_autocomplete)
    async def timezone_cmd(interaction, zone: str):
        result, row = await assign_member_zone(repo, interaction.user, zone,
                                               now_local(settings))
        await interaction.response.send_message(
            _assign_reply(result, zone, row), ephemeral=True)
