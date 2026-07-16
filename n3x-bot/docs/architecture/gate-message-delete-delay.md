# Architecture: configurable auto-delete of COMPLETED gate messages

Deletion delay for completed gate-input messages becomes configurable via `.env`
(`GATE_MESSAGE_DELETE_DELAY`, default `1m`), runtime-overridable through the
`runtime_config` DB (`!config gate-delete-delay <value>`), and applied uniformly
to both the a/b/c success path and the d/e/z/k drop-confirm success path.

## Tests this design satisfies

`tests/test_config.py`
- `test_parse_duration_plain_integer_is_seconds` — `parse_duration("90") == 90`
- `test_parse_duration_seconds_suffix` — `"30s" -> 30`
- `test_parse_duration_minutes_suffix` — `"1m" -> 60`, `"5m" -> 300`
- `test_parse_duration_hours_suffix` — `"2h" -> 7200`
- `test_parse_duration_combined_minutes_and_seconds` — `"1m30s" -> 90`
- `test_parse_duration_combined_hours_minutes_seconds` — `"1h1m1s" -> 3661`
- `test_parse_duration_rejects_malformed_input` — `"", "   ", "abc", "1x", "m", "1m1x"` raise `ValueError`
- `test_gate_message_delete_delay_defaults_to_one_minute` — Settings field default `"1m"`
- `test_gate_message_delete_delay_read_from_env` — env `GATE_MESSAGE_DELETE_DELAY=5m` -> `"5m"`
- `test_env_example_documents_gate_message_delete_delay` — `.env.example` contains `GATE_MESSAGE_DELETE_DELAY`

`tests/test_runtime_config.py`
- `test_gate_delete_delay_seconds_default_is_sixty` — property (no parens) -> 60
- `test_gate_delete_delay_seconds_reads_env_value` — settings `"5m"` -> 300
- `test_gate_delete_delay_seconds_db_override_wins` — override `"2m"` -> 120
- `test_gate_delete_delay_seconds_malformed_override_falls_back_to_sixty` — bad override -> 60
- `test_gate_message_delete_delay_is_overridable_key` — key in `OVERRIDABLE_KEYS`

`tests/test_config_commands.py`
- `test_config_gate_delete_delay_sets_value_verbatim_and_refreshes` — writes `"2m"` verbatim to `gate_message_delete_delay`; resolver reads 120 after refresh
- `test_config_group_exposes_expected_subcommands` — `gate-delete-delay` in the group's subcommand-name set
- `test_config_content_setter_non_admin_refused` / idempotency — unaffected (must still pass)

`tests/test_gate_input_autodelete.py`
- `test_abc_success_schedules_delete_with_configured_delay` (a/b/c param) — `message.delete(delay=60)`
- `test_abc_success_delay_reflects_resolver_override_value` — `delay=120`
- `test_abc_success_delete_uses_keyword_delay_not_positional` — `args == ()`, `kwargs == {"delay": 60}`
- `test_abc_dedup_reject_does_not_delete_and_adds_hourglass` — dedup ⏳ -> no delete
- `test_abc_success_still_reacts_check_and_runs_post_processing` — ✅ + embed + records preserved
- `test_abc_success_survives_delete_failure` — delete raises -> handler does not raise, store still happened
- `test_dezk_input_does_not_delete_message` — d/e/z/k input handler never deletes

`tests/test_gate_drop_reactions.py`
- `test_message_deleted_after_successful_store` — drop-confirm success now `message.delete(delay=60)` (was immediate)
- `test_message_delete_delay_reflects_configured_seconds` — resolver 120 -> `delay=120`
- `test_store_survives_delete_failure` — delete raises -> no crash, `delete(delay=60)` still awaited
- (existing drop tests: dedup/ignore/atomicity paths must remain green — they assert `delete.assert_not_awaited()` on non-store paths)

## Files to modify

### `n3x_bot/config.py`
- Add module-level `parse_duration(raw: str) -> int`, placed with the other
  `parse_*` helpers (after `parse_voice_roles`, before `class Settings`).
  Algorithm (lenient unit ordering, strict on junk):
  ```
  raw = raw.strip()
  if not raw: raise ValueError(...)
  if raw.isdigit(): return int(raw)                     # "90" -> 90
  if not re.fullmatch(r"(?:\d+[hms])+", raw): raise ValueError(...)
  total = 0
  for value, unit in re.findall(r"(\d+)([hms])", raw):
      total += int(value) * {"h": 3600, "m": 60, "s": 1}[unit]
  return total
  ```
  Requires `import re` at module top (not currently imported — add it).
  - `fullmatch(r"(?:\d+[hms])+", …)` is the gatekeeper: `"1x"`, `"m"`, `"1m1x"`,
    `"abc"` all fail it and raise. `"90"` is handled by the `isdigit` branch
    (a bare integer has no unit and would otherwise fail fullmatch). Order of
    tokens is NOT enforced — any sum of `h`/`m`/`s` tokens is accepted.
