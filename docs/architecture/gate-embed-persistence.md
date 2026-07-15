# Architecture: gate-embed persistence (`channel_messages` store)

## Problem
`update_gate_stats_embed` (`n3x_bot/bot.py`, current lines 262-289) tracks the live
gate-stats embed message id only in-memory on `bot._gate_embed_msg_id` (init `None`
at line 96). On restart that resets to `None`, so the bot `channel.send`s a brand-new
embed instead of editing the existing one. `stat_last_post` cannot hold the id because
it is FK-bound to a real `stats` row and `"gate"` is not one (`set_last_post` KeyErrors).
Fix: add a NON-FK keyed store `channel_messages`, persist the id under `"gate_stats"`,
and edit-in-place across restarts while KEEPING `_gate_embed_msg_id` as an in-run cache.

## Tests this design satisfies

### `tests/storage/test_channel_messages_contract.py` (parametrized json / sqlite / postgres)
- `test_get_channel_message_unknown_key_returns_none` — `get_channel_message` on empty repo returns `None`.
- `test_set_channel_message_roundtrips` — set then get returns `(42, 555)`.
- `test_get_channel_message_returns_int_tuple` — both elements are `int` (SQL must coerce).
- `test_set_channel_message_upserts_same_key` — re-setting same key overwrites → `(99, 777)`.
- `test_multiple_keys_are_independent` — distinct keys stored/read independently.
- `test_set_one_key_leaves_other_keys_unset` — unset key returns `None`.
- `test_channel_message_preserves_large_snowflake_ids` — BigInteger; `>2**53` survives.
- `test_export_all_includes_channel_messages_and_is_json_serializable` — snapshot has the table and `json.dumps` works.
- `test_round_trip_preserves_channel_messages` — export→import into fresh repo preserves values.
- `test_snapshot_is_stable_after_channel_message_round_trip` — `dest.export_all() == snapshot`.
- `test_clear_wipes_channel_messages` — `clear()` empties the table.

### `tests/test_gate_embed_persistence.py` (flatfile repo + wiring)
- `test_first_post_persists_channel_message_in_repo` — first call sends once AND `get_channel_message("gate_stats") == (42, 555)`.
- `test_second_call_edits_persisted_message_without_new_send` — 2nd call in same run `fetch_message(42)` + `edit`, only one `send`, persisted id unchanged `(42, 555)`.
- `test_restart_edits_persisted_message_instead_of_reposting` — pre-seeded `(42,555)`, fresh bot: `fetch_message(42)` + `edit`, `send` NOT called.
- `test_restart_reposts_and_repersists_when_stored_message_gone` — pre-seeded `(42,555)`, `fetch_message` raises → `send` once → repersist `(99, 555)`.
- `test_noop_when_channel_unset_persists_nothing` — `gate_stats_channel_id=0`: `get_channel` not called, persists nothing.
- `test_noop_when_channel_missing_persists_nothing` — `get_channel` returns `None`: persists nothing.

### `tests/test_bot_wiring.py` — MUST STAY GREEN (the 3 in-memory-cache assertions)
- `test_update_gate_stats_embed_first_post_sends_and_records_id` — asserts `bot._gate_embed_msg_id == 42`.
- `test_update_gate_stats_embed_second_call_edits_existing_message` — `fetch_message(42)` + `edit`, one `send`.
- `test_update_gate_stats_embed_falls_back_to_new_post_if_edit_fails` — after fetch failure + resend, asserts `bot._gate_embed_msg_id == 99`.

## Files to modify

### 1. `n3x_bot/storage/schema.py`
Append a new table def after `base_timers` (after line 118), mirroring `kodex_messages`
(BigInteger PK style) and `base_timers` (String PK, single-purpose keyed table):
```
channel_messages = Table(
    "channel_messages", metadata,
    Column("key", String(50), primary_key=True),
    Column("message_id", BigInteger, nullable=False),
    Column("channel_id", BigInteger, nullable=False),
)
```
`String`/`BigInteger`/`Table`/`Column` are already imported (line 1-4). No import change.
`String(50)` matches the width used by `stats.key`; `BigInteger` matches `kodex_messages`
snowflake columns so `>2**53` ids survive.

