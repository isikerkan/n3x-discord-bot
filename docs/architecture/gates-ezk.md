# Architecture: Epsilon / Zeta / Kappa gates (e/z/k)

Feature branch: `feature/gates-ezk`. This design generalizes the Delta laser-drop
mechanic into a per-item drop map and adds three new gate types:
- **Epsilon `e`** — 1 item `lf4`, reaction-confirmed ✅/❎ (Delta-style).
- **Zeta `z`** — 1 item `havoc`, reaction-confirmed ✅/❎ (Delta-style).
- **Kappa `k`** — 2 items `hercules` + `lf4u`, confirmed via a button panel
  (`KappaConfirmView`).

The single hardest constraint: everything that Delta does today must keep working
byte-for-byte, because several existing green tests pin the legacy `laser_dropped=`
param, the exact `_pending_delta` dict shape for `d`, and the `laser_dropped` key
in `export_all`. The design keeps `laser_dropped` as a thin compat alias/column and
routes `d` confirmations through it, while `e/z/k` use the new `drops=` path.

## Tests this design satisfies

### tests/storage/test_gate_drops_contract.py (json + sqlite + postgres)
- `test_gate_types_includes_epsilon_zeta_kappa` — GATE_TYPES gains e/z/k
- `test_gate_drop_stats_empty_is_zeroed_with_no_rates` — empty → count 0, avg 0, rates {}
- `test_epsilon_drop_rate_two_of_four` — count 4, avg 43000, lf4 rate 50.0
- `test_zeta_drop_rate_one_of_three` — havoc rate 33.3
- `test_kappa_two_items_have_independent_rates` — hercules/lf4u independent 50/50
- `test_kappa_both_items_can_drop_on_same_entry` — both 100.0
- `test_abc_entries_have_no_drop_rates` — a: count 1, rates {}
- `test_delta_generalized_rates_match_laser_rate` — gate_drop_stats("d").rates["laser"] == delta_stats().laser_rate
- `test_delta_legacy_laser_dropped_param_still_reads_as_laser_rate` — laser_dropped= writes read back as drops["laser"]
- `test_gate_record_covers_epsilon/zeta/kappa`
- `test_user_gate_counts_includes_ezk`
- `test_round_trip_preserves_ezk_drops` — export/import preserves per-item drops
- `test_clear_removes_ezk_entries`

### tests/storage/test_gate_records_contract.py (LEGACY — must stay green)
- All `laser_dropped=` writes, `delta_stats()` shape, `export_all` gate rows carrying a `laser_dropped` key (True/False/None), round-trip preserving `laser_dropped`.

### tests/test_gates.py
- `_PATTERN` accepts e/z/k (case-insensitive, dotted thousands); still rejects f/g/y/m
- `GATE_NAMES["e"/"z"/"k"]` == "Epsilon Gate"/"Zeta Gate"/"Kappa Gate"
- `build_gate_embed(..., epsilon=, zeta=, kappa=)` renders `🔷 Epsilon/Zeta/Kappa Gate` fields with the right drop-rate labels and NO Reward line
- `test_build_gate_embed_delta_field_still_carries_reward` — Delta field unchanged

### tests/test_ezk_gates.py
- e/z input seeds `_pending_delta[msg.id]` with `gate_type` + reacts ✅/❎, stores nothing
- e/z confirmation stores lf4/havoc drop; non-author ignored + pending preserved
- k input sends a `discord.ui.View` (>=3 children), stores nothing
- `KappaConfirmView` defaults both drops False; author toggles + submit store both bools; non-author submit/toggle are no-ops
- `_handle_gate_stat` accepts `k` (validates against GATE_TYPES, not gate_rewards)

### tests/test_achievements.py
- TOTAL_ACHIEVEMENTS == 83; e/z/k each get 8 tiers; metrics gate_e/z/k; not secret; titles contain Epsilon/Zeta/Kappa
- `user_metric_value` reads gate_e count; gate_total / gate_cost_total include e/z/k
- `check_achievements("gate_e"/"gate_k")` unlocks e_5/k_5
- `cards._milestone_line` renders Epsilon/Zeta/Kappa lines
- `/erfolge` reports `2/83`

