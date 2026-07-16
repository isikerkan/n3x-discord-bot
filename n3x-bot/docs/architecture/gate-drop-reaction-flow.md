# Architecture: Reaction-icon gate-drop confirmation flow

Rework the d/e/z/k gate-input confirmation from the old ✅/❎ reactions (d/e/z)
and `KappaConfirmView` button panel (k) into a single unified flow: the bot
seeds the user's own message with one drop-icon reaction per drop item for that
gate PLUS a ❌ ("nothing") reaction, records a pending entry, and stores the
entry instantly when the message author clicks exactly one icon. After a
successful store the bot deletes the user's message and runs the usual
post-processing.

## Tests this design satisfies

`tests/test_gate_drop_reactions.py` (26 cases):
- `test_resolve_drop_emoji_returns_custom_when_guild_has_named_emoji` — resolver returns the `guild.emojis` entry whose `.name` matches the item's name.
- `test_resolve_drop_emoji_falls_back_to_unicode_when_emoji_absent` — no match → unicode fallback `str`.
- `test_resolve_drop_emoji_never_raises_on_none_guild` — `guild=None` → fallback `str`.
- `test_resolve_drop_emoji_per_item_name_lookup_is_independent` — lookup keys off the per-item name (`hercu` present ⇒ hercules custom, lf4u name `lf4` absent ⇒ fallback).
- `test_kappa_unicode_fallbacks_are_distinct_from_each_other_and_cross` — hercules/lf4u fallbacks distinct from each other and from ❌.
- `test_delta_input_adds_custom_emoji_when_guild_has_it` — d input stores nothing, adds a custom emoji named `prom`, ❌ last.
- `test_delta_input_adds_unicode_fallback_when_guild_lacks_emoji` — d input adds `[fallback_str, ❌]`, never raises.
- `test_delta_input_registers_pending_with_gate_type_and_options` — pending has `cost/user_id/username/gate_type` scalars + an options dict `{❌: None, drop_icon: "laser"}`.
- `test_epsilon_input_seeds_pending_and_single_drop_plus_cross` — e: pending gate_type "e", `[lf4_icon, ❌]`, nothing stored.
- `test_zeta_input_seeds_pending_and_single_drop_plus_cross` — z: gate_type "z", `[havoc_icon, ❌]`.
- `test_kappa_input_seeds_two_drops_plus_cross_and_sends_no_message` — k: gate_type "k", `[hercules_icon, lf4u_icon, ❌]`, `message.channel.send` NOT awaited.
- `test_delta_drop_click_stores_laser_dropped_true` — clicking `added[0]` stores `laser_dropped is True`, pending popped.
- `test_delta_nothing_click_stores_laser_dropped_false` — ❌ stores `laser_dropped is False`.
- `test_epsilon_drop_click_stores_lf4_true` — lf4 rate 100.0.
- `test_zeta_nothing_click_stores_havoc_false` — havoc rate 0.0.
- `test_kappa_hercules_click_stores_hercules_true_lf4u_false` — hercules 100.0, lf4u 0.0.
- `test_kappa_lf4u_click_stores_lf4u_true_hercules_false` — clicking `added[1]`: lf4u 100.0, hercules 0.0.
- `test_kappa_nothing_click_stores_both_false` — ❌: both 0.0.
- `test_message_deleted_after_successful_store` — `message.delete` awaited.
- `test_store_survives_delete_failure` — delete raises → no raise, store still happened, delete awaited.
- `test_non_author_reaction_is_ignored_then_author_can_store` — other user ignored (pending kept, no delete), author later stores.
- `test_unknown_message_reaction_is_ignored` — unknown message id no-op, real pending untouched.
- `test_non_option_emoji_is_ignored_and_pending_preserved` — 🎉 ignored, pending kept, no delete.
- `test_double_dispatch_stores_exactly_one` — concurrent drop+❌ dispatch stores exactly one row, pending claimed once.
- `test_on_raw_reaction_add_dispatches_to_drop_confirmation` — `on_raw_reaction_add` calls `handle_gate_drop_confirmation`.
- `test_store_invokes_post_processing` — embed/chart/`_announce_records` awaited; `check_achievements` called for `{gate_d, gate_total, gate_cost_total}`.

