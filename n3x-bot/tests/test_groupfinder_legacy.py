"""Stage 5 of the Group Finder: the cutover — adopting the old LFG channel as
the hub and importing the LFGs that are still running."""
import itertools
import os
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import discord

from n3x_bot.config import Settings
from n3x_bot.groupfinder import admin as gf_admin
from n3x_bot.groupfinder import events, legacy, provision, start_groupfinder, sync
from n3x_bot.groupfinder.zones import ACTIVE
from n3x_bot.seed import seed_defaults
from n3x_bot.storage.json_repo import JsonRepository

UTC = timezone.utc
BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)          # 12:00 Berlin
BOT_ID, MEMBER_ID = 5555, 42
LEGACY_CHANNEL = 1531288209730830447
ADMIN_ROLE = 900


def _settings() -> Settings:
    return Settings(discord_token="tok", target_role_id=1, welcome_channel_id=2,
                    reminder_channel_id=999, julez_id=424242,
                    admin_role_id=ADMIN_ROLE, lfg_channel_id=LEGACY_CHANNEL,
                    _env_file=None, _env_prefix="NONEXISTENT_")


async def _repo() -> JsonRepository:
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    repo = JsonRepository(path)
    await repo.connect()
    await seed_defaults(repo)
    return repo


_ids = itertools.count(120_000)


def _not_found():
    return discord.NotFound(MagicMock(status=404, reason="Not Found"), "gone")


class _Snowflake(SimpleNamespace):
    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        return isinstance(other, _Snowflake) and other.id == self.id


class FakeChannel:
    def __init__(self, guild, cid=None, name="chan"):
        self.id = cid or next(_ids)
        self.name = name
        self.guild = guild
        self.messages = {}
        self.send = AsyncMock(side_effect=self._send)
        self.fetch_message = AsyncMock(side_effect=self._fetch)
        self.edit = AsyncMock()

    def add(self, author_id, content="", mid=None):
        msg = SimpleNamespace(id=mid or next(_ids), author=SimpleNamespace(id=author_id),
                              content=content, embed=None, view=None)
        msg.delete = AsyncMock(side_effect=lambda: self.messages.pop(msg.id, None))

        async def _edit(**k):
            msg.content, msg.embed, msg.view = k.get("content"), k.get("embed"), k.get("view")
        msg.edit = AsyncMock(side_effect=_edit)
        self.messages[msg.id] = msg
        return msg

    async def _send(self, content=None, embed=None, view=None, allowed_mentions=None, **kw):
        msg = self.add(BOT_ID, content)
        msg.embed, msg.view, msg.allowed_mentions = embed, view, allowed_mentions
        return msg

    async def _fetch(self, mid):
        if mid not in self.messages:
            raise _not_found()
        return self.messages[mid]

    async def history(self, limit=None):
        for msg in list(self.messages.values()):
            yield msg


class FakeGuild:
    def __init__(self):
        self.id = 1
        self.channels = {}
        self.roles = {}
        self.default_role = _Snowflake(id=1)
        self.me = _Snowflake(id=BOT_ID)
        self.roles[ADMIN_ROLE] = _Snowflake(id=ADMIN_ROLE)
        self.create_category = AsyncMock(side_effect=self._category)
        self.create_role = AsyncMock(side_effect=self._role)
        self.create_text_channel = AsyncMock(side_effect=self._text)

    async def _category(self, name, reason=None):
        ch = FakeChannel(self, name=name)
        self.channels[ch.id] = ch
        return ch

    async def _role(self, name, mentionable=False, reason=None):
        role = _Snowflake(id=next(_ids), name=name)
        self.roles[role.id] = role
        return role

    async def _text(self, name, category=None, overwrites=None, topic=None, reason=None):
        ch = FakeChannel(self, name=name)
        self.channels[ch.id] = ch
        return ch

    def get_channel(self, cid):
        return self.channels.get(cid)

    def get_role(self, rid):
        return self.roles.get(rid)


