"""Migration-fidelity contract: the new activity tables MUST be included in
``export_all()`` / ``import_all()`` / ``clear()``.

Parametrized across every backend via the shared ``repo`` / ``make_repo``
fixtures, mirroring ``tests/storage/test_export_import_contract.py``.

Kept separate from the existing export/import contract (and from
``tests/_seed.py``) so the seeding here uses ONLY the new activity methods:
that keeps the existing contract green and isolates this feature's RED to the
missing activity surface (``add_activity`` raises AttributeError first).

As with the existing round-trip tests, the source snapshot is captured BEFORE
requesting a fresh ``make_repo`` (the postgres factory shares one physical DB).
"""

import json


async def _seed_activity(repo):
    await repo.add_activity(1001, "messages", 5)
    await repo.add_activity(1001, "reactions", 2)
    await repo.add_activity(1001, "voice_seconds", 3600)
    await repo.add_activity(1002, "messages", 9)
    await repo.set_streak(1001, 3, "2026-07-13", 9)
    await repo.set_night(1001, 4, "2026-07-13")


async def test_export_all_includes_activity_and_is_json_serializable(repo):
    await _seed_activity(repo)
    snapshot = await repo.export_all()
    json.dumps(snapshot)  # must cross wire/disk unchanged


async def test_round_trip_preserves_activity_counters(repo, make_repo):
    await _seed_activity(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    assert await dest.get_activity(1001, "messages") == 5
    assert await dest.get_activity(1001, "reactions") == 2
    assert await dest.get_activity(1001, "voice_seconds") == 3600
    assert await dest.get_activity(1002, "messages") == 9


async def test_round_trip_preserves_streak(repo, make_repo):
    await _seed_activity(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    assert await dest.get_streak(1001) == {
        "current_streak": 3, "last_active_date": "2026-07-13", "max_streak": 9}


async def test_round_trip_preserves_night(repo, make_repo):
    await _seed_activity(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    assert await dest.get_night(1001) == {
        "night_count": 4, "last_night_date": "2026-07-13"}


async def test_snapshot_is_stable_after_activity_round_trip(repo, make_repo):
    await _seed_activity(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    assert await dest.export_all() == snapshot


async def test_clear_wipes_activity_data(repo):
    await _seed_activity(repo)
    await repo.clear()
    assert await repo.get_activity(1001, "messages") == 0
    assert await repo.get_activity(1001, "voice_seconds") == 0
    assert await repo.get_streak(1001) is None
    assert await repo.get_night(1001) is None
