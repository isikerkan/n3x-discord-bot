# Architecture: role-gated admin CRUD (`n3x_bot/admin.py`)

Turns the RED admin-CRUD suite green by adding a new modular feature file
`n3x_bot/admin.py`, one config field, and thin wiring in `bot.py`. Follows the
existing `gates.py` / `migrate.py` "one feature per module, bot.py just wires it"
split. No production code here — this is the coder's spec.

## Tests this design satisfies

`n3x-bot/tests/test_config.py`
- `test_admin_role_id_defaults_to_zero` — `Settings.admin_role_id` defaults to `0`.
- `test_admin_role_id_read_from_env` — `admin_role_id` reads env `ADMIN_ROLE_ID`.

`n3x-bot/tests/test_admin_commands.py`
- `test_is_admin_true_for_member_holding_admin_role` — `is_admin` True when member holds the admin role id.
- `test_is_admin_false_for_member_without_admin_role` — False when absent.
- `test_is_admin_false_when_admin_role_unset_even_if_member_has_role_zero` — False when `admin_role_id == 0` even if a member role id is `0`.
- `test_admin_stat_add_command_refuses_non_admin_and_mutates_nothing` — prefix `admin stat add` callback gates on `is_admin`; non-admin => `ctx.send` refusal, no DB write, no live command.
- `test_admin_create_stat_adds_row_and_registers_live_command` — `admin_create_stat` writes row + registers live `!<key>`.
- `test_admin_create_targeted_stat_command_takes_member_argument` — `targeted=True` => command has `member` param.
- `test_admin_create_non_targeted_stat_command_has_no_member_argument` — non-targeted => no `member` param.
- `test_admin_create_stat_links_message_by_name` — `message_name` resolves to `message_id`.
- `test_admin_create_stat_duplicate_key_raises_value_error` — duplicate key => `ValueError`.
- `test_admin_edit_stat_renames` — `admin_edit_stat(name=...)` renames via `update_stat`.
- `test_admin_edit_stat_relinks_message` — `admin_edit_stat(message_name=...)` relinks via `set_stat_message`.
- `test_admin_archive_stat_unregisters_live_command` — archive + `remove_command`, row kept with `archived_at`.
- `test_admin_delete_stat_unregisters_command_and_removes_row` — delete + `remove_command`, row gone.
- `test_admin_list_stats_excludes_archived_by_default` — `admin_list_stats` honors `include_archived`.
- `test_admin_create_message_adds_message` — `admin_create_message` writes row.
- `test_admin_create_message_duplicate_name_raises_value_error` — duplicate name => `ValueError`.
- `test_admin_edit_message_updates_template` / `test_admin_edit_message_renames` — `admin_edit_message`.
- `test_admin_archive_message_hides_from_default_list` — `admin_archive_message`.
- `test_admin_delete_message_removes_row` — `admin_delete_message`.
- `test_admin_list_messages_returns_non_archived` — `admin_list_messages`.
- `test_build_bot_registers_admin_prefix_group_with_subgroups` — `bot.get_command("admin")` is a `commands.Group` with `stat` + `msg` subgroups.
- `test_admin_stat_subgroup_exposes_crud_subcommands` — `stat` subgroup exposes `{add, edit, archive, rm, list}`.
- `test_admin_msg_subgroup_exposes_crud_subcommands` — `msg` subgroup exposes `{add, edit, archive, rm, list}`.
- `test_build_bot_registers_admin_slash_group_on_tree` — `bot.tree.get_commands()` contains `admin`.
- `test_register_admin_commands_entrypoint_exists` — `register_admin_commands` is callable on `n3x_bot.bot`.

## Files to create

### `n3x_bot/admin.py` (new feature module)

All helpers are module-level `async` functions (Discord-free, unit-testable) —
they take `repo` and mutate through it, mirroring how `build_output` is testable.
Stat-mutating helpers take `(bot, repo, settings, ...)` to match the RED call
signatures exactly (even where `settings` is presently unused); message helpers
take only `(repo, ...)`.

Permission helper:
- `def is_admin(member, settings) -> bool`
  `return bool(settings.admin_role_id) and any(r.id == settings.admin_role_id for r in member.roles)`.
  The `bool(...)` guard makes the disabled (`0`) case return False even if a member holds role id `0`.

