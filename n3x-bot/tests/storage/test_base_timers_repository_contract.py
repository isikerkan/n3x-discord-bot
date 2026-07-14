"""Contract tests for the Base-timer repository surface (v3 port #6).

Parametrized across every registered backend via the shared ``repo`` / ``make_repo``
fixtures (json, sqlite, and postgres when TEST_POSTGRES_URL is set), mirroring
``tests/storage/test_activity_repository_contract.py`` and
``tests/storage/test_kodex_repository_contract.py``.

Fixes v3 bug B12: base timers lived in an in-memory dict (``base_timers = {}``)
and were lost on every restart. They MUST now be persisted in a new table
``base_timers(map_name PK, end_time DateTime(timezone=True))``.

New interface (to be implemented downstream on StatsRepository + json_repo +
sql_repo + schema):

    async def set_base_timer(map_name: str, end_time: datetime) -> None   # upsert
    async def remove_base_timer(map_name: str) -> bool                    # existed?
    async def list_base_timers() -> dict[str, datetime]                   # all rows
    async def purge_expired_base_timers(now: datetime) -> list[str]       # <= now

Plus migration fidelity: the table MUST be included in ``export_all()`` /
``import_all()`` / ``clear()``.

The KEY cross-backend guarantee (fixes v3 bug B6: naive datetimes): ``end_time``
round-trips **tz-aware** through every backend and preserves the same instant.
Aware-datetime ``==`` compares instants regardless of tz, so we assert both the
instant equality and ``.tzinfo is not None`` after the roundtrip.

RED until then: calling these raises AttributeError on the repo.
"""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def _berlin(y=2026, mo=7, d=14, h=12, mi=0, s=0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=ZoneInfo("Europe/Berlin"))


# ── set / list roundtrip ────────────────────────────────────────────────────

async def test_list_base_timers_empty_by_default(repo):
    assert await repo.list_base_timers() == {}


async def test_set_base_timer_is_listed_back(repo):
    end = _berlin()
    await repo.set_base_timer("4-1", end)
    timers = await repo.list_base_timers()
    assert set(timers) == {"4-1"}
    assert timers["4-1"] == end


async def test_multiple_base_timers_are_listed_independently(repo):
    a = _berlin(h=12)
    b = _berlin(h=13)
    await repo.set_base_timer("4-1", a)
    await repo.set_base_timer("1-5", b)
    timers = await repo.list_base_timers()
    assert timers["4-1"] == a
    assert timers["1-5"] == b


# ── the key cross-backend guarantee: tz-aware end_time preserved (B6) ────────

async def test_end_time_round_trips_tz_aware(repo):
    end = _berlin(h=9)  # Berlin +02:00 in July
    await repo.set_base_timer("2-6", end)
    read = (await repo.list_base_timers())["2-6"]
    # Same instant regardless of the tz the backend chose to store it in...
    assert read == end
    # ...and never a naive datetime (the v3 B6 bug).
    assert read.tzinfo is not None


async def test_end_time_preserves_instant_for_utc_input(repo):
    end = datetime(2026, 7, 14, 10, 0, 0, tzinfo=timezone.utc)
    await repo.set_base_timer("3-7", end)
    read = (await repo.list_base_timers())["3-7"]
    assert read == end
    assert read.tzinfo is not None


# ── upsert semantics: re-setting a map overwrites its end_time ──────────────

async def test_set_base_timer_upserts_same_map(repo):
    await repo.set_base_timer("4-1", _berlin(h=12))
    await repo.set_base_timer("4-1", _berlin(h=15))
    timers = await repo.list_base_timers()
    assert set(timers) == {"4-1"}          # no duplicate row
    assert timers["4-1"] == _berlin(h=15)  # newest end_time wins


# ── remove ──────────────────────────────────────────────────────────────────

async def test_remove_base_timer_returns_true_when_it_existed(repo):
    await repo.set_base_timer("4-1", _berlin())
    assert await repo.remove_base_timer("4-1") is True


async def test_remove_base_timer_actually_deletes_the_row(repo):
    await repo.set_base_timer("4-1", _berlin())
    await repo.remove_base_timer("4-1")
    assert await repo.list_base_timers() == {}


async def test_remove_base_timer_returns_false_when_absent(repo):
    assert await repo.remove_base_timer("4-1") is False


async def test_remove_base_timer_second_call_returns_false(repo):
    await repo.set_base_timer("4-1", _berlin())
    assert await repo.remove_base_timer("4-1") is True
    assert await repo.remove_base_timer("4-1") is False


async def test_remove_base_timer_leaves_other_maps_untouched(repo):
    await repo.set_base_timer("4-1", _berlin(h=12))
    await repo.set_base_timer("1-5", _berlin(h=13))
    await repo.remove_base_timer("4-1")
    assert set(await repo.list_base_timers()) == {"1-5"}


# ── purge_expired: deletes rows with end_time <= now, returns removed names ──

async def test_purge_expired_removes_only_past_timers(repo):
    now = _berlin(h=12)
    await repo.set_base_timer("4-1", now - timedelta(minutes=5))   # expired
    await repo.set_base_timer("1-5", now + timedelta(minutes=10))  # future
    await repo.purge_expired_base_timers(now)
    assert set(await repo.list_base_timers()) == {"1-5"}


async def test_purge_expired_returns_removed_map_names(repo):
    now = _berlin(h=12)
    await repo.set_base_timer("4-1", now - timedelta(minutes=5))
    await repo.set_base_timer("2-7", now - timedelta(minutes=1))
    await repo.set_base_timer("1-5", now + timedelta(minutes=10))
    removed = await repo.purge_expired_base_timers(now)
    assert set(removed) == {"4-1", "2-7"}


async def test_purge_expired_treats_exactly_now_as_expired(repo):
    now = _berlin(h=12)
    await repo.set_base_timer("4-1", now)  # end_time == now -> expired (<=)
    removed = await repo.purge_expired_base_timers(now)
    assert removed == ["4-1"]
    assert await repo.list_base_timers() == {}


async def test_purge_expired_returns_empty_when_nothing_expired(repo):
    now = _berlin(h=12)
    await repo.set_base_timer("1-5", now + timedelta(minutes=10))
    assert await repo.purge_expired_base_timers(now) == []
    assert set(await repo.list_base_timers()) == {"1-5"}


# ── migration fidelity: export / import / clear ─────────────────────────────

async def _seed_timers(repo):
    await repo.set_base_timer("4-1", _berlin(h=12))
    await repo.set_base_timer("1-5", _berlin(h=13))


async def test_export_all_includes_base_timers_and_is_json_serializable(repo):
    await _seed_timers(repo)
    snapshot = await repo.export_all()
    json.dumps(snapshot)  # must cross wire/disk unchanged (datetimes as ISO str)


async def test_round_trip_preserves_base_timers(repo, make_repo):
    await _seed_timers(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    timers = await dest.list_base_timers()
    assert timers["4-1"] == _berlin(h=12)
    assert timers["1-5"] == _berlin(h=13)


async def test_round_trip_keeps_end_time_tz_aware(repo, make_repo):
    await _seed_timers(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    assert (await dest.list_base_timers())["4-1"].tzinfo is not None


async def test_snapshot_is_stable_after_base_timer_round_trip(repo, make_repo):
    await _seed_timers(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    assert await dest.export_all() == snapshot


async def test_clear_wipes_base_timers(repo):
    await _seed_timers(repo)
    await repo.clear()
    assert await repo.list_base_timers() == {}
