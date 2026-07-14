# Architecture: Kodex (rules-acceptance)

## Tests this design satisfies

### tests/test_config.py
- `test_kodex_check_channel_id_defaults_to_zero`
- `test_kodex_check_channel_id_read_from_env`

### tests/storage/test_kodex_repository_contract.py (parametrized json / sqlite / postgres)
- `test_has_confirmed_kodex_false_by_default`
- `test_confirm_kodex_marks_user_confirmed`
- `test_confirm_kodex_is_idempotent`
- `test_confirmations_are_isolated_per_user`
- `test_list_kodex_confirmed_empty_by_default`
- `test_list_kodex_confirmed_returns_every_confirmer`
- `test_save_and_get_kodex_message_roundtrip`
- `test_get_kodex_message_user_unknown_returns_none`
- `test_kodex_messages_map_each_message_to_its_own_member`
- `test_export_all_includes_kodex_and_is_json_serializable`
- `test_round_trip_preserves_kodex_confirmations`
- `test_round_trip_preserves_kodex_messages`
- `test_snapshot_is_stable_after_kodex_round_trip`
- `test_clear_wipes_kodex_data`

### tests/test_kodex.py
- `test_kodex_text_is_non_empty_str_with_expected_keyword`
- `test_kodex_emoji_is_check_mark`
- `test_send_kodex_dm_sends_text_reacts_and_records_message`
- `test_send_kodex_dm_skips_bot_member`
- `test_send_kodex_dm_swallows_dm_failure_and_records_nothing`
- `test_handle_kodex_confirmation_confirms_tracked_user`
- `test_handle_kodex_confirmation_ignores_untracked_message`
- `test_handle_kodex_confirmation_ignores_non_checkmark_emoji`
- `test_build_kodex_report_marks_confirmed_and_unconfirmed_members`
- `test_build_kodex_report_chunks_long_member_lists_within_limit`
- `test_register_kodex_commands_registers_both_commands`
- `test_register_kodex_commands_is_idempotent`
- `test_kodex_command_refuses_non_admin_and_sends_no_dms`
- `test_kodex_command_admin_dms_each_non_bot_member`
- `test_kodex_check_command_refuses_non_admin_and_posts_nothing`
- `test_kodex_check_command_admin_posts_report_to_check_channel`
- `test_on_member_join_sends_kodex_dm_and_still_registers_user`
- `test_on_raw_reaction_add_confirms_kodex_on_tracked_message`
- `test_build_bot_registers_kodex_commands`

### tests/test_bot_wiring.py (existing, needs adjustment)
- `test_register_stat_commands_adds_one_command_per_stat_plus_rank` — the command-count exclusion tuple must gain `"kodex"`, `"kodex_check"`.
- `test_register_stat_commands_is_idempotent` — same.

---

## Files to create

### `n3x_bot/kodex.py`
New module, modeled structurally on `n3x_bot/activity.py` (module-level async helpers + a `register_*` function that guards on `bot.get_command(...) is not None` and adds `commands.Command(...)`). Admin gating reuses `is_admin` from `n3x_bot.admin`.

Top-level imports:
- `from discord.ext import commands`
- `from n3x_bot.admin import is_admin`
- `from n3x_bot.config import Settings`
- `from n3x_bot.storage.base import StatsRepository`

Constants (ported verbatim from `manus versions/v3/bot.py` lines 42-54):
- `KODEX_EMOJI = "✅"`
- `KODEX_TEXT` — the exact multi-line string below (do NOT port `KODEX_CHECK_CHANNEL_ID`; that becomes a config field, not a module constant):

