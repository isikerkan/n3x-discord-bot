# Architecture: Phase 2a — achievement definitions as DB-backed objects

Foundation slice. Mirrors the Phase-1 `content_texts` precedent exactly. When the
new `achievement_defs` table is empty (prod state until 2b seeds it), every
behaviour is identical to today: the code-default `ACHIEVEMENTS` list is used.

## Tests this design satisfies

Storage contract — `tests/storage/test_achievement_defs_contract.py` (json + sqlite + postgres):
- `test_get_achievement_def_unknown_id_returns_none`
- `test_set_achievement_def_roundtrips_all_fields`
- `test_get_achievement_def_returns_dict_with_all_seven_keys`
- `test_threshold_round_trips_as_int`
- `test_secret_round_trips_as_bool`
- `test_color_defaults_to_none_when_omitted`
- `test_set_achievement_def_upserts_same_id`
- `test_multiple_ids_are_independent`
- `test_set_one_id_leaves_other_ids_unset`
- `test_delete_achievement_def_returns_true_when_present`
- `test_delete_achievement_def_removes_the_id`
- `test_delete_achievement_def_returns_false_when_absent`
- `test_all_achievement_defs_empty_by_default`
- `test_all_achievement_defs_returns_every_row`
- `test_all_achievement_defs_rows_have_all_seven_keys`
- `test_all_achievement_defs_is_ordered_by_id`
- `test_export_all_includes_achievement_defs_and_is_json_serializable`
- `test_round_trip_preserves_achievement_defs`
- `test_snapshot_is_stable_after_achievement_defs_round_trip`
- `test_clear_wipes_achievement_defs`
- `test_migrate_data_tables_includes_achievement_defs`

Resolver + wiring — `tests/test_achievement_defs.py`:
- default resolver mirrors code list / total 83 / total==len(all)
- `for_metric` (filter + unknown→[]), `by_id` (hit + miss), `metrics()` sorted-unique
- explicit-defs constructor uses given list (identity preserved)
- `refresh`: empty table→code defaults; DB rows→total replacement; never raises + skips malformed row
- `load` classmethod (refreshed + empty-table behaviour-preserving)
- end-to-end 83+1 → total 84 flows into `build_overview_embed(total=...)` "/84"
- `build_bot` attaches `bot.achievement_defs` defaulting to 83
- `on_ready` refreshes from DB (→84) and keeps defaults when table empty

Consumer extensions:
- `tests/test_achievements.py`: `Achievement.color` trailing field (default None; code seed all None); `check_achievements(..., defs=)`; `recompute_user_achievements(..., defs=)`
- `tests/test_cards.py`: `tier_color` honours valid `#RRGGBB`; None/blank/malformed fall back unchanged; `#010203`→`(1,2,3)`
- `tests/test_overview.py`: `build_overview_embed(..., total=)` (override + None→module total)
- `tests/test_voice_roles.py`: `voice_role_transition(..., defs=)`
- `tests/test_bot_wiring.py`: `bot.achievement_defs` attribute, total 83 (no command-count change)

## Files to create

- `n3x_bot/achievement_defs.py` — the resolver, mirroring `n3x_bot/content.py::ContentTexts`.
  - `class AchievementDefs`:
    - `__init__(self, defs: list[Achievement] | None = None)` → stores `list(defs) if defs is not None else list(ACHIEVEMENTS)`. (Uses `list(ACHIEVEMENTS)` so the module constant is never mutated; explicit-list identity in `test_explicit_defs_constructor_uses_given_list` is preserved because the *elements* are the same objects — `by_id` returns `is one`.)
    - `all(self) -> list[Achievement]` → returns the internal list (may return the list itself or a shallow copy; tests only read it).
    - `total` property → `len(self._defs)`.
    - `for_metric(self, metric: str) -> list[Achievement]` → `[a for a in self._defs if a.metric == metric]`.
    - `by_id(self, aid: str) -> Achievement | None` → first match or None.
    - `metrics(self) -> list[str]` → `sorted({a.metric for a in self._defs})`.
    - `async refresh(self, repo) -> None` → see "Row→Achievement conversion" below. Never raises.
    - `@classmethod async load(cls, repo) -> "AchievementDefs"` → `inst = cls(); await inst.refresh(repo); return inst`.
  - Imports: `from n3x_bot.achievements import ACHIEVEMENTS, Achievement`.

