# Architecture: Base timers (v3 port #6)

Role-gated per-map countdown timers with a self-editing overview embed, persisted
to the repo (fixes B12 in-memory loss), tz-aware (B6), guarded loop start (B4).

## Tests this design satisfies

### `tests/test_config.py` (Base-timer config block)
- `test_base_timer_role_id_defaults_to_zero` / `_read_from_env`
- `test_timer_overview_channel_id_defaults_to_zero` / `_read_from_env`
- `test_timer_overview_message_id_defaults_to_zero` / `_read_from_env`
- `test_allowed_maps_defaults_to_the_v3_map_list`
- `test_allowed_maps_list_parses_default_into_thirteen_maps`
- `test_allowed_maps_list_strips_whitespace_and_drops_empties`
- `test_allowed_maps_read_from_env`

### `tests/storage/test_base_timers_repository_contract.py` (json / sqlite / postgres)
- empty-by-default, set→list roundtrip, multiple independent maps
- `test_end_time_round_trips_tz_aware`, `test_end_time_preserves_instant_for_utc_input`
- upsert overwrites same map
- remove: true-when-existed, deletes-row, false-when-absent, second-call-false, leaves-others
- purge_expired: removes-only-past, returns-removed-names, exactly-now-is-expired, empty-when-nothing
- export/import/clear: json-serializable, round-trip preserves, keeps-tz-aware, snapshot-stable, clear-wipes

### `tests/test_timers.py`
- `build_timer_overview_embed`: title, empty→red+"Keine aktiven Base Timer.", populated→blue, sorted asc, floors minutes, past→0
- `has_base_timer_role`: true/false/unconfigured
- `start_base_timer`: stores now+minutes, tz-aware, rejects invalid map (ValueError, stores nothing)
- `update_timer_overview`: edits fixed message, purges-before-render, noop channel-missing, swallows fetch/edit failure
- `register_timer_commands`: registers base+basestop, idempotent, base stores+refresh for role-holder, base refused for non-role-holder, base invalid-map names allowed list, basestop removes+refresh, basestop unknown→reports, basestop refused for non-role-holder
- `start_timer_overview_loop`: starts loop, guarded double-start
- `test_build_bot_registers_base_and_basestop`

### `tests/test_bot_wiring.py`
- `"base"`, `"basestop"` already present in both exclusion tuples (verified lines 75, 90) — build_bot must register them so they don't appear as "unexpected wired commands".

## Files to create

### `n3x-bot/n3x_bot/timers.py`
New module. Imports: `from datetime import datetime, timedelta`, `from zoneinfo import ZoneInfo`,
`import discord`, `from discord.ext import commands, tasks`, `from n3x_bot.config import Settings`,
`from n3x_bot.storage.base import StatsRepository`. Does NOT import `n3x_bot.bot` (no cycle).

Symbols (mirror `admin.py`/`activity.py` conventions):

- `build_timer_overview_embed(timers: dict[str, datetime], now: datetime) -> discord.Embed`
  - Create `discord.Embed(title="🛰️ BASE TIMER ÜBERSICHT", color=discord.Color.blue())`.
  - Empty `timers`: set `description = "Keine aktiven Base Timer."`, `color = discord.Color.red()`, return.
  - Else: `ordered = sorted(timers.items(), key=lambda kv: kv[1])`; for each,
    `remaining = max(0, int((end_time - now).total_seconds() // 60))`,
    line `f"📍 **{map_name}** — {remaining} Min"`; `description = "\n".join(lines)`. (Renders all rows,
    clamps past→0; caller purges.) Leaves color blue.

- `has_base_timer_role(member, settings: Settings) -> bool`
  - `return bool(settings.base_timer_role_id) and any(r.id == settings.base_timer_role_id for r in getattr(member, "roles", []))`.
  - Exact mirror of `admin.is_admin` but against `base_timer_role_id` (do NOT reuse `is_admin`). `0 → False`.

- `async start_base_timer(repo, settings, map_name, minutes, now) -> datetime`
  - Validate FIRST: `if map_name not in settings.allowed_maps_list: raise ValueError(f"Ungültige Map: {map_name}")` (stores nothing).
  - `end_time = now + timedelta(minutes=minutes)`; `await repo.set_base_timer(map_name, end_time)`; `return end_time`.

- `async update_timer_overview(bot, repo, settings, now) -> None`
  - `await repo.purge_expired_base_timers(now)` (B12 reconcile every render).
  - `timers = await repo.list_base_timers()`; `embed = build_timer_overview_embed(timers, now)`.
  - `channel = bot.get_channel(settings.timer_overview_channel_id)`; `if channel is None: return`.
  - `try: msg = await channel.fetch_message(settings.timer_overview_message_id); await msg.edit(content=None, embed=embed)` / `except Exception: pass` (best-effort; swallows fetch AND edit failure).

