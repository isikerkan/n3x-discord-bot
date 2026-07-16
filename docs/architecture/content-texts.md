# Architecture: Phase 1 de-hardcode — editable narrative copy → `content_texts`

Move six hardcoded German player-facing strings into a DB-backed `content_texts`
table with CODE defaults, editable live via `!content` admin commands. Exact
mirror of the `runtime_config` resolver pattern, except the fallback is a code
constant (`CONTENT_DEFAULTS`) rather than `Settings`.

Feature branch: `feature/content-texts`. Behaviour-preserving: every default
equals the current hardcoded string, so with zero overrides all existing tests
stay green.

## Tests this design satisfies

### `tests/storage/test_content_texts_contract.py` (parametrized json/sqlite/postgres)
- `test_get_content_text_unknown_key_returns_none`
- `test_set_content_text_roundtrips`
- `test_get_content_text_returns_str`
- `test_set_content_text_upserts_same_key`
- `test_multiple_keys_are_independent`
- `test_set_one_key_leaves_other_keys_unset`
- `test_multiline_and_placeholder_value_survives_verbatim`
- `test_delete_content_text_returns_true_when_present`
- `test_delete_content_text_removes_the_key`
- `test_delete_content_text_returns_false_when_absent`
- `test_all_content_texts_empty_by_default`
- `test_all_content_texts_returns_full_map`
- `test_export_all_includes_content_texts_and_is_json_serializable`
- `test_round_trip_preserves_content_texts`
- `test_snapshot_is_stable_after_content_texts_round_trip`
- `test_clear_wipes_content_texts`
- `test_migrate_data_tables_includes_content_texts`

### `tests/test_content_texts.py`
- CONTENT_DEFAULTS/KEYS: `test_content_defaults_has_all_expected_keys`,
  `test_content_keys_is_frozenset_of_defaults`,
  `test_kodex_text_default_is_the_kodex_constant`,
  `test_reminder_defaults_equal_current_hardcoded_strings`,
  `test_welcome_dm_default_carries_mention_placeholder`,
  `test_record_templates_carry_user_name_cost_placeholders`,
  `test_record_lucky_default_formats_without_keyerror`,
  `test_record_unlucky_default_formats_without_keyerror`
- Resolver: `test_get_no_override_returns_default`,
  `test_get_override_wins_over_default`,
  `test_get_unset_key_still_default_when_another_is_overridden`,
  `test_get_unknown_key_raises_keyerror`,
  `test_init_ignores_override_for_non_content_key`,
  `test_refresh_loads_db_value_and_filters_to_content_keys`,
  `test_load_classmethod_builds_resolver_with_db_overrides`,
  `test_load_with_no_overrides_is_behaviour_preserving`
- Wiring: `test_build_bot_attaches_content_texts`,
  `test_build_bot_content_texts_behaviour_preserving_without_overrides`,
  `test_build_bot_registers_content_group`,
  `test_content_group_exposes_expected_subcommands`,
  `test_register_content_commands_entrypoint_exists`,
  `test_register_content_commands_is_idempotent`,
  `test_on_ready_refreshes_content_texts`
- Commands: `test_content_set_stores_value_and_refreshes_live_resolver`,
  `test_content_set_unknown_key_rejected_no_write`,
  `test_content_set_non_admin_refused_no_write`,
  `test_content_reset_reverts_to_default`,
  `test_content_reset_unknown_key_rejected`,
  `test_content_reset_non_admin_refused`,
  `test_content_show_reports_effective_value_for_key`,
  `test_content_show_unknown_key_rejected`, `test_content_show_non_admin_refused`,
  `test_content_list_includes_all_keys_and_overridden_marker`,
  `test_content_list_non_admin_refused`
- Read-site routing: `test_kodex_dm_uses_content_text_override`,
  `test_kodex_dm_no_override_sends_default_constant`,
  `test_welcome_card_uses_content_text_override`,
  `test_welcome_card_no_override_uses_default_template`,
  `test_announce_records_uses_content_text_override`

