# Architecture: Runtime Config Phase 1 (.env base + per-key DB overrides)

Feature: move overridable bot config off the AMP GUI onto a `.env` base
(`Settings`) plus per-key DB overrides in a new `runtime_config` table, resolved
at read time through a new `RuntimeConfig` resolver. Phase 1 delivers: storage
surface, resolver, `build_bot` attachment + `on_ready` refresh, AND the
read-site refactor that actually routes overridable reads through the resolver.

All paths below are under `/home/isikerkan/n3x/n3x-bot/`.

## Tests this design satisfies

Storage contract — `tests/storage/test_runtime_config_contract.py` (parametrized json/sqlite/postgres via the shared `repo`/`make_repo` fixtures):
- `test_get_runtime_config_unknown_key_returns_none`
- `test_set_runtime_config_roundtrips`
- `test_get_runtime_config_returns_str`
- `test_set_runtime_config_upserts_same_key`
- `test_multiple_keys_are_independent`
- `test_set_one_key_leaves_other_keys_unset`
- `test_delete_runtime_config_returns_true_when_present`
- `test_delete_runtime_config_removes_the_key`
- `test_delete_runtime_config_returns_false_when_absent`
- `test_all_runtime_config_empty_by_default`
- `test_all_runtime_config_returns_full_map`
- `test_content_override_string_survives_verbatim`
- `test_export_all_includes_runtime_config_and_is_json_serializable`
- `test_round_trip_preserves_runtime_config`
- `test_snapshot_is_stable_after_runtime_config_round_trip`
- `test_clear_wipes_runtime_config`
- `test_migrate_data_tables_includes_runtime_config`

Resolver + wiring — `tests/test_runtime_config.py`:
- `test_int_fields_pass_through_to_settings_when_no_overrides`
- `test_int_override_wins_and_is_int_coerced`
- `test_unset_int_fields_still_equal_settings_when_another_is_overridden`
- `test_gate_rewards_map_no_override_equals_settings` / `..._override_is_parsed`
- `test_voice_role_map_no_override_equals_settings` / `..._override_is_parsed`
- `test_allowed_maps_list_no_override_equals_settings` / `..._override_is_parsed`
- `test_reminder_hm_no_override_equals_settings` / `..._override_is_parsed`
- `test_non_overridable_field_passes_through_to_settings`
- `test_override_for_non_overridable_key_is_ignored`
- `test_overridable_keys_membership_guard`
- `test_refresh_reloads_overrides_from_repo`
- `test_load_builds_resolver_with_overrides_from_repo`
- `test_load_with_no_overrides_is_behavior_preserving`
- `test_build_bot_attaches_runtime_config`
- `test_build_bot_runtime_config_is_behavior_preserving_without_overrides`

Plus: every existing suite MUST stay green — the read-site refactor is
behavior-preserving (no DB overrides ⇒ resolver == Settings).

## Files to create

