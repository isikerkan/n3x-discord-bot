# Architecture: de-hardcode Phase 2b — `/achievement` editor + resolver flow

## Tests this design satisfies

### Part A — `tests/test_achievement_commands.py` (31)
- `test_achievement_is_app_group_not_prefix_command` — group on `bot.tree`, absent from prefix registry.
- `test_achievement_group_exposes_expected_subcommands` — `{list, show, set, reset, reset-all}`.
- `test_register_achievement_def_commands_entrypoint_exists` — `register_achievement_def_commands` callable.
- `test_register_achievement_def_commands_is_idempotent` — re-register is a no-op.
- `test_build_bot_registers_achievement_group_on_tree` — `build_bot` alone wires the group.
- `test_list_empty_table_reports_defaults_active` — resolver total (83) + "Code-Defaults aktiv".
- `test_list_with_db_rows_reports_resolver_total` — 84 shown when 83 seeds + 1 custom.
- `test_list_is_ephemeral`, `test_list_non_admin_refused`.
- `test_show_returns_detail_from_resolver` — id/threshold/title surfaced.
- `test_show_reflects_a_db_override_via_resolver` — id/title/`#ABCDEF` surfaced.
- `test_show_unknown_id_reports_german_error_ephemeral`, `test_show_is_ephemeral`, `test_show_non_admin_refused`.
- `test_show_id_param_has_substring_filtered_autocomplete` — Choices, ≤25, substring-filtered, real id present.
- `test_set_on_empty_table_seeds_all_defaults_then_adds_new_def` — 83 seeds + new = 84.
- `test_set_on_empty_table_editing_existing_id_stays_at_baseline` — 83 rows, override threshold.
- `test_set_on_non_empty_table_is_plain_upsert_no_reseed` — 2 rows, no reseed.
- `test_set_writes_def_and_refreshes_resolver`.
- `test_set_accepts_valid_hex_color`, `test_set_rejects_invalid_color_no_write`, `test_set_rejects_threshold_below_one_no_write`.
- `test_set_confirms_ephemerally`, `test_set_non_admin_refused_no_write`.
- `test_reset_deletes_the_row`, `test_reset_last_row_falls_back_to_code_defaults`, `test_reset_unknown_id_reports_german_error`, `test_reset_non_admin_refused_no_write`, `test_reset_id_param_has_autocomplete`.
- `test_reset_all_wipes_every_row_back_to_defaults`, `test_reset_all_non_admin_refused_no_write`.

### Part B — `tests/test_resolver_flow.py` (7)
- `test_gate_success_path_unlocks_resolver_only_def` — `handle_gate_input_message` reads resolver.
- `test_reaction_activity_path_unlocks_resolver_only_def` — `handle_activity_reaction` reads resolver.
- `test_sync_all_achievements_accepts_and_honours_defs` — `sync_all_achievements(repo, defs=...)`.
- `test_sync_achievements_command_uses_resolver_defs` — `/sync_achievements` feeds resolver defs.
- `test_erfolge_embed_uses_resolver_total` — `/erfolge` shows `/84`.
- `test_overview_embed_uses_resolver_total` — `/overview` shows `/84`.
- `test_apply_voice_roles_resolves_custom_def_via_resolver` — `apply_voice_roles` threads resolver defs.

## Files to create

### `n3x_bot/achievement_commands.py`
New write surface, mirroring `n3x_bot/config_commands.py` structure (module-level `register_*`, nested `_require_admin`, tree idempotency guard, ephemeral).

- `register_achievement_def_commands(bot, repo: StatsRepository, settings: Settings) -> None`
  - Guard: `if bot.tree.get_command("achievement") is not None: return`.
  - Build `group = app_commands.Group(name="achievement", description="Achievement-Definitionen (Admin).")`.
  - Nested `async def _require_admin(interaction) -> bool` — copy verbatim from config_commands (uses `app_is_admin`, sends `"❌ Keine Berechtigung."` ephemeral, returns bool).
  - Nested `async def _id_autocomplete(interaction, current: str) -> list[app_commands.Choice[str]]` — shared by `show` and `reset`:
    - `ids = bot.achievement_defs.all()`; filter `current.lower() in a.id.lower()`; cap 25; each `app_commands.Choice(name=f"{a.id} — {a.title}"[:100], value=a.id)`.
  - Five subcommands (see Data flow). Register autocomplete via `@sub.autocomplete("id")` on `show` and `reset`.
  - `bot.tree.add_command(group)` at the end.
- Module-level helper `async def _seed_defaults_if_empty(repo) -> None`:
  - `if await repo.all_achievement_defs():` return.
  - Else `for a in ACHIEVEMENTS: await repo.set_achievement_def(a.id, category=a.category, metric=a.metric, threshold=a.threshold, title=a.title, secret=a.secret, color=a.color)`.
