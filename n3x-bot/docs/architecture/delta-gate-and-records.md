# Architecture: Delta ('d') Gate + Gate Min/Max Records

Turns GREEN: `tests/test_delta_gate.py`, `tests/storage/test_gate_records_contract.py`,
and the edited `tests/test_gates.py` / `tests/test_config.py`. Un-inerts the 8 `d_*`
achievements (erfolge cap 51/59 → 59/59).

## Tests this design satisfies

Storage contract (`tests/storage/test_gate_records_contract.py`, parametrized json/sqlite/postgres):
- `test_gate_types_includes_delta` — `GATE_TYPES` contains `"d"`.
- `test_add_delta_entry_with_laser_true_is_counted` / `..._false_is_counted` — `add_gate_entry("d", …, laser_dropped=…)` stores + counts.
- `test_abc_entries_store_laser_dropped_as_none` — a/b/c rows export `laser_dropped is None`.
- `test_delta_stats_laser_rate_two_of_three` — `delta_stats()` → count/avg/laser_rate.
- `test_delta_stats_laser_rate_zero_when_no_entries` — empty → count 0, laser_rate 0.
- `test_gate_record_returns_min_and_max_with_holders` — min/max cost + holder user ids.
- `test_gate_record_none_when_no_entries` — None when empty.
- `test_gate_record_recomputes_after_deleting_record_holder` — on-demand (B7 fix).
- `test_gate_record_covers_delta_gate` — works for `"d"`.
- `test_user_gate_counts_includes_delta` / `test_user_gate_cost_total_includes_delta` — d rolls into user aggregates (already true; guarded).
- `test_round_trip_preserves_laser_dropped` — export/import preserves True/False/None cross-repo.

Delta gate (`tests/test_delta_gate.py`, JsonRepository integration):
- `test_parse_gate_message_recognizes_delta` / `..._strips_dotted_thousands` / `..._uppercase_normalizes`.
- `test_gate_rewards_default_includes_delta` — `gate_rewards_map()["d"] == 75361`.
- `test_delta_input_registers_pending_and_reacts_check_and_cross` — d input sets `_pending_delta`, reacts ✅+❎, does NOT store.
- `test_delta_confirmation_check_stores_with_laser_true` / `..._cross_stores_with_laser_false`.
- `test_delta_confirmation_on_non_pending_message_does_nothing`.
- `test_delta_confirmation_ignores_non_confirm_emoji`.
- `test_delta_confirmation_by_non_author_is_ignored`.
- `test_changed_records_first_entry_sets_both` / `_new_min_only` / `_new_max_only` / `_no_change_is_empty`.
- `test_five_delta_entries_unlock_d_5_achievement` / `test_gate_total_metric_includes_delta_entries`.

Regression (must stay green): `tests/test_gates.py` (abc-only embed), `tests/test_bot_wiring.py`
(a/b/c immediate store + embed refresh), `tests/storage/test_export_import_contract.py` +
`tests/test_migrate.py` (snapshot equality).

## Files to modify

### 1. `n3x_bot/storage/schema.py`
Add one column to the `gate_entries` table (after `username`, before `created_at`):
`Column("laser_dropped", Boolean, nullable=True)`. `Boolean` is already imported.
Meaningful only for `"d"`; a/b/c rows store NULL.

### 2. `n3x_bot/storage/base.py`
- Line 4: `GATE_TYPES: tuple[str, ...] = ("a", "b", "c", "d")`.
- `add_gate_entry` abstract signature → add trailing kwarg:
  `async def add_gate_entry(self, gate_type, cost, user_id, username, dedup_window_seconds=30, laser_dropped: bool | None = None) -> bool`.
  Docstring: laser_dropped meaningful only for `"d"`; a/b/c pass None.