def _world():
    guild = FakeGuild()
    old = FakeChannel(guild, cid=LEGACY_CHANNEL, name="group-finder")
    guild.channels[old.id] = old
    bot = MagicMock()
    bot.get_channel = guild.get_channel
    bot.user = SimpleNamespace(id=BOT_ID)
    bot.runtime_config = SimpleNamespace(lfg_channel_id=LEGACY_CHANNEL)
    bot.add_view = MagicMock()
    return bot, guild, old


async def _legacy(repo, old, *, status="OPEN", times=("19:00", "20:00", "21:00"),
                  voters=None, confirmed=None, roster=(), players=(4, 8),
                  day="2026-09-26"):
    lid = await repo.create_lfg(
        creator_id=7, title="Satura Gruppen Gate", event_date=day,
        min_players=players[0], max_players=players[1], start_times=list(times),
        status="OPEN", channel_id=old.id, created_at=NOW,
        cleanup_at=NOW + timedelta(hours=12))
    post = old.add(BOT_ID, "legacy lfg")
    await repo.set_lfg_message(lid, post.id, old.id)
    for uid, picked in (voters or {}).items():
        await repo.set_lfg_availability(lid, uid, list(picked))
    if confirmed:
        await repo.confirm_lfg(lid, start_time=confirmed, event_at=NOW,
                               cleanup_at=NOW + timedelta(hours=12),
                               status="CONFIRMED", participants=list(roster),
                               joined_at=NOW, expect_status="OPEN")
    if status not in ("OPEN", "CONFIRMED"):
        await repo.set_lfg_status(lid, status)
    return lid, post


async def _zone(repo, guild, name="Europe/Berlin"):
    ch = FakeChannel(guild, name=name)
    guild.channels[ch.id] = ch
    await repo.gf_save_zone(name, role_id=next(_ids), channel_id=ch.id,
                            status=ACTIVE, now=NOW)
    return ch


async def _migrated(repo):
    return [e for e in await repo.gf_events_with_status(
        ["VOTING", "SCHEDULED", "CLOSED", "STARTED", "EXPIRED", "NO_TIME_FOUND",
         "CANCELLED"]) if e["legacy_lfg_id"]]


# ── time conversion ────────────────────────────────────────────────────────

async def test_legacy_times_are_read_as_berlin_wall_time():
    assert legacy.legacy_slot("2026-09-26", "20:00", BERLIN) == datetime(
        2026, 9, 26, 18, 0, tzinfo=UTC)                  # CEST, UTC+2
    assert legacy.legacy_slot("2026-10-26", "20:00", BERLIN) == datetime(
        2026, 10, 26, 19, 0, tzinfo=UTC)                 # CET, UTC+1


async def test_ambiguous_legacy_time_takes_the_first_occurrence():
    # 25.10.2026 02:30 exists twice in Berlin; nobody left to ask
    assert legacy.legacy_slot("2026-10-25", "02:30", BERLIN) == datetime(
        2026, 10, 25, 0, 30, tzinfo=UTC)


# ── migration ──────────────────────────────────────────────────────────────

async def test_migration_waits_until_a_zone_exists():
    repo = await _repo()
    bot, guild, old = _world()
    lid, post = await _legacy(repo, old)
    assert await legacy.migrate_legacy_lfgs(bot, repo, _settings(), NOW) == []
    assert (await repo.get_lfg(lid))["status"] == "OPEN"
    assert post.id in old.messages                      # still visible
    await repo.close()


async def test_open_lfg_becomes_a_voting_event():
    repo = await _repo()
    bot, guild, old = _world()
    await _zone(repo, guild)
    lid, post = await _legacy(repo, old, voters={1: ("19:00", "20:00"), 2: ("20:00",)})
    [eid] = await legacy.migrate_legacy_lfgs(bot, repo, _settings(), NOW)
    e = await repo.gf_get_event(eid)
    assert e["status"] == events.VOTING and e["legacy_lfg_id"] == lid
    assert e["title"] == "Satura Gruppen Gate"
    assert (e["min_players"], e["max_players"], e["creator_id"]) == (4, 8, 7)
    assert e["slots"] == [datetime(2026, 9, 26, h, 0, tzinfo=UTC) for h in (17, 18, 19)]
    assert e["cleanup_at"] == datetime(2026, 9, 26, 20, 0, tzinfo=UTC)
    votes = await repo.gf_get_votes(eid)
    assert sorted((v["discord_id"], v["starts_at"].hour, v["zone"]) for v in votes) == [
        (1, 17, "Europe/Berlin"), (1, 18, "Europe/Berlin"), (2, 18, "Europe/Berlin")]
    assert (await repo.get_lfg(lid))["status"] == legacy.MIGRATED
    assert post.id not in old.messages                  # old post removed
    await repo.close()