### tests/test_delta_gate.py (LEGACY — must stay green)
- `_pending_delta[5001] == {"cost","user_id","username"}` (NO gate_type for d)
- d confirmation via `laser_dropped=` alias; `export_all` d-rows have `laser_dropped is True/False`
- `_SuspendingDeltaRepo.add_gate_entry(..., laser_dropped=None)` (no `drops` kwarg) — the d path MUST call `add_gate_entry(..., laser_dropped=...)`, not `drops=`

## Files to create
None. All changes are edits to existing modules.

## Files to modify

### `n3x_bot/storage/base.py`
- `GATE_TYPES = ("a", "b", "c", "d", "e", "z", "k")`.
- Change abstract `add_gate_entry` signature to:
  `async def add_gate_entry(self, gate_type, cost, user_id, username, dedup_window_seconds=30, laser_dropped: bool | None = None, drops: dict[str, bool] | None = None) -> bool`
  (both `laser_dropped` and `drops` keyword — order keeps every existing `laser_dropped=` caller working; new callers use `drops=`.)
- Add abstract method:
  `async def gate_drop_stats(self, gate_type: str) -> dict` →
  `{"count": int, "avg": int, "rates": {item: float_pct}}`. Empty gate → count 0, avg 0, rates {}.
- Keep `delta_stats()` abstract as-is (docstring can note it now delegates to `gate_drop_stats("d")`).

### `n3x_bot/storage/schema.py`
- Add one column to `gate_entries`: `Column("drops", Text, nullable=True)` holding a JSON string `{item: bool}`.
- **KEEP** the existing `Column("laser_dropped", Boolean, nullable=True)`. Rationale (lowest-risk, PINNED): the `laser_dropped` column stays the legacy-compatible surface (`export_all` d-rows, `test_gate_records_contract`, `test_delta_gate`), while `drops` is the generalized surface. For every d-entry BOTH are populated; e/z/k populate `drops` only (their `laser_dropped` column is NULL). `Text` already imported.
- No Alembic migration (project uses `create_all` only). Fresh Postgres/SQLite get the column automatically. Note for the operator: an existing `gate_entries` table needs a one-time manual rebuild (handled out-of-band). Legacy rows with only `laser_dropped` set read back correctly via the fallback below.

### `n3x_bot/storage/json_repo.py`
Add a small read helper (module-level or static):
```
def _drops_of(row) -> dict:   # canonical drop-map read
    d = row.get("drops")
    if d:
        return d
    lz = row.get("laser_dropped")
    return {"laser": lz} if lz is not None else {}
```
- `add_gate_entry(..., laser_dropped=None, drops=None)`:
  - Normalize: `if drops is None and laser_dropped is not None: drops = {"laser": bool(laser_dropped)}`.
  - Derive legacy column: `laser_col = drops.get("laser") if drops else None`.
  - Store row with BOTH keys: `"drops": drops` (dict or None) and `"laser_dropped": laser_col`.
  - Dedup logic unchanged.
- Add `async def gate_drop_stats(self, gate_type)`:
  - `rows = [r for r in gate_entries if r["gate_type"] == gate_type]`
  - `count = len(rows)`; `avg = round(sum(costs)/count) if count else 0`
  - Tally: for each row, `for item, val in _drops_of(row).items():` record `observed.add(item)` and `trues[item] += 1 if val`.
  - `rates = {item: 100 * trues.get(item, 0) / count for item in observed}` when count else `{}`.
- Change `delta_stats()` to delegate:
  `s = await self.gate_drop_stats("d"); return {"count": s["count"], "avg": s["avg"], "laser_rate": s["rates"].get("laser", 0.0)}`. Guarantees `test_delta_generalized_rates_match_laser_rate` agreement and keeps the legacy shape/zero-behaviour.
- `export_all()`: in the gate_entries backfill loop, also `g.setdefault("drops", None)` alongside the existing `g.setdefault("laser_dropped", None)`. (New rows already carry both keys; setdefault only shapes legacy rows.)
- `import_all()`: gate_entries are deep-copied wholesale — no change needed; keeps both keys.
- `clear()`, `list_gate_costs`, `user_gate_counts`, `user_gate_cost_total`, `gate_record`, `gate_totals` already filter by `gate_type` string / iterate `GATE_TYPES` and therefore cover e/z/k with no change.