Internal resolver:
- `async def _resolve_message_id(repo, name: str) -> int`
  Looks up a message by name over `await repo.list_messages(include_archived=True)`;
  returns its `id`, or raises `ValueError(f"no message named {name!r}")` if absent.
  Shared by `admin_create_stat` and `admin_edit_stat`.

Stat helpers:
- `async def admin_create_stat(bot, repo, settings, key, name, targeted=False, message_name=None) -> Stat`
  1. If `await repo.get_stat(key) is not None` => `raise ValueError(f"stat {key!r} already exists")` (repo does NOT guard duplicates; pre-check here).
  2. `message_id = await _resolve_message_id(repo, message_name) if message_name else None` (raises `ValueError` on miss).
  3. `stat = await repo.create_stat(key, name, message_id=message_id, targeted=targeted)`.
  4. Register live command reusing bot.py's existing registrars (see import-cycle section): `_add_targeted_stat_command(bot, repo, settings, key)` if `targeted` else `_add_stat_command(bot, repo, settings, key)`. Those already dedup via `bot.get_command(key)`.
  5. `return stat`.
- `async def admin_edit_stat(bot, repo, settings, key, name=None, message_name=None) -> Stat`
  - `if name is not None: await repo.update_stat(key, name=name)`.
  - `if message_name is not None: await repo.set_stat_message(key, await _resolve_message_id(repo, message_name))`.
  - `return await repo.get_stat(key)`. (No targeted toggle — repo has no flip method; out of scope.)
- `async def admin_archive_stat(bot, repo, settings, key) -> None`
  - `await repo.archive_stat(key)`; then `bot.remove_command(key)` (returns None safely if never registered).
- `async def admin_delete_stat(bot, repo, settings, key) -> None`
  - `await repo.delete_stat(key)`; then `bot.remove_command(key)`.
- `async def admin_list_stats(repo, include_archived=False) -> list[Stat]`
  - `return await repo.list_stats(include_archived=include_archived)`.

Message helpers:
- `async def admin_create_message(repo, name, template) -> Message`
  - Pre-check duplicate: if any `m.name == name` in `await repo.list_messages(include_archived=True)` => `raise ValueError(f"message {name!r} already exists")`.
  - `return await repo.create_message(name, template)`.
- `async def admin_edit_message(repo, message_id, name=None, template=None) -> Message`
  - `return await repo.update_message(message_id, name=name, template=template)`.
- `async def admin_archive_message(repo, message_id) -> None`
  - `await repo.archive_message(message_id)`.
- `async def admin_delete_message(repo, message_id) -> None`
  - `await repo.delete_message(message_id)`.
- `async def admin_list_messages(repo, include_archived=False) -> list[Message]`
  - `return await repo.list_messages(include_archived=include_archived)`.

Registration entrypoint:
- `def register_admin_commands(bot, repo, settings) -> None` — wires BOTH surfaces (see wiring sections below). Called once from `build_bot`.

### `docs/architecture/admin-crud.md`
This blueprint (already being written).

## Files to modify

### `n3x_bot/config.py`
Add one field alongside the other Discord-id fields (near line 17, after `julez_id`):
- `admin_role_id: int = 0`
Env var name is the uppercased field name (`ADMIN_ROLE_ID`), consistent with
`target_role_id` -> `TARGET_ROLE_ID`. No validator change needed.

### `n3x_bot/bot.py`
1. Add a top-level import that both re-exports the admin API on `n3x_bot.bot`
   (so `botmod.admin_create_stat` etc. resolve — the RED tests reference every
   symbol through the module object) and pulls in the entrypoint:
   ```
   from n3x_bot.admin import (
       is_admin,
       admin_create_stat, admin_edit_stat, admin_archive_stat,
       admin_delete_stat, admin_list_stats,
       admin_create_message, admin_edit_message, admin_archive_message,
       admin_delete_message, admin_list_messages,
       register_admin_commands,
   )
   ```
   Optionally add these names to a module `__all__`, but the bare import is
   sufficient for attribute resolution. This is the ONLY module-level coupling
   direction: `bot -> admin`. `admin` must NOT import `bot` at module top.