- `n3x_bot/runtime_config.py` — the resolver.
  - `OVERRIDABLE_KEYS: frozenset[str]` — the 16 override keys:
    12 int channel/role field names (`welcome_channel_id`, `reminder_channel_id`,
    `gate_input_channel_id`, `gate_stats_channel_id`, `milestone_channel_id`,
    `overview_channel_id`, `kodex_check_channel_id`, `timer_overview_channel_id`,
    `timer_overview_message_id`, `target_role_id`, `gate_delete_role_id`,
    `base_timer_role_id`) + 4 RAW derived-source field names (`gate_rewards`,
    `voice_achievement_roles`, `allowed_maps`, `reminder_time`).
    EXCLUDES `admin_role_id`, `discord_token`, `storage_backend`, `database_url`,
    `data_file`, `command_prefix`, `prefix_str`, `timezone`, `migration_dir`,
    `julez_id`.
  - `class RuntimeConfig`:
    - `__init__(self, settings: Settings, overrides: dict[str, str] | None = None)`
      — stores `self._settings = settings` and
      `self._overrides = {k: v for k, v in (overrides or {}).items() if k in OVERRIDABLE_KEYS}`
      (filtering to OVERRIDABLE_KEYS is the structural lockout guard: a stray
      non-overridable key never enters the cache).
    - `async def refresh(self, repo) -> None` — `raw = await repo.all_runtime_config();
      self._overrides = {k: v for k, v in raw.items() if k in OVERRIDABLE_KEYS}`.
    - `@classmethod async def load(cls, repo, settings) -> "RuntimeConfig"` —
      `rc = cls(settings); await rc.refresh(repo); return rc`.
    - private `_int(self, key: str) -> int` —
      `int(self._overrides[key]) if key in self._overrides else getattr(self._settings, key)`.
    - 12 int `@property` one-liners, each `return self._int("<field>")`:
      welcome_channel_id, reminder_channel_id, gate_input_channel_id,
      gate_stats_channel_id, milestone_channel_id, overview_channel_id,
      kodex_check_channel_id, timer_overview_channel_id,
      timer_overview_message_id, target_role_id, gate_delete_role_id,
      base_timer_role_id.
    - derived getters (override string parsed via the shared parsers from
      `config.py`, else delegate to Settings):
      - `def gate_rewards_map(self) -> dict[str, int]` — key `gate_rewards`
      - `def voice_role_map(self) -> dict[str, int]` — key `voice_achievement_roles`
      - `@property def allowed_maps_list(self) -> list[str]` — key `allowed_maps`
      - `def reminder_hm(self) -> tuple[int, int]` — key `reminder_time`
      Pattern: `v = self._overrides.get(<key>); return parse_x(v) if v is not None
      else self._settings.<derived>`.
    - non-overridable pass-through `@property` (always Settings, never consult
      cache — this is why an override for them is ignored):
      `admin_role_id`, `command_prefix`, `prefix_str`, `timezone`, `julez_id`,
      `discord_token`, `storage_backend`.

## Files to modify

### Storage layer (mirrors the `channel_messages` keyed store exactly)

- `n3x_bot/storage/schema.py` — add after `channel_messages` (line 126):
  ```
  runtime_config = Table(
      "runtime_config", metadata,
      Column("key", String(50), primary_key=True),
      Column("value", Text, nullable=True),
  )
  ```
  `metadata.create_all` in `SqlRepository.connect` (sql_repo.py:42) auto-creates
  it; no migration script needed.

- `n3x_bot/storage/base.py` — add 4 abstractmethods to `StatsRepository`
  (near the `channel_messages` block, ~line 100):
  - `async def set_runtime_config(self, key: str, value: str) -> None` (upsert)
  - `async def get_runtime_config(self, key: str) -> str | None`
  - `async def delete_runtime_config(self, key: str) -> bool`
  - `async def all_runtime_config(self) -> dict[str, str]`

- `n3x_bot/storage/json_repo.py`:
  - `_empty()` (line 42): add `"runtime_config": {}` to the dict. `connect()`'s
    `setdefault` loop backfills the key on pre-existing json files.
  - New methods (mirror `set/get_channel_message`, ~after line 271):
    - `set_runtime_config`: `self._db["runtime_config"][key] = value; self._flush()`
    - `get_runtime_config`: `return self._db["runtime_config"].get(key)`
    - `delete_runtime_config`: `existed = key in self._db["runtime_config"];
      self._db["runtime_config"].pop(key, None); self._flush(); return existed`
    - `all_runtime_config`: `return dict(self._db["runtime_config"])`
  - `export_all` (line 495 dict): add
    `"runtime_config": copy.deepcopy(self._db["runtime_config"]),`
  - `import_all` (~line 539): add
    `self._db["runtime_config"] = copy.deepcopy(snapshot.get("runtime_config", {}))`
  - `clear` uses `_empty()`, so it is wiped automatically — no edit.

