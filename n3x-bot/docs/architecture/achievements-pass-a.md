# Architecture: Achievements — Pass A (definitions + detection engine + storage + `/erfolge`)

Scope: declarative achievement definitions, a pure detection engine, metric
resolution, detect-and-record, storage surface (new `achievements` table +
per-user gate queries), the `!erfolge` progress command, and side-effect-free
detection wiring into the existing activity/gate hot paths. **NO** image cards,
auto-posting, roles, overview UI, or sync command (later passes).

All new *logic* lives in a new module `n3x_bot/achievements.py`. `bot.py` only
imports + wires. `achievements.py` MUST NOT import `n3x_bot.bot` (no import is
needed at all — it depends only on `config`, `storage.base`, `discord`).

## Tests this design satisfies

`tests/test_achievements.py`
- `test_milestone_channel_id_defaults_to_zero`
- `test_overview_channel_id_defaults_to_zero`
- `test_voice_achievement_roles_defaults_to_empty_string`
- `test_milestone_channel_id_read_from_env`
- `test_overview_channel_id_read_from_env`
- `test_total_achievements_constant_is_59`
- `test_there_are_exactly_59_definitions`
- `test_all_definition_ids_are_unique`
- `test_every_definition_exposes_the_required_fields`
- `test_expected_sample_ids_are_present`
- `test_every_gate_type_has_all_eight_tiers`
- `test_thresholds_match_the_id_suffix`
- `test_titles_are_ported_from_v3`
- `test_message_category_achievements_are_secret`
- `test_reaction_category_achievements_are_secret`
- `test_gate_voice_streak_night_are_not_secret`
- `test_exactly_eight_definitions_are_secret`
- `test_tracker_metric_names_map_to_repo_sources`
- `test_gate_metric_names_map_to_gate_sources`
- `test_newly_unlocked_crosses_multiple_tiers_at_once`
- `test_newly_unlocked_excludes_already_unlocked`
- `test_newly_unlocked_returns_nothing_below_lowest_threshold`
- `test_newly_unlocked_returns_only_the_newly_crossed_tier`
- `test_user_metric_value_reads_message_counter`
- `test_user_metric_value_reads_voice_seconds`
- `test_user_metric_value_reads_reactions`
- `test_user_metric_value_streak_uses_max_streak`
- `test_user_metric_value_night_uses_night_count`
- `test_user_metric_value_defaults_to_zero_without_streak`
- `test_user_metric_value_gate_type_counts_entries_of_that_type`
- `test_user_metric_value_gate_total_sums_all_gate_counts`
- `test_user_metric_value_gate_cost_total_sums_all_costs`
- `test_check_achievements_records_newly_unlocked`
- `test_check_achievements_is_idempotent_on_second_call`
- `test_check_achievements_records_all_crossed_streak_tiers`
- `test_check_achievements_records_nothing_below_threshold`
- `test_register_achievement_commands_wires_erfolge_prefix_command`
- `test_register_achievement_commands_is_idempotent`
- `test_erfolge_reports_unlocked_count_and_total`

`tests/storage/test_achievements_repository_contract.py` (json + sqlite + postgres)
- unlock returns True when new / False when already; has False before / True after;
  get_user_achievements empty default / returns all / isolated per user;
  list_achievement_holders empty / maps every user to their unlocks.

`tests/storage/test_achievements_export_import_contract.py` (json + sqlite + postgres)
- export includes achievements and is `json.dumps`-able; round-trip preserves;
  snapshot stable after round-trip; clear wipes achievements.

## Files to create

