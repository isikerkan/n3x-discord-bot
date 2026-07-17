# Architecture: Phase 6 — DB-driven counter commands + `/rank` as dynamic slash commands

The per-stat counter commands (`tit`, `smart`, `crash`, `home`, …) and `rank`
move off the prefix registry onto `bot.tree` as `app_commands.Command`s,
registered dynamically from the DB and re-synced to guilds on admin CRUD. The
callbacks answer the interaction directly; the old reminder-channel mirror-post
is retired for these commands.

All discord.py 2.7.1 mechanics below were verified against the installed
interpreter (`.venv/bin/python`) — see "Verified facts" at the end.

## Tests this design satisfies

### tests/test_bot_wiring.py
- `test_register_stat_commands_adds_one_app_command_per_stat_plus_rank` — each DB stat + `rank` on `bot.tree`, off prefix.
- `test_register_stat_commands_is_idempotent` — second pass must not raise `CommandAlreadyRegistered`.
- `test_rank_slash_reports_no_usage_when_user_has_none` — `/rank` callback replies "…noch keine Befehle genutzt…".
- `test_rank_slash_reports_ordered_usage_when_user_has_data` — `/rank` reply lists stats ordered by count desc.
- `test_non_targeted_stat_slash_records_use_and_responds` — `/tit` callback increments `user_stats` and replies.
- `test_non_targeted_stat_slash_has_cooldown_and_no_member_param` — cooldown in `cmd.checks`, no `member` param.
- `test_non_targeted_stat_slash_calls_build_output` — callback delegates to module-global `build_output(repo, key, uid, name)` and sends its return text.
- `test_targeted_stat_slash_increments_target_and_invoker` — `/smart member` increments target counter + invoker `user_stats`; reply contains `member.mention`.
- `test_targeted_stat_slash_has_member_param_and_cooldown` — `member` param present + cooldown.
- `test_targeted_stat_slash_counts_per_target_separately` — per-target totals.
- `test_non_targeted_stats_are_unaffected_by_targeted_wiring`.
- `test_home_slash_targets_configured_julez_id_with_no_argument` — `home` no `member` param, fixed target `settings.julez_id`, reply contains `<@julez_id>`.
- `test_home_slash_is_skipped_when_julez_id_unset` — `home` not registered when `julez_id` falsy; `smart`/`crash` still register.
- Retained (must keep working, NOT changed by this phase): `test_send_or_update_*`, `test_send_rank_*` (helpers `_send_or_update`, `_send_ephemeral`, `_send_rank`, `bot._rank_last_posts` remain), plus all `on_message` / `on_command_error` / `on_ready` / gate / member-lifecycle tests.

### tests/test_admin_commands.py
- `test_admin_create_stat_adds_row_and_registers_live_tree_command` — live command on `bot.tree`, off prefix.
- `test_admin_create_targeted_stat_command_takes_member_argument` / `..._non_targeted..._has_no_member_argument`.
- `test_admin_archive_stat_unregisters_live_tree_command` / `test_admin_delete_stat_unregisters_tree_command_and_removes_row` — use `bot.tree.remove_command`.
- `test_admin_create_stat_reactivates_archived_key` — re-register on reactivation.
- `test_slash_admin_stat_add_registers_live_tree_counter_and_resyncs` / `..._rm_...` / `..._archive_...` — CRUD slash callbacks (un)register on tree + `resync` awaited.
- `test_admin_create_stat_triggers_resync` / `test_admin_archive_stat_triggers_resync` / `test_admin_delete_stat_triggers_resync` — helpers call `botmod.resync_stat_commands` (monkeypatchable).
- All other admin tests (message CRUD, reserved keys, edit, list, is_admin) — unchanged behavior.

### tests/test_command_list.py
- `test_curated_description_map_has_rank_blurb` and the non-xfail command-list tests keep passing with NO change to `build_command_list` / `_COMMAND_DESCRIPTIONS`. The 4 `@_PHASE7` xfail tests (`rank`/per-stat lines in the prefix-derived embed) stay xfail — do not touch `build_command_list` this phase.

