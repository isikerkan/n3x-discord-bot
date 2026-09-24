"""Stage 2 of the Group Finder: events, voting, time finding (Variant 1),
rendering and cross-channel synchronisation."""
import itertools
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import discord
import pytest

from n3x_bot.bot import build_bot
from n3x_bot.config import Settings
from n3x_bot.groupfinder import events, parsing, render, sync, views
from n3x_bot.groupfinder.zones import ACTIVE, DEACTIVATED
from n3x_bot.seed import seed_defaults
from n3x_bot.storage.json_repo import JsonRepository

UTC = timezone.utc
NOW = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)          # Sat 12:00 in Berlin
BERLIN, KARACHI, TOKYO = "Europe/Berlin", "Asia/Karachi", "Asia/Tokyo"
JULES, MAX, TOM, ALEX, SARAH = 1, 2, 3, 4, 5


def _settings(**overrides) -> Settings:
    kwargs = dict(discord_token="tok", target_role_id=1, welcome_channel_id=2,
                  reminder_channel_id=999, julez_id=424242, admin_role_id=900,
                  _env_file=None, _env_prefix="NONEXISTENT_")
    kwargs.update(overrides)
    return Settings(**kwargs)


async def _repo() -> JsonRepository:
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    repo = JsonRepository(path)
    await repo.connect()
    await seed_defaults(repo)
    return repo


def _berlin(hour, minute=0, day=26):
    return datetime(2026, 9, day, hour, minute, tzinfo=ZoneInfo(BERLIN)).astimezone(UTC)


def _draft(**overrides):
    kwargs = dict(title="Satura Gruppen Gate", players="4-8",
                  event_date="26.09.2026", times="19:00 / 20:00 / 21:00",
                  zone=BERLIN, now=NOW)
    kwargs.update(overrides)
    return parsing.build_draft(**kwargs)


async def _create(repo, **overrides):
    return await events.create(repo, _draft(**overrides), creator_id=JULES,
                               origin_zone=BERLIN, now=NOW)


async def _vote(repo, eid, who, *hours, zone=BERLIN, now=NOW):
    return await events.vote(repo, eid, who, zone,
                             [_berlin(h) for h in hours], now)


# ── parsing ────────────────────────────────────────────────────────────────

async def test_draft_converts_local_times_to_utc():
    d = _draft()
    assert d.slots == (_berlin(19), _berlin(20), _berlin(21))
    assert d.slots[0] == datetime(2026, 9, 26, 17, 0, tzinfo=UTC)   # CEST = UTC+2
    assert (d.min_players, d.max_players) == (4, 8)


@pytest.mark.parametrize("zone,expected_utc_hour", [
    ("Europe/Zurich", 18), ("Europe/London", 19),
    ("America/New_York", 0), ("Asia/Tokyo", 11)])
async def test_the_same_wall_time_is_a_different_instant_per_zone(zone, expected_utc_hour):
    # 20:00 local on 26.09.2026 in each zone
    now = datetime(2026, 9, 25, 0, 0, tzinfo=UTC)
    slot = parsing.build_draft(title="x", players="1-2", event_date="26.09.2026",
                               times="20:00", zone=zone, now=now).slots[0]
    assert slot.hour == expected_utc_hour
    assert slot.astimezone(ZoneInfo(zone)).hour == 20


async def test_iso_date_is_accepted():
    assert _draft(event_date="2026-09-26").slots == _draft().slots


async def test_times_are_sorted():
    assert _draft(times="21:00, 19:00").slots == (_berlin(19), _berlin(21))


@pytest.mark.parametrize("players", ["8-4", "0-8", "abc", "4", "4-51"])
async def test_invalid_player_ranges(players):
    with pytest.raises(parsing.GfValidationError):
        _draft(players=players)


@pytest.mark.parametrize("times", ["", "25:00", "19:70", "evening", "19-00"])
async def test_invalid_times(times):
    with pytest.raises(parsing.GfValidationError):
        _draft(times=times)


async def test_duplicate_times_rejected():
    with pytest.raises(parsing.GfValidationError, match="twice"):
        _draft(times="20:00 / 20:00")


async def test_more_than_25_times_rejected():
    many = " / ".join(f"{h:02d}:{m:02d}" for h in range(13, 24) for m in (0, 20, 40))
    with pytest.raises(parsing.GfValidationError):
        _draft(times=many)


