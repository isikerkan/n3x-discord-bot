# Architecture: Activity Tracking (v1 — tracking + storage + view)

Scope: message/voice/reaction counters, daily streak, night-owl counter, and a
`!activity` view command. NO achievements. Coverage target >= 80%. Follow the
existing `gates.py`/`admin.py` module-split + repository-contract patterns.

## Tests this design satisfies

`tests/test_config.py`
- `test_timezone_defaults_to_europe_berlin` — `Settings.timezone == "Europe/Berlin"`.
- `test_timezone_read_from_env` — env `TIMEZONE` overrides it.

`tests/storage/test_activity_repository_contract.py` (json + sqlite + postgres)
- `test_get_activity_defaults_to_zero`
- `test_add_activity_returns_new_running_total`
- `test_get_activity_reads_back_accumulated_total`
- `test_activity_metrics_are_independent`
- `test_activity_counters_are_per_user`
- `test_get_streak_missing_returns_none`
- `test_set_and_get_streak_roundtrip`
- `test_set_streak_overwrites_previous`
- `test_streak_is_per_user`
- `test_get_night_missing_returns_none`
- `test_set_and_get_night_roundtrip`
- `test_set_night_overwrites_previous`

`tests/storage/test_activity_export_import_contract.py` (json + sqlite + postgres)
- `test_export_all_includes_activity_and_is_json_serializable`
- `test_round_trip_preserves_activity_counters`
- `test_round_trip_preserves_streak`
- `test_round_trip_preserves_night`
- `test_snapshot_is_stable_after_activity_round_trip` (cross-backend `dest.export_all() == snapshot`)
- `test_clear_wipes_activity_data`

`tests/test_activity.py`
- pure: `test_elapsed_seconds_*` (3), `test_next_streak_*` (5), `test_is_night_*` (4),
  `test_next_night_*` (3), `test_now_local_*`, `test_today_local_*`
- wiring: `test_build_bot_registers_activity_view_command`,
  `test_register_activity_registers_command_on_bot`
- message: `test_record_message_activity_increments_message_count`,
  `_starts_streak_for_today`, `_counts_night_inside_window`, `_skips_night_outside_window`
- voice: `test_voice_join_then_leave_persists_elapsed_seconds`, `test_voice_join_alone_persists_nothing`
- reaction: `test_reaction_increments_reaction_count`,
  `test_reaction_skipped_in_gate_input_channel`, `test_reaction_skipped_in_gate_stats_channel`
- view: `test_activity_command_reports_tracked_values`

## Files to create

- `n3x_bot/activity.py` — pure logic + event helpers + view command registration.
  Symbols (all module-level):
  - `elapsed_seconds(join_dt: datetime, leave_dt: datetime) -> int`
  - `next_streak(prev: dict | None, today: date) -> dict`
  - `is_night(dt: datetime) -> bool`
  - `next_night(prev: dict | None, today: date) -> dict | None`
  - `now_local(settings: Settings) -> datetime`
  - `today_local(settings: Settings) -> date`
  - `async record_message_activity(repo, settings, member_id: int, now: datetime) -> None`
  - `async handle_voice_state_update(bot, repo, settings, member, before, after, now: datetime) -> None`
  - `async handle_activity_reaction(bot, repo, settings, payload) -> None`
  - `register_activity(bot, repo, settings) -> None`

  Imports only: `datetime`, `date`, `zoneinfo.ZoneInfo`, `discord`, `discord.ext.commands`,
  `n3x_bot.config.Settings` (type hint), `n3x_bot.storage.base.StatsRepository` (type hint).
  MUST NOT import `n3x_bot.bot` (avoids the import cycle — the view command
  replies via `ctx.send`, never through bot `_send_*` helpers).

## Files to modify

- `n3x_bot/config.py` — add one field after `admin_role_id` (line 18):
  `timezone: str = "Europe/Berlin"`. pydantic-settings maps it to env `TIMEZONE`
  automatically (matches `test_timezone_read_from_env`). No validator change.

- `n3x_bot/storage/base.py` — add 6 abstract methods to `StatsRepository`
  (a new `# activity` section after `# gate tracker`, before `# bulk export / import`):
  - `async add_activity(self, discord_id: int, metric: str, amount: int) -> int`
  - `async get_activity(self, discord_id: int, metric: str) -> int`
  - `async get_streak(self, discord_id: int) -> dict | None`
  - `async set_streak(self, discord_id: int, current_streak: int, last_active_date: str, max_streak: int) -> None`
  - `async get_night(self, discord_id: int) -> dict | None`
  - `async set_night(self, discord_id: int, night_count: int, last_night_date: str) -> None`

