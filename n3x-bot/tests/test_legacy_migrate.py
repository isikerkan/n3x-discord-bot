"""RED-phase specs for the v3 legacy database migration (feature/legacy-migration).

On startup the bot looks in a `migration/` folder; if a v3 `bot_data.db`
(legacy SQLite schema) or a legacy flatfile is present, it migrates the data
into OUR current schema (mapping v3 -> n3x), then renames the source aside so
it never re-runs.

These tests target a NOT-YET-EXISTING module `n3x_bot.legacy_migrate` and a
NOT-YET-EXISTING `Settings.migration_dir` field, so they are expected to FAIL
at import/reference time (that is the RED signal). Imports of the new module
are done lazily inside the tests so the file collects even while the module is
absent.

Pinned signatures verified against n3x_bot/storage/base.py + json_repo.py:
  set_streak(discord_id, current_streak, last_active_date, max_streak)
  set_night(discord_id, night_count, last_night_date)
  add_activity(discord_id, metric, amount)   metrics: voice_seconds/messages/reactions
  record_target_use(target_discord_id, stat_key)
  add_gate_entry(gate_type, cost, user_id, username, dedup_window_seconds=30,
                 laser_dropped=None, drops=None)
  get_streak -> {current_streak, last_active_date, max_streak}
  get_night  -> {night_count, last_night_date}
  gate_drop_stats(gt) -> {count, avg, rates}   (rates["laser"] for delta)
  save_kodex_message(message_id, discord_id) / get_kodex_message_user(message_id)
"""

import os
import sqlite3
import types


from n3x_bot.config import Settings
from n3x_bot.storage.json_repo import JsonRepository
from n3x_bot.seed import seed_defaults


# ── v3 source database builder (pure stdlib sqlite3, no new prod symbols) ────

def _build_v3_db(path: str) -> None:
    """Create the real v3 `bot_data.db` schema and insert a representative
    fixture covering every table the migration reads (plus a few SKIP tables
    to prove they are ignored without crashing).

    Note: all id columns are TEXT in v3, matching production, so the migration
    must int()-coerce them.
    """
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.executescript(
        """
        CREATE TABLE global_stats (command_name TEXT PRIMARY KEY, count INT);
        CREATE TABLE user_stats (user_id TEXT, command_name TEXT, count INT,
            PRIMARY KEY(user_id, command_name));
        CREATE TABLE target_stats (target_id TEXT, command_name TEXT, count INT,
            PRIMARY KEY(target_id, command_name));
        CREATE TABLE gate_stats (id INTEGER PRIMARY KEY, gate_type TEXT, cost INT,
            user_id TEXT, username TEXT, timestamp TEXT);
        CREATE TABLE delta_stats (id INTEGER PRIMARY KEY, cost INT,
            laser_dropped BOOL, user_id TEXT, username TEXT, timestamp TEXT);
        CREATE TABLE achievements (user_id TEXT, achievement_id TEXT,
            PRIMARY KEY(user_id, achievement_id));
        CREATE TABLE gate_records (gate_type TEXT, record_type TEXT, cost INT,
            user_id TEXT, PRIMARY KEY(gate_type, record_type));
        CREATE TABLE voice_stats (user_id TEXT PRIMARY KEY, total_seconds INT);
        CREATE TABLE message_stats (user_id TEXT PRIMARY KEY, total_messages INT);
        CREATE TABLE reaction_stats (user_id TEXT PRIMARY KEY, reaction_count INT);
        CREATE TABLE streak_stats (user_id TEXT PRIMARY KEY, current_streak INT,
            last_active_date TEXT, max_streak INT);
        CREATE TABLE night_stats (user_id TEXT PRIMARY KEY, night_count INT,
            last_night_date TEXT);
        CREATE TABLE event_stats (user_id TEXT PRIMARY KEY, event_count INT);
        CREATE TABLE last_messages (key TEXT PRIMARY KEY, message_id TEXT);
        CREATE TABLE kodex_confirmations (user_id TEXT PRIMARY KEY, timestamp TEXT);
        CREATE TABLE kodex_messages (message_id TEXT PRIMARY KEY, user_id TEXT);
        """
    )

    # global_stats — SKIP (record_use reconstructs the global total)
    cur.executemany("INSERT INTO global_stats VALUES (?,?)",
                    [("tit", 99), ("cry", 5)])

    # user_stats — 'schlaganfall' is a v3 command we do NOT seed -> create-missing
    cur.executemany("INSERT INTO user_stats VALUES (?,?,?)", [
        ("1001", "tit", 3),
        ("1001", "cry", 1),
        ("2002", "schlaganfall", 2),
    ])

    # target_stats
    cur.execute("INSERT INTO target_stats VALUES (?,?,?)", ("3003", "smart", 2))

    # gate_stats — two IDENTICAL (user, gate, cost) rows: with dedup_window=0
    # BOTH must import (they are legitimate separate runs, not dupes).
    cur.executemany("INSERT INTO gate_stats (gate_type,cost,user_id,username,timestamp) VALUES (?,?,?,?,?)", [
        ("a", 46000, "4001", "gamerA", "2024-01-01T00:00:00"),
        ("a", 46000, "4001", "gamerA", "2024-01-01T00:00:05"),
        ("b", 90000, "4002", "gamerB", "2024-01-01T00:00:10"),
    ])

    # delta_stats — laser dropped True on the one delta entry
    cur.execute(
        "INSERT INTO delta_stats (cost,laser_dropped,user_id,username,timestamp) VALUES (?,?,?,?,?)",
        (150000, 1, "4001", "gamerA", "2024-01-02T00:00:00"))

    # achievements — voice_3600 is a REAL id; ghost_stat_9 / legacy_only_1 are
    # ids no current definition has, so migration must skip them.
    cur.executemany("INSERT INTO achievements VALUES (?,?)", [
        ("5001", "voice_3600"),
        ("5001", "ghost_stat_9"),
        ("5001", "legacy_only_1"),
    ])

    # gate_records — SKIP (ours derive on demand)
    cur.execute("INSERT INTO gate_records VALUES (?,?,?,?)", ("a", "min", 46000, "4001"))

    # activity source tables
    cur.execute("INSERT INTO voice_stats VALUES (?,?)", ("7001", 3700))
    cur.execute("INSERT INTO message_stats VALUES (?,?)", ("7001", 1200))
    cur.execute("INSERT INTO reaction_stats VALUES (?,?)", ("7001", 150))

    # streak / night
    cur.execute("INSERT INTO streak_stats VALUES (?,?,?,?)",
                ("8001", 5, "2024-06-01", 9))
    cur.execute("INSERT INTO night_stats VALUES (?,?,?)",
                ("8001", 12, "2024-06-02"))

    # kodex
    cur.execute("INSERT INTO kodex_confirmations VALUES (?,?)",
                ("9001", "2024-01-01T00:00:00"))
    cur.execute("INSERT INTO kodex_messages VALUES (?,?)",
                ("1523088462511734941", "9001"))

    # SKIP tables with rows present -> migration must ignore, not crash
    cur.execute("INSERT INTO event_stats VALUES (?,?)", ("9999", 7))
    cur.execute("INSERT INTO last_messages VALUES (?,?)", ("wahab", "123"))

    con.commit()
    con.close()