### 2. `n3x_bot/storage/base.py`
Add two abstract methods to `StatsRepository`. Place them right after the `set_last_post`
abstractmethod block (after line 85), before the `# target tracking` comment, under a new
`# channel messages` sub-header — mirroring the `get_last_post`/`set_last_post` pairing:
```
    # channel messages
    @abstractmethod
    async def set_channel_message(self, key: str, message_id: int,
                                  channel_id: int) -> None:
        """Upsert the (message_id, channel_id) tracked under `key`.

        A NON-FK keyed store for live single-message embeds whose key is not a
        real `stats` row (e.g. "gate_stats"), so `set_last_post` can't hold it.
        """
        ...
    @abstractmethod
    async def get_channel_message(self, key: str) -> tuple[int, int] | None:
        """`(message_id, channel_id)` for `key`, or None if unset. Both ints."""
        ...
```

### 3. `n3x_bot/storage/json_repo.py`
Mirror the `stat_last_post` idiom exactly (values stored as a 2-element list of ints,
keyed by the string key — no FK indirection needed since `key` is already the map key).

- **`_empty()` (lines 25-35):** add `"channel_messages": {}` to the returned dict
  (put it next to `"kodex_messages": {}` / `"base_timers": {}`). `connect()` already
  `setdefault`s every `_empty()` key onto older files (lines 41-42), so existing json
  files gain the table automatically.
- **New methods** — place after `set_last_post` (after line 254), before `# target tracking`:
  ```
  async def set_channel_message(self, key, message_id, channel_id):
      self._db["channel_messages"][key] = [message_id, channel_id]
      self._flush()

  async def get_channel_message(self, key):
      v = self._db["channel_messages"].get(key)
      return (v[0], v[1]) if v else None
  ```
  Returns a tuple of the stored ints (already ints in JSON → satisfies the int-tuple test).
  Assigning to `self._db["channel_messages"][key]` is the upsert.
- **`export_all()` (lines 448-485):** add `"channel_messages": copy.deepcopy(self._db["channel_messages"]),`
  to the returned dict (next to the `"base_timers"` entry, line 478). Deepcopy of a
  `{str: [int, int]}` dict is JSON-serializable and stable across round-trip (lists stay
  lists), satisfying the stability test.
- **`import_all()` (lines 487-506):** add
  `self._db["channel_messages"] = copy.deepcopy(snapshot.get("channel_messages", {}))`
  next to the `base_timers` import (line 504). Use `.get(..., {})` for forward-compat with
  older snapshots, matching the pattern used for `activity_counters`/`base_timers`.
- **`clear()` (lines 508-510):** no change — it reassigns `self._db = self._empty()`, which
  now includes the empty `channel_messages`.

### 4. `n3x_bot/storage/sql_repo.py`
Mirror `set_last_post` (exists-check upsert) for the write and `get_kodex_message_user`
(`int(...)` coercion) for the read.

- **New methods** — place after `set_last_post` (after line 303), before `# target tracking`:
  ```
  async def set_channel_message(self, key, message_id, channel_id):
      async with self.engine.begin() as conn:
          exists = (await conn.execute(select(sc.channel_messages.c.key)
                    .where(sc.channel_messages.c.key == key))).one_or_none()
          if exists is None:
              await conn.execute(insert(sc.channel_messages).values(
                  key=key, message_id=message_id, channel_id=channel_id))
          else:
              await conn.execute(update(sc.channel_messages)
                                 .where(sc.channel_messages.c.key == key)
                                 .values(message_id=message_id, channel_id=channel_id))

  async def get_channel_message(self, key):
      async with self.engine.connect() as conn:
          r = (await conn.execute(
              select(sc.channel_messages.c.message_id,
                     sc.channel_messages.c.channel_id)
              .where(sc.channel_messages.c.key == key))).one_or_none()
          return (int(r.message_id), int(r.channel_id)) if r else None
  ```
  `int(...)` coercion is required (SQLite BigInteger can read back as a plain int already,
  but the explicit cast matches `get_kodex_message_user`/`gate_record` and guarantees the
  int-tuple contract). No unlike `set_last_post`, there is NO FK/stat lookup and NO KeyError
  path — the key IS the primary key.