- `n3x_bot/storage/schema.py` — add 3 tables (see Storage design).

- `n3x_bot/storage/json_repo.py` — extend `_empty()`, add the 6 methods, extend
  `export_all`/`import_all` (`clear` already resets via `_empty()`).

- `n3x_bot/storage/sql_repo.py` — add the 6 methods, extend `export_all`/`import_all`/`clear`.

- `n3x_bot/bot.py` — re-export the activity symbols, register the command in
  `build_bot`, seed `voice_join_times`, and wire the three events (see Event wiring).

- `.env.example` — add under a new `# ─── Activity tracking ───` block:
  `TIMEZONE=Europe/Berlin`.

## Storage design

### Metric names
`metric ∈ {"voice_seconds", "messages", "reactions"}`. `voice_seconds` can grow
large → use `BigInteger` for counts.

### Streak/night shape (DECISION — ISO date STRINGS everywhere)
Both repo returns and pure-logic returns carry ISO date strings, never `date`
objects. Dates stored in SQL as `String(10)` (`"YYYY-MM-DD"`) — no DB date type,
so no tz/parse drift, and cross-backend `==` is exact.
- streak dict: `{"current_streak": int, "last_active_date": str, "max_streak": int}`
- night dict: `{"night_count": int, "last_night_date": str}`

### SQL schema (schema.py — append after `gate_entries`)
```
activity_counters = Table(
    "activity_counters", metadata,
    Column("discord_id", BigInteger, primary_key=True),
    Column("metric", String(20), primary_key=True),
    Column("count", BigInteger, nullable=False, default=0),
)
streak_stats = Table(
    "streak_stats", metadata,
    Column("discord_id", BigInteger, primary_key=True),
    Column("current_streak", Integer, nullable=False),
    Column("last_active_date", String(10), nullable=False),
    Column("max_streak", Integer, nullable=False),
)
night_stats = Table(
    "night_stats", metadata,
    Column("discord_id", BigInteger, primary_key=True),
    Column("night_count", Integer, nullable=False),
    Column("last_night_date", String(10), nullable=False),
)
```
No autoincrement ids → nothing to add to the `seq` block or the postgres
`setval` loop. `metadata.create_all` (already called in `connect()`) creates them.

### JSON `_db` shape (json_repo `_empty()` — add 3 keys)
```
"activity_counters": {},   # {str(discord_id): {metric: count}}
"streak_stats": {},        # {str(discord_id): {"current_streak","last_active_date","max_streak"}}
"night_stats": {},         # {str(discord_id): {"night_count","last_night_date"}}
```
`connect()` already `setdefault`s every `_empty()` key, so existing on-disk DBs
gain the keys on load (backward compatible).

### Repository methods — semantics (identical across json + sql)
- `add_activity(id, metric, amount)`: upsert counter, return NEW total.
  - json: `d = activity_counters.setdefault(str(id), {}); d[metric] = d.get(metric, 0) + amount; flush; return d[metric]`.
  - sql: select `count` where `discord_id & metric`; insert `count=amount` if
    absent else update to `existing+amount`; return new total. Mirror the
    `record_use` select/insert/update upsert pattern (single `engine.begin()`).
- `get_activity(id, metric)`: return stored count or `0` (never raises).
- `get_streak(id)`: return the dict (str keys/ISO) or `None` if absent.
- `set_streak(id, cur, last, mx)`: upsert the row (json: assign dict; sql: select→update-or-insert).
- `get_night(id)` / `set_night(id, cnt, last)`: same pattern.
- json getters return the dict directly (build a fresh dict with the 3 / 2 keys,
  do not leak `_db` internals); the values are plain ints/strs so no deepcopy needed.

### export_all additions (both backends — MUST produce byte-identical shapes)
Add three keys to the returned snapshot dict:
```
"activity_counters": {str(discord_id): {metric: int_count}},
"streak_stats":      {str(discord_id): {"current_streak": int, "last_active_date": str, "max_streak": int}},
"night_stats":       {str(discord_id): {"night_count": int, "last_night_date": str}},
```
- json: `copy.deepcopy` the three `_db` dicts.
- sql: build the nested dicts from `select(...)`; cast `discord_id` to `str` for
  keys, keep ints for counts/streaks and strings for dates. Ordering is
  irrelevant for `==` (dict equality is order-independent), so no `order_by`
  needed for these three — but do it for determinism if trivial.
