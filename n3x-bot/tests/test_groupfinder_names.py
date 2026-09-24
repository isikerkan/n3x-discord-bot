"""Channels are named after their clock's current UTC offset and renamed at
every DST switch; members never move."""
import asyncio
import itertools
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from n3x_bot.groupfinder import lifecycle, provision, zones
from tests.test_groupfinder_zones import _repo, _settings

UTC = timezone.utc
SUMMER = datetime(2026, 10, 1, 12, tzinfo=UTC)
WINTER = datetime(2026, 11, 1, 12, tzinfo=UTC)
_ids = itertools.count(300_000)


def _rows(*zone_ids):
    return [{"zone": z, "created_at": datetime(2026, 9, 1 + i, tzinfo=UTC)}
            for i, z in enumerate(zone_ids)]


# ── names (pure) ───────────────────────────────────────────────────────────

async def test_name_shows_the_offset_right_now():
    rows = _rows("Europe/Zurich")
    assert zones.zone_names(rows, SUMMER)["Europe/Zurich"] == ("gf-utc+2", "UTC+2")
    assert zones.zone_names(rows, WINTER)["Europe/Zurich"] == ("gf-utc+1", "UTC+1")


async def test_negative_zero_and_half_hour_offsets():
    names = zones.zone_names(_rows("America/New_York", "Europe/London",
                                   "Asia/Kolkata", "Asia/Kathmandu"), WINTER)
    assert names["America/New_York"] == ("gf-utc-5", "UTC-5")
    assert names["Europe/London"] == ("gf-utc+0", "UTC+0")
    assert names["Asia/Kolkata"] == ("gf-utc+5h30", "UTC+5:30")
    assert names["Asia/Kathmandu"] == ("gf-utc+5h45", "UTC+5:45")


async def test_shared_offset_gets_a_city_and_the_older_group_keeps_the_plain_name():
    # Zurich (older) and Lagos are both UTC+1 in winter
    names = zones.zone_names(_rows("Europe/Zurich", "Africa/Lagos"), WINTER)
    assert names["Europe/Zurich"] == ("gf-utc+1", "UTC+1")
    assert names["Africa/Lagos"] == ("gf-utc+1-lagos", "UTC+1 · Lagos")
    # in summer they differ again: no suffix needed
    names = zones.zone_names(_rows("Europe/Zurich", "Africa/Lagos"), SUMMER)
    assert names["Africa/Lagos"] == ("gf-utc+1", "UTC+1")


async def test_words_spelling():
    names = zones.zone_names(_rows("Europe/Zurich", "America/New_York"), SUMMER,
                             words=True)
    assert names["Europe/Zurich"][0] == "gf-utc-plus-2"
    assert names["America/New_York"][0] == "gf-utc-minus-4"


# ── renaming live channels ─────────────────────────────────────────────────

def _world(strip_plus=False):
    guild = SimpleNamespace(roles={})
    guild.get_role = guild.roles.get
    channels = {}

    def _channel(name):
        ch = SimpleNamespace(id=next(_ids), name=name, guild=guild)

        async def _edit(name=None, reason=None):
            ch.name = name.replace("+", "") if strip_plus else name
            return ch
        ch.edit = AsyncMock(side_effect=_edit)
        channels[ch.id] = ch
        return ch

    def _role(name):
        role = SimpleNamespace(id=next(_ids), name=name)

        async def _edit(name=None, reason=None):
            role.name = name
        role.edit = AsyncMock(side_effect=_edit)
        guild.roles[role.id] = role
        return role
    bot = MagicMock()
    bot.get_channel = channels.get
    return bot, _channel, _role


async def _zone(repo, channel, role, zone, created):
    await repo.gf_save_zone(zone, role_id=role.id, channel_id=channel.id,
                            status=zones.ACTIVE, now=created)


