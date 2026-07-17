"""Contract tests for the ``gate_pending`` repository surface.

Persists the in-flight drop-confirm pending state that today lives ONLY in the
in-memory ``bot._pending_gate`` dict and is wiped on every restart/deploy — so a
user who clicks a d/e/z/k drop reaction after a restart hits a silent no-op and
the stat is never recorded. This adds a DB-backed keyed store so restarts don't
drop in-flight confirmations.

Parametrized across every registered backend via the shared ``repo`` /
``make_repo`` fixtures (json, sqlite, and postgres when TEST_POSTGRES_URL is
set), mirroring ``tests/storage/test_channel_messages_contract.py`` and
``tests/storage/test_achievement_defs_contract.py``.

New interface (to be implemented downstream on StatsRepository + json_repo +
sql_repo + schema — new keyed table ``gate_pending`` with columns
``message_id BigInteger PK, channel_id BigInteger, gate_type String,
cost BigInteger/Integer, user_id BigInteger, username String,
options Text/JSON``):

    async def set_gate_pending(message_id, *, channel_id, gate_type, cost,
                               user_id, username, options: dict) -> None  # upsert
    async def get_gate_pending(message_id) -> dict | None  # all fields, options
                                                           # as a dict; None absent
    async def delete_gate_pending(message_id) -> bool      # True if a row removed
    async def all_gate_pending() -> list[dict]             # every row

``options`` is the emoji_key -> item map (values ``str`` or ``None`` for the
"nothing" ❌ choice); it is stored as JSON text and round-trips back to a dict
whose keys are strings and whose values are ``str | None``.

Plus migration fidelity: the table MUST be included in ``export_all()`` /
``import_all()`` / ``clear()`` and in ``n3x_bot.migrate._DATA_TABLES``.

RED until then: calling these raises AttributeError on the repo.
"""

import json


ALL_KEYS = {"message_id", "channel_id", "gate_type", "cost", "user_id",
            "username", "options"}

NOTHING = "❌"


def _seed_one(repo, message_id=7001, **overrides):
    kwargs = dict(channel_id=777, gate_type="d", cost=250000, user_id=7,
                  username="Erkan", options={"111": "laser", NOTHING: None})
    kwargs.update(overrides)
    return repo.set_gate_pending(message_id, **kwargs)


# ── set / get roundtrip ─────────────────────────────────────────────────────

async def test_get_gate_pending_unknown_id_returns_none(repo):
    assert await repo.get_gate_pending(7001) is None


async def test_set_gate_pending_roundtrips_all_fields(repo):
    await _seed_one(repo, 7001)
    row = await repo.get_gate_pending(7001)
    assert row == {
        "message_id": 7001, "channel_id": 777, "gate_type": "d",
        "cost": 250000, "user_id": 7, "username": "Erkan",
        "options": {"111": "laser", NOTHING: None},
    }


async def test_get_gate_pending_returns_dict_with_all_keys(repo):
    await _seed_one(repo, 7001)
    row = await repo.get_gate_pending(7001)
    assert set(row) == ALL_KEYS


async def test_message_id_round_trips_as_int(repo):
    await _seed_one(repo, 7001)
    row = await repo.get_gate_pending(7001)
    assert isinstance(row["message_id"], int)
    assert row["message_id"] == 7001


async def test_cost_round_trips_as_int(repo):
    await _seed_one(repo, 7001, cost=46892)
    row = await repo.get_gate_pending(7001)
    assert isinstance(row["cost"], int)
    assert row["cost"] == 46892


# ── options: JSON text that round-trips back to a dict (incl. None values) ────

async def test_options_round_trips_as_a_dict(repo):
    await _seed_one(repo, 7001, options={"111": "laser", NOTHING: None})
    row = await repo.get_gate_pending(7001)
    assert isinstance(row["options"], dict)


async def test_options_preserves_none_value_for_nothing_choice(repo):
    await _seed_one(repo, 7001, options={"111": "laser", NOTHING: None})
    row = await repo.get_gate_pending(7001)
    assert row["options"][NOTHING] is None
    assert row["options"]["111"] == "laser"


async def test_options_keys_are_strings_after_round_trip(repo):
    await _seed_one(repo, 7001, options={"111": "laser", NOTHING: None})
    row = await repo.get_gate_pending(7001)
    assert all(isinstance(k, str) for k in row["options"])


async def test_options_supports_multiple_items_kappa_shape(repo):
    opts = {"900001": "hercules", "900002": "lf4u", NOTHING: None}
    await _seed_one(repo, 7001, gate_type="k", cost=500, options=opts)
    row = await repo.get_gate_pending(7001)
    assert row["options"] == opts