- `n3x_bot/storage/sql_repo.py`:
  - New methods after `get_channel_message` (~line 326), mirroring
    `set/get_channel_message` and `remove_base_timer`:
    - `set_runtime_config`: `select` exists on `runtime_config.c.key`; `insert`
      when None else `update ... .values(value=value)`.
    - `get_runtime_config`: `select(sc.runtime_config.c.value).where(key==key)`;
      `return r.value if r else None` (Text column ⇒ already `str`).
    - `delete_runtime_config`: `select` exists → `delete` → return the bool.
    - `all_runtime_config`: `{r.key: r.value for r in await conn.execute(select(sc.runtime_config))}`.
  - `export_all` (~line 752, beside `channel_messages`): add
    `runtime_config = {r.key: r.value for r in await conn.execute(select(sc.runtime_config))}`
    and `"runtime_config": runtime_config,` in the returned dict.
  - `import_all` (~line 843, after `channel_messages` loop):
    `for key, value in snapshot.get("runtime_config", {}).items():
        await conn.execute(insert(sc.runtime_config).values(key=key, value=value))`
  - `clear` (line 860 delete tuple): append `sc.runtime_config`.

- `n3x_bot/migrate.py` — add `"runtime_config"` to `_DATA_TABLES` (line 25-31).

### Config parser reuse (recommended, pinned)

- `n3x_bot/config.py` — extract the 4 parsers into module-level pure functions so
  `Settings` and `RuntimeConfig` share ONE implementation (no duplicated parsing):
  - `def parse_gate_rewards(raw: str) -> dict[str, int]` (body of current
    `gate_rewards_map`, lines 90-95)
  - `def parse_voice_roles(raw: str) -> dict[str, int]` (body of `voice_role_map`, 102-110)
  - `def parse_allowed_maps(raw: str) -> list[str]` (body of `allowed_maps_list`, 99)
  - `def parse_reminder_hm(raw: str) -> tuple[int, int]` (body of `reminder_hm`, 86-87)
  Then `Settings.gate_rewards_map` → `return parse_gate_rewards(self.gate_rewards)`,
  etc. Return types/signatures of the `Settings` methods are UNCHANGED, so
  `tests/test_config.py` and `tests/test_voice_roles.py` stay green.

### build_bot attachment + on_ready refresh

- `n3x_bot/bot.py` `build_bot` (line 85-117): attach
  `bot.runtime_config = RuntimeConfig(settings)` (empty overrides) right after
  `bot.n3x_repo = repo` (line 94) and BEFORE `_wire_events(bot, settings, repo)`
  (line 108) — `_wire_events` reads `bot.runtime_config` at wiring time (reminder
  loop). Import `from n3x_bot.runtime_config import RuntimeConfig`.
- `on_ready` (line 520): add best-effort refresh at the top, before the first
  overridable read (gate_stats at line 545):
  `try: await bot.runtime_config.refresh(repo) \n except Exception: log...` — a DB
  hiccup must not block startup. Phase 2 config commands will call `refresh` after
  each `set`.

### Read-site refactor (routes OVERRIDABLE reads through the resolver)

Routing rule (pinned): overridable reads → resolver; non-overridable reads stay
on `settings`. Two mechanisms, chosen per read-site by whether a REAL `bot` is in
scope there (some unit tests pass `bot = MagicMock()` or omit `bot`, so those
helpers must NOT read `bot.runtime_config`):

- Strategy B (read `bot.runtime_config.X` inline): used where the read-site has a
  guaranteed-real `bot` — verified via its tests using `build_bot(...)`.
- Strategy A (caller threads `bot.runtime_config` in place of the `settings` arg;
  helper body + signature UNCHANGED): used for helpers unit-tested with a
  `MagicMock` bot (`apply_voice_roles`, `update_timer_overview`) or with no `bot`
  param (`enforce_nick`, `has_base_timer_role`, `start_base_timer`). Since
  `RuntimeConfig` exposes every field name (overridable resolved + non-overridable
  pass-through), it is a drop-in for the `settings` parameter. Unit tests that pass
  a real `Settings` keep working unchanged.

Exact edits (file:line — old → new):