- **`export_all()` (lines 628-723):** add a `channel_messages` comprehension inside the
  `connect()` block (next to `base_timers`, lines 704-707):
  ```
  channel_messages = {
      r.key: [int(r.message_id), int(r.channel_id)]
      for r in await conn.execute(select(sc.channel_messages))
  }
  ```
  and add `"channel_messages": channel_messages,` to the returned dict (next to
  `"base_timers"`, line 721). Value shape `{key: [msg, chan]}` matches the json backend so
  cross-backend snapshots and the stability test agree.
- **`import_all()` (lines 725-797):** add a loop next to the `base_timers` import (lines 787-790):
  ```
  for key, v in snapshot.get("channel_messages", {}).items():
      await conn.execute(insert(sc.channel_messages).values(
          key=key, message_id=v[0], channel_id=v[1]))
  ```
  `.get(..., {})` for forward-compat, matching the other tables.
- **`clear()` (lines 799-808):** add `sc.channel_messages` to the delete tuple (append after
  `sc.base_timers`). No FK ordering constraint — it references nothing.

### 5. `n3x_bot/bot.py` — the wiring rewrite
- **Module constant:** add `GATE_STATS_KEY = "gate_stats"` at the top of the
  `# ── gate tracker ──` section (just above `update_gate_stats_embed`, ~line 260-261).
- **KEEP line 96** `bot._gate_embed_msg_id = None` in `build_bot` — it remains the in-run
  fast-path cache; the wiring tests assert on it.
- **Rewrite `update_gate_stats_embed` (lines 262-289)** to the following flow (replace the
  docstring too — it currently documents the buggy in-memory-only behavior):
  ```
  async def update_gate_stats_embed(bot, repo, settings):
      if not settings.gate_stats_channel_id:
          return
      channel = bot.get_channel(settings.gate_stats_channel_id)
      if channel is None:
          return
      totals = await repo.gate_totals()
      rewards = settings.gate_rewards_map()
      delta = await repo.delta_stats()
      now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
      embed = build_gate_embed(totals, rewards, now_str, delta)

      # Resolve the target message id: in-run cache first (fast path), else the
      # persisted store (survives restart).
      target_id = bot._gate_embed_msg_id
      if target_id is None:
          stored = await repo.get_channel_message(GATE_STATS_KEY)
          if stored is not None:
              target_id = stored[0]

      if target_id is not None:
          try:
              msg = await channel.fetch_message(target_id)
              await msg.edit(embed=embed)
              bot._gate_embed_msg_id = target_id   # keep the cache warm
              return
          except Exception:
              pass

      new_msg = await channel.send(embed=embed)
      bot._gate_embed_msg_id = new_msg.id                       # in-run cache
      await repo.set_channel_message(GATE_STATS_KEY, new_msg.id, channel.id)  # persisted
  ```
  Key properties:
  - Resolve order = cache → persisted store. The two early `return`/`noop` guards run
    BEFORE any repo access, so the noop tests persist nothing and (for `channel_id=0`)
    never call `get_channel`.
  - Edit-success path does NOT re-write the store → satisfies "editing must NOT churn the
    persisted id" (`test_second_call_edits_persisted_message_without_new_send`).
  - Send path writes BOTH the cache and the persisted store → first-post + fetch-fail-repost
    tests see the new id in the repo AND the cache assertions in `test_bot_wiring.py` hold.

## Data flow (restart scenario, the regression under fix)
1. Prior run posted the embed; `set_channel_message("gate_stats", 42, 555)` wrote a row.
2. Process restarts; `build_bot` sets `bot._gate_embed_msg_id = None`.
3. `update_gate_stats_embed` runs: channel resolves; `target_id = None` (cache) →
   `get_channel_message("gate_stats")` → `(42, 555)` → `target_id = 42`.
4. `channel.fetch_message(42)` succeeds → `msg.edit(embed=...)` → cache set to 42 → return.
   No new `send`, so the embed is edited in place instead of duplicated.
