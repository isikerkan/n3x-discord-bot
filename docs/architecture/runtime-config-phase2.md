# Architecture: Runtime Config Phase 2 — `!config` commands

Blueprint for turning `n3x-bot/tests/test_config_commands.py` (31 tests) green.
Implementation goes in a new module `n3x_bot/config_commands.py` plus one wiring
line in `n3x_bot/bot.py::build_bot`. No changes to `runtime_config.py`,
`admin.py`, `config.py`, or the repos — Phase 1 already provides everything the
commands consume.

## Tests this design satisfies

Channel subcommand (`config channel <purpose>`):
- `test_config_channel_valid_purpose_posts_view_with_channel_select` — valid purpose posts a View holding a `discord.ui.ChannelSelect`.
- `test_config_channel_select_callback_stores_id_and_refreshes` — select callback writes `<purpose>_channel_id = str(id)` and refreshes so `bot.runtime_config.welcome_channel_id == 999`.
- `test_config_channel_select_confirms_ephemerally` — callback replies via `interaction.response.send_message(..., ephemeral=True)`.
- `test_all_channel_purposes_map_to_expected_keys` — all 8 purposes in `CHANNEL_MAP` route to the right key.
- `test_config_channel_invalid_purpose_rejected_no_view` — bogus purpose: `ctx.send` awaited, no view, no DB write.
- `test_config_channel_non_admin_refused_no_view` — non-admin: "Berechtigung" text, no view, no DB write.

Role subcommand (`config role <purpose>`):
- `test_config_role_valid_purpose_posts_view_with_role_select`
- `test_config_role_select_callback_stores_id_and_refreshes`
- `test_all_role_purposes_map_to_expected_keys`
- `test_config_role_invalid_purpose_rejected_no_view`
- `test_config_role_non_admin_refused_no_view`

Message subcommand (`config message <purpose> <id>`):
- `test_config_message_stores_message_id_and_refreshes` — numeric id → set + refresh.
- `test_config_message_non_numeric_id_rejected_no_write` — `"55x"` → no write.
- `test_config_message_invalid_purpose_rejected_no_write` — bogus purpose → no write.
- `test_config_message_non_admin_refused`

Content setters:
- `test_config_gate_rewards_sets_value_and_refreshes` → key `gate_rewards`.
- `test_config_allowed_maps_sets_value_and_refreshes` → key `allowed_maps`.
- `test_config_voice_roles_sets_value_and_refreshes` → key `voice_achievement_roles`.
- `test_config_reminder_time_sets_value_and_refreshes` → key `reminder_time`.
- `test_config_content_setter_non_admin_refused`

Show:
- `test_config_show_includes_overridden_key_and_db_value` — overridden key name + DB value present.
- `test_config_show_lists_a_non_overridden_key_at_env_default` — key name + env value present.
- `test_config_show_non_admin_refused`

Reset:
- `test_config_reset_reverts_override_to_env_default` — deletes override + refresh reverts to `.env`.
- `test_config_reset_non_overridable_key_rejected` — `admin_role_id` rejected, no effect.
- `test_config_reset_unknown_key_rejected` — unknown key rejected, no DB write.
- `test_config_reset_non_admin_refused` — override survives.

Wiring:
- `test_register_config_commands_entrypoint_exists`
- `test_build_bot_registers_config_group`
- `test_config_group_exposes_expected_subcommands`
- `test_register_config_commands_is_idempotent`

## Files to create

### `n3x-bot/n3x_bot/config_commands.py`

Module-level constants (purpose→key maps, exactly as pinned by the tests'
`CHANNEL_MAP`/`ROLE_MAP` and the message spec):

```
CHANNEL_PURPOSES: dict[str, str] = {
    "welcome": "welcome_channel_id",
    "reminder": "reminder_channel_id",
    "gate_input": "gate_input_channel_id",
    "gate_stats": "gate_stats_channel_id",
    "milestone": "milestone_channel_id",
    "overview": "overview_channel_id",
    "kodex_check": "kodex_check_channel_id",
    "timer_overview": "timer_overview_channel_id",
}
ROLE_PURPOSES: dict[str, str] = {
    "target": "target_role_id",
    "gate_delete": "gate_delete_role_id",
    "base_timer": "base_timer_role_id",
}
MESSAGE_PURPOSES: dict[str, str] = {
    "timer_overview": "timer_overview_message_id",
}
```

