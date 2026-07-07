# Architecture: Data Migration (export/import + repo→repo migrate + CLI)

Turns the RED suites in `tests/storage/test_export_import_contract.py` and
`tests/test_migrate.py` GREEN. All paths below are relative to
`/home/isikerkan/n3x/n3x-bot`.

## Tests this design satisfies

Export/import contract (`tests/storage/test_export_import_contract.py`, parametrized json/sqlite/postgres):
- `test_export_all_returns_json_serializable_snapshot` — `export_all()` output survives `json.dumps`.
- `test_round_trip_preserves_active_and_archived_users` — users 1001/1002/1003 preserved incl. archived flag.
- `test_round_trip_preserves_messages_including_archived` — greet/old, template + archived flag.
- `test_round_trip_preserves_stat_flags_and_message_link` — `tit.message_id == greet.id`, `smart.targeted`, `dead.archived_at`.
- `test_round_trip_preserves_user_stats_for_multiple_users` — per-user counts.
- `test_round_trip_preserves_stat_totals` — total 3.
- `test_round_trip_preserves_target_stats_for_multiple_targets` — 2001→2, 2002→1.
- `test_round_trip_preserves_stat_last_post` — (55501, 66601).
- `test_round_trip_preserves_gate_entries_and_totals` — costs [46000,48000], count 2, avg 47000.
- `test_snapshot_is_stable_after_round_trip` — `dest.export_all() == source snapshot` (id + timestamp + gate metadata fidelity).

Migrate + CLI (`tests/test_migrate.py`):
- `test_migrate_flatfile_to_sqlite_preserves_message_link`
- `test_migrate_flatfile_to_sqlite_preserves_stat_flags`
- `test_migrate_flatfile_to_sqlite_preserves_user_counts`
- `test_migrate_flatfile_to_sqlite_preserves_target_stats`
- `test_migrate_flatfile_to_sqlite_preserves_gate_entries`
- `test_migrate_flatfile_to_sqlite_full_snapshot_equal` — **cross-backend** `sqlite.export_all() == json.export_all()`.
- `test_migrate_flatfile_to_postgres_full_snapshot_equal` — same, gated on `TEST_POSTGRES_URL`.
- `test_run_migration_flatfile_to_sqlite_copies_all_data`
- `test_run_migration_refuses_nonempty_dest_without_overwrite` — raises `DestinationNotEmptyError`.
- `test_run_migration_overwrite_replaces_nonempty_dest` — clears then migrates.
- `test_migrate_module_exposes_cli_entrypoint` — `main`, `run_migration`, `migrate` all callable.

## Files to create

- `n3x_bot/migrate.py` — the migration module.
  - `class DestinationNotEmptyError(Exception)` — raised when dest holds data and `overwrite=False`.
  - `async def migrate(source: StatsRepository, dest: StatsRepository, *, overwrite: bool = False) -> None` — snapshot source, guard/clear dest, import into dest. Both repos must already be connected.
  - `async def run_migration(*, from_backend: str, from_location: str, to_backend: str, to_location: str, overwrite: bool = False) -> None` — build both repos from (backend, location), connect, `migrate`, close both in `finally`.
  - `def _build_repo(backend: str, location: str) -> StatsRepository` — `"flatfile" -> JsonRepository(location)`, else `SqlRepository(location)`. (Private helper.)
  - `def _has_data(snapshot: dict) -> bool` — non-empty detection (see Data flow).
  - `def main() -> None` — argparse entrypoint; `asyncio.run(run_migration(...))`.

## Files to modify

- `n3x_bot/storage/base.py` — add three abstract methods to `StatsRepository` (after the gate section, ~line 116):
  - `async def export_all(self) -> dict: ...`
  - `async def import_all(self, snapshot: dict) -> None: ...`
  - `async def clear(self) -> None: ...`
  - All three MUST be implemented in BOTH concrete repos in the same change, otherwise the classes become abstract and every existing storage test (which instantiates them via the conftest factories) fails at construction with `TypeError`.

- `n3x_bot/storage/json_repo.py` — add `export_all`, `import_all`, `clear`. Add `import copy` (or reuse `json`) for deep-copying snapshot structures. No changes to existing methods.

- `n3x_bot/storage/sql_repo.py` — add `export_all`, `import_all`, `clear`. Reuse existing `_as_aware_utc`; add a private `_parse_dt` helper mirroring json_repo (`datetime.fromisoformat(v) if v else None`). Import `text` and `func` (func already imported) from sqlalchemy for the postgres `setval` and max-id queries. No changes to existing methods.