5. If step 4's fetch raises (message deleted): fall through → `channel.send` returns id 99 →
   cache = 99 → `set_channel_message("gate_stats", 99, 555)` repersists the fresh id.

## Dependencies
- New packages: NONE. Uses existing SQLAlchemy Core (`insert/update/select`) and the json
  in-memory dict pattern already present in both repos.
- Internal: `n3x_bot/storage/schema.py` (new `channel_messages` Table), consumed by
  `sql_repo.py`; `bot.py` depends on the new `StatsRepository.{get,set}_channel_message`.

## Build sequence (for the Coder)
1. **`schema.py`** — add `channel_messages` Table. (Greens nothing yet; enables SQL repo.)
2. **`base.py`** — add the two abstractmethods. (Makes the interface real; without a concrete
   impl the repos would be abstract — do steps 3/4 in the same pass before running tests.)
3. **`json_repo.py`** — `_empty` key + `set/get` methods + export/import entries.
   → Greens the FULL `test_channel_messages_contract.py` under the `json` backend param,
   AND unblocks `test_gate_embed_persistence.py` (flatfile) repo calls.
4. **`sql_repo.py`** — `set/get` methods + export/import/clear entries.
   → Greens `test_channel_messages_contract.py` under `sqlite` (and `postgres` when
   `TEST_POSTGRES_URL` set).
5. **`bot.py`** — add `GATE_STATS_KEY`; rewrite `update_gate_stats_embed`.
   → Greens all of `test_gate_embed_persistence.py`; keeps the 3 `_gate_embed_msg_id`
   assertions in `test_bot_wiring.py` green (cache still written on send/edit).
6. Run `tests/storage/test_channel_messages_contract.py`, `tests/test_gate_embed_persistence.py`,
   and `tests/test_bot_wiring.py` together to confirm no regression.

## Confirming the 3 existing `_gate_embed_msg_id` wiring tests stay green
- `first_post_sends_and_records_id`: fresh cache `None`, empty store → `target_id None` →
  `send` → `bot._gate_embed_msg_id = 42`. Assertion `== 42` holds; `send` awaited once;
  embed title unaffected. ✓
- `second_call_edits_existing_message`: call 1 sets cache=42; call 2 takes the cache fast
  path → `fetch_message(42)` (awaited once) → `edit` (once); `send` awaited once total. ✓
- `falls_back_to_new_post_if_edit_fails`: call 1 sets cache=42; call 2 `fetch_message(42)`
  raises → falls through → `send` returns 99 → `bot._gate_embed_msg_id = 99`. Assertion
  `== 99` holds. ✓

## Recommended (NOT test-required) follow-up
- `n3x_bot/migrate.py` `_DATA_TABLES` (lines 25-30) lists data-bearing tables for the
  "destination non-empty" guard. Add `"channel_messages"` for migration fidelity so a
  destination holding only a channel-message row is correctly treated as non-empty. No test
  in this handoff exercises it, so it is out of the strict red→green scope — flagging it so
  the coder/reviewer decides. Consistent with `base_timers` already being listed there.

## Risks and open questions
- **Edit path leaves a stale persisted id when the message is later deleted mid-run.** If the
  stored message is deleted between two same-run edits, the first edit after deletion hits the
  `except` branch, reposts, and repersists — self-healing. No test covers a stale-store edit
  loop; behavior matches the analogous `_send_or_update` swallow-and-repost pattern. No change
  recommended.
- **`get_channel_message` on json returns the live stored list wrapped in a fresh tuple**, so
  callers can't mutate internal state; matches `get_last_post`. Fine.
- **Cross-backend snapshot equality** relies on both backends emitting `{key: [msg, chan]}`
  (lists, ints). The contract's `make_repo` uses the SAME backend as `repo`, so strict
  equality is only exercised within a backend — but keeping the shapes identical (as
  specified) also keeps `migrate.py` json↔sql transfers correct.
- **No ambiguity found in the TDD assertions.** The one design tension the handoff called out
  (keep vs. delete `_gate_embed_msg_id`) is resolved exactly as instructed: keep it as an
  in-run cache written alongside the persisted store. I would not change any TDD assumption.