Note: the test file's local `CHANNEL_MAP` includes `timer_overview →
timer_overview_channel_id`; the module's `CHANNEL_PURPOSES` must match that
(there is both a `timer_overview` channel key AND a `timer_overview` message
key — different maps, no conflict). Constant names are pinned by the TDD
handoff (`CHANNEL_PURPOSES`, `ROLE_PURPOSES`, `MESSAGE_PURPOSES`).

Two View classes (mirror the `KappaConfirmView` idiom: build the UI item in
`__init__`, assign `item.callback = self.<coro>`, keep a reference to the item,
`add_item`):

- `class ChannelConfigView(discord.ui.View)`
  - `__init__(self, repo, bot, key: str)` — store `repo`, `bot`, `key`; create
    `select = discord.ui.ChannelSelect(placeholder=...)`; `select.callback =
    self._on_select`; `self._select = select`; `self.add_item(select)`.
    `super().__init__(timeout=...)` (a finite timeout is fine; the tests never
    inspect it — use e.g. `timeout=120` to match a config-picker UX; not asserted).
  - `async def _on_select(self, interaction) -> None`:
    `channel = self._select.values[0]` (uses discord.py's `values → _values`
    fallback that the tests drive); `await self.repo.set_runtime_config(self.key,
    str(channel.id))`; `await self.bot.runtime_config.refresh(self.repo)`;
    `await interaction.response.send_message("<confirm text with channel.id>",
    ephemeral=True)`.

- `class RoleConfigView(discord.ui.View)`
  - Identical shape but `discord.ui.RoleSelect`, reads `role = self._select.values[0]`,
    writes `str(role.id)`.

Rationale for two concrete classes over one parametrized class: the select
*type* differs (`ChannelSelect` vs `RoleSelect`) and the tests locate the child
by `isinstance(c, discord.ui.ChannelSelect / RoleSelect)`, so the item type must
be fixed at construction. Two tiny classes are clearer than a factory switch.
(Reading `self._select.values[0]` — not `interaction`'s data — is exactly how
the tests drive it: they set `select._values = [fake]` then await
`select.callback(interaction)`.)

Wiring entrypoint:

- `def register_config_commands(bot, repo, settings) -> None`
  - Idempotency guard first: `if bot.get_command("config") is not None: return`.
  - Define the group + 9 subcommands as closures over `bot`, `repo`, `settings`
    (same closure style as `admin._register_prefix_commands`).

Group + subcommands (all via `@bot.group` / `@config.command`, prefix only):

```
@bot.group(name="config", invoke_without_command=True)
async def config(ctx):
    # usage hint, mirrors admin_group
    await ctx.send("Nutze `!config channel|role|message|gate-rewards|"
                   "allowed-maps|voice-roles|reminder-time|show|reset ...`.",
                   delete_after=5)