## Files to modify

- `n3x_bot/achievements.py`
  - Dataclass (lines 10-17): add trailing field `color: str | None = None`. It MUST be last with a default so existing `Achievement(...)` calls in `_build_achievements()` (lines 34-105) that omit it still construct. Frozen dataclass stays frozen. No other change to the seed — `test_code_default_achievements_have_no_color` asserts every seeded def has `color is None`.
  - `check_achievements` (line 144): add trailing param `defs: list[Achievement] | None = None`. First line of body: `source = defs if defs is not None else ACHIEVEMENTS`. Rename the current local `defs = [a for a in ACHIEVEMENTS ...]` (line 147) to `metric_defs = [a for a in source if a.metric == metric]` and update the three downstream references (lines 150, 153, 155). No behaviour change when `defs is None`.
  - `recompute_user_achievements` (line 255): add trailing param `defs: list[Achievement] | None = None`. Body: `source = defs if defs is not None else ACHIEVEMENTS`; `metrics = sorted({a.metric for a in source})`; forward into the loop as `await check_achievements(repo, discord_id, metric, defs=source)`.
  - `build_overview_embed` (line 164): add trailing param `total: int | None = None`. At the top of the non-empty branch: `denom = total if total is not None else TOTAL_ACHIEVEMENTS`. Replace the two `TOTAL_ACHIEVEMENTS` uses inside the function body (line 175 `filled` calc guard, line 184 f-string) with `denom`. The empty-`user_ids` early return (lines 166-170) is unaffected. NOTE the guard `if TOTAL_ACHIEVEMENTS else 0` becomes `if denom else 0`.
  - Live call sites (`register_achievement_commands`/`_erfolge` at 281-318, `post_overview`/`handle_overview_reaction`) STAY on module `ACHIEVEMENTS`/`TOTAL_ACHIEVEMENTS` — see decision below.

- `n3x_bot/cards.py`
  - Add module-private helper `_parse_hex_color(value: str | None) -> tuple[int, int, int] | None`. Contract: returns `(r, g, b)` only for a well-formed `"#RRGGBB"` string (leading `#`, exactly 6 hex digits); returns `None` for `None`, empty/blank, or any malformed value. Must NOT raise (wrap the `int(part, 16)` parse; guard length + hexness). `"#010203"` → `(1, 2, 3)`.
  - `tier_color` (line 54): prepend `parsed = _parse_hex_color(achievement.color); if parsed is not None: return parsed`. The existing gate/category derivation (lines 55-57) stays as the fallback, unchanged. `achievement.color` is always present now (dataclass default None).

- `n3x_bot/activity.py`
  - `voice_role_transition` (line 60): add trailing param `defs: list[Achievement] | None = None`. Body first line: `source = defs if defs is not None else ACHIEVEMENTS`. In the `max(...)` key (line 66) replace `ACHIEVEMENTS` with `source`. `apply_voice_roles` (line 72) keeps calling with the default (module list) in 2a. `Achievement` is already imported here.

- `n3x_bot/storage/schema.py`
  - Add after `content_texts` (line 138):
    ```
    achievement_defs = Table(
        "achievement_defs", metadata,
        Column("id", String(50), primary_key=True),
        Column("category", String(50), nullable=False),
        Column("metric", String(50), nullable=False),
        Column("threshold", Integer, nullable=False),
        Column("title", Text, nullable=False),
        Column("secret", Boolean, nullable=False),
        Column("color", String(50), nullable=True),
    )
    ```
  - `id String(50)` matches `achievements.achievement_id` / `content_texts.key`. `title` uses `Text` (mirrors `messages.template`). `secret` is `Boolean` non-null (mirrors `stats.targeted`); a coercion note is below. `color` is nullable `String(50)`. `String`/`Integer`/`Text`/`Boolean` are already imported at the top of the file.

- `n3x_bot/storage/base.py`
  - Add four abstract methods after the "content texts" block (after line 136), in a new `# achievement definitions` section, mirroring the content_texts abstract signatures:
    - `async def set_achievement_def(self, id: str, *, category: str, metric: str, threshold: int, title: str, secret: bool, color: str | None = None) -> None`
    - `async def get_achievement_def(self, id: str) -> dict | None`
    - `async def delete_achievement_def(self, id: str) -> bool`
    - `async def all_achievement_defs(self) -> list[dict]`
  - Keyword-only (`*`) after `id` matches the test call style (`set_achievement_def(aid, category=..., ...)`) and the resolver seeding helper.