- `n3x_bot/achievements.py` — the whole feature module.
  - `@dataclass(frozen=True) class Achievement` with attributes
    `id: str, category: str, metric: str, threshold: int, title: str, secret: bool`.
    Frozen + attribute access (tests use `a.id`, `a.threshold`, …).
  - `ACHIEVEMENTS: list[Achievement]` — exactly 59 entries (built by the loops in
    "ACHIEVEMENTS definition plan" below). Order: gate tiers (a,b,c,d × 8),
    then gate specials, then voice, message, streak, night, reaction.
  - `TOTAL_ACHIEVEMENTS: int = 59` (literal constant; `len(ACHIEVEMENTS)` must equal it).
  - `def newly_unlocked(defs_for_metric: list[Achievement], value: int, already: set[str]) -> set[str]`
    — pure, no I/O.
  - `async def user_metric_value(repo: StatsRepository, discord_id: int, metric: str) -> int`.
  - `async def check_achievements(repo: StatsRepository, discord_id: int, metric: str) -> list[Achievement]`.
  - `def register_achievement_commands(bot, repo: StatsRepository, settings: Settings) -> None`
    — idempotent, adds prefix command `erfolge`.
  - Module imports: `from dataclasses import dataclass`, `import discord`,
    `from discord.ext import commands`, `from n3x_bot.config import Settings`,
    `from n3x_bot.storage.base import StatsRepository`. NOTHING from `n3x_bot.bot`
    or `n3x_bot.activity`.

## Files to modify

- `n3x_bot/config.py` (`Settings`) — add three fields alongside the existing
  gate block (env names are the upper-snake of the attribute; pydantic-settings
  maps them automatically as the other fields already demonstrate):
  - `milestone_channel_id: int = 0`
  - `overview_channel_id: int = 0`
  - `voice_achievement_roles: str = ""`
  No validators, no behaviour — Pass A pins fields only.

- `n3x_bot/storage/schema.py` — add a table after `night_stats`:
  ```
  achievements = Table(
      "achievements", metadata,
      Column("discord_id", BigInteger, primary_key=True),
      Column("achievement_id", String(50), primary_key=True),
  )
  ```
  (Composite PK matches v3's `PRIMARY KEY (user_id, achievement_id)`; `discord_id`
  is BigInteger to match every other discord-id column.)

- `n3x_bot/storage/base.py` (`StatsRepository` ABC) — add six abstract coroutines
  in an "achievements" section, plus two per-user gate methods in the gate section:
  - `async def unlock_achievement(self, discord_id: int, achievement_id: str) -> bool`
    — insert `(discord_id, achievement_id)`; return True if newly inserted, False
    if the row already existed.
  - `async def has_achievement(self, discord_id: int, achievement_id: str) -> bool`
  - `async def get_user_achievements(self, discord_id: int) -> set[str]`
  - `async def list_achievement_holders(self) -> dict[int, set[str]]`
    — every discord_id with ≥1 unlock → its set of ids.
  - `async def user_gate_counts(self, discord_id: int) -> dict[str, int]`
    — `{gate_type: count}` over that user's `gate_entries` rows; only gate types the
    user actually has appear (so `d` is naturally absent until the delta port).
  - `async def user_gate_cost_total(self, discord_id: int) -> int`
    — sum of `cost` over that user's `gate_entries` rows (0 if none).

- `n3x_bot/storage/json_repo.py` — see "Storage" below. Add `"achievements": {}`
  to `_empty()`; add the six + two methods; extend `export_all`/`import_all`;
  `clear()` already resets via `_empty()` so it needs no change once `_empty()`
  includes the key.

- `n3x_bot/storage/sql_repo.py` — add the six + two methods; extend
  `export_all`/`import_all`; add `sc.achievements` to the `clear()` delete loop
  (list it FIRST — it is independent, no FKs, order is irrelevant but keep it
  grouped with the other discord-id tables).

- `n3x_bot/bot.py` — import + wire only:
  - `from n3x_bot.achievements import register_achievement_commands, check_achievements`
    (re-export, mirroring how `activity`/`gates` symbols are surfaced from `bot`).
  - In `build_bot`, after `register_activity(bot, repo, settings)` add
    `register_achievement_commands(bot, repo, settings)`.
  - In `handle_gate_input_message`, inside the existing `if inserted:` branch
    (after `update_gate_stats_embed`), add gate-metric detection (see Data flow).

