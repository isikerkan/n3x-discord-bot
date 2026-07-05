# N3X Bot — v2 Feature Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Port the five new features from the Manus `v2/bot.py` prototype into our `n3x_bot` package architecture (config-driven, pluggable storage, tested) — without copying v2's single-file/hardcoded/untested code.

**Architecture:** Reuse existing seams. New config → `Settings`. New storage → `StatsRepository` interface + all three backends + shared contract tests. New commands → dynamic where possible, special-cased only where a Discord argument is required.

**Tech Stack:** unchanged (discord.py, pydantic-settings, SQLAlchemy async, pytest).

## Global Constraints

- No hardcoded IDs/secrets — all IDs, the reward table, and role gates come from `Settings`/`.env`.
- Async repository; JSON + SQL behave identically; every new repo method has a contract test covering all backends.
- Coverage stays >= 80%.
- German command copy preserved verbatim from v2 where a command is ported.
- Em-dash default render and existing behavior unchanged.

---

### Task A: Quick wins — error handlers, prefix strip, format helper

**Files:**
- Modify: `n3x_bot/bot.py` (on_command_error, enforce_prefix)
- Create: `n3x_bot/format.py` (`format_number`)
- Test: `tests/test_format.py`; `tests/test_bot_wiring.py` (error-handler + prefix additions where testable)

**Interfaces:**
- Produces: `format_number(n: int) -> str` → German grouping (`1234567 -> "1.234.567"`).

- [ ] **Step 1: `format_number` test (TDD)** — `tests/test_format.py`

```python
from n3x_bot.format import format_number


def test_format_number_german_grouping():
    assert format_number(1234567) == "1.234.567"
    assert format_number(0) == "0"
    assert format_number(999) == "999"
```

- [ ] **Step 2: Implement** — `n3x_bot/format.py`

```python
def format_number(n: int) -> str:
    return "{:,}".format(n).replace(",", ".")
```

Run: `uv run pytest tests/test_format.py -v` → pass.

- [ ] **Step 3: Error handlers** — in `n3x_bot/bot.py` `on_command_error`, add branches after the cooldown one (verbatim German copy from v2):

```python
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❌ Bitte gib einen Nutzer an.", delete_after=5)
        elif isinstance(error, commands.MemberNotFound):
            await ctx.send("❌ Nutzer nicht gefunden.", delete_after=5)
```

- [ ] **Step 4: Prefix strip fix** — in `enforce_prefix`, strip `[N3X]` too when rebuilding the base name. Change the `base = current.replace("R3X", "").strip()` line to also remove the prefix string:

```python
            base = current.replace("R3X", "").replace(settings.prefix_str, "").strip()
```

- [ ] **Step 5: Verify + commit**

Run: `uv run pytest --cov=n3x_bot --cov-report=term-missing` → pass, >= 80%.
Commit: `feat: german number format, richer command errors, prefix strip fix`

---

### Task B: Target stats + targeted commands

**Files:**
- Modify: `n3x_bot/storage/schema.py` (new `target_stats` table; `targeted` column on `stats`)
- Modify: `n3x_bot/storage/base.py` (`create_stat` gains `targeted: bool = False`; new `record_target_use`, `get_target_total`; `Stat` gains `targeted`)
- Modify: `n3x_bot/models.py` (`Stat.targeted: bool = False`)
- Modify: `n3x_bot/storage/json_repo.py`, `n3x_bot/storage/sql_repo.py`
- Modify: `n3x_bot/seed.py` (seed `smart`,`crash` as targeted; `home` targeted with default-target)
- Modify: `n3x_bot/config.py` (`julez_id: int = 0` for the `home` default target)
- Modify: `n3x_bot/bot.py` (targeted stats register a command taking `member: discord.Member`)
- Test: `tests/storage/test_repository_contract.py`; `tests/test_seed.py`; `tests/test_bot_wiring.py`

**Interfaces:**
- `Stat.targeted: bool`
- `create_stat(key, name, message_id=None, targeted=False) -> Stat`
- `record_target_use(target_discord_id: int, stat_key: str) -> int` — increments a per-target counter for the stat, returns the new target count. Raises KeyError for unknown stat. Also records the invoker via normal `record_use`? NO — target counting is separate; the invoker's `user_stats` is updated by the command via `record_use` as today. `record_target_use` only touches the target counter.
- `get_target_total(target_discord_id: int, stat_key: str) -> int`