## Files to modify

### `n3x_bot/bot.py`

**1. `register_stat_commands(bot, repo, settings)` (currently ~212–234) — rewrite.**
- Keep the loop: `for stat in await repo.list_stats(): _add_targeted_stat_command(...) if stat.targeted else _add_stat_command(...)`.
- Delete the prefix `_rank` closure and the `bot.add_command(commands.Command(_rank, name="rank"))` block.
- Add a dynamic `rank` app command guarded by `if bot.tree.get_command("rank") is None:`. Build the `rank` callback closure (below) and `bot.tree.add_command(app_commands.Command(name="rank", description=<german blurb>, callback=_rank_cmd))`. No cooldown decorator on `rank`.

**2. `_add_stat_command(bot, repo, settings, key)` (currently ~237–246) — rewrite to tree registration.**
- Guard: `if bot.tree.get_command(key) is not None: return` (was `bot.get_command`).
- Build the callback closing over `key`, `repo`, `settings` (NOT via a `_key=key` default arg — extra params become slash options):
  ```
  @app_commands.checks.cooldown(1, 20)
  async def _cmd(interaction: discord.Interaction):
      ...
  ```
  Body (Data flow §): call module-global `build_output`, then `interaction.response.send_message(text)`.
- `bot.tree.add_command(app_commands.Command(name=key, description=<desc>, callback=_cmd))`.
- `<desc>` = a short non-empty German string ≤100 chars, e.g. `f"Zählt {key}."` — description content is not test-pinned.

**3. `_add_targeted_stat_command(bot, repo, settings, key)` (currently ~249–286) — rewrite to tree registration.**
- Guard: `if bot.tree.get_command(key) is not None: return`.
- `home` special-case preserved: `if key == HOME_KEY: if not settings.julez_id: return` then build the no-arg home callback and add; `return`.
- Home callback:
  ```
  @app_commands.checks.cooldown(1, 20)
  async def _home_cmd(interaction: discord.Interaction):
      ...
  ```
- Ordinary targeted callback (the `member` OPTION is derived from the annotation — keep the annotation exactly `discord.Member`):
  ```
  @app_commands.checks.cooldown(1, 20)
  async def _tcmd(interaction: discord.Interaction, member: discord.Member):
      ...
  ```
- Add each via `bot.tree.add_command(app_commands.Command(name=key, description=<desc>, callback=_tcmd))`.

**4. Add module-level `async def resync_stat_commands(bot)` in `n3x_bot/bot.py`** (place near `sync_commands_to_guilds`, ~890). Body: `await sync_commands_to_guilds(bot)`. `sync_commands_to_guilds` already early-returns when `not bot.guilds`, so this is a safe no-op in unit tests (bot has no connected guilds). Must be a module-level name so `admin.py` can call `n3x_bot.bot.resync_stat_commands` and tests can monkeypatch it.

**5. KEEP unchanged:** `_send_or_update`, `_send_ephemeral`, `_send_rank`, `bot._rank_last_posts`, `bot._target_last_posts`, `build_output`, `build_target_output`, `build_command_list`, `_COMMAND_DESCRIPTIONS`. `_send_rank`/`_send_or_update` are still imported and exercised directly by `test_bot_wiring.py`; the counter callbacks simply no longer call them.

**6. `on_ready` (in `_wire_events`) — NO reordering needed.** Confirmed: `await register_stat_commands(...)` is at ~925 and `await sync_commands_to_guilds(bot)` at ~977, so the dynamic stat/rank commands are on the tree BEFORE the one-shot guild sync → startup stats publish on first ready. Do not move anything. (See Risks for the reconnect edge.)

### `n3x_bot/admin.py`

**7. `admin_archive_stat` (currently ~87–90):** replace `bot.remove_command(key)` with `bot.tree.remove_command(key)`, then `await botmod.resync_stat_commands(bot)`.

**8. `admin_delete_stat` (currently ~93–96):** replace `bot.remove_command(key)` with `bot.tree.remove_command(key)`, then `await botmod.resync_stat_commands(bot)`.