### Regression guard (must stay green)
- `tests/test_kodex.py::test_send_kodex_dm_sends_text_reacts_and_records_message`
  — REQUIRES a one-line fix (see below); the only pre-existing test that breaks.
- All other `tests/test_kodex.py`, `tests/test_welcome.py`, `tests/test_bot_wiring.py`.

## Files to create

### `n3x_bot/content.py`
Resolver + code defaults. Mirrors `runtime_config.RuntimeConfig` structurally.

Imports: `from n3x_bot.kodex import KODEX_TEXT` (one-way; see cycle note).

Symbols:
- `CONTENT_DEFAULTS: dict[str, str]` — the six defaults, EXACT current strings:
  - `"kodex_text": KODEX_TEXT`
  - `"reminder_aceball": "*EVENT REMINDER*: ACE-BALL beginnt in 30 Minuten! @everyone"`
  - `"reminder_invasion": "*EVENT REMINDER*: Invasion beginnt in 30 Minuten! @everyone"`
  - `"record_lucky": "🍀 **Neuer Glückspilz!** <@{user}> hat den neuen Tiefpreis-Rekord für das **{name}** aufgestellt: **{cost}**"`
  - `"record_unlucky": "💀 **Neuer Pechvogel!** <@{user}> hat den neuen Höchstpreis-Rekord für das **{name}** aufgestellt: **{cost}**"`
  - `"welcome_dm": "Willkommen {mention}!"`
- `CONTENT_KEYS = frozenset(CONTENT_DEFAULTS)`
- `class ContentTexts`:
  - `__init__(self, overrides: dict[str, str] | None = None)` — store
    `self._overrides = {k: v for k, v in (overrides or {}).items() if k in CONTENT_KEYS}`
    (mirrors `RuntimeConfig.__init__` filtering).
  - `get(self, key: str) -> str` — `return self._overrides.get(key, CONTENT_DEFAULTS[key])`.
    `CONTENT_DEFAULTS[key]` raises `KeyError` for an unknown key by plain-dict
    semantics — no explicit guard needed. Overrides only ever hold CONTENT_KEYS,
    so a stray key still hits the default lookup and raises.
  - `async refresh(self, repo) -> None` — `raw = await repo.all_content_texts();
    self._overrides = {k: v for k, v in raw.items() if k in CONTENT_KEYS}`.
  - `@classmethod async load(cls, repo) -> "ContentTexts"` — `ct = cls();
    await ct.refresh(repo); return ct`.

  NON-OBVIOUS DECISION: `get` reuses the plain `dict.__getitem__` KeyError rather
  than raising a custom error — the test asserts a bare `KeyError`, and it keeps
  the resolver a 1-line lookup matching `RuntimeConfig._overrides.get(...)` style.

### `n3x_bot/content_commands.py`
Admin-gated `!content` prefix group. Structural mirror of
`config_commands.register_config_commands` (module-level funcs, no cog, `is_admin`
gate, `delete_after=5`, refresh-after-write).

Imports: `from n3x_bot.admin import is_admin`; `from n3x_bot.config import Settings`;
`from n3x_bot.content import CONTENT_KEYS`; `from n3x_bot.storage.base import StatsRepository`.

