# Architecture: Slash-command migration — Phase 1 (foundation + proof batch)

Scope: the FOUNDATION for the prefix→slash migration plus the proof batch of three
commands (`erfolge`, `activity`, `overview`). Everything else stays prefix. This is a
surgical set of edits to four files — no new modules.

## Tests this design satisfies

### `tests/test_slash_migration.py`
- `test_app_is_admin_true_for_member_holding_admin_role`
- `test_app_is_admin_false_for_member_without_admin_role`
- `test_app_is_admin_false_when_admin_role_unset`
- `test_app_is_admin_false_for_user_without_roles_attribute`
- `test_erfolge_is_app_command_not_prefix_command`
- `test_erfolge_app_command_responds_with_embed_for_caller`
- `test_erfolge_app_command_response_is_not_ephemeral`
- `test_activity_is_app_command_not_prefix_command`
- `test_activity_app_command_defaults_to_caller`
- `test_activity_app_command_uses_explicit_member`
- `test_overview_is_app_command_not_prefix_command`
- `test_overview_app_command_defers_before_posting`

### `tests/test_stale_guild_commands.py` (Part A — rewritten)
- `test_sync_commands_to_guilds_clears_copies_and_syncs_each_guild`
- `test_sync_commands_to_guilds_clears_then_copies_then_syncs_per_guild`
- `test_sync_commands_to_guilds_processes_all_guilds_despite_one_failing`
- `test_sync_commands_to_guilds_is_noop_with_no_guilds`
- `test_on_ready_syncs_commands_to_guilds_after_global_sync`
- `test_on_ready_syncs_commands_to_guilds_only_once_across_reconnects`
- Part B (`on_app_command_error`) tests — already GREEN with the existing handler; no change.

### `tests/test_achievements.py` (migration-affected only)
- `test_register_achievement_commands_wires_erfolge_app_command`
- `test_register_achievement_commands_is_idempotent`
- `test_erfolge_reports_unlocked_count_and_total`
- (all other achievements tests unaffected — pure defs/engine/repo, already GREEN)

### `tests/test_activity.py` (migration-affected only)
- `test_build_bot_registers_activity_app_command`
- `test_register_activity_registers_app_command_on_tree`
- `test_activity_command_reports_tracked_values`
- (all other activity tests unaffected)

### `tests/test_overview.py` (migration-affected only)
- `test_build_bot_registers_overview_app_command`
- `test_overview_command_triggers_post_overview`
- (all `build_overview_embed` / `post_overview` / `handle_overview_reaction` tests unaffected)

### `tests/test_command_list.py`
- No production change required. The three migrated commands leave `bot.commands`, so
  the registry-driven `build_command_list` drops them automatically. The command-list
  tests were adjusted upstream to expect that; they pass once the migration lands.

## Files to modify

### `n3x_bot/admin.py` — add app-command admin gate (Part B)
- Add, next to the existing `is_admin` (line 21):
  - `def app_is_admin(interaction, settings: Settings) -> bool` — mirrors `is_admin`
    but reads `interaction.user`. Body is exactly `is_admin`'s semantics against
    `interaction.user`:
    `return bool(settings.admin_role_id) and any(r.id == settings.admin_role_id for r in getattr(interaction.user, "roles", []))`
  - Note: the tests call `adminmod.app_is_admin(interaction, settings)` **synchronously**
    (`assert adminmod.app_is_admin(...) is True`). Define it as a **plain `def`**, NOT
    `async` and NOT an `app_commands.check` predicate. The task prose says "async
    helper"; the tests contradict that — see Risks. Implement it as a plain sync `def`.
  - `getattr(interaction.user, "roles", [])` handles the DM/no-`.roles` case
    (`SimpleNamespace(id=5)`) → returns `False`, never raises.
  - The `admin_role_id == 0` case is covered by `bool(settings.admin_role_id)` short-
    circuiting before the `any(...)`, so a role id of `0` never grants admin.
- Nothing else in `admin.py` changes; `app_is_admin` is not yet wired into any command
  this phase (only unit-tested).