- Add `Settings` field, grouped with the gate vars (near `gate_rewards`, line ~80):
  `gate_message_delete_delay: str = "1m"` (env `GATE_MESSAGE_DELETE_DELAY`).
  It is a raw string; parsing happens at the resolver, never at load. The
  existing `_blank_env_to_default` before-validator (lines 92-104) is generic
  over all string fields, so an AMP-injected `GATE_MESSAGE_DELETE_DELAY=""`
  already drops to the `"1m"` default — no change needed there. Do NOT add an
  after-validator (a malformed value must be tolerated at the DB-override
  boundary, and the field must never brick startup).

### `n3x_bot/runtime_config.py`
- Import `parse_duration` from `n3x_bot.config` (extend the existing import
  block, lines 3-9).
- Add `"gate_message_delete_delay"` to the `OVERRIDABLE_KEYS` frozenset
  (append to the string-value group on line 21).
- Add a **property** (NOT a method — the tests read `rc.gate_delete_delay_seconds`
  without parens; `test_gate_delete_delay_seconds_default_is_sixty` etc.),
  placed with the derived getters (after `reminder_hm`, ~line 145):
  ```
  @property
  def gate_delete_delay_seconds(self) -> int:
      return self._derived(
          "gate_message_delete_delay", parse_duration,
          lambda: parse_duration(self._settings.gate_message_delete_delay))
  ```
  Reuses the existing `_derived(key, parse, fallback)` helper (lines 56-69),
  exactly like `gate_rewards_map` / `reminder_hm`. Resolution:
  - no override, settings `"1m"` -> `fallback()` -> `parse_duration("1m")` = 60
  - no override, settings `"5m"` -> `fallback()` -> 300  (the env-value test)
  - override `"2m"` -> `parse_duration("2m")` = 120
  - override `"garbage"` -> parse raises -> `_derived` logs + calls `fallback()`
    -> `parse_duration("1m")` = 60

### `n3x_bot/config_commands.py`
- Update the group help string (lines 100-102) to include `gate-delete-delay`
  in the pipe-separated usage list (e.g. after `reminder-time|`). Cosmetic; not
  asserted, but requested for consistency.
- Add a content-setter subcommand next to the other verbatim setters
  (after `reminder-time`, line 164), mirroring them exactly:
  ```
  @config.command(name="gate-delete-delay")
  async def gate_delete_delay(ctx, value: str):
      await _set_content(ctx, "gate_message_delete_delay", value)
  ```
  `_set_content` (lines 142-148) already does admin-gate + `set_runtime_config`
  (verbatim write) + `bot.runtime_config.refresh(repo)` + confirmation. Verbatim
  write is what `test_config_gate_delete_delay_sets_value_verbatim_and_refreshes`
  asserts (`get_runtime_config("gate_message_delete_delay") == "2m"`); the
  resolver does the parse (`gate_delete_delay_seconds == 120`).

### `n3x_bot/bot.py`
Two call-site changes; both read the delay from the resolver and guard the
delete so a permissions/HTTP failure never bubbles.

1. a/b/c success path in `handle_gate_input_message` (inside `if inserted:`,
   currently lines 690-706). This branch does NOT delete today. Append, AFTER
   the achievements post-processing (end of the `if inserted:` block):
   ```
   try:
       await message.delete(delay=bot.runtime_config.gate_delete_delay_seconds)
   except Exception:
       pass
   ```
   Acts on `message` directly (the tests assert on `message.delete`, not a
   fetched copy). Keyword `delay=` is required (Message.delete is keyword-only,
   and `test_abc_success_delete_uses_keyword_delay_not_positional` pins
   `kwargs == {"delay": 60}`, `args == ()`). The dedup path (`inserted` falsy)
   is outside this block, so it keeps the ⏳ and never deletes. The d/e/z/k
   branch returns at line 683 before reaching here, so those inputs never delete.

2. drop-confirm success path in `handle_gate_drop_confirmation` (line 759).
   Change the immediate delete to a scheduled one:
   ```
   await msg.delete()
   ->
   await msg.delete(delay=bot.runtime_config.gate_delete_delay_seconds)
   ```
   It is already inside the `if inserted:` try/except (lines 756-761) that
   fetches `msg`, so the guard requirement is met unchanged. The dedup-reject
   `else` branch (adds ⏳, no delete) stays as-is.

### `.env.example`
- Add near the other gate vars (after `GATE_REWARDS`, line 44):
  ```
  # Delay before a completed gate-input message is auto-deleted.
  # Accepts plain seconds ("90") or h/m/s tokens ("30s", "1m", "2h", "1m30s").
  # Runtime-overridable via `!config gate-delete-delay <value>`.
  GATE_MESSAGE_DELETE_DELAY=1m
  ```

## Data flow