**Schema (SQL):**
- `stats` gains `Column("targeted", Boolean, nullable=False, server_default=text("0"))` (default false).
- `target_stats(target_discord_id BIGINT, stat_id FK->stats, count, PK(target_discord_id, stat_id))`.

**JSON shape:** add `"target_stats": {"<stat_id>": {"<target_discord_id>": count}}` to `_empty()`; `stats` rows gain `"targeted": bool`.

- [ ] **Step 1: Contract test (TDD)** — add to `tests/storage/test_repository_contract.py`:

```python
async def test_targeted_stat_and_record_target_use(repo):
    await repo.create_stat("smart", "Smart", targeted=True)
    s = await repo.get_stat("smart")
    assert s.targeted is True
    c1 = await repo.record_target_use(999, "smart")
    c2 = await repo.record_target_use(999, "smart")
    c3 = await repo.record_target_use(111, "smart")
    assert (c1, c2, c3) == (1, 2, 1)
    assert await repo.get_target_total(999, "smart") == 2
    assert await repo.get_target_total(111, "smart") == 1


async def test_record_target_use_unknown_stat_raises(repo):
    import pytest
    with pytest.raises(KeyError):
        await repo.record_target_use(1, "ghost")


async def test_create_stat_defaults_not_targeted(repo):
    await repo.create_stat("tit", "Tit")
    assert (await repo.get_stat("tit")).targeted is False
```

Run `uv run pytest tests/storage -k "target or targeted" -v` → FAILS (methods/field missing) for both backends.

- [ ] **Step 2: Model** — `n3x_bot/models.py` add to `Stat` (after `message_id`): `targeted: bool = False`.

- [ ] **Step 3: Interface** — `n3x_bot/storage/base.py`: change `create_stat` signature to `create_stat(self, key, name, message_id=None, targeted=False)`; add abstract `record_target_use(self, target_discord_id, stat_key) -> int` and `get_target_total(self, target_discord_id, stat_key) -> int`.

- [ ] **Step 4: JSON backend** — `n3x_bot/storage/json_repo.py`:
  - `_empty()` add `"target_stats": {}`.
  - `_stat()` read `r.get("targeted", False)`.
  - `create_stat` store `"targeted": targeted`.
  - Implement:

```python
    async def record_target_use(self, target_discord_id, stat_key):
        stat = self._find("stats", key=stat_key)
        if stat is None:
            raise KeyError(stat_key)
        sid, tid = str(stat["id"]), str(target_discord_id)
        ts = self._db["target_stats"].setdefault(sid, {})
        ts[tid] = ts.get(tid, 0) + 1
        self._flush()
        return ts[tid]

    async def get_target_total(self, target_discord_id, stat_key):
        stat = self._find("stats", key=stat_key)
        if stat is None:
            return 0
        return self._db["target_stats"].get(str(stat["id"]), {}).get(str(target_discord_id), 0)
```

- [ ] **Step 5: SQL backend** — `n3x_bot/storage/schema.py` add `targeted` column to `stats` and the `target_stats` table:

```python
from sqlalchemy import Boolean, text
# in stats table, add:
    Column("targeted", Boolean, nullable=False, server_default=text("0")),
# new table:
target_stats = Table(
    "target_stats", metadata,
    Column("target_discord_id", BigInteger, primary_key=True),
    Column("stat_id", Integer, ForeignKey("stats.id"), primary_key=True),
    Column("count", Integer, nullable=False, default=0),
)
```

`n3x_bot/storage/sql_repo.py`: `_stat()` include `targeted=r.targeted`; `create_stat` insert `targeted=targeted`; implement `record_target_use` (select-then-insert-or-update upsert, KeyError before mutation, return new count) and `get_target_total` (join stats on key). Mirror the `record_use`/`get_total` patterns already in the file.

Run `uv run pytest tests/storage -k "target or targeted" -v` → PASS both backends.

- [ ] **Step 6: Seed targeted stats** — `n3x_bot/config.py` add `julez_id: int = 0`. In `n3x_bot/seed.py`, extend `LEGACY_STATS` handling so `smart` and `crash` are created with `targeted=True`, and add a `home` stat (targeted). Keep the German templates; targeted templates use `{target}` and `{count}` (target count). Define the targeted set explicitly, e.g.:

```python
TARGETED_STATS = {"smart", "crash", "home"}
```

and in `seed_defaults`, pass `targeted=(key in TARGETED_STATS)` to `create_stat`. Add `home` to `LEGACY_STATS` with template `"Der aller echteste Homelander {target} hat euch schon {count} mal am leben gelassen!"` and `smart`/`crash` templates updated to `{target}` form:
- `smart` → `"{target} beweist zum {count} Mal, dass er ein Klugscheisser ist.."`
- `crash` → `"{target} geht zum {count} mal komplett crashout... opfer"`

Update `tests/test_seed.py` to assert `smart`/`crash`/`home` are `targeted` and non-targeted stats are not.

- [ ] **Step 7: Render + targeted command wiring** — `n3x_bot/models.py`: extend `render_output` to also accept a `target_display` and expose `{target}` in `.format` (keep the try/except fallback). Signature: `render_output(stat, message, user_display, count, target_display=None)`; pass `target=target_display or ""` into `.format`.

`n3x_bot/bot.py`: in `register_stat_commands`, if `stat.targeted`, register a command that takes `member: discord.Member`:
```python
    async def _tcmd(ctx, member: discord.Member, _key=key):
        await repo.record_use(ctx.author.id, ctx.author.display_name, _key)  # invoker stats
        count = await repo.record_target_use(member.id, _key)
        stat = await repo.get_stat(_key)
        message = await repo.get_message(stat.message_id) if stat.message_id else None
        text = render_output(stat, message, ctx.author.display_name, count, target_display=member.mention)
        await _send_or_update(bot, repo, settings, f"{_key}_{member.id}", text)
```
For `home` (fixed target Julez): if `settings.julez_id` is set, `home` targets that id without needing an argument — register `home` as a no-arg command that uses `settings.julez_id` as the target. Add a `HOME_KEY = "home"` special-case in registration.

Add `build_target_output`-style unit tests in `tests/test_bot_wiring.py` using a seeded JsonRepository (assert target count increments and the message includes the target mention; assert invoker `user_stats` also incremented).

- [ ] **Step 8: Verify + commit**

Run: `uv run pytest --cov=n3x_bot --cov-report=term-missing` → pass, >= 80%. Re-verify postgres via container if available.
Commit: `feat: targeted stats (smart/crash/home) with per-target counters`

---

### Task C: Gate tracker subsystem

**Files:**
- Modify: `n3x_bot/config.py` (`gate_input_channel_id`, `gate_stats_channel_id`, `gate_delete_role_id`, `gate_rewards` parsed map)
- Modify: `n3x_bot/storage/schema.py` (`gate_entries` table)
- Modify: `n3x_bot/storage/base.py` + both repos (gate methods)
- Create: `n3x_bot/gates.py` (embed rendering + parsing helpers, Discord-free where possible)
- Modify: `n3x_bot/bot.py` (`on_message` gate-input handling, `!stat`, `!del`, `on_ready` embed refresh)
- Test: `tests/storage/test_repository_contract.py`; `tests/test_gates.py`

**Config:** `.env` / `Settings`:
- `gate_input_channel_id: int = 0`
- `gate_stats_channel_id: int = 0`
- `gate_delete_role_id: int = 0`
- `gate_rewards: str = "a:46892,b:93820,c:139522"` with a parser `gate_rewards_map() -> dict[str,int]`.

**Repo interface (new, contract-tested):**
- `add_gate_entry(gate_type: str, cost: int, user_id: int, username: str, dedup_window_seconds: int = 30) -> bool` — inserts unless an identical (user_id, gate_type, cost) row exists within the window; returns True if inserted.
- `list_gate_costs(gate_type: str) -> list[int]` — costs ordered by insertion.
- `delete_gate_entry(gate_type: str, index: int) -> bool` — 1-based index into the ordered list; True if deleted.
- `gate_totals() -> dict[str, dict]` — `{gate_type: {"count": int, "avg": int}}` for the configured gate types.

