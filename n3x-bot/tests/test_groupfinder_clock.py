"""Member-created zones and one channel per clock.

Members pick any timezone; if no channel exists for its clock the bot creates
one, otherwise they join the existing one (Zurich -> the Berlin channel).
Zones whose clocks always match share a channel; zones that only match part
of the year (Berlin/Lagos) do not.
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from n3x_bot.groupfinder import events, hub, notify, parsing, provision, sync, zones
from tests.test_groupfinder_zones import (
    FakeGuild, FakeMember, _bot_for, _repo, _settings,
)

NOW = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)


# ── clock comparison (pure) ────────────────────────────────────────────────

@pytest.mark.parametrize("a,b,same", [
    ("Europe/Berlin", "Europe/Zurich", True),
    ("Europe/Berlin", "Europe/Paris", True),
    ("Europe/London", "Europe/Lisbon", True),
    ("America/New_York", "America/Toronto", True),
    ("Asia/Singapore", "Asia/Shanghai", True),
    ("Europe/Berlin", "Africa/Lagos", False),      # same in winter only
    ("Europe/London", "Africa/Lagos", False),      # same in summer only
    ("America/New_York", "America/Bogota", False),
    ("Asia/Kolkata", "Asia/Karachi", False),
])
async def test_same_clock(a, b, same):
    assert zones.same_clock(a, b) is same


async def test_popular_zones_collapse_into_fewer_channels():
    reps = []
    for z in zones.POPULAR_ZONES:
        if zones.find_same_clock(z, reps) is None:
            reps.append(z)
    assert len(reps) < len(zones.POPULAR_ZONES)
    assert "Europe/Zurich" not in reps and "Europe/Berlin" in reps


async def test_offset_label():
    assert zones.offset_label("Asia/Kolkata", NOW) == "UTC+05:30"
    assert zones.offset_label("America/New_York", NOW) == "UTC-04:00"
    assert zones.offset_label("Europe/Berlin", NOW) == "UTC+02:00"


# ── provisioning by clock ──────────────────────────────────────────────────

async def test_same_clock_zone_is_covered_not_created():
    repo, guild = await _repo(), FakeGuild()
    await provision.activate_zone(guild, repo, _settings(), "Europe/Berlin", NOW)
    before = (len(guild.roles), len(guild.channels))
    assert await provision.activate_zone(guild, repo, _settings(),
                                         "Europe/Zurich", NOW) == (
        "covered", "Europe/Berlin")
    assert (len(guild.roles), len(guild.channels)) == before
    assert await repo.gf_get_zone("Europe/Zurich") is None
    await repo.close()


async def test_different_clock_gets_its_own_channel():
    repo, guild = await _repo(), FakeGuild()
    await provision.activate_zone(guild, repo, _settings(), "Europe/Berlin", NOW)
    assert await provision.activate_zone(guild, repo, _settings(),
                                         "Africa/Lagos", NOW) == (
        "created", "Africa/Lagos")
    await repo.close()


async def test_deactivated_group_comes_back_under_its_old_name():
    repo, guild = await _repo(), FakeGuild()
    await provision.activate_zone(guild, repo, _settings(), "Europe/Berlin", NOW)
    row = await repo.gf_get_zone("Europe/Berlin")
    await guild.get_channel(row["channel_id"]).delete()
    await provision.deactivate_for_deleted_channel(repo, row["channel_id"], NOW)
    # someone picks Zurich later: Berlin's channel returns, Berlin's role reused
    assert await provision.activate_zone(guild, repo, _settings(),
                                         "Europe/Zurich", NOW) == (
        "reactivated", "Europe/Berlin")
    assert (await repo.gf_get_zone("Europe/Berlin"))["role_id"] == row["role_id"]
    assert guild.create_role.await_count == 1
    await repo.close()


# ── members creating and joining zones ────────────────────────────────────

async def test_first_member_creates_the_zone():
    repo, guild = await _repo(), FakeGuild()
    member = FakeMember(guild)
    reply = await hub.join_zone(_bot_for(guild), repo, _settings(), member,
                                "Asia/Tokyo")
    row = await repo.gf_get_zone("Asia/Tokyo")
    assert row["status"] == zones.ACTIVE
    assert guild.get_role(row["role_id"]) in member.roles
    assert "channel is new" in reply and f"<#{row['channel_id']}>" in reply
    await repo.close()


async def test_member_with_same_clock_joins_the_existing_channel():
    repo, guild = await _repo(), FakeGuild()
    await hub.join_zone(_bot_for(guild), repo, _settings(), FakeMember(guild, 1),
                        "Europe/Berlin")
    zurich = FakeMember(guild, 2)
    reply = await hub.join_zone(_bot_for(guild), repo, _settings(), zurich,
                                "Europe/Zurich")
    berlin = await repo.gf_get_zone("Europe/Berlin")
    assert guild.get_role(berlin["role_id"]) in zurich.roles
    assert await repo.gf_get_member_zone(2) == "Europe/Berlin"
    assert "same clock as Europe/Berlin" in reply
    assert guild.create_text_channel.await_count == 1
    await repo.close()


async def test_simultaneous_picks_create_one_channel():
    repo, guild = await _repo(), FakeGuild()
    bot = _bot_for(guild)
    await asyncio.gather(
        hub.join_zone(bot, repo, _settings(), FakeMember(guild, 1), "Europe/Paris"),
        hub.join_zone(bot, repo, _settings(), FakeMember(guild, 2), "Europe/Rome"),
        hub.join_zone(bot, repo, _settings(), FakeMember(guild, 3), "Europe/Berlin"))
    assert guild.create_text_channel.await_count == 1
    assert len(await provision.active_zones(repo)) == 1
    await repo.close()


async def test_switching_to_another_clock_moves_the_member():
    repo, guild = await _repo(), FakeGuild()
    member = FakeMember(guild)
    await hub.join_zone(_bot_for(guild), repo, _settings(), member, "Europe/Berlin")
    await hub.join_zone(_bot_for(guild), repo, _settings(), member, "Asia/Karachi")
    berlin = guild.get_role((await repo.gf_get_zone("Europe/Berlin"))["role_id"])
    karachi = guild.get_role((await repo.gf_get_zone("Asia/Karachi"))["role_id"])
    assert karachi in member.roles and berlin not in member.roles
    await repo.close()


async def test_a_new_channel_receives_the_running_searches():
    repo, guild = await _repo(), FakeGuild()
    bot = _bot_for(guild)
    await hub.join_zone(bot, repo, _settings(), FakeMember(guild, 1), "Europe/Berlin")
    draft = parsing.build_draft(title="Gate", players="2-4", event_date="26.09.2026",
                                times="20:00", zone="Europe/Berlin", now=NOW)
    await events.create(repo, draft, creator_id=1, origin_zone="Europe/Berlin", now=NOW)
    await hub.join_zone(bot, repo, _settings(), FakeMember(guild, 2), "Asia/Tokyo")
    tokyo = guild.get_channel((await repo.gf_get_zone("Asia/Tokyo"))["channel_id"])
    tokyo.send.assert_awaited()                      # the search was posted there
    await repo.close()


async def test_hub_offers_popular_and_active_zones_west_to_east():
    options = hub.hub_options(["America/Toronto"], NOW)
    assert "America/Toronto" in options              # active, not in the popular list
    assert set(zones.POPULAR_ZONES) <= set(options)
    offsets = [NOW.astimezone(zones.ZoneInfo(z)).utcoffset() for z in options]
    assert offsets == sorted(offsets)


# ── pings and links follow the clock ───────────────────────────────────────

async def test_participant_stored_as_berlin_is_pinged_in_the_zurich_channel():
    # the live case: migrated voters are stored as Europe/Berlin, the only
    # channel is Europe/Zurich
    repo = await _repo()
    channel = SimpleNamespace(id=500, guild=SimpleNamespace(id=1))
    sent = SimpleNamespace(id=900)
    channel.send = AsyncMock(return_value=sent)
    channel.fetch_message = AsyncMock()
    await repo.gf_save_zone("Europe/Zurich", role_id=1, channel_id=500,
                            status=zones.ACTIVE, now=NOW)
    bot = MagicMock()
    bot.get_channel = lambda cid: channel if cid == 500 else None
    draft = parsing.build_draft(title="Gate", players="1-4", event_date="26.09.2026",
                                times="20:00", zone="Europe/Berlin", now=NOW)
    eid = await events.create(repo, draft, creator_id=1, origin_zone="Europe/Berlin",
                              now=NOW)
    await events.vote(repo, eid, 42, "Europe/Berlin", list(draft.slots), NOW)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    allowed = channel.send.call_args.kwargs["allowed_mentions"]
    assert [u.id for u in allowed.users] == [42]
    await repo.close()


async def test_reminder_link_points_to_the_channel_of_the_members_clock():
    repo = await _repo()
    guild = SimpleNamespace(id=1)
    chans = {500: SimpleNamespace(id=500, guild=guild),
             600: SimpleNamespace(id=600, guild=guild)}
    bot = MagicMock()
    bot.get_channel = chans.get
    draft = parsing.build_draft(title="Gate", players="1-4", event_date="26.09.2026",
                                times="20:00", zone="Europe/Berlin", now=NOW)
    eid = await events.create(repo, draft, creator_id=1, origin_zone="Europe/Berlin",
                              now=NOW)
    await repo.gf_set_event_message(eid, "Asia/Tokyo", 600, 6000, NOW)
    await repo.gf_set_event_message(eid, "Europe/Zurich", 500, 5000, NOW)
    link = await notify._jump_url(bot, repo, eid, "Europe/Berlin")
    assert link.endswith("/500/5000")
    await repo.close()


async def test_admin_timezone_add_reports_a_covered_zone():
    from n3x_bot.bot import build_bot
    from tests.test_groupfinder_zones import _interaction, _sub, _text
    repo, guild = await _repo(), FakeGuild()
    bot = build_bot(_settings(), repo)
    bot.get_channel = guild.get_channel
    await provision.activate_zone(guild, repo, _settings(), "Europe/Berlin", NOW)
    interaction = _interaction(guild, bot=bot)
    await _sub(bot, "timezone-add").callback(interaction, zone="Europe/Vienna")
    assert "same clock as **Europe/Berlin**" in _text(interaction)
    assert [z["zone"] for z in await provision.active_zones(repo)] == ["Europe/Berlin"]
    assert await repo.gf_get_zone("Europe/Vienna") is None
    await repo.close()


async def test_deleting_is_still_admin_only():
    from n3x_bot.bot import build_bot
    from tests.test_groupfinder_zones import _interaction, _sub, _text
    repo, guild = await _repo(), FakeGuild()
    bot = build_bot(_settings(), repo)
    await provision.activate_zone(guild, repo, _settings(), "Europe/Berlin", NOW)
    interaction = _interaction(guild, admin=False)
    await _sub(bot, "timezone-delete").callback(interaction, zone="Europe/Berlin")
    assert "Only admins" in _text(interaction)
    assert (await repo.gf_get_zone("Europe/Berlin"))["status"] == zones.ACTIVE
    await repo.close()