@pytest.mark.parametrize("raw", ["09/26/2026", "32.09.2026", ""])
async def test_invalid_dates(raw):
    with pytest.raises(parsing.GfValidationError):
        _draft(event_date=raw)


async def test_past_date_rejected():
    with pytest.raises(parsing.GfValidationError, match="past"):
        _draft(event_date="25.09.2026")


async def test_times_within_5_minutes_rejected():
    now = _berlin(18, 56)
    with pytest.raises(parsing.GfValidationError, match="too soon"):
        _draft(times="19:00", now=now)
    assert _draft(times="19:00", now=_berlin(18, 54)).slots == (_berlin(19),)


async def test_dst_gap_is_rejected():
    # 29.03.2026 02:30 does not exist in Berlin
    with pytest.raises(parsing.GfValidationError, match="does not exist"):
        parsing.to_utc(date(2026, 3, 29), 2, 30, ZoneInfo(BERLIN))


async def test_dst_overlap_is_rejected():
    # 25.10.2026 02:30 happens twice in Berlin
    with pytest.raises(parsing.GfValidationError, match="twice"):
        parsing.to_utc(date(2026, 10, 25), 2, 30, ZoneInfo(BERLIN))


async def test_dst_new_york_gap():
    with pytest.raises(parsing.GfValidationError):
        parsing.to_utc(date(2026, 3, 8), 2, 30, ZoneInfo("America/New_York"))


async def test_times_around_dst_keep_their_real_offsets():
    tz = ZoneInfo(BERLIN)
    before = parsing.to_utc(date(2026, 10, 24), 20, 0, tz)   # CEST
    after = parsing.to_utc(date(2026, 10, 26), 20, 0, tz)    # CET
    assert before.hour == 18 and after.hour == 19


# ── creation ───────────────────────────────────────────────────────────────

async def test_create_stores_voting_event():
    repo = await _repo()
    e = await repo.gf_get_event(await _create(repo))
    assert e["status"] == events.VOTING
    assert e["slots"] == [_berlin(19), _berlin(20), _berlin(21)]
    assert e["cleanup_at"] == _berlin(22)          # last time + 1 h
    assert e["origin_zone"] == BERLIN
    await repo.close()


async def test_creator_is_not_a_participant():
    repo = await _repo()
    e = await repo.gf_get_event(await _create(repo))
    assert await events.participants(repo, e) == []
    await repo.close()


# ── voting ─────────────────────────────────────────────────────────────────

async def test_vote_one_and_several_times():
    repo = await _repo()
    eid = await _create(repo)
    await _vote(repo, eid, JULES, 19, 20)
    await _vote(repo, eid, MAX, 20)
    e = await repo.gf_get_event(eid)
    counts = events.vote_counts(e, await repo.gf_get_votes(eid))
    assert counts == {_berlin(19): 1, _berlin(20): 2, _berlin(21): 0}
    await repo.close()


async def test_participants_are_visible_before_a_time_is_found():
    repo = await _repo()
    eid = await _create(repo)
    await _vote(repo, eid, JULES, 19)
    await _vote(repo, eid, MAX, 20, zone=KARACHI)
    e = await repo.gf_get_event(eid)
    assert e["status"] == events.VOTING
    assert await events.participants(repo, e) == [(JULES, BERLIN), (MAX, KARACHI)]
    await repo.close()


async def test_changing_and_removing_votes():
    repo = await _repo()
    eid = await _create(repo)
    await _vote(repo, eid, MAX, 19)
    await _vote(repo, eid, MAX, 21)
    assert [v["starts_at"] for v in await repo.gf_get_votes(eid)] == [_berlin(21)]
    await _vote(repo, eid, MAX)
    assert await repo.gf_get_votes(eid) == []
    await repo.close()


async def test_votes_for_closed_times_are_ignored():
    repo = await _repo()
    eid = await _create(repo)
    await _vote(repo, eid, MAX, 19, 20, now=_berlin(18, 56))   # 19:00 closed
    assert [v["starts_at"] for v in await repo.gf_get_votes(eid)] == [_berlin(20)]
    await repo.close()