**Schema:** `gate_entries(id PK autoincrement, gate_type STRING, cost INTEGER, user_id BIGINT, username STRING, created_at DateTime(timezone=True))`. Dedup uses `created_at > now - window`. Note: the dedup time comparison must be done in Python (fetch recent rows) OR via a dialect-portable filter — to keep sqlite/postgres identical, fetch candidate rows and compare timestamps in Python.

**JSON shape:** `"gate_entries": [ {id, gate_type, cost, user_id, username, created_at} ]`, plus `seq["gate"]`.

- [ ] **Step 1: Contract tests (TDD)** — add to `tests/storage/test_repository_contract.py`:

```python
async def test_gate_add_list_delete_totals(repo):
    assert await repo.add_gate_entry("a", 46000, 1, "u1") is True
    assert await repo.add_gate_entry("a", 47000, 2, "u2") is True
    assert await repo.list_gate_costs("a") == [46000, 47000]
    totals = await repo.gate_totals()
    assert totals["a"]["count"] == 2
    assert totals["a"]["avg"] == 46500
    assert await repo.delete_gate_entry("a", 1) is True
    assert await repo.list_gate_costs("a") == [47000]


async def test_gate_dedup_window(repo):
    assert await repo.add_gate_entry("b", 5, 1, "u1", dedup_window_seconds=3600) is True
    # identical within window -> rejected
    assert await repo.add_gate_entry("b", 5, 1, "u1", dedup_window_seconds=3600) is False
```

Run → FAILS both backends.

- [ ] **Step 2: Config** — `n3x_bot/config.py` add the four gate fields + `gate_rewards_map()`:

```python
    gate_input_channel_id: int = 0
    gate_stats_channel_id: int = 0
    gate_delete_role_id: int = 0
    gate_rewards: str = "a:46892,b:93820,c:139522"

    def gate_rewards_map(self) -> dict[str, int]:
        out = {}
        for pair in self.gate_rewards.split(","):
            if ":" in pair:
                k, v = pair.split(":", 1)
                out[k.strip()] = int(v)
        return out
```

Add these to `.env.example` too.

- [ ] **Step 3: JSON gate methods** — `n3x_bot/storage/json_repo.py`: add `"gate_entries": []` to `_empty()`, `"gate"` to `seq`. Implement the four methods using `datetime` for the dedup window (compare `created_at` isoformat parsed). Full code (write it out; mirror existing style).

- [ ] **Step 4: SQL gate methods** — `n3x_bot/storage/schema.py` add `gate_entries` table; `n3x_bot/storage/sql_repo.py` implement the four methods. For dedup, `select` rows matching (user_id, gate_type, cost) and filter `created_at > _now() - timedelta(seconds=window)` — do the timedelta comparison in the query with a Python-computed threshold datetime (portable). `gate_totals` computes count + avg (use SQL `func.count`/`func.avg`, cast avg to int in Python).

Run `uv run pytest tests/storage -k gate -v` → PASS both backends.

- [ ] **Step 5: Gate embed + parsing** — `n3x_bot/gates.py`:
  - `parse_gate_message(content: str) -> tuple[str, int] | None` — regex `^([abc])\s+([\d\.]+)$` (case-insensitive), strip dots from the number; return `(gate_type_lower, cost)` or None.
  - `build_gate_embed(totals: dict, rewards: dict, now_str: str) -> discord.Embed` — port v2's embed layout using `format_number`; `now_str` passed in (no `datetime.now()` inside, for testability).
  Unit-test `parse_gate_message` and the totals→content math in `tests/test_gates.py` (embed construction can be lightly asserted via `.description`).