async def _dest_repo(tmp_path) -> JsonRepository:
    """A fresh, connected, seed_defaults'd JsonRepository as the destination."""
    path = os.path.join(str(tmp_path), "dest.json")
    repo = JsonRepository(path)
    await repo.connect()
    await seed_defaults(repo)
    return repo


async def _migrate_full(tmp_path):
    """Build the v3 db + dest repo, run the (not-yet-existing) migration,
    return (repo, summary). The import is lazy so absence of the module is the
    RED signal at call time, not at collection time."""
    from n3x_bot.legacy_migrate import migrate_legacy_sqlite  # RED: module absent

    db_path = os.path.join(str(tmp_path), "bot_data.db")
    _build_v3_db(db_path)
    repo = await _dest_repo(tmp_path)
    summary = await migrate_legacy_sqlite(repo, db_path)
    return repo, summary


# ── migrate_legacy_sqlite: destination-state assertions ─────────────────────

async def test_users_are_upserted_with_gate_username(tmp_path):
    repo, _ = await _migrate_full(tmp_path)
    u = await repo.get_user(4001)
    assert u is not None and u.display_name == "gamerA"
    await repo.close()


async def test_user_without_name_source_gets_synthetic_display_name(tmp_path):
    # 1001 appears only in user_stats (no username column) -> "user_1001"
    repo, _ = await _migrate_full(tmp_path)
    u = await repo.get_user(1001)
    assert u is not None and u.display_name == "user_1001"
    await repo.close()


async def test_text_ids_are_int_coerced(tmp_path):
    # v3 stores ids as TEXT; destination lookups use ints.
    repo, _ = await _migrate_full(tmp_path)
    assert await repo.get_user(1523088462511734941) is None  # not a user, sanity
    assert await repo.get_kodex_message_user(1523088462511734941) == 9001
    await repo.close()