- `n3x_bot/activity.py` — add detection wiring (side-effect-free) in the three
  event helpers (see Data flow). Add `from n3x_bot.achievements import check_achievements`
  at module top (no cycle: `achievements` imports neither `activity` nor `bot`).

- `n3x_bot/migrate.py` — add `"achievements"` to the `_DATA_TABLES` tuple so a
  destination holding only achievements counts as non-empty.

- `.env.example` — add an "Achievements / Milestones" section:
  ```
  # ─── Achievements / Milestones ─────────────────────────────────────────────
  # Channel milestone announcement cards are posted in (later pass). 0 disables.
  MILESTONE_CHANNEL_ID=0
  # Channel the achievement overview UI is posted in (later pass). 0 disables.
  OVERVIEW_CHANNEL_ID=0
  # Voice achievement -> role id mapping (later pass). Empty disables.
  VOICE_ACHIEVEMENT_ROLES=
  ```

## ACHIEVEMENTS definition plan (all 59, ported verbatim from v3)

The coder builds the list with small literal tables + comprehensions. Source of
truth: `manus versions/v3/bot.py` lines 718–764, 855, 1430–1434.

### Gate tiers — 32 (category `gate`, `secret=False`)
`GATE_NAMES = {"a": "Alpha", "b": "Beta", "c": "Gamma", "d": "Delta"}`
`MILESTONE_LEVELS = {5:"Bronze",10:"Silber",25:"Gold",50:"Platin",100:"Diamant",250:"Master",500:"Grandmaster",1000:"Gott"}`

For each `gtype` in `("a","b","c","d")`, for each `(thr, level)` in `MILESTONE_LEVELS`:
- `id = f"{gtype}_{thr}"`  (e.g. `a_5`, `d_1000`)
- `category = "gate"`
- `metric = f"gate_{gtype}"`  (e.g. `gate_a` … `gate_d`)
- `threshold = thr`
- `title = f"{GATE_NAMES[gtype]} {level} Pilot"`  (e.g. `a_5` → "Alpha Bronze Pilot", `d_1000` → "Delta Gott Pilot")
- `secret = False`

### Gate specials — 4 (category `gate`, `secret=False`)  [controller decision #3]
| id | metric | threshold | title |
|----|--------|-----------|-------|
| `total_1` | `gate_total` | 1 | Einsteiger Pilot |
| `total_50` | `gate_total` | 50 | Profi Pilot |
| `total_100` | `gate_total` | 100 | Veteran Pilot |
| `millionaire` | `gate_cost_total` | 1000000 | Millionärs-Club Pilot |

### Voice — 6 (category `voice`, metric `voice_seconds`, `secret=False`)
| id | threshold | title |
|----|-----------|-------|
| `voice_3600` | 3600 | Rookie Talker |
| `voice_36000` | 36000 | Stammgast |
| `voice_180000` | 180000 | Stammspieler |
| `voice_360000` | 360000 | Veteran |
| `voice_1800000` | 1800000 | Elite Player |
| `voice_3600000` | 3600000 | Night Shadow Legende |

### Message — 4 (category `message`, metric `messages`, `secret=True`)  ← id prefix is `msg_`
| id | threshold | title |
|----|-----------|-------|
| `msg_1000` | 1000 | Tastatur-Krieger |
| `msg_5000` | 5000 | Chat-Maschine |
| `msg_10000` | 10000 | Nachrichten-Veteran |
| `msg_50000` | 50000 | Spam-Gott |

