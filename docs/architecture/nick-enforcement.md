# Architecture: Nick / Prefix Enforcement (v3 port #7)

Behavior-preserving refactor + hardening. Extract the inline `enforce_prefix`
closure from `n3x_bot/bot.py::_wire_events` into a new modular, unit-tested
module `n3x_bot/nicknames.py` with a **pure** `desired_nick` decision helper and
an `async enforce_nick` Discord-side wrapper. No config, no storage changes.

## Tests this design satisfies

New RED suite `n3x-bot/tests/test_nicknames.py` (18 tests):

Pure `desired_nick`:
- `test_desired_nick_adds_prefix_for_role_holder_without_prefix` — `("Player", True, "[N3X]") == "[N3X]Player"`
- `test_desired_nick_returns_none_for_already_prefixed_role_holder` — `("[N3X]Player", True) is None` (B5)
- `test_desired_nick_removes_prefix_for_non_role_holder` — `("[N3X]Player", False) == "Player"`
- `test_desired_nick_returns_none_for_unprefixed_non_role_holder` — `("Player", False) is None`
- `test_desired_nick_strips_legacy_r3x_marker` — `("R3XPlayer", True) == "[N3X]Player"`
- `test_desired_nick_truncates_base_keeping_full_prefix_within_32` — 40-char name → `PREFIX + "X"*(32-len(PREFIX))`, `len <= 32`, still startswith prefix
- `test_desired_nick_handles_whitespace_only_name` — `("   ", True) == "[N3X]"` (base empty after strip)
- `test_desired_nick_role_holder_named_only_prefix_is_noop` — `("[N3X]", True) is None`
- `test_desired_nick_removal_strips_stray_leading_space` — `("[N3X] Player", False) == "Player"`

Async `enforce_nick`:
- `test_enforce_nick_edits_and_returns_true_when_role_granted` — edits `nick="[N3X]Player"`, returns `True`
- `test_enforce_nick_edits_and_returns_true_when_role_revoked` — edits `nick="Player"`, returns `True`
- `test_enforce_nick_does_not_edit_correct_role_holder` — no edit, returns `False` (B5)
- `test_enforce_nick_skips_bot_member` — no edit, `False`
- `test_enforce_nick_skips_guild_owner` — no edit, `False`
- `test_enforce_nick_skips_when_bot_lacks_manage_nicknames` — no edit, `False`
- `test_enforce_nick_skips_when_member_outranks_bot` — member `top_role`(10) >= bot `top_role`(10) → no edit, `False`
- `test_enforce_nick_swallows_edit_failure_and_returns_false` — `member.edit` raises, must not propagate, returns `False`

Existing integration tests that MUST STAY GREEN (unchanged nick output):
- `tests/test_bot_wiring.py::test_on_member_update_adds_prefix_when_target_role_granted` — nick `"[N3X]Player"`
- `tests/test_bot_wiring.py::test_on_member_update_removes_prefix_when_target_role_revoked` — nick `"Player"`
- `tests/test_bot_wiring.py::test_on_member_update_skips_bot_and_owner_and_unprivileged_cases` — no edit
- `tests/test_welcome.py::test_on_member_join_still_runs_enforce_prefix` — join role-holder edited, nick startswith prefix
- `tests/test_kodex.py` join-path test (relies on `enforce_*` early-returning when bot lacks `manage_nicknames`)

## Files to create

### `n3x-bot/n3x_bot/nicknames.py`

Module docstring in the style of `admin.py` / `welcome.py`: one paragraph noting
this is the extracted, unit-testable prefix-enforcement logic; `desired_nick` is
a pure decision helper (no Discord), `enforce_nick` is the thin Discord wrapper.

Import only `from n3x_bot.config import Settings` for the type hint. No `discord`
import needed (member is duck-typed, mirroring how the closure used only
attribute access — keeps the module import-light and the tests mock-free on the
pure side).

**`def desired_nick(display_name: str, has_role: bool, prefix_str: str) -> str | None`**

Pure. Branch logic, in this order:

