# Architecture: Phase 7 — slash-only finalization (`/sync_achievements`, tree-driven command list, prefix teardown)

Scope: `n3x_bot/bot.py` only. All supporting symbols
(`recompute_user_achievements`, `sync_all_achievements`, `command_list_channel_id`
in Settings/RuntimeConfig/CHANNEL_PURPOSES/.env.example, `COMMAND_LIST_KEY`,
`update_command_list`) already exist and their tests are already green. This
phase reworks three surfaces inside `bot.py`.

## Tests this design satisfies

### `tests/test_achievement_sync.py` (the `/sync_achievements` app-command block; recompute/sync_all tests already pass)
- `test_sync_achievements_is_app_command_not_prefix_command` — `bot.get_command("sync_achievements") is None`, `bot.tree.get_command("sync_achievements") is not None`.
- `test_sync_achievements_refuses_non_admin_and_mutates_nothing` — non-admin refused, `"Berechtigung"` surfaced, no DB mutation.
- `test_sync_achievements_defers_ephemeral_then_sends_followup` — `defer(ephemeral=True)` awaited once, then `followup.send` once, order `["defer", "followup"]`.
- `test_sync_achievements_admin_records_threshold_met_achievements` — admin run unlocks `msg_1000`.
- `test_sync_achievements_admin_second_run_adds_nothing_new` — idempotent (holders unchanged).
- `test_sync_achievements_followup_reports_german_summary_counts` — followup once, text contains a digit.

### `tests/test_command_list.py` (the tree-rework block; config-plumbing + update_command_list tests already pass)
- `test_command_list_contains_slash_top_level_commands` — `/rank /erfolge /overview /activity` present, `!rank` absent.
- `test_command_list_includes_slash_group_subcommands` — `/gate verlauf`, `/admin stat add`, `/config channel`, `/content set` present.
- `test_command_list_includes_dynamic_per_stat_commands` — registered per-stat counters appear as `/name`.
- `test_command_list_is_derived_from_the_live_tree` — every `bot.tree.get_commands()` top-level command appears as `/name`.
- `test_curated_rank_description_appears_in_embed` — `_COMMAND_DESCRIPTIONS["rank"]` flows into the embed text.
- `test_command_list_top_level_commands_are_sorted` — `/erfolge` index < `/rank` index.
- `test_command_list_title_is_german` / `test_command_list_is_deterministic` — title still contains `"Befehl"`, output stable across calls (already satisfied, must not regress).

### `tests/test_bot_wiring.py` (Phase 7 teardown block; the rest already passes)
- `test_on_message_does_not_delete_prefixed_messages_nor_process` — `!`-message not deleted, `process_commands` not called.
- `test_on_message_ignores_messages_from_self` — self message returns early, no delete/process.
- `test_on_message_records_activity_for_normal_message_without_processing` — activity recorded, no delete/process.
- `test_on_command_error_handler_is_removed` — `"on_command_error" not in bot.__dict__`.
- `test_generic_arg_commands_symbol_is_removed` — `not hasattr(botmod, "_GENERIC_ARG_COMMANDS")`.
- `test_on_message_routes_gate_input_channel_to_gate_handler` — gate-input routing kept; `process_commands` not called.
- `test_on_message_ignores_other_channels_for_gate_parsing` — non-gate channel: no reaction.
- `test_tree_on_error_surfaces_helper_error_to_interaction` — `bot.tree.on_error` STAYS, unchanged.
- `test_build_bot_wires_prefix_repo_settings_and_intents` — `bot.command_prefix == settings.command_prefix` STAYS.

## Files to create
- None.

## Files to modify

All edits are in `/home/isikerkan/n3x/n3x-bot/n3x_bot/bot.py`.

### 1. Remove `_GENERIC_ARG_COMMANDS` (lines 59–68)
Delete the comment block and the `frozenset(...)`. It only fed the prefix
`on_command_error` missing-argument branch, which is also being removed. No
other module references it (grep-confirmed: sole definition + sole use are both
in `bot.py`).

### 2. Convert prefix `sync_achievements` → tree app command in `register_overview_and_sync_commands` (lines 144–164)
- Keep the function; keep its `overview` tree command block unchanged. After
  this edit the function registers TWO tree commands (`overview`,
  `sync_achievements`) and NO prefix commands. It is still called from
  `build_bot` (line 137) — leave that wiring intact.