### `n3x_bot/achievements.py` — migrate `erfolge` to a global app command (Part C)
- Add import: `from discord import app_commands` (top of file, near `import discord`).
- Rewrite `register_achievement_commands(bot, repo, settings)` (lines 288–325):
  - Change the idempotency guard from `if bot.get_command("erfolge") is not None:` to
    `if bot.tree.get_command("erfolge") is not None:` (tree-scoped now).
  - Extract the embed body (current lines 296–321) into a module-level helper so the
    callback stays thin and the logic is reusable/testable:
    `def _build_erfolge_embed(owned: set[str], display_name: str) -> discord.Embed`
    — takes the already-fetched `owned` achievement-id set and the caller's display
    name; returns the same gold embed the prefix command built. (The only edits vs.
    today: `ctx.author.display_name` → `display_name` param; `ctx.author.id` read moves
    to the callback.)
  - Register the app command via the tree-decorator form inside the register fn:
    ```
    @bot.tree.command(name="erfolge", description="Zeigt deine freigeschalteten Achievements.")
    async def erfolge(interaction):
        owned = await repo.get_user_achievements(interaction.user.id)
        embed = _build_erfolge_embed(owned, interaction.user.display_name)
        await interaction.response.send_message(embed=embed)
    ```
  - Public (NOT ephemeral) — `send_message(embed=embed)` with no `ephemeral=` kwarg,
    preserving current public-post behavior (`test_..._response_is_not_ephemeral`).
  - Remove `bot.add_command(commands.Command(_erfolge, name="erfolge"))` (line 325) and
    the old `_erfolge(ctx)` closure.
- Remove the now-unused `from discord.ext import commands` import (grep confirms line
  325 was its only use in this module).

### `n3x_bot/activity.py` — migrate `activity` to a global app command (Part C)
- Add import: `from discord import app_commands`.
- Rewrite `register_activity(bot, repo, settings)` (lines 244–267):
  - Guard: `if bot.tree.get_command("activity") is not None: return`.
  - Extract the embed body (current lines 250–265) into:
    `async def _build_activity_embed(repo, target) -> discord.Embed` — keeps the repo
    reads (`get_activity` ×3, `get_streak`, `get_night`) and builds the blurple embed
    titled `f"📊 Aktivität von {target.display_name}"`. `target` only needs `.id` and
    `.display_name` (tests pass `SimpleNamespace(id=…, display_name=…)`).
  - Register the app command with an optional member option:
    ```
    @bot.tree.command(name="activity", description="Zeigt die Aktivitätsstatistik.")
    @app_commands.describe(member="Nutzer, dessen Aktivität angezeigt werden soll.")
    async def activity(interaction, member: discord.Member | None = None):
        target = member or interaction.user
        embed = await _build_activity_embed(repo, target)
        await interaction.response.send_message(embed=embed)
    ```
  - `member` defaults to `None` → falls back to `interaction.user`
    (`test_..._defaults_to_caller`); an explicit member wins
    (`test_..._uses_explicit_member`, called as `.callback(interaction, target)`).
  - Remove the old `bot.add_command(commands.Command(_activity_cmd, name="activity"))`
    and the `_activity_cmd(ctx, ...)` closure.
- Remove the now-unused `from discord.ext import commands` import (grep confirms line
  267 was its only use).
- `_format_voice` (line 240) is unchanged and reused by `_build_activity_embed`.

### `n3x_bot/bot.py` — migrate `overview`, add `sync_commands_to_guilds`, rewire `on_ready`
1. **`register_overview_and_sync_commands` (lines 143–159):**
   - Migrate `overview` to an app command; keep `sync_achievements` as a prefix command
     unchanged (still uses `is_admin` + `ctx`).
   - Replace the `overview` block (lines 145–148) with:
     ```
     if bot.tree.get_command("overview") is None:
         @bot.tree.command(name="overview", description="Postet die Achievement-Übersicht.")
         async def overview(interaction):
             await interaction.response.defer()
             await post_overview(bot, repo, settings)
     ```
   - `defer()` is awaited BEFORE `post_overview` (order pinned by
     `test_overview_app_command_defers_before_posting`).
   - Leave the `sync_achievements` prefix registration (lines 150–159) exactly as is.