### Streak — 6 (category `streak`, metric `streak`, `secret=False`)
| id | threshold | title |
|----|-----------|-------|
| `streak_7` | 7 | Treuer Soldat |
| `streak_14` | 14 | Zuverlässig |
| `streak_30` | 30 | Monats-Krieger |
| `streak_60` | 60 | Unaufhaltsam |
| `streak_100` | 100 | Eiserner Wille |
| `streak_365` | 365 | 365-Tage-Legende |

### Night — 3 (category `night`, metric `night`, `secret=False`)
| id | threshold | title |
|----|-----------|-------|
| `night_10` | 10 | Nachteule |
| `night_50` | 50 | Schlaflos |
| `night_100` | 100 | Vampir Pilot |

### Reaction — 4 (category `reaction`, metric `reactions`, `secret=True`)
| id | threshold | title |
|----|-----------|-------|
| `reaction_100` | 100 | Emoji-Fan |
| `reaction_500` | 500 | Reaktions-Profi |
| `reaction_1000` | 1000 | Reaktions-Meister |
| `reaction_5000` | 5000 | Reaktions-Maschine |

**Count check:** 32 + 4 + 6 + 4 + 6 + 3 + 4 = **59**. Secret = message(4) + reaction(4) = **8**.
NOTE: v3's `EVENT_MILESTONES` are intentionally EXCLUDED (event tracking is not
part of the 59 and has no metric source in this codebase).

**Category → metric summary (for reference):**
gate tiers → `gate_a`/`gate_b`/`gate_c`/`gate_d`; `total_*` → `gate_total`;
`millionaire` → `gate_cost_total`; voice → `voice_seconds`; message → `messages`;
reaction → `reactions`; streak → `streak`; night → `night`.

## `user_metric_value` metric → source mapping

| metric | resolution |
|--------|-----------|
| `messages` | `await repo.get_activity(discord_id, "messages")` |
| `voice_seconds` | `await repo.get_activity(discord_id, "voice_seconds")` |
| `reactions` | `await repo.get_activity(discord_id, "reactions")` |
| `streak` | `r = await repo.get_streak(discord_id); r["max_streak"] if r else 0` |
| `night` | `r = await repo.get_night(discord_id); r["night_count"] if r else 0` |
| `gate_a`/`gate_b`/`gate_c`/`gate_d` | `counts = await repo.user_gate_counts(discord_id); counts.get(metric.split("_")[1], 0)` |
| `gate_total` | `sum((await repo.user_gate_counts(discord_id)).values())` |
| `gate_cost_total` | `await repo.user_gate_cost_total(discord_id)` |

For `messages`/`voice_seconds`/`reactions` the metric name IS the `get_activity`
key (they match `add_activity` calls in `activity.py`). Implement as a small
dispatch (dict-of-handlers or if/elif); unknown metric → 0 is acceptable but not
required by any test.

## Storage design detail

### JSON (`json_repo.py`)
Internal shape: `self._db["achievements"]: dict[str, list[str]]` keyed by
`str(discord_id)` → list of achievement ids (JSON has no set type).
- `unlock_achievement`: `lst = self._db["achievements"].setdefault(str(discord_id), [])`;
  if `achievement_id in lst`: return `False`; else append, `self._flush()`, return `True`.
- `has_achievement`: `achievement_id in self._db["achievements"].get(str(discord_id), [])`.
- `get_user_achievements`: `set(self._db["achievements"].get(str(discord_id), []))`.
- `list_achievement_holders`: `{int(did): set(ids) for did, ids in self._db["achievements"].items() if ids}`.
- `user_gate_counts`: iterate `gate_entries`, count `r["gate_type"]` where `r["user_id"] == discord_id` → dict.
- `user_gate_cost_total`: `sum(r["cost"] for r in gate_entries if r["user_id"] == discord_id)`.
- `export_all`: add key `"achievements": {did: sorted(ids) for did, ids in self._db["achievements"].items() if ids}`
  (sorted lists = deterministic + `json.dumps`-able; drop empty lists so the
  snapshot never drifts).
