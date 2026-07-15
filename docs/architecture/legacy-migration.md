# Architecture: Legacy v3 database migration

Turns `n3x-bot/tests/test_legacy_migrate.py` green. On startup the bot scans a
`migration/` folder; a v3 SQLite `bot_data.db` or a legacy flatfile is imported
into OUR current schema (v3 -> n3x mapping) and then renamed aside so it never
re-runs.

Two production symbols live in a NEW module `n3x_bot/legacy_migrate.py`:
`migrate_legacy_sqlite(repo, db_path)` and `run_migration_folder(repo, settings)`.
Plus a new `Settings.migration_dir` field and one wiring line in `__main__._prepare`.

## Tests this design satisfies
- `test_users_are_upserted_with_gate_username` — 4001 -> display_name "gamerA" (upsert from gate username map)
- `test_user_without_name_source_gets_synthetic_display_name` — 1001 -> "user_1001"
- `test_text_ids_are_int_coerced` — all TEXT ids `int()`-coerced; kodex message user resolves as int
- `test_user_stats_counts_are_recorded` — record_use per count; totals/per-user match
- `test_missing_stat_is_created_so_no_data_is_lost` — create-missing stat for `schlaganfall`
- `test_global_stats_table_is_skipped` — `tit` total rebuilt from user_stats (=3), not 99
- `test_target_stats_counts_are_recorded` — record_target_use per count
- `test_gate_entries_import` — gate_totals counts a=2, b=1
- `test_identical_gate_rows_are_not_deduped_during_migration` — dedup_window_seconds=0 keeps both a/46000 rows
- `test_delta_entry_imports_as_gate_d_with_laser_drop` — delta -> gate "d", rates["laser"]==100.0
- `test_voice_activity_imports` / `test_message_activity_imports` / `test_reaction_activity_imports` — add_activity metrics
- `test_streak_imports_with_correct_field_mapping` — set_streak field order
- `test_night_imports_with_correct_field_mapping` — set_night field order
- `test_kodex_confirmation_imports` / `test_kodex_message_imports` — confirm_kodex / save_kodex_message
- `test_known_achievement_is_unlocked` — voice_3600 unlocked
- `test_unknown_achievement_id_is_skipped` — a_1 / streak_3 not inserted
- `test_summary_counts_skipped_achievements` — summary["achievements_skipped"] >= 2
- `test_summary_reports_imported_gate_entries` — summary["gate_entries"] == 3
- `test_summary_reports_delta_count` — summary["delta"] == 1
- `test_migration_recomputes_achievements_from_counts` — 7001 voice=3700 -> voice_3600 via recompute
- `test_run_migration_folder_migrates_sqlite_and_renames_aside` — imports + renames `bot_data.db` -> `.imported`
- `test_run_migration_folder_is_idempotent_second_call_is_noop` — second call returns None, counts unchanged
- `test_run_migration_folder_returns_none_for_empty_dir` — empty dir -> None
- `test_run_migration_folder_returns_none_for_absent_dir` — missing dir -> None
- `test_run_migration_folder_routes_legacy_flatfile_to_json_importer` — stats.json -> json importer, non-None summary, file renamed aside
- `test_migration_dir_defaults_to_migration` — Settings.migration_dir default "migration"
- `test_migration_dir_read_from_env` — MIGRATION_DIR env override
- `test_prepare_invokes_run_migration_folder` — `__main__.run_migration_folder` name exists and is called by `_prepare`