### `n3x_bot/storage/sql_repo.py`
- `import json` at top.
- `add_gate_entry(..., laser_dropped=None, drops=None)`: same normalization as json; insert `drops=json.dumps(drops) if drops else None` and `laser_dropped=laser_col` into `sc.gate_entries`.
- Add `async def gate_drop_stats(self, gate_type)`: SELECT `cost, drops, laser_dropped` where `gate_type == gate_type`; compute count/avg/rates in Python (parse `drops` via `json.loads`, fall back to `{"laser": laser_dropped}` when drops is NULL and laser_dropped not None). Mirror the json math exactly so both backends agree.
- Change `delta_stats()` to delegate to `gate_drop_stats("d")` (same shape as json), OR keep the current SQL aggregate but source `laser_rate` from `gate_drop_stats`. Prefer delegation for guaranteed agreement.
- `export_all()`: add `"drops"` to each gate row dict: `"drops": (json.loads(r.drops) if r.drops else None)`. Keep the existing `laser_dropped` key derivation.
- `import_all()`: gate_entries insert also writes `drops=json.dumps(r["drops"]) if r.get("drops") else None` and `laser_dropped=r.get("laser_dropped")`.
- `clear()` already deletes `sc.gate_entries` — no change.

### `n3x_bot/gates.py`
- `_PATTERN = re.compile(r"^([abcdezk])\s+([\d.]+)$", re.IGNORECASE)`.
- Extend `GATE_NAMES` with `"e": "Epsilon Gate", "z": "Zeta Gate", "k": "Kappa Gate"`.
- Add a label map for drop items:
  `_DROP_LABELS = {"laser": "Laser Drop Rate", "lf4": "LF4 Drop Rate", "havoc": "Havoc Drop Rate", "hercules": "Hercules Drop Rate", "lf4u": "LF4-U Drop Rate"}`.
- `build_gate_embed(totals, rewards, now_str, delta=None, epsilon=None, zeta=None, kappa=None)`:
  - Delta field unchanged (keeps Reward line).
  - For each of epsilon/zeta/kappa that is not None, add a field:
    - name: `"🔷 Epsilon Gate"` / `"🔷 Zeta Gate"` / `"🔷 Kappa Gate"`
    - value: `Runs: {count}` + `Avg. Cost: {format_number(avg)}` + one line per rate `f"{_DROP_LABELS[item]}: {rate:.1f} %"` (iterate `stats["rates"].items()`). **No Reward line.**
  - Field order: delta, epsilon, zeta, kappa (all `inline=True`, matching delta).
- Add `KappaConfirmView(discord.ui.View)`:
  - `__init__(self, repo, bot, settings, *, cost, user_id, username)`: `super().__init__(timeout=None)`; store attrs; `self.hercules_dropped = False`; `self.lf4u_dropped = False`. Create three `discord.ui.Button`s (Hercules toggle, LF4-U toggle, Submit); set each `.callback` to the matching coroutine below; `self.add_item(...)` each → `len(self.children) == 3`.
  - `async def on_toggle_hercules(self, interaction)`: author guard `if interaction.user.id != self.user_id: return` (no state change); else `self.hercules_dropped = not self.hercules_dropped`; optionally refresh the button visual and `await interaction.response.edit_message(view=self)` (or `defer()`), not asserted.
  - `async def on_toggle_lf4u(self, interaction)`: same pattern for `lf4u_dropped`.
  - `async def on_submit(self, interaction)`: author guard `if interaction.user.id != self.user_id: return` (store nothing). Else store:
    `await self.repo.add_gate_entry("k", self.cost, self.user_id, self.username, drops={"hercules": self.hercules_dropped, "lf4u": self.lf4u_dropped})`. On insert, mirror the delta post-store side effects (records + `update_gate_stats_embed` + achievements) guarded by try/except so mocked interactions never raise. To avoid a circular import, import `update_gate_stats_embed` / `_announce_records` / `check_achievements` / `announce_achievements` lazily inside the method (from `n3x_bot.bot` and existing modules) — see Risks.