- `import_all`: `self._db["achievements"] = copy.deepcopy(snapshot.get("achievements", {}))`.
- `clear`: no change — `_empty()` now includes `"achievements": {}`.

### SQL (`sql_repo.py`)
- `unlock_achievement`: within `engine.begin()`, `select` the PK row; if present
  return `False`; else `insert(sc.achievements).values(discord_id=…, achievement_id=…)`,
  return `True`.
- `has_achievement`: `select(sc.achievements).where(discord_id & achievement_id).one_or_none() is not None`.
- `get_user_achievements`: `select(sc.achievements.c.achievement_id).where(discord_id)` → `{r.achievement_id for r in rows}`.
- `list_achievement_holders`: `select(sc.achievements)`; fold into
  `out.setdefault(r.discord_id, set()).add(r.achievement_id)`; return `out`
  (keys are already `int`).
- `user_gate_counts`: `select(sc.gate_entries.c.gate_type, func.count()).where(user_id == discord_id).group_by(gate_type)` → `{gate_type: count}`.
- `user_gate_cost_total`: `select(func.coalesce(func.sum(sc.gate_entries.c.cost), 0)).where(user_id == discord_id)` → scalar `int`.
- `export_all`: add
  `achievements: dict[str, list[str]] = {}`; for each row
  `achievements.setdefault(str(r.discord_id), []).append(r.achievement_id)`;
  then `{did: sorted(ids) for did, ids in achievements.items()}`; include under
  key `"achievements"`.
- `import_all`: `for did, ids in snapshot.get("achievements", {}).items(): for aid in ids: insert(sc.achievements).values(discord_id=int(did), achievement_id=aid)`.
- `clear`: add `sc.achievements` to the delete-loop tuple.

### Cross-backend equality (the export/import contract)
Both backends emit `"achievements"` as `{str(discord_id): sorted(list_of_ids)}`.
Dict equality is order-independent; lists are sorted → deterministic. Therefore
JSON-export == SQL-export for the same data, and `export → import → export`
round-trips to an identical dict. `json.dumps` succeeds (no sets, no non-string
keys). This satisfies all four export/import contract tests on every backend.

## `newly_unlocked` + `check_achievements` algorithms

`newly_unlocked(defs_for_metric, value, already)`:
```
return {a.id for a in defs_for_metric if value >= a.threshold and a.id not in already}
```
Pure set comprehension. Crossing multiple tiers at once yields all of them
(streak value 30 → {streak_7, streak_14, streak_30}); already-unlocked excluded;
below the lowest threshold → empty set.

`check_achievements(repo, discord_id, metric)`:
```
value   = await user_metric_value(repo, discord_id, metric)
defs    = [a for a in ACHIEVEMENTS if a.metric == metric]
already = await repo.get_user_achievements(discord_id)
new_ids = newly_unlocked(defs, value, already)
unlocked = []
for a in sorted((d for d in defs if d.id in new_ids), key=lambda d: d.threshold):
    if await repo.unlock_achievement(discord_id, a.id):
        unlocked.append(a)
return unlocked
```
Returns `Achievement` objects (ascending threshold order for determinism).
Idempotent: on a second call `already` contains them → `new_ids` empty → `[]`.
Persists via `unlock_achievement`; the `if` guards against a race double-insert.

## `/erfolge` rendering

`register_achievement_commands(bot, repo, settings)`:
- Guard: `if bot.get_command("erfolge") is not None: return` (idempotent).
- `bot.add_command(commands.Command(_erfolge, name="erfolge"))`.

`_erfolge(ctx)` builds and sends (via `ctx.send(embed=embed)`):
- `owned = await repo.get_user_achievements(ctx.author.id)`
- `count = len(owned)`
- `discord.Embed(title=f"🏆 Achievements - {ctx.author.display_name}", …)`;
  `embed.description = f"**{count}/{TOTAL_ACHIEVEMENTS}** Achievements freigeschaltet"`
  → guarantees the required `"<count>/59"` substring (test asserts `"2/59"`).