**9. `admin_create_stat` (currently ~41–70):** after the existing deferred-import `_add_*` registration, `await botmod.resync_stat_commands(bot)`. (Reactivation path re-registers because the tree guard now sees `None` after the earlier archive removed it.)

**10. Deferred-import / monkeypatch pattern (all three helpers):** import the bot module as an object and call the attribute at call time so the monkeypatch on `n3x_bot.bot.resync_stat_commands` is honored:
```
import n3x_bot.bot as botmod
...
await botmod.resync_stat_commands(bot)
```
The existing `from n3x_bot.bot import _add_stat_command, _add_targeted_stat_command` deferred import inside `admin_create_stat` stays (those are looked up once, not monkeypatched). `resync_stat_commands` MUST be reached via the module object, not a `from`-import, or the monkeypatch is missed.

- `admin_edit_stat` — NO resync / NO re-register (rename only; no test requires it).

## Data flow

### Non-targeted `/tit` (invoked in tests via `cmd.callback(interaction)`)
1. `interaction.user` → `(id, display_name)`.
2. `text = await build_output(repo, key, interaction.user.id, interaction.user.display_name)` — resolved as the bot-module global, so the `botmod.build_output` monkeypatch is honored; real `build_output` does `repo.record_use(...)` (the counter increment) and renders via `render_output`.
3. `await interaction.response.send_message(text)` — non-ephemeral. This IS the public counter post now.

### Targeted `/smart member`
1. `text = await build_target_output(repo, key, interaction.user.id, interaction.user.display_name, member.id, member.mention)` — updates invoker `user_stats` via `record_use` and the target's counter via `record_target_use`; renders with `target_display=member.mention` (so `<@id>` appears).
2. `await interaction.response.send_message(text)`.

### `/home` (no argument)
1. `text = await build_target_output(repo, HOME_KEY, interaction.user.id, interaction.user.display_name, settings.julez_id, f"<@{settings.julez_id}>")`.
2. `await interaction.response.send_message(text)`.

### `/rank`
1. `data = await repo.get_user_stats(interaction.user.id)`.
2. Empty → text with "…noch keine Befehle genutzt!"; else sort `data.items()` by count desc and render the same `📊 Command-Ranking` block currently in `_rank` (keep the `!{cmd}` line format so "tit"/"cry" substrings and their order are preserved).
3. `await interaction.response.send_message(text)`. No `_send_rank`, no channel post.

### Admin CRUD → live command + resync (e.g. `/admin stat add`)
1. Slash wrapper gates on `is_admin`; non-admin → ephemeral refusal, no mutation (unchanged).
2. Admin → `admin_create_stat` writes the row, deferred-imports `_add_*` and registers the `app_commands.Command` on `bot.tree`, then `await botmod.resync_stat_commands(bot)` → `sync_commands_to_guilds(bot)` (no-op without guilds).

## Dynamic `app_commands.Command` construction (mechanical recipe — verified)

For each command:
1. Define the callback as a nested `async def` inside `_add_stat_command` / `_add_targeted_stat_command` / `register_stat_commands`, closing over `key`/`repo`/`settings` from the enclosing scope. First param is `interaction`; targeted adds `member: discord.Member`. Do NOT add closure state as default parameters — every non-`interaction` parameter is turned into a slash option.
2. Apply `@app_commands.checks.cooldown(1, 20)` directly above the callback `async def` (stat commands only; NOT `rank`). On a bare coroutine this decorator appends its predicate to `callback.__discord_app_commands_checks__`.
3. Construct `cmd = app_commands.Command(name=key, description=<non-empty ≤100-char german>, callback=callback)`. The constructor derives `cmd.parameters` from the callback signature/annotations and copies `callback.__discord_app_commands_checks__` into `cmd.checks`.
4. `bot.tree.add_command(cmd)`.