- Imports: `from discord import app_commands`; `from n3x_bot.admin import app_is_admin`; `from n3x_bot.achievements import ACHIEVEMENTS`; `from n3x_bot.cards import _parse_hex_color`; `from n3x_bot.config import Settings`; `from n3x_bot.storage.base import StatsRepository`.

## Files to modify

### `n3x_bot/bot.py`
- Import (near line 33, next to `register_config_commands`): `from n3x_bot.achievement_commands import register_achievement_def_commands`.
- In `build_bot` (after line 125 `register_achievement_commands(...)`): add `register_achievement_def_commands(bot, repo, settings)`.
- `register_achievement_commands` `erfolge` callback (line 373-377): pass resolver values —
  `embed = await _build_erfolge_embed(repo, owned, interaction.user.id, interaction.user.display_name, defs=bot.achievement_defs.all(), total=bot.achievement_defs.total)`. `bot` is in scope.
- `register_overview_and_sync_commands` `sync_achievements` callback (line 153): `summary = await sync_all_achievements(repo, defs=bot.achievement_defs.all())`.
- `handle_gate_input_message` a/b/c success block (lines 743-745): append `defs=bot.achievement_defs.all()` to each of the three `check_achievements(...)` calls. `bot` is a parameter.
- `handle_gate_drop_confirmation` d/e/z/k block (lines 834-836): same edit on the three `check_achievements(...)` calls. `bot` is a parameter.

### `n3x_bot/activity.py`
All five `check_achievements` call sites gain `defs=bot.achievement_defs.all()`. Verify `bot` is in scope at each:
- `record_message_activity` (lines 124, 126, 128) — **`bot` is NOT a parameter** (signature `record_message_activity(repo, settings, member_id, now)`). Grow the signature to `record_message_activity(bot, repo, settings, member_id, now)` and update the single caller in `bot.py` `on_message` (line 1050) to pass `bot` first. Then pass `defs=bot.achievement_defs.all()` to all three calls.
- `handle_voice_state_update` (line 165) — `bot` is a parameter. Add `defs=bot.achievement_defs.all()`.
- `flush_voice_times` (line 204) — `bot` is a parameter. Add `defs=bot.achievement_defs.all()`.
- `handle_activity_reaction` (line 231) — `bot` is a parameter. Add `defs=bot.achievement_defs.all()`.
- `apply_voice_roles` (line 78) already forwards nothing; change `voice_role_transition([a.id for a in newly], role_map)` → `voice_role_transition([a.id for a in newly], role_map, defs=bot.achievement_defs.all())`. `bot` is a parameter. (The `settings` param it receives is actually `bot.runtime_config` at call sites — unchanged; only `bot` is read for the resolver.)

### `n3x_bot/achievements.py`
- `sync_all_achievements` (line 302): grow to `async def sync_all_achievements(repo, defs=None) -> dict` and forward into the per-user recompute: `newly = await recompute_user_achievements(repo, uid, defs=defs)` (line 312). `defs=None` preserves the existing `test_achievement_sync.py` positional calls.
- `_build_erfolge_embed` (line 328): grow to `(repo, owned, uid, display_name, defs=None, total=None)`.
  - `source = defs if defs is not None else ACHIEVEMENTS`; `denom = total if total is not None else TOTAL_ACHIEVEMENTS`.
  - Replace `TOTAL_ACHIEVEMENTS` in header/bar/percent (lines 331, 336, 337) with `denom`.
  - Replace `ACHIEVEMENTS` in the category loop (line 342) and secret counts (lines 358-359) with `source`.
- `build_overview_embed` (line 180): add `defs=None` param (keep existing `total=None`). Thread `defs` into the breakdown: `_overview_breakdown(owned, defs)`.
- `_overview_breakdown` (line 210): grow to `(owned, defs=None)`, `source = defs if defs is not None else ACHIEVEMENTS`, use `source` in `owned_count`/`total_count`. `defs=None` keeps `test_overview.py` positional calls green.
- `post_overview` (line 237): `embed = build_overview_embed(holders, user_ids, page, total=bot.achievement_defs.total, defs=bot.achievement_defs.all())`.
- `handle_overview_reaction` (line 276): `embed = build_overview_embed(holders, user_ids, new_page, total=bot.achievement_defs.total, defs=bot.achievement_defs.all())`.

## Data flow

### `/achievement set custom_new title=Neu threshold=5 category=voice metric=voice_seconds color=#AABBCC`
1. `_require_admin` → refuse-and-return on non-admin (no write).
2. Validate color: `if color is not None and _parse_hex_color(color) is None:` → `send_message("❌ Ungültige Farbe ...", ephemeral=True)`, return (no write).
3. Validate threshold: `if threshold < 1:` → `send_message("❌ Threshold muss ≥ 1 sein.", ephemeral=True)`, return (no write).
4. `await _seed_defaults_if_empty(repo)` — on an empty table this upserts all 83 code defaults first, guaranteeing the other 82 survive total-replacement; on a non-empty table it is a no-op (no reseed).
5. `await repo.set_achievement_def(id, category=..., metric=..., threshold=..., title=..., secret=..., color=color)` — upserts the target (overwrites the just-seeded row when editing an existing id → stays 83; new id → 84).
6. `await bot.achievement_defs.refresh(repo)` — rebuild resolver from the table.
7. `await interaction.response.send_message(f"✅ `{id}` gespeichert.", ephemeral=True)`.