- Non-secret categories (`gate`, `voice`, `streak`, `night`): one field each.
  For a category, take its defs sorted by threshold; unlocked = those in `owned`;
  `next_thr` = threshold of the first def NOT in `owned` (or "—" / "Alle" if all
  unlocked). Field value shows `{len(unlocked)}/{len(defs)}` and the next
  threshold + its title.
- Secret categories (`message`, `reaction`): a single "🔒 Secret" field showing
  `{secret_unlocked}/{secret_total}` (count only — NO thresholds/titles), where
  `secret_total = sum(1 for a in ACHIEVEMENTS if a.secret)` (== 8) and
  `secret_unlocked = len(owned & {a.id for a in ACHIEVEMENTS if a.secret})`.
- No metric reads needed — everything is derived from `owned` + `ACHIEVEMENTS`.

The test flattens embed title/description/fields, so an embed (or plain text)
both work; the load-bearing requirement is the literal `"2/59"` in the output.

## Data flow

### `!erfolge` (user with 2 unlocks)
`ctx` → `_erfolge` → `repo.get_user_achievements(7)` = `{msg_1000, voice_3600}`
→ `count=2` → embed description `**2/59** …` → per-category fields from
`ACHIEVEMENTS` ∩ owned → `ctx.send(embed=…)`.

### Detection on a chat message (wired in `activity.py::record_message_activity`)
Existing body increments `messages`, updates streak, maybe night. AFTER that,
append (side-effect-free — return values ignored):
```
await check_achievements(repo, member_id, "messages")
await check_achievements(repo, member_id, "streak")
if is_night(now):
    await check_achievements(repo, member_id, "night")
```
Each reads the just-updated value, computes newly-unlocked vs stored, and records
via `unlock_achievement`. No Discord I/O. `/erfolge` then reflects reality.

### Detection on voice credit (`activity.py::handle_voice_state_update`)
Track a local `credited` flag set True on each `secs > 0` branch. After the
`async with bot.voice_lock` block:
```
if credited:
    await check_achievements(repo, member.id, "voice_seconds")
```
Join-only events credit nothing → no check (keeps the hot path cheap).

### Detection on reaction (`activity.py::handle_activity_reaction`)
After `await repo.add_activity(payload.user_id, "reactions", 1)`:
```
await check_achievements(repo, payload.user_id, "reactions")
```