Verified outcomes (prototype run):
- Targeted → `cmd.parameters == ['member']`; non-targeted/home/rank → `[]`.
- `cmd.checks` contains a predicate whose `__qualname__` is `_create_cooldown_decorator.<locals>.predicate` → `_has_app_cooldown(cmd)` is True for stats; rank has no such check.
- `cmd.callback` is the raw coroutine the tests invoke directly. Direct `.callback(interaction[, member])` BYPASSES cooldown/checks (checks run at `Command.invoke`, not on the raw callback) — tests only assert the cooldown is PRESENT in `cmd.checks`, never enforced, so this is correct and safe. Passing a `SimpleNamespace` member is fine because direct-callback invocation runs no transformers.

## Dependencies
- New packages: none.
- Internal: `discord.app_commands` (already imported in bot.py), `build_output`/`build_target_output`/`render_output` (existing), `sync_commands_to_guilds` (existing), `HOME_KEY` (existing), `RESERVED_STAT_KEYS` (existing, already includes `rank`).

## Build sequence (for the Coder)
1. Add module-level `async def resync_stat_commands(bot)` in `bot.py` (delegates to `sync_commands_to_guilds`).
2. Rewrite `_add_stat_command` to register a no-arg, cooldown’d `app_commands.Command` on `bot.tree` (guard on `bot.tree.get_command`).
3. Rewrite `_add_targeted_stat_command` for the `member`-option form and the `home` fixed-target/skip form (guard on `bot.tree.get_command`).
4. Rewrite `register_stat_commands`: keep the DB loop, drop the prefix `rank`, add the dynamic `rank` app command (no cooldown, guarded).
5. In `admin.py`: switch `admin_archive_stat`/`admin_delete_stat` to `bot.tree.remove_command`; add `await botmod.resync_stat_commands(bot)` to create/archive/delete via `import n3x_bot.bot as botmod`.
6. Run the three test files; then the full suite to confirm no regression (the retained `_send_*` and prefix-error tests must still be green, the 4 command-list tests stay xfail).

## Risks and open questions
- **Ephemerality (design choice, NOT test-pinned):** counter replies and `/rank` are NON-ephemeral (`interaction.response.send_message(text)` with no `ephemeral=`), preserving the old public counter-post semantics. The reminder-channel mirror-post via `_send_or_update` is RETIRED for these commands — the slash reply is now the public post, posted in the invoking channel rather than the reminder channel. If a channel mirror is still desired product-wise, flag back; tests neither require nor forbid it.
- **`_send_or_update` / `_send_rank` kept but unused by callbacks:** they remain because `test_bot_wiring.py` imports and tests them directly. Do not delete.
- **on_ready reconnect edge:** the guild sync is one-shot (`bot._stale_guild_commands_cleared`). A stat added via `/admin stat add` after startup relies on `resync_stat_commands` → `sync_commands_to_guilds` to publish. That is exactly what the CRUD helpers now do; no change to the one-shot startup guard is needed.
- **`build_command_list` drift (Phase 7):** it still enumerates the prefix `bot.commands`, which no longer contains `rank` or the per-stat counters. This is intentional and covered by the `@_PHASE7` xfail marks; leaving `_COMMAND_DESCRIPTIONS["rank"]` in place keeps `test_curated_description_map_has_rank_blurb` green. Do not rework it this phase.
- **`description` value is unconstrained by tests** — any non-empty ≤100-char German string works; pick something sensible per stat (e.g. `f"Zählt {stat.name}."`).

## Verified facts (discord.py 2.7.1, `.venv/bin/python`)
- `app_commands.checks.cooldown(1, 20)` on a bare coroutine → predicate qualname `_create_cooldown_decorator.<locals>.predicate`, landing in `cmd.checks` after `app_commands.Command(...)` construction (`Command.__init__: self.checks = getattr(callback, '__discord_app_commands_checks__', [])`).
- `app_commands.Command(name=..., description=..., callback=coro)` derives options from the callback signature: `member: discord.Member` → one option named `member`; interaction-only → no options.
- `cmd.callback` is the undecorated raw coroutine; direct invocation bypasses checks.