### `n3x_bot/bot.py`
- Import `GATE_TYPES` from `n3x_bot.storage.base` (already imports `StatsRepository` from there).
- Import list from gates: add nothing new required unless the coder chooses to move KappaConfirmView here (keep it in gates.py per the test import `from n3x_bot.gates import KappaConfirmView`).
- `handle_gate_input_message` routing (replace the current `if gate_type == "d":` block):
  - `if gate_type in ("d", "e", "z"):`
    - `pending = {"cost": cost, "user_id": message.author.id, "username": message.author.name}`
    - `if gate_type != "d": pending["gate_type"] = gate_type` (d keeps its exact legacy dict — pins `test_delta_gate`)
    - `bot._pending_delta[message.id] = pending`; react ✅ + ❎; `return`.
  - `elif gate_type == "k":`
    - `await message.channel.send(view=KappaConfirmView(repo, bot, settings, cost=cost, user_id=message.author.id, username=message.author.name))`; `return`. (Import KappaConfirmView from n3x_bot.gates.)
  - a/b/c: unchanged (immediate store).
- `handle_delta_confirmation` (generalize):
  - After the atomic `pop`, `gate_type = pending.get("gate_type", "d")`.
  - `dropped = emoji == "✅"`.
  - Store, branching to preserve the legacy `d` path:
    - `if gate_type == "d": inserted = await repo.add_gate_entry("d", cost, uid, uname, laser_dropped=dropped)`
    - `else: item = {"e": "lf4", "z": "havoc"}[gate_type]; inserted = await repo.add_gate_entry(gate_type, cost, uid, uname, drops={item: dropped})`
    - **Why the branch (PINNED):** `test_delta_gate::_SuspendingDeltaRepo.add_gate_entry` only accepts `laser_dropped=`, no `drops=` kwarg. The d path must keep using `laser_dropped=` or that concurrency test raises TypeError.
  - Post-store side effects use `gate_type` throughout: `gate_record(gate_type)`, `_announce_records(..., gate_type, ...)`, `update_gate_stats_embed`, and achievements
    `check_achievements(repo, uid, f"gate_{gate_type}") + ... "gate_total" + ... "gate_cost_total"`.
- `update_gate_stats_embed`: after `delta = await repo.delta_stats()`, add
  `epsilon = await repo.gate_drop_stats("e")`, `zeta = await repo.gate_drop_stats("z")`, `kappa = await repo.gate_drop_stats("k")`, and call
  `build_gate_embed(totals, rewards, now_str, delta, epsilon=epsilon, zeta=zeta, kappa=kappa)`. (Real repos only reach this path; the persistence tests use a real JsonRepo, so gate_drop_stats resolves. The concurrency test mocks this function out.)
- `_handle_gate_stat`: replace `if gtype not in settings.gate_rewards_map():` with `if gtype not in GATE_TYPES:`; update the German string to `"Ungültiger Gate-Typ. Bitte nutze a, b, c, d, e, z oder k."`.

### `n3x_bot/achievements.py`
- Extend `GATE_NAMES` with `"e": "Epsilon", "z": "Zeta", "k": "Kappa"` (short names used in titles + cards).
- `_build_achievements`: change the gate tier loop to `for gtype in ("a", "b", "c", "d", "e", "z", "k"):` → adds 24 achievements (8×3) → total 83. `TOTAL_ACHIEVEMENTS` is `len(...)` so it auto-derives to 83.
- `user_metric_value`: extend the gate-tier branch to include e/z/k, e.g. change the tuple to `("gate_a", "gate_b", "gate_c", "gate_d", "gate_e", "gate_z", "gate_k")`. `gate_total` already sums `user_gate_counts().values()` (all types) and `gate_cost_total` sums all costs — no change; they include e/z/k automatically.

### `n3x_bot/cards.py`
- `_milestone_line`: extend the first branch tuple to include gate_e/z/k:
  `if metric in ("gate_a", "gate_b", "gate_c", "gate_d", "gate_e", "gate_z", "gate_k"):` → returns `f"{threshold} {GATE_NAMES[gtype]} Gates"` (e.g. `"5 Epsilon Gates"`). `GATE_NAMES` (imported from achievements) now includes e/z/k.