### Detection on gate entry (`bot.py::handle_gate_input_message`)
Inside the existing `if inserted:` branch, after `update_gate_stats_embed`:
```
await check_achievements(repo, message.author.id, f"gate_{gate_type}")
await check_achievements(repo, message.author.id, "gate_total")
await check_achievements(repo, message.author.id, "gate_cost_total")
```
`user_gate_counts`/`user_gate_cost_total` are per-user (controller decision #1);
`gate_d` naturally resolves to 0 until the delta port (decision #2).

## Dependencies

- New packages: **none**. Uses stdlib `dataclasses`, existing `discord`,
  `discord.ext.commands`, SQLAlchemy (already a dependency).
- Internal modules `achievements.py` depends on: `n3x_bot.config.Settings`,
  `n3x_bot.storage.base.StatsRepository` (interface only; concrete repos injected).
- `activity.py` gains a dependency on `achievements.py` (one-directional, no cycle).
- `bot.py` gains a dependency on `achievements.py` (import + call).

## Build sequence (for the Coder)

1. `config.py`: add the three fields. `.env.example`: add the Achievements block.
   → config tests (`test_milestone_channel_id_*`, `test_overview_channel_id_*`,
   `test_voice_achievement_roles_*`) go green.
2. `achievements.py`: `Achievement` dataclass + `ACHIEVEMENTS` (all 59 per the
   plan) + `TOTAL_ACHIEVEMENTS = 59`. → all definition/metric-name/secret tests green.
3. `achievements.py`: `newly_unlocked` (pure). → four `newly_unlocked` tests green.
4. `schema.py`: add `achievements` table. `base.py`: add the six achievement
   abstract methods + `user_gate_counts` + `user_gate_cost_total`.
5. `json_repo.py`: `_empty()` key + the eight methods + export/import additions.
   → JSON slice of repository + export/import contract tests green; enables the
   `user_metric_value`/`check_achievements` JSON tests.
6. `sql_repo.py`: the eight methods + export/import/clear additions.
   → sqlite (+ postgres if DSN) contract tests green.
7. `achievements.py`: `user_metric_value` (mapping table) + `check_achievements`.
   → metric-resolution + detect/record tests green.
8. `achievements.py`: `register_achievement_commands` + `_erfolge`.
   → `register`/idempotent/`erfolge` tests green.
9. `bot.py`: import + `register_achievement_commands` call in `build_bot`;
   gate detection in `handle_gate_input_message`.
10. `activity.py`: detection wiring in the three helpers.
11. `migrate.py`: add `"achievements"` to `_DATA_TABLES`.
12. Full run: `pytest` from `n3x-bot/` (json + sqlite; postgres via `TEST_POSTGRES_URL`).

## Risks and open questions

- **No dedicated contract test for `user_gate_counts`/`user_gate_cost_total`.**
  The controller asked for one (decision #1) but the RED handoff only exercises
  these through `user_metric_value` against the JSON backend. They MUST still be
  implemented on `base` + json + sql because `check_achievements` runs against
  sqlite/postgres in production. Flagging that sqlite/postgres paths for these two
  methods are covered only indirectly (via the wired gate flow, if a gate test
  ever runs on SQL) — recommend the coder or a follow-up TDD pass add explicit
  SQL contract coverage. Not a blocker for green.
- **Cross-backend set serialization.** Achievements are a per-user set; both
  backends serialize to `{str(discord_id): sorted(list)}`. If either backend
  emits sets, unsorted lists, or int keys, `json.dumps` or `==` breaks. The
  sorted-list + str-key contract above is the single source of correctness —
  implement both backends identically.
- **Detection wiring on hot paths.** Each wired call adds a few async reads +
  possible writes per message/voice-credit/reaction/gate. For Pass A this is
  acceptable; the returned list is ignored (no Discord I/O). The voice path is
  gated on `credited` to avoid a read on every join event. If volume becomes a
  concern, a later pass can batch/debounce — out of scope here.
- **Existing tests won't break.** Verified: `test_activity.py` and
  `test_bot_wiring.py` build repos via a real `JsonRepository`, so the new repo
  methods exist. `check_achievements` only writes the `achievements` table, never
  the activity/streak/night/voice/gate values those tests assert. In the mock-heavy
  `on_message` tests, activity recording is already skipped because a `MagicMock`
  author's `.bot` attribute is truthy, so the added `check_achievements` calls in
  the same guarded block are skipped too.
- **`gate_d` inert by design** (decision #2). The 32 gate defs include all `d_*`
  tiers, but `user_gate_counts` returns no `d` key until the delta-gate port adds
  `d` entries; `gate_total`/`gate_cost_total` sum over whatever exists. Do NOT
  special-case `d`.
- **59-count exactness.** `TOTAL_ACHIEVEMENTS` is a hard literal `59` AND
  `len(ACHIEVEMENTS)` must equal it (two separate tests). If the coder omits a row
  or adds `EVENT_MILESTONES`, both drift. Keep the definition tables exactly as
  listed above.
- **`achievements` in `migrate._DATA_TABLES`.** Without it, a destination holding
  only achievements is treated as empty and `import_all` could collide on PKs. No
  RED test covers this directly, but it mirrors the existing activity/streak/night
  entries and is required for migration fidelity.