- `def register_content_commands(bot, repo: StatsRepository, settings: Settings) -> None`:
  - Idempotency guard: `if bot.get_command("content") is not None: return`.
  - `@bot.group(name="content", invoke_without_command=True)` → `content(ctx)`:
    sends a usage hint (`!content list|show|set|reset ...`, `delete_after=5`).
    NOT admin-gated (matches `config` bare-group behaviour; no test drives it).
  - `@content.command(name="list")` → `list_cmd(ctx)`:
    admin gate → else `"❌ Keine Berechtigung."`. Read `overrides =
    await repo.all_content_texts()`. For each `key in sorted(CONTENT_KEYS)`
    build a line `` f"`{key}`" `` + `" (Override)"` when `key in overrides`.
    Chunk at ~1900 chars (copy the `config show` chunking loop) and `ctx.send`
    each chunk. MUST NOT emit full values (kodex_text alone > 1000 chars) — the
    test only asserts key NAMES appear.
  - `@content.command(name="show")` → `show_cmd(ctx, key)`:
    admin gate; then `if key not in CONTENT_KEYS: await ctx.send("❌ Unbekannter
    Schlüssel ...", delete_after=5); return`; else send the effective value
    `bot.content_texts.get(key)` inside a fenced code block (raw string, NO
    `.format()` — the value legitimately contains `{mention}`/`{user}` braces).
  - `@content.command(name="set")` → `set_cmd(ctx, key, *, value)`:
    admin gate; then unknown-key reject (no write); else
    `await repo.set_content_text(key, value)`;
    `await bot.content_texts.refresh(repo)`; confirm send.
    `*, value` is keyword-only so it greedily consumes the rest of the line
    (multi-word German copy survives) and matches `.callback(ctx, key, value=...)`.
  - `@content.command(name="reset")` → `reset_cmd(ctx, key)`:
    admin gate; then unknown-key reject; else `await repo.delete_content_text(key)`;
    `await bot.content_texts.refresh(repo)`; confirm send.

  Gate ordering in every sub: admin check FIRST, key-validity check SECOND
  (mirrors `config`; the non-admin tests use a VALID key and assert "Berechtigung",
  the unknown-key tests use an ADMIN and assert no write — both orderings pass,
  admin-first is the established convention).

## Files to modify

### `n3x_bot/storage/schema.py` (after `runtime_config`, ~line 132)
Add the table, same shape as `runtime_config`:
```
content_texts = Table(
    "content_texts", metadata,
    Column("key", String(...), primary_key=True),
    Column("value", Text, nullable=True),
)
```
Use `String` (match `runtime_config` which uses bare `String`... actually
`runtime_config.key` is `String(50)`? — it is `Column("key", String(50)...)`).
Content keys are short slugs (`record_unlucky` = 13 chars); use `String(50)` to
match `runtime_config` exactly.

### `n3x_bot/storage/base.py` (after `all_runtime_config`, ~line 118)
Add four abstract methods under a `# content texts` comment, mirroring the
runtime-config block signatures/docstrings:
- `async set_content_text(self, key: str, value: str) -> None` (upsert)
- `async get_content_text(self, key: str) -> str | None`
- `async delete_content_text(self, key: str) -> bool`
- `async all_content_texts(self) -> dict[str, str]`

### `n3x_bot/storage/sql_repo.py`
1. Add four methods next to the runtime_config block (~line 357), COPY the
   runtime_config bodies verbatim, swap `runtime_config` → `content_texts`:
   - `set_content_text`: `await self._upsert(conn, sc.content_texts, {"key": key}, {"value": value})`.
   - `get_content_text`: select `sc.content_texts.c.value` where key == key.
   - `delete_content_text`: existence check + delete, return `exists is not None`.
   - `all_content_texts`: `{r.key: r.value for r in await conn.execute(select(sc.content_texts))}`.
2. `export_all` (~line 777): add
   `content_texts = {r.key: r.value for r in await conn.execute(select(sc.content_texts))}`
   and add `"content_texts": content_texts,` to the returned dict (~line 796).
3. `import_all` (~line 870): after the runtime_config loop add
   `for key, value in snapshot.get("content_texts", {}).items():
       await conn.execute(insert(sc.content_texts).values(key=key, value=value))`.
4. `clear` (~line 890): add `sc.content_texts` to the delete tuple (order-agnostic,
   no FK — append after `sc.runtime_config`).

### `n3x_bot/storage/json_repo.py`
1. `_empty()` (~line 50): add `"content_texts": {},` — this alone makes `clear()`
   (which resets to `_empty()`) wipe the table.