## Data flow

### Epsilon confirmation (representative)
1. User posts `e 46.892` in the gate-input channel → `on_message` → `handle_gate_input_message`.
2. `parse_gate_message` → `("e", 46892)`. `gate_type in ("d","e","z")` → seed
   `bot._pending_delta[msg.id] = {"cost":46892,"user_id":7,"username":"Erkan","gate_type":"e"}`, react ✅/❎, return (nothing stored).
3. Author reacts ✅ → `on_raw_reaction_add` → `handle_delta_confirmation`.
4. Guards pass (emoji ✅/❎, author match), atomic `pop`. `gate_type = "e"`, `dropped = True`, `item = "lf4"`.
5. `repo.add_gate_entry("e", 46892, 7, "Erkan", drops={"lf4": True})` → normalized (drops already set), `laser_col = drops.get("laser") = None`; row stored with `drops={"lf4":True}`, `laser_dropped=None`.
6. `gate_drop_stats("e")` → count 1, rates `{"lf4": 100.0}`. Records/embed/achievements fire (gate_e/gate_total/gate_cost_total).

### Kappa confirmation
1. `k 500` → `handle_gate_input_message` sends `KappaConfirmView` on the channel; nothing stored.
2. Author clicks Hercules → button callback → `on_toggle_hercules` flips `hercules_dropped` (author guard).
3. Author clicks Submit → `on_submit` (author guard) → `repo.add_gate_entry("k", 500, 7, "Erkan", drops={"hercules": True, "lf4u": False})`.
4. `gate_drop_stats("k")` → rates `{"hercules":100.0,"lf4u":0.0}`.

### Legacy Delta (unchanged)
`d 250.000` → pending dict WITHOUT gate_type → ✅ → `add_gate_entry("d", ..., laser_dropped=True)` → drops normalized to `{"laser":True}`, laser_dropped column True. `export_all` d-row has `laser_dropped is True`. `delta_stats()` derives laser_rate from `gate_drop_stats("d")`.

## Dependencies
- New packages: **none**. `json` (stdlib) for SQL drops (de)serialization; `re`, `discord.ui` already available.
- Internal modules touched: `storage/{base,schema,json_repo,sql_repo}`, `gates`, `bot`, `achievements`, `cards`. `KappaConfirmView.on_submit` depends (lazily) on `bot.update_gate_stats_embed`, `bot._announce_records`, `achievements.check_achievements`, `cards.announce_achievements`.

## Build sequence (for the Coder)

1. **schema.py** — add `drops` Text column to `gate_entries`; keep `laser_dropped`.
   (No test greens yet, but unblocks storage.)
2. **base.py** — GATE_TYPES += e/z/k; new `add_gate_entry` signature (`+drops`); add abstract `gate_drop_stats`. → greens `test_gate_types_includes_epsilon_zeta_kappa`.
3. **json_repo.py** — `_drops_of` helper; normalize+store drops & laser_col in `add_gate_entry`; `gate_drop_stats`; delegate `delta_stats`; export/import `drops` backfill. → greens the bulk of `test_gate_drops_contract` (json) and keeps `test_gate_records_contract` (json) green.
4. **sql_repo.py** — mirror json for `add_gate_entry`, `gate_drop_stats`, `delta_stats`, export/import. → greens `test_gate_drops_contract` (sqlite/postgres) + keeps `test_gate_records_contract` (sqlite/postgres).
5. **gates.py** — `_PATTERN` + GATE_NAMES + `_DROP_LABELS` + `build_gate_embed` e/z/k fields. → greens `test_gates.py` (parse + embed).
6. **gates.py** — `KappaConfirmView`. → greens the KappaConfirmView unit tests in `test_ezk_gates.py`.
7. **bot.py** — `handle_gate_input_message` routing (d/e/z/k); `handle_delta_confirmation` generalization with the d-branch; `update_gate_stats_embed` e/z/k; `_handle_gate_stat` GATE_TYPES validation. → greens the input/confirmation/`!stat` tests in `test_ezk_gates.py`; keeps `test_delta_gate.py` + `test_gate_embed_persistence.py` green.
8. **achievements.py** — GATE_NAMES += e/z/k; gate loop += e/z/k; `user_metric_value` gate branch += e/z/k. → greens the definition/metric/detection tests + `2/83`.
9. **cards.py** — `_milestone_line` gate tuple += e/z/k. → greens `test_milestone_line_renders_ezk_gate_names`.
10. Run the four target test files + the two legacy files (`test_delta_gate.py`, `test_gate_records_contract.py`, plus storage contracts) to confirm no regression.