`tests/test_live_gate_charts.py` (rewritten cases):
- `test_gate_drop_confirmation_refreshes_gate_chart_d` — d confirmation writes `gate_chart_d`.
- `test_gate_drop_confirmation_refreshes_gate_chart_k` — k confirmation writes `gate_chart_k`.
- `test_kappa_store_processes_achievements_even_when_chart_fails` — chart raises inside its guard → k store survives, achievements still processed.

`tests/test_gate_input_help.py` (rewritten help copy):
- `test_help_drops_no_longer_confirmed_by_check_cross_reactions` — `"❎"` absent from help text.
- `test_help_no_longer_documents_kappa_buttons` — `"Button"` absent.
- (unchanged) title / Alpha / Delta+Laser / Epsilon+LF4 / Zeta+Havoc / Kappa+Hercules+LF4-U / `sofort` / (`Duplikat` or `30`) / determinism still pinned.

`tests/test_delta_gate.py`, `tests/test_ezk_gates.py` — obsolete ✅/❎/`KappaConfirmView`/`handle_delta_confirmation`/`_pending_delta` cases stripped; nothing to satisfy beyond the symbols being gone.

## Files to create

None. All changes are edits to existing modules.

## Files to modify

### `n3x_bot/gates.py`

Add module constants (near the existing `_GATE_DROP_ITEMS` at line 26 — keep
`_GATE_DROP_ITEMS`, `_DROP_LABELS`, `_drop_rate_lines` exactly as they are;
delta's embed still uses `laser_rate` and e/z/k drop-rate lines still use
`_GATE_DROP_ITEMS`):

- `GATE_DROP_REACTION_ITEMS = {"d": ["laser"], "e": ["lf4"], "z": ["havoc"], "k": ["hercules", "lf4u"]}`
  — per-gate ordered drop-item list for reaction seeding. NEW and distinct from
  `_GATE_DROP_ITEMS` (which is e/z/k-only and feeds the embed); do NOT overload.
- `DROP_EMOJI_NAMES = {"laser": "prom", "lf4": "lf4", "havoc": "havoc", "hercules": "hercu", "lf4u": "lf4"}`
  — item → custom-guild-emoji NAME (pinned by the tests).
- `DROP_EMOJI_FALLBACKS = {"laser": "🔫", "lf4": "🟦", "havoc": "🟪", "hercules": "🟩", "lf4u": "🔷"}`
  — item → fixed unicode fallback. All five distinct and none equal to ❌.
  Load-bearing constraints (only these are asserted): hercules ≠ lf4u, both ≠ ❌,
  and each drop fallback ≠ ❌. The full-distinct choice above is a superset that
  also avoids any within-gate reaction collision. Coder may pick other glyphs as
  long as those constraints hold.
- `DROP_NOTHING_EMOJI = "❌"` — the "no drop" reaction.

Add the pure resolver:

- `resolve_drop_emoji(guild, item) -> discord.Emoji | str`
  - `name = DROP_EMOJI_NAMES[item]`.
  - if `guild is not None`: iterate `guild.emojis`, return the first whose
    `.name == name`.
  - otherwise (or no match) return `DROP_EMOJI_FALLBACKS[item]`.
  - Never raises (no attribute access on `None`; the `guild is not None` guard
    handles `guild=None`).

Remove:

- The entire `KappaConfirmView` class (lines 151–258). Its only in-module use of
  `discord.ui` goes away; keep `import discord` (still used by `build_gate_embed`
  for `discord.Embed`/`discord.Color` and by the `resolve_drop_emoji` return
  annotation). No other imports become unused.

### `n3x_bot/bot.py`

1. **Import (lines 38–41).** Drop `KappaConfirmView`; add the new symbols:
   ```
   from n3x_bot.gates import (
       build_gate_embed, parse_gate_message, changed_records, GATE_NAMES,
       parse_de_date, resolve_drop_emoji, GATE_DROP_REACTION_ITEMS,
       DROP_NOTHING_EMOJI,
   )
   ```

