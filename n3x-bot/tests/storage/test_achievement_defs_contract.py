"""Contract tests for the ``achievement_defs`` repository surface — Phase 2a of
the de-hardcode-to-DB-objects effort (DB-backed achievement DEFINITIONS).

Mirrors ``tests/storage/test_content_texts_contract.py``: a per-row keyed store
parametrized across every registered backend via the shared ``repo`` /
``make_repo`` fixtures (json, sqlite, and postgres when TEST_POSTGRES_URL set).

New interface (to be implemented downstream on StatsRepository + json_repo +
sql_repo + schema — new table ``achievement_defs`` with columns
``id String PK, category String, metric String, threshold Integer,
title Text, secret Boolean, color String nullable``):

    async def set_achievement_def(id, *, category, metric, threshold, title,
                                  secret, color=None) -> None            # upsert
    async def get_achievement_def(id) -> dict | None   # 7 keys, None if absent
    async def delete_achievement_def(id) -> bool       # True if a row removed
    async def all_achievement_defs() -> list[dict]     # ordered by id

Plus migration fidelity: the table MUST be included in ``export_all()`` /
``import_all()`` / ``clear()`` and in ``n3x_bot.migrate._DATA_TABLES``.

RED until then: calling these raises AttributeError on the repo.
"""

import json


ALL_KEYS = {"id", "category", "metric", "threshold", "title", "secret", "color"}


def _seed_one(repo, aid="voice_3600", **overrides):
    kwargs = dict(category="voice", metric="voice_seconds", threshold=3600,
                  title="Rookie Talker", secret=False, color=None)
    kwargs.update(overrides)
    return repo.set_achievement_def(aid, **kwargs)


# ── set / get roundtrip ─────────────────────────────────────────────────────

async def test_get_achievement_def_unknown_id_returns_none(repo):
    assert await repo.get_achievement_def("voice_3600") is None


async def test_set_achievement_def_roundtrips_all_fields(repo):
    await _seed_one(repo, "voice_3600", color="#1E90FF")
    row = await repo.get_achievement_def("voice_3600")
    assert row == {
        "id": "voice_3600", "category": "voice", "metric": "voice_seconds",
        "threshold": 3600, "title": "Rookie Talker", "secret": False,
        "color": "#1E90FF",
    }


async def test_get_achievement_def_returns_dict_with_all_seven_keys(repo):
    await _seed_one(repo, "voice_3600")
    row = await repo.get_achievement_def("voice_3600")
    assert set(row) == ALL_KEYS


async def test_threshold_round_trips_as_int(repo):
    await _seed_one(repo, "voice_3600", threshold=3600)
    row = await repo.get_achievement_def("voice_3600")
    assert isinstance(row["threshold"], int)
    assert row["threshold"] == 3600


async def test_secret_round_trips_as_bool(repo):
    await _seed_one(repo, "msg_1000", category="message", metric="messages",
                    threshold=1000, title="Tastatur-Krieger", secret=True)
    row = await repo.get_achievement_def("msg_1000")
    assert row["secret"] is True


async def test_color_defaults_to_none_when_omitted(repo):
    await repo.set_achievement_def("voice_3600", category="voice",
                                   metric="voice_seconds", threshold=3600,
                                   title="Rookie Talker", secret=False)
    row = await repo.get_achievement_def("voice_3600")
    assert row["color"] is None


# ── upsert semantics: re-setting an id overwrites its stored row ─────────────

async def test_set_achievement_def_upserts_same_id(repo):
    await _seed_one(repo, "voice_3600", title="Rookie Talker")
    await _seed_one(repo, "voice_3600", title="Umbenannt", color="#000000")
    row = await repo.get_achievement_def("voice_3600")
    assert row["title"] == "Umbenannt"
    assert row["color"] == "#000000"


# ── ids are independent ─────────────────────────────────────────────────────

async def test_multiple_ids_are_independent(repo):
    await _seed_one(repo, "voice_3600", title="Rookie Talker")
    await _seed_one(repo, "voice_36000", threshold=36000, title="Stammgast")
    assert (await repo.get_achievement_def("voice_3600"))["title"] == "Rookie Talker"
    assert (await repo.get_achievement_def("voice_36000"))["title"] == "Stammgast"


async def test_set_one_id_leaves_other_ids_unset(repo):
    await _seed_one(repo, "voice_3600")
    assert await repo.get_achievement_def("voice_36000") is None


# ── delete: returns bool, then the id is gone ───────────────────────────────

async def test_delete_achievement_def_returns_true_when_present(repo):
    await _seed_one(repo, "voice_3600")
    assert await repo.delete_achievement_def("voice_3600") is True