- Replace the prefix block (lines 155–164) with a tree command mirroring the
  phase-5 admin slash pattern (`admin.py::_register_slash_commands`,
  admin-check-first, refuse via `interaction.response.send_message`):

  Guard: `if bot.tree.get_command("sync_achievements") is None:` (was
  `bot.get_command(...)` — switch to the tree lookup for idempotency).

  New callback shape (signature + ordering; body is the coder's to write):
  ```
  @bot.tree.command(name="sync_achievements",
                    description="Synchronisiert alle Achievements (Admin).")
  async def sync_achievements(interaction):
      # 1. admin-check FIRST — refuse via response.send_message, ephemeral, return
      #    if not is_admin(interaction.user, settings): ... "❌ Keine Berechtigung." ...
      # 2. await interaction.response.defer(ephemeral=True)
      # 3. summary = await sync_all_achievements(repo)
      # 4. await interaction.followup.send(<German summary embedding
      #       summary['users_processed'] and summary['achievements_added']>,
      #       ephemeral=True)
  ```
  - `is_admin` is already imported (line 11). `sync_all_achievements` already
    imported (line 29). Reuse the existing German summary string from the old
    prefix body (`f"✅ Sync: {summary['users_processed']} Nutzer, "
    f"{summary['achievements_added']} neue Achievements."`) — it already carries
    the count digits the test scans for.
  - Refusal text must contain `"Berechtigung"` (use the exact admin.py string
    `"❌ Keine Berechtigung."`).
  - Order is load-bearing: admin-check → `defer` → `sync_all_achievements` →
    `followup.send`. The refusal path returns BEFORE `defer`, so a non-admin
    never mutates and never defers (satisfies `mutates_nothing`).
  - Drop `commands.Command(...)` / `bot.add_command(...)`.

### 3. Rework `build_command_list` (lines 381–407) to walk `bot.tree`
Replace the `bot.commands` walk with a recursive `bot.tree.get_commands()` walk.
Keep the chunking, the `📋 Befehlsübersicht` title, and the description-map
lookup by `qualified_name`. Algorithm (deterministic, no date/random):

```
lines: list[str] = []

def _emit(cmd):
    desc = _COMMAND_DESCRIPTIONS.get(cmd.qualified_name, "")
    lines.append(f"/{cmd.qualified_name} — {desc}" if desc
                 else f"/{cmd.qualified_name}")
    if isinstance(cmd, app_commands.Group):
        for sub in sorted(cmd.commands, key=lambda c: c.name):
            _emit(sub)

for cmd in sorted(bot.tree.get_commands(), key=lambda c: c.name):
    _emit(cmd)
```
- `app_commands` is already imported (line 8).
- Distinguish a group with `isinstance(cmd, app_commands.Group)`; its children
  are `group.commands` (an `app_commands.Command | Group` list). Recurse so
  nested groups (`admin` → `stat` → `add`) render each leaf via
  `qualified_name`, which discord.py space-joins to `"admin stat add"` →
  `/admin stat add`.
- Every top-level entry (leaf OR group) emits its own `/name` line, so
  `test_command_list_is_derived_from_the_live_tree` passes even for groups
  (`/gate`, `/admin`, `/config`, `/content`).
- Sorting top-level by `.name` puts `erfolge` before `rank`
  (`test_..._sorted`).
- `_COMMAND_DESCRIPTIONS.get(cmd.qualified_name, "")` keeps the map DRIVING the
  description column: `"rank"` → curated blurb flows into the embed line
  (`test_curated_rank_description_appears_in_embed`).
- Keep the tail unchanged:
  `chunks = _chunk_gate_lines(lines, limit=1024)`, then build the embed with the
  `📋 Befehlsübersicht` title and add overflow chunks as fields. Both the title
  substring `"Befehl"` and determinism are preserved.
- Remove the `if cmd.name == "help": continue` guard — `help` is a prefix
  command, never on the tree, so it can't appear.
- Update the docstring: "enumerates `bot.tree.get_commands()` … renders each as
  a `/`-prefixed line".

### 4. `_COMMAND_DESCRIPTIONS` (lines 375–378)
Already keyed by `qualified_name`; both existing keys are valid app-command
qualified names, so no dead prefix key exists to delete:
- `"rank"` — KEEP (pinned by `test_curated_description_map_has_rank_blurb` and
  `test_curated_rank_description_appears_in_embed`).
- `"sync_achievements"` — KEEP (now the qualified name of the new top-level
  app command; its blurb renders on the `/sync_achievements` line).
Optional (NOT test-required) additions for nicer output, all as qualified-name
keys: `"gate verlauf"`, `"admin stat add"`, `"config channel"`,
`"content set"`, `"overview"`, `"activity"`, `"erfolge"`, and per-stat keys.
Any command absent from the map renders name-only — that is the intended
fallback, so leave the map minimal unless the user wants curated blurbs.

### 5. Remove `on_command_error` (lines 1000–1019)
Delete the entire `@bot.event async def on_command_error(...)` block. `@bot.event`
assigns the handler as an instance attribute; deleting it means
`"on_command_error" not in bot.__dict__` (discord.py's no-op class default
remains, so nothing breaks). Leave the `@bot.tree.error` /
`on_app_command_error` handler (lines 1021–1039) untouched — it is the
app-command error surface the tests require.

### 6. Trim `on_message` (lines 1041–1061)
Delete the prefix-delete block and the `process_commands` call (lines 1056–1061):
```
        if message.content.startswith(settings.command_prefix):
            try:
                await message.delete(delay=5.0)
            except Exception:
                pass
        await bot.process_commands(message)
```
KEEP, in order:
- self/None-author early return (1043–1044),
- activity recording + achievement announce (1045–1053),
- gate-input-channel routing to `handle_gate_input_message` (1054–1055).
The method ends after the gate-input routing. No `process_commands` remains.

### 7. `command_prefix` — NO CHANGE
`build_bot` (line 105) keeps `command_prefix=settings.command_prefix`.
discord.py requires a prefix and `test_build_bot_wires_prefix_repo_settings_and_intents`
still asserts it. Because `on_message` no longer calls `process_commands`, the
prefix is inert — no prefix command ever dispatches.

## Data flow

### `/sync_achievements` (admin)
`interaction` → `sync_achievements` callback → `is_admin(interaction.user,
settings)` True → `interaction.response.defer(ephemeral=True)` →
`sync_all_achievements(repo)` (enumerates users via `repo.export_all()`,
additively backfills threshold-met unlocks, returns
`{users_processed, achievements_added}`) → `interaction.followup.send(<German
summary with both counts>, ephemeral=True)`.
Non-admin: `is_admin` False → `interaction.response.send_message("❌ Keine
Berechtigung.", ephemeral=True)` → return (no defer, no sync, no mutation).

### `build_command_list(bot)`
`sorted(bot.tree.get_commands())` → for each, `_emit` appends `/qualified_name`
(+ optional mapped blurb); groups recurse into `sorted(group.commands)` →
`_chunk_gate_lines(lines, 1024)` → `discord.Embed(title="📋 Befehlsübersicht",
description=chunks[0])` + one field per overflow chunk → returned to
`update_command_list`, which posts/edits it in the `command_list` channel via
the `channel_messages` store under `COMMAND_LIST_KEY` (unchanged path).

### `on_message` (normal human message)
early-return checks → `record_message_activity` (+ best-effort achievement
announce) → if channel is the gate-input channel, `handle_gate_input_message` →
return. No delete, no `process_commands`.

## Dependencies
- New packages: none.
- Internal modules: all already imported in `bot.py` — `is_admin` (line 11),
  `sync_all_achievements` (line 29), `app_commands` (line 8),
  `_chunk_gate_lines` (same module). No import changes needed. `commands` import
  stays (still used for `commands.Bot`, `tasks`, etc.); `commands.Command` /
  `commands.Group` usages in the two reworked functions go away but the module
  is still needed elsewhere.

## Build sequence (for the Coder)
1. Delete `_GENERIC_ARG_COMMANDS` (lines 59–68).
2. Delete the `on_command_error` handler (lines 1000–1019). Run
   `test_bot_wiring.py::test_on_command_error_handler_is_removed` and
   `::test_generic_arg_commands_symbol_is_removed` → green.
3. Trim `on_message` (remove prefix-delete + `process_commands`). Run the four
   `on_message` teardown/gate-routing tests → green.
4. Convert `sync_achievements` to a `@bot.tree.command` in
   `register_overview_and_sync_commands` (admin-check-first → defer → sync →
   followup; guard on `bot.tree.get_command`). Run the six
   `test_achievement_sync.py` app-command tests → green.
5. Rework `build_command_list` to the recursive tree walk; update its docstring.
   Run all `test_command_list.py` tests → green.
6. Full run of the three files; then the whole suite to confirm no regression
   (especially `test_build_bot_wires_prefix_repo_settings_and_intents` for the
   retained `command_prefix`, and `test_tree_on_error_...` for the retained
   `@bot.tree.error`).

## Risks and open questions
- **`bot.commands` after teardown is not empty.** `commands.Bot` auto-registers
  a default `help` prefix command, so `bot.commands` still contains `help`. No
  code reads `bot.commands` after this change (grep: the only reader was
  `build_command_list`, now retargeted to `bot.tree`), and `process_commands` is
  never called, so `help` is dead but harmless. No test asserts `bot.commands`
  is empty. If a future cleanup wants a truly empty prefix registry, pass
  `help_command=None` to `commands.Bot` — out of scope here, not test-driven.
- **Group lines add noise.** `_emit` prints a bare `/gate`, `/admin`, `/config`,
  `/content` line for each group in addition to its subcommands. This is
  required to satisfy `test_command_list_is_derived_from_the_live_tree`
  (asserts `/{command.name}` for every top-level command, groups included).
  Rendering only leaves would still pass via substring (`/gate` ⊂ `/gate
  verlauf`), but emitting the explicit group line is clearer and equally
  deterministic — recommended.
- **Description map stays minimal.** Tests only pin `"rank"`. I recommend NOT
  inventing German blurbs for the other commands unless the user wants curated
  text, to avoid unreviewed prose. Flagging that the embed will otherwise list
  most commands name-only — a product decision, not a test failure.
- **`sync_all_achievements` / `recompute_user_achievements` already exist and
  pass their tests** (`achievements.py:259,270`). This phase does not touch
  them; it only adds the slash entry point that calls `sync_all_achievements`.
- **Admin-check ordering choice pinned to admin-check-first** (refuse via
  `response.send_message`, matching `admin.py`). The tests accept defer-first
  too, but admin-check-first avoids a needless defer on refusal and keeps the
  no-mutation guarantee obvious.
