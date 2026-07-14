"""Contract tests for the Kodex (rules-acceptance) repository surface.

Parametrized across every registered backend via the shared ``repo`` / ``make_repo``
fixtures (json, sqlite, and postgres when TEST_POSTGRES_URL is set), mirroring
``tests/storage/test_activity_repository_contract.py`` and
``tests/storage/test_achievements_repository_contract.py``.

New interface (to be implemented downstream on StatsRepository + json_repo +
sql_repo + schema — two new tables ``kodex_confirmations(discord_id PK)`` and
``kodex_messages(message_id PK, discord_id)``):

    async def confirm_kodex(discord_id: int) -> None            # idempotent
    async def has_confirmed_kodex(discord_id: int) -> bool
    async def list_kodex_confirmed() -> set[int]                # all confirmers
    async def save_kodex_message(message_id: int, discord_id: int) -> None
    async def get_kodex_message_user(message_id: int) -> int | None

Plus migration fidelity: both tables MUST be included in ``export_all()`` /
``import_all()`` / ``clear()``.

RED until then: calling these raises AttributeError on the repo.
"""

import json


# ── confirmations ───────────────────────────────────────────────────────────

async def test_has_confirmed_kodex_false_by_default(repo):
    assert await repo.has_confirmed_kodex(111) is False


async def test_confirm_kodex_marks_user_confirmed(repo):
    await repo.confirm_kodex(111)
    assert await repo.has_confirmed_kodex(111) is True


async def test_confirm_kodex_is_idempotent(repo):
    await repo.confirm_kodex(111)
    await repo.confirm_kodex(111)  # second confirm must not raise
    assert await repo.has_confirmed_kodex(111) is True


async def test_confirmations_are_isolated_per_user(repo):
    await repo.confirm_kodex(111)
    assert await repo.has_confirmed_kodex(222) is False


async def test_list_kodex_confirmed_empty_by_default(repo):
    assert await repo.list_kodex_confirmed() == set()


async def test_list_kodex_confirmed_returns_every_confirmer(repo):
    await repo.confirm_kodex(111)
    await repo.confirm_kodex(222)
    await repo.confirm_kodex(333)
    assert await repo.list_kodex_confirmed() == {111, 222, 333}


# ── DM-message → member mapping ─────────────────────────────────────────────

async def test_save_and_get_kodex_message_roundtrip(repo):
    await repo.save_kodex_message(9001, 111)
    assert await repo.get_kodex_message_user(9001) == 111


async def test_get_kodex_message_user_unknown_returns_none(repo):
    await repo.save_kodex_message(9001, 111)
    assert await repo.get_kodex_message_user(4040) is None


async def test_kodex_messages_map_each_message_to_its_own_member(repo):
    await repo.save_kodex_message(9001, 111)
    await repo.save_kodex_message(9002, 222)
    assert await repo.get_kodex_message_user(9001) == 111
    assert await repo.get_kodex_message_user(9002) == 222


# ── migration fidelity: export / import / clear ─────────────────────────────

async def _seed_kodex(repo):
    await repo.confirm_kodex(111)
    await repo.confirm_kodex(222)
    await repo.save_kodex_message(9001, 111)
    await repo.save_kodex_message(9002, 222)


async def test_export_all_includes_kodex_and_is_json_serializable(repo):
    await _seed_kodex(repo)
    snapshot = await repo.export_all()
    json.dumps(snapshot)  # must cross wire/disk unchanged


async def test_round_trip_preserves_kodex_confirmations(repo, make_repo):
    await _seed_kodex(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    assert await dest.list_kodex_confirmed() == {111, 222}
    assert await dest.has_confirmed_kodex(111) is True


async def test_round_trip_preserves_kodex_messages(repo, make_repo):
    await _seed_kodex(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    assert await dest.get_kodex_message_user(9001) == 111
    assert await dest.get_kodex_message_user(9002) == 222


async def test_snapshot_is_stable_after_kodex_round_trip(repo, make_repo):
    await _seed_kodex(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    assert await dest.export_all() == snapshot


async def test_clear_wipes_kodex_data(repo):
    await _seed_kodex(repo)
    await repo.clear()
    assert await repo.has_confirmed_kodex(111) is False
    assert await repo.list_kodex_confirmed() == set()
    assert await repo.get_kodex_message_user(9001) is None
