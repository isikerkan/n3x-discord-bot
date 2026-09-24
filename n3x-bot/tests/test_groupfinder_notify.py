"""Stage 4 of the Group Finder: the one-time "time found" ping, the mandatory
15-minute DM, and the cancel DM — each exactly once, restart-safe."""
import asyncio
import itertools
import os
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import discord

from n3x_bot.config import Settings
from n3x_bot.groupfinder import events, lifecycle, notify, parsing, sync, views
from n3x_bot.groupfinder.zones import ACTIVE
from n3x_bot.seed import seed_defaults
from n3x_bot.storage.json_repo import JsonRepository

UTC = timezone.utc
NOW = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)          # 12:00 Berlin
BERLIN, KARACHI = "Europe/Berlin", "Asia/Karachi"
CREATOR, JULES, KEVIN, MUNEEB, ALEX, SARAH = 1, 2, 3, 4, 5, 6
GUILD = 777


def _settings() -> Settings:
    return Settings(discord_token="tok", target_role_id=1, welcome_channel_id=2,
                    reminder_channel_id=999, julez_id=424242, admin_role_id=900,
                    _env_file=None, _env_prefix="NONEXISTENT_")


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


async def _create(repo, players="4-8"):
    draft = parsing.build_draft(title="Galaxy Gate", players=players,
                                event_date="26.09.2026",
                                times="19:00 / 20:00 / 21:00", zone=BERLIN, now=NOW)
    return await events.create(repo, draft, creator_id=CREATOR,
                               origin_zone=BERLIN, now=NOW)


# ── fake world ─────────────────────────────────────────────────────────────

_ids = itertools.count(90_000)


def _not_found():
    return discord.NotFound(MagicMock(status=404, reason="Not Found"), "gone")


class FakeChannel:
    def __init__(self):
        self.id = next(_ids)
        self.guild = SimpleNamespace(id=GUILD)
        self.messages = {}
        self.send = AsyncMock(side_effect=self._send)
        self.fetch_message = AsyncMock(side_effect=self._fetch)

    async def _send(self, content=None, embed=None, view=None,
                    allowed_mentions=None, **kw):
        msg = SimpleNamespace(id=next(_ids), content=content, embed=embed,
                              view=view, allowed_mentions=allowed_mentions)

        async def _edit(**k):
            msg.content, msg.embed, msg.view = k.get("content"), k["embed"], k.get("view")
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


class FakeUser:
    def __init__(self, uid, dms_open=True):
        self.id = uid
        self.inbox = []
        self.send = AsyncMock(side_effect=self._send if dms_open else discord.Forbidden(
            MagicMock(status=403, reason="Forbidden"), "Cannot send messages"))

    async def _send(self, text):
        self.inbox.append(text)


async def _world(repo, *zones, closed_dms=()):
    chans = {}
    for z in zones:
        ch = FakeChannel()
        chans[z] = ch
        await repo.gf_save_zone(z, role_id=next(_ids), channel_id=ch.id,
                                status=ACTIVE, now=NOW)
    by_id = {c.id: c for c in chans.values()}
    users = {uid: FakeUser(uid, uid not in closed_dms)
             for uid in (CREATOR, JULES, KEVIN, MUNEEB, ALEX, SARAH)}
    bot = MagicMock()
    bot.get_channel = lambda cid: by_id.get(cid)
    bot.get_user = lambda uid: users.get(uid)
    bot.fetch_user = AsyncMock(side_effect=lambda uid: users[uid])
    bot.users = users
    return bot, chans


async def _found(repo, bot, *, players="4-8"):
    """Jules/Kevin/Alex (Berlin) and Muneeb (Karachi) pick 20:00; Sarah 21:00.
    The event is posted before the time is found, then announced."""
    eid = await _create(repo, players)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    for who, zone in ((JULES, BERLIN), (KEVIN, BERLIN), (MUNEEB, KARACHI)):
        await events.vote(repo, eid, who, zone, [_berlin(20)], NOW)
    await events.vote(repo, eid, SARAH, BERLIN, [_berlin(21)], NOW)
    await events.vote(repo, eid, ALEX, BERLIN, [_berlin(20)], NOW)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    return eid