- `register_timer_commands(bot, repo, settings) -> None`
  - Idempotent per command (mirror `register_gate_commands`): guard each with `if bot.get_command(<name>) is None:`.
  - `_base_cmd(ctx, map_name: str, zeit: int)`:
    - `if not has_base_timer_role(ctx.author, settings): await ctx.send("❌ Keine Berechtigung.", delete_after=5); return`
    - `now = datetime.now(ZoneInfo(settings.timezone))`
    - `try: await start_base_timer(repo, settings, map_name, zeit, now)`
      `except ValueError: await ctx.send(f"❌ Ungültige Map. Erlaubte Maps: {', '.join(settings.allowed_maps_list)}", delete_after=10); return`
    - `await update_timer_overview(bot, repo, settings, now)`
    - `await ctx.send(f"✅ Timer für {map_name} gestartet ({zeit} Min).", delete_after=5)`
    - Register: `bot.add_command(commands.Command(_base_cmd, name="base"))`.
  - `_basestop_cmd(ctx, map_name: str)`:
    - role check identical refusal.
    - `now = datetime.now(ZoneInfo(settings.timezone))`
    - `if await repo.remove_base_timer(map_name): await update_timer_overview(bot, repo, settings, now); await ctx.send(f"✅ Timer für {map_name} gestoppt.", delete_after=5)`
      `else: await ctx.send(f"❌ Kein aktiver Timer für Map {map_name}.", delete_after=5)`
    - Register: `bot.add_command(commands.Command(_basestop_cmd, name="basestop"))`.
  - NOTE: gating is done INSIDE the body (not a discord check decorator) because the tests invoke
    `bot.get_command("base").callback(ctx, ...)` directly, bypassing dispatch/decorators — matches
    the `is_admin` in-body pattern in `admin.py`. ValueError is caught in-body (not left to
    `on_command_error`) for the same reason.

- `start_timer_overview_loop(bot, repo, settings) -> tasks.Loop`
  - Guard: `existing = getattr(bot, "_timer_overview_loop", None)`;
    `if isinstance(existing, tasks.Loop) and existing.is_running(): return existing`.
    (isinstance check is REQUIRED — a bare `getattr(..., None) is not None` guard breaks under the
    tests' `MagicMock` bot, which auto-creates a truthy attribute. isinstance rejects the auto-mock,
    then we overwrite it with the real Loop so the second call sees the running Loop.)
  - Create `@tasks.loop(seconds=30)` coroutine `_timer_overview_loop()` that calls
    `await update_timer_overview(bot, repo, settings, datetime.now(ZoneInfo(settings.timezone)))`.
  - `bot._timer_overview_loop = _timer_overview_loop`; `if not _timer_overview_loop.is_running(): _timer_overview_loop.start()`; `return _timer_overview_loop`.

## Files to modify

### `n3x-bot/n3x_bot/config.py`
Add fields to `Settings` (after `voice_achievement_roles`, before validators — env names auto-derive
uppercase):
```
base_timer_role_id: int = 0
timer_overview_channel_id: int = 0
timer_overview_message_id: int = 0
allowed_maps: str = "4-1,4-2,4-3,4-4,1-5,1-6,1-7,2-5,2-6,2-7,3-5,3-6,3-7"
```
Add a **@property** (accessed WITHOUT parens; NOT a method) near `gate_rewards_map`/`voice_role_map`:
```
@property
def allowed_maps_list(self) -> list[str]:
    return [m.strip() for m in self.allowed_maps.split(",") if m.strip()]
```
(Plain `@property` works on a pydantic-settings model for read access; it is not a model field and
does not collide with `allowed_maps`.)

### `n3x-bot/n3x_bot/storage/schema.py`
Append a new table (String PK, tz-aware end_time, both non-null):
```
base_timers = Table(
    "base_timers", metadata,
    Column("map_name", String(20), primary_key=True),
    Column("end_time", DateTime(timezone=True), nullable=False),
)
```

### `n3x-bot/n3x_bot/storage/base.py`
Add 4 abstract async methods to `StatsRepository` (place a new "# base timers" section before the
"# bulk export / import" section):
- `async def set_base_timer(self, map_name: str, end_time: datetime) -> None` — upsert.
- `async def remove_base_timer(self, map_name: str) -> bool` — True if a row existed.
- `async def list_base_timers(self) -> dict[str, datetime]` — all rows, end_time tz-aware.
- `async def purge_expired_base_timers(self, now: datetime) -> list[str]` — delete `end_time <= now`, return removed map names.
Add `from datetime import datetime` to the imports at top (currently only imports models).