async def test_user_stats_counts_are_recorded(tmp_path):
    repo, _ = await _migrate_full(tmp_path)
    assert await repo.get_total("tit") == 3
    assert (await repo.get_user_stats(1001)).get("tit") == 3
    assert (await repo.get_user_stats(1001)).get("cry") == 1
    await repo.close()


async def test_missing_stat_is_created_so_no_data_is_lost(tmp_path):
    # 'schlaganfall' is not in our seed set; create-missing (pinned decision).
    repo, _ = await _migrate_full(tmp_path)
    assert await repo.get_stat("schlaganfall") is not None
    assert await repo.get_total("schlaganfall") == 2
    assert (await repo.get_user_stats(2002)).get("schlaganfall") == 2
    await repo.close()


async def test_global_stats_table_is_skipped(tmp_path):
    # global_stats(tit)=99 must NOT leak in; total is rebuilt from user_stats(=3).
    repo, _ = await _migrate_full(tmp_path)
    assert await repo.get_total("tit") == 3
    await repo.close()


async def test_target_stats_counts_are_recorded(tmp_path):
    repo, _ = await _migrate_full(tmp_path)
    assert await repo.get_target_total(3003, "smart") == 2
    await repo.close()


async def test_gate_entries_import(tmp_path):
    repo, _ = await _migrate_full(tmp_path)
    totals = await repo.gate_totals()
    assert totals["a"]["count"] == 2
    assert totals["b"]["count"] == 1
    await repo.close()


async def test_identical_gate_rows_are_not_deduped_during_migration(tmp_path):
    # dedup_window_seconds=0 on import -> both same-user/same-cost rows survive.
    repo, _ = await _migrate_full(tmp_path)
    assert await repo.list_gate_costs("a") == [46000, 46000]
    await repo.close()


async def test_delta_entry_imports_as_gate_d_with_laser_drop(tmp_path):
    repo, _ = await _migrate_full(tmp_path)
    stats = await repo.gate_drop_stats("d")
    assert stats["count"] == 1
    assert stats["rates"].get("laser") == 100.0
    await repo.close()


async def test_voice_activity_imports(tmp_path):
    repo, _ = await _migrate_full(tmp_path)
    assert await repo.get_activity(7001, "voice_seconds") == 3700
    await repo.close()


async def test_message_activity_imports(tmp_path):
    repo, _ = await _migrate_full(tmp_path)
    assert await repo.get_activity(7001, "messages") == 1200
    await repo.close()


async def test_reaction_activity_imports(tmp_path):
    repo, _ = await _migrate_full(tmp_path)
    assert await repo.get_activity(7001, "reactions") == 150
    await repo.close()


async def test_streak_imports_with_correct_field_mapping(tmp_path):
    repo, _ = await _migrate_full(tmp_path)
    s = await repo.get_streak(8001)
    assert s == {"current_streak": 5, "last_active_date": "2024-06-01",
                 "max_streak": 9}
    await repo.close()


async def test_night_imports_with_correct_field_mapping(tmp_path):
    repo, _ = await _migrate_full(tmp_path)
    n = await repo.get_night(8001)
    assert n == {"night_count": 12, "last_night_date": "2024-06-02"}
    await repo.close()


async def test_kodex_confirmation_imports(tmp_path):
    repo, _ = await _migrate_full(tmp_path)
    assert await repo.has_confirmed_kodex(9001) is True
    await repo.close()


async def test_kodex_message_imports(tmp_path):
    repo, _ = await _migrate_full(tmp_path)
    assert await repo.get_kodex_message_user(1523088462511734941) == 9001
    await repo.close()


async def test_known_achievement_is_unlocked(tmp_path):
    repo, _ = await _migrate_full(tmp_path)
    assert await repo.has_achievement(5001, "voice_3600") is True
    await repo.close()


async def test_unknown_achievement_id_is_skipped(tmp_path):
    # ghost_stat_9 / legacy_only_1 are not real ids in achievements.ACHIEVEMENTS.
    repo, _ = await _migrate_full(tmp_path)
    assert await repo.has_achievement(5001, "ghost_stat_9") is False
    assert await repo.has_achievement(5001, "legacy_only_1") is False
    await repo.close()


async def test_summary_counts_skipped_achievements(tmp_path):
    _, summary = await _migrate_full(tmp_path)
    assert summary["achievements_skipped"] >= 2


async def test_summary_reports_imported_gate_entries(tmp_path):
    _, summary = await _migrate_full(tmp_path)
    assert summary["gate_entries"] == 3


async def test_summary_reports_delta_count(tmp_path):
    _, summary = await _migrate_full(tmp_path)
    assert summary["delta"] == 1


