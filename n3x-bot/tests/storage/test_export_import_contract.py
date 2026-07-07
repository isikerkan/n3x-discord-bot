"""Contract tests for bulk export/import on StatsRepository.

Parametrized across every registered backend via the shared ``repo`` fixture
(json, sqlite, and postgres when TEST_POSTGRES_URL is set).

Expected NEW interface (to be implemented downstream):
    async def export_all(self) -> dict   # JSON-serializable snapshot of ALL tables
    async def import_all(self, snapshot: dict) -> None   # populate an EMPTY repo

These are RED until then: calling ``repo.export_all()`` raises AttributeError.
"""

import json

from tests._seed import seed_everything


async def _round_trip(repo, make_repo):
    """Seed source, export a snapshot, import into a fresh empty repo."""
    await seed_everything(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    return dest, snapshot


async def test_export_all_returns_json_serializable_snapshot(repo):
    await seed_everything(repo)
    snapshot = await repo.export_all()
    # Must round-trip through JSON so it can cross the wire / disk unchanged.
    json.dumps(snapshot)


async def test_round_trip_preserves_active_and_archived_users(repo, make_repo):
    dest, _ = await _round_trip(repo, make_repo)
    by_id = {u.discord_id: u for u in await dest.list_users(include_archived=True)}
    assert set(by_id) == {1001, 1002, 1003}
    assert by_id[1003].archived_at is not None
    assert by_id[1001].archived_at is None


async def test_round_trip_preserves_messages_including_archived(repo, make_repo):
    dest, _ = await _round_trip(repo, make_repo)
    msgs = {m.name: m for m in await dest.list_messages(include_archived=True)}
    assert set(msgs) == {"greet", "old"}
    assert msgs["greet"].template == "hi {user} x{count}"
    assert msgs["old"].archived_at is not None


async def test_round_trip_preserves_stat_flags_and_message_link(repo, make_repo):
    dest, _ = await _round_trip(repo, make_repo)
    greet = next(m for m in await dest.list_messages(include_archived=True)
                 if m.name == "greet")
    assert (await dest.get_stat("tit")).message_id == greet.id
    assert (await dest.get_stat("smart")).targeted is True
    dead = next(s for s in await dest.list_stats(include_archived=True)
                if s.key == "dead")
    assert dead.archived_at is not None


async def test_round_trip_preserves_user_stats_for_multiple_users(repo, make_repo):
    dest, _ = await _round_trip(repo, make_repo)
    assert await dest.get_user_stats(1001) == {"tit": 2}
    assert await dest.get_user_stats(1002) == {"tit": 1}


async def test_round_trip_preserves_stat_totals(repo, make_repo):
    dest, _ = await _round_trip(repo, make_repo)
    assert await dest.get_total("tit") == 3


async def test_round_trip_preserves_target_stats_for_multiple_targets(repo, make_repo):
    dest, _ = await _round_trip(repo, make_repo)
    assert await dest.get_target_total(2001, "smart") == 2
    assert await dest.get_target_total(2002, "smart") == 1


async def test_round_trip_preserves_stat_last_post(repo, make_repo):
    dest, _ = await _round_trip(repo, make_repo)
    assert await dest.get_last_post("tit") == (55501, 66601)


async def test_round_trip_preserves_gate_entries_and_totals(repo, make_repo):
    dest, _ = await _round_trip(repo, make_repo)
    assert await dest.list_gate_costs("a") == [46000, 48000]
    totals = await dest.gate_totals()
    assert totals["a"]["count"] == 2
    assert totals["a"]["avg"] == 47000


async def test_snapshot_is_stable_after_round_trip(repo, make_repo):
    # Strongest fidelity check: re-exporting the imported repo yields the exact
    # same snapshot, incl. ids, timestamps, and gate metadata (user_id/username).
    dest, snapshot = await _round_trip(repo, make_repo)
    assert await dest.export_all() == snapshot