### `n3x-bot/n3x_bot/storage/json_repo.py`
- `_empty()`: add `"base_timers": {}` (dict `map_name -> ISO string`). `connect()`'s `setdefault`
  loop already backfills this key for pre-existing files.
- New methods (store ISO strings via `end_time.isoformat()`, read via `_parse_dt`):
  - `set_base_timer`: `self._db["base_timers"][map_name] = end_time.isoformat(); self._flush()`.
  - `list_base_timers`: `return {m: _parse_dt(v) for m, v in self._db["base_timers"].items()}`.
  - `remove_base_timer`: `existed = map_name in self._db["base_timers"]; self._db["base_timers"].pop(map_name, None); self._flush(); return existed`.
  - `purge_expired_base_timers(now)`: `removed = [m for m, v in self._db["base_timers"].items() if _parse_dt(v) <= now]; for m in removed: del self._db["base_timers"][m]; self._flush(); return removed`.
- `export_all()`: add `"base_timers": copy.deepcopy(self._db["base_timers"])` to the returned dict
  (already JSON-serializable — dict of ISO strings).
- `import_all()`: add `self._db["base_timers"] = copy.deepcopy(snapshot.get("base_timers", {}))`.
- `clear()`: no change (rebuilds via `_empty()`, which now includes `base_timers`).

### `n3x-bot/n3x_bot/storage/sql_repo.py`
Reuse the existing module-level helpers `_as_aware_utc` (line 19) and `_parse_dt` (line 15) and the
static `_dt` (line 582). New methods (new "# base timers" section, before bulk export/import):
- `set_base_timer`: exists-check then insert/update (mirror `set_last_post`/`set_streak`).
  **Write end_time as UTC**: `stored = _as_aware_utc(end_time).astimezone(timezone.utc)`, insert/update
  `end_time=stored`. This is the crucial deviation from existing columns (which always wrote UTC via
  `_now()`): base_timer inputs are arbitrary-tz aware (e.g. Berlin +02:00). SQLite drops tzinfo on
  write, so the stored *wall clock* MUST be UTC for the read-back `_as_aware_utc` coerce to reconstruct
  the correct instant.