- Add two abstract methods (place in the "gate tracker" block):
  - `async def delta_stats(self) -> dict` — `{"count": int, "avg": int, "laser_rate": float}` over `gate_type == "d"`; `laser_rate = 100 * (# laser_dropped True) / count`, `0` when count is 0.
  - `async def gate_record(self, gate_type: str) -> dict | None` — `{"min_cost", "min_user", "max_cost", "max_user"}` computed ON-DEMAND from `gate_entries`; `min_user`/`max_user` are int discord user ids; None when the gate type has no entries.

### 3. `n3x_bot/storage/json_repo.py`
- `add_gate_entry(...)`: add `laser_dropped: bool | None = None` param. Keep the existing
  (user_id, gate_type, cost) dedup unchanged. Add `"laser_dropped": laser_dropped` to the
  inserted row dict (line ~281).
- New `delta_stats(self)`:
  - `costs = [r["cost"] for r in gate_entries if r["gate_type"] == "d"]`; `count = len(costs)`.
  - `avg = round(sum(costs)/count) if count else 0`.
  - `laser = sum(1 for r in gate_entries if r["gate_type"] == "d" and r.get("laser_dropped"))`.
  - `laser_rate = 100 * laser / count if count else 0`.
  - return `{"count": count, "avg": avg, "laser_rate": laser_rate}`.
- New `gate_record(self, gate_type)`:
  - `rows = [r for r in gate_entries if r["gate_type"] == gate_type]`; if empty `return None`.
  - `min_row = min(rows, key=lambda r: r["cost"])`; `max_row = max(rows, key=lambda r: r["cost"])`
    (Python `min`/`max` keep the FIRST insertion-order row on ties — deterministic).
  - return `{"min_cost": min_row["cost"], "min_user": min_row["user_id"], "max_cost": max_row["cost"], "max_user": max_row["user_id"]}`.
- `export_all`: after building `gate_entries` (deepcopy), backfill each row so legacy rows are
  shaped: `for g in gate_entries: g.setdefault("laser_dropped", None)` (same pattern as the
  `stats.setdefault("targeted", …)` backfill already there).
- `import_all`: no change needed — `copy.deepcopy(snapshot["gate_entries"])` already carries the
  key; export backfills any absent one.

### 4. `n3x_bot/storage/sql_repo.py`
- Add `case` to the sqlalchemy import: `from sqlalchemy import func, select, insert, update, delete, text, case`.
- `add_gate_entry(...)`: add `laser_dropped: bool | None = None` param; include
  `laser_dropped=laser_dropped` in the `insert(sc.gate_entries).values(...)`. Dedup unchanged.
- New `delta_stats(self)` — single query inside a `connect()`:
  `select(func.count(sc.gate_entries.c.id), func.avg(sc.gate_entries.c.cost), func.sum(case((sc.gate_entries.c.laser_dropped == True, 1), else_=0))).where(sc.gate_entries.c.gate_type == "d")`.
  Read `count, avg, laser`; `count = count or 0`; `avg = round(avg) if avg else 0`;
  `laser_rate = 100 * (laser or 0) / count if count else 0`. (`== True` is correct SQLAlchemy
  boolean comparison; SQLite stores 0/1, Boolean type reads back bool.)
- New `gate_record(self, gate_type)` — two ordered-limit queries (mirrors v3 `ORDER BY cost … LIMIT 1`):
  - min: `select(cost, user_id).where(gate_type == :gt).order_by(cost.asc(), id.asc()).limit(1)`.
  - if min row is None → `return None`.
  - max: same with `order_by(cost.desc(), id.asc())`.
  - return `{"min_cost": int, "min_user": int, "max_cost": int, "max_user": int}` from the rows.
- `export_all`: add `"laser_dropped": (None if r.laser_dropped is None else bool(r.laser_dropped))`
  to each `gate_entries` dict (guards the `is True/is False/is None` assertions across SQLite/PG).
- `import_all`: in the `snapshot["gate_entries"]` insert loop add
  `laser_dropped=r.get("laser_dropped")`.

### 5. `n3x_bot/config.py`
Line 29: `gate_rewards: str = "a:46892,b:93820,c:139522,d:75361"`. `gate_rewards_map()` already
parses arbitrary keys, so `["d"] == 75361` follows automatically. `_handle_gate_stat`'s
validity check (`gtype not in gate_rewards_map()`) now accepts `"d"` for free.