2. In `build_bot` (currently lines 62-64, after `register_gate_commands`), add:
   `register_admin_commands(bot, repo, settings)` before `return bot`.
3. Everything else in `bot.py` is unchanged. In particular the existing gate
   `!stat` / `!del` commands stay exactly as-is — the admin feature uses the
   `admin stat` subgroup and `admin msg`, never a top-level `!stat`.

### `n3x-bot/.env.example`
Under the "Discord IDs / behavior" block, add:
```
# Role allowed to run !admin ... and /admin ... CRUD commands. 0 disables.
ADMIN_ROLE_ID=0
```

## Import-cycle resolution

Constraint: `bot.py` imports `admin.py` at module top (for re-export + the
`register_admin_commands` call in `build_bot`), AND `admin_create_stat` needs
`bot.py`'s private `_add_stat_command` / `_add_targeted_stat_command` to register
the live command.

**Chosen fix: function-local (deferred) import inside `admin_create_stat`.**
```
# inside admin_create_stat, at call time:
from n3x_bot.bot import _add_stat_command, _add_targeted_stat_command
```
`admin.py` has NO top-level `import n3x_bot.bot`. So when Python imports `bot.py`,
it triggers `admin.py`'s import, which completes cleanly (no back-reference), and
`bot.py` finishes defining `_add_stat_command` et al. By the time any
`admin_create_stat` call runs, `n3x_bot.bot` is fully initialized, so the local
import resolves.

Justification: minimal footprint and it keeps the low-level dynamic-registration
logic (which closes over `build_output` / `_send_or_update` / `_send_ephemeral`,
all bot.py-local) exactly where it already lives — moving those into a shared
module would cascade four more functions and their helpers for no test benefit.
Deferred import is the standard, lowest-risk way to break a genuine two-way cycle
here. Rejected alternatives: (a) moving registrars to a `commands_dyn.py` shared
module — larger blast radius, more churn; (b) passing the registrar callable into
`register_admin_commands` — leaks a bot.py implementation detail through the
public entrypoint signature and complicates the direct helper calls the tests make.

## Prefix command wiring (inside `register_admin_commands`)

Built with the non-cog decorator form so each callback closes over
`bot, repo, settings`, and `.callback(ctx, ...)` is a plain function the tests can
call directly (no `self`). Gating is done INLINE at the top of each callback (a
`commands.check` would NOT run when the test invokes `.callback` directly, and the
non-admin test asserts a `ctx.send` refusal from the direct call).

```
@bot.group(name="admin", invoke_without_command=True)
async def admin_group(ctx): ...            # no-op / brief usage hint

@admin_group.group(name="stat", invoke_without_command=True)
async def admin_stat(ctx): ...

@admin_group.group(name="msg", invoke_without_command=True)
async def admin_msg(ctx): ...
```

Stat subcommands (function names distinct; `name=` sets the command token):
- `@admin_stat.command(name="add")` `async def admin_stat_add(ctx, key, name, targeted: bool = False, message: str | None = None)`
  - `if not is_admin(ctx.author, settings): await ctx.send("❌ Keine Berechtigung.", delete_after=5); return`
  - delegate: `await admin_create_stat(bot, repo, settings, key, name, targeted=targeted, message_name=message)`, then `ctx.send` a confirmation.
  - Signature note: the RED test calls `add_cmd.callback(ctx, "newkey", "New Stat")` — two positionals — so `targeted`/`message` MUST be trailing optionals with defaults. See Risks re: `--flag` syntax.
- `@admin_stat.command(name="edit")` `async def admin_stat_edit(ctx, key, name: str | None = None, message: str | None = None)` -> gate -> `admin_edit_stat(bot, repo, settings, key, name=name, message_name=message)`.
- `@admin_stat.command(name="archive")` `async def admin_stat_archive(ctx, key)` -> gate -> `admin_archive_stat(...)`.
- `@admin_stat.command(name="rm")` `async def admin_stat_rm(ctx, key)` -> gate -> `admin_delete_stat(...)`.
- `@admin_stat.command(name="list")` `async def admin_stat_list(ctx)` -> gate -> `admin_list_stats(repo)` -> `ctx.send` a formatted list.

