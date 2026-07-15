# Architecture: Gate history graph (`!gate verlauf`)

Turns the RED suite on `feature/gate-verlauf` green. Four surfaces: a repo
read-view (`list_gate_entries`), a date parser (`parse_de_date`), a pure
matplotlib chart renderer (`n3x_bot/charts.py`), and a `!gate` command group
with a `verlauf` subcommand wired by `build_bot`. No schema change (the
`gate_entries` table already carries `cost`, `laser_dropped`, `drops`,
`created_at`). No new dependency in `pyproject.toml` (matplotlib is already
pinned there) — but `requirements.txt` MUST be regenerated (see Risks).

## Tests this design satisfies

Storage contract — `tests/storage/test_gate_entries_contract.py` (json + sqlite + postgres):
- `test_list_gate_entries_empty_gate_is_empty_list` — empty gate → `[]`
- `test_list_gate_entries_returns_cost_created_at_and_drops` — dict shape `{cost, created_at, drops}`, a/b/c drops `{}`
- `test_list_gate_entries_created_at_is_tz_aware` — `created_at.tzinfo is not None`
- `test_list_gate_entries_ordered_by_created_at_ascending` — ASC order
- `test_list_gate_entries_abc_have_empty_drops` — b → `{}`
- `test_list_gate_entries_delta_exposes_laser_drop` — d (legacy `laser_dropped`) → `{"laser": True}`
- `test_list_gate_entries_epsilon_exposes_lf4_drop` — e → `{"lf4": False}`
- `test_list_gate_entries_zeta_exposes_havoc_drop` — z → `{"havoc": True}`
- `test_list_gate_entries_kappa_exposes_both_items` — k → `{"hercules": True, "lf4u": False}`
- `test_list_gate_entries_since_is_inclusive` / `_until_is_inclusive` — inclusive tz-aware bounds
- `test_list_gate_entries_window_fully_in_future_is_empty` / `_in_past_is_empty`
- `test_list_gate_entries_only_returns_requested_gate_type`

Feature — `tests/test_gate_verlauf.py`:
- `test_parse_de_date_accepts_german_format` / `_accepts_iso_format` / `_returns_none_for_junk` / `_returns_none_for_empty_string`
- `test_render_chart_returns_non_empty_png_bytes_for_cost_gate` — a → valid PNG bytes
- `test_render_chart_handles_drop_gates[d|e|z|k]` — each drop gate → valid PNG
- `test_render_chart_kappa_partial_drop_renders` — k partial drop, single entry → valid PNG
- `test_render_chart_empty_entries_still_returns_valid_png` — `[]` → valid "keine Daten" PNG
- `test_gate_group_and_verlauf_subcommand_are_registered`
- `test_verlauf_valid_gate_posts_png_file` / `_uppercase_gate_resolves`
- `test_verlauf_invalid_gate_refuses_without_file` — "Ungültiger Gate-Typ", no file
- `test_verlauf_invalid_date_refuses_with_german_hint_no_file` — message contains "Datum", no file
- `test_verlauf_date_range_filters_entries_by_parsed_window` — `list_gate_entries` called with tz-aware since@00:00:00 / until@23:59 in `settings.timezone`
- `test_verlauf_no_data_in_range_still_posts_empty_chart` — still posts a `discord.File`
- `test_verlauf_is_not_admin_gated` — non-admin still gets the chart

Wiring — `tests/test_bot_wiring.py`:
- `test_build_bot_wires_gate_verlauf_group` — `bot.get_command("gate")` is a `commands.Group` exposing `verlauf` (`"gate"` already in the two exclusion tuples)

## Files to create

