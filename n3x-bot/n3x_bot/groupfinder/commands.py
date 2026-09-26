"""`/lfg` — create a group search. Only in zone channels: the zone of the
channel decides how the entered date and times are read."""
from datetime import datetime, timezone

import discord
from discord import app_commands

from n3x_bot.admin import is_admin
from n3x_bot.groupfinder import events, parsing, provision, sync
from n3x_bot.groupfinder.zones import ACTIVE

UTC = timezone.utc


async def _hub_hint(repo) -> str:
    raw = await repo.gf_get_setting(provision.HUB_CHANNEL_KEY)
    where = f"<#{raw}>" if raw else "the Group Finder hub"
    return (f"❌ Use `/lfg` in your timezone channel. Pick your timezone in "
            f"{where}.")


HINT_SECONDS = 15


async def guard_zone_message(bot, repo, settings, message) -> bool:
    """Zone channels hold group searches only. A message typed there by a
    member is removed with a short hint to use `/lfg`; admins may still write.
    Returns whether the message was removed."""
    if message.author.bot or message.guild is None:
        return False
    # Cheap filter first: this runs for every message on the server.
    if not getattr(message.channel, "name", "").startswith("gf-"):
        return False
    zone = await repo.gf_zone_by_channel(message.channel.id)
    if zone is None or zone["status"] != ACTIVE:
        return False
    if is_admin(message.author, settings):
        return False
    try:
        await message.delete()
    except discord.HTTPException:
        return False
    try:
        await message.channel.send(
            f"{message.author.mention} This channel only holds group "
            f"searches. Post one with `/lfg`.",
            delete_after=HINT_SECONDS,
            allowed_mentions=discord.AllowedMentions(users=[message.author]))
    except discord.HTTPException:
        pass
    return True


def register_lfg_command(bot, repo, settings) -> None:
    if bot.tree.get_command("lfg") is not None:
        return

    @bot.tree.command(name="lfg",
                      description="Create a group search in your timezone channel.")
    @app_commands.describe(
        title="What are you planning? e.g. Satura Galaxy Gate",
        players="Players as min-max, e.g. 4-8",
        date="Date, e.g. 26.09.2026",
        times="Possible start times in your timezone, e.g. 19:00 / 20:00 / 21:00")
    async def lfg_cmd(interaction, title: str, players: str, date: str,
                      times: str):
        zone = await repo.gf_zone_by_channel(interaction.channel_id)
        if zone is None or zone["status"] != ACTIVE:
            await interaction.response.send_message(await _hub_hint(repo),
                                                    ephemeral=True)
            return
        now = datetime.now(UTC)
        try:
            draft = parsing.build_draft(title=title, players=players,
                                        event_date=date, times=times,
                                        zone=zone["zone"], now=now)
        except parsing.GfValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        # Posting into every zone channel can take longer than 3 s.
        await interaction.response.defer(ephemeral=True)
        event_id = await events.create(repo, draft,
                                       creator_id=interaction.user.id,
                                       origin_zone=zone["zone"], now=now)
        await sync.sync_event(bot, repo, settings, event_id, now)
        # No success text: the posted group search is the feedback.
        try:
            await interaction.delete_original_response()
        except Exception:
            pass
