"""Migration-fidelity contract: the new ``achievements`` table MUST be included
in ``export_all()`` / ``import_all()`` / ``clear()``.

Parametrized across every backend via the shared ``repo`` / ``make_repo``
fixtures, mirroring ``tests/storage/test_activity_export_import_contract.py``.

Kept separate so seeding uses ONLY the new achievements methods: that keeps
the existing contract green and isolates this feature's RED to the missing
achievements surface (``unlock_achievement`` raises AttributeError first).

As with the existing round-trip tests, the source snapshot is captured BEFORE
requesting a fresh ``make_repo`` (the postgres factory shares one physical DB).
"""

import json


async def _seed_achievements(repo):
    await repo.unlock_achievement(1001, "msg_1000")
    await repo.unlock_achievement(1001, "voice_3600")
    await repo.unlock_achievement(1002, "streak_7")


async def test_export_all_includes_achievements_and_is_json_serializable(repo):
    await _seed_achievements(repo)
    snapshot = await repo.export_all()
    json.dumps(snapshot)  # must cross wire/disk unchanged


async def test_round_trip_preserves_achievements(repo, make_repo):
    await _seed_achievements(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    assert await dest.get_user_achievements(1001) == {"msg_1000", "voice_3600"}
    assert await dest.get_user_achievements(1002) == {"streak_7"}


async def test_snapshot_is_stable_after_achievements_round_trip(repo, make_repo):
    await _seed_achievements(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    assert await dest.export_all() == snapshot


async def test_clear_wipes_achievements(repo):
    await _seed_achievements(repo)
    await repo.clear()
    assert await repo.get_user_achievements(1001) == set()
    assert await repo.list_achievement_holders() == {}
