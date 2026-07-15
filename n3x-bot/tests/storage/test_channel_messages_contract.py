"""Contract tests for the channel-message repository surface.

Parametrized across every registered backend via the shared ``repo`` /
``make_repo`` fixtures (json, sqlite, and postgres when TEST_POSTGRES_URL is
set), mirroring ``tests/storage/test_base_timers_repository_contract.py`` and
``tests/storage/test_kodex_repository_contract.py``.

Fixes the gate-embed persistence bug: the live gate-stats embed message id was
tracked in-memory on ``bot._gate_embed_msg_id`` and lost on every restart, so
the bot re-posted a NEW embed after each restart instead of editing the
existing one. The existing ``stat_last_post`` table can't hold it because it is
FK-bound to a real ``stats`` row and "gate" isn't one (KeyError). This adds a
NON-FK-bound keyed message store.

New interface (to be implemented downstream on StatsRepository + json_repo +
sql_repo + schema — new table
``channel_messages(key TEXT/String PK, message_id BIGINT, channel_id BIGINT)``):

    async def set_channel_message(key: str, message_id: int, channel_id: int) -> None  # upsert
    async def get_channel_message(key: str) -> tuple[int, int] | None  # (message_id, channel_id) or None

Plus migration fidelity: the table MUST be included in ``export_all()`` /
``import_all()`` / ``clear()``.

RED until then: calling these raises AttributeError on the repo.
"""

import json


# ── set / get roundtrip ─────────────────────────────────────────────────────

async def test_get_channel_message_unknown_key_returns_none(repo):
    assert await repo.get_channel_message("gate_stats") is None


async def test_set_channel_message_roundtrips(repo):
    await repo.set_channel_message("gate_stats", 42, 555)
    assert await repo.get_channel_message("gate_stats") == (42, 555)


async def test_get_channel_message_returns_int_tuple(repo):
    await repo.set_channel_message("gate_stats", 42, 555)
    msg_id, chan_id = await repo.get_channel_message("gate_stats")
    assert isinstance(msg_id, int)
    assert isinstance(chan_id, int)


# ── upsert semantics: re-setting a key overwrites its stored ids ─────────────

async def test_set_channel_message_upserts_same_key(repo):
    await repo.set_channel_message("gate_stats", 42, 555)
    await repo.set_channel_message("gate_stats", 99, 777)
    assert await repo.get_channel_message("gate_stats") == (99, 777)


# ── keys are independent ─────────────────────────────────────────────────────

async def test_multiple_keys_are_independent(repo):
    await repo.set_channel_message("gate_stats", 42, 555)
    await repo.set_channel_message("timer_overview", 43, 556)
    assert await repo.get_channel_message("gate_stats") == (42, 555)
    assert await repo.get_channel_message("timer_overview") == (43, 556)


async def test_set_one_key_leaves_other_keys_unset(repo):
    await repo.set_channel_message("gate_stats", 42, 555)
    assert await repo.get_channel_message("timer_overview") is None


# ── large (BIGINT) discord snowflake ids survive the roundtrip ──────────────

async def test_channel_message_preserves_large_snowflake_ids(repo):
    # Real Discord snowflakes exceed 2**53 (JS-double / 32-bit range); the
    # column must be BIGINT. Values stay within signed int64 (max
    # 9223372036854775807) — real snowflakes are ~1.3e18 and won't approach
    # 2**63 for decades — matching the codebase's BigInteger snowflake columns.
    big_msg = 1234567890123456789
    big_chan = 9223372036854775807
    await repo.set_channel_message("gate_stats", big_msg, big_chan)
    assert await repo.get_channel_message("gate_stats") == (big_msg, big_chan)


# ── migration fidelity: export / import / clear ─────────────────────────────

async def _seed_channel_messages(repo):
    await repo.set_channel_message("gate_stats", 42, 555)
    await repo.set_channel_message("timer_overview", 43, 556)


async def test_export_all_includes_channel_messages_and_is_json_serializable(repo):
    await _seed_channel_messages(repo)
    snapshot = await repo.export_all()
    json.dumps(snapshot)  # must cross wire/disk unchanged


async def test_round_trip_preserves_channel_messages(repo, make_repo):
    await _seed_channel_messages(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    assert await dest.get_channel_message("gate_stats") == (42, 555)
    assert await dest.get_channel_message("timer_overview") == (43, 556)


async def test_snapshot_is_stable_after_channel_message_round_trip(repo, make_repo):
    await _seed_channel_messages(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    assert await dest.export_all() == snapshot


async def test_clear_wipes_channel_messages(repo):
    await _seed_channel_messages(repo)
    await repo.clear()
    assert await repo.get_channel_message("gate_stats") is None
    assert await repo.get_channel_message("timer_overview") is None
