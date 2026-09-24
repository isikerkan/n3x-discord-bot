"""Stage 3 of the Group Finder: the lifecycle loop (countdown, closing, start,
cleanup, no-time-found), restart catch-up, and cancelling."""
import itertools
import os
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import discord
import pytest

from n3x_bot.config import Settings
from n3x_bot.groupfinder import events, lifecycle, parsing, render, sync, views
from n3x_bot.groupfinder.zones import ACTIVE
from n3x_bot.seed import seed_defaults
from n3x_bot.storage.json_repo import JsonRepository

UTC = timezone.utc
NOW = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)          # 12:00 Berlin
BERLIN, TOKYO = "Europe/Berlin", "Asia/Tokyo"
CREATOR, MAX, TOM, ALEX, SARAH = 1, 2, 3, 4, 5
ADMIN_ROLE = 900


def _settings() -> Settings:
    return Settings(discord_token="tok", target_role_id=1, welcome_channel_id=2,
                    reminder_channel_id=999, julez_id=424242,
                    admin_role_id=ADMIN_ROLE, _env_file=None,
                    _env_prefix="NONEXISTENT_")


async def _repo() -> JsonRepository:
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    repo = JsonRepository(path)
    await repo.connect()
    await seed_defaults(repo)
    return repo


def _berlin(hour, minute=0):
    return datetime(2026, 9, 26, hour, minute,
                    tzinfo=ZoneInfo(BERLIN)).astimezone(UTC)


async def _create(repo, players="4-8", times="19:00 / 20:00 / 21:00"):
    draft = parsing.build_draft(title="Satura", players=players,
                                event_date="26.09.2026", times=times,
                                zone=BERLIN, now=NOW)
    return await events.create(repo, draft, creator_id=CREATOR,
                               origin_zone=BERLIN, now=NOW)


async def _scheduled(repo, players="4-8"):
    """Scheduled for 20:00 Berlin with four participants."""
    eid = await _create(repo, players)
    for who in (MAX, TOM, ALEX, SARAH):
        await events.vote(repo, eid, who, BERLIN, [_berlin(20)], NOW)
    return eid


# ── fake zone channels ─────────────────────────────────────────────────────

_ids = itertools.count(70_000)


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

        async def _edit(**k):
            msg.embed, msg.view = k["embed"], k.get("view")
        msg.edit = AsyncMock(side_effect=_edit)
        msg.delete = AsyncMock(side_effect=lambda: self.messages.pop(msg.id, None))
        self.messages[msg.id] = msg
        return msg

    async def _fetch(self, mid):
        if mid not in self.messages:
            raise _not_found()
        return self.messages[mid]

    @property
    def only(self):
        [msg] = self.messages.values()
        return msg


async def _world(repo, *zones):
    chans = {}
    for z in zones:
        ch = FakeChannel()
        chans[z] = ch
        await repo.gf_save_zone(z, role_id=next(_ids), channel_id=ch.id,
                                status=ACTIVE, now=NOW)
    by_id = {c.id: c for c in chans.values()}
    bot = MagicMock()
    bot.get_channel = lambda cid: by_id.get(cid)
    return bot, chans


async def _tick(bot, repo, now):
    await lifecycle.tick(bot, repo, _settings(), now)


# ── transitions (pure) ─────────────────────────────────────────────────────

async def test_next_status_table():
    repo = await _repo()
    e = await repo.gf_get_event(await _scheduled(repo))
    assert events.next_status(e, _berlin(19, 54)) is None
    assert events.next_status(e, _berlin(19, 55)) == events.CLOSED
    assert events.next_status({**e, "status": events.CLOSED},
                              _berlin(20)) == events.STARTED
    assert events.next_status({**e, "status": events.STARTED},
                              _berlin(20, 59)) is None
    assert events.next_status({**e, "status": events.STARTED},
                              _berlin(21)) == events.EXPIRED
    await repo.close()


async def test_voting_without_open_times_becomes_no_time_found():
    repo = await _repo()
    e = await repo.gf_get_event(await _create(repo))
    assert events.next_status(e, _berlin(20, 54)) is None       # 21:00 still open
    assert events.next_status(e, _berlin(20, 55)) == events.NO_TIME_FOUND
    await repo.close()


async def test_advance_catches_up_several_steps_at_once():
    # the bot was down from before the start until after the cleanup time
    repo = await _repo()
    eid = await _scheduled(repo)
    e = await events.advance(repo, await repo.gf_get_event(eid), _berlin(22))
    # SCHEDULED -> STARTED -> EXPIRED; CLOSED is skipped once the start is past
    assert e["status"] == events.EXPIRED
    await repo.close()


async def test_advance_does_not_overwrite_a_concurrent_change():
    repo = await _repo()
    eid = await _scheduled(repo)
    stale = await repo.gf_get_event(eid)
    await repo.gf_update_event(eid, status=events.CANCELLED)   # meanwhile
    e = await events.advance(repo, stale, _berlin(19, 56))
    assert e["status"] == events.CANCELLED
    await repo.close()