def _allowed_ids(message) -> set[int]:
    return {u.id for u in message.allowed_mentions.users}


# ── time found: the one-time ping ──────────────────────────────────────────

async def test_time_found_reposts_in_every_zone_with_the_same_text():
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN, KARACHI)
    eid = await _found(repo, bot)
    contents = set()
    for ch in chans.values():
        assert ch.send.await_count == 2               # pre-announcement + repost
        msg = ch.only                                 # the old one was deleted
        assert msg.content.startswith("✅ **Time found!**")
        contents.add(msg.content)
        assert msg.embed.title.startswith("🎉")
    [content] = contents                              # identical everywhere
    for uid in (JULES, KEVIN, MUNEEB, ALEX):
        assert f"<@{uid}>" in content
    assert f"<@{SARAH}>" not in content               # she dropped out
    e = await repo.gf_get_event(eid)
    assert e["time_found_notified_at"] == NOW
    assert {m["message_id"] for m in await repo.gf_get_event_messages(eid)} == {
        ch.only.id for ch in chans.values()}
    await repo.close()


async def test_each_channel_only_pings_its_own_zone():
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN, KARACHI)
    await _found(repo, bot)
    assert _allowed_ids(chans[BERLIN].only) == {JULES, KEVIN, ALEX}
    assert _allowed_ids(chans[KARACHI].only) == {MUNEEB}
    for ch in chans.values():
        am = ch.only.allowed_mentions
        assert am.everyone is False and am.roles is False
    await repo.close()


async def test_announcement_happens_only_once():
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN, KARACHI)
    eid = await _found(repo, bot)
    for _ in range(3):
        await sync.sync_event(bot, repo, _settings(), eid, NOW)
        await lifecycle.tick(bot, repo, _settings(), NOW + timedelta(minutes=1))
    for ch in chans.values():
        assert ch.send.await_count == 2               # never posted again
    await repo.close()


async def test_countdown_edits_never_carry_pings():
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN)
    await _found(repo, bot)
    for minute in (1, 15, 30, 45, 60):
        await lifecycle.tick(bot, repo, _settings(), NOW + timedelta(minutes=minute))
    msg = chans[BERLIN].only
    assert msg.edit.await_count >= 2
    for call in msg.edit.await_args_list:
        assert "<@" not in (call.kwargs.get("content") or "")
    assert msg.content == "✅ **Time found!**"         # mentions dropped, line kept
    await repo.close()


async def test_plain_reposts_ping_nobody():
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN)
    eid = await _found(repo, bot)
    chans[BERLIN].messages.clear()                    # deleted by someone
    bot._gf_rendered.clear()
    await sync.sync_event(bot, repo, _settings(), eid, NOW + timedelta(minutes=20))
    msg = chans[BERLIN].only
    assert "<@" not in (msg.content or "")
    assert msg.allowed_mentions.users is False        # AllowedMentions.none()
    await repo.close()


async def test_restart_after_scheduling_but_before_the_ping_sends_it():
    # the time was fixed, then the bot died before announcing
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN)
    eid = await _create(repo, players="1-8")
    await events.vote(repo, eid, JULES, BERLIN, [_berlin(20)], NOW)   # no sync
    await lifecycle.tick(bot, repo, _settings(), NOW + timedelta(minutes=3))
    assert chans[BERLIN].only.content.startswith("✅ **Time found!**")
    assert _allowed_ids(chans[BERLIN].only) == {JULES}
    await repo.close()


async def test_restart_after_the_ping_never_pings_again():
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN)
    eid = await _found(repo, bot)
    fresh, fresh_chans = bot, chans                   # same channels, new process
    fresh._gf_rendered = {}
    await lifecycle.tick(fresh, repo, _settings(), NOW + timedelta(minutes=5))
    assert fresh_chans[BERLIN].send.await_count == 2
    assert (await repo.gf_get_event(eid))["time_found_notified_at"] == NOW
    await repo.close()