Operator sets `!config gate-delete-delay 2m`:
1. `config.command(name="gate-delete-delay")` -> `_set_content(ctx, "gate_message_delete_delay", "2m")`.
2. `_set_content` admin-gates, `repo.set_runtime_config("gate_message_delete_delay", "2m")` (verbatim), then `bot.runtime_config.refresh(repo)` reloads the override cache (filtered to `OVERRIDABLE_KEYS`).

A user posts `a 500` in the gate-input channel:
1. `on_message` -> `handle_gate_input_message`. Parse succeeds; `a` is not a drop gate.
2. `repo.add_gate_entry(...)` returns `inserted=True`; ✅ reaction added.
3. Records/embed/chart/achievement post-processing runs (unchanged).
4. `delay = bot.runtime_config.gate_delete_delay_seconds` resolves: override `"2m"` present -> `parse_duration("2m")` = 120.
5. `message.delete(delay=120)` schedules deletion inside discord.py (returns immediately; the timer runs independently). Wrapped in try/except.

A user confirms a drop gate (clicks the laser icon on their `d 250.000`):
1. `on_raw_reaction_add` -> `handle_gate_drop_confirmation`. Pending claimed via `pop`; `add_gate_entry(..., laser_dropped=True)` -> `inserted=True`.
2. `msg = await channel.fetch_message(payload.message_id)`.
3. `await msg.delete(delay=bot.runtime_config.gate_delete_delay_seconds)` schedules deletion; guarded. Post-processing runs.

## Dependencies

- No new packages. `re` is stdlib (add the import to `config.py`).
- Internal: `runtime_config.py` imports `parse_duration` from `config.py`
  (same module the other parsers come from); `config_commands.py` reuses the
  existing `_set_content`; `bot.py` reads the existing `bot.runtime_config`
  resolver attribute already attached by `build_bot`.

## Build sequence (for the Coder)

1. `n3x_bot/config.py` — add `import re`, `parse_duration`, and the
   `gate_message_delete_delay` field. Run `tests/test_config.py` green.
2. `n3x_bot/runtime_config.py` — import `parse_duration`, extend
   `OVERRIDABLE_KEYS`, add the `gate_delete_delay_seconds` property. Run
   `tests/test_runtime_config.py` green.
3. `n3x_bot/config_commands.py` — add the `gate-delete-delay` subcommand and the
   help-string entry. Run `tests/test_config_commands.py` green.
4. `n3x_bot/bot.py` — add the guarded a/b/c delete; change the drop-confirm
   delete to `delay=…`. Run `tests/test_gate_input_autodelete.py` and
   `tests/test_gate_drop_reactions.py` green.
5. `.env.example` — add the documented variable. Confirms
   `test_env_example_documents_gate_message_delete_delay`.
6. Full focused run of the five test files to confirm no regression.

## Risks and open questions

- **Property vs method (confirmed):** `gate_delete_delay_seconds` MUST be a
  `@property`. Every test reads it without parentheses
  (`rc.gate_delete_delay_seconds == 60`, and the bot reads
  `bot.runtime_config.gate_delete_delay_seconds`). The test stubs also model it
  as a plain attribute (`SimpleNamespace(gate_delete_delay_seconds=seconds)`),
  which only round-trips with attribute (property) access, not a call.
- **Malformed-fallback semantics (chosen):** fallback is
  `parse_duration(self._settings.gate_message_delete_delay)`, NOT a hardcoded
  `60`. This is required, not stylistic: `_derived` returns `fallback()` in the
  no-override case too, so a hardcoded 60 would break
  `test_gate_delete_delay_seconds_reads_env_value` (`"5m"` must yield 300). For
  the default `"1m"` the parsed-settings fallback equals 60, so
  `..._malformed_override_falls_back_to_sixty` is satisfied by the same code
  path — the "fall back to 60" and "fall back to parsed .env" behaviours coincide
  by construction. Edge note (untested, low risk): if BOTH the DB override and
  the `.env` base string were malformed, `fallback()` would raise inside
  `_derived` (which only try/wraps `parse(override)`). The `.env` base is
  operator-controlled and defaults to a valid `"1m"`; the tests never exercise a
  malformed base. If defence-in-depth is later wanted, a `try/except -> 60`
  around the whole property body would close it, but it is out of the current
  test surface — flag rather than build.
- **Reading the delay after the pending is popped (not a problem):** in
  `handle_gate_drop_confirmation` the pending dict is `pop`ped before the store,
  and the message is separately `fetch`ed for deletion. `bot.runtime_config` is
  independent of the pending map, and `Message.delete(delay=…)` only *schedules*
  a background task in discord.py (it returns immediately without blocking), so
  reading the resolver once at the call site is correct and cheap. No ordering
  hazard.
- **No new edge cases introduced:** the design touches only the exact call sites
  and symbols the tests exercise. `parse_duration`'s leniency on unit ordering is
  intentional and matches the handoff (tests do not pin strict ordering; only
  trailing/interior junk like `"1m1x"` must raise, which the `fullmatch` gate
  enforces).
