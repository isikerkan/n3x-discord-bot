"""Contract tests for the runtime_config repository surface (Phase 1 of moving
bot config off the AMP GUI onto a `.env` base + per-key DB overrides).

Parametrized across every registered backend via the shared ``repo`` /
``make_repo`` fixtures (json, sqlite, and postgres when TEST_POSTGRES_URL is
set), mirroring ``tests/storage/test_channel_messages_contract.py`` and
``tests/storage/test_base_timers_repository_contract.py``.

New interface (to be implemented downstream on StatsRepository + json_repo +
sql_repo + schema — new table
``runtime_config(key String PK, value Text nullable)``):

    async def set_runtime_config(key: str, value: str) -> None    # upsert
    async def get_runtime_config(key: str) -> str | None
    async def delete_runtime_config(key: str) -> bool
    async def all_runtime_config() -> dict[str, str]

Plus migration fidelity: the table MUST be included in ``export_all()`` /
``import_all()`` / ``clear()`` and in ``n3x_bot.migrate._DATA_TABLES``.

RED until then: calling these raises AttributeError on the repo.
"""

import json


# ── set / get roundtrip ─────────────────────────────────────────────────────

async def test_get_runtime_config_unknown_key_returns_none(repo):
    assert await repo.get_runtime_config("gate_stats_channel_id") is None


async def test_set_runtime_config_roundtrips(repo):
    await repo.set_runtime_config("gate_stats_channel_id", "999")
    assert await repo.get_runtime_config("gate_stats_channel_id") == "999"


async def test_get_runtime_config_returns_str(repo):
    await repo.set_runtime_config("gate_stats_channel_id", "999")
    assert isinstance(await repo.get_runtime_config("gate_stats_channel_id"), str)


# ── upsert semantics: re-setting a key overwrites its stored value ───────────

async def test_set_runtime_config_upserts_same_key(repo):
    await repo.set_runtime_config("gate_stats_channel_id", "999")
    await repo.set_runtime_config("gate_stats_channel_id", "111")
    assert await repo.get_runtime_config("gate_stats_channel_id") == "111"


# ── keys are independent ─────────────────────────────────────────────────────

async def test_multiple_keys_are_independent(repo):
    await repo.set_runtime_config("welcome_channel_id", "10")
    await repo.set_runtime_config("reminder_channel_id", "20")
    assert await repo.get_runtime_config("welcome_channel_id") == "10"
    assert await repo.get_runtime_config("reminder_channel_id") == "20"


async def test_set_one_key_leaves_other_keys_unset(repo):
    await repo.set_runtime_config("welcome_channel_id", "10")
    assert await repo.get_runtime_config("reminder_channel_id") is None


# ── delete: returns bool, then the key is gone ──────────────────────────────

async def test_delete_runtime_config_returns_true_when_present(repo):
    await repo.set_runtime_config("welcome_channel_id", "10")
    assert await repo.delete_runtime_config("welcome_channel_id") is True


async def test_delete_runtime_config_removes_the_key(repo):
    await repo.set_runtime_config("welcome_channel_id", "10")
    await repo.delete_runtime_config("welcome_channel_id")
    assert await repo.get_runtime_config("welcome_channel_id") is None


async def test_delete_runtime_config_returns_false_when_absent(repo):
    assert await repo.delete_runtime_config("nope") is False


# ── all_runtime_config: full map ─────────────────────────────────────────────

async def test_all_runtime_config_empty_by_default(repo):
    assert await repo.all_runtime_config() == {}


async def test_all_runtime_config_returns_full_map(repo):
    await repo.set_runtime_config("welcome_channel_id", "10")
    await repo.set_runtime_config("gate_rewards", "a:1,b:2")
    assert await repo.all_runtime_config() == {
        "welcome_channel_id": "10",
        "gate_rewards": "a:1,b:2",
    }


# ── content-typed override values survive verbatim (not coerced by storage) ──

async def test_content_override_string_survives_verbatim(repo):
    # The storage layer stores the RAW override string; parsing/coercion is the
    # resolver's job, so a comma/colon-laden value must round-trip unchanged.
    await repo.set_runtime_config("gate_rewards", "a:1,b:2,c:3")
    assert await repo.get_runtime_config("gate_rewards") == "a:1,b:2,c:3"


# ── migration fidelity: export / import / clear ─────────────────────────────

async def _seed_runtime_config(repo):
    await repo.set_runtime_config("gate_stats_channel_id", "999")
    await repo.set_runtime_config("gate_rewards", "a:1,b:2")


async def test_export_all_includes_runtime_config_and_is_json_serializable(repo):
    await _seed_runtime_config(repo)
    snapshot = await repo.export_all()
    assert "runtime_config" in snapshot
    json.dumps(snapshot)  # must cross wire/disk unchanged


async def test_round_trip_preserves_runtime_config(repo, make_repo):
    await _seed_runtime_config(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    assert await dest.get_runtime_config("gate_stats_channel_id") == "999"
    assert await dest.get_runtime_config("gate_rewards") == "a:1,b:2"


async def test_snapshot_is_stable_after_runtime_config_round_trip(repo, make_repo):
    await _seed_runtime_config(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    assert await dest.export_all() == snapshot


async def test_clear_wipes_runtime_config(repo):
    await _seed_runtime_config(repo)
    await repo.clear()
    assert await repo.all_runtime_config() == {}


# ── migrate._DATA_TABLES registers the new table (non-empty-dest detection) ──

def test_migrate_data_tables_includes_runtime_config():
    from n3x_bot import migrate
    assert "runtime_config" in migrate._DATA_TABLES