- `n3x_bot/storage/json_repo.py`
  - `_empty()` (lines 40-52): add `"achievement_defs": {},` alongside `"content_texts": {}`. Shape: a dict keyed by id → inner 6-field dict `{"category","metric","threshold","title","secret","color"}` (id is the key, not duplicated inside). JSON natively preserves `int`/`bool`/`None`, so no coercion needed in this backend.
  - Add a `# ── achievement definitions ──` section after `all_content_texts` (after line 314):
    - `set_achievement_def(id, *, category, metric, threshold, title, secret, color=None)` → `self._db["achievement_defs"][id] = {"category": category, "metric": metric, "threshold": threshold, "title": title, "secret": secret, "color": color}`; `self._flush()`. Upsert semantics come free from dict assignment.
    - `get_achievement_def(id)` → `row = self._db["achievement_defs"].get(id); return None if row is None else {"id": id, **row}` (reconstruct the 7-key dict).
    - `delete_achievement_def(id)` → mirror `delete_content_text`: `existed = id in ...; pop; flush; return existed`.
    - `all_achievement_defs()` → `return [{"id": k, **self._db["achievement_defs"][k]} for k in sorted(self._db["achievement_defs"])]`. `sorted(...)` over the string keys guarantees id-ascending.
  - `export_all` (near line 574): add `"achievement_defs": copy.deepcopy(self._db["achievement_defs"]),`.
  - `import_all` (near line 605): add `self._db["achievement_defs"] = copy.deepcopy(snapshot.get("achievement_defs", {}))` (use `.get` default `{}` so older snapshots import).
  - `clear()` is `self._db = self._empty()` (line 610-612) — covered automatically by the `_empty()` addition.