1. `if has_role and not display_name.startswith(prefix_str):`
   - `base = display_name.replace("R3X", "").replace(prefix_str, "").strip()`
   - `return prefix_str + base[:32 - len(prefix_str)]`
   - Safe-truncation expression: trim the BASE to `32 - len(prefix_str)` so the
     full prefix always survives and total length `<= 32`. (The old closure did
     `f"{prefix_str}{base}"[:32]`, which would clip into the prefix if the prefix
     were long; for `"[N3X]"` (len 5) both yield the same result, so the 40-char
     integration expectation is preserved — this is the hardening.)
2. `if has_role and display_name.startswith(prefix_str):`
   - `return None` — already correct, B5 "only edit when needed".
   - (Whitespace-only `"   "` falls in branch 1, not here, because it does not
     start with the prefix → base empties to `""` → returns bare `"[N3X]"`.
     `"[N3X]"` itself starts with the prefix → this branch → `None`.)
3. `if not has_role and display_name.startswith(prefix_str):`
   - `return display_name[len(prefix_str):].strip()` — de-prefix; `.strip()` is
     the hardening so `"[N3X] Player"` → `"Player"`, never `" Player"`.
4. else (`not has_role and not startswith`):
   - `return None` — nothing to do.

Implementation note for the coder: branches 1 and 2 can be a single
`if has_role:` with an inner `if display_name.startswith(prefix_str): return None`
guard first; branches 3 and 4 a single `else:` with the startswith guard. Either
shape is fine as long as the four outcomes above hold.

**`async def enforce_nick(member, settings) -> bool`**

`member` is a `discord.Member` (duck-typed; untyped param like nothing else in
the module needs `discord`). `settings: Settings`. Guard order — identical to the
old closure, each returning `False`:

1. `if member.bot or member == member.guild.owner: return False`
2. `if not member.guild.me.guild_permissions.manage_nicknames: return False`
3. `if member.guild.me.top_role <= member.top_role: return False`
4. `has_role = any(r.id == settings.target_role_id for r in member.roles)`
5. `target = desired_nick(member.display_name, has_role, settings.prefix_str)`
6. `if target is None: return False`
7. `reason = "N3X Prefix Enforcement" if has_role else "N3X Prefix Removal"`
8. `try: await member.edit(nick=target, reason=reason); return True`
   `except Exception: return False`

`edit` is called with **keyword** `nick=` (tests assert
`await_args.kwargs["nick"]`) and `reason=`, matching the old closure.

## Files to modify

### `n3x-bot/n3x_bot/bot.py`

1. **Add import** (in the `from n3x_bot....` block, lines ~29-40, alphabetically
   near `from n3x_bot.models import render_output`):
   `from n3x_bot.nicknames import enforce_nick`

2. **Delete the inline closure** — lines **469-490** inclusive (the
   `async def enforce_prefix(member: discord.Member):` block, from the blank line
   after `reminder_h, reminder_m = settings.reminder_hm()` down through the final
   `except Exception: pass` of the removal branch). Nothing else in
   `_wire_events` references it after the three call-site swaps below.

3. **Call-site 1 — `on_ready` full-member scan** (~line 519):
   `await enforce_prefix(m)` → `await enforce_nick(m, settings)`.
   Keep the surrounding loop and the `await repo.upsert_user(m.id, m.display_name)`
   reconcile exactly as-is (lines 516-518). Only the closure call swaps.

4. **Call-site 2 — `on_member_update`** (~line 625):
   `await enforce_prefix(after)` → `await enforce_nick(after, settings)`.
   The surrounding `if before.roles != after.roles or before.display_name != after.display_name:`
   guard is unchanged.

5. **Call-site 3 — `on_member_join`** (~line 640):
   `await enforce_prefix(member)` → `await enforce_nick(member, settings)`.
   The preceding `await asyncio.sleep(5)` (line 639) is unchanged.

`settings` is already in `_wire_events` scope (the old closure captured it),
so passing it explicitly at each call site compiles cleanly.

Confirmed by grep: the only production references to `enforce_prefix` are the
definition (469) and the three call sites (519, 625, 640). All other matches are
test files and docstring/comment mentions — none require changes.

## Data flow

Representative call: a member is granted the target role → `on_member_update`
fires.