- Cross-backend `==` holds because keys are `str`, dates are ISO `str`, counts
  are `int` in both — same as the existing `user_stats`/`target_stats` maps.

### import_all additions (both backends)
Use `snapshot.get(key, {})` (tolerant of pre-feature snapshots that lack the keys):
- json: deepcopy the three maps into `_db`.
- sql: iterate and `insert` rows:
  - `activity_counters`: nested loop `discord_id → metric → count`,
    `insert(...).values(discord_id=int(did), metric=metric, count=count)`.
  - `streak_stats`: one insert per `did` from its dict.
  - `night_stats`: one insert per `did` from its dict.

### clear additions
- json: already covered — `clear()` calls `_empty()`.
- sql: add `sc.activity_counters, sc.streak_stats, sc.night_stats` to the
  `delete(table)` loop tuple (order doesn't matter — no FKs between them).

## activity.py pure-function algorithms

`elapsed_seconds(join_dt, leave_dt)`
- `return int((leave_dt - join_dt).total_seconds())` — `int()` truncates toward
  zero (90.9→90; same instant→0). Works for naive or aware as long as both match.

`next_streak(prev, today)` (`today` is a `date`; returns dict with ISO string)
- `t = today.isoformat()`
- `prev is None` → `{"current_streak": 1, "last_active_date": t, "max_streak": 1}`
- `prev["last_active_date"] == t` → `return prev` (unchanged — same-day no-op)
- `last = date.fromisoformat(prev["last_active_date"]); delta = (today - last).days`
- `delta == 1` → `cur = prev["current_streak"] + 1; mx = max(prev["max_streak"], cur);`
  `{"current_streak": cur, "last_active_date": t, "max_streak": mx}`
- else (gap `delta > 1`, or any non-consecutive) → `{"current_streak": 1, "last_active_date": t, "max_streak": prev["max_streak"]}`

`is_night(dt)` → `return 0 <= dt.hour < 5` (caller passes LOCAL time; midnight→True, 04:59→True, 05:00→False, 23:59→False).

`next_night(prev, today)` (DECISION for already-counted: return the UNCHANGED dict, not None)
- `t = today.isoformat()`
- `prev is None` → `{"night_count": 1, "last_night_date": t}`
- `prev["last_night_date"] == t` → `return prev` (already counted today; unchanged)
- else → `{"night_count": prev["night_count"] + 1, "last_night_date": t}`

`now_local(settings)` → `datetime.now(ZoneInfo(settings.timezone))` (tz-aware; injectable for tests).

`today_local(settings)` → `now_local(settings).date()`.

## Event helper algorithms

`record_message_activity(repo, settings, member_id, now)` — message → +1, then streak, then night:
1. `await repo.add_activity(member_id, "messages", 1)`
2. `today = now.date()`
3. `prev = await repo.get_streak(member_id); new = next_streak(prev, today)`
   `if new != prev: await repo.set_streak(member_id, new["current_streak"], new["last_active_date"], new["max_streak"])`
4. `if is_night(now):` `prev_n = await repo.get_night(member_id); new_n = next_night(prev_n, today)`
   `if new_n is not None and new_n != prev_n: await repo.set_night(member_id, new_n["night_count"], new_n["last_night_date"])`
   (the `new_n != prev_n` guard makes the already-counted "unchanged dict" case a
   clean no-op, per the DECISION above — compute-before-write, no redundant flush.)

`handle_voice_state_update(bot, repo, settings, member, before, after, now)`:
1. `if getattr(member, "bot", False): return`
2. `times = bot.voice_join_times; b = before.channel; a = after.channel`
3. join (`b is None and a is not None`): `times[member.id] = now`
4. leave (`b is not None and a is None`): pop join time; if present,
   `secs = elapsed_seconds(join, now); if secs > 0: await repo.add_activity(member.id, "voice_seconds", secs)`
5. move (`b and a` both non-None and `b.id != a.id`): flush old (same as leave)
   THEN `times[member.id] = now` (restart timing for the new channel)
6. else (same channel — mute/deaf/self-video toggle): no-op.
- Note: voice does NOT touch streak/night in v1 (only `record_message_activity` does).
  Nothing persists on join alone (`test_voice_join_alone_persists_nothing`).

`handle_activity_reaction(bot, repo, settings, payload)` — reaction → ONLY the reaction counter (no streak/night):
1. `member = getattr(payload, "member", None)`
2. `if member is None or getattr(member, "bot", False): return` (ignores raw removals / bot self-reactions)
3. `if payload.channel_id in (settings.gate_input_channel_id, settings.gate_stats_channel_id): return`
   (skip set = the two gate channels)
4. `await repo.add_activity(payload.user_id, "reactions", 1)`

`register_activity(bot, repo, settings)` — idempotent view-command registration:
1. `if bot.get_command("activity") is not None: return`
2. define `async def _activity_cmd(ctx, member: discord.Member = None):`
   - `target = member or ctx.author`
   - read: `msgs = await repo.get_activity(target.id, "messages")`,
     `reacts = await repo.get_activity(target.id, "reactions")`,
     `vsecs = await repo.get_activity(target.id, "voice_seconds")`,
     `streak = await repo.get_streak(target.id) or {"current_streak": 0, "max_streak": 0}`,
     `night = await repo.get_night(target.id) or {"night_count": 0}`
   - format voice seconds as human h/m (helper, e.g. `f"{vsecs//3600}h {vsecs%3600//60}m"`)
   - build a `discord.Embed` with fields carrying the raw numbers
     (`msgs`, `reacts`, `streak["current_streak"]`, `streak["max_streak"]`,
     `night["night_count"]`) and `await ctx.send(embed=embed)`.
   - The test flattens title+description+all field name/values and asserts the
     tokens `"17","23","13","29","8"` appear; keep the numbers unformatted in the
     field values. 3661 voice-seconds renders `"1h 1m"` — must not collide with
     those tokens (it doesn't).
   - `member` branch: designed (target = member) though only the self path
     (`cmd.callback(ctx)` with a mocked `ctx.author`) is unit-tested.
3. `bot.add_command(commands.Command(_activity_cmd, name="activity"))`

Slash variant: OMIT (not trivial here; prefix-only is acceptable per brief).

## Event wiring in bot.py (RISKIEST PART — integrate, do not redefine)

Current `_wire_events` already defines `@bot.event on_message`, `on_member_update`,
`on_member_join`, `on_member_remove`, `on_ready`, `on_command_error`. There is NO
`on_voice_state_update` and NO `on_raw_reaction_add` anywhere (verified by grep).
Redefining an existing `@bot.event` would OVERWRITE it, so message activity must be
CALLED FROM the existing `on_message`; the two new events are ADDED fresh.

Re-export (top of bot.py, alongside the other `from n3x_bot.X import ...`):
```
from n3x_bot.activity import (
    register_activity,
    record_message_activity,
    handle_voice_state_update,
    handle_activity_reaction,
    now_local,
)
```
This binds `register_activity`/`handle_voice_state_update`/`handle_activity_reaction`
as `n3x_bot.bot` attributes so the test imports resolve. No cycle: activity.py does
not import bot.

`build_bot` (after `bot._gate_embed_msg_id = None`, before `_wire_events`):
```
bot.voice_join_times = {}     # {member_id: aware datetime of current-session join}
```
and after `register_admin_commands(...)`:
```
register_activity(bot, repo, settings)
```

Inside existing `on_message` — add ONE call after the `if message.author == bot.user: return`
guard and before/after the gate handling (order doesn't matter for correctness),
guarding out bots so other bots don't accrue activity:
```
if not message.author.bot:
    await record_message_activity(repo, settings, message.author.id, now_local(settings))
```
(Every non-bot message counts, commands included — tests don't constrain this;
keep it simple.)

Add two NEW `@bot.event` handlers in `_wire_events`:
```
@bot.event
async def on_voice_state_update(member, before, after):
    await handle_voice_state_update(bot, repo, settings, member, before, after, now_local(settings))

@bot.event
async def on_raw_reaction_add(payload):
    await handle_activity_reaction(bot, repo, settings, payload)
```
`now` is always obtained via `now_local(settings)` at the call site so the handlers
stay pure/injectable (tests pass their own `now`).

`on_ready` additions (thin, not unit-tested — keep minimal):
- Voice seeding: after the member loop, for each `guild.voice_channels` and each
  non-bot member currently in a voice channel, `bot.voice_join_times[m.id] = now_local(settings)`
  so members already connected at startup get timed from `on_ready`.
- Periodic voice-flush task: a `@tasks.loop(minutes=5)` guarded-start task
  (`if not <task>.is_running(): <task>.start()`, mirroring `event_reminder_task`
  per bug B4) that, for each tracked `voice_join_times` entry, flushes elapsed
  since the stored join, adds it to `voice_seconds`, and RESETS the stored join to
  `now_local(settings)` (so long sessions persist incrementally and aren't lost on
  restart). Define it in `_wire_events` next to `event_reminder_task`. Keep the
  body a handful of lines; it is not required by any test — do not over-engineer.

## Data flow (representative traces)

Night-time message:
`on_message` → `record_message_activity(repo, settings, author_id, now_local(settings))`
→ `add_activity(id,"messages",1)` → `get_streak`→`next_streak`→`set_streak` (if changed)
→ `is_night(now)` True → `get_night`→`next_night`→`set_night` (if changed).

Voice session:
join `on_voice_state_update(before=None, after=chan)` → `voice_join_times[id]=now`.
leave `on_voice_state_update(before=chan, after=None)` → pop join → `elapsed_seconds`
→ `add_activity(id,"voice_seconds",secs)`.

`!activity`:
`_activity_cmd(ctx)` → `get_activity`×3 + `get_streak` + `get_night` → build embed → `ctx.send(embed=...)`.

Export/import round-trip:
`export_all()` adds `activity_counters`/`streak_stats`/`night_stats` maps →
`json.dumps` OK → `import_all()` re-inserts → `export_all()` on dest equals source
(str keys, ISO strings, int counts → order-independent `==`).

## Dependencies
- New packages: NONE. `zoneinfo` is stdlib (py>=3.9); SQLAlchemy already present.
- Internal deps: activity.py → `config.Settings`, `storage.base.StatsRepository`,
  `discord`, `discord.ext.commands`. bot.py → activity.py (one-way).

## Build sequence (for the Coder)
1. `config.py`: add `timezone` field. Run `tests/test_config.py` → 2 tz tests GREEN.
2. `schema.py`: add the 3 tables.
3. `base.py`: add the 6 abstract methods.
4. `json_repo.py`: `_empty()` keys + 6 methods + export/import additions.
   Run `tests/storage/test_activity_repository_contract.py` (json id) and the
   activity export/import contract (json) → GREEN.
5. `sql_repo.py`: 6 methods + export/import/clear additions. Re-run the two
   storage contract files → sqlite (and postgres if `TEST_POSTGRES_URL`) GREEN,
   including the cross-backend `snapshot ==` stability test.
6. `activity.py`: pure functions first (`elapsed_seconds`, `next_streak`, `is_night`,
   `next_night`, `now_local`, `today_local`) → the pure `test_activity` tests GREEN.
7. `activity.py`: event helpers (`record_message_activity`, `handle_voice_state_update`,
   `handle_activity_reaction`) + `register_activity` view command.
8. `bot.py`: re-export imports, `voice_join_times`, `register_activity(...)` in
   `build_bot`, `record_message_activity` call in `on_message`, the two new
   `@bot.event` handlers, `on_ready` seeding + guarded flush task.
   Run full `tests/test_activity.py` → GREEN.
9. Full suite + coverage; add `TIMEZONE` to `.env.example`.

## Risks and open questions
- CRITICAL — event override: `on_message` is redefined only if you use `@bot.event`;
  message activity MUST be a call inside the existing `on_message`. `on_voice_state_update`
  / `on_raw_reaction_add` do not exist today, so adding them as `@bot.event` is safe
  (confirmed by grep). Any future re-run that introduces a second `@bot.event` with
  these names would silently override — keep them single-defined in `_wire_events`.
- Cross-backend export `==`: guaranteed only if json and sql emit the SAME shape
  (str `discord_id` keys, ISO-string dates, int counts, no stray fields). Do not
  add ids/timestamps to the three activity maps.
- Timezone correctness: `now_local`/`today_local` use `ZoneInfo(settings.timezone)`;
  `is_night` reads the LOCAL `dt.hour`; `next_streak`/`next_night` compare LOCAL
  `.date()`. All time enters handlers via injected `now` — never call `datetime.now()`
  inside the pure logic.
- Voice in-memory state (`bot.voice_join_times`) is lost on restart; the guarded
  periodic flush + `on_ready` seeding mitigate loss but are best-effort and untested.
  Acceptable for v1.
- Idempotent registration: `register_activity` guards on `bot.get_command("activity")`;
  `build_bot` calls it once and the standalone test call is a no-op.
- Performance / correctness (bugs B2/B3): compute-before-write — read current
  streak/night, compute the next value purely, write only when it changed. Async
  repo only; no blocking calls in handlers.
- Reaction handler ignores `payload` with no `member` (raw removals, or reactions
  where the member isn't cached) and bot self-reactions — flag if the TDD stage
  intended to count reactions from users not present in `payload.member`.
- Open question for TDD: `record_message_activity` is wired to fire on EVERY non-bot
  message including bot commands and messages in the gate-input channel. No test
  constrains this; if commands/gate posts should be excluded, add a guard — raise
  back rather than assume.