async def test_votes_from_different_zones_count_together():
    # availability is timezone-independent: one instant, whatever the channel
    repo = await _repo()
    eid = await _create(repo, players="2-8")
    await _vote(repo, eid, MAX, 20, zone=KARACHI)
    await _vote(repo, eid, TOM, 20, zone=TOKYO)
    e = await repo.gf_get_event(eid)
    assert e["scheduled_at"] == _berlin(20)
    await repo.close()


# ── time finding (Variant 1) ───────────────────────────────────────────────

async def test_below_minimum_stays_voting():
    repo = await _repo()
    eid = await _create(repo)
    for who in (JULES, MAX, TOM):
        await _vote(repo, eid, who, 20)
    assert (await repo.gf_get_event(eid))["status"] == events.VOTING
    await repo.close()


async def test_spec_example():
    # min 4: Jules 19+20, Max 20, Tom 20, Alex 20, Sarah 21
    # -> 20:00; participants Jules/Max/Tom/Alex; Sarah drops out
    repo = await _repo()
    eid = await _create(repo)
    await _vote(repo, eid, JULES, 19, 20)
    await _vote(repo, eid, MAX, 20)
    await _vote(repo, eid, TOM, 20)
    await _vote(repo, eid, SARAH, 21)
    result = await _vote(repo, eid, ALEX, 20)
    assert result.scheduled_at == _berlin(20)
    e = await repo.gf_get_event(eid)
    assert e["status"] == events.SCHEDULED
    assert e["scheduled_at"] == _berlin(20)
    assert e["cleanup_at"] == _berlin(21)
    ids = [p for p, _ in await events.participants(repo, e)]
    assert ids == [JULES, MAX, TOM, ALEX]
    assert SARAH not in ids
    await repo.close()


async def test_votes_are_kept_after_the_time_is_fixed():
    repo = await _repo()
    eid = await _create(repo, players="1-8")
    await _vote(repo, eid, JULES, 19, 20)
    assert len(await repo.gf_get_votes(eid)) == 2
    await repo.close()


async def test_several_times_reaching_minimum_at_once_earliest_wins():
    repo = await _repo()
    eid = await _create(repo, players="2-8")
    await _vote(repo, eid, JULES, 21, 20)             # 1 vote each
    await _vote(repo, eid, MAX, 20, 21)               # both reach 2 at once
    assert (await repo.gf_get_event(eid))["scheduled_at"] == _berlin(20)
    await repo.close()


async def test_second_schedule_attempt_changes_nothing():
    repo = await _repo()
    eid = await _create(repo, players="1-8")
    await _vote(repo, eid, JULES, 20)
    assert await events.try_schedule(repo, eid, NOW) is None
    assert (await repo.gf_get_event(eid))["scheduled_at"] == _berlin(20)
    await repo.close()


async def test_voting_after_the_time_is_fixed_is_refused():
    repo = await _repo()
    eid = await _create(repo, players="1-8")
    await _vote(repo, eid, JULES, 20)
    assert (await _vote(repo, eid, MAX, 21)).error == "not_voting"
    await repo.close()


async def test_roster_capped_at_maximum_by_vote_order():
    repo = await _repo()
    eid = await _create(repo, players="2-2")
    # three votes land before the check (e.g. a restart in between)
    for who, minute in ((TOM, 3), (JULES, 1), (MAX, 2)):
        await repo.gf_set_votes(eid, who, [_berlin(20)], BERLIN,
                                NOW + timedelta(minutes=minute))
    assert await events.try_schedule(repo, eid, NOW) == _berlin(20)
    e = await repo.gf_get_event(eid)
    assert e["status"] == events.CLOSED
    assert [p for p, _ in await events.participants(repo, e)] == [JULES, MAX]
    await repo.close()


# ── join / leave ───────────────────────────────────────────────────────────

async def _scheduled(repo, players="4-5"):
    eid = await _create(repo, players=players)
    for who in (JULES, MAX, TOM, ALEX):
        await _vote(repo, eid, who, 20)
    return eid


async def test_join_after_the_time_is_found():
    repo = await _repo()
    eid = await _scheduled(repo)
    assert await events.join(repo, eid, SARAH, KARACHI, NOW) == "added"
    roster = await repo.gf_get_participants(eid)
    assert (roster[-1]["discord_id"], roster[-1]["zone"], roster[-1]["source"]) == (
        SARAH, KARACHI, "JOIN")
    await repo.close()