2. Add four methods next to runtime_config (~line 296), COPY runtime_config bodies:
   - `set_content_text`: `self._db["content_texts"][key] = value; self._flush()`.
   - `get_content_text`: `return self._db["content_texts"].get(key)`.
   - `delete_content_text`: `existed = key in ...; pop; _flush(); return existed`.
   - `all_content_texts`: `return dict(self._db["content_texts"])`.
3. `export_all` (~line 555): add
   `"content_texts": copy.deepcopy(self._db["content_texts"]),`.
4. `import_all` (~line 585): add
   `self._db["content_texts"] = copy.deepcopy(snapshot.get("content_texts", {}))`.

### `n3x_bot/migrate.py` (`_DATA_TABLES`, ~line 25-31)
Append `"content_texts"` to the tuple.

### `n3x_bot/bot.py`
1. Imports (~line 47): add `from n3x_bot.content import ContentTexts` and
   `from n3x_bot.content_commands import register_content_commands`.
2. `build_bot` (~line 103, next to `bot.runtime_config = RuntimeConfig(settings)`):
   add `bot.content_texts = ContentTexts()`.
3. `build_bot` register block (~line 121, next to `register_config_commands`):
   add `register_content_commands(bot, repo, settings)`.
4. `_announce_records` (lines 774-783): replace the two f-string `channel.send`
   calls with resolver-driven templates:
   - min branch:
     `await channel.send(bot.content_texts.get("record_lucky").format(
         user=record["min_user"], name=name, cost=format_number(record["min_cost"])))`
   - max branch:
     `await channel.send(bot.content_texts.get("record_unlucky").format(
         user=record["max_user"], name=name, cost=format_number(record["max_cost"])))`
   `bot` is a param of `_announce_records` — no signature change. `name` and
   `format_number` are already in scope.
5. `event_reminder_task` closure (lines 798/800): replace the two literal sends:
   - `await channel.send(bot.content_texts.get("reminder_aceball"))`
   - `await channel.send(bot.content_texts.get("reminder_invasion"))`
   `bot` is captured by the closure (verified: `_wire_events(bot, ...)` — `bot`
   is in scope). No refactor needed.
6. `on_ready` (~line 810, right after the `bot.runtime_config.refresh` try/except):
   add a best-effort block:
   `try: await bot.content_texts.refresh(repo)
    except Exception: log.exception("content_texts refresh failed; using defaults")`.

### `n3x_bot/kodex.py`
- `send_kodex_dm` (line 27): `KODEX_TEXT` → `bot.content_texts.get("kodex_text")`.
  Resolve into a local BEFORE `member.send` inside the existing `try`:
  `text = bot.content_texts.get("kodex_text"); msg = await member.send(text)`.
  `bot` is already a param.
- `_kodex_cmd` DM-all loop (line 74-75): unchanged — it already calls
  `send_kodex_dm(bot, repo, member)`, so routing flows through automatically.
- `KODEX_TEXT` stays defined here (line 9) unchanged — it remains the source of
  truth for the default and the constant other modules import.

### `n3x_bot/welcome.py`
- `send_welcome_card` (line 89-91): replace `f"Willkommen {member.mention}!"` with
  `bot.content_texts.get("welcome_dm").format(mention=member.mention)`. `bot` is a
  param. Keep the `discord.File(...)` kwarg untouched.

### `tests/test_kodex.py` — the flagged fix (line 123-134)
`test_send_kodex_dm_sends_text_reacts_and_records_message` passes a bare
`MagicMock()` as `bot`, so `bot.content_texts.get("kodex_text")` returns a child
MagicMock and `member.send.assert_awaited_once_with(kodex.KODEX_TEXT)` fails.
Minimal fix: give the mock bot a real resolver.
```
from n3x_bot.content import ContentTexts
...
bot = MagicMock()
bot.content_texts = ContentTexts()
await kodex.send_kodex_dm(bot, repo, member)
```
This is the ONLY pre-existing test that breaks (see Risks for the grep audit).

## Data flow

### `!content set kodex_text Neuer Text` (write path)
1. Prefix router dispatches to `content` group → `set` subcommand
   `set_cmd(ctx, "kodex_text", value="Neuer Text")`.