## Confirmed repo signatures (from `storage/base.py` + `json_repo.py`)
- `set_streak(discord_id, current_streak, last_active_date, max_streak)` — CONFIRMED (base.py:192, json_repo.py:394)
- `set_night(discord_id, night_count, last_night_date)` — CONFIRMED (base.py:197, json_repo.py:408)
- `add_activity(discord_id, metric, amount) -> int` — CONFIRMED (base.py:184, json_repo.py:377)
- `record_use(discord_id, display_name, stat_key)` — raises `KeyError` if stat absent; internally upserts the user with `display_name` (json_repo.py:224-234)
- `record_target_use(target_discord_id, stat_key)` — raises `KeyError` if stat absent; does NOT create a user (json_repo.py:274)
- `add_gate_entry(gate_type, cost, user_id, username, dedup_window_seconds=30, laser_dropped=None, drops=None) -> bool` — passing `drops={"laser": bool}` populates both `drops` and the `laser_dropped` column (json_repo.py:291-309)
- `upsert_user(discord_id, display_name)` — does NOT auto-create for gate entries; must be called explicitly for gate/activity-only users
- `unlock_achievement(discord_id, achievement_id) -> bool`, `confirm_kodex(discord_id)`, `save_kodex_message(message_id, discord_id)`, `create_message(name, template)`, `create_stat(key, name, message_id=None, targeted=False)`, `get_stat(key)` — CONFIRMED
- Achievements recompute path is Discord-free: `achievements.sync_all_achievements(repo) -> dict` and `achievements.recompute_user_achievements(repo, discord_id)` take ONLY `repo` (no bot/settings/discord). Verified: they call `check_achievements` -> `user_metric_value` which reads repo only (achievements.py:122-278).

## Files to create

### `n3x_bot/legacy_migrate.py`
Imports: `os`, `sqlite3`, `logging`, `glob`; from `n3x_bot.storage.base import StatsRepository`;
from `n3x_bot.achievements import ACHIEVEMENTS, sync_all_achievements`;
from `n3x_bot.seed import migrate_legacy_json`.
Module logger: `log = logging.getLogger(__name__)`.

Symbols:

- `async def migrate_legacy_sqlite(repo: StatsRepository, db_path: str) -> dict`
  Reads the v3 db (read-only, best-effort) and writes into `repo` via the mapping
  below. Returns the summary dict. On unreadable/locked/malformed db, logs and
  returns a partial/empty summary (never raises to caller — startup must survive).

- `async def run_migration_folder(repo: StatsRepository, settings) -> dict | None`
  Folder detection + idempotent rename-aside. Uses ONLY `settings.migration_dir`.

- Private helpers (all module-level, no new public API):
  - `def _connect_ro(db_path) -> sqlite3.Connection | None` — `sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)`; return None on `sqlite3.Error`.
  - `def _rows(con, sql) -> list[tuple]` — execute, `fetchall()`; return `[]` on `sqlite3.Error` (tolerates a missing table in a malformed db).
  - `def _looks_like_legacy_flatfile(path) -> bool` — replicate `__main__._is_legacy_flatfile` logic locally (has a `*_count` key AND no `seq` key). Defined here to AVOID a circular import with `__main__` (which imports `run_migration_folder`).

## Files to modify

### `n3x_bot/config.py`
Add one field to `Settings` (place near `data_file`, line ~13):
`migration_dir: str = "migration"`.
pydantic-settings reads env `MIGRATION_DIR` case-insensitively by default (no
prefix), satisfying both config tests. The default-test passes `_env_prefix="NONEXISTENT_"`
so no env is read and the literal default applies. No validator changes needed.

### `n3x_bot/__main__.py`
- Add import: `from n3x_bot.legacy_migrate import run_migration_folder` (top, near line 8).
  This creates the module-global name the wiring test patches.
- In `_prepare`, AFTER the existing seed_defaults + `migrate_legacy_json` block
  (after line 56, before `return repo`), add:
  `await run_migration_folder(repo, settings)`.
  Must reference the module-global name (not a local import) so `monkeypatch.setattr`
  in `test_prepare_invokes_run_migration_folder` intercepts the call. Return value
  is intentionally not consumed by `_prepare` (summary is for logging only; add an
  optional `if summary: log.info(...)` — non-load-bearing).

## Data flow (representative: `migrate_legacy_sqlite`)

1. `con = _connect_ro(db_path)`; if None -> log warning, return empty summary.
2. Read all needed tables up front into memory via `_rows` (each independently
   tolerant of absence). SKIP `global_stats`, `gate_records`, `event_stats`,
   `last_messages`. Close the connection (all sqlite reads are sync + fast).
3. Build the display-name map: `names: dict[int, str]` from `gate_stats.username`
   and `delta_stats.username` (keyed by `int(user_id)`). Helper
   `name_for(uid) = names.get(uid) or f"user_{uid}"`.