- `n3x-bot/n3x_bot/charts.py` — pure matplotlib (Agg) renderer.
  - Module top, in this exact order (pinned): `import matplotlib` then
    `matplotlib.use("Agg")` then `import matplotlib.pyplot as plt`. Also
    `from io import BytesIO`, `from datetime import datetime`, and
    `from n3x_bot.gates import GATE_NAMES, _DROP_LABELS`.
  - Module-level `_CHART_DROP_ITEMS = {"d": ["laser"], "e": ["lf4"], "z": ["havoc"], "k": ["hercules", "lf4u"]}`
    (local copy because `gates._GATE_DROP_ITEMS` omits `"d"`).
  - `def render_gate_history_chart(gate_type: str, entries: list[dict], now: datetime, von=None, bis=None) -> bytes`
    - Responsibilities: build one `fig, ax = plt.subplots()`; title
      `f"Gate-Verlauf: {GATE_NAMES[gate_type]}"`, xlabel `"Datum"`, ylabel
      `"Kosten"`. Then:
      - `entries == []` → draw a centered `ax.text(..., "keine Daten", ...)`
        (no plot). Still a valid PNG (pinned — not a text reply).
      - a/b/c → plot `(created_at, cost)` points/line, plus a single average
        `ax.axhline(mean(costs))`. NO drop legend.
      - d/e/z/k → plot the `(created_at, cost)` cost series, then for each item
        in `_CHART_DROP_ITEMS[gate_type]` overlay a `scatter` of only the runs
        where `drops.get(item)` is True, labeled `_DROP_LABELS[item]`
        (Laser/LF4/Havoc/Hercules/LF4-U), distinct color per item, and
        `ax.legend()`. An item that never dropped True yields an empty scatter
        but still contributes its legend entry — this is what lets Kappa render
        on a partial drop (`{hercules: True, lf4u: False}`) without raising.
    - Serialize: `buf = BytesIO(); fig.savefig(buf, format="png"); return buf.getvalue()`.
    - Always `plt.close(fig)` in a `finally` (leak guard, pinned).
    - `now` / `von` / `bis` are accepted for signature compatibility; they MAY
      bound the x-axis (e.g. `ax.set_xlim(von or first_date, bis or now)`) but
      no test asserts their effect — keep any use minimal and defensive.

## Files to modify

- `n3x-bot/n3x_bot/storage/base.py` — add one `@abstractmethod` in the
  "gate tracker" block (after `user_gate_cost_total`, ~line 198):
  `async def list_gate_entries(self, gate_type: str, since: datetime | None = None, until: datetime | None = None) -> list[dict]: ...`
  with a docstring pinning: `{"cost": int, "created_at": tz-aware datetime,
  "drops": dict[str,bool]}`, ASC by `created_at`, inclusive tz-aware
  since/until, read-view only (NOT in export/import/clear). `datetime` is
  already imported.

- `n3x-bot/n3x_bot/storage/json_repo.py` — add `list_gate_entries` in the gate
  block (near `list_gate_costs`, ~line 362). Add a module-level
  `_as_aware_utc(dt)` helper mirroring sql_repo (coerce a naive dt to
  UTC-aware; `timezone` is already imported) to satisfy the handoff's
  "coerce both sides" idiom. Implementation shape:
  - filter rows by `gate_type`; parse each `created_at` via `_parse_dt`
    (already tz-aware, stored as UTC isoformat); sort by `(created_at, id)` ASC;
  - inclusive filter: skip when `created < _as_aware_utc(since)` or
    `created > _as_aware_utc(until)` (both None-guarded);
  - `drops` via the existing `_drops_of(r)` (returns `{}` / `{"laser": bool}` /
    the stored map);
  - emit `{"cost": r["cost"], "created_at": created, "drops": _drops_of(r)}`.
  No change to `export_all`/`import_all`/`clear`.

- `n3x-bot/n3x_bot/storage/sql_repo.py` — add `list_gate_entries` near
  `list_gate_costs` (~line 471). Uses existing `_as_aware_utc`, `json`, `sc`:
  - `select(cost, drops, laser_dropped, created_at).where(gate_type == gate_type)
    .order_by(created_at.asc(), id.asc())` via `self.engine.connect()`;
  - filter in Python (the `purge_expired_base_timers` idiom, because SQLite
    round-trips `created_at` naive): `created = _as_aware_utc(r.created_at)`,
    skip when `< _as_aware_utc(since)` or `> _as_aware_utc(until)`;
  - drops parse mirroring `gate_drop_stats`: `json.loads(r.drops)` if set,
    elif `r.laser_dropped is not None` → `{"laser": bool(r.laser_dropped)}`,
    else `{}`;
  - emit `{"cost": r.cost, "created_at": created, "drops": drop_map}`.
  No change to `export_all`/`import_all`/`clear`.