async def test_reaching_the_maximum_closes_joining():
    repo = await _repo()
    eid = await _scheduled(repo)
    await events.join(repo, eid, SARAH, BERLIN, NOW)          # 5/5
    assert (await repo.gf_get_event(eid))["status"] == events.CLOSED
    assert await events.join(repo, eid, 6, BERLIN, NOW) == "full"
    await repo.close()


async def test_joining_closes_5_minutes_before_start():
    repo = await _repo()
    eid = await _scheduled(repo)
    assert await events.join(repo, eid, SARAH, BERLIN, _berlin(19, 56)) == "closed"
    await repo.close()


async def test_join_while_voting_is_refused():
    repo = await _repo()
    eid = await _create(repo)
    assert await events.join(repo, eid, SARAH, BERLIN, NOW) == "not_scheduled"
    await repo.close()


async def test_leave_while_voting_withdraws_votes():
    repo = await _repo()
    eid = await _create(repo)
    await _vote(repo, eid, MAX, 19, 20)
    assert await events.leave(repo, eid, MAX, NOW) == "left"
    assert await repo.gf_get_votes(eid) == []
    assert await events.leave(repo, eid, MAX, NOW) == "not_member"
    await repo.close()


async def test_leave_keeps_the_start_time():
    repo = await _repo()
    eid = await _scheduled(repo)
    await events.leave(repo, eid, JULES, NOW)
    await events.leave(repo, eid, MAX, NOW)
    e = await repo.gf_get_event(eid)
    assert e["scheduled_at"] == _berlin(20) and e["status"] == events.SCHEDULED
    await repo.close()


async def test_leaving_a_full_group_reopens_joining():
    repo = await _repo()
    eid = await _scheduled(repo, players="4-4")               # full at once
    assert (await repo.gf_get_event(eid))["status"] == events.CLOSED
    await events.leave(repo, eid, JULES, NOW)
    assert (await repo.gf_get_event(eid))["status"] == events.SCHEDULED
    assert await events.join(repo, eid, SARAH, BERLIN, NOW) == "added"
    await repo.close()


async def test_leaving_within_5_minutes_does_not_reopen():
    repo = await _repo()
    eid = await _scheduled(repo, players="4-4")
    await events.leave(repo, eid, JULES, _berlin(19, 57))
    assert (await repo.gf_get_event(eid))["status"] == events.CLOSED
    await repo.close()


# ── rendering ──────────────────────────────────────────────────────────────

async def _embed(repo, eid, zone, now=NOW):
    e = await repo.gf_get_event(eid)
    votes = await repo.gf_get_votes(eid)
    return render.build_event_embed(e, zone, counts=events.vote_counts(e, votes),
                                    people=await events.participants(repo, e),
                                    now=now)


async def test_each_zone_shows_its_own_local_time():
    repo = await _repo()
    eid = await _create(repo)
    await _vote(repo, eid, JULES, 20)
    berlin = await _embed(repo, eid, BERLIN)
    karachi = await _embed(repo, eid, KARACHI)
    assert "20:00 — 1 vote" in berlin.description        # Berlin 20:00
    assert "23:00 — 1 vote" in karachi.description       # = Karachi 23:00
    assert "23:00" not in berlin.description             # no foreign times
    await repo.close()


async def test_other_day_is_shown_when_a_time_crosses_midnight():
    repo = await _repo()
    # Karachi = Berlin + 3 h: 20:00 -> Sat 23:00, 22:00 -> Sun 01:00
    eid = await _create(repo, times="20:00 / 22:00")
    karachi = await _embed(repo, eid, KARACHI)
    assert karachi.description.startswith("Sat, 26.09.")
    assert "🕚 23:00 — 0 votes" in karachi.description        # same day: time only
    assert "Sun, 27.09. 01:00 — 0 votes" in karachi.description   # next day: dated
    await repo.close()


async def test_participants_identical_in_every_zone():
    repo = await _repo()
    eid = await _create(repo)
    await _vote(repo, eid, JULES, 19)
    await _vote(repo, eid, MAX, 20, zone=KARACHI)
    a, b = await _embed(repo, eid, BERLIN), await _embed(repo, eid, TOKYO)
    for embed in (a, b):
        assert f"<@{JULES}> <@{MAX}>" in embed.description
        assert "2 participants" in embed.description
    await repo.close()