2. `is_admin(ctx.author, settings)` passes → key ∈ `CONTENT_KEYS` passes.
3. `await repo.set_content_text("kodex_text", "Neuer Text")` → `_upsert` into
   `content_texts`.
4. `await bot.content_texts.refresh(repo)` → resolver `_overrides` now holds the
   filtered `all_content_texts()`.
5. Confirmation `ctx.send`.

### Kodex DM read path (override live)
1. `!kodex` → `_kodex_cmd` loops members → `send_kodex_dm(bot, repo, member)`.
2. `text = bot.content_texts.get("kodex_text")` → override if set in `_overrides`,
   else `CONTENT_DEFAULTS["kodex_text"]` (== `KODEX_TEXT`).
3. `msg = await member.send(text)`; persist mapping; seed reaction.

### Record announcement read path
1. Gate entry beats a record → `_announce_records(bot, settings, gate_type, changed, record)`.
2. For `"min"`: `bot.content_texts.get("record_lucky").format(user=record["min_user"],
   name=GATE_NAMES.get(gate_type, ...), cost=format_number(record["min_cost"]))`.
3. `channel.send(...)`. Default template reproduces the exact current message.

### on_ready cold-start / reconnect
`on_ready` → `runtime_config.refresh` (existing) → `content_texts.refresh`
(new, best-effort) → offline edits go live on reconnect, matching runtime_config.

## Dependencies
- New packages: NONE. Reuses SQLAlchemy Core, discord.ext.commands, existing
  `_upsert`/`_insert_if_absent` helpers, `is_admin`, `format_number`, `GATE_NAMES`.
- Internal module dependency added: `content.py` → `kodex.py` (for `KODEX_TEXT`),
  `content_commands.py` → `content.py` + `admin.py`, `bot.py` → both new modules.

## Build sequence (for the Coder)

1. **Schema** — add `content_texts` table to `schema.py`.
   Greens: nothing yet (needed by everything below).
2. **base.py** — add the four abstract methods.
   Greens: nothing directly; keeps the ABC contract consistent.
3. **json_repo.py** — `_empty` key + four methods + export/import.
   Greens: entire `test_content_texts_contract.py` on the json backend, incl.
   `test_clear_wipes_content_texts` (via `_empty`), export/import/round-trip.
4. **sql_repo.py** — four methods + export/import + clear.
   Greens: `test_content_texts_contract.py` on sqlite (and postgres when
   `TEST_POSTGRES_URL` set).
5. **migrate.py** — `_DATA_TABLES += ("content_texts",)`.
   Greens: `test_migrate_data_tables_includes_content_texts`.
6. **content.py** — `CONTENT_DEFAULTS`, `CONTENT_KEYS`, `ContentTexts`.
   Greens: all of `test_content_texts.py` sections 1 (defaults) and 2 (resolver).
   Depends on step 3/4 for `refresh`/`load` tests.
7. **content_commands.py** — `register_content_commands` + 4 subs.
   Greens (once wired in step 8): section 4 command tests +
   `test_register_content_commands_entrypoint_exists`.
8. **bot.py** — imports, `bot.content_texts` attach, register call, on_ready
   refresh, `_announce_records` + `event_reminder_task` routing.
   Greens: section 3 wiring tests, `test_on_ready_refreshes_content_texts`,
   `test_announce_records_uses_content_text_override`, and keeps
   `test_bot_wiring.py` green (`"content"` already excluded).
9. **kodex.py** — route `send_kodex_dm` to the resolver.
   Greens: `test_kodex_dm_uses_content_text_override`,
   `test_kodex_dm_no_override_sends_default_constant`.
10. **welcome.py** — route `send_welcome_card` to the resolver.
    Greens: `test_welcome_card_uses_content_text_override`,
    `test_welcome_card_no_override_uses_default_template`.
11. **tests/test_kodex.py** — the one-line resolver fix on the flagged test.
    Greens: `test_send_kodex_dm_sends_text_reacts_and_records_message`.