async def test_no_announcement_after_the_start():
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN)
    eid = await _create(repo, players="1-8")
    await events.vote(repo, eid, JULES, BERLIN, [_berlin(20)], NOW)
    await lifecycle.tick(bot, repo, _settings(), _berlin(20, 10))     # came back late
    assert (await repo.gf_get_event(eid))["time_found_notified_at"] is not None
    assert "<@" not in (chans[BERLIN].only.content or "")
    await repo.close()


async def test_vote_click_that_finds_the_time_announces_it():
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN, KARACHI)
    eid = await _create(repo, players="1-8")
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    select = views.VoteSelect(repo, _settings())
    select._values = [views.slot_value(_berlin(20))]
    it = MagicMock()
    it.client = bot
    it.message = SimpleNamespace(id=chans[KARACHI].only.id)
    it.user = SimpleNamespace(id=MUNEEB)
    it.response = MagicMock()
    it.response.defer = AsyncMock()
    await select.callback(it)
    assert _allowed_ids(chans[KARACHI].only) == {MUNEEB}
    assert _allowed_ids(chans[BERLIN].only) == set()
    await repo.close()


# ── 15-minute reminder ─────────────────────────────────────────────────────

async def test_reminder_not_before_15_minutes():
    repo = await _repo()
    bot, _ = await _world(repo, BERLIN)
    eid = await _found(repo, bot)
    assert await notify.send_due_reminders(bot, repo, eid, _berlin(19, 44)) == 0
    assert all(not u.inbox for u in bot.users.values())
    await repo.close()


async def test_reminder_goes_to_every_participant_with_a_link_to_their_zone():
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN, KARACHI)
    await _found(repo, bot)
    await lifecycle.tick(bot, repo, _settings(), _berlin(19, 45))
    for uid in (JULES, KEVIN, ALEX):
        [dm] = bot.users[uid].inbox
        assert dm.startswith("🚀 **Galaxy Gate** starts in 15 minutes!")
        assert f"/{GUILD}/{chans[BERLIN].id}/{chans[BERLIN].only.id}" in dm
    [dm] = bot.users[MUNEEB].inbox
    assert f"/{chans[KARACHI].id}/{chans[KARACHI].only.id}" in dm
    assert bot.users[SARAH].inbox == []               # not a participant
    assert bot.users[CREATOR].inbox == []             # creator did not vote
    await repo.close()


async def test_reminder_is_sent_exactly_once():
    repo = await _repo()
    bot, _ = await _world(repo, BERLIN)
    await _found(repo, bot)
    for minute in (45, 46, 50, 54):
        await lifecycle.tick(bot, repo, _settings(), _berlin(19, minute))
    assert all(len(bot.users[uid].inbox) == 1 for uid in (JULES, KEVIN, ALEX))
    await repo.close()


async def test_concurrent_triggers_still_send_once():
    repo = await _repo()
    bot, _ = await _world(repo, BERLIN)
    eid = await _found(repo, bot)
    await asyncio.gather(*(notify.send_due_reminders(bot, repo, eid, _berlin(19, 46))
                           for _ in range(4)))
    assert all(len(bot.users[uid].inbox) == 1 for uid in (JULES, KEVIN, ALEX))
    await repo.close()


async def test_bot_down_at_the_15_minute_mark_catches_up_with_real_time():
    repo = await _repo()
    bot, _ = await _world(repo, BERLIN)
    await _found(repo, bot)
    await lifecycle.tick(bot, repo, _settings(), _berlin(19, 51))   # back at 19:51
    [dm] = bot.users[JULES].inbox
    assert "starts in 9 minutes!" in dm
    await repo.close()


async def test_no_reminder_once_started():
    repo = await _repo()
    bot, _ = await _world(repo, BERLIN)
    await _found(repo, bot)
    await lifecycle.tick(bot, repo, _settings(), _berlin(20, 2))    # back after start
    assert bot.users[JULES].inbox == []
    await repo.close()