```python
KODEX_TEXT = (
    "📜 **Verhaltenskodex**\n\n"
    "Bei uns steht Spass an erster Stelle. Humor, dumme Sprüche und freundschaftliche Beleidigungen gehören zum Alltag – solange alle darüber lachen können.\n\n"
    "Respektiert persönliche Grenzen. Humor ist für jeden anders. Wenn euch etwas zu weit geht, sprecht es offen an. Niemand kann Gedanken lesen.\n"
    "Etwas Zurückhaltung schadet nie. Nicht jeder Witz kommt bei jedem gleich an.\n"
    "Tabu sind Witze oder Beleidigungen über Familie, Partner oder Kinder, auch wenn sie nur scherzhaft gemeint sind.\n"
    "Alles andere ist grundsätzlich erlaubt, solange es nicht gegen andere Serverregeln verstösst oder jemandem ernsthaft schadet.\n"
    "Bei Problemen oder Spannungen zögert nicht, jemanden aus der Serverleitung hinzuzuziehen. Wir helfen gerne dabei, Missverständnisse zu klären.\n\n"
    "Am Ende gilt:\n\n"
    "Habt Spass, nehmt nicht alles zu ernst – aber respektiert die Grenzen eurer Mitspieler. ❤️\n\n"
    "Bitte bestätige, dass du den Verhaltenskodex gelesen hast, indem du mit der unten angegebenen Reaktion auf diese Nachricht reagierst. Erst danach gilt der Kodex als bestätigt."
)
```
(Contains "Verhaltenskodex", satisfying the keyword assertion.)

Functions:

- `async def send_kodex_dm(bot, repo: StatsRepository, member) -> None`
  - `if getattr(member, "bot", False): return` (skips bot members — `test_send_kodex_dm_skips_bot_member`; note `member.send` must NOT be awaited, so the bot check comes first).
  - `try: msg = await member.send(KODEX_TEXT)` / `except Exception: return` (best-effort; on DM failure records nothing and adds no reaction — `test_send_kodex_dm_swallows_dm_failure_and_records_nothing`).
  - After a successful send: `await msg.add_reaction(KODEX_EMOJI)` then `await repo.save_kodex_message(msg.id, member.id)`.
  - `bot` is unused in the body but kept in the signature (mirrors v3 call shape and the wiring/command callers; tests pass a `MagicMock()`).