1. `on_member_update(before, after)` sees `before.roles != after.roles` → true.
2. `await enforce_nick(after, settings)`.
3. Guards pass (not bot, not owner, bot has `manage_nicknames`, bot outranks member).
4. `has_role = True` (target role id present in `after.roles`).
5. `desired_nick("Player", True, "[N3X]")`: not prefixed → `base = "Player"` →
   returns `"[N3X]" + "Player"[:27]` = `"[N3X]Player"`.
6. `target` is a string, not None → `reason = "N3X Prefix Enforcement"`.
7. `await after.edit(nick="[N3X]Player", reason="N3X Prefix Enforcement")` → `True`.

Removal mirror: role revoked → `has_role=False`, name `"[N3X]Player"` startswith
prefix → `desired_nick` returns `"Player"` → `reason="N3X Prefix Removal"` →
edit → `True`. Already-correct role-holder (`"[N3X]Player"`, has role) →
`desired_nick` returns `None` → `enforce_nick` returns `False`, no edit (B5).

## Dependencies

- New packages: **none**.
- Internal: `nicknames.py` depends only on `n3x_bot.config.Settings` (type hint).
  `bot.py` gains a dependency on `n3x_bot.nicknames`. No circular import:
  `nicknames` does not import `bot`.

## Build sequence (for the Coder)

1. Create `n3x_bot/nicknames.py` with `desired_nick` (pure) and `enforce_nick`
   (async). Run `pytest tests/test_nicknames.py` — all 18 go green. The 9 pure
   tests need no I/O; the 9 async tests use the file's local `_FakeMember`/
   `_FakeGuild` fakes, no DB.
2. Edit `bot.py`: add the `enforce_nick` import, delete the closure (469-490),
   swap the three call sites.
3. Run the existing integration suites — `tests/test_bot_wiring.py` (nick add /
   remove / skip), `tests/test_welcome.py::test_on_member_join_still_runs_enforce_prefix`,
   and the `tests/test_kodex.py` join test. All stay green because output is
   byte-identical for every input those tests exercise.
4. Run the full `n3x-bot` test suite once to confirm no import-time regressions
   from the new module.

## Risks and open questions

- **`enforce_nick` return value conflates no-op with failure.** `desired_nick`
  returning `None` (nothing to do) and a swallowed `member.edit` exception both
  yield `False`. The TDD suite pins exactly this (`test_enforce_nick_does_not_edit_correct_role_holder`
  and `test_enforce_nick_swallows_edit_failure_and_returns_false` both assert
  `False`). Harmless in practice: all three call sites ignore the return value.
  Flagging as the handoff requested — if a caller ever needs to distinguish
  "already correct" from "edit failed," this signature can't. Not changing it;
  the tests fix the contract.

- **Reason-string choice (pinned).** One function now handles both add and
  removal, so I select the reason by branch: `has_role` → `"N3X Prefix
  Enforcement"`, else `"N3X Prefix Removal"`. This preserves the exact two
  strings the old closure used. Tests do not assert on `reason`, so this is a
  fidelity choice, not a test requirement. `has_role` is a correct proxy because
  `desired_nick` only returns a non-None target for role-holders in the add
  branch and non-holders in the removal branch — the two reason cases line up
  1:1 with the two edit-producing branches.

- **Truncation hardening is a genuine behavior change only for prefixes longer
  than would fit — not reachable with the current `"[N3X]"` (len 5).** For every
  input the integration tests use, `prefix_str + base[:32-len(prefix_str)]`
  equals the old `f"{prefix_str}{base}"[:32]`, so green is preserved. The change
  is defensive against a future longer prefix; called out so no one mistakes it
  for a semantic drift.

- **Removal `.strip()` hardening** changes output only for names with a stray
  space after the prefix (`"[N3X] Player"`). The current observable format is the
  no-space `"[N3X]Player"`, so existing tests (which use `"[N3X]Player"`) are
  unaffected; the new `test_desired_nick_removal_strips_stray_leading_space`
  covers the hardened path. No v3 B18 stray-space bug is introduced because we
  never insert a space on the add side.

- **No ambiguity or contradiction found** between the new suite and the three
  preserved integration tests; the extraction is a clean lift of identical logic.