async def test_live_zurich_channel_is_renamed_to_its_offset():
    repo = await _repo()
    bot, channel, role = _world()
    ch, r = channel("gf-europe-zurich"), role("TZ Europe/Zurich")
    await _zone(repo, ch, r, "Europe/Zurich", SUMMER)
    assert await provision.sync_zone_names(bot, repo, SUMMER) == 1
    assert (ch.name, r.name) == ("gf-utc+2", "UTC+2")
    await repo.close()


async def test_renamed_at_the_switch_and_not_otherwise():
    repo = await _repo()
    bot, channel, role = _world()
    ch, r = channel("gf-utc+2"), role("UTC+2")
    await _zone(repo, ch, r, "Europe/Zurich", SUMMER)
    assert await provision.sync_zone_names(bot, repo, SUMMER) == 0
    ch.edit.assert_not_awaited()
    r.edit.assert_not_awaited()
    assert await provision.sync_zone_names(bot, repo, WINTER) == 1
    assert (ch.name, r.name) == ("gf-utc+1", "UTC+1")
    await repo.close()


async def test_exact_switch_moment():
    # clocks go back 25.10.2026 at 01:00 UTC
    repo = await _repo()
    bot, channel, role = _world()
    ch, r = channel("gf-utc+2"), role("UTC+2")
    await _zone(repo, ch, r, "Europe/Zurich", SUMMER)
    await provision.sync_zone_names(bot, repo, datetime(2026, 10, 25, 0, 59, tzinfo=UTC))
    assert ch.name == "gf-utc+2"
    await provision.sync_zone_names(bot, repo, datetime(2026, 10, 25, 1, 0, tzinfo=UTC))
    assert ch.name == "gf-utc+1"
    await repo.close()


async def test_stripped_plus_switches_to_words_for_good():
    repo = await _repo()
    bot, channel, role = _world(strip_plus=True)
    ch, r = channel("gf-europe-zurich"), role("TZ")
    await _zone(repo, ch, r, "Europe/Zurich", SUMMER)
    await provision.sync_zone_names(bot, repo, SUMMER)
    assert ch.name == "gf-utc-plus-2"                 # not the ambiguous gf-utc2
    assert await repo.gf_get_setting(provision.NAME_STYLE_KEY) == "words"
    ch.edit.reset_mock()
    await provision.sync_zone_names(bot, repo, SUMMER)
    ch.edit.assert_not_awaited()                      # settled, no ping-pong
    await repo.close()


async def test_suffix_disappears_when_the_offset_is_no_longer_shared():
    repo = await _repo()
    bot, channel, role = _world()
    zurich, lagos = channel("gf-utc+1"), channel("gf-utc+1-lagos")
    await _zone(repo, zurich, role("UTC+1"), "Europe/Zurich", SUMMER)
    await _zone(repo, lagos, role("UTC+1 · Lagos"), "Africa/Lagos", WINTER)
    await provision.sync_zone_names(bot, repo, WINTER)
    assert lagos.name == "gf-utc+1-lagos"
    await provision.sync_zone_names(bot, repo, datetime(2027, 4, 1, tzinfo=UTC))
    assert (zurich.name, lagos.name) == ("gf-utc+2", "gf-utc+1")
    await repo.close()


# ── never stalls the lifecycle tick ────────────────────────────────────────

async def test_tick_does_not_wait_for_a_slow_rename(monkeypatch):
    repo = await _repo()
    gate = asyncio.Event()
    calls = []

    async def _slow(bot, repo, now):                  # a rate-limited rename
        calls.append(now)
        await gate.wait()
    monkeypatch.setattr(provision, "sync_zone_names", _slow)
    bot = MagicMock()
    bot._gf_name_task = None
    await asyncio.wait_for(lifecycle.tick(bot, repo, _settings(), SUMMER), 1)
    await asyncio.wait_for(lifecycle.tick(bot, repo, _settings(), SUMMER), 1)
    gate.set()
    await bot._gf_name_task
    assert len(calls) == 1                             # never two at once
    await repo.close()