Run `feature/content-texts` suite after 8, 10, 11; then the full suite to confirm
behaviour preservation.

## Behaviour-preserving confirmation
- Every `CONTENT_DEFAULTS` value is byte-identical to the current hardcoded
  string (kodex `KODEX_TEXT` reused by reference; reminders match the pinned
  `ACEBALL_STRING`/`INVASION_STRING`; `welcome_dm` == `"Willkommen {mention}!"`;
  record templates == the current f-strings with `{user}`/`{name}`/`{cost}`
  substituted for the interpolations, `Tiefpreis`/`Höchstpreis` wording kept).
- With no DB overrides, `.get()` returns the default → `send_kodex_dm`,
  `send_welcome_card`, `_announce_records`, `event_reminder_task` emit exactly
  the same bytes as before.
- `record_lucky`/`record_unlucky` render via `.format(user=…, name=…, cost=…)`
  where `cost` is passed the `format_number(...)`ed string (same as today's
  inline `format_number` call) — output unchanged.

## Risks and open questions

1. **KODEX_TEXT cycle-direction decision (flagged as required):**
   RESOLVED — keep `KODEX_TEXT` defined in `kodex.py`; `content.py` does
   `from n3x_bot.kodex import KODEX_TEXT`. This is non-cyclic: `kodex.py` imports
   only `discord.ext.commands`, `admin`, `config`, `storage.base` — none import
   `content`. The read-site (`send_kodex_dm`) reaches the resolver via the
   `bot.content_texts` attribute, NOT via importing `content`, so `kodex.py`
   never needs to import `content`. Chosen over "define in content, import into
   kodex" because it is the minimal diff (constant stays put) and every existing
   `kodex.KODEX_TEXT` reference and `test_kodex_text_default_is_the_kodex_constant`
   keep working unchanged.

2. **Flagged test — sole breakage confirmed:** grep of `tests/test_kodex.py`
   shows three bare-`MagicMock()`-bot calls to `send_kodex_dm` (lines 128, 141,
   154). Only line 128 asserts on the sent text (`assert_awaited_once_with(
   KODEX_TEXT)`) and breaks. Line 141 returns early (bot member guard, before the
   resolver call). Line 154's `member.send` raises inside the `try` and the test
   only asserts no-record/no-reaction — the MagicMock `get(...)` does not raise,
   so it stays green. `tests/test_welcome.py` has NO bare-MagicMock bot calling
   `send_welcome_card` (all call sites use `build_bot`, which attaches a real
   `ContentTexts`), so none break.

3. **`event_reminder_task` has no send-level test.** Confirmed: no test drives
   the reminder send; correctness is pinned only via `CONTENT_DEFAULTS[
   "reminder_aceball"/"reminder_invasion"] == ACEBALL_STRING/INVASION_STRING`.
   The closure captures `bot`, so routing needs no refactor. Low risk, but note
   there is zero integration coverage of the reminder read-site — flagging back
   to TDD if they want an explicit send test.

4. **`content show` value formatting.** The effective value must be emitted RAW
   (no `str.format`) because content values legitimately contain `{mention}`/
   `{user}` braces; formatting them would raise `KeyError`/`IndexError`. Emit
   inside a fenced code block so Discord markdown in the value (`**bold**`,
   newlines) is shown literally. Tests only assert substring presence, so this is
   safe.

5. **`content list` must not dump full values.** `kodex_text` alone exceeds a
   single Discord message; listing effective values would need chunking AND could
   still be noisy. Design lists key names + `(Override)` marker only, matching the
   test which asserts key NAMES appear. If a human wants values in `list`, that is
   a product decision beyond the test surface — not designed for here.

6. **`String(50)` PK width.** Chosen to match `runtime_config.key`. All six
   content keys are ≤ 13 chars, so ample. Admin `set` of an unknown/oversized key
   is rejected by the `CONTENT_KEYS` guard before any DB write, so no truncation
   risk from the command surface.