- `tests/storage/conftest.py` — NO change needed. The `sqlite` backend is already appended (line 89) and `postgres` is conditional (line 105). The comment at line 19 ("appended by Task 5/6") is stale but harmless. Do not touch.

## The exact snapshot schema (`export_all` returns)

Identical structure and values across json/sqlite/postgres so `dest.export_all() == source.export_all()` holds by `==`.

```
{
  "users": [ {"id": int, "discord_id": int, "display_name": str,
              "archived_at": str|None, "created_at": str|None}, ... ],   # ORDER BY id ASC
  "messages": [ {"id": int, "name": str, "template": str,
                 "archived_at": str|None, "created_at": str|None}, ... ], # ORDER BY id ASC
  "stats": [ {"id": int, "key": str, "name": str, "message_id": int|None,
              "targeted": bool, "archived_at": str|None,
              "created_at": str|None}, ... ],                             # ORDER BY id ASC
  "user_stats":  { "<user_id>": { "<stat_id>": int } },                  # nested, STRING keys
  "stat_totals": { "<stat_id>": int },                                   # STRING keys
  "stat_last_post": { "<stat_id>": [discord_message_id:int, channel_id:int] },  # value is a LIST
  "target_stats": { "<stat_id>": { "<target_discord_id>": int } },       # nested, STRING keys
  "gate_entries": [ {"id": int, "gate_type": str, "cost": int,
                     "user_id": int, "username": str,
                     "created_at": str|None}, ... ],                      # ORDER BY id ASC
  "seq": {"user": int, "message": int, "stat": int, "gate": int},
}
```

Field-representation rules (critical for cross-backend `==`):
- **Datetimes** (`archived_at`, `created_at`): ISO-8601 strings or `None`, never `datetime` objects.
  - JSON backend already stores them as ISO strings internally (`_now()` / `_parse_dt`), so pass them through verbatim.
  - SQL backend converts each read-back value with `_as_aware_utc(dt).isoformat()` (or `None`). `_as_aware_utc` is required so sqlite's naive read-back becomes `+00:00`-suffixed, matching the json string exactly; postgres is already aware. This is lossless at microsecond precision, so a value written by json `_now()` re-emerges byte-identical after a json→sqlite→export round trip.
- **Map keys** (`user_stats`, `stat_totals`, `stat_last_post`, `target_stats`): always Python `str` (JSON object keys are strings; json backend already uses str keys, SQL must wrap ids with `str(...)`).
- **`stat_last_post` value**: a two-element **list** `[dmid, cid]`, NOT a tuple — `[a,b] == (a,b)` is `False`, and the json backend stores a list.
- **List ordering**: `users`, `messages`, `stats`, `gate_entries` ordered by `id` ascending. json lists are already insertion(=id) ordered; SQL must `ORDER BY id ASC`.
- **`targeted`**: Python `bool`.
- **`seq`**: computed identically on BOTH backends as `max(id) or 0` per table (see Risks for why not the json internal `_db["seq"]`).

## `export_all` mechanics per backend

**JSON** — mostly a deep copy of `_db`:
- `users`/`messages`/`stats`/`gate_entries`: `copy.deepcopy` of `_db[table]` (already the exact dict shape; sort defensively by `id` — they are already ordered).
- `user_stats`/`stat_totals`/`stat_last_post`/`target_stats`: `copy.deepcopy` of `_db[table]`.
- `seq`: `{"user": max_id(users), "message": max_id(messages), "stat": max_id(stats), "gate": max_id(gate_entries)}` where `max_id(rows) = max((r["id"] for r in rows), default=0)`.

**SQL** — one read connection, assemble dicts:
- Query each table (`select(...).order_by(id)` for the four id-ordered lists), map rows to dicts, converting datetimes via `_as_aware_utc(...).isoformat()`.
- `user_stats`: `select(user_id, stat_id, count)` → `{str(user_id): {str(stat_id): count}}` (build nested).
- `stat_totals`: `select(stat_id, count)` → `{str(stat_id): count}`.
- `stat_last_post`: `select(stat_id, discord_message_id, channel_id)` → `{str(stat_id): [dmid, cid]}`.
- `target_stats`: `select(stat_id, target_discord_id, count)` → `{str(stat_id): {str(target_discord_id): count}}`.
- `seq`: four `select(func.max(<table>.c.id))` (or one query each), coalescing `None -> 0`.

## `import_all` mechanics per backend

