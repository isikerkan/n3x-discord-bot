"""Contract tests for the ``content_texts`` repository surface — Phase 1 of the
de-hardcode-to-DB-objects effort (editable narrative copy).

Mirrors ``tests/storage/test_runtime_config_contract.py`` exactly: a keyed
``key -> value`` store parametrized across every registered backend via the
shared ``repo`` / ``make_repo`` fixtures (json, sqlite, and postgres when
TEST_POSTGRES_URL is set).

New interface (to be implemented downstream on StatsRepository + json_repo +
sql_repo + schema — new table
``content_texts(key String PK, value Text nullable)``):

    async def set_content_text(key: str, value: str) -> None    # upsert
    async def get_content_text(key: str) -> str | None
    async def delete_content_text(key: str) -> bool
    async def all_content_texts() -> dict[str, str]

Plus migration fidelity: the table MUST be included in ``export_all()`` /
``import_all()`` / ``clear()`` and in ``n3x_bot.migrate._DATA_TABLES``.

RED until then: calling these raises AttributeError on the repo.
"""

import json


# ── set / get roundtrip ─────────────────────────────────────────────────────

async def test_get_content_text_unknown_key_returns_none(repo):
    assert await repo.get_content_text("kodex_text") is None


async def test_set_content_text_roundtrips(repo):
    await repo.set_content_text("kodex_text", "Neuer Kodex")
    assert await repo.get_content_text("kodex_text") == "Neuer Kodex"


async def test_get_content_text_returns_str(repo):
    await repo.set_content_text("kodex_text", "Neuer Kodex")
    assert isinstance(await repo.get_content_text("kodex_text"), str)


# ── upsert semantics: re-setting a key overwrites its stored value ───────────

async def test_set_content_text_upserts_same_key(repo):
    await repo.set_content_text("kodex_text", "erste Fassung")
    await repo.set_content_text("kodex_text", "zweite Fassung")
    assert await repo.get_content_text("kodex_text") == "zweite Fassung"


# ── keys are independent ─────────────────────────────────────────────────────

async def test_multiple_keys_are_independent(repo):
    await repo.set_content_text("reminder_aceball", "A")
    await repo.set_content_text("reminder_invasion", "B")
    assert await repo.get_content_text("reminder_aceball") == "A"
    assert await repo.get_content_text("reminder_invasion") == "B"


async def test_set_one_key_leaves_other_keys_unset(repo):
    await repo.set_content_text("reminder_aceball", "A")
    assert await repo.get_content_text("reminder_invasion") is None


# ── multi-line / placeholder-laden copy survives verbatim ────────────────────

async def test_multiline_and_placeholder_value_survives_verbatim(repo):
    # Narrative copy has newlines, markdown and `{named}` placeholders; the
    # storage layer must round-trip the RAW string unchanged (formatting/
    # substitution is the resolver's / read-site's job, not storage).
    value = "🍀 **Rekord!** <@{user}> — **{name}**:\n**{cost}**"
    await repo.set_content_text("record_lucky", value)
    assert await repo.get_content_text("record_lucky") == value


# ── delete: returns bool, then the key is gone ──────────────────────────────

async def test_delete_content_text_returns_true_when_present(repo):
    await repo.set_content_text("welcome_dm", "Willkommen {mention}!")
    assert await repo.delete_content_text("welcome_dm") is True


async def test_delete_content_text_removes_the_key(repo):
    await repo.set_content_text("welcome_dm", "Willkommen {mention}!")
    await repo.delete_content_text("welcome_dm")
    assert await repo.get_content_text("welcome_dm") is None


async def test_delete_content_text_returns_false_when_absent(repo):
    assert await repo.delete_content_text("nope") is False


# ── all_content_texts: full map ──────────────────────────────────────────────

async def test_all_content_texts_empty_by_default(repo):
    assert await repo.all_content_texts() == {}


async def test_all_content_texts_returns_full_map(repo):
    await repo.set_content_text("reminder_aceball", "A")
    await repo.set_content_text("welcome_dm", "Willkommen {mention}!")
    assert await repo.all_content_texts() == {
        "reminder_aceball": "A",
        "welcome_dm": "Willkommen {mention}!",
    }


# ── migration fidelity: export / import / clear ─────────────────────────────

async def _seed_content_texts(repo):
    await repo.set_content_text("kodex_text", "Kodex Override")
    await repo.set_content_text("welcome_dm", "Hallo {mention}")


async def test_export_all_includes_content_texts_and_is_json_serializable(repo):
    await _seed_content_texts(repo)
    snapshot = await repo.export_all()
    assert "content_texts" in snapshot
    json.dumps(snapshot)  # must cross wire/disk unchanged


async def test_round_trip_preserves_content_texts(repo, make_repo):
    await _seed_content_texts(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    assert await dest.get_content_text("kodex_text") == "Kodex Override"
    assert await dest.get_content_text("welcome_dm") == "Hallo {mention}"


async def test_snapshot_is_stable_after_content_texts_round_trip(repo, make_repo):
    await _seed_content_texts(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    assert await dest.export_all() == snapshot


async def test_clear_wipes_content_texts(repo):
    await _seed_content_texts(repo)
    await repo.clear()
    assert await repo.all_content_texts() == {}


# ── migrate._DATA_TABLES registers the new table (non-empty-dest detection) ──

def test_migrate_data_tables_includes_content_texts():
    from n3x_bot import migrate
    assert "content_texts" in migrate._DATA_TABLES