## Compat confirmation (existing tests stay green)
- **`laser_dropped=` alias:** kept on `add_gate_entry` in base + both repos; d writes still accept it and populate the `laser_dropped` column. `test_gate_records_contract` + `test_delta_gate` unaffected.
- **`_pending_delta` d dict:** for `d` we do NOT add a `gate_type` key, so `test_delta_input_registers_pending_and_reacts_check_and_cross`'s exact-dict equality holds; `default gate_type "d"` is applied via `pending.get("gate_type", "d")` at confirmation time.
- **d confirmation path uses `laser_dropped=`** (not `drops=`), so `_SuspendingDeltaRepo` (which lacks a `drops` kwarg) keeps working — pins `test_concurrent_delta_confirmations_store_exactly_one`.
- **`export_all` shape:** every gate row still carries `laser_dropped` (True/False/None); `drops` is additive. `test_abc_entries_store_laser_dropped_as_none` and the round-trip legacy test hold.
- **`delta_stats()` shape** `{count, avg, laser_rate}` preserved; laser_rate now derived from `gate_drop_stats("d")` so it numerically matches the generalized path AND the legacy 66.7 / 0 assertions.
- **`update_gate_stats_embed` gate-embed-persistence PR** — untouched control flow (still `channel_messages`/`_gate_embed_msg_id`); only the embed-building args grow. Persistence tests use a real JsonRepo, so the new `gate_drop_stats` calls resolve.

## Risks and open questions
- **d confirmation must branch on `laser_dropped=` vs `drops=`.** The TDD handoff text says "store via drops={item: bool}" for d/e/z uniformly, but `test_delta_gate::_SuspendingDeltaRepo.add_gate_entry` only accepts `laser_dropped=`. Implementing the handoff literally would raise TypeError there. Resolution (PINNED): d → `laser_dropped=`, e/z → `drops=`. Flagging because it deviates from the handoff wording to keep a pinned legacy test green. The stored result is identical (`laser_dropped=True` normalizes to `drops={"laser":True}`).
- **`KappaConfirmView.on_submit` side effects.** The tests only assert storage + rates; they don't require records/achievements/embed refresh. Mirroring the delta post-store path adds Delta-parity but needs a lazy import of `n3x_bot.bot` (circular-import avoidance) inside `on_submit`. If parity is not wanted, `on_submit` can store-only. Recommendation: store + guarded (try/except) parity side effects, lazily imported. Confirm desired scope.
- **Rate denominator.** `gate_drop_stats` uses the gate's TOTAL entry count as the denominator for every observed item (per the pinned spec), even if some entries lack that item key. In practice all entries of a given gate carry the same item keys, so this only matters for mixed legacy data; called out in case future data mixes shapes.
- **`gate_totals()` now iterates e/z/k too** (via GATE_TYPES), so its dict gains e/z/k keys. `build_gate_content` only consumes a/b/c, so this is inert, but any other consumer of `gate_totals()` will now see the extra keys — no current consumer depends on the key set being exactly a–d.
- **Prod schema rebuild.** No ALTER framework; the operator must manually rebuild `gate_entries` to add the `drops` column (stated as handled out-of-band). Fresh DBs get it from `create_all`. Legacy rows (laser_dropped only, drops NULL) read correctly via the `_drops_of` fallback.
- **Button visual/response in KappaConfirmView.** Tests pass MagicMock interactions and don't assert any `interaction.response.*` call, so the exact response mechanism (edit_message vs defer) is free; pick one that satisfies Discord at runtime.