async def test_confirmed_lfg_becomes_scheduled_without_a_retroactive_ping():
    repo = await _repo()
    bot, guild, old = _world()
    zone_ch = await _zone(repo, guild)
    await _legacy(repo, old, voters={1: ("20:00",), 2: ("20:00",)},
                  confirmed="20:00", roster=(1, 2, 3, 4))
    [eid] = await legacy.migrate_legacy_lfgs(bot, repo, _settings(), NOW)
    e = await repo.gf_get_event(eid)
    assert e["status"] == events.SCHEDULED
    assert e["scheduled_at"] == datetime(2026, 9, 26, 18, 0, tzinfo=UTC)
    assert e["cleanup_at"] == datetime(2026, 9, 26, 19, 0, tzinfo=UTC)
    assert e["time_found_notified_at"] == NOW           # counts as announced
    assert [p["discord_id"] for p in await repo.gf_get_participants(eid)] == [1, 2, 3, 4]
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    msg = next(iter(zone_ch.messages.values()))
    assert "<@" not in (msg.content or "")              # nobody pinged
    assert msg.allowed_mentions.users is False
    await repo.close()


async def test_full_lfg_becomes_closed():
    repo = await _repo()
    bot, guild, old = _world()
    await _zone(repo, guild)
    await _legacy(repo, old, status="FULL", confirmed="20:00", roster=(1, 2, 3, 4),
                  players=(4, 4))
    [eid] = await legacy.migrate_legacy_lfgs(bot, repo, _settings(), NOW)
    assert (await repo.gf_get_event(eid))["status"] == events.CLOSED
    await repo.close()


async def test_finished_legacy_lfgs_are_not_imported():
    repo = await _repo()
    bot, guild, old = _world()
    await _zone(repo, guild)
    for status in ("EXPIRED", "CANCELLED"):
        await _legacy(repo, old, status=status)
    assert await legacy.migrate_legacy_lfgs(bot, repo, _settings(), NOW) == []
    assert await _migrated(repo) == []
    await repo.close()


async def test_migration_is_idempotent():
    repo = await _repo()
    bot, guild, old = _world()
    await _zone(repo, guild)
    await _legacy(repo, old)
    await _legacy(repo, old, confirmed="20:00", roster=(1,), players=(1, 8))
    assert len(await legacy.migrate_legacy_lfgs(bot, repo, _settings(), NOW)) == 2
    assert await legacy.migrate_legacy_lfgs(bot, repo, _settings(), NOW) == []
    assert len(await _migrated(repo)) == 2
    await repo.close()


async def test_crash_between_import_and_marking_does_not_duplicate():
    repo = await _repo()
    bot, guild, old = _world()
    await _zone(repo, guild)
    lid, post = await _legacy(repo, old)
    await legacy._import(repo, await repo.get_lfg(lid), "Europe/Berlin", NOW)
    # ... and the bot died before marking the row MIGRATED
    assert await legacy.migrate_legacy_lfgs(bot, repo, _settings(), NOW) == []
    assert len(await _migrated(repo)) == 1
    assert (await repo.get_lfg(lid))["status"] == legacy.MIGRATED
    assert post.id not in old.messages
    await repo.close()


async def test_migrated_event_is_rendered_in_every_zone():
    repo = await _repo()
    bot, guild, old = _world()
    berlin = await _zone(repo, guild, "Europe/Berlin")
    karachi = await _zone(repo, guild, "Asia/Karachi")
    await _legacy(repo, old)
    [eid] = await legacy.migrate_legacy_lfgs(bot, repo, _settings(), NOW)
    await sync.sync_event(bot, repo, _settings(), eid, NOW)
    assert "19:00" in next(iter(berlin.messages.values())).embed.description
    assert "22:00" in next(iter(karachi.messages.values())).embed.description
    await repo.close()