`n3x_bot/bot.py` (Strategy B; `bot` always real — integration tests use `build_bot`):
- 140 `_send_or_update`: `settings.reminder_channel_id` → `bot.runtime_config.reminder_channel_id`
- 166 `_send_ephemeral`: `settings.reminder_channel_id` → `bot.runtime_config.reminder_channel_id`
- 275 `update_gate_stats_embed`: `not settings.gate_stats_channel_id` → `not bot.runtime_config.gate_stats_channel_id`
- 277 `settings.gate_stats_channel_id` → `bot.runtime_config.gate_stats_channel_id`
- 281 `settings.gate_rewards_map()` → `bot.runtime_config.gate_rewards_map()`
- 343 `_handle_gate_del`: `settings.gate_delete_role_id` → `bot.runtime_config.gate_delete_role_id`
- 475 `_announce_records`: `not settings.milestone_channel_id` → `not bot.runtime_config.milestone_channel_id`
- 477 `settings.milestone_channel_id` → `bot.runtime_config.milestone_channel_id`
- 502 `_wire_events`: `settings.reminder_hm()` → `bot.runtime_config.reminder_hm()` (see RISK — cosmetic in P1)
- 507 `event_reminder_task`: `settings.reminder_channel_id` → `bot.runtime_config.reminder_channel_id`
- 545 `on_ready`: `if settings.gate_stats_channel_id:` → `if bot.runtime_config.gate_stats_channel_id:`
- 598 `on_message`: both `settings.gate_input_channel_id` → `bot.runtime_config.gate_input_channel_id`
- 600 `on_message`: `settings.command_prefix` — NON-overridable, STAYS.
- 531 `on_ready`: `enforce_nick(m, settings)` → `enforce_nick(m, bot.runtime_config)` (Strategy A)
- 637 `on_member_update`: `enforce_nick(after, settings)` → `enforce_nick(after, bot.runtime_config)` (Strategy A)
- 652 `on_member_join`: `enforce_nick(member, settings)` → `enforce_nick(member, bot.runtime_config)` (Strategy A)
- Note: line 546 `update_gate_stats_embed(bot, repo, settings)` call is UNCHANGED
  (the helper now reads `bot.runtime_config` internally). Its `settings` param
  becomes unused for config but keep it — tests call it with `settings`.

`n3x_bot/activity.py`:
- 74 `apply_voice_roles` body: `settings.voice_role_map()` — STAYS (Strategy A helper).
- 170 caller: `apply_voice_roles(bot, settings, member, newly)` → `apply_voice_roles(bot, bot.runtime_config, member, newly)`
- 215 caller: `apply_voice_roles(bot, settings, member, newly)` → `apply_voice_roles(bot, bot.runtime_config, member, newly)`
- 223 `handle_activity_reaction`: `settings.gate_input_channel_id` → `bot.runtime_config.gate_input_channel_id` (Strategy B; tests use `build_bot`)
- 224 `settings.gate_stats_channel_id` → `bot.runtime_config.gate_stats_channel_id`
- 225 `settings.overview_channel_id` → `bot.runtime_config.overview_channel_id`

`n3x_bot/cards.py` (Strategy B; `announce_achievements` tested with `build_bot`):
- 175 `settings.milestone_channel_id == 0` → `bot.runtime_config.milestone_channel_id == 0`
- 181 `settings.milestone_channel_id` → `bot.runtime_config.milestone_channel_id`

`n3x_bot/achievements.py` (Strategy B; both tested with `build_bot`):
- 191 `post_overview`: `settings.overview_channel_id == 0` → `bot.runtime_config.overview_channel_id == 0`
- 198 `settings.overview_channel_id` → `bot.runtime_config.overview_channel_id`
- 227 `handle_overview_reaction`: `settings.overview_channel_id` → `bot.runtime_config.overview_channel_id`
- 241 `settings.overview_channel_id` → `bot.runtime_config.overview_channel_id`

`n3x_bot/kodex.py` (Strategy B; `_kodex_check_cmd` closure, real `bot`):
- 85 `settings.kodex_check_channel_id` → `bot.runtime_config.kodex_check_channel_id`

