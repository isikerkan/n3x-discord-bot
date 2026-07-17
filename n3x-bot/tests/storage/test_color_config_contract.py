"""Contract tests for the ``color_config`` repository surface — Phase 3 of the
de-hardcode-to-DB-objects effort (editable achievement TIER / CATEGORY colours).

Mirrors ``tests/storage/test_content_texts_contract.py`` EXACTLY: a keyed
``key -> value`` store parametrized across every registered backend via the
shared ``repo`` / ``make_repo`` fixtures (json, sqlite, and postgres when
TEST_POSTGRES_URL is set).

New interface (to be implemented downstream on StatsRepository + json_repo +
sql_repo + schema — new table
``color_config(key String PK, value Text nullable)``):

    async def set_color_config(key: str, value: str) -> None    # upsert
    async def get_color_config(key: str) -> str | None
    async def delete_color_config(key: str) -> bool
    async def all_color_config() -> dict[str, str]

Keys follow the resolver convention ``tier:<substring>`` / ``category:<name>``
mapping to a ``#RRGGBB`` value, but STORAGE is convention-agnostic: it round-trips
any string key/value verbatim (parsing/merging is the resolver's job).

Plus migration fidelity: the table MUST be included in ``export_all()`` /
``import_all()`` / ``clear()`` and in ``n3x_bot.migrate._DATA_TABLES``.

RED until then: calling these raises AttributeError on the repo.
"""

import json


# ── set / get roundtrip ─────────────────────────────────────────────────────

async def test_get_color_config_unknown_key_returns_none(repo):
    assert await repo.get_color_config("tier:gold") is None


async def test_set_color_config_roundtrips(repo):
    await repo.set_color_config("tier:gold", "#010203")
    assert await repo.get_color_config("tier:gold") == "#010203"


async def test_get_color_config_returns_str(repo):
    await repo.set_color_config("tier:gold", "#010203")
    assert isinstance(await repo.get_color_config("tier:gold"), str)


# ── upsert semantics: re-setting a key overwrites its stored value ───────────

async def test_set_color_config_upserts_same_key(repo):
    await repo.set_color_config("tier:gold", "#111111")
    await repo.set_color_config("tier:gold", "#222222")
    assert await repo.get_color_config("tier:gold") == "#222222"


# ── keys are independent ─────────────────────────────────────────────────────

async def test_multiple_keys_are_independent(repo):
    await repo.set_color_config("tier:gold", "#010203")
    await repo.set_color_config("category:voice", "#040506")
    assert await repo.get_color_config("tier:gold") == "#010203"
    assert await repo.get_color_config("category:voice") == "#040506"


async def test_set_one_key_leaves_other_keys_unset(repo):
    await repo.set_color_config("tier:gold", "#010203")
    assert await repo.get_color_config("category:voice") is None


# ── value survives verbatim (storage does no hex validation) ─────────────────

async def test_value_survives_verbatim(repo):
    # The storage layer round-trips the RAW string unchanged; hex validation /
    # parsing is the resolver's / write-command's job, not storage.
    value = "#AbCdEf"
    await repo.set_color_config("category:streak", value)
    assert await repo.get_color_config("category:streak") == value


# ── delete: returns bool, then the key is gone ──────────────────────────────

async def test_delete_color_config_returns_true_when_present(repo):
    await repo.set_color_config("tier:gold", "#010203")
    assert await repo.delete_color_config("tier:gold") is True


async def test_delete_color_config_removes_the_key(repo):
    await repo.set_color_config("tier:gold", "#010203")
    await repo.delete_color_config("tier:gold")
    assert await repo.get_color_config("tier:gold") is None


async def test_delete_color_config_returns_false_when_absent(repo):
    assert await repo.delete_color_config("nope") is False


# ── all_color_config: full map ───────────────────────────────────────────────

async def test_all_color_config_empty_by_default(repo):
    assert await repo.all_color_config() == {}


async def test_all_color_config_returns_full_map(repo):
    await repo.set_color_config("tier:gold", "#010203")
    await repo.set_color_config("category:voice", "#040506")
    assert await repo.all_color_config() == {
        "tier:gold": "#010203",
        "category:voice": "#040506",
    }


# ── migration fidelity: export / import / clear ─────────────────────────────

async def _seed_color_config(repo):
    await repo.set_color_config("tier:gold", "#010203")
    await repo.set_color_config("category:voice", "#040506")


async def test_export_all_includes_color_config_and_is_json_serializable(repo):
    await _seed_color_config(repo)
    snapshot = await repo.export_all()
    assert "color_config" in snapshot
    json.dumps(snapshot)  # must cross wire/disk unchanged


async def test_round_trip_preserves_color_config(repo, make_repo):
    await _seed_color_config(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    assert await dest.get_color_config("tier:gold") == "#010203"
    assert await dest.get_color_config("category:voice") == "#040506"


async def test_snapshot_is_stable_after_color_config_round_trip(repo, make_repo):
    await _seed_color_config(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    assert await dest.export_all() == snapshot


async def test_clear_wipes_color_config(repo):
    await _seed_color_config(repo)
    await repo.clear()
    assert await repo.all_color_config() == {}


# ── migrate._DATA_TABLES registers the new table (non-empty-dest detection) ──

def test_migrate_data_tables_includes_color_config():
    from n3x_bot import migrate
    assert "color_config" in migrate._DATA_TABLES