- `n3x-bot/n3x_bot/gates.py` — add `parse_de_date`. Add `from datetime import
  datetime, date` at top (currently only `re`/`discord`/`format` imported).
  `def parse_de_date(s: str) -> date | None`: strip, try `"%d.%m.%Y"` then
  `"%Y-%m-%d"` via `datetime.strptime(...).date()`, return the first that
  parses, else `None` (junk and `""` → `None`, never raises).

- `n3x-bot/n3x_bot/bot.py` — imports + command group.
  - Imports: add `from io import BytesIO`; add `from zoneinfo import ZoneInfo`;
    extend `from n3x_bot.gates import (...)` (line 32) with `parse_de_date`;
    add `from n3x_bot.charts import render_gate_history_chart`. `datetime` and
    `time` are already imported (line 3); `now_local`, `GATE_TYPES`,
    `discord`, `commands` already present.
  - Extend `register_gate_commands` (line 427) to also wire the group behind a
    `if bot.get_command("gate") is None:` guard (mirrors the existing `stat`/
    `del` guards; `register_gate_commands` is already called in `build_bot` at
    line 115, so no `build_bot` edit is needed):
    - `@bot.group(name="gate", invoke_without_command=True)` → `gate_group(ctx)`
      that sends a short German usage hint (`delete_after=5`), matching the
      `register_config_commands` `@bot.group` idiom.
    - `@gate_group.command(name="verlauf")` →
      `verlauf(ctx, gate, von=None, bis=None)` (NOT admin-gated):
      1. `gtype = gate.lower()`; if `gtype not in GATE_TYPES` →
         `await ctx.send("Ungültiger Gate-Typ. Bitte nutze a, b, c, d, e, z oder k.", delete_after=5)`; return (no file).
      2. For each of `von`/`bis` that is not None: `parse_de_date(...)`; if it
         returns None → `await ctx.send("❌ Ungültiges Datum. Nutze TT.MM.JJJJ oder JJJJ-MM-TT.", delete_after=5)`; return (message contains "Datum", no file).
      3. `tz = ZoneInfo(settings.timezone)`;
         `since = datetime.combine(von_d, time(0, 0, 0), tzinfo=tz)` when `von`
         given else None; `until = datetime.combine(bis_d, time(23, 59, 59), tzinfo=tz)` when `bis` given else None.
      4. `entries = await repo.list_gate_entries(gtype, since, until)` (pass
         `since`/`until` POSITIONALLY so the spy sees `call.args[1]`/`args[2]`).
      5. `png = render_gate_history_chart(gtype, entries, now_local(settings), von_d, bis_d)`.
      6. `await ctx.send(file=discord.File(BytesIO(png), filename=f"verlauf_{gtype}.png"))`.

- `n3x-bot/requirements.txt` — regenerate to include matplotlib + transitive
  deps (see Risks). This is the AMP install source.

## Data flow

`!gate verlauf a 01.07.2026 15.07.2026`:
1. `commands.Group` "gate" dispatches to `verlauf`.
2. `gate="a"` → `gtype="a"` ∈ `GATE_TYPES`; `von`/`bis` parse to
   `date(2026,7,1)` / `date(2026,7,15)`.
3. Build `since = 2026-07-01 00:00:00+Berlin`, `until = 2026-07-15 23:59:59+Berlin`.
4. `repo.list_gate_entries("a", since, until)` → SQL/JSON reads `gate_entries`
   for gate "a", coerces `created_at` to aware UTC, keeps rows within the
   inclusive window, returns `[{cost, created_at, drops={}}, ...]` ASC.
5. `render_gate_history_chart("a", entries, now_local, von_d, bis_d)` → plots
   cost series + average line (a/b/c: no drop legend), `savefig` → PNG bytes,
   `plt.close`.
6. `ctx.send(file=discord.File(BytesIO(png), filename="verlauf_a.png"))`.

Empty-window variant (`01.01.2000`–`02.01.2000`): step 4 returns `[]`; step 5
draws the "keine Daten" PNG; step 6 still posts a `discord.File`.

## Dependencies

- New packages: NONE to add to `pyproject.toml` — `matplotlib>=3.8` (line 13)
  and `pillow>=10.0` (line 12) are already declared. PIL is a test-only import.