async def test_closed_dms_are_recorded_and_not_retried():
    repo = await _repo()
    bot, _ = await _world(repo, BERLIN, closed_dms=(KEVIN,))
    eid = await _found(repo, bot)
    await lifecycle.tick(bot, repo, _settings(), _berlin(19, 45))
    await lifecycle.tick(bot, repo, _settings(), _berlin(19, 46))
    assert bot.users[KEVIN].send.await_count == 1                   # not retried
    status = {r["discord_id"]: r["status"] for r in await repo.gf_get_reminders(eid)}
    assert status[KEVIN] == "FAILED" and status[JULES] == "SENT"
    assert len(bot.users[JULES].inbox) == 1                         # others fine
    await repo.close()


async def test_late_joiner_is_reminded_on_joining(monkeypatch):
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN)
    await _found(repo, bot)
    await lifecycle.tick(bot, repo, _settings(), _berlin(19, 45))   # first batch
    # Sarah joins at 19:50 via the button
    join = views.FixedView(repo, _settings()).children[0]
    it = MagicMock()
    it.client = bot
    it.message = SimpleNamespace(id=chans[BERLIN].only.id)
    it.user = SimpleNamespace(id=SARAH)
    it.response = MagicMock()
    it.response.defer = AsyncMock()
    it.response.send_message = AsyncMock()

    class _Clock(datetime):                          # the click happens at 19:50
        @classmethod
        def now(cls, tz=None):
            return _berlin(19, 50)
    monkeypatch.setattr(views, "datetime", _Clock)
    await join.callback(it)
    [dm] = bot.users[SARAH].inbox
    assert "starts in 10 minutes!" in dm
    assert len(bot.users[JULES].inbox) == 1                         # not again
    await repo.close()


async def test_manual_reminder_does_not_duplicate_the_loop():
    repo = await _repo()
    bot, _ = await _world(repo, BERLIN)
    eid = await _found(repo, bot)
    await notify.send_due_reminders(bot, repo, eid, _berlin(19, 45))
    await lifecycle.tick(bot, repo, _settings(), _berlin(19, 46))
    assert len(bot.users[ALEX].inbox) == 1
    await repo.close()


# ── cancel notice ──────────────────────────────────────────────────────────

async def test_cancel_notifies_participants_but_not_the_canceller():
    repo = await _repo()
    bot, chans = await _world(repo, BERLIN, KARACHI)
    eid = await _found(repo, bot)
    confirm = views._ConfirmCancelView(repo, _settings(), eid)
    it = MagicMock()
    it.client = bot
    it.user = SimpleNamespace(id=JULES, roles=[SimpleNamespace(id=900)])  # admin
    it.response = MagicMock()
    it.response.edit_message = AsyncMock()
    it.edit_original_response = AsyncMock()
    await confirm.confirm.callback(it)

    for uid in (KEVIN, MUNEEB, ALEX):
        assert bot.users[uid].inbox == ["❌ **Galaxy Gate** was cancelled."]
    assert bot.users[JULES].inbox == []             # cancelled it himself
    assert bot.users[SARAH].inbox == []             # was not a participant
    assert not any(ch.messages for ch in chans.values())
    await repo.close()


async def test_cancel_while_voting_notifies_the_voters():
    repo = await _repo()
    bot, _ = await _world(repo, BERLIN)
    eid = await _create(repo)
    await events.vote(repo, eid, JULES, BERLIN, [_berlin(19)], NOW)
    event = await repo.gf_get_event(eid)
    people = await events.participants(repo, event)
    await events.cancel(repo, eid, CREATOR, False, NOW)
    assert await notify.send_cancel_notices(bot, repo, event, people, CREATOR, NOW) == 1
    assert await notify.send_cancel_notices(bot, repo, event, people, CREATOR, NOW) == 0
    assert len(bot.users[JULES].inbox) == 1
    await repo.close()