async def test_needs_cleanup():
    repo = await _repo()
    e = await repo.gf_get_event(await _create(repo))
    nt = {**e, "status": events.NO_TIME_FOUND}
    assert events.needs_cleanup(nt, _berlin(21, 59)) is False
    assert events.needs_cleanup(nt, _berlin(22)) is True       # last time + 1 h
    assert events.needs_cleanup({**e, "status": events.CANCELLED}, NOW) is True
    assert events.needs_cleanup({**e, "status": events.EXPIRED,
                                 "cleaned_at": NOW}, NOW) is False
    await repo.close()


async def test_started_ago_in_5_minute_steps():
    start = NOW
    assert render.started_ago(start, start + timedelta(minutes=4)) == "Started just now"
    assert render.started_ago(start, start + timedelta(minutes=7)) == "Started 5m ago"
    assert render.started_ago(start, start + timedelta(minutes=59)) == "Started 55m ago"


# ── the tick ───────────────────────────────────────────────────────────────

async def test_countdown_is_edited_only_when_its_text_changes():
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN, TOKYO)
    eid = await _scheduled(repo)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)     # announcement
    await _tick(bot, repo, NOW)          # the one edit that drops the mentions
    for ch in chans.values():
        ch.only.edit.reset_mock()
    for minute in range(1, 15):                 # 8h away: 15-minute steps
        await _tick(bot, repo, NOW + timedelta(minutes=minute))
    for ch in chans.values():
        # 12:01–12:14 all read "Starting in 8h"; at 12:15 it reads 7h 45m
        ch.only.edit.assert_not_awaited()
    await _tick(bot, repo, NOW + timedelta(minutes=15))
    for ch in chans.values():
        ch.only.edit.assert_awaited_once()
        assert "Starting in 7h 45m" in ch.only.embed.description
    await repo.close()


async def test_joining_closes_5_minutes_before_start():
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN)
    eid = await _scheduled(repo)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    await _tick(bot, repo, _berlin(19, 55))
    assert (await repo.gf_get_event(eid))["status"] == events.CLOSED
    join = chans[BERLIN].only.view.children[0]
    assert join.custom_id == views.JOIN_ID and join.disabled is True
    assert "Joining is closed" in chans[BERLIN].only.embed.description
    await repo.close()


async def test_start_switches_to_started_everywhere():
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN, TOKYO)
    eid = await _scheduled(repo)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    await _tick(bot, repo, _berlin(20, 7))
    assert (await repo.gf_get_event(eid))["status"] == events.STARTED
    for ch in chans.values():
        assert ch.only.embed.title == "🔵 Satura"
        assert ch.only.embed.description == "Started 5m ago"
        assert ch.only.view is None                     # nothing to click any more
    await repo.close()


async def test_cleanup_one_hour_after_start_in_every_channel():
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN, TOKYO)
    eid = await _scheduled(repo)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    await _tick(bot, repo, _berlin(20, 59))
    assert all(ch.messages for ch in chans.values())    # not yet
    await _tick(bot, repo, _berlin(21))
    assert not any(ch.messages for ch in chans.values())
    e = await repo.gf_get_event(eid)
    assert e["status"] == events.EXPIRED and e["cleaned_at"] == _berlin(21)
    assert await repo.gf_get_event_messages(eid) == []
    # history is kept, and the event is no longer processed
    assert await repo.gf_events_with_status([events.EXPIRED],
                                            uncleaned_only=True) == []
    await repo.close()


async def test_no_time_found_is_shown_then_removed():
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN)
    eid = await _create(repo)
    await events.vote(repo, eid, MAX, BERLIN, [_berlin(19)], NOW)   # never enough
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    await _tick(bot, repo, _berlin(20, 55))
    assert (await repo.gf_get_event(eid))["status"] == events.NO_TIME_FOUND
    assert chans[BERLIN].only.embed.description == "No time found."
    await _tick(bot, repo, _berlin(22))                 # last time 21:00 + 1 h
    assert not chans[BERLIN].messages
    await repo.close()


async def test_restart_catch_up_in_a_single_tick():
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN)
    eid = await _scheduled(repo)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    # the bot was down from 12:00 until 23:00
    await _tick(bot, repo, _berlin(23))
    assert (await repo.gf_get_event(eid))["status"] == events.EXPIRED
    assert not chans[BERLIN].messages
    await repo.close()


async def test_failed_removal_is_retried_next_tick():
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN)
    eid = await _scheduled(repo)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    chans[BERLIN].only.delete = AsyncMock(side_effect=RuntimeError("503"))
    await _tick(bot, repo, _berlin(21))
    assert (await repo.gf_get_event(eid))["cleaned_at"] is None
    assert len(await repo.gf_get_event_messages(eid)) == 1
    msg = chans[BERLIN].only
    msg.delete = AsyncMock(side_effect=lambda: chans[BERLIN].messages.pop(msg.id))
    await _tick(bot, repo, _berlin(21, 1))
    assert (await repo.gf_get_event(eid))["cleaned_at"] is not None
    await repo.close()