- `requirements.txt` must be regenerated to actually ship matplotlib to the AMP
  venv (see Risks).
- Internal: `n3x_bot.gates` (`GATE_NAMES`, `_DROP_LABELS`, new `parse_de_date`),
  `n3x_bot.charts` (new), `n3x_bot.activity.now_local`,
  `n3x_bot.storage.base.GATE_TYPES`, the repos' existing `_drops_of` /
  `_as_aware_utc` / `_parse_dt` helpers.

## Build sequence (for the Coder)

1. `storage/base.py`: add the `list_gate_entries` abstractmethod. (Instantiating
   either repo now requires it; contract collection stays valid.)
2. `storage/json_repo.py`: add `_as_aware_utc` + `list_gate_entries`. → greens
   all `test_gate_entries_contract` cases on the json backend.
3. `storage/sql_repo.py`: add `list_gate_entries`. → greens the sqlite (and
   postgres, when `TEST_POSTGRES_URL` set) contract cases.
4. `gates.py`: add `parse_de_date` (+ datetime import). → greens the four
   `parse_de_date` tests.
5. `charts.py`: new module. → greens the six render-chart tests (needs
   matplotlib importable — step 8).
6. `bot.py`: imports + extend `register_gate_commands` with the `gate` group and
   `verlauf`. → greens the group-registration, command-behaviour, date-filter,
   empty-chart, non-admin tests and `test_build_bot_wires_gate_verlauf_group`.
7. Confirm no `export_all`/`import_all`/`clear` edits (read view only).
8. Regenerate `requirements.txt` so matplotlib installs in the AMP venv:
   `uv --project n3x-bot export --no-dev --no-hashes --no-emit-project --format requirements-txt -o n3x-bot/requirements.txt`
   (matches the file's existing autogen header). Without this the chart tests /
   runtime `import matplotlib` fail in the AMP env even though `pyproject.toml`
   is correct.

## Risks and open questions

- **`requirements.txt` is the AMP install source and lacks matplotlib.**
  `pyproject.toml` already pins `matplotlib>=3.8`, but `requirements.txt`
  (uv-exported, fully pinned incl. transitive deps) has neither matplotlib nor
  its transitive chain (numpy, contourpy, cycler, fonttools, kiwisolver,
  packaging, pyparsing, python-dateutil, six). Regenerate via `uv export`
  (step 8) rather than hand-editing, so the transitive pins are correct. The
  chart RED tests currently fail on `ModuleNotFoundError` precisely because
  matplotlib isn't installed in this env — this is the fix. Flagging because it
  is a non-obvious cross-file coupling (config repo / AMP venv).
- **Kappa two-item viz choice.** For d/e/z/k I overlay one `scatter` per drop
  item containing only the True-outcome runs, each added to the legend
  (Hercules + LF4-U for Kappa). An item with zero True runs still gets a legend
  entry from an empty scatter — this is deliberate so partial/absent drops never
  raise. The tests assert PNG validity only (never pixel content), so the exact
  marker/color styling is a free choice; this is the simplest scheme that
  satisfies "per-run drop-outcome markers + legend naming the item(s)" and the
  partial-Kappa case. If a richer viz is wanted (e.g. dropped-vs-not two-tone
  per run) it can be swapped without touching any test.
- **since/until passed positionally.** `test_verlauf_date_range...` reads both
  `call.kwargs` and `call.args[1]/[2]`, so keyword would also pass; I specify
  positional for clarity and to keep the signature call site simple.
- **Date-filter tz bounds confirmed.** `since = combine(von, 00:00:00, tz)` and
  `until = combine(bis, 23:59:59, tz)` in `ZoneInfo(settings.timezone)` satisfy
  the test's `since_local` == von@(0,0,0) and `until_local` == bis with
  `hour == 23 and minute == 59`. The repos compare inclusively
  (`created < since` / `created > until` are the only exclusions), matching the
  contract's inclusive since/until tests.
- **No new StatsRepository subclass / no DB mock.** Only `JsonRepository` and
  `SqlRepository` implement the ABC; both are updated. Contract tests run
  against real json/sqlite/postgres per the no-mock rule — no in-memory
  substitute is introduced.