async def test_delete_achievement_def_removes_the_id(repo):
    await _seed_one(repo, "voice_3600")
    await repo.delete_achievement_def("voice_3600")
    assert await repo.get_achievement_def("voice_3600") is None


async def test_delete_achievement_def_returns_false_when_absent(repo):
    assert await repo.delete_achievement_def("nope") is False


# ── all_achievement_defs: full, deterministically ordered list ──────────────

async def test_all_achievement_defs_empty_by_default(repo):
    assert await repo.all_achievement_defs() == []


async def test_all_achievement_defs_returns_every_row(repo):
    await _seed_one(repo, "voice_3600")
    await _seed_one(repo, "msg_1000", category="message", metric="messages",
                    threshold=1000, title="Tastatur-Krieger", secret=True)
    rows = await repo.all_achievement_defs()
    assert {r["id"] for r in rows} == {"voice_3600", "msg_1000"}


async def test_all_achievement_defs_rows_have_all_seven_keys(repo):
    await _seed_one(repo, "voice_3600")
    rows = await repo.all_achievement_defs()
    assert all(set(r) == ALL_KEYS for r in rows)


async def test_all_achievement_defs_is_ordered_by_id(repo):
    for aid in ("voice_3600", "a_5", "msg_1000"):
        await _seed_one(repo, aid)
    rows = await repo.all_achievement_defs()
    ids = [r["id"] for r in rows]
    assert ids == sorted(ids)


# ── replace_achievement_defs: atomic bulk replace ──────────────────────────

def _def(aid, **overrides):
    d = dict(id=aid, category="voice", metric="voice_seconds", threshold=3600,
             title="Rookie Talker", secret=False, color=None)
    d.update(overrides)
    return d


async def test_replace_achievement_defs_sets_exactly_those_rows(repo):
    await _seed_one(repo, "stale_1")
    await _seed_one(repo, "stale_2")
    await repo.replace_achievement_defs(
        [_def("a_5", threshold=5), _def("msg_1000", category="message",
                                        metric="messages", threshold=1000,
                                        title="Tastatur-Krieger", secret=True)])
    rows = await repo.all_achievement_defs()
    assert {r["id"] for r in rows} == {"a_5", "msg_1000"}


async def test_replace_achievement_defs_empty_wipes_the_table(repo):
    await _seed_one(repo, "voice_3600")
    await repo.replace_achievement_defs([])
    assert await repo.all_achievement_defs() == []


async def test_replace_achievement_defs_round_trips_all_fields(repo):
    await repo.replace_achievement_defs(
        [_def("voice_3600", threshold=3600, color="#1E90FF", secret=True)])
    row = await repo.get_achievement_def("voice_3600")
    assert row == {
        "id": "voice_3600", "category": "voice", "metric": "voice_seconds",
        "threshold": 3600, "title": "Rookie Talker", "secret": True,
        "color": "#1E90FF",
    }


async def test_replace_achievement_defs_defaults_color_to_none(repo):
    d = _def("voice_3600")
    d.pop("color")
    await repo.replace_achievement_defs([d])
    assert (await repo.get_achievement_def("voice_3600"))["color"] is None


# ── migration fidelity: export / import / clear ─────────────────────────────

async def _seed_defs(repo):
    await _seed_one(repo, "voice_3600", color="#1E90FF")
    await _seed_one(repo, "msg_1000", category="message", metric="messages",
                    threshold=1000, title="Tastatur-Krieger", secret=True)


async def test_export_all_includes_achievement_defs_and_is_json_serializable(repo):
    await _seed_defs(repo)
    snapshot = await repo.export_all()
    assert "achievement_defs" in snapshot
    json.dumps(snapshot)


async def test_round_trip_preserves_achievement_defs(repo, make_repo):
    await _seed_defs(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    row = await dest.get_achievement_def("voice_3600")
    assert row["title"] == "Rookie Talker"
    assert row["color"] == "#1E90FF"
    assert (await dest.get_achievement_def("msg_1000"))["secret"] is True


async def test_snapshot_is_stable_after_achievement_defs_round_trip(repo, make_repo):
    await _seed_defs(repo)
    snapshot = await repo.export_all()
    dest = await make_repo()
    await dest.import_all(snapshot)
    assert await dest.export_all() == snapshot


async def test_clear_wipes_achievement_defs(repo):
    await _seed_defs(repo)
    await repo.clear()
    assert await repo.all_achievement_defs() == []


# ── migrate._DATA_TABLES registers the new table ────────────────────────────

def test_migrate_data_tables_includes_achievement_defs():
    from n3x_bot import migrate
    assert "achievement_defs" in migrate._DATA_TABLES
