# Architecture: Editable achievement TIER / CATEGORY colours (de-hardcode Phase 3)

A 1:1 clone of the `content_texts` storage work plus a new resolver
(`ColorConfig`) and three `/config` colour setters. The resolver MERGES overrides
onto the `cards.py` code defaults (unlike `AchievementDefs`, which
total-replaces). No new packages. No new patterns — every edit mirrors an
existing `content_texts` / `RuntimeConfig` site.

## Tests this design satisfies

### `tests/storage/test_color_config_contract.py` (Part A — keyed store, all backends)
- `test_get_color_config_unknown_key_returns_none`
- `test_set_color_config_roundtrips`
- `test_get_color_config_returns_str`
- `test_set_color_config_upserts_same_key`
- `test_multiple_keys_are_independent`
- `test_set_one_key_leaves_other_keys_unset`
- `test_value_survives_verbatim`
- `test_delete_color_config_returns_true_when_present`
- `test_delete_color_config_removes_the_key`
- `test_delete_color_config_returns_false_when_absent`
- `test_all_color_config_empty_by_default`
- `test_all_color_config_returns_full_map`
- `test_export_all_includes_color_config_and_is_json_serializable`
- `test_round_trip_preserves_color_config`
- `test_snapshot_is_stable_after_color_config_round_trip`
- `test_clear_wipes_color_config`
- `test_migrate_data_tables_includes_color_config`

### `tests/test_colors.py` (Parts B + C — resolver + `cards.tier_color` param)
- B1 defaults: `test_tier_color_default_matches_gate_tier_color` (6 params),
  `test_tier_color_default_no_substring_match_is_white`,
  `test_category_color_default_matches_activity_table` (5 params),
  `test_category_color_unknown_category_is_white`
- B2 tier override/merge/order/malformed:
  `test_tier_override_recolours_only_that_tier`,
  `test_tier_override_leaves_other_tiers_at_default_merge`,
  `test_tier_override_preserves_substring_match_order`,
  `test_tier_override_applies_when_the_matched_substring_is_overridden`,
  `test_tier_malformed_override_falls_back_to_default`,
  `test_tier_override_does_not_change_no_match_white`
- B3 category override/merge/malformed/unknown:
  `test_category_override_recolours_only_that_category`,
  `test_category_override_leaves_other_categories_default_merge`,
  `test_category_malformed_override_falls_back_to_default`,
  `test_category_override_for_unknown_category_still_resolves`,
  `test_never_raises_on_malformed_overrides`
- B4 refresh/load: `test_refresh_loads_overrides_from_repo`,
  `test_refresh_merges_leaving_unset_tiers_default`,
  `test_load_classmethod_builds_resolver_with_repo_overrides`,
  `test_load_with_no_overrides_is_behaviour_preserving`
- C `cards.tier_color(..., colors=)`: `test_tier_color_colors_none_gate_byte_identical_to_today`,
  `test_tier_color_colors_none_non_gate_byte_identical_to_today`,
  `test_tier_color_gate_uses_colors_tier_override`,
  `test_tier_color_non_gate_uses_colors_category_override`,
  `test_tier_color_explicit_achievement_color_still_beats_colors_override`,
  `test_tier_color_gate_with_colors_but_no_override_matches_default`

### `tests/test_color_config_commands.py` (Part D — wiring + `/config` setters)
- `test_build_bot_attaches_colors_resolver`,
  `test_build_bot_colors_behaviour_preserving_without_overrides`,
  `test_on_ready_refreshes_colors`
- `test_config_group_exposes_colour_subcommands`
- tier-color: `test_tier_color_writes_key_and_refreshes_live_resolver`,
  `test_tier_color_lowercases_the_name`,
  `test_tier_color_invalid_hex_rejected_no_write`,
  `test_tier_color_non_admin_refused_no_write`
- category-color: `test_category_color_writes_key_and_refreshes`,
  `test_category_color_invalid_hex_rejected_no_write`,
  `test_category_color_non_admin_refused_no_write`
- color-reset: `test_color_reset_deletes_key_and_refreshes`,
  `test_color_reset_non_admin_refused`
- announce integration: `test_announce_uses_bot_colors_tier_override`