2. **`build_bot` (line 112).** Rename `bot._pending_delta = {}` → `bot._pending_gate = {}`.

3. **`handle_gate_input_message` (lines 645–661).** Replace the two branches
   (`if gate_type in ("d","e","z")` and `if gate_type == "k"`) with ONE unified
   branch covering d/e/z/k. a/b/c fall through UNCHANGED (lines 662–684 stay).
   New branch behavior:
   - `items = GATE_DROP_REACTION_ITEMS[gate_type]`.
   - Build an `options` dict and an ordered `reactions` list:
     for each `item` in `items`: `emoji = resolve_drop_emoji(message.guild, item)`;
     set `options[str(emoji)] = item`; append `emoji` to `reactions`.
     Then `options[DROP_NOTHING_EMOJI] = None` and append `DROP_NOTHING_EMOJI`.
   - Record pending BEFORE adding reactions:
     `bot._pending_gate[message.id] = {"cost": cost, "user_id": message.author.id, "username": message.author.name, "gate_type": gate_type, "options": options}`.
     (`gate_type` is now stored for ALL of d/e/z/k, including d — the old code
     omitted it for d.)
   - Add the reactions to the user's message in `reactions` order inside a single
     `try/except Exception: pass` (drop-icons first, ❌ last).
   - `return`. Do NOT store, do NOT delete, do NOT `message.channel.send`.

4. **Replace `handle_delta_confirmation` (lines 687–740) with
   `handle_gate_drop_confirmation(bot, repo, settings, payload)`.** Body order
   (mirrors the old atomic-pop pattern; keying by `str(emoji)`):
   - `pending = bot._pending_gate.get(payload.message_id)`; if `None`: return.
   - `options = pending["options"]`; `key = str(payload.emoji)`;
     if `key not in options`: return (leave pending intact — no store, no delete).
   - if `payload.user_id != pending["user_id"]`: return.
   - **Atomic claim:** `pending = bot._pending_gate.pop(payload.message_id, None)`;
     if `pending is None`: return. (No `await` between the `get`/membership/user
     guards and this `pop`, so a double dispatch stores exactly once.)
   - `chosen = pending["options"][key]` (an item name, or `None` for ❌).
   - `gate_type = pending["gate_type"]`; unpack `cost/user_id/username`.
   - `before = await repo.gate_record(gate_type)`.
   - Build the per-gate drop payload with exactly `chosen` True:
     - `d`: `await repo.add_gate_entry("d", cost, user_id, username, laser_dropped=(chosen == "laser"))`.
     - `e`: `... drops={"lf4": chosen == "lf4"}`.
     - `z`: `... drops={"havoc": chosen == "havoc"}`.
     - `k`: `... drops={"hercules": chosen == "hercules", "lf4u": chosen == "lf4u"}`.
     (❌ ⇒ `chosen is None` ⇒ all comparisons False.)
     Assign result to `inserted`.
   - `if inserted:` (see ordering section below):
     1. **Delete (guarded, its own try/except):**
        `channel = bot.get_channel(payload.channel_id)`;
        `msg = await channel.fetch_message(payload.message_id)`;
        `await msg.delete()`. Swallow any exception.
     2. **Post-processing (same as the old handler, lines 724–740):**
        `after = await repo.gate_record(gate_type)`;
        `await _announce_records(bot, settings, gate_type, changed_records(before, after), after)`;
        `await update_gate_stats_embed(bot, repo, settings)`;
        `try: await update_gate_chart(bot, repo, settings, gate_type) except Exception: pass`;
        `member = getattr(payload, "member", None)`;
        `newly = check_achievements(gate_{type}) + gate_total + gate_cost_total`;
        `if newly: try: await announce_achievements(bot, settings, member, newly) except Exception: pass`.

5. **`on_raw_reaction_add` (line 941).** Swap the
   `await handle_delta_confirmation(bot, repo, settings, payload)` call for
   `await handle_gate_drop_confirmation(bot, repo, settings, payload)`. Keep the
   surrounding `try/except Exception: pass` wrapper unchanged.