async def test_one_failing_event_does_not_stop_the_others(monkeypatch):
    repo = await _repo()
    bot, _ = await _world(repo, BERLIN)
    bad = await _scheduled(repo)
    good = await _scheduled(repo)
    real = lifecycle.process_event

    async def _flaky(bot, repo, settings, event, now):
        if event["id"] == bad:
            raise RuntimeError("boom")
        await real(bot, repo, settings, event, now)
    monkeypatch.setattr(lifecycle, "process_event", _flaky)
    await _tick(bot, repo, _berlin(20, 1))
    assert (await repo.gf_get_event(good))["status"] == events.STARTED
    await repo.close()


async def test_loop_is_guarded_and_survives_a_failing_tick():
    repo = await _repo()
    repo.gf_events_with_status = AsyncMock(side_effect=RuntimeError("connection is closed"))
    bot = MagicMock()
    loop = lifecycle.start_lifecycle_loop(bot, repo, _settings())
    try:
        await loop.coro()                               # must not raise
        assert loop.is_running()
        assert lifecycle.start_lifecycle_loop(bot, repo, _settings()) is loop
    finally:
        loop.cancel()
    await repo.close()


# ── cancel ─────────────────────────────────────────────────────────────────

async def test_creator_and_admin_may_cancel_others_not():
    repo = await _repo()
    eid = await _create(repo)
    assert await events.cancel(repo, eid, MAX, False, NOW) == "forbidden"
    assert await events.cancel(repo, eid, MAX, True, NOW) == "cancelled"
    other = await _create(repo)
    assert await events.cancel(repo, other, CREATOR, False, NOW) == "cancelled"
    e = await repo.gf_get_event(other)
    assert e["status"] == events.CANCELLED and e["cancelled_at"] == NOW
    await repo.close()


async def test_started_event_cannot_be_cancelled():
    repo = await _repo()
    eid = await _scheduled(repo)
    await events.advance(repo, await repo.gf_get_event(eid), _berlin(20, 1))
    assert await events.cancel(repo, eid, CREATOR, False, NOW) == "not_cancellable"
    await repo.close()


def _interaction(bot, message_id, user_id, *, admin=False):
    it = MagicMock()
    it.client = bot
    it.message = SimpleNamespace(id=message_id)
    it.user = SimpleNamespace(id=user_id, roles=[SimpleNamespace(id=ADMIN_ROLE)]
                              if admin else [])
    it.response = MagicMock()
    it.response.send_message = AsyncMock()
    it.response.edit_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.edit_original_response = AsyncMock()
    return it


def _cancel_button(repo):
    return next(c for c in views.VotingView(repo, _settings()).children
                if getattr(c, "custom_id", None) == views.CANCEL_ID)


async def test_cancel_button_refuses_non_creators():
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN)
    eid = await _create(repo)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    it = _interaction(bot, chans[BERLIN].only.id, MAX)
    await _cancel_button(repo).callback(it)
    assert "Only the creator or an admin" in it.response.send_message.call_args.args[0]
    assert "view" not in it.response.send_message.call_args.kwargs
    await repo.close()


async def test_cancel_asks_then_removes_the_event_everywhere():
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN, TOKYO)
    eid = await _create(repo)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    it = _interaction(bot, chans[TOKYO].only.id, CREATOR)
    await _cancel_button(repo).callback(it)
    confirm = it.response.send_message.call_args.kwargs["view"]
    assert (await repo.gf_get_event(eid))["status"] == events.VOTING   # not yet

    it2 = _interaction(bot, chans[TOKYO].only.id, CREATOR)
    await confirm.confirm.callback(it2)

    e = await repo.gf_get_event(eid)
    assert e["status"] == events.CANCELLED and e["cleaned_at"] is not None
    assert not any(ch.messages for ch in chans.values())
    assert "cancelled" in it2.edit_original_response.call_args.kwargs["content"]
    await repo.close()


async def test_admin_can_cancel_via_button():
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN)
    eid = await _create(repo)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    it = _interaction(bot, chans[BERLIN].only.id, MAX, admin=True)
    await _cancel_button(repo).callback(it)
    confirm = it.response.send_message.call_args.kwargs["view"]
    await confirm.confirm.callback(_interaction(bot, 0, MAX, admin=True))
    assert (await repo.gf_get_event(eid))["status"] == events.CANCELLED
    await repo.close()


async def test_cancelled_event_is_never_reposted():
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN)
    eid = await _create(repo)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    await events.cancel(repo, eid, CREATOR, False, NOW)
    await sync.remove_event_messages(bot, repo, eid)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    assert chans[BERLIN].send.await_count == 1
    await repo.close()


async def test_both_views_carry_the_cancel_button():
    repo = await _repo()
    for view in (views.VotingView(repo, _settings()), views.FixedView(repo, _settings())):
        assert views.CANCEL_ID in {getattr(c, "custom_id", None) for c in view.children}
    await repo.close()


@pytest.mark.parametrize("status", [events.EXPIRED, events.CANCELLED])
async def test_finished_events_render_nothing(status):
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN)
    eid = await _create(repo)
    await repo.gf_update_event(eid, status=status)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    chans[BERLIN].send.assert_not_awaited()
    await repo.close()