async def test_fixed_embed_shows_countdown_and_local_time():
    repo = await _repo()
    eid = await _scheduled(repo)
    embed = await _embed(repo, eid, BERLIN)
    assert embed.title == "🎉 Satura Gruppen Gate"
    assert "🟢 Starting in 8h" in embed.description          # 12:00 -> 20:00
    assert "Sat, 26.09. · 20:00" in embed.description
    assert "4/5 participants" in embed.description
    tokyo = await _embed(repo, eid, TOKYO)
    assert "Sun, 27.09. · 03:00" in tokyo.description         # same instant
    assert "Starting in 8h" in tokyo.description              # same countdown
    await repo.close()


async def test_closed_embed_says_full():
    repo = await _repo()
    eid = await _scheduled(repo, players="4-4")
    assert "🔒 Group is full." in (await _embed(repo, eid, BERLIN)).description
    await repo.close()


@pytest.mark.parametrize("minutes,text", [
    (135, "Starting in 2h 15m"), (127, "Starting in 2h 15m"), (60, "Starting in 1h"),
    (44, "Starting in 45m"), (16, "Starting in 20m"), (15, "Starting in 15m"),
    (3, "Starting in 3m"), (0, "Starting now")])
async def test_countdown_steps(minutes, text):
    assert render.countdown(NOW + timedelta(minutes=minutes), NOW) == text


async def test_countdown_text_changes_only_at_steps():
    start = NOW + timedelta(hours=3)
    texts = {render.countdown(start, NOW + timedelta(minutes=m)) for m in range(0, 60)}
    assert len(texts) == 4                    # 15-minute steps above one hour


# ── views ──────────────────────────────────────────────────────────────────

async def test_vote_select_offers_open_times_in_the_zone():
    repo = await _repo()
    e = await repo.gf_get_event(await _create(repo))
    view = views.view_for(repo, _settings(), e, KARACHI, NOW)
    select = view.children[0]
    assert select.custom_id == views.VOTE_SELECT_ID
    assert [o.label for o in select.options] == [
        "Sat, 26.09. 22:00", "Sat, 26.09. 23:00", "Sun, 27.09. 00:00"]
    assert [views.slot_from_value(o.value) for o in select.options] == e["slots"]
    assert select.min_values == 0 and select.max_values == 3
    await repo.close()


async def test_closed_times_disappear_from_the_select():
    repo = await _repo()
    e = await repo.gf_get_event(await _create(repo))
    # 18:56: voting on 19:00 closed at 18:55; 20:00 and 21:00 still open
    view = views.view_for(repo, _settings(), e, BERLIN, _berlin(18, 56))
    assert [views.slot_from_value(o.value) for o in view.children[0].options] == [
        _berlin(20), _berlin(21)]
    await repo.close()


async def test_fixed_view_disables_join_when_closed():
    repo = await _repo()
    eid = await _scheduled(repo, players="4-4")
    e = await repo.gf_get_event(eid)
    join = views.view_for(repo, _settings(), e, BERLIN, NOW).children[0]
    assert join.custom_id == views.JOIN_ID and join.disabled is True
    await repo.close()


async def test_router_views_are_persistent():
    repo = await _repo()
    assert views.VotingView(repo, _settings()).timeout is None
    assert views.FixedView(repo, _settings()).timeout is None
    await repo.close()


# ── sync across zone channels ──────────────────────────────────────────────

_ids = itertools.count(50_000)


def _not_found():
    return discord.NotFound(MagicMock(status=404, reason="Not Found"), "gone")


class FakeChannel:
    def __init__(self):
        self.id = next(_ids)
        self.messages = {}
        self.send = AsyncMock(side_effect=self._send)
        self.fetch_message = AsyncMock(side_effect=self._fetch)

    async def _send(self, embed=None, view=None, **kw):
        msg = SimpleNamespace(id=next(_ids), embed=embed, view=view)
        msg.edit = AsyncMock(side_effect=lambda **k: setattr(msg, "embed", k["embed"]))
        msg.delete = AsyncMock(side_effect=lambda: self.messages.pop(msg.id, None))
        self.messages[msg.id] = msg
        return msg

    async def _fetch(self, mid):
        if mid not in self.messages:
            raise _not_found()
        return self.messages[mid]