# ── upsert semantics: re-setting a message_id overwrites its stored row ──────

async def test_set_gate_pending_upserts_same_message_id(repo):
    await _seed_one(repo, 7001, cost=250000, username="Erkan")
    await _seed_one(repo, 7001, cost=999000, username="Umbenannt")
    row = await repo.get_gate_pending(7001)
    assert row["cost"] == 999000
    assert row["username"] == "Umbenannt"


# ── message_ids are independent ─────────────────────────────────────────────

async def test_multiple_message_ids_are_independent(repo):
    await _seed_one(repo, 7001, gate_type="d")
    await _seed_one(repo, 7002, gate_type="e", cost=46892)
    assert (await repo.get_gate_pending(7001))["gate_type"] == "d"
    assert (await repo.get_gate_pending(7002))["gate_type"] == "e"


async def test_set_one_message_id_leaves_others_unset(repo):
    await _seed_one(repo, 7001)
    assert await repo.get_gate_pending(7002) is None


# ── large (BIGINT) discord snowflake ids survive the roundtrip ──────────────

async def test_gate_pending_preserves_large_snowflake_ids(repo):
    big_msg = 1234567890123456789
    big_chan = 9223372036854775807
    big_user = 1111111111111111111
    await _seed_one(repo, big_msg, channel_id=big_chan, user_id=big_user)
    row = await repo.get_gate_pending(big_msg)
    assert row["message_id"] == big_msg
    assert row["channel_id"] == big_chan
    assert row["user_id"] == big_user


# ── delete: returns bool, then the id is gone ───────────────────────────────

async def test_delete_gate_pending_returns_true_when_present(repo):
    await _seed_one(repo, 7001)
    assert await repo.delete_gate_pending(7001) is True


async def test_delete_gate_pending_removes_the_id(repo):
    await _seed_one(repo, 7001)
    await repo.delete_gate_pending(7001)
    assert await repo.get_gate_pending(7001) is None


async def test_delete_gate_pending_returns_false_when_absent(repo):
    assert await repo.delete_gate_pending(7001) is False


# ── all_gate_pending: full list ─────────────────────────────────────────────

async def test_all_gate_pending_empty_by_default(repo):
    assert await repo.all_gate_pending() == []


async def test_all_gate_pending_returns_every_row(repo):
    await _seed_one(repo, 7001)
    await _seed_one(repo, 7002, gate_type="e", cost=46892)
    rows = await repo.all_gate_pending()
    assert {r["message_id"] for r in rows} == {7001, 7002}


async def test_all_gate_pending_rows_have_all_keys(repo):
    await _seed_one(repo, 7001)
    rows = await repo.all_gate_pending()
    assert all(set(r) == ALL_KEYS for r in rows)


async def test_all_gate_pending_rows_carry_options_as_dicts(repo):
    await _seed_one(repo, 7001, options={"111": "laser", NOTHING: None})
    rows = await repo.all_gate_pending()
    assert rows[0]["options"] == {"111": "laser", NOTHING: None}


# ── migration fidelity: export / import / clear ─────────────────────────────

async def _seed_pending(repo):
    await _seed_one(repo, 7001, gate_type="d", cost=250000,
                    options={"111": "laser", NOTHING: None})
    await _seed_one(repo, 7002, gate_type="k", cost=500, user_id=8,
                    username="Ali",
                    options={"900001": "hercules", "900002": "lf4u",
                             NOTHING: None})


async def test_export_all_includes_gate_pending_and_is_json_serializable(repo):
    await _seed_pending(repo)
    snapshot = await repo.export_all()
    assert "gate_pending" in snapshot
    json.dumps(snapshot)  # must cross wire/disk unchanged


async def test_round_trip_preserves_gate_pending(repo, make_repo):
    await _seed_pending(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    row = await dest.get_gate_pending(7001)
    assert row["gate_type"] == "d"
    assert row["cost"] == 250000
    assert row["options"] == {"111": "laser", NOTHING: None}
    kappa = await dest.get_gate_pending(7002)
    assert kappa["options"] == {"900001": "hercules", "900002": "lf4u",
                                NOTHING: None}


async def test_snapshot_is_stable_after_gate_pending_round_trip(repo, make_repo):
    await _seed_pending(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    assert await dest.export_all() == snapshot


async def test_clear_wipes_gate_pending(repo):
    await _seed_pending(repo)
    await repo.clear()
    assert await repo.all_gate_pending() == []


# ── migrate._DATA_TABLES registers the new table ────────────────────────────

def test_migrate_data_tables_includes_gate_pending():
    from n3x_bot import migrate
    assert "gate_pending" in migrate._DATA_TABLES