### 6. `n3x_bot/gates.py`
- Regex: `_PATTERN = re.compile(r"^([abcd])\s+([\d.]+)$", re.IGNORECASE)`.
- `GATE_NAMES`: add `"d": "Delta Gate"`.
- New pure fn `changed_records(before: dict | None, after: dict) -> set[str]`:
  - `if before is None: return {"min", "max"}`.
  - `out = set()`; `if after["min_cost"] < before["min_cost"]: out.add("min")`;
    `if after["max_cost"] > before["max_cost"]: out.add("max")`; `return out`.
- `build_gate_content`: change the loop `for gate_type in GATE_TYPES:` → `for gate_type in ("a", "b", "c"):`
  (must NOT iterate the now-4-element `GATE_TYPES`, or `test_build_gate_content_*` and the
  bot_wiring embed tests break). Drop the now-unused `GATE_TYPES` import if nothing else uses it.
- `build_gate_embed(totals, rewards, now_str, delta: dict | None = None)`: keep positional
  backward-compat (the existing test calls it with 3 args). When `delta is not None`, append a
  separate field after setting the description:
  `embed.add_field(name="💎 Delta Gate", value=f"Runs: {delta['count']}\nAvg. Cost: {format_number(delta['avg'])}\nReward: {format_number(rewards.get('d', 0))}\nDrop Rate: {delta['laser_rate']:.1f} %", inline=True)`.

### 7. `n3x_bot/bot.py`
- `build_bot`: add `bot._pending_delta = {}` alongside the other in-memory trackers (~line 89).
- `handle_gate_input_message`: after `gate_type, cost = parsed`, branch on delta BEFORE the
  a/b/c store logic:
  ```
  if gate_type == "d":
      bot._pending_delta[message.id] = {"cost": cost, "user_id": message.author.id, "username": message.author.name}
      try: await message.add_reaction("✅"); await message.add_reaction("❎")
      except Exception: pass
      return
  ```
  Then restructure the a/b/c path to read the record before/after so records announce:
  ```
  before = await repo.gate_record(gate_type)
  inserted = await repo.add_gate_entry(gate_type, cost, message.author.id, message.author.name)
  try: await message.add_reaction("✅" if inserted else "⏳")
  except Exception: pass
  if inserted:
      after = await repo.gate_record(gate_type)
      await _announce_records(bot, settings, gate_type, changed_records(before, after), after)
      await update_gate_stats_embed(bot, repo, settings)
      newly = (check_achievements gate_<t> + gate_total + gate_cost_total)  # unchanged
      if newly: try announce_achievements(bot, settings, message.author, newly) except: pass
  ```
- New async `handle_delta_confirmation(bot, repo, settings, payload) -> None`:
  ```
  pending = bot._pending_delta.get(payload.message_id)
  if pending is None: return
  emoji = str(payload.emoji)
  if emoji not in ("✅", "❎"): return
  if payload.user_id != pending["user_id"]: return          # author-only
  laser = emoji == "✅"
  before = await repo.gate_record("d")
  inserted = await repo.add_gate_entry("d", pending["cost"], pending["user_id"], pending["username"], laser_dropped=laser)
  del bot._pending_delta[payload.message_id]                 # clear pending
  if inserted:
      after = await repo.gate_record("d")
      await _announce_records(bot, settings, "d", changed_records(before, after), after)
      await update_gate_stats_embed(bot, repo, settings)
      member = getattr(payload, "member", None)
      newly = (await check_achievements(repo, pending["user_id"], "gate_d")
               + await check_achievements(repo, pending["user_id"], "gate_total")
               + await check_achievements(repo, pending["user_id"], "gate_cost_total"))
      if newly:
          try: await announce_achievements(bot, settings, member, newly)
          except Exception: pass
  ```
  Note: no message fetch is needed — everything comes from the pending dict; the non-author /
  non-confirm / non-pending guards short-circuit before any store.