async def _zones(repo, *names):
    channels = {}
    for z in names:
        ch = FakeChannel()
        channels[ch.id] = ch
        await repo.gf_save_zone(z, role_id=next(_ids), channel_id=ch.id,
                                status=ACTIVE, now=NOW)
    bot = MagicMock()
    bot.get_channel = lambda cid: channels.get(cid)
    return bot, {z: channels[(await repo.gf_get_zone(z))["channel_id"]] for z in names}


async def test_sync_posts_one_message_per_zone():
    repo = await _repo()
    bot, chans = await _zones(repo, BERLIN, KARACHI, TOKYO)
    eid = await _create(repo)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    for ch in chans.values():
        ch.send.assert_awaited_once()
    assert {m["zone"] for m in await repo.gf_get_event_messages(eid)} == {
        BERLIN, KARACHI, TOKYO}
    await repo.close()


async def test_unchanged_event_is_not_edited_again():
    repo = await _repo()
    bot, chans = await _zones(repo, BERLIN, KARACHI)
    eid = await _create(repo)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    for ch in chans.values():
        ch.fetch_message.assert_not_awaited()
    await repo.close()


async def test_a_change_updates_every_zone():
    repo = await _repo()
    bot, chans = await _zones(repo, BERLIN, KARACHI)
    eid = await _create(repo)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    await _vote(repo, eid, MAX, 20)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    for ch in chans.values():
        msg = next(iter(ch.messages.values()))
        msg.edit.assert_awaited_once()
        assert f"<@{MAX}>" in msg.embed.description
    await repo.close()


async def test_status_change_is_reflected_everywhere():
    repo = await _repo()
    bot, chans = await _zones(repo, BERLIN, TOKYO)
    eid = await _create(repo, players="1-8")
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    await _vote(repo, eid, MAX, 20)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    for ch in chans.values():
        assert next(iter(ch.messages.values())).embed.title.startswith("🎉")
    await repo.close()


async def test_new_zone_receives_existing_events():
    repo = await _repo()
    bot, chans = await _zones(repo, BERLIN)
    eid = await _create(repo)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    tokyo = FakeChannel()
    await repo.gf_save_zone(TOKYO, role_id=1, channel_id=tokyo.id, status=ACTIVE,
                            now=NOW)
    bot.get_channel = lambda cid: {chans[BERLIN].id: chans[BERLIN],
                                   tokyo.id: tokyo}.get(cid)
    await sync.sync_all(bot, repo, _settings(), NOW)
    tokyo.send.assert_awaited_once()
    await repo.close()


async def test_deleted_event_message_is_reposted():
    repo = await _repo()
    bot, chans = await _zones(repo, BERLIN)
    eid = await _create(repo)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    chans[BERLIN].messages.clear()                 # deleted by someone
    bot._gf_rendered.clear()
    await _vote(repo, eid, MAX, 20)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    assert chans[BERLIN].send.await_count == 2
    await repo.close()


async def test_event_message_never_reposted_on_other_errors():
    repo = await _repo()
    bot, chans = await _zones(repo, BERLIN)
    eid = await _create(repo)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    chans[BERLIN].fetch_message = AsyncMock(side_effect=RuntimeError("503"))
    await _vote(repo, eid, MAX, 20)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    assert chans[BERLIN].send.await_count == 1
    await repo.close()


async def test_messages_of_deactivated_zones_are_forgotten():
    repo = await _repo()
    bot, chans = await _zones(repo, BERLIN, TOKYO)
    eid = await _create(repo)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    await repo.gf_save_zone(TOKYO, role_id=1, channel_id=None, status=DEACTIVATED,
                            now=NOW)
    await _vote(repo, eid, MAX, 20)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    assert {m["zone"] for m in await repo.gf_get_event_messages(eid)} == {BERLIN}
    await repo.close()


# ── interactions end to end ────────────────────────────────────────────────

def _interaction(bot, message_id, user_id=MAX):
    it = MagicMock()
    it.client = bot
    it.message = SimpleNamespace(id=message_id)
    it.user = SimpleNamespace(id=user_id)
    it.response = MagicMock()
    it.response.defer = AsyncMock()
    it.response.send_message = AsyncMock()
    return it


async def _message_id(repo, eid, zone):
    return next(m["message_id"] for m in await repo.gf_get_event_messages(eid)
                if m["zone"] == zone)