4. Collect distinct user ids from every id-bearing migrated table (user_stats.user_id,
   target_stats.target_id, gate/delta.user_id, achievements.user_id,
   voice/message/reaction.user_id, streak/night.user_id, kodex_confirmations.user_id,
   kodex_messages.user_id-value). `upsert_user(uid, name_for(uid))` for each. This is
   what gives 4001 -> "gamerA" and 1001 -> "user_1001".
5. user_stats: for `(user_id, cmd, count)` — `await _ensure_stat(repo, cmd, targeted=False)`;
   loop `count` times `await repo.record_use(int(user_id), name_for(int(user_id)), cmd)`.
   (`global_stats` never read -> `tit` total is exactly 3.)
6. target_stats: for `(target_id, cmd, count)` — `await _ensure_stat(repo, cmd, targeted=True)`;
   loop `count` times `await repo.record_target_use(int(target_id), cmd)`.
7. gate_stats: for each row `await repo.add_gate_entry(gate_type, cost, int(user_id),
   username, dedup_window_seconds=0)`; increment `gate_entries` counter per row imported.
8. delta_stats: for each row `await repo.add_gate_entry("d", cost, int(user_id),
   username, dedup_window_seconds=0, drops={"laser": bool(laser_dropped)})`;
   increment `delta` counter.
9. achievements: `valid = {a.id for a in ACHIEVEMENTS}`; per `(user_id, ach_id)` —
   if `ach_id in valid`: `await repo.unlock_achievement(int(user_id), ach_id)`;
   else `achievements_skipped += 1`.
10. voice_stats -> `add_activity(int(id), "voice_seconds", total_seconds)`;
    message_stats -> `add_activity(int(id), "messages", total_messages)`;
    reaction_stats -> `add_activity(int(id), "reactions", reaction_count)`.
11. streak_stats -> `set_streak(int(id), current_streak, last_active_date, max_streak)`;
    night_stats -> `set_night(int(id), night_count, last_night_date)`.
12. kodex_confirmations -> `confirm_kodex(int(user_id))`;
    kodex_messages -> `save_kodex_message(int(message_id), int(user_id))`.
13. Recompute: `await sync_all_achievements(repo)`. It export_all's the now-populated
    repo, gathers every user with activity/gate/streak/night/achievements, and runs
    `check_achievements` per metric. This is what unlocks `voice_3600` for 7001
    (voice_seconds 3700 >= 3600) despite no explicit achievements row. Discord-free.
14. Return summary dict.

`_ensure_stat(repo, key, targeted)` helper: if `await repo.get_stat(key) is None`,
`msg = await repo.create_message(f"{key}_msg", <generic template with {count} (+ {target} when targeted)>)`,
then `await repo.create_stat(key, key.capitalize(), message_id=msg.id, targeted=targeted)`.
Generic template content is not asserted by any test; pick a sensible German
placeholder-bearing string.

### Summary dict construction
Build incrementally; MUST contain the three pinned keys:
```
{"gate_entries": <count of gate_stats rows imported>,   # == 3
 "delta": <count of delta_stats rows imported>,          # == 1
 "achievements_skipped": <count of unknown ach ids>,     # >= 2
 # additional non-asserted keys OK for logging: "users", "user_stats", "targets",
 # "activity", "streaks", "nights", "kodex", "achievements_unlocked"}
```
`gate_entries` counts ONLY a/b/c gate_stats rows; delta is separate (test pins both).