Message subcommands:
- `@admin_msg.command(name="add")` `async def admin_msg_add(ctx, name, *, template)` -> gate -> `admin_create_message(repo, name, template)` (keyword-only `template` consumes the rest of the line so templates with spaces survive).
- `@admin_msg.command(name="edit")` `async def admin_msg_edit(ctx, message_id: int, name: str | None = None, *, template: str | None = None)` -> gate -> `admin_edit_message(repo, message_id, name=name, template=template)`.
- `@admin_msg.command(name="archive")` `async def admin_msg_archive(ctx, message_id: int)` -> gate -> `admin_archive_message(repo, message_id)`.
- `@admin_msg.command(name="rm")` `async def admin_msg_rm(ctx, message_id: int)` -> gate -> `admin_delete_message(repo, message_id)`.
- `@admin_msg.command(name="list")` `async def admin_msg_list(ctx)` -> gate -> `admin_list_messages(repo)` -> `ctx.send`.

Keep every callback body THIN (gate + delegate + one `ctx.send`). All real logic
lives in the already-tested helpers; thin wrappers keep the untested-branch
surface small (coverage).

## Slash command wiring (inside `register_admin_commands`)

`build_bot` builds a `commands.Bot`, which auto-creates `bot.tree`
(`app_commands.CommandTree`). Only structural presence is tested (no `sync`).

```
admin_g = app_commands.Group(name="admin", description="Admin CRUD")
stat_g  = app_commands.Group(name="stat", description="Stat CRUD", parent=admin_g)
msg_g   = app_commands.Group(name="msg",  description="Message CRUD", parent=admin_g)
```
`parent=admin_g` auto-attaches the subgroups to `admin_g`. `/admin stat add` is
group -> subgroup -> command = exactly Discord's max 2-level nesting depth (OK).

Slash subcommands mirror the prefix set, gating INLINE on
`is_admin(interaction.user, settings)` (uniform with prefix; avoids
`app_commands.checks.has_role(0)` when the feature is disabled), delegating to the
SAME helpers, and replying via `interaction.response.send_message(...)`:
- `@stat_g.command(name="add")` `async def slash_stat_add(interaction, key: str, name: str, targeted: bool = False, message: str | None = None)`
- `edit / archive / rm / list` analogous on `stat_g`.
- `add / edit / archive / rm / list` on `msg_g`.

Finally register the top group on the tree (subgroups ride along):
`bot.tree.add_command(admin_g)`.

`bot.tree.sync()` is NOT called in the unit path. If live registration is desired,
add `await bot.tree.sync()` inside the existing `on_ready` in `_wire_events`
(flagged, optional) — but that is a network call and not required by any test.

## Data flow

Representative: `!admin stat add boop "Boop" true boop_msg` (or the test's direct
`admin_create_stat(bot, repo, settings, "boop", "Boop", message_name="boop_msg")`).

1. Prefix path: `on_message` -> `bot.process_commands` routes to
   `admin` -> `stat` -> `add`; `admin_stat_add(ctx, "boop", "Boop", True, "boop_msg")`.
2. `is_admin(ctx.author, settings)` — non-admin short-circuits to a `ctx.send`
   refusal and returns (no mutation, no command registered).
3. Admin path delegates to `admin_create_stat(bot, repo, settings, "boop", "Boop", targeted=True, message_name="boop_msg")`.
4. Helper: `repo.get_stat("boop")` is None (else `ValueError`); `_resolve_message_id`
   scans `repo.list_messages(include_archived=True)` for `boop_msg` -> id (else `ValueError`).
5. `repo.create_stat("boop", "Boop", message_id=<id>, targeted=True)` writes + flushes.
6. Deferred `from n3x_bot.bot import _add_targeted_stat_command`; call it ->
   `bot.add_command(commands.Command(_tcmd, name="boop"))` with a `member` param.
7. Now `bot.get_command("boop")` is live and `repo.get_stat("boop")` exists — the
   assertions the tests check.

Archive/delete flow: helper hits `repo.archive_stat`/`delete_stat`, then
`bot.remove_command(key)` tears the live command down (safe no-op if it was never
registered, e.g. `home` skipped when `julez_id` is unset).