6. **`build_gate_input_help` (lines 308–309).** Replace the two hint lines
   ```
   "• Delta, Epsilon & Zeta bestätigen: ✅ (Drop erhalten) / ❎ (kein Drop)\n"
   "• Kappa bestätigen: nutze die Buttons (Hercules / LF4-U)\n"
   ```
   with copy describing the new icon flow, e.g.:
   ```
   "• Delta, Epsilon, Zeta & Kappa: reagiere auf deine eigene Nachricht mit dem Drop-Icon, das du erhalten hast (oder ❌ für keinen Drop)\n"
   "• Der Bot trägt den Drop dann ein und entfernt deine Nachricht\n"
   ```
   Constraints the tests pin: NO `❎` and NO `Button` anywhere in the rendered
   text; keep `sofort` (line 307) and the `Duplikat`/`30` line (310); keep the
   title and the example lines (Alpha/Delta+Laser/Epsilon+LF4/Zeta+Havoc/
   Kappa+Hercules+LF4-U). Leave the rest of the embed structure intact.

## Data flow

Representative trace — user posts `k 500` in the gate-input channel, then clicks
the hercules icon:

1. `on_message` → `handle_gate_input_message(bot, repo, settings, message)`.
2. `parse_gate_message("k 500")` → `("k", 500)`.
3. Unified drop branch: `items = ["hercules", "lf4u"]`.
   - `resolve_drop_emoji(message.guild, "hercules")` → custom emoji named `hercu`
     if present, else `"🟩"`. `resolve_drop_emoji(..., "lf4u")` → custom `lf4`
     if present, else `"🔷"`.
   - `options = {str(hercu): "hercules", str(lf4u): "lf4u", "❌": None}`.
   - `bot._pending_gate[message.id] = {cost:500, user_id, username, gate_type:"k", options}`.
   - `message.add_reaction(hercu)`, `add_reaction(lf4u)`, `add_reaction("❌")`.
4. User clicks the hercules reaction → `on_raw_reaction_add(payload)` →
   `handle_gate_drop_confirmation(bot, repo, settings, payload)`.
5. `pending` found; `key = str(payload.emoji)` == `str(hercu)` ∈ options; author
   matches; atomic `pop`; `chosen = "hercules"`.
6. `add_gate_entry("k", 500, uid, name, drops={"hercules": True, "lf4u": False})`
   → `inserted=True`.
7. Delete: `bot.get_channel(payload.channel_id).fetch_message(payload.message_id).delete()`
   (guarded).
8. Post-processing: `_announce_records`, `update_gate_stats_embed`,
   `update_gate_chart(..., "k")` (guarded), `check_achievements` ×3,
   `announce_achievements` if any.

`str(emoji)` keying consistency: at seed time the options map is keyed by
`str(resolved_emoji)` and the exact `resolved_emoji` object is passed to
`add_reaction` (so it round-trips into `payload.emoji`). At confirm time the
matcher computes `str(payload.emoji)`. For a custom `discord.Emoji` both sides
render `<:name:id>` (the `_FakeEmoji` stand-in reproduces this in `__str__`);
for a unicode fallback the value is a plain `str`, so `str(fallback) == fallback`
and the seeded key equals the clicked key. ❌ is a literal `str`, keyed and
matched as `"❌"`.

## Dependencies

- New packages: none.
- Internal modules: `n3x_bot.gates` (new `resolve_drop_emoji`,
  `GATE_DROP_REACTION_ITEMS`, `DROP_NOTHING_EMOJI`, `DROP_EMOJI_NAMES`,
  `DROP_EMOJI_FALLBACKS`); `n3x_bot.storage` `add_gate_entry`/`gate_record`/
  `gate_drop_stats`/`list_gate_costs` (existing, unchanged signatures — verified
  `add_gate_entry(gate_type, cost, user_id, username, dedup_window_seconds=30, laser_dropped=None, drops=None)`);
  `update_gate_stats_embed`, `update_gate_chart`, `_announce_records`,
  `check_achievements`, `announce_achievements` (existing, patched at module
  scope by the tests — call them as module-level names so monkeypatch works).

## Build sequence (for the Coder)