- [ ] **Step 6: Bot wiring** — `n3x_bot/bot.py`:
  - `on_message`: if `message.channel.id == settings.gate_input_channel_id`, parse; on match `add_gate_entry` and if True refresh the embed + react ✅ (else ⏳); on no-match non-`!` message react ❌. Preserve the existing `!`-message auto-delete + `process_commands`.
  - `!stat <a|b|c>` command: list costs (chunked embeds as in v2) — validate the gate type against `settings.gate_rewards_map()`.
  - `!del <a|b|c> <index>`: role-gated on `settings.gate_delete_role_id`; delete + refresh embed.
  - Add an `update_gate_stats_embed(bot, repo, settings)` helper (stores last embed message id via a gate key in `stat_last_post`? No — gates aren't stats). Use an in-memory `bot._gate_embed_msg_id` like the rank map, or a dedicated `last_messages`-style store. Simplest: in-memory `bot._gate_embed_msg_id` (ephemeral; re-posts on restart) OR persist via a new tiny repo method. Use in-memory to avoid schema bloat, consistent with the rank approach.
  - `on_ready`: call `update_gate_stats_embed(...)` once after setup (only if `gate_stats_channel_id` set).

- [ ] **Step 7: Verify + commit**

Run: `uv run pytest --cov=n3x_bot --cov-report=term-missing` → pass, >= 80%. Re-verify postgres via container if available.
Commit: `feat: gate tracker (input parsing, stats embed, !stat/!del)`

---

## Self-Review

- Target stats + member-arg commands (v2 #1,#3) → Task B. ✓
- Richer errors + prefix strip + format_number (v2 #4,#5) → Task A. ✓
- Gate subsystem (v2 #2) → Task C. ✓
- No hardcoded IDs — all IDs/rewards/roles in Settings → Tasks B, C. ✓
- All new repo methods contract-tested across backends → Tasks B, C. ✓
- Placeholder scan: none. Type consistency: `record_target_use`/`get_target_total`/gate method names used consistently between interface, impls, and tests.

---

### Task D: AMP feature-rich custom template (GUI-configurable) — REQUESTED 2026-07-05

**Goal:** Replace stock "Python App Runner" reuse with a CUSTOM CubeCoders AMP template so an admin sets **Discord token, channel IDs, role IDs, storage backend (Database Type), and DB/Python version** directly in the **AMP web GUI** — AMP maps those GUI fields to env vars the bot reads via `pydantic-settings`. No manual `.env` editing.

**Files:**
- Create: `deploy/amp/n3x-bot.kvp` (metadata; based on python-app-runner.kvp; `App.EnvironmentVariables` maps `{{FieldName}}` → env vars: DISCORD_TOKEN, STORAGE_BACKEND, DATABASE_URL, all channel/role IDs, reminder time, gate config)
- Create: `deploy/amp/n3x-botconfig.json` (GUI field manifest: DiscordToken=password, StorageBackend=enum flatfile|sqlite|postgres, DatabaseUrl=text, TargetRoleId/WelcomeChannelId/ReminderChannelId/GateInput/GateStats/GateDeleteRole=text, ReminderTime, GateRewards, PythonVersion=enum, plus git repo download settings baked to this repo)
- Create: `deploy/amp/n3x-botupdates.json` (git-clone this repo + venv + requirements.txt install, ported from python-app-runner)
- Create: `deploy/amp/n3x-botports.json` (minimal/none — bot needs no inbound port)
- Update: `deploy/amp/README.md` (install the custom template vs stock)

**Design notes:**
- Reference format: CubeCoders/AMPTemplates `python-app-runner.{kvp,config.json,updates.json,ports.json}` (already studied). Map GUI fields to env vars via `App.EnvironmentVariables={"DISCORD_TOKEN":"{{DiscordToken}}", "STORAGE_BACKEND":"{{StorageBackend}}", ...}` so no `.env` file is required — the bot's `Settings` reads from the process env.
- "Database Type" = `StorageBackend` enum (flatfile/sqlite/postgres). "Version": for postgres this is an EXTERNAL DB (AMP's Python App Runner does not provision a DB) — so "version" here is the **Python version** selector (already a field in the stock template) plus a documented note that postgres must be provisioned separately (its version chosen there). CLARIFY with user whether they expect AMP to also spin up the Postgres instance (would be a separate AMP Postgres instance/template) before building.
- Secrets: DiscordToken uses `InputType: password`.

- [ ] Steps: TBD at implementation time — port the four python-app-runner files, remap the config manifest to our Settings fields, bake this repo as the git source, test the JSON manifests parse (jq), document in README. (This task is config/templating, not Python — no pytest; validation = JSON well-formed + field/env-var mapping matches `Settings` field names.)

**OPEN QUESTION for user (resolve before building Task D):** Should AMP also *provision* the Postgres database (separate AMP Postgres instance), or does "DB type + version" just mean selecting the backend + pointing `DATABASE_URL` at an externally-managed Postgres whose version is chosen outside AMP?