## Dependencies

- New packages: NONE. Uses `discord.ext.commands` and `discord.app_commands`
  (discord.py 2.7.1, already pinned).
- Internal modules `admin.py` depends on: `n3x_bot.models` (types only, optional
  for annotations), `n3x_bot.storage.base.StatsRepository` (via the passed `repo`),
  and — at call time only — `n3x_bot.bot`'s `_add_stat_command` /
  `_add_targeted_stat_command`.

## Build sequence (for the Coder)

1. `config.py`: add `admin_role_id: int = 0`. Run `test_config.py::test_admin_role_id_*` — green.
2. `.env.example`: add `ADMIN_ROLE_ID=0`.
3. Create `admin.py` with `is_admin`, `_resolve_message_id`, all stat + message
   helpers (deferred bot import inside `admin_create_stat`), and a stub
   `register_admin_commands` that does nothing yet. This makes all the pure-helper
   tests runnable.
4. `bot.py`: add the top-level `from n3x_bot.admin import (...)` re-export block.
   Run the helper tests (`is_admin`, `admin_create_*`, `admin_edit_*`,
   `admin_archive_*`, `admin_delete_*`, `admin_list_*`) — green.
5. Flesh out `register_admin_commands`: prefix `admin`/`stat`/`msg` groups +
   `add/edit/archive/rm/list` subcommands (inline `is_admin` gate), then the slash
   `admin_g`/`stat_g`/`msg_g` groups + `bot.tree.add_command(admin_g)`.
6. `bot.py`: call `register_admin_commands(bot, repo, settings)` in `build_bot`.
   Run the structural/registration tests + the non-admin refusal test — green.
7. Full focused run: `pytest tests/test_admin_commands.py tests/test_config.py`.
   Confirm coverage >= 80%.

## Risks and open questions

- `--targeted` / `--message <name>` syntax vs. positional optionals: the task
  described the prefix UX as `add <key> <name> [--targeted] [--message <name>]`,
  but the RED test invokes `add_cmd.callback(ctx, "newkey", "New Stat")` with only
  positionals. A discord.py `--flag` UX needs a `FlagConverter` keyword-only param,
  which has no default and would break that direct-callback call. DECISION: use
  trailing optional params (`targeted: bool = False, message: str | None = None`),
  so real usage is `!admin stat add boop "Boop" true boop_msg`. This deviates from
  the `--flag` wording but satisfies the tests. Flag for product sign-off if the
  literal `--flag` UX is a hard requirement (would require a FlagConverter redesign
  the current tests can't express).
- Import cycle: relies on the deferred import in `admin_create_stat` and on
  `admin.py` NEVER importing `bot` at module top. If a future edit adds a top-level
  `import n3x_bot.bot` to `admin.py`, the cycle returns. Documented above.
- `app_commands.Group` nesting: `/admin stat add` sits at Discord's max nesting
  depth (2 subgroup levels). No room to add a further level later without
  restructuring. Subgroups must be created with `parent=admin_g` and only the top
  group added to the tree.
- Live re-registration edge cases: `admin_archive_stat` / `admin_delete_stat` call
  `bot.remove_command(key)` unconditionally; that's a safe no-op when the command
  was never registered (e.g. `home` when `julez_id` is unset, or an archived stat
  reloaded without a command). No guard needed.
- Duplicate detection lives in the helpers (`get_stat` pre-check;
  `list_messages` name scan) because `repo.create_stat` / `create_message` do NOT
  guard and would silently append a second row otherwise.
- Slash `sync`: `build_bot` registers the group structurally but does not
  `tree.sync()`. Live slash availability requires adding `await bot.tree.sync()`
  to `on_ready` — intentionally left out of the unit path; call out for the
  deployment step.
- Coverage: slash callback bodies and the admin (non-refusal) prefix branches are
  not executed by the suite. Keeping wrappers thin (gate + delegate + one send) is
  deliberate to hold total coverage >= 80%, since all substantive logic sits in the
  directly-tested helpers.
- No TDD-stage flagged assumptions are being changed; the tests are taken verbatim
  as the contract.