## Data flow (`run_migration_folder`)
1. `d = settings.migration_dir`; if `not os.path.isdir(d)` -> return None (absent-dir test).
2. `db_path = os.path.join(d, "bot_data.db")`.
3. If `os.path.exists(db_path)`:
   - `summary = await migrate_legacy_sqlite(repo, db_path)`
   - `os.replace(db_path, db_path + ".imported")` (idempotency: next call won't see it)
   - DECISION (sqlite wins): if a legacy `*.json` also exists in `d`, log a warning
     and rename it aside (`<name>.imported`) WITHOUT importing it — sqlite "wins",
     json loses and is neutralized so it can never surprise-import later.
   - return `summary`.
4. Else scan for a legacy flatfile: first `glob(os.path.join(d, "*.json"))` entry
   for which `_looks_like_legacy_flatfile(path)` is True:
   - `await migrate_legacy_json(repo, path)` (reuses `seed.migrate_legacy_json`)
   - `os.replace(path, path + ".imported")`
   - return a NON-None dict, e.g. `{"source": "flatfile", "path": path}`
     (`migrate_legacy_json` itself returns None, but the test asserts `summary is not None`).
5. Else return None (empty-dir test).
Idempotency is purely rename-based: after a successful import the source no longer
matches, so the second call falls through to `return None`.

## Dependencies
- New packages: NONE. `sqlite3`, `os`, `glob`, `logging`, `json` are stdlib.
- Internal modules: `storage.base.StatsRepository`, `achievements.{ACHIEVEMENTS, sync_all_achievements}`,
  `seed.migrate_legacy_json`. `__main__` gains a dependency on `legacy_migrate`
  (one-directional; `legacy_migrate` must NOT import `__main__`).

## Build sequence (for the Coder)
1. `config.py`: add `migration_dir`. -> greens `test_migration_dir_defaults_to_migration`,
   `test_migration_dir_read_from_env`.
2. `legacy_migrate.py` skeleton with `_connect_ro`, `_rows`, `_looks_like_legacy_flatfile`,
   `_ensure_stat`, and `migrate_legacy_sqlite` implementing steps 1-14 above.
   -> greens all `migrate_legacy_sqlite` destination-state + summary tests
   (users, ids, user_stats, missing-stat, global-skip, target, gate, dedup=0, delta,
   activity x3, streak, night, kodex x2, known/unknown achievement, 3 summary tests,
   recompute test).
3. `run_migration_folder` in the same module (folder detect + rename-aside + json route).
   -> greens the 5 `run_migration_folder` tests.
4. `__main__.py`: import `run_migration_folder`, call it in `_prepare` after the
   existing legacy handling. -> greens `test_prepare_invokes_run_migration_folder`.
5. Run the focused suite: `pytest n3x-bot/tests/test_legacy_migrate.py` (per MEMORY:
   focused tests, not the full suite).

## Risks and open questions
- DECISION — "both sources present" (sqlite + legacy json): I pinned "sqlite wins"
  literally — the json is renamed aside UNIMPORTED (its data is dropped). Trade-off:
  deterministic single-source-per-run and no surprise second-run import, but the json
  operator-dropped data is silently discarded. Alternative (import json on a later run)
  reopens the ambiguity. No test covers this; flag for the user to confirm the
  discard-json interpretation. Easy to flip to "import-both-across-runs" if preferred.
- Recompute breadth: `sync_all_achievements` recomputes ALL affected users, so gate/
  activity users beyond 7001 may gain achievements our v3 definitions imply (e.g. 4001
  gets `total_1` from gate_total>=1). This is correct-by-design and unasserted, but
  means the migration can unlock achievements the source db never had. Called out so
  it is not mistaken for a bug.
- `_ensure_stat` targeted flag: user_stats create-missing uses `targeted=False`,
  target_stats uses `targeted=True`. `record_use`/`record_target_use` do not enforce
  the flag, so this only affects future display semantics, not the tests. If the SAME
  key appears in both user_stats and target_stats (not in the fixture), the first
  `_ensure_stat` wins and the flag is whatever ran first — acceptable, flagged.
- The generic create-missing message template content is unspecified by the tests;
  Coder picks a `{count}`-bearing German string consistent with `seed.LEGACY_STATS`.
- Best-effort db reads: a locked/corrupt `bot_data.db` must not crash startup. Every
  sqlite call is wrapped; `migrate_legacy_sqlite` returns a partial/empty summary and
  logs. This means a partially-readable db imports what it can rather than aborting —
  confirm that "best-effort partial import" is the desired failure mode (task said so).
- `__main__` real-run behavior: with `migration_dir` defaulting to "migration",
  production startup now stats a `migration/` dir every boot. Harmless when absent
  (returns None). No test exercises the real (non-spied) path in `_prepare`.