`n3x_bot/timers.py`:
- 36-37 `has_base_timer_role` body: `settings.base_timer_role_id` — STAYS (Strategy A helper; MagicMock in test / no bot).
- 44 `start_base_timer` body: `settings.allowed_maps_list` — STAYS (Strategy A helper).
- 56 `update_timer_overview` body: `settings.timer_overview_channel_id` — STAYS (Strategy A; MagicMock bot in test_timers).
- 60 `update_timer_overview` body: `settings.timer_overview_message_id` — STAYS.
- 70 `_base_cmd`: `has_base_timer_role(ctx.author, settings)` → `has_base_timer_role(ctx.author, bot.runtime_config)`
- 73/91 `settings.timezone` — NON-overridable, STAYS.
- 75 `_base_cmd`: `start_base_timer(repo, settings, map_name, zeit, now)` → `start_base_timer(repo, bot.runtime_config, map_name, zeit, now)`
- 79 `_base_cmd`: `settings.allowed_maps_list` → `bot.runtime_config.allowed_maps_list` (Strategy B — closure has real `bot`)
- 81 `_base_cmd`: `update_timer_overview(bot, repo, settings, now)` → `update_timer_overview(bot, repo, bot.runtime_config, now)`
- 88 `_basestop_cmd`: `has_base_timer_role(ctx.author, settings)` → `has_base_timer_role(ctx.author, bot.runtime_config)`
- 93 `_basestop_cmd`: `update_timer_overview(bot, repo, settings, now)` → `update_timer_overview(bot, repo, bot.runtime_config, now)`
- 110 `_timer_overview_loop`: `update_timer_overview(bot, repo, settings, ...)` → `update_timer_overview(bot, repo, bot.runtime_config, ...)`

`n3x_bot/nicknames.py`:
- 31 `enforce_nick` body: `settings.target_role_id` — STAYS (Strategy A helper; `settings` is bound to the resolver at runtime).
- 32 `settings.prefix_str` — STAYS (non-overridable pass-through).

`n3x_bot/welcome.py` (Strategy B; `send_welcome_card` tested with `build_bot`):
- 83 `settings.welcome_channel_id` → `bot.runtime_config.welcome_channel_id`
- 87 `settings.prefix_str` — STAYS (non-overridable).
- `_sync_welcome_cmd` closure call `send_welcome_card(bot, settings, member)` — UNCHANGED.

Non-overridable reads confirmed left on `settings` everywhere: `admin_role_id`
(via `is_admin`, admin.py:22), `command_prefix` (bot.py:600), `prefix_str`
(welcome/nicknames), `timezone` (timers `now_local`/`ZoneInfo`), `julez_id`
(bot.py:234/241), `discord_token`/`storage_backend`/`database_url`/`data_file`/
`migration_dir` (bootstrap only).

## Data flow

Representative: an operator has set a DB override `gate_stats_channel_id → "999"`;
a member posts an `a 50000` gate entry after a restart.

1. `build_bot(settings, repo)` attaches `bot.runtime_config = RuntimeConfig(settings)`
   with empty overrides (mirrors Settings exactly at this point).
2. `on_ready` runs `await bot.runtime_config.refresh(repo)` →
   `repo.all_runtime_config()` returns `{"gate_stats_channel_id": "999"}` →
   filtered to OVERRIDABLE_KEYS → cached in `rc._overrides`.
3. Member posts in the gate-input channel. `on_message` checks
   `bot.runtime_config.gate_input_channel_id` (still Settings value; not
   overridden) → matches → `handle_gate_input_message(bot, repo, settings, message)`.
4. Entry recorded; handler calls `update_gate_stats_embed(bot, repo, settings)`.
   Inside, `bot.runtime_config.gate_stats_channel_id` → `int("999") == 999`
   (override wins) and `bot.runtime_config.gate_rewards_map()` (no override →
   `settings.gate_rewards_map()`). The live embed posts/edits in channel 999.
5. With NO override present (every existing test), `rc.X == settings.X` for all
   fields ⇒ byte-identical behavior.

## Dependencies

- New packages: NONE. Uses stdlib + existing SQLAlchemy Core / pydantic-settings.
- Internal: `runtime_config.py` imports `Settings` and the 4 `parse_*` helpers
  from `config.py` (one-way; `config.py` does not import `runtime_config`, so no
  cycle). `bot.py` imports `RuntimeConfig`. Storage impls import `schema as sc`.