- New async helper `_announce_records(bot, settings, gate_type, changed, record) -> None`:
  ```
  if not changed or not settings.milestone_channel_id: return
  channel = bot.get_channel(settings.milestone_channel_id)
  if channel is None: return
  name = GATE_NAMES.get(gate_type, gate_type.upper())        # from n3x_bot.gates
  try:
      if "min" in changed:
          await channel.send(f"🍀 **Neuer Glückspilz!** <@{record['min_user']}> hat den neuen Tiefpreis-Rekord für das **{name}** aufgestellt: **{format_number(record['min_cost'])}**")
      if "max" in changed:
          await channel.send(f"💀 **Neuer Pechvogel!** <@{record['max_user']}> hat den neuen Höchstpreis-Rekord für das **{name}** aufgestellt: **{format_number(record['max_cost'])}**")
  except Exception: pass
  ```
  The `milestone_channel_id` guard keeps the a/b/c bot_wiring tests green
  (they set `gate_stats_channel_id` only → `milestone_channel_id == 0` → no extra `channel.send`).
- `update_gate_stats_embed`: compute `delta = await repo.delta_stats()` and pass it:
  `embed = build_gate_embed(totals, rewards, now_str, delta)`.
- Imports: extend the gates import to
  `from n3x_bot.gates import build_gate_embed, parse_gate_message, changed_records, GATE_NAMES`.
- `_wire_events` → `on_raw_reaction_add`: add a third independent best-effort block after the
  existing two:
  ```
  try: await handle_delta_confirmation(bot, repo, settings, payload)
  except Exception: pass
  ```
  (Bot's own ✅/❎ reactions fire this with `payload.user_id == bot.user.id`, which fails the
  author check and is safely ignored — no self-store, no second event handler registered.)
- `_handle_gate_stat` (optional cosmetic): update the German error string to mention `d`
  ("a, b, c oder d"). `!stat d` / `!del d N` work unchanged since `d` is now a valid reward key
  and `list_gate_costs`/`delete_gate_entry` are gate-type agnostic. `!stat d` lists delta costs
  like other gates (laser flag NOT shown — kept minimal).

### 8. `n3x_bot/achievements.py` (comment-only, no functional change)
`GATE_NAMES`, the `("a","b","c","d")` def loop, and `user_metric_value`'s `gate_d` branch
already exist — the `d_*` tiers become live the moment `"d"` rows exist. Update the stale NOTE
comment at lines 29–30 (drop "stay inert … caps at 51/59"); leave code untouched.

## Data flow

Delta happy path (`d 250.000` in the gate-input channel, laser received):
1. `on_message` → `handle_gate_input_message`. `parse_gate_message("d 250.000")` → `("d", 250000)`.
2. Delta branch: `bot._pending_delta[msg.id] = {"cost":250000,"user_id":author.id,"username":author.name}`;
   reacts ✅ then ❎; returns WITHOUT storing.
3. Author reacts ✅ → `on_raw_reaction_add` → `handle_delta_confirmation`.
4. Guards pass (pending exists, emoji ✅ ∈ confirm set, `payload.user_id == pending.user_id`).
5. `before = gate_record("d")`; `add_gate_entry("d", 250000, uid, name, laser_dropped=True)`;
   `del _pending_delta[msg.id]`.
6. `after = gate_record("d")`; `changed_records(before, after)` → announce Glückspilz/Pechvogel to
   `milestone_channel_id` (best-effort).
7. `update_gate_stats_embed` refreshes the embed incl. the Delta field from `delta_stats()`.
8. Achievement fan-out: `check_achievements` for `gate_d`, `gate_total`, `gate_cost_total` →
   `announce_achievements(bot, settings, payload.member, newly)` (best-effort).

a/b/c path is unchanged except it now also does the before/after `gate_record` + `_announce_records`.