2. **Replace `clear_stale_guild_commands` (lines 845–852) with `sync_commands_to_guilds`:**
   ```
   async def sync_commands_to_guilds(bot) -> None:
       for guild in bot.guilds:
           try:
               bot.tree.clear_commands(guild=guild)
               bot.tree.copy_global_to(guild=guild)
               await bot.tree.sync(guild=guild)
           except Exception:
               log.exception("failed to sync commands to guild %s",
                             getattr(guild, "id", guild))
   ```
   - `clear_commands` / `copy_global_to` are sync; only `sync` is awaited — matches the
     `_FakeTree` in the test (MagicMock/MagicMock/AsyncMock). All three take `guild=guild`
     as a **keyword** (tests read `c.kwargs.get("guild")`). Order per guild is
     clear→copy→sync. `try/except` per iteration means one guild's failure never aborts
     the loop and the helper never raises. Empty `bot.guilds` → loop body never runs →
     no-op.
3. **`on_ready` wiring (lines 935–937):** replace the `clear_stale_guild_commands` call:
   ```
   if not bot._stale_guild_commands_cleared:
       await sync_commands_to_guilds(bot)
       bot._stale_guild_commands_cleared = True
   ```
   - Keep the existing flag name `bot._stale_guild_commands_cleared` (set in `build_bot`
     at line 127) to minimize churn — the task allows keeping or renaming; keep it.
   - This stays AFTER the global `await bot.tree.sync()` at line 928 (order pinned by
     `test_on_ready_syncs_commands_to_guilds_after_global_sync`).
   - Called by bare module-global name so `monkeypatch.setattr("n3x_bot.bot.sync_commands_to_guilds", …)`
     is honored (name resolved in module globals at call time).
   - Flag set only AFTER the call → one-shot across reconnects
     (`test_..._only_once_across_reconnects`) with self-heal on transient failure.
4. **No new import needed in bot.py:** the `@bot.tree.command()` decorator needs no
   `app_commands` symbol; `overview` has no typed options. bot.py already imports
   `discord` and `commands`; both remain in use (`sync_achievements`, stat commands, etc.).
5. **`build_command_list` / `_COMMAND_DESCRIPTIONS` (lines 357–412):** NO change. The
   three migrated commands leave `bot.commands`, so they drop from the registry-driven
   list automatically. Leaving any stale `_COMMAND_DESCRIPTIONS` entries for
   overview/activity/erfolge is harmless (looked up only when the command is present).
   Do not rework `build_command_list` this phase.

## Data flow

### `/erfolge` (representative)
1. Discord dispatches the interaction; discord.py resolves the global tree command
   `erfolge` and calls its callback with `interaction`.
2. Callback reads `owned = await repo.get_user_achievements(interaction.user.id)`.
3. `_build_erfolge_embed(owned, interaction.user.display_name)` builds the gold embed
   (count `len(owned)/TOTAL_ACHIEVEMENTS`, per-category progress, secret tally).
4. `await interaction.response.send_message(embed=embed)` — public.
- In tests, `bot.tree.get_command("erfolge").callback(interaction)` invokes step 2–4
  directly against a `MagicMock` interaction whose `response.send_message` is an
  `AsyncMock`.

### `/overview`
1. Callback awaits `interaction.response.defer()` (ack fast; the render is slow).
2. Awaits `post_overview(bot, repo, settings)` — unchanged: builds holders from the
   repo, posts/edits the paginated embed in `overview_channel_id`, tracks
   `bot._overview_state`.

### `sync_commands_to_guilds(bot)` (on_ready, one-shot)
1. Global `await bot.tree.sync()` publishes global app commands (existing, line 928).
2. Guard passes once → `sync_commands_to_guilds(bot)`: per guild,
   clear guild-scoped leftovers → copy globals into the guild (instant availability) →
   `sync(guild=…)` (registers ours, drops phantoms). Guard flag set True after.