## Build sequence (for the Coder)

1. `schema.py`: add the `runtime_config` Table.
2. `json_repo.py` + `sql_repo.py`: implement the 4 methods + export/import/clear
   in BOTH, THEN add the 4 abstractmethods to `base.py`. (Do impls before/with the
   abstract decls — an abstractmethod with no override makes the repo classes
   uninstantiable and would break the entire suite.) → greens all of
   `test_runtime_config_contract.py` (json, sqlite, and postgres when DSN set).
3. `migrate.py`: add `"runtime_config"` to `_DATA_TABLES`. → greens
   `test_migrate_data_tables_includes_runtime_config`.
4. `config.py`: extract the 4 module-level `parse_*` functions; delegate the
   `Settings` methods to them. → `test_config.py` stays green.
5. `runtime_config.py`: `OVERRIDABLE_KEYS` + `RuntimeConfig`. → greens all resolver
   tests in `test_runtime_config.py` except the two `build_bot` wiring tests.
6. `bot.py` `build_bot`: attach `bot.runtime_config`; `on_ready` refresh. → greens
   `test_build_bot_attaches_runtime_config` and
   `test_build_bot_runtime_config_is_behavior_preserving_without_overrides`.
7. Read-site refactor across bot.py / activity.py / cards.py / achievements.py /
   kodex.py / timers.py / nicknames.py / welcome.py per the table above. No new
   test; the acceptance bar is the FULL existing suite staying green
   (behavior-preserving). Run the focused suites for each touched module
   (test_activity, test_voice_roles, test_timers, test_overview, test_kodex,
   test_welcome, test_achievement_announce, test_gates, test_ezk_gates,
   test_delta_gate, test_nicknames, test_gate_embed_persistence, test_smoke).

## Risks and open questions

- READ-SITE ROUTING IS UNVERIFIED BY TESTS. The TDD suite pins the resolver,
  storage, and `build_bot` attachment, but NO test exercises an override actually
  changing a channel/role at a read-site (every test runs with zero overrides, so
  `rc.X == settings.X` and the routing is invisible). The refactor is therefore
  "correct by construction / behavior-preserving" but unproven end-to-end.
  Recommend TDD add one integration test (e.g. `set_runtime_config(
  "gate_stats_channel_id", "<id>")` → `refresh` → assert `update_gate_stats_embed`
  posts to that id) to lock the feature in.

- `reminder_time` override is effectively COSMETIC in Phase 1. The reminder loop
  time is fixed when the `@tasks.loop(time=...)` decorator is evaluated during
  `_wire_events` (bot.py:502-504), which runs inside `build_bot` BEFORE
  `on_ready`'s `refresh`. So even routing line 502 to `bot.runtime_config.reminder_hm()`
  reads the pre-refresh (empty-override) value. A `reminder_time` override cannot
  take effect until Phase 2 reschedules the loop after `refresh`. Flagging rather
  than silently designing a reschedule mechanism the tests don't ask for.

- Strategy B fragility. `apply_voice_roles` and `update_timer_overview` are
  unit-tested with `bot = MagicMock()`; reading `bot.runtime_config` inside them
  would return a `MagicMock` and break those tests — hence they use Strategy A
  (caller threads the resolver). If a future test switches a Strategy-B helper's
  fixture to `MagicMock`, that helper would break. Documented per read-site above.

- `String(50)` for `runtime_config.key`. Longest current key is
  `voice_achievement_roles` (23 chars); 50 matches the `stats.key`/`channel_messages.key`
  convention. Bump if longer keys are ever introduced.

- Lockout guard is enforced in TWO places for defense-in-depth: `__init__` and
  `refresh` filter the cache to `OVERRIDABLE_KEYS`, AND the non-overridable
  pass-through properties never consult the cache. `test_override_for_non_overridable_key_is_ignored`
  passes on the pass-through alone; the filter is belt-and-suspenders against a
  stray DB row.