1. **`gates.py` constants + resolver.** Add `GATE_DROP_REACTION_ITEMS`,
   `DROP_EMOJI_NAMES`, `DROP_EMOJI_FALLBACKS`, `DROP_NOTHING_EMOJI`, and
   `resolve_drop_emoji`. (Unblocks the 5 resolver tests immediately.)
2. **`gates.py` removal.** Delete `KappaConfirmView`. Leave `import discord`,
   `_GATE_DROP_ITEMS`, `_DROP_LABELS`, `_drop_rate_lines` untouched.
3. **`bot.py` import line.** Drop `KappaConfirmView`, add the three new gates
   symbols.
4. **`bot.py` `build_bot`.** Rename `_pending_delta` → `_pending_gate`.
5. **`bot.py` `handle_gate_input_message`.** Replace the d/e/z and k branches
   with the unified drop branch. (Unblocks the input-seeding tests.)
6. **`bot.py` new `handle_gate_drop_confirmation`** replacing
   `handle_delta_confirmation`. (Unblocks the confirmation, guard, atomic,
   delete, post-processing, and chart tests.)
7. **`bot.py` `on_raw_reaction_add`.** Swap the handler call.
8. **`bot.py` `build_gate_input_help`.** Update the German hint lines.
9. Run the four affected test files; then the full gate suite to catch
   regressions in `test_delta_gate.py` / `test_ezk_gates.py`.

## Delete-vs-post-processing ordering

Order: **store → (if inserted) delete [own try/except] → post-processing
[records, embed, chart in its own try/except, achievements]**.

- `test_store_survives_delete_failure`: delete raises but is wrapped in its own
  try/except placed AFTER the store, so the store persists and the handler does
  not propagate — passes regardless of whether post-processing runs.
- `test_kappa_store_processes_achievements_even_when_chart_fails`: the chart
  refresh keeps its own inner `try/except` (as in the a/b/c and old delta sites),
  so a chart failure does not skip the achievement checks that follow it.

Both delete and chart are independently guarded, so either failing cannot skip
the store or the achievement processing. The store must come first so its result
(`inserted`) gates the delete and post-processing.

## Risks and open questions

- **`str(emoji)` custom-emoji round-trip is only exercised with the unicode
  fallback in the *dispatch* tests.** Every confirmation/dispatch test uses
  `_fake_guild()` (no custom emojis), so `str(payload.emoji)` matching against a
  real `<:name:id>` custom-emoji key is validated only indirectly (via the
  `_FakeEmoji.__str__` in seeding tests, never through a full click round-trip).
  The design keys both sides off `str(...)`, which is correct for real
  `discord.Emoji` too, but this specific path is not end-to-end covered. Flagging,
  not designing around it.
- **Delete targets `bot.get_channel(payload.channel_id).fetch_message(...)`.**
  In `test_live_gate_charts.py` `bot.get_channel` is a `MagicMock` returning the
  chart channel for ANY argument, and those tests set `fetched.delete = AsyncMock()`
  so the delete resolves. In `test_gate_drop_reactions.py` `bot.get_channel`
  returns a channel whose `fetch_message` returns the original message object.
  Both wirings satisfy the same code path. No ambiguity, but the coder must fetch
  via the channel (not reuse a cached message handle) to match both harnesses.
- **Stale comments only (no code stragglers).** `n3x_bot/config_commands.py:9`
  and `tests/test_config_commands.py:43` mention `KappaConfirmView` in comments
  describing an author-lock pattern. They are not imports or calls and do not
  break; optionally reword the `config_commands.py` comment to drop the dangling
  reference. No runtime references to `KappaConfirmView`, `handle_delta_confirmation`,
  or `_pending_delta` remain after the edits above (grep-verified).
- **`_GATE_DROP_ITEMS` vs `GATE_DROP_REACTION_ITEMS` divergence is intentional.**
  The embed/drop-rate path (`_drop_rate_lines`) is e/z/k-only and must stay that
  way (delta's embed uses `laser_rate` directly); the reaction path adds `d`.
  Keeping them as two separate constants is deliberate — do not merge.