```

Nine subcommands (python fn name → registered `name=`):

| python fn        | `@config.command(name=...)` | signature                                  |
|------------------|-----------------------------|--------------------------------------------|
| `channel`        | `"channel"`                 | `(ctx, purpose)`                            |
| `role`           | `"role"`                    | `(ctx, purpose)`                            |
| `message`        | `"message"`                 | `(ctx, purpose, message_id: str)`           |
| `gate_rewards`   | `"gate-rewards"`            | `(ctx, *, value: str)` — see note           |
| `allowed_maps`   | `"allowed-maps"`            | `(ctx, *, value: str)`                       |
| `voice_roles`    | `"voice-roles"`             | `(ctx, *, value: str)`                       |
| `reminder_time`  | `"reminder-time"`           | `(ctx, value: str)`                          |
| `show`           | `"show"`                    | `(ctx)`                                      |
| `reset`          | `"reset"`                   | `(ctx, key)`                                 |

Note on `value` param: the tests call `.callback(ctx, "a:1,b:2")` with a single
positional token, so a plain positional `value: str` satisfies every test. For
real prefix usage a value like `"a:1, b:2"` contains spaces and would be split
across args — use a consume-rest keyword-only `*, value: str` for the
content setters so operators can pass spaces. This is invoked positionally in
tests (keyword-only params still bind positionally when called directly as a
Python function), so it stays green. `reminder_time` takes a single spaceless
token (`hh:mm`); a plain positional is fine there. Coder: verify `*, value:
str` still accepts `.callback(ctx, "a:1,b:2")` — it does, because the direct
`.callback(...)` call is an ordinary function call, not discord arg parsing.

## Files to modify

### `n3x-bot/n3x_bot/bot.py`
- Add import near the other command-module imports (top of file, alongside
  `from n3x_bot.admin import register_admin_commands`):
  `from n3x_bot.config_commands import register_config_commands`.
- In `build_bot` (currently lines 110–118, the `register_*` block), add one
  line after `register_admin_commands(bot, repo, settings)`:
  `register_config_commands(bot, repo, settings)`.
  Everything else in `build_bot` stays. The `bot.runtime_config =
  RuntimeConfig(settings)` on line 96 already exists — the commands mutate it
  via `refresh`.

No other files change. `tests/test_bot_wiring.py` already excludes `"config"`
from its wired-command assertions (per the handoff; confirmed at json_repo-
unrelated lines 75/90 of that test file), so the new top-level group won't trip
the wiring count checks.

## Per-subcommand logic (admin gate first, then validation)

Refusal string must CONTAIN "Berechtigung" — reuse the admin module's exact
style `"❌ Keine Berechtigung."`. Ordering is pinned: admin check BEFORE
purpose/arg validation, and on refusal NO view is posted and NO DB write occurs.

- `channel(ctx, purpose)`:
  1. `if not is_admin(ctx.author, settings): await ctx.send("❌ Keine
     Berechtigung.", delete_after=5); return`
  2. `if purpose not in CHANNEL_PURPOSES: await ctx.send("<invalid purpose
     msg>"); return` (no view)
  3. `view = ChannelConfigView(repo, bot, CHANNEL_PURPOSES[purpose]); await
     ctx.send("<prompt>", view=view)`
  The select callback (only reachable by an admin who received the view) does
  set + refresh + ephemeral confirm. Not re-gating inside the callback is
  intentional and matches the tests (they never assert a non-admin can't drive
  the callback). Documented as a known, accepted gap below.

- `role(ctx, purpose)`: same shape with `ROLE_PURPOSES` and `RoleConfigView`.

- `message(ctx, purpose, message_id: str)`:
  1. admin gate.
  2. `if purpose not in MESSAGE_PURPOSES: send reject; return` (no write).
  3. `if not message_id.isdigit(): send reject; return` (no write). Use
     `str.isdigit()` — rejects `"55x"`, accepts `"555"`. (`.isdigit()` is
     sufficient for the pinned cases; ids are always non-negative ints.)
  4. `await repo.set_runtime_config("timer_overview_message_id", message_id);
     await bot.runtime_config.refresh(repo); await ctx.send("<confirm>")`.

- Content setters `gate_rewards / allowed_maps / voice_roles / reminder_time`:
  1. admin gate.
  2. `await repo.set_runtime_config(<KEY>, value); await
     bot.runtime_config.refresh(repo); await ctx.send("<confirm>")`.
  Key per subcommand: `gate_rewards`→`"gate_rewards"`,
  `allowed_maps`→`"allowed_maps"`, `voice_roles`→`"voice_achievement_roles"`,
  `reminder_time`→`"reminder_time"`. No value validation is required by the
  tests; the resolver tolerates malformed overrides (falls back to `.env` on
  read). Do NOT pre-validate — the tests store raw strings and read back the
  parsed live value, so the store must be verbatim.

- `show(ctx)`:
  1. admin gate.
  2. `overrides = await repo.all_runtime_config()` (dict of key→str).
  3. For each `key` in a stable-ordered iteration of `OVERRIDABLE_KEYS`
     (sort for deterministic output), compute:
     - if `key in overrides`: `value = overrides[key]`, mark as overridden
       (e.g. a trailing `(Override)` / `*` marker).
     - else: `value = getattr(settings, key)` — every OVERRIDABLE key is a
       `Settings` attribute of the same name (verified against `config.py`
       lines 46–70), so this yields the `.env`/default base value.
     - render a line containing BOTH the literal key name and the string value
       (tests assert substring presence of `"gate_stats_channel_id"` and
       `"999"`, and `"welcome_channel_id"` and `"222"`).
  4. Render format is free (plain text or embed; `_sent_text` reads both). Use
     plain text lines. Mind the 2000-char Discord limit: with ~16 keys this is
     well under 2000, but chunk defensively — accumulate lines and flush via
     `ctx.send(chunk)` whenever the buffer would exceed ~1900 chars, then send
     the remainder. Multiple `ctx.send` calls are fine (`_sent_text` joins all).

- `reset(ctx, key)`:
  1. admin gate (before the guard — `test_config_reset_non_admin_refused`
     expects the override to survive).
  2. `if key not in OVERRIDABLE_KEYS: await ctx.send("<reject: not resettable>");
     return` — this rejects `admin_role_id`, `discord_token`, and unknown keys
     in one guard (all three are absent from `OVERRIDABLE_KEYS`), with no DB
     write.
  3. `await repo.delete_runtime_config(key); await
     bot.runtime_config.refresh(repo); await ctx.send("<confirm>")`. Refresh
     reverts the live resolver to the `.env` base.

Import `is_admin` from `n3x_bot.admin` and `OVERRIDABLE_KEYS` from
`n3x_bot.runtime_config` at module top (no import cycle: `config_commands`
imports `admin`, `admin` does not import `config_commands`; `bot` imports
`config_commands` at top level like it imports `admin`).

## Data flow

Representative trace — `!config channel welcome` by an admin, then picking a channel:

1. Prefix dispatch resolves `config` group → `channel` subcommand →
   `channel(ctx, "welcome")`.
2. `is_admin(ctx.author, settings)` → True.
3. `"welcome" in CHANNEL_PURPOSES` → key `"welcome_channel_id"`.
4. `view = ChannelConfigView(repo, bot, "welcome_channel_id")`; the ctor builds
   a `ChannelSelect` and binds `select.callback = view._on_select`.
5. `await ctx.send("<prompt>", view=view)` posts the picker.
6. Admin selects a channel; Discord invokes `select.callback(interaction)`
   (tests simulate via `select._values=[chan]; await select.callback(it)`).
7. `_on_select`: `chan = self._select.values[0]` →
   `await repo.set_runtime_config("welcome_channel_id", str(chan.id))` →
   `await bot.runtime_config.refresh(repo)` (reloads all overrides from DB into
   the resolver's `_overrides` cache) →
   `await interaction.response.send_message("<confirm>", ephemeral=True)`.
8. Next read of `bot.runtime_config.welcome_channel_id` returns the override
   (`RuntimeConfig._int` sees the cached override → `int("999")` → 999).

## Dependencies

- New packages: NONE. Uses only `discord`, `discord.ext.commands`, and the
  existing `n3x_bot.admin` / `n3x_bot.runtime_config`.
- Internal modules depended on:
  - `n3x_bot.admin.is_admin`
  - `n3x_bot.runtime_config.OVERRIDABLE_KEYS`
  - repo methods `set_runtime_config`, `get_runtime_config`,
    `delete_runtime_config`, `all_runtime_config` (present on `JsonRepository`
    and the SQL repos from Phase 1).
  - `bot.runtime_config` (a `RuntimeConfig` instance set in `build_bot`).

## Build sequence (for the Coder)

1. Create `n3x_bot/config_commands.py` with the three purpose→key constants.
2. Add imports (`discord`, `commands` unused directly but group is via
   `bot.group`; `is_admin`, `OVERRIDABLE_KEYS`).
3. Implement `ChannelConfigView` and `RoleConfigView` (ctor builds select +
   binds `_on_select`; `_on_select` does set → refresh → ephemeral confirm).
   Green-check: channel/role select-callback + ephemeral + purpose-mapping tests.
4. Implement `register_config_commands` with the idempotency guard, the `config`
   group, and the two select-posting subcommands (`channel`, `role`) incl.
   admin gate + purpose validation. Green-check: channel/role view-post +
   invalid-purpose + non-admin tests.
5. Add `message` subcommand (admin gate → purpose → numeric guard → set/refresh).
   Green-check: the four message tests.
6. Add the four content setters. Green-check: the five setter tests.
7. Add `show` (iterate `OVERRIDABLE_KEYS`, override-else-settings, chunked send).
   Green-check: the three show tests.
8. Add `reset` (admin gate → OVERRIDABLE_KEYS guard → delete/refresh).
   Green-check: the four reset tests.
9. Wire into `bot.py::build_bot` (import + one call after
   `register_admin_commands`). Green-check: the four wiring tests + full file.
10. Run the whole `test_config_commands.py` and `test_bot_wiring.py`.

## Risks and open questions

- **Prefix-only, no slash.** The TDD handoff pins prefix and asserts nothing
  about a slash mirror. `admin.py` ships both a prefix and a slash surface. I am
  NOT adding a slash `config` group — it's unexercised (respect-the-test-surface
  rule) and slash channel/role pickers would need a different (autocomplete/UI)
  design. Flagging as a deliberate scope call; raise to product if a slash
  surface is desired later.

- **Message id via plain arg, not a Modal.** Pinned by the handoff and
  `test_config_message_*`. Consistent — accepted, not changed.

- **Select callback is not re-gated for admin.** The view is only posted to an
  admin (the command gate), but Discord does not restrict who can interact with
  a posted component. The tests do not assert component-level author locking, so
  the design does not add it. This is a minor real-world gap: a non-admin in the
  same channel could theoretically operate the posted picker before it times
  out. If that matters, add an author-id check inside `_on_select` (mirroring
  `KappaConfirmView`'s `interaction.user.id != self.user_id` guard) — would need
  the view to capture `ctx.author.id`. Flagging for the user; NOT implementing
  since it's outside the pinned test surface.

- **`show` value source for derived keys.** For `gate_rewards` /
  `allowed_maps` / `voice_achievement_roles` / `reminder_time`, `show` prints
  the RAW stored/settings string (via `getattr(settings, key)` or the override
  string), not the parsed structure. Tests only check channel-id substrings, so
  raw is safe and arguably clearer for operators. Noting the choice.

- **Content-setter `*, value` vs positional.** Using consume-rest keyword-only
  `value` improves real UX (spaces in values) while staying green under direct
  `.callback(ctx, token)` calls. If the Coder finds any discord.py registration
  quirk with keyword-only params on a group subcommand, fall back to a plain
  positional `value: str` — all tests pass single spaceless tokens either way.
