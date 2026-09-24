"""Persistent views on Group Finder event messages.

There are many event messages at once, one per event per zone channel, so no
state lives on a view: every interaction resolves its event (and the zone of
the channel it came from) through `gf_messages` by message id. The fixed
custom_ids let the two router instances registered on startup route every
click after a restart. The option lists shown in a message are stored by
Discord with the message; the router only needs the custom_ids to match.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import discord

from n3x_bot.groupfinder import events
from n3x_bot.groupfinder.render import clock, day_label, time_label

UTC = timezone.utc
VOTE_SELECT_ID = "n3x:gf:vote"
JOIN_ID = "n3x:gf:join"
LEAVE_ID = "n3x:gf:leave"

_GONE = "❌ This group search no longer exists."


def slot_value(slot: datetime) -> str:
    return str(int(slot.timestamp()))


def slot_from_value(value: str) -> datetime:
    return datetime.fromtimestamp(int(value), UTC)


async def _resolve(repo, interaction):
    ref = await repo.gf_message_lookup(interaction.message.id)
    if ref is None:
        return None, None
    return ref["event_id"], ref["zone"]


async def _after_change(interaction, repo, settings, event_id) -> None:
    """Acknowledge the click, then re-render the event in every zone channel.
    Deferring first: editing N channels can take longer than Discord's 3 s."""
    from n3x_bot.groupfinder import sync      # sync imports this module
    await interaction.response.defer()
    await sync.sync_event(interaction.client, repo, settings, event_id,
                          datetime.now(UTC))


class VoteSelect(discord.ui.Select):
    def __init__(self, repo, settings, *, event=None, zone=None, now=None):
        self.repo = repo
        self.settings = settings
        options = []
        if event is not None:
            tz = ZoneInfo(zone)
            for slot in events.open_slots(event, now):
                local = slot.astimezone(tz)
                options.append(discord.SelectOption(
                    label=f"{day_label(local)} {time_label(local)}",
                    value=slot_value(slot), emoji=clock(local)))
        options = options or [discord.SelectOption(label="—", value="0")]
        super().__init__(custom_id=VOTE_SELECT_ID,
                         placeholder="Which times work for you?",
                         min_values=0, max_values=len(options), options=options)

    async def callback(self, interaction):
        event_id, zone = await _resolve(self.repo, interaction)
        if event_id is None:
            await interaction.response.send_message(_GONE, ephemeral=True)
            return
        slots = [slot_from_value(v) for v in self.values if v != "0"]
        result = await events.vote(self.repo, event_id, interaction.user.id,
                                   zone, slots, datetime.now(UTC))
        if result.error == "not_voting":
            await interaction.response.send_message(
                "❌ The time is already fixed — use **Join** instead.",
                ephemeral=True)
            return
        if result.error:
            await interaction.response.send_message(_GONE, ephemeral=True)
            return
        await _after_change(interaction, self.repo, self.settings, event_id)


class _LeaveButton(discord.ui.Button):
    def __init__(self, repo, settings):
        super().__init__(label="Leave", style=discord.ButtonStyle.secondary,
                         custom_id=LEAVE_ID)
        self.repo = repo
        self.settings = settings

    async def callback(self, interaction):
        event_id, _zone = await _resolve(self.repo, interaction)
        if event_id is None:
            await interaction.response.send_message(_GONE, ephemeral=True)
            return
        result = await events.leave(self.repo, event_id, interaction.user.id,
                                    datetime.now(UTC))
        if result != "left":
            await interaction.response.send_message(
                {"not_member": "❌ You are not part of this group.",
                 "started": "❌ This group has already started."}.get(result, _GONE),
                ephemeral=True)
            return
        await _after_change(interaction, self.repo, self.settings, event_id)


class _JoinButton(discord.ui.Button):
    def __init__(self, repo, settings, *, disabled=False):
        super().__init__(label="Join", style=discord.ButtonStyle.success,
                         custom_id=JOIN_ID, disabled=disabled)
        self.repo = repo
        self.settings = settings

    async def callback(self, interaction):
        event_id, zone = await _resolve(self.repo, interaction)
        if event_id is None:
            await interaction.response.send_message(_GONE, ephemeral=True)
            return
        result = await events.join(self.repo, event_id, interaction.user.id,
                                   zone, datetime.now(UTC))
        if result != "added":
            await interaction.response.send_message(
                {"already": "✅ You are already in this group.",
                 "full": "❌ This group is full.",
                 "closed": "❌ Joining is closed.",
                 "not_scheduled": "❌ No time yet — vote for the times that "
                                  "work for you."}.get(result, _GONE),
                ephemeral=True)
            return
        await _after_change(interaction, self.repo, self.settings, event_id)


class VotingView(discord.ui.View):
    """While times are being voted on: time select + Leave. Without `event`
    this is the startup router."""

    def __init__(self, repo, settings, *, event=None, zone=None, now=None):
        super().__init__(timeout=None)
        if event is None or events.open_slots(event, now):
            self.add_item(VoteSelect(repo, settings, event=event, zone=zone,
                                     now=now))
        self.add_item(_LeaveButton(repo, settings))


class FixedView(discord.ui.View):
    """Once the time is fixed: Join + Leave. Join is rendered disabled when
    joining is closed — the join logic still re-checks, a disabled button is
    display only."""

    def __init__(self, repo, settings, *, joinable=True):
        super().__init__(timeout=None)
        self.add_item(_JoinButton(repo, settings, disabled=not joinable))
        self.add_item(_LeaveButton(repo, settings))


def view_for(repo, settings, event: dict, zone: str, now: datetime):
    """The view for one zone's message, or None when nothing is clickable."""
    if event["status"] == events.VOTING:
        return VotingView(repo, settings, event=event, zone=zone, now=now)
    if event["status"] in (events.SCHEDULED, events.CLOSED):
        return FixedView(repo, settings,
                         joinable=events.joining_open(event, now))
    return None