async def test_migration_recomputes_achievements_from_counts(tmp_path):
    # PINNED: migration re-derives achievements under OUR definitions after
    # importing counts. 7001 has voice_seconds=3700 (>=3600) but NO explicit
    # achievements row -> voice_3600 must still be unlocked via recompute.
    repo, _ = await _migrate_full(tmp_path)
    assert await repo.has_achievement(7001, "voice_3600") is True
    await repo.close()


# ── run_migration_folder: folder detection + idempotent rename-aside ────────

async def test_run_migration_folder_migrates_sqlite_and_renames_aside(tmp_path):
    from n3x_bot.legacy_migrate import run_migration_folder  # RED: absent

    mig_dir = os.path.join(str(tmp_path), "migration")
    os.makedirs(mig_dir)
    db_path = os.path.join(mig_dir, "bot_data.db")
    _build_v3_db(db_path)
    repo = await _dest_repo(tmp_path)
    settings = types.SimpleNamespace(migration_dir=mig_dir)

    summary = await run_migration_folder(repo, settings)
    assert summary is not None
    assert await repo.get_total("tit") == 3
    # source renamed aside so a restart never re-imports
    assert not os.path.exists(db_path)
    assert os.path.exists(db_path + ".imported")
    await repo.close()


async def test_run_migration_folder_is_idempotent_second_call_is_noop(tmp_path):
    from n3x_bot.legacy_migrate import run_migration_folder  # RED: absent

    mig_dir = os.path.join(str(tmp_path), "migration")
    os.makedirs(mig_dir)
    _build_v3_db(os.path.join(mig_dir, "bot_data.db"))
    repo = await _dest_repo(tmp_path)
    settings = types.SimpleNamespace(migration_dir=mig_dir)

    await run_migration_folder(repo, settings)
    second = await run_migration_folder(repo, settings)
    assert second is None
    # counts unchanged -> no double import
    assert await repo.get_total("tit") == 3
    await repo.close()


async def test_run_migration_folder_returns_none_for_empty_dir(tmp_path):
    from n3x_bot.legacy_migrate import run_migration_folder  # RED: absent

    mig_dir = os.path.join(str(tmp_path), "migration")
    os.makedirs(mig_dir)
    repo = await _dest_repo(tmp_path)
    settings = types.SimpleNamespace(migration_dir=mig_dir)
    assert await run_migration_folder(repo, settings) is None
    await repo.close()


async def test_run_migration_folder_returns_none_for_absent_dir(tmp_path):
    from n3x_bot.legacy_migrate import run_migration_folder  # RED: absent

    repo = await _dest_repo(tmp_path)
    settings = types.SimpleNamespace(
        migration_dir=os.path.join(str(tmp_path), "does_not_exist"))
    assert await run_migration_folder(repo, settings) is None
    await repo.close()


async def test_run_migration_folder_routes_legacy_flatfile_to_json_importer(tmp_path):
    import json
    from n3x_bot.legacy_migrate import run_migration_folder  # RED: absent

    mig_dir = os.path.join(str(tmp_path), "migration")
    os.makedirs(mig_dir)
    flat = os.path.join(mig_dir, "stats.json")
    with open(flat, "w") as f:
        json.dump({"wahab_count": 7, "user_stats": {}}, f)
    repo = await _dest_repo(tmp_path)
    settings = types.SimpleNamespace(migration_dir=mig_dir)

    summary = await run_migration_folder(repo, settings)
    assert summary is not None
    assert await repo.get_total("wahab") == 7
    # flatfile renamed aside too -> second run is a noop
    assert not os.path.exists(flat)
    await repo.close()


# ── failure paths: safe-failure must NOT rename the irreplaceable source ─────

async def test_unreadable_db_returns_failed_summary_without_raising(tmp_path):
    # A path _connect_ro can't open -> migrate_legacy_sqlite must flag ok False
    # and NOT raise (startup must survive).
    from n3x_bot.legacy_migrate import migrate_legacy_sqlite

    repo = await _dest_repo(tmp_path)
    summary = await migrate_legacy_sqlite(
        repo, os.path.join(str(tmp_path), "does_not_exist.db"))
    assert summary["ok"] is False
    assert summary["errors"] >= 1
    assert summary["users"] == 0
    await repo.close()