async def test_vote_click_records_zone_and_updates_all_channels():
    repo = await _repo()
    bot, chans = await _zones(repo, BERLIN, KARACHI)
    eid = await _create(repo, players="1-8")
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    select = views.VoteSelect(repo, _settings())
    select._values = [views.slot_value(_berlin(20))]
    interaction = _interaction(bot, await _message_id(repo, eid, KARACHI))

    await select.callback(interaction)

    interaction.response.defer.assert_awaited_once()
    vote = (await repo.gf_get_votes(eid))[0]
    assert (vote["discord_id"], vote["zone"]) == (MAX, KARACHI)
    assert (await repo.gf_get_event(eid))["status"] == events.SCHEDULED
    for ch in chans.values():
        assert next(iter(ch.messages.values())).embed.title.startswith("🎉")
    await repo.close()


async def test_join_click_when_full_answers_ephemerally():
    repo = await _repo()
    bot, _ = await _zones(repo, BERLIN)
    eid = await _scheduled(repo, players="4-4")
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    button = views.FixedView(repo, _settings()).children[0]
    interaction = _interaction(bot, await _message_id(repo, eid, BERLIN), SARAH)
    await button.callback(interaction)
    assert "full" in interaction.response.send_message.call_args.args[0]
    interaction.response.defer.assert_not_awaited()
    await repo.close()


async def test_click_on_unknown_message():
    repo = await _repo()
    select = views.VoteSelect(repo, _settings())
    select._values = []
    interaction = _interaction(MagicMock(), 123)
    await select.callback(interaction)
    assert "no longer exists" in interaction.response.send_message.call_args.args[0]
    await repo.close()


# ── /lfg ───────────────────────────────────────────────────────────────────

def _command_interaction(bot, channel_id, user_id=JULES):
    it = MagicMock()
    it.client = bot
    it.channel_id = channel_id
    it.user = SimpleNamespace(id=user_id)
    it.response = MagicMock()
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.delete_original_response = AsyncMock()
    return it


def _future_date(days=1) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).strftime("%d.%m.%Y")


async def test_lfg_outside_a_zone_channel_points_to_the_hub():
    repo = await _repo()
    await repo.gf_set_setting("hub_channel_id", "4242")
    bot = build_bot(_settings(), repo)
    it = _command_interaction(bot, channel_id=1)
    await bot.tree.get_command("lfg").callback(
        it, title="x", players="1-2", date=_future_date(), times="20:00")
    assert "<#4242>" in it.response.send_message.call_args.args[0]
    assert await repo.gf_events_with_status([events.VOTING]) == []
    await repo.close()


async def test_lfg_in_a_zone_channel_creates_and_posts_everywhere():
    repo = await _repo()
    bot = build_bot(_settings(), repo)
    fake, chans = await _zones(repo, BERLIN, TOKYO)
    bot.get_channel = fake.get_channel
    it = _command_interaction(bot, chans[TOKYO].id)
    await bot.tree.get_command("lfg").callback(
        it, title="Galaxy Gate", players="4-8", date=_future_date(),
        times="20:00 / 21:00")
    [event] = await repo.gf_events_with_status([events.VOTING])
    assert event["origin_zone"] == TOKYO
    assert event["slots"][0].astimezone(ZoneInfo(TOKYO)).hour == 20   # read as Tokyo time
    for ch in chans.values():
        ch.send.assert_awaited_once()
    it.delete_original_response.assert_awaited()
    await repo.close()


async def test_lfg_invalid_input_creates_nothing():
    repo = await _repo()
    bot = build_bot(_settings(), repo)
    fake, chans = await _zones(repo, BERLIN)
    bot.get_channel = fake.get_channel
    it = _command_interaction(bot, chans[BERLIN].id)
    await bot.tree.get_command("lfg").callback(
        it, title="x", players="8-4", date=_future_date(), times="20:00")
    assert "maximum" in it.response.send_message.call_args.args[0]
    assert await repo.gf_events_with_status([events.VOTING]) == []
    await repo.close()


async def test_lfg_is_the_group_finder_command():
    repo = await _repo()
    bot = build_bot(_settings(), repo)
    params = {p.name for p in bot.tree.get_command("lfg").parameters}
    assert params == {"title", "players", "date", "times"}
    await repo.close()