### `/erfolge` (resolver total)
`erfolge` callback → `get_user_achievements` → `_build_erfolge_embed(repo, owned, uid, name, defs=bot.achievement_defs.all(), total=bot.achievement_defs.total)` → header renders `count/denom` where `denom = total = 84` → embed contains `/84`.

### gate success (resolver-only unlock)
`on_message` → `handle_gate_input_message(bot, repo, settings, message)` → after insert, `check_achievements(repo, author_id, "gate_a", defs=bot.achievement_defs.all())` sees the resolver's `a_1` (threshold 1, absent from module) → `unlock_achievement(author_id, "a_1")`.

## Dependencies
- New packages: none.
- Internal: `n3x_bot.admin.app_is_admin`, `n3x_bot.cards._parse_hex_color`, `n3x_bot.achievements.ACHIEVEMENTS`, `bot.achievement_defs` (AchievementDefs: `all`/`total`/`by_id`/`refresh`), repo CRUD (`set_/get_/delete_/all_achievement_defs`).

## Build sequence (for the Coder)
1. **`n3x_bot/achievements.py` pure-helper param growth** (no behaviour change, defaults preserve old tests): `sync_all_achievements(repo, defs=None)`, `recompute` forwarding, `_build_erfolge_embed(... defs=None, total=None)`, `build_overview_embed(... defs=None)`, `_overview_breakdown(owned, defs=None)`, and the `post_overview`/`handle_overview_reaction` call-site upgrades. Run `test_overview.py`, `test_achievement_sync.py`, `test_achievement_defs.py` — must stay green.
2. **`n3x_bot/activity.py`**: grow `record_message_activity` signature to take `bot` first; add `defs=bot.achievement_defs.all()` at all 5 `check_achievements` sites and the `voice_role_transition` site. Update the single `on_message` caller in `bot.py` line 1050.
3. **`n3x_bot/bot.py`**: add `defs=` to the 6 gate `check_achievements` calls; upgrade the `erfolge` and `sync_achievements` callbacks. Run `test_resolver_flow.py` (Part B).
4. **Create `n3x_bot/achievement_commands.py`** with `_seed_defaults_if_empty` + `register_achievement_def_commands` (list/show/set/reset/reset-all + shared autocomplete). Wire it into `build_bot`. Run `test_achievement_commands.py` (Part A).
5. Full focused run of both new test files, then the touched legacy files, then the suite.

## Refresh discipline
Every write path (`set`, `reset`, `reset-all`) MUST call `await bot.achievement_defs.refresh(repo)` after the DB mutation and before the confirm, exactly as `config_commands._write` does. `reset-all` refreshes once after deleting all rows (row-by-row delete, then a single refresh). A missed refresh leaves a stale resolver — the primary failure mode; mirror the config pattern to avoid it.

## Risks and open questions
- **`record_message_activity` signature growth is a breaking change** to its one caller. Low risk (single call site at bot.py:1050) but the Coder must update it in the same commit or `on_message` breaks. Any test that calls `record_message_activity` directly with the old arg order would need the new `bot` leading arg — none found in the current suite, but confirm during step 2.
- **Per-call `.all()` copies**: `AchievementDefs.all()` returns `list(self._defs)` on every call site (each `check_achievements` invocation). At 83-84 small dataclasses this is cheap; no caching needed. Acceptable.
- **`reset-all` iterates `all_achievement_defs` then deletes each** — 84 row deletes on the flatfile/SQL repo. No bulk-delete method exists (`grep` confirms only per-id `delete_achievement_def`); row-by-row is fine at this scale. Flag if the def count ever grows by an order of magnitude.
- **`category`/`metric` on `set` are free-form strings, not `app_commands.Choice`-constrained.** The tests pass raw values (`voice`/`voice_seconds`, `message`/`messages`) and never assert a rejection for an unknown category/metric, so no validation is designed in. If product intent is to constrain these, that is out of scope for the pinned test surface — raise back to TDD rather than inventing it.
- **`sync_commands_to_guilds` is untouched.** Achievement *definitions* are DB rows, not slash commands; only the `achievement` group itself is a command and it registers through the normal `bot.tree` path in `build_bot`. No guild-sync interaction.
- **Resolver `color` on built `Achievement` objects**: `AchievementDefs.refresh` already carries `row.get("color")` into `Achievement.color`, and `cards.tier_color` reads it, so the announce/tier-color path works with no extra wiring. Verified against `achievement_defs.py:45` and `cards.py:72`.
