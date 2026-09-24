"""Group Finder — one global group-planning system shown in one channel per
timezone. See instructions/Groupfinder Spec 3.md.

Stage 1: zone administration, the hub and member timezones.
Stage 2: events, voting, time finding (Variant 1), rendering in every zone.
Stage 3: the lifecycle loop (countdown, closing, start, cleanup) and cancel.
"""
import logging

from datetime import datetime, timezone

from n3x_bot.activity import now_local
from n3x_bot.groupfinder import hub, lifecycle, provision, sync, views
from n3x_bot.groupfinder.admin import register_groupfinder_admin
from n3x_bot.groupfinder.commands import register_lfg_command
from n3x_bot.groupfinder.hub import register_timezone_command

log = logging.getLogger("N3X-Bot")


def register_groupfinder(bot, repo, settings) -> None:
    """Commands and the channel-delete listener. Called from `build_bot`."""
    register_groupfinder_admin(bot, repo, settings)
    register_timezone_command(bot, repo, settings)
    register_lfg_command(bot, repo, settings)

    async def _on_channel_delete(channel):
        # add_listener, not @bot.event: must not replace any other handler.
        try:
            zone = await provision.deactivate_for_deleted_channel(
                repo, channel.id, now_local(settings))
            if zone is not None:
                await hub.update_hub(bot, repo, settings)
        except Exception:
            log.exception("group finder: channel-delete handling failed")

    bot.add_listener(_on_channel_delete, "on_guild_channel_delete")


async def start_groupfinder(bot, repo, settings) -> None:
    """On ready: re-attach the hub and event routers, catch up on channels
    deleted while offline, refresh the hub, bring every event message in line
    with the database (zones added while offline get their messages here)."""
    bot.add_view(hub.HubView(repo, settings))
    bot.add_view(views.VotingView(repo, settings))
    bot.add_view(views.FixedView(repo, settings))
    gone = await provision.reconcile_zones(bot, repo, now_local(settings))
    if gone:
        log.info("group finder: deactivated while offline: %s", ", ".join(gone))
    await hub.update_hub(bot, repo, settings)
    await sync.sync_all(bot, repo, settings, datetime.now(timezone.utc))
    lifecycle.start_lifecycle_loop(bot, repo, settings)


__all__ = ["register_groupfinder", "start_groupfinder"]