Precondition (both): repo is connected and EMPTY (the fixtures/`migrate` guarantee this).

**JSON**:
1. Deep-copy snapshot lists/maps straight into `_db`:
   - `_db["users"] = deepcopy(snapshot["users"])`, same for `messages`, `stats`, `gate_entries`.
   - `_db["user_stats"|"stat_totals"|"stat_last_post"|"target_stats"] = deepcopy(snapshot[...])`.
2. `_db["seq"] = dict(snapshot["seq"])` — restores id counters so future `create_*` never collide with imported ids (next id = max+1).
3. `self._flush()`.
   The stored shapes are identical to what json writes natively (ISO-string datetimes, str map keys, list `stat_last_post`), so no conversion is required.

**SQL** — one `async with self.engine.begin() as conn:` transaction, insert in FK-safe order with EXPLICIT ids/timestamps:
1. `messages` — insert each row with explicit `id`, `name`, `template`, `archived_at=_parse_dt(...)`, `created_at=_parse_dt(...)`.
2. `users` — explicit `id`, `discord_id`, `display_name`, parsed `archived_at`/`created_at`.
3. `stats` — explicit `id`, `key`, `name`, `message_id`, `targeted`, parsed datetimes. (After messages, so `message_id` FK resolves.)
4. `user_stats` — for `uid,inner` in map, for `sid,count`: insert `user_id=int(uid), stat_id=int(sid), count=count`.
5. `stat_totals` — insert `stat_id=int(sid), count`.
6. `stat_last_post` — insert `stat_id=int(sid), discord_message_id=v[0], channel_id=v[1]`.
7. `target_stats` — for `sid,inner`, for `tid,count`: insert `target_discord_id=int(tid), stat_id=int(sid), count`.
8. `gate_entries` — explicit `id`, `gate_type`, `cost`, `user_id`, `username`, parsed `created_at`.
9. **Sequence fixup** (identity/serial): only for postgres. For each autoincrement table (`users`, `messages`, `stats`, `gate_entries`) whose `seq[...] > 0`, run
   `text("SELECT setval(pg_get_serial_sequence(:t, 'id'), :v)")` with the corresponding `seq` value, so the serial's next value is `seq+1` and future inserts don't collide with the explicit ids.
   Gate on `self.engine.dialect.name == "postgresql"`. sqlite needs NO fixup (see Risks).

Datetime handling: `_parse_dt(s) = datetime.fromisoformat(s) if s else None` yields an aware-UTC datetime (json emits `+00:00` strings); binding an aware datetime into `DateTime(timezone=True)` is exactly what existing `create_*` already do via `_now()`, so this is a proven path.

## `clear` mechanics per backend

**JSON**: `self._db = self._empty(); self._flush()`.

**SQL**: one `engine.begin()` transaction, `DELETE` from all tables in FK-safe (child→parent) order:
`gate_entries`, `user_stats`, `stat_totals`, `stat_last_post`, `target_stats`, `stats`, `users`, `messages`.
(No sequence reset needed here — the subsequent `import_all` re-inserts explicit ids and, for postgres, runs `setval`.)

## Data flow (representative: `run_migration` flatfile→sqlite, overwrite)

1. CLI/`main` parses args → `run_migration(from_backend="flatfile", from_location=<path>, to_backend="sqlite", to_location=<url>, overwrite=True)`.
2. `_build_repo` → `JsonRepository(path)` and `SqlRepository(url)`.
3. `await source.connect()` (loads json), `await dest.connect()` (creates schema).
4. `migrate(source, dest, overwrite=True)`:
   a. `snapshot = await source.export_all()` — full json snapshot (ISO datetimes, str keys, `seq`).
   b. `dest_snapshot = await dest.export_all()`; `_has_data(dest_snapshot)` → `bool(dest_snapshot["users"] or ["messages"] or ["stats"] or ["gate_entries"])`.
   c. non-empty + `overwrite=True` → `await dest.clear()`. (If `overwrite=False` → `raise DestinationNotEmptyError`.)
   d. `await dest.import_all(snapshot)` — explicit-id inserts in FK order + postgres setval.
5. `finally`: `await source.close()`, `await dest.close()`.

## Dependencies

- New packages: **none**. Uses stdlib `argparse`, `asyncio`, `copy`, `datetime`, and existing SQLAlchemy Core / `sqlalchemy.text`.
- Internal modules `n3x_bot/migrate.py` depends on: `storage.base.StatsRepository`, `storage.json_repo.JsonRepository`, `storage.sql_repo.SqlRepository`. It does **not** depend on `config.Settings`/`factory.create_repository` (see Risks — Settings has unrelated required fields).