async def test_garbage_db_does_not_rename_and_a_later_good_run_still_imports(tmp_path):
    # A non-sqlite file opens read-only but every table read fails -> flagged
    # failed. run_migration_folder must NOT raise and must leave the db in place
    # (not renamed aside), so a subsequent good db can still import on retry.
    from n3x_bot.legacy_migrate import run_migration_folder

    mig_dir = os.path.join(str(tmp_path), "migration")
    os.makedirs(mig_dir)
    db_path = os.path.join(mig_dir, "bot_data.db")
    with open(db_path, "wb") as f:
        f.write(b"this is not a sqlite database at all")
    repo = await _dest_repo(tmp_path)
    settings = types.SimpleNamespace(migration_dir=mig_dir)

    summary = await run_migration_folder(repo, settings)  # must NOT raise
    assert summary is not None and summary["ok"] is False
    # left in place for retry, NOT renamed aside
    assert os.path.exists(db_path)
    assert not os.path.exists(db_path + ".imported")

    # retry with a genuine v3 db -> imports and renames aside
    os.remove(db_path)
    _build_v3_db(db_path)
    summary2 = await run_migration_folder(repo, settings)
    assert summary2["ok"] is True and summary2["errors"] == 0
    assert await repo.get_total("tit") == 3
    assert not os.path.exists(db_path)
    assert os.path.exists(db_path + ".imported")
    await repo.close()


async def test_write_time_exception_is_caught_and_db_not_renamed(tmp_path):
    # A write-phase failure mid-loop (add_gate_entry raising) must be caught,
    # flag the summary failed, NOT raise out of run_migration_folder, and leave
    # the db in place (unrenamed) for retry.
    from n3x_bot.legacy_migrate import run_migration_folder

    mig_dir = os.path.join(str(tmp_path), "migration")
    os.makedirs(mig_dir)
    db_path = os.path.join(mig_dir, "bot_data.db")
    _build_v3_db(db_path)
    repo = await _dest_repo(tmp_path)

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated write failure")

    repo.add_gate_entry = _boom  # explode partway through the write body

    settings = types.SimpleNamespace(migration_dir=mig_dir)
    summary = await run_migration_folder(repo, settings)  # must NOT raise
    assert summary is not None and summary["ok"] is False
    assert summary["errors"] >= 1
    assert os.path.exists(db_path)                 # not renamed aside
    assert not os.path.exists(db_path + ".imported")
    await repo.close()


async def test_happy_path_still_reports_ok_and_renames(tmp_path):
    # Regression: a clean import stays a single run — ok True, errors 0, renamed.
    from n3x_bot.legacy_migrate import run_migration_folder

    mig_dir = os.path.join(str(tmp_path), "migration")
    os.makedirs(mig_dir)
    db_path = os.path.join(mig_dir, "bot_data.db")
    _build_v3_db(db_path)
    repo = await _dest_repo(tmp_path)
    settings = types.SimpleNamespace(migration_dir=mig_dir)

    summary = await run_migration_folder(repo, settings)
    assert summary["ok"] is True and summary["errors"] == 0
    assert os.path.exists(db_path + ".imported")
    second = await run_migration_folder(repo, settings)
    assert second is None
    await repo.close()


# ── Settings.migration_dir config ───────────────────────────────────────────

_BASE = dict(
    discord_token="tok",
    target_role_id=1,
    welcome_channel_id=2,
    reminder_channel_id=3,
    _env_file=None,
    _env_prefix="NONEXISTENT_",
)


def test_migration_dir_defaults_to_migration():
    s = Settings(**_BASE)
    assert s.migration_dir == "migration"  # RED: field absent


def test_migration_dir_read_from_env(monkeypatch):
    monkeypatch.setenv("MIGRATION_DIR", "/data/migration")
    s = Settings(
        discord_token="tok", target_role_id=1,
        welcome_channel_id=2, reminder_channel_id=3, _env_file=None)
    assert s.migration_dir == "/data/migration"  # RED: field absent


# ── wiring: _prepare invokes run_migration_folder ───────────────────────────

async def test_prepare_invokes_run_migration_folder(tmp_path, monkeypatch):
    # Light offline wiring check: patch the name _prepare should call and assert
    # it is invoked. monkeypatch.setattr raises AttributeError until __main__
    # imports run_migration_folder -> that IS the RED signal.
    import n3x_bot.__main__ as main_mod

    calls = []

    async def _spy(repo, settings):
        calls.append(repo)
        return None

    monkeypatch.setattr(main_mod, "run_migration_folder", _spy)  # RED: name absent

    data_file = os.path.join(str(tmp_path), "stats.json")
    settings = Settings(**_BASE, storage_backend="flatfile", data_file=data_file)
    repo = await main_mod._prepare(settings)
    assert calls, "run_migration_folder was not called by _prepare"
    await repo.close()