- `async def handle_kodex_confirmation(bot, repo: StatsRepository, payload) -> None`
  - `if str(payload.emoji) != KODEX_EMOJI: return` (rejects `❌` — `test_..._ignores_non_checkmark_emoji`; `str()` wrap handles both the plain-string test payloads and real `PartialEmoji`).
  - `user_id = await repo.get_kodex_message_user(payload.message_id)` — `if user_id is None: return` (untracked message — `test_..._ignores_untracked_message`).
  - `await repo.confirm_kodex(user_id)` — confirm the tracked user (NOT `payload.user_id`; v3 confirms the DM's tracked owner).

- `def build_kodex_report(confirmed: set[int], members: list) -> list[str]`
  - Per member build a line: `f"{'✅' if m.id in confirmed else '❌'} {m.mention} — {m.display_name}"`.
  - Does NOT filter bots (caller filters). Chunk lines into strings each `<= 1900` chars, joined by `"\n"`: accumulate into a current buffer, and when appending the next line (plus a `"\n"` separator) would exceed 1900, flush the buffer as one chunk and start a new one. Return `list[str]`. For an empty `members` list return `[]` (acceptable; the long-list test needs `>1` chunk and every chunk `<= 1900`).

- `def register_kodex_commands(bot, repo: StatsRepository, settings: Settings) -> None`
  - Idempotency guard first: `if bot.get_command("kodex") is not None: return`.
  - Define `async def _kodex_cmd(ctx):`
    - `if not is_admin(ctx.author, settings):` → `await ctx.send("❌ Keine Berechtigung.", delete_after=5)`; `return` (non-admin refusal, no DMs — `test_kodex_command_refuses_non_admin_and_sends_no_dms`).
    - else: `for member in ctx.guild.members: await send_kodex_dm(bot, repo, member)` (bots skipped inside `send_kodex_dm` — `test_kodex_command_admin_dms_each_non_bot_member`). Optionally a confirmation `await ctx.send(...)`.
  - Define `async def _kodex_check_cmd(ctx):`
    - `if not is_admin(ctx.author, settings):` → refuse via `ctx.send`; `return` (posts nothing — `test_kodex_check_command_refuses_non_admin_and_posts_nothing`).
    - else: `confirmed = await repo.list_kodex_confirmed()`; `members = [m for m in ctx.guild.members if not getattr(m, "bot", False)]`; `chunks = build_kodex_report(confirmed, members)`; `channel = bot.get_channel(settings.kodex_check_channel_id)`; `if channel is not None: for chunk in chunks: await channel.send(chunk)`.
  - Register both: `bot.add_command(commands.Command(_kodex_cmd, name="kodex"))` and `bot.add_command(commands.Command(_kodex_check_cmd, name="kodex_check"))` (mirrors `register_activity`'s `commands.Command(...)` style; makes `bot.get_command("kodex").callback(ctx)` reachable in tests).

---

## Files to modify

### `n3x_bot/config.py`
Add one field to `Settings` (alongside the other `*_channel_id: int = 0` fields, e.g. after line 32 `overview_channel_id`):
- `kodex_check_channel_id: int = 0`

Pydantic-settings reads it from env var `KODEX_CHECK_CHANNEL_ID` automatically by field name (same mechanism as `admin_role_id`/`ADMIN_ROLE_ID`). No validator changes.

### `n3x_bot/storage/schema.py`
Append two tables (match existing `BigInteger` PK style used by `activity_counters`/`achievements`):
```python
kodex_confirmations = Table(
    "kodex_confirmations", metadata,
    Column("discord_id", BigInteger, primary_key=True),
)

kodex_messages = Table(
    "kodex_messages", metadata,
    Column("message_id", BigInteger, primary_key=True),
    Column("discord_id", BigInteger, nullable=False),
)
```

### `n3x_bot/storage/base.py`
Add five abstract methods to `StatsRepository` (new `# kodex` section, e.g. after the achievements block ~line 185):
- `async def confirm_kodex(self, discord_id: int) -> None: ...` (docstring: idempotent insert-or-ignore)
- `async def has_confirmed_kodex(self, discord_id: int) -> bool: ...`
- `async def list_kodex_confirmed(self) -> set[int]: ...`
- `async def save_kodex_message(self, message_id: int, discord_id: int) -> None: ...`
- `async def get_kodex_message_user(self, message_id: int) -> int | None: ...`

### `n3x_bot/storage/json_repo.py`
- `_empty()` (line 25): add two keys — `"kodex_confirmations": []`, `"kodex_messages": {}` (existing `connect()` `setdefault` loop backfills these into any pre-existing on-disk db).
- New `# ── kodex ──` section (after achievements, ~line 397):
  - `confirm_kodex`: `lst = self._db["kodex_confirmations"]; if discord_id not in lst: lst.append(discord_id); self._flush()`.
  - `has_confirmed_kodex`: `return discord_id in self._db["kodex_confirmations"]`.
  - `list_kodex_confirmed`: `return set(self._db["kodex_confirmations"])`.
  - `save_kodex_message`: `self._db["kodex_messages"][str(message_id)] = discord_id; self._flush()` (keys str for JSON safety, mirroring `activity_counters`/`streak_stats`).
  - `get_kodex_message_user`: `return self._db["kodex_messages"].get(str(message_id))` (returns stored int or None).
- `export_all()` (returned dict, ~line 418): add
  - `"kodex_confirmations": sorted(self._db["kodex_confirmations"])` (sorted → deterministic snapshot for `test_snapshot_is_stable_after_kodex_round_trip`),
  - `"kodex_messages": copy.deepcopy(self._db["kodex_messages"])`.
- `import_all()` (~line 452): add
  - `self._db["kodex_confirmations"] = copy.deepcopy(snapshot.get("kodex_confirmations", []))`
  - `self._db["kodex_messages"] = copy.deepcopy(snapshot.get("kodex_messages", {}))`
- `clear()`: no change needed — it rebuilds from `_empty()`.

### `n3x_bot/storage/sql_repo.py`
- New `# ── kodex ──` section (after achievements, ~line 541):
  - `confirm_kodex`: within `engine.begin()`, `select(sc.kodex_confirmations).where(discord_id == ...)`; if `one_or_none()` is None → `insert(...).values(discord_id=discord_id)` (idempotent, mirrors `unlock_achievement`).
  - `has_confirmed_kodex`: `engine.connect()`, `select(...).where(discord_id == ...)`; `return r is not None`.
  - `list_kodex_confirmed`: `engine.connect()`, `select(sc.kodex_confirmations.c.discord_id)`; `return {r.discord_id for r in rows}`.
  - `save_kodex_message`: within `engine.begin()`, check existing row for `message_id`; if present `update(...).values(discord_id=...)`, else `insert(...).values(message_id=message_id, discord_id=discord_id)` (upsert keeps re-saves from raising a PK violation; mirrors `set_last_post`).
  - `get_kodex_message_user`: `engine.connect()`, `select(sc.kodex_messages.c.discord_id).where(message_id == ...)`; `return int(r.discord_id) if r else None`.
- `export_all()` (returned dict, ~line 621): add
  - `"kodex_confirmations": [r.discord_id for r in await conn.execute(select(sc.kodex_confirmations).order_by(sc.kodex_confirmations.c.discord_id.asc()))]`
  - `"kodex_messages": {str(r.message_id): r.discord_id for r in await conn.execute(select(sc.kodex_messages))}`
- `import_all()` (~line 685, inside the same `engine.begin()`):
  - `for did in snapshot.get("kodex_confirmations", []): insert(sc.kodex_confirmations).values(discord_id=did)`
  - `for mid, did in snapshot.get("kodex_messages", {}).items(): insert(sc.kodex_messages).values(message_id=int(mid), discord_id=did)`
- `clear()`: add `sc.kodex_confirmations, sc.kodex_messages` to the delete-loop tuple (no FKs, order irrelevant).

### `n3x_bot/bot.py`
- Imports (top, ~after line 30): `from n3x_bot.kodex import register_kodex_commands, send_kodex_dm, handle_kodex_confirmation`.
- `build_bot` (~line 105): add `register_kodex_commands(bot, repo, settings)` alongside the other `register_*` calls (`test_build_bot_registers_kodex_commands`).
- `on_raw_reaction_add` (lines 591-606): append a THIRD best-effort block (do NOT add a new event):
  ```python
  try:
      await handle_kodex_confirmation(bot, repo, payload)
  except Exception:
      pass
  ```
  (`test_on_raw_reaction_add_confirms_kodex_on_tracked_message`).
- `on_member_join` (lines 613-625): after the `if not member.bot: await repo.upsert_user(...)` block, add a best-effort Kodex DM that does not disturb welcome/sleep/`enforce_prefix`:
  ```python
  try:
      await send_kodex_dm(bot, repo, member)
  except Exception:
      pass
  ```
  Place it before the `await asyncio.sleep(5)` line. `send_kodex_dm` skips bots itself, so no extra `member.bot` guard is required. (`test_on_member_join_sends_kodex_dm_and_still_registers_user`.)

### `tests/test_bot_wiring.py` (necessary existing-test adjustment)
`build_bot` now registers two more commands, so the command-count exclusion tuples in `test_register_stat_commands_adds_one_command_per_stat_plus_rank` (line 75) and `test_register_stat_commands_is_idempotent` (line 90) must include `"kodex"`, `"kodex_check"`:
`("help", "stat", "del", "admin", "activity", "erfolge", "overview", "sync_achievements", "kodex", "kodex_check")`.

---

## Data flow

### New member joins
1. `on_member_join(member)` fires → `repo.upsert_user(member.id, member.display_name)` (unchanged auto-registration).
2. Best-effort `send_kodex_dm(bot, repo, member)`: skips bots; DMs `KODEX_TEXT`; on the returned DM message adds `✅`; `repo.save_kodex_message(msg.id, member.id)` records the message→member mapping.
3. Welcome message + `asyncio.sleep(5)` + `enforce_prefix(member)` proceed unchanged.

### Member reacts ✅ on the DM
1. `on_raw_reaction_add(payload)` fires → the new third try/except calls `handle_kodex_confirmation(bot, repo, payload)`.
2. Emoji check (`str(payload.emoji) == "✅"`) → `repo.get_kodex_message_user(payload.message_id)` resolves the tracked member → `repo.confirm_kodex(user_id)` (idempotent).

### Admin `!kodex` / `!kodex_check`
- `!kodex`: `is_admin` gate → iterate `ctx.guild.members`, `send_kodex_dm` each (bots skipped internally).
- `!kodex_check`: `is_admin` gate → `repo.list_kodex_confirmed()` + non-bot members → `build_kodex_report(confirmed, members)` → post each chunk to `bot.get_channel(settings.kodex_check_channel_id)`.

---

## Dependencies
- No new packages.
- Internal: `n3x_bot.admin.is_admin`, `n3x_bot.config.Settings`, `n3x_bot.storage.base.StatsRepository`, `discord.ext.commands`, `n3x_bot.storage.schema` (new tables), and `bot.py` ↔ `kodex.py` (one-directional: `bot.py` imports `kodex.py`, `kodex.py` does NOT import `bot`, so no import cycle — unlike the deferred import needed in `admin.py`).

---

## Build sequence (for the Coder)

1. **Config** — add `kodex_check_channel_id` to `Settings`. Turns green: the two `tests/test_config.py` kodex tests.
2. **Schema** — add `kodex_confirmations` + `kodex_messages` tables to `schema.py`.
3. **Base** — add the five abstract methods to `StatsRepository`.
4. **json_repo** — `_empty` keys + five methods + export/import handling. Turns green: the json-parametrized rows of `test_kodex_repository_contract.py`.
5. **sql_repo** — five methods + export/import + clear. Turns green: the sqlite (and postgres when `TEST_POSTGRES_URL` set) rows of the contract file.
6. **kodex.py** — constants + `send_kodex_dm`, `handle_kodex_confirmation`, `build_kodex_report`, `register_kodex_commands`. Turns green: the module-constant, `send_kodex_dm`, `handle_kodex_confirmation`, `build_kodex_report`, and `register_kodex_commands` command-level tests in `test_kodex.py`.
7. **bot.py wiring** — import + `build_bot` registration + `on_raw_reaction_add` third block + `on_member_join` DM block. Turns green: `test_build_bot_registers_kodex_commands`, `test_on_raw_reaction_add_confirms_kodex_on_tracked_message`, `test_on_member_join_sends_kodex_dm_and_still_registers_user`.
8. **test_bot_wiring.py** — extend the two exclusion tuples with `"kodex"`, `"kodex_check"`. Keeps `test_register_stat_commands_*` green.

Each step leaves the tree importable and adds no broken intermediate state (config/schema/base/json/sql are independent additive changes; `kodex.py` depends only on already-present `admin`/`config`/`base`; the `bot.py` import of `kodex` lands only in step 7 when `kodex.py` exists).

---

## Risks and open questions

1. **`send_kodex_dm` receives `bot` but never uses it.** Kept for signature parity with v3 and the caller conventions the TDD handoff pinned; harmless. Flagging so the Coder does not "optimize" it away — the tests pass a positional `MagicMock()` there.
2. **Editing `tests/test_bot_wiring.py`.** Adding two commands to `build_bot` deliberately breaks the existing stat-command-count assertion. The handoff explicitly authorizes extending the exclusion tuples; this is the intended, minimal adjustment, not a workaround. No other assertion in that file is affected.
3. **`save_kodex_message` upsert vs plain insert (SQL).** v3 used a plain `INSERT`; the contract only ever saves distinct `message_id`s, so a plain insert would pass. I specify an upsert (check-then-update/insert) purely for robustness against a re-save of the same message id (e.g. an admin re-running `!kodex`), consistent with `set_last_post`. If the Coder prefers to mirror v3 exactly, a plain insert also satisfies every test — call it out rather than silently diverging.
4. **`build_kodex_report` empty-members behavior is untested.** Returning `[]` for no members is the natural result of the chunking loop; the `!kodex_check` command then posts nothing. No test pins this, so it is a design choice, not a requirement.
5. **`confirm_kodex` stores `payload.user_id`'s *tracked owner*, not the reactor.** By v3 design the DM message maps to exactly one member, so the reactor and tracked owner coincide in a DM. If the same tracked message id could ever be reacted to by a different user (not possible in a 1:1 DM), the tracked owner is confirmed — matching v3 and `test_handle_kodex_confirmation_confirms_tracked_user`.
6. **Non-admin refusal channel.** `!kodex`/`!kodex_check` send the refusal via `ctx.send` (tests assert `ctx.send` awaited / channel untouched). Using `delete_after=5` matches the admin-module convention; the tests only assert that `ctx.send` was awaited, so the kwarg is stylistic.