## Build sequence (for the Coder)

1. `base.py`: add abstract `export_all`, `import_all`, `clear` to `StatsRepository`.
2. `json_repo.py`: implement `export_all` (deepcopy + computed `seq`), `import_all` (assign into `_db` + restore `seq` + `_flush`), `clear`. Run `tests/storage/test_export_import_contract.py -k json` and `tests/storage/test_repository_contract.py` → json green.
3. `sql_repo.py`: add `_parse_dt`; implement `export_all` (queries + `_as_aware_utc().isoformat()` + str keys + list `stat_last_post` + computed `seq`), `import_all` (FK-ordered explicit-id inserts + postgres `setval`), `clear` (FK-ordered deletes). Run the contract suite → sqlite green (and postgres if DSN set).
4. `migrate.py`: `DestinationNotEmptyError`, `_build_repo`, `_has_data`, `migrate`, `run_migration`, `main` (argparse: `--from`/`--to` with `choices=["flatfile","sqlite","postgres"]` and `dest=`, `--from-location`, `--to-location`, `--overwrite` store_true). Run `tests/test_migrate.py` → green.
5. Full run: `cd n3x-bot && .venv/bin/python -m pytest -q` (optionally with `TEST_POSTGRES_URL`). Confirm coverage ≥ 80%.

## Risks and open questions

- **`seq` cross-backend equality.** json's internal `_db["seq"]` is a monotonic watermark that, after row deletions, can exceed `max(id)`; the SQL backend has no equivalent portable watermark. To guarantee `json.export_all() == sql.export_all()` I compute `seq = max(id) or 0` on BOTH backends rather than reading `_db["seq"]`. In the seeded fixtures (no deletes) these are identical, so all tests pass. Trade-off: a json source that had deletions would export the max-id, not its higher watermark, so a migrated repo could reuse a previously-deleted id. Untested and acceptable (a deleted id is free); flagged for visibility. If exact watermark fidelity is later required, `seq` must move out of the equality-checked snapshot or the SQL side must read `sqlite_sequence`/`pg_sequences`.
- **sqlite identity reset.** The schema uses `Integer primary_key autoincrement=True` WITHOUT `sqlite_autoincrement=True`, so sqlite emits a plain `INTEGER PRIMARY KEY` (rowid alias): after explicit-id inserts, the next auto id is `max(rowid)+1` automatically. Therefore NO sqlite sequence fixup is needed. Only postgres (SERIAL + sequence) requires `setval`.
- **postgres sequence reset** must run only under `dialect.name == "postgresql"` and only for tables with `seq > 0`; guarding on empty tables avoids `setval(seq, 0)` edge cases.
- **Datetime precision.** Correctness of the full-snapshot-equality tests hinges on `json _now()` ISO string == `_as_aware_utc(sql_read_back).isoformat()`. This holds because SQLAlchemy's sqlite DateTime stores/reads 6-digit microseconds and the existing code already binds aware UTC datetimes. No sub-second truncation risk observed; called out because it is the single most fragile equality.
- **`Settings`/`factory` reuse.** The task suggests reusing `Settings`/`create_repository`, but `Settings` has unrelated REQUIRED fields (`discord_token`, `target_role_id`, `welcome_channel_id`, `reminder_channel_id`) with no defaults, and `run_migration` is called in tests without those env vars. Building a full `Settings` would raise `ValidationError`. Decision: `_build_repo` constructs `JsonRepository`/`SqlRepository` directly (two-line mapping mirroring `factory.create_repository`). This is the only way the migrate tests pass without a populated environment.
- **`gate_entries` id preservation** is handled by explicit-id inserts + `list_gate_costs`/`delete_gate_entry` ordering-by-id, so insertion order and dedup metadata survive; verified by `test_round_trip_preserves_gate_entries_and_totals` and the stable-snapshot test.
- **Empty-table edge cases** (e.g. no gate entries) are handled: empty lists/maps produce no inserts, `seq` entries are `0`, postgres `setval` is skipped. Not directly tested but must not raise.
- **`import_all` empty-repo precondition.** `import_all` assumes an empty dest and does explicit-id inserts (would violate PK/unique on a populated repo). `migrate` enforces this by refusing or clearing first; `import_all` itself does not re-check. Acceptable given it is only reached through `migrate` and the round-trip fixtures.