- `n3x_bot/storage/sql_repo.py`
  - Add a `# ── achievement definitions ──` section after `all_content_texts` (after line 382), mirroring content_texts but with the multi-column value dict and the `_upsert` helper:
    - `set_achievement_def(id, *, category, metric, threshold, title, secret, color=None)`:
      `async with self.engine.begin() as conn: await self._upsert(conn, sc.achievement_defs, {"id": id}, {"category": category, "metric": metric, "threshold": threshold, "title": title, "secret": secret, "color": color})`.
    - `get_achievement_def(id)`:
      `async with self.engine.connect() as conn:` select the row `where sc.achievement_defs.c.id == id`; `one_or_none()`; if None return None else return the 7-key dict with coercion: `{"id": r.id, "category": r.category, "metric": r.metric, "threshold": int(r.threshold), "title": r.title, "secret": bool(r.secret), "color": r.color}`.
    - `delete_achievement_def(id)`: mirror `delete_content_text` (lines 371-377) — existence probe via `select(sc.achievement_defs.c.id)`, `delete(...)`, return `exists is not None`.
    - `all_achievement_defs()`:
      `async with self.engine.connect() as conn:` `select(sc.achievement_defs).order_by(sc.achievement_defs.c.id.asc())`; return `[{"id": r.id, "category": r.category, "metric": r.metric, "threshold": int(r.threshold), "title": r.title, "secret": bool(r.secret), "color": r.color} for r in rows]`. `.order_by(...id.asc())` guarantees id-ascending (mirrors the `users`/`gate_entries` export ordering pattern).
  - `export_all` (build the local var near line 806, add to the returned dict near line 826): emit `achievement_defs` in the SAME shape as the json backend — a dict keyed by id → 6-field inner dict, so cross-backend `import_all` is symmetric:
    ```
    achievement_defs = {
        r.id: {"category": r.category, "metric": r.metric,
               "threshold": int(r.threshold), "title": r.title,
               "secret": bool(r.secret), "color": r.color}
        for r in await conn.execute(select(sc.achievement_defs))
    }
    ```
    Add `"achievement_defs": achievement_defs,` to the returned dict.
  - `import_all` (near line 903, next to the content_texts loop): 
    ```
    for aid, v in snapshot.get("achievement_defs", {}).items():
        await conn.execute(insert(sc.achievement_defs).values(
            id=aid, category=v["category"], metric=v["metric"],
            threshold=v["threshold"], title=v["title"],
            secret=v["secret"], color=v.get("color")))
    ```
  - `clear()` (line 914-924): add `sc.achievement_defs` to the delete tuple (order doesn't matter — no FKs reference it).

- `n3x_bot/migrate.py`
  - `_DATA_TABLES` (lines 25-31): append `"achievement_defs"` (e.g. on the last line next to `"content_texts"`).

- `n3x_bot/bot.py`
  - Import: add `from n3x_bot.achievement_defs import AchievementDefs` (next to the `from n3x_bot.content import ContentTexts` at line 33).
  - `build_bot` (after line 106 `bot.content_texts = ContentTexts()`): add `bot.achievement_defs = AchievementDefs()`.
  - `on_ready` (after the content_texts refresh try/except at lines 815-818): add a parallel guard:
    ```
    try:
        await bot.achievement_defs.refresh(repo)
    except Exception:
        log.exception("achievement_defs refresh failed; using defaults")
    ```
    (`refresh` is already defensive; the try/except mirrors the sibling wiring and is what the test's "keeps defaults when table empty" expects.)

## Row→Achievement conversion + malformed-row guard (in the resolver)

Location: `AchievementDefs.refresh` in `n3x_bot/achievement_defs.py`. Algorithm:

1. Read rows defensively: `try: rows = await repo.all_achievement_defs() except Exception: rows = []`. (Whole method must never raise — this also covers a repo with no such method, though wiring always uses a real repo.)
2. If `rows` is falsy (empty list) → `self._defs = list(ACHIEVEMENTS)` and return. This is the behaviour-preserving empty-table path.
3. If `rows` is non-empty → convert with a per-row guard (TOTAL REPLACEMENT):
   ```
   converted = []
   for row in rows:
       try:
           converted.append(Achievement(
               id=row["id"], category=row["category"], metric=row["metric"],
               threshold=int(row["threshold"]), title=row["title"],
               secret=bool(row["secret"]), color=row.get("color")))
       except (KeyError, TypeError, ValueError):
           continue   # skip malformed row; never raise
   self._defs = converted
   ```
   `int(row["threshold"])` is what makes `threshold="not-an-int"` raise `ValueError` → skipped; a missing key raises `KeyError` → skipped. The good row (`voice_3600`) survives, matching `test_refresh_never_raises_and_skips_malformed_rows`.

Note: the fallback-to-code-defaults decision keys off the RAW row list being empty (step 2), NOT off the converted list. If every non-empty row were malformed the resolver would hold an empty list — untested edge, flagged below.

## Hex parsing helper for `tier_color`

`n3x_bot/cards.py::_parse_hex_color(value: str | None) -> tuple[int, int, int] | None`.
- `None` / non-str / blank → `None`.
- Must start with `#` and have exactly 6 remaining hex digits; otherwise `None`.
- Parse `int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16)`; wrap in try/except → `None` on failure.
- `tier_color` returns the parsed tuple when non-None, else the existing gate/category derivation. This keeps all pre-Phase-2a `tier_color` tests green (they pass `color=None`).

## Data flow

Representative trace — `on_ready` with a seeded table plus one extra def:

1. `build_bot` sets `bot.achievement_defs = AchievementDefs()` → holds `list(ACHIEVEMENTS)` (83).
2. `on_ready` calls `await bot.achievement_defs.refresh(repo)`.
3. `refresh` → `repo.all_achievement_defs()`. In json backend this returns `[{id, category, metric, threshold, title, secret, color}, ...]` sorted by id; in sql backend the same shape via `order_by(id.asc())` with `int()`/`bool()` coercion.
4. Rows non-empty (84 rows) → each converted to a frozen `Achievement`; any malformed row skipped. `self._defs` becomes exactly those 84.
5. `bot.achievement_defs.total == 84`; `by_id("voice_7200000")` resolves the extra def.
6. Downstream (this slice): `build_overview_embed(holders, ids, page, total=bot.achievement_defs.total)` renders "/84". Live `_erfolge`/gate/activity paths still use module `ACHIEVEMENTS` (83) — intentional for 2a (empty table in prod → identical numbers).

Storage write trace — `set_achievement_def("voice_3600", category=..., secret=False, color="#1E90FF")`:
- json: assigns the 6-field dict under key `"voice_3600"` in `_db["achievement_defs"]`, flushes. `get` reconstructs `{"id": "voice_3600", ...}`.
- sql: `_upsert` probes by PK `id`, inserts or updates the row. `get` selects and coerces `threshold→int`, `secret→bool`, returns 7-key dict.

## Dependencies

- New packages: NONE. Uses existing SQLAlchemy Core (`String`, `Integer`, `Text`, `Boolean` already imported in `schema.py`) and the stdlib.
- Internal modules the new code depends on: `n3x_bot.achievements` (`Achievement`, `ACHIEVEMENTS`) from the resolver; `n3x_bot.storage.schema` from both repos; `n3x_bot.achievement_defs` from `bot.py`.

## Build sequence (for the Coder)

1. `n3x_bot/achievements.py`: add `Achievement.color` trailing field. (Unblocks everything; `_build_achievements` still constructs. Run `tests/test_achievements.py::test_achievement_color_field_defaults_to_none` and the seed-color test.)
2. `n3x_bot/storage/schema.py`: add the `achievement_defs` table.
3. `n3x_bot/storage/base.py`: add the four abstract methods.
4. `n3x_bot/storage/json_repo.py`: `_empty` entry + four methods + export/import. (Now the json parametrization of `test_achievement_defs_contract.py` goes green.)
5. `n3x_bot/storage/sql_repo.py`: four methods + export/import/clear entry. (sqlite + postgres parametrizations go green.)
6. `n3x_bot/migrate.py`: add to `_DATA_TABLES`. (`test_migrate_data_tables_includes_achievement_defs`.)
7. `n3x_bot/achievement_defs.py`: the `AchievementDefs` resolver. (Most of `tests/test_achievement_defs.py` except bot wiring.)
8. `n3x_bot/achievements.py`: `check_achievements` / `recompute_user_achievements` `defs=` params; `build_overview_embed` `total=` param.
9. `n3x_bot/cards.py`: `_parse_hex_color` + `tier_color` override.
10. `n3x_bot/activity.py`: `voice_role_transition` `defs=` param.
11. `n3x_bot/bot.py`: import + `bot.achievement_defs` in `build_bot` + `on_ready` refresh guard. (`test_bot_wiring.py` + the two `on_ready` tests.)

Run the focused test files after each relevant step; run the full `tests/storage/` + `tests/test_achievement_defs.py` + touched consumer files at the end.

## Risks and open questions

- **All-rows-malformed edge is untested.** Per spec, `refresh` falls back to code defaults only when the RAW row list is empty. If a non-empty table contained exclusively malformed rows, the resolver would hold an empty list (total 0) rather than the code defaults. This follows the literal spec ("non-empty → total replacement") and no test covers it; flag to TDD if a different fallback is desired.
- **Export shape choice (dict-keyed-by-id) is a design decision.** The json backend's natural `_db` shape is `{id: {6 fields}}`; I mirror that exact shape in the sql `export_all` so `import_all` is symmetric AND cross-backend `migrate` works. The contract tests only round-trip within one backend, so this is stronger than required but matches the `content_texts` precedent (dict-shaped snapshot) and the existing migrate story. No ambiguity, just noting the intentional choice.
- **`secret` Boolean coercion across backends.** SQLite stores booleans as 0/1; SQLAlchemy's `Boolean` type decodes them back to Python `bool`, and I additionally wrap reads in `bool(...)` (as `export_all` already does for `stats.targeted` and `gate_entries.laser_dropped`) so `row["secret"] is True` holds on every backend. `threshold` is wrapped in `int(...)` for the same defensiveness. json needs no coercion.
- **Call-site routing decision (stated).** In 2a the live consumers — `_erfolge`, `post_overview`/`handle_overview_reaction`, gate/activity `check_achievements` calls, and `apply_voice_roles` — REMAIN on the module `ACHIEVEMENTS`/`TOTAL_ACHIEVEMENTS` defaults. Rationale: the prod `achievement_defs` table is empty until 2b seeds it, so routing now would change nothing but widen the blast radius; the tests keep the 83 baseline valid and only exercise the new optional params directly. The `bot.achievement_defs` resolver is wired and refreshed so 2b can flip call sites over with no storage/plumbing work. `test_bot_wiring.py` adds no command, so command counts are unchanged.
- **`String(50)` lengths.** `category`/`metric`/`color` use `String(50)` to match the `content_texts.key`/`achievements.achievement_id` house style. Current seed values fit comfortably; if 2b introduces longer categories this may need widening (noted, not a blocker for 2a).