## Dependencies
- New packages: NONE. `case` is already provided by the installed SQLAlchemy.
- Internal: `gates.changed_records`/`GATE_NAMES`/`build_gate_embed`, `format.format_number`,
  `cards.announce_achievements`, `achievements.check_achievements`, repo `gate_record`/`delta_stats`/
  `add_gate_entry`.

## Build sequence (for the Coder)
1. `schema.py` — add `laser_dropped` column (both backends read the same schema).
2. `base.py` — `GATE_TYPES += ("d",)`, `add_gate_entry` signature, `delta_stats`/`gate_record` abstract methods.
3. `json_repo.py` — `add_gate_entry` laser param + row key, `delta_stats`, `gate_record`, export backfill.
4. `sql_repo.py` — `case` import, `add_gate_entry`, `delta_stats`, `gate_record`, export/import laser.
   → run `pytest tests/storage/test_gate_records_contract.py tests/storage/test_export_import_contract.py`.
5. `config.py` — default reward. → `pytest tests/test_config.py tests/test_delta_gate.py::test_gate_rewards_default_includes_delta`.
6. `gates.py` — regex, `GATE_NAMES["d"]`, `changed_records`, abc-only `build_gate_content`, delta field.
   → `pytest tests/test_gates.py tests/test_delta_gate.py -k "parse or changed_records"`.
7. `bot.py` — `_pending_delta` init, delta branch + a/b/c record wiring in `handle_gate_input_message`,
   `handle_delta_confirmation`, `_announce_records`, `update_gate_stats_embed` delta arg,
   `on_raw_reaction_add` wiring, imports. → `pytest tests/test_delta_gate.py tests/test_bot_wiring.py`.
8. `achievements.py` — NOTE comment update.
9. Full run: `pytest` (expect gate/delta/bot_wiring/config/migrate/export-import all green; erfolge 59/59).

## Risks and open questions
- **GATE_TYPES-iterating code**: two call sites (`json_repo.gate_totals` L302, `sql_repo.gate_totals`
  L379) now also emit a `"d"` bucket — harmless (no test pins the totals key set). The ONE site that
  must change is `gates.build_gate_content` (L47) — leaving it iterating `GATE_TYPES` breaks
  `test_build_gate_content_includes_all_three_gate_types_in_order` and the "3 🟢" count. Fixed by the
  explicit `("a","b","c")` loop.
- **laser_dropped nullable serialization**: SQLite has no native bool; guard the export with
  `None if x is None else bool(x)` so `is True/is False/is None` assertions and cross-backend
  `export_all()` equality (migrate snapshot tests) hold. JSON stores the Python bool directly.
- **On-demand `gate_record` correctness/perf**: recomputed from `gate_entries` each call (the v3 B7
  fix — `!del` can never leave a stale record). O(n) scan (JSON) / two `ORDER BY … LIMIT 1` (SQL);
  fine at this data scale, no stored `gate_records` table introduced.
- **Delta dedup collision**: `add_gate_entry` dedups on (user_id, gate_type, cost) within 30s. Two
  legitimate identical-cost delta confirms by the same user inside the window → the second is
  rejected (`inserted=False`, no store/announce). Matches v3 behavior; pending is still cleared.
- **Pending delta lost on restart**: `_pending_delta` is in-memory only (like `_gate_embed_msg_id`);
  a restart between input and confirmation drops the pending entry — accepted, consistent with the
  existing ephemeral-tracker pattern.
- **Not double-registering events**: the delta confirmation is added as a third try/except block
  inside the SINGLE existing `on_raw_reaction_add`, not a new `@bot.event` — avoids clobbering the
  activity/overview handlers.
- **`announce_achievements` member source**: from `payload.member` in confirmation (None in the unit
  tests, which supply a `SimpleNamespace` payload) — the call is best-effort and `gate_total`'s
  `total_1` (threshold 1) unlocks on the first delta, so the None-member path executes and must stay
  wrapped in try/except.
- **Achievements module**: no functional edit — `gate_d`/`d_*` were pre-wired and inert only for
  lack of a data source; they light up automatically. Only the misleading NOTE comment changes.