- `list_base_timers`: `select(sc.base_timers)`; `return {r.map_name: _as_aware_utc(r.end_time) for r in rows}`
  (`_as_aware_utc` re-attaches UTC for sqlite's naive read; postgres already aware). Instant preserved
  because it was stored UTC.
- `remove_base_timer`: exists-check (`select(map_name).where(...)`), `delete(...)`; return bool.
- `purge_expired_base_timers(now)`: select all `(map_name, end_time)`; compute
  `removed = [r.map_name for r in rows if _as_aware_utc(r.end_time) <= _as_aware_utc(now)]` in Python
  (mirrors `add_gate_entry`'s Python-side threshold compare — avoids cross-dialect tz WHERE issues);
  `if removed: delete(...).where(sc.base_timers.c.map_name.in_(removed))`; return `removed`.
- `export_all()`: add `base_timers = {r.map_name: self._dt(r.end_time) for r in await conn.execute(select(sc.base_timers))}` and include `"base_timers": base_timers` in the returned dict (`_dt` → UTC ISO string).
- `import_all()`: insert each `for map_name, iso in snapshot.get("base_timers", {}).items():`
  `end_time=_as_aware_utc(_parse_dt(iso)).astimezone(timezone.utc)` (converts a cross-backend
  Berlin-ISO snapshot to UTC before storing; UTC-ISO is a no-op).
- `clear()`: add `sc.base_timers` to the delete-loop tuple.

### `n3x-bot/n3x_bot/bot.py`
- Import near the other feature imports (~line 34): `from n3x_bot.timers import register_timer_commands, start_timer_overview_loop`.
- `build_bot`: add `bot._timer_overview_loop = None` alongside the other `bot._*` attr init (~line 102,
  so the real-bot guard starts clean), and add `register_timer_commands(bot, repo, settings)` in the
  `register_*` block (after `register_welcome_commands`, before `return bot`).
- `on_ready` (optional, recommended for production B4 completeness, NOT required by any test): after the
  existing guarded `voice_flush_task.start()` block, add `start_timer_overview_loop(bot, repo, settings)`
  (self-guarded; best-effort). Keep it out of any try that would mask errors; it cannot break the wiring
  tests, which only call `build_bot` and never enter `on_ready`.

### `n3x-bot/n3x_bot/migrate.py` (optional, no test coverage)
Consider appending `"base_timers"` to `_DATA_TABLES` (line 25) so a destination holding only base
timers is treated as non-empty. Flagged as a judgment call — no test exercises it; leave as-is if
preferring minimal change.

## Data flow

`!base 4-1 30` from a role-holder:
1. Dispatch invokes `_base_cmd(ctx, "4-1", 30)`.
2. `has_base_timer_role(ctx.author, settings)` → True.
3. `now = datetime.now(ZoneInfo(settings.timezone))` (tz-aware, B6).
4. `start_base_timer(repo, settings, "4-1", 30, now)`: validates `"4-1" ∈ allowed_maps_list`,
   computes `end = now + 30m`, `repo.set_base_timer("4-1", end)` (json: ISO string; sql: UTC-normalized
   aware datetime), returns `end`.
5. `update_timer_overview(bot, repo, settings, now)`: `purge_expired(now)` drops any lapsed rows,
   `list_base_timers()` reads all (tz-aware), `build_timer_overview_embed(...)` renders blue sorted
   embed, `bot.get_channel(timer_overview_channel_id).fetch_message(timer_overview_message_id).edit(embed=...)`
   (best-effort).
6. Confirmation `ctx.send`.

Background: `start_timer_overview_loop` fires `update_timer_overview` every 30 s with a fresh tz-aware
`now`, so timers reconcile/expire even with no command traffic.

## Dependencies
- New packages: none. Uses stdlib `datetime`/`zoneinfo`, existing `discord`, `discord.ext.tasks`,
  `sqlalchemy`, pydantic-settings.
- Internal: `n3x_bot.config.Settings`, `n3x_bot.storage.base.StatsRepository`,
  `n3x_bot.storage.schema`, sql_repo helpers `_as_aware_utc`/`_parse_dt`/`_dt`.

## Build sequence (for the Coder)
1. **config.py** fields + `allowed_maps_list` property → greens the `tests/test_config.py` Base-timer
   block, and unblocks `_settings(...)` construction in `tests/test_timers.py`.
2. **schema.py** `base_timers` table.
3. **base.py** abstract methods (+ `datetime` import) → concrete backends must now implement them.
4. **json_repo.py** methods + `_empty`/export/import → greens the `json` parametrization of
   `tests/storage/test_base_timers_repository_contract.py`.
5. **sql_repo.py** methods + export/import/clear → greens the `sqlite` (and `postgres` when DSN set)
   parametrizations of the repository contract.
6. **timers.py** pure functions first (`build_timer_overview_embed`, `has_base_timer_role`,
   `start_base_timer`) → greens the embed/role/start tests; then `update_timer_overview`,
   `register_timer_commands`, `start_timer_overview_loop` → greens the overview/command/loop tests.
7. **bot.py** import + `register_timer_commands` call (+ `_timer_overview_loop=None`, optional on_ready
   loop start) → greens `test_build_bot_registers_base_and_basestop` and keeps `tests/test_bot_wiring.py`
   green (base/basestop expected in the exclusion tuples).

## Risks and open questions
- **SQLite UTC normalization (load-bearing).** Existing tz-aware columns only ever stored UTC (via
  `_now()`), so `_as_aware_utc` on read is a pure "re-attach UTC" for sqlite. base_timers is the first
  column fed arbitrary-tz aware input, so the writer MUST `.astimezone(timezone.utc)` before storing;
  otherwise sqlite drops the offset and the read-back coerce yields the wrong instant, failing
  `test_end_time_round_trips_tz_aware`. Design does this in `set_base_timer` and `import_all`. Flagging
  because it is subtle and easy to omit.
- **Cross-backend snapshot bytes differ.** json stores the *original* offset ISO string; sql stores the
  UTC ISO string. The `snapshot_is_stable` contract test is per-backend (json→json, sqlite→sqlite), so
  both are stable within a backend. A json→sql *migration* (real `migrate.py`) relies on `import_all`
  re-parsing + UTC-converting the ISO string, which the design handles. No test asserts cross-backend
  byte equality, so this is acceptable; noted so nobody "fixes" json to also store UTC (which would
  change the json contract expectation `read == _berlin(...)` — still true either way, but unnecessary).
- **Command gating lives in the body, not a decorator.** Required because tests call `.callback(...)`
  directly. If a coder reflexively adds a `@commands.has_role(...)`/check decorator, the refusal tests
  (which assert `ctx.send` awaited and repo untouched via a direct callback call) will not behave as the
  tests expect. Keep the in-body `has_base_timer_role` guard.
- **Loop guard vs MagicMock.** The `isinstance(existing, tasks.Loop)` guard is deliberately stricter
  than the `is_running()`-on-a-known-object pattern used by `event_reminder_task`/`voice_flush_task`,
  because here the Loop is created per-call and the test bot is a `MagicMock` (auto-creating a truthy
  `_timer_overview_loop`). Do not simplify to `getattr(...) is not None`.
- **on_ready loop start is optional.** The tdd deferred reconcile-on-ready (every render purges). Wiring
  `start_timer_overview_loop` into `on_ready` is recommended for production but untested; keep it
  best-effort and outside error-masking so it can't regress existing wiring/on_ready behavior.