Existing regression suites that must stay green: `tests/test_cards.py` (single-arg
`cards.tier_color(ach)`), `tests/test_achievement_announce.py` (builds real bots
via `build_bot`, so gains `bot.colors` for free), all `content_texts` contract tests.

## Files to create

- `n3x_bot/colors.py` — the MERGE resolver, mirroring `n3x_bot/content.py`
  (`ContentTexts`) shape but resolving against the ordered `cards` colour tables
  instead of a `DEFAULTS` dict. Imports defaults from `cards` (do NOT duplicate
  the numbers).

  ```python
  from n3x_bot.cards import (
      GATE_TIER_COLORS, ACTIVITY_CATEGORY_COLORS, _parse_hex_color,
  )
  WHITE = (255, 255, 255)

  class ColorConfig:
      def __init__(self, overrides: dict[str, str] | None = None) -> None
      def tier_color(self, title: str) -> tuple[int, int, int]
      def category_color(self, category: str) -> tuple[int, int, int]
      async def refresh(self, repo) -> None            # from all_color_config()
      @classmethod
      async def load(cls, repo) -> "ColorConfig"
  ```

  Behaviour (see Data flow for the exact branch order):
  - `__init__`: store `self._overrides = dict(overrides or {})` verbatim — NO key
    filtering (keys are open-ended `tier:*` / `category:*`; a bogus prefix-less
    key is simply never looked up, so it can't raise). This differs from
    `ContentTexts.__init__`, which filters to `CONTENT_KEYS`.
  - `tier_color(title)`: `t = title.lower()`; iterate `GATE_TIER_COLORS` IN ORDER;
    on the first `substring in t`, look up `self._overrides.get(f"tier:{substring}")`,
    run it through `_parse_hex_color`; return the parsed rgb if not None, else the
    tier's default rgb from the table; return immediately (do NOT keep scanning).
    No substring matches → return `WHITE`.
  - `category_color(category)`: parse `self._overrides.get(f"category:{category}")`;
    if valid → return it; else `ACTIVITY_CATEGORY_COLORS.get(category)`; else `WHITE`.
  - `refresh`: `self._overrides = dict(await repo.all_color_config())`.
  - `load`: `cfg = cls(); await cfg.refresh(repo); return cfg` (identical to
    `ContentTexts.load`).
  - Never raises: `_parse_hex_color` already returns None for non-strings /
    malformed hex, so every override path has a safe fallback.

## Files to modify

Each edit clones the adjacent `content_texts` code EXACTLY, substituting the
`color_config` table/method names. `color_config` starts EMPTY (not seeded).

- `n3x_bot/storage/schema.py` (after `content_texts`, ~line 138) — add a new
  `Table` identical to `content_texts` but named `color_config`:
  `color_config = Table("color_config", metadata, Column("key", String(50),
  primary_key=True), Column("value", Text, nullable=True))`. Auto-created by the
  existing `metadata.create_all` (sql_repo.py:48).

- `n3x_bot/storage/base.py` (after the `all_content_texts` abstractmethod, ~line
  136) — add four abstract methods mirroring the content-texts block:
  `set_color_config(self, key: str, value: str) -> None`,
  `get_color_config(self, key: str) -> str | None`,
  `delete_color_config(self, key: str) -> bool`,
  `all_color_config(self) -> dict[str, str]`. Under a `# color config` comment.

- `n3x_bot/storage/json_repo.py` — four edits mirroring `content_texts`:
  1. `_empty()` (line 51): add `"color_config": {},`.
  2. New method block after `all_content_texts` (line 315): clone lines 301–315
     with `content_texts`→`color_config` and method names `set_color_config` /
     `get_color_config` / `delete_color_config` / `all_color_config`.
  3. `export_all` (line 605): add
     `"color_config": copy.deepcopy(self._db["color_config"]),`.
  4. `import_all` (line 637): add
     `self._db["color_config"] = copy.deepcopy(snapshot.get("color_config", {}))`.
  - `connect()` (line 59–60) already `setdefault`s any new `_empty()` key onto
    existing files, and `clear()` (line 645) rebuilds from `_empty()` — both pick
    up `color_config` automatically, no edit needed.

- `n3x_bot/storage/sql_repo.py` — clone every `content_texts` site (targets `sc.color_config`):
  1. Methods after `all_content_texts` (line 382): clone 360–382 →
     `set_color_config` (uses `self._upsert(conn, sc.color_config, {"key": key},
     {"value": value})`), `get_color_config`, `delete_color_config`,
     `all_color_config`. Under a `# color config` comment.
  2. `export_all` (line 851): add a `color_config = {r.key: r.value for r in await
     conn.execute(select(sc.color_config))}` block, and `"color_config":
     color_config,` in the returned dict (line 878).
  3. `import_all` (line 955): add
     `for key, value in snapshot.get("color_config", {}).items(): await
     conn.execute(insert(sc.color_config).values(key=key, value=value))`.
  4. `clear` (line 980): add `sc.color_config` to the delete-table tuple.

- `n3x_bot/migrate.py` (`_DATA_TABLES`, line 25–32) — append `"color_config"` to
  the tuple.

- `n3x_bot/cards.py` — change ONLY the public `tier_color` signature + body
  (lines 71–77); `_gate_tier_color`, `ACTIVITY_CATEGORY_COLORS`,
  `GATE_TIER_COLORS`, `_parse_hex_color` are UNCHANGED (they become the resolver's
  imported defaults):
  ```python
  def tier_color(achievement, colors=None):   # colors: "ColorConfig | None"
      parsed = _parse_hex_color(achievement.color)
      if parsed is not None:
          return parsed                        # per-achievement colour wins first
      if colors is not None:
          if achievement.category == "gate":
              return colors.tier_color(achievement.title)
          return colors.category_color(achievement.category)
      # colors is None -> today's module-default branch, byte-identical
      if achievement.category == "gate":
          return _gate_tier_color(achievement.title)
      return ACTIVITY_CATEGORY_COLORS.get(achievement.category, (255, 255, 255))
  ```
  Do NOT `import` `colors.py` here (avoids a circular import; type is a forward
  string only). `announce_achievements` (line 226–230) is the sole internal
  caller: change its call to
  `tier_color(ach, colors=getattr(bot, "colors", None))`. The `getattr` default
  keeps any bot lacking the attribute safe (falls to the `colors is None` branch).

- `n3x_bot/bot.py` — two edits:
  1. `build_bot` (after line 100, next to `bot.content_texts = ContentTexts()`):
     add `bot.colors = ColorConfig()`. Add
     `from n3x_bot.colors import ColorConfig` to the import block (next to the
     `ContentTexts` import, line 36).
  2. `on_ready` (after the `content_texts` refresh, lines 970–973): add an
     independent guarded refresh mirroring it exactly:
     ```python
     try:
         await bot.colors.refresh(repo)
     except Exception:
         log.exception("colors refresh failed; using defaults")
     ```
     Independent try/except so a colours-refresh failure never blocks
     `achievement_defs` or downstream startup.

- `n3x_bot/config_commands.py` — add three subcommands inside
  `register_config_commands` (before `bot.tree.add_command(config_group)`,
  line 170), reusing the existing `_require_admin` closure and
  `cards._parse_hex_color`. Add `from n3x_bot import cards` at the top.
  ```python
  @config_group.command(name="tier-color", description="Setzt die Farbe einer Gate-Tier-Stufe.")
  @app_commands.describe(name="Tier-Name (Substring, z.B. gold)", hex="Farbe als #RRGGBB")
  async def tier_color(interaction, name: str, hex: str):
      if not await _require_admin(interaction): return
      if cards._parse_hex_color(hex) is None:
          await interaction.response.send_message("❌ Ungültige Farbe ...", ephemeral=True); return
      await repo.set_color_config(f"tier:{name.lower()}", hex)
      await bot.colors.refresh(repo)
      await interaction.response.send_message("✅ ...", ephemeral=True)

  @config_group.command(name="category-color", ...)          # same shape, key f"category:{name.lower()}"
  async def category_color(interaction, name: str, hex: str): ...

  @config_group.command(name="color-reset", description="Setzt eine Farb-Override zurück.")
  @app_commands.describe(key="Voller Schlüssel, z.B. tier:gold")
  async def color_reset(interaction, key: str):
      if not await _require_admin(interaction): return
      await repo.delete_color_config(key)
      await bot.colors.refresh(repo)
      await interaction.response.send_message("✅ ...", ephemeral=True)
  ```
  Notes pinned by tests:
  - Admin gate FIRST (non-admin → "Keine Berechtigung." reply, NO write); this is
    what `"Berechtigung" in _sent_text` asserts.
  - Invalid hex → ephemeral error, NO `set_color_config`, NO `refresh`.
  - `name.lower()` before building the key (`GOLD` → `tier:gold`).
  - Every reply `ephemeral=True` (tests assert `_last_send(...).kwargs["ephemeral"]
    is True`).
  - `color-reset` deletes the FULL raw key as given (e.g. `tier:gold`) — no
    lowercasing, no prefix synthesis.
  - Optionally add `"config tier-color"` / `"config category-color"` /
    `"config color-reset"` blurbs to `_COMMAND_DESCRIPTIONS` in `bot.py`
    (line 371) for the command-list embed — cosmetic, NOT asserted by any test.

## Data flow

`/config tier-color name="gold" hex="#010203"` (happy path):
1. `_require_admin(interaction)` → `app_is_admin` true → proceed.
2. `cards._parse_hex_color("#010203")` → `(1,2,3)` (not None) → valid.
3. `repo.set_color_config("tier:gold", "#010203")` → upserts the row (json:
   dict write + flush; sql: `_upsert` on `sc.color_config`).
4. `bot.colors.refresh(repo)` → `all_color_config()` → `{"tier:gold": "#010203"}`
   becomes `bot.colors._overrides`.
5. ephemeral `✅` reply.

Later card render for "Alpha Gold Pilot" (gate achievement, `.color=None`):
`announce_achievements` → `tier_color(ach, colors=bot.colors)` → `.color` parse is
None → `colors is not None` and category == "gate" → `bot.colors.tier_color("Alpha
Gold Pilot")` → lowercase → scan `GATE_TIER_COLORS`: "bronze"? no … "gold" in
"alpha gold pilot"? yes → look up `_overrides["tier:gold"]` = "#010203" → parse →
`(1,2,3)` → returned → `render_achievement_card(..., tier=(1,2,3))`. This is
exactly what `test_announce_uses_bot_colors_tier_override` captures.

Order-preservation edge (`test_tier_override_preserves_substring_match_order`):
"Alpha Grandmaster Pilot" with override `tier:master` — the scan hits
"grandmaster" (listed BEFORE "master") first, looks up `tier:grandmaster` (absent)
→ falls to grandmaster's default rgb and returns; "master" is never reached, so
its override cannot hijack the colour.

## Dependencies

- New packages: NONE. Everything (SQLAlchemy `Table`, discord `app_commands`) is
  already in use at the mirrored sites.
- Internal modules the new code depends on:
  - `n3x_bot/colors.py` → imports `GATE_TIER_COLORS`, `ACTIVITY_CATEGORY_COLORS`,
    `_parse_hex_color` from `n3x_bot.cards`. `cards.py` does NOT import `colors.py`
    (no circular import — the `colors` param is a runtime value / forward-ref).
  - `config_commands.py` → `cards._parse_hex_color`, `repo.set_color_config` /
    `delete_color_config`, `bot.colors.refresh`, existing `_require_admin`.
  - `bot.py` → `n3x_bot.colors.ColorConfig`.
  - `bot.colors` refresh in `on_ready` is INDEPENDENT of `content_texts` /
    `achievement_defs` / `runtime_config` (its own guarded try/except).

## Build sequence (for the Coder)

Build storage bottom-up so each layer's tests can pass before the next.

1. **Schema**: add the `color_config` `Table` in `storage/schema.py`.
2. **Base ABC**: add the four abstract methods in `storage/base.py`.
3. **JsonRepository**: `_empty` key, the four methods, `export_all`, `import_all`.
   → the `repo`-parametrized contract tests pass for the json backend.
4. **SqlRepository**: the four methods, `export_all`, `import_all`, `clear`.
   → contract tests pass for sqlite (and postgres if `TEST_POSTGRES_URL` set).
5. **migrate**: append `"color_config"` to `_DATA_TABLES`.
   → `test_migrate_data_tables_includes_color_config` passes. Part A complete.
6. **colors.py**: create `ColorConfig` (imports from `cards`).
   → all `tests/test_colors.py` B1–B4 pass.
7. **cards.tier_color**: add the `colors=None` param + 3-branch body; update the
   `announce_achievements` call to pass `colors=getattr(bot, "colors", None)`.
   → `tests/test_colors.py` Part C + existing `tests/test_cards.py` stay green.
8. **bot.py wiring**: import `ColorConfig`, set `bot.colors` in `build_bot`, add
   the guarded `bot.colors.refresh` in `on_ready`.
   → `test_build_bot_*`, `test_on_ready_refreshes_colors`,
   `test_announce_uses_bot_colors_tier_override` pass.
9. **config_commands.py**: add `tier-color`, `category-color`, `color-reset`.
   → the remaining `tests/test_color_config_commands.py` cases pass.
10. Run the three new test files + `tests/test_cards.py` +
    `tests/test_achievement_announce.py` + the content_texts contract suite as a
    regression gate.

## Risks and open questions

- **MERGE not total-replace (pinned)**: `ColorConfig` must NOT rebuild the whole
  colour table from overrides. A single `tier:gold` override recolours only gold;
  every other tier/category keeps its `cards` default. This is the key divergence
  from `AchievementDefs` (which total-replaces) and from `ContentTexts` (which
  filters to a fixed key set). Implement by resolving per-lookup against the code
  defaults with the override consulted only for the matched key. Enforced by
  `test_tier_override_leaves_other_tiers_at_default_merge` /
  `test_category_override_leaves_other_categories_default_merge`.
- **Per-key malformed-hex fallback**: a malformed override falls back to THAT
  key's default (tier default rgb / category default rgb), never white and never
  raising. `_parse_hex_color` returning None is the single fallback gate. Enforced
  by `test_tier_malformed_override_falls_back_to_default`,
  `test_category_malformed_override_falls_back_to_default`,
  `test_never_raises_on_malformed_overrides`.
- **Substring match-order preservation**: overrides change colour, never match
  order — the `GATE_TIER_COLORS` scan order is the source of truth. Return on the
  FIRST substring hit; don't let an override on a later, more-generic substring
  ("master") win over an earlier, more-specific one ("grandmaster"). Enforced by
  `test_tier_override_preserves_substring_match_order`.
- **getattr-safe announce**: `announce_achievements` reads
  `getattr(bot, "colors", None)` so a bot without the attribute is safe. The Part-D
  tests build real bots (which HAVE `bot.colors`), so this defends only
  hypothetical/legacy fakes — keep it anyway; it's the pinned contract and costs
  nothing.
- **`__init__` does not filter keys**: unlike `ContentTexts` (filters to
  `CONTENT_KEYS`), `ColorConfig` stores overrides verbatim because tier/category
  keys are open-ended (e.g. `category:custom` for a category with no code default,
  per `test_category_override_for_unknown_category_still_resolves`). A prefix-less
  garbage key (`bogus_key_without_prefix`) is harmless: it's never looked up.
- **No interaction with `sync_commands_to_guilds`**: the three new subcommands are
  added to the existing `config` `app_commands.Group` before
  `bot.tree.add_command(config_group)`, so they ride the group already published by
  the unchanged sync path. No sync-logic edit needed. `_config_group`/`_config_sub`
  in the tests read the group straight off `bot.tree` right after `build_bot`,
  confirming registration is synchronous at build time (no `on_ready` dependency).
- **`color_config` is NOT seeded**: `test_all_color_config_empty_by_default`
  requires an empty table on a fresh repo. Do not add it to `seed_defaults`.
- **`hex` param name shadows builtin**: the callbacks use `hex` as the parameter
  name (dictated by the test call `callback(interaction, name=..., hex=...)`).
  Acceptable — it shadows the builtin only inside the callback body, which never
  needs `hex()`. Do not rename.
- Open question (non-blocking): whether to add the new subcommands to
  `_COMMAND_DESCRIPTIONS` for the `/command_list` embed. No test asserts it;
  recommend adding the three blurbs for consistency with the existing `config`
  entries, but it is optional and out of the test surface.