## Dependencies
- New packages: **none**.
- Internal: unchanged. `admin.app_is_admin` uses only `Settings` + `interaction.user`.
  `achievements`/`activity` app commands add `from discord import app_commands`
  (already a discord.py dependency). `bot.py` reuses existing `post_overview`,
  `is_admin`, `sync_all_achievements` imports.

## Build sequence (for the Coder)
1. `admin.py`: add `app_is_admin` (plain sync `def`). Run the four Part-B tests in
   `test_slash_migration.py` — they only need `admin.py`.
2. `achievements.py`: add `app_commands` import, extract `_build_erfolge_embed`,
   convert `register_achievement_commands` to the tree-decorator form with the
   `bot.tree.get_command` guard, delete the prefix registration + unused `commands`
   import. Run the three erfolge tests (`test_achievements.py`) + the erfolge tests in
   `test_slash_migration.py`.
3. `activity.py`: mirror step 2 for `activity` (with the optional `member` option +
   `describe`). Run `test_activity.py` activity-command tests + activity tests in
   `test_slash_migration.py`.
4. `bot.py` — `register_overview_and_sync_commands`: convert `overview` to the tree
   command (defer→post), leave `sync_achievements`. Run the overview command tests in
   `test_overview.py` + `test_slash_migration.py`.
5. `bot.py` — replace `clear_stale_guild_commands` with `sync_commands_to_guilds` and
   rewire the `on_ready` call. Run `test_stale_guild_commands.py` Part A.
6. Full run of the six affected test files, then the whole suite to catch fallout in
   `test_command_list.py` (the three commands must be absent from `bot.commands`).

## Risks and open questions
- **`app_is_admin` sync vs async.** The task prose says "plain async helper (NOT an
  `app_commands.check`)", but the tests assert on the return value directly
  (`assert adminmod.app_is_admin(...) is True`) with no `await`. Awaiting is not
  possible there; an `async def` would return a coroutine and every assertion would
  fail. **Design decision: implement `app_is_admin` as a plain synchronous `def`.**
  This satisfies the tests and matches `is_admin`'s signature. Flagging the wording
  mismatch back to TDD/task author.
- **`@bot.tree.command()` vs `app_commands.Command(...)` + `add_command`.** Chosen the
  decorator form: it captures `repo`/`settings` via closure, mirrors the ergonomics of
  the existing `@stat_g.command` blocks in `admin.py`, and yields a tree command whose
  `.callback` attribute is the raw coroutine the tests invoke directly (`.callback(interaction)`,
  `.callback(interaction, target)`). The first callback param is `interaction` (no
  `self`) as discord.py requires; optional options carry a Python default so the raw
  callback is directly callable with just `interaction`. If a coder hits any discord.py
  quirk building the `activity` option, the equivalent explicit
  `app_commands.Command(name=…, description=…, callback=cb)` + `bot.tree.add_command(cmd)`
  form is a drop-in fallback with identical `.callback` semantics.
- **`activity` member annotation.** Uses `discord.Member | None = None`. discord.py 2.x
  renders this as an optional user option; the raw-callback test path bypasses transforms
  so a `SimpleNamespace` member is accepted. No `Optional[...]` import needed.
- **Part B error handler is already correct** (bot.py:960–978): `discord.NotFound`
  subclasses `HTTPException` (so the send-failure swallow already covers 10062), and the
  wrapped-`CommandNotFound` case is handled via `getattr(error, "original", error)`.
  Those `test_stale_guild_commands.py` Part B tests should pass with no change; if any
  fails, that's a genuine regression signal, not a migration task — surface it rather
  than editing the handler blindly.
- **Removing `from discord.ext import commands`** from `activity.py`/`achievements.py`:
  grep confirms the migrated prefix registration was the sole use in each. Safe to
  remove; if a coder finds another reference, keep the import.