# ── hub adoption ───────────────────────────────────────────────────────────

async def test_hub_adoption_moves_the_old_channel_and_cleans_bot_posts():
    repo = await _repo()
    bot, guild, old = _world()
    guide = old.add(BOT_ID, "alte deutsche Anleitung")
    member_post = old.add(MEMBER_ID, "würde für heute noch jemand?")
    finished = old.add(BOT_ID, "expired lfg")
    lid, running_post = await _legacy(repo, old)

    assert await legacy.adopt_legacy_hub(bot, repo, _settings()) is True

    kwargs = old.edit.call_args.kwargs
    category = guild.get_channel(int(await repo.gf_get_setting(provision.CATEGORY_KEY)))
    assert kwargs["category"] is category and category.name == "Group Finder"
    everyone = kwargs["overwrites"][guild.default_role]
    assert everyone.view_channel is True and everyone.send_messages is False
    assert guide.id not in old.messages and finished.id not in old.messages
    assert member_post.id in old.messages               # members' messages stay
    assert running_post.id in old.messages              # until it is migrated
    assert await repo.gf_get_setting(provision.HUB_CHANNEL_KEY) == str(LEGACY_CHANNEL)
    await repo.close()


async def test_hub_adoption_happens_only_once():
    repo = await _repo()
    bot, guild, old = _world()
    await legacy.adopt_legacy_hub(bot, repo, _settings())
    old.edit.reset_mock()
    assert await legacy.adopt_legacy_hub(bot, repo, _settings()) is False
    old.edit.assert_not_awaited()
    await repo.close()


async def test_no_legacy_channel_no_adoption():
    repo = await _repo()
    bot, guild, old = _world()
    guild.channels.pop(LEGACY_CHANNEL)
    assert await legacy.adopt_legacy_hub(bot, repo, _settings()) is False
    assert await repo.gf_get_setting(provision.HUB_CHANNEL_KEY) is None
    await repo.close()


# ── the whole cutover ──────────────────────────────────────────────────────

async def test_cutover_from_deploy_to_first_setup():
    repo = await _repo()
    bot, guild, old = _world()
    guide = old.add(BOT_ID, "alte deutsche Anleitung")
    lid, running_post = await _legacy(repo, old, voters={1: ("20:00",)})

    # 1. deploy: the bot starts on the new code
    await start_groupfinder(bot, repo, _settings())
    bot._gf_lifecycle_loop.cancel()
    assert await repo.gf_get_setting(provision.HUB_CHANNEL_KEY) == str(LEGACY_CHANNEL)
    assert guide.id not in old.messages
    assert running_post.id in old.messages              # nothing to show it in yet
    assert (await repo.get_lfg(lid))["status"] == "OPEN"
    hub_msg = [m for m in old.messages.values() if m.embed is not None]
    assert len(hub_msg) == 1 and "Group Finder" in hub_msg[0].embed.title

    # 2. the admin adds the first zone
    select = gf_admin.SetupSelect(repo, _settings(), ["Europe/Berlin"])
    select._values = ["Europe/Berlin"]
    it = MagicMock()
    it.guild = guild
    it.client = bot
    it.user = SimpleNamespace(id=MEMBER_ID, roles=[guild.roles[ADMIN_ROLE]])
    it.response = MagicMock()
    it.response.defer = AsyncMock()
    it.followup = MagicMock()
    it.followup.send = AsyncMock()
    it.edit_original_response = AsyncMock()
    await select.callback(it)

    # 3. the running LFG moved over
    assert (await repo.get_lfg(lid))["status"] == legacy.MIGRATED
    assert running_post.id not in old.messages
    zone = await repo.gf_get_zone("Europe/Berlin")
    zone_channel = guild.get_channel(zone["channel_id"])
    [event_msg] = zone_channel.messages.values()
    assert event_msg.embed.title == "🔎 Satura Gruppen Gate"
    assert "<@1>" in event_msg.embed.description        # the voter carried over
    hub_view = hub_msg[0].view
    assert "Europe/Berlin" in {o.value for c in hub_view.children for o in c.options}
    assert "`Europe/Berlin`" in hub_msg[0].embed.description   # listed as a channel
    await repo.close()
