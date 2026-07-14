# Architecture: Achievements Pass C (voice-tier roles, paginated overview, additive sync)

## Tests this design satisfies

**`tests/test_voice_roles.py`**
- `test_voice_role_map_empty_string_is_empty_dict`
- `test_voice_role_map_parses_populated_string`
- `test_voice_role_map_ignores_malformed_entries`
- `test_transition_grants_highest_newly_mapped_and_lists_others`
- `test_transition_collapses_multiple_newly_tiers_to_highest`
- `test_transition_returns_none_when_no_mapped_voice_in_newly`
- `test_transition_returns_none_for_empty_role_map`
- `test_apply_grants_new_role_and_revokes_lower_held_role`
- `test_apply_does_not_remove_unheld_mapped_roles`
- `test_apply_is_noop_for_non_voice_unlock`
- `test_apply_is_noop_when_role_map_empty`
- `test_apply_is_best_effort_when_add_roles_raises`

**`tests/test_overview.py`**
- `test_build_overview_embed_page_zero_shows_first_user_count_over_total`
- `test_build_overview_embed_page_one_shows_second_user_count`
- `test_build_overview_embed_out_of_range_page_wraps_to_first`
- `test_build_overview_embed_shows_page_indicator`
- `test_post_overview_sends_embed_and_two_nav_reactions`
- `test_post_overview_records_message_id_in_overview_state`
- `test_post_overview_is_noop_when_channel_unset`
- `test_post_overview_is_noop_when_no_holders`
- `test_reaction_forward_advances_page_and_edits_embed`
- `test_reaction_in_unrelated_channel_is_ignored`
- `test_reaction_with_unrelated_emoji_is_ignored`
- `test_build_bot_registers_overview_command`
- `test_overview_command_triggers_post_overview`

**`tests/test_achievement_sync.py`**
- `test_recompute_records_all_threshold_met_metrics`
- `test_recompute_is_idempotent_on_second_run`
- `test_recompute_is_additive_and_never_wipes_existing`
- `test_sync_all_processes_every_user_with_data`
- `test_sync_all_is_idempotent_second_run_adds_zero`
- `test_sync_all_backfills_missing_row_for_over_threshold_user`
- `test_build_bot_registers_sync_achievements_command`
- `test_sync_command_refuses_non_admin_and_mutates_nothing`
- `test_sync_command_admin_records_threshold_met_achievements`
- `test_sync_command_admin_second_run_adds_nothing_new`

**`tests/test_activity.py`**
- `test_reaction_skipped_in_overview_channel` (B9 skip-set extension)

## Files to modify (NO new files, NO new config fields, NO new storage methods)

### `n3x_bot/config.py`
Add ONE method to `Settings`, mirroring the existing `gate_rewards_map()` (lines 58-64):
- `def voice_role_map(self) -> dict[str, int]` — parse `self.voice_achievement_roles`
  (format `"voice_36000:111,voice_180000:222"`) into `{achievement_id: role_id}`.
  Parse rules (must match `test_voice_role_map_*`):
  - Split on `,`. For each token, require exactly one `:` (skip a bare token with no colon).
  - `k, v = token.split(":", 1)`; `strip()` the key. Attempt `int(v)`; on `ValueError`
    skip that entry (non-int role id like `"abc"` is dropped).
  - Empty string → `{}` (the `split(",")` on `""` yields `[""]`, which has no colon → skipped).
  - Well-formed entries survive even when siblings are malformed.
  Reuse the `gate_rewards_map` shape but wrap the `int(v)` in try/except (gate_rewards_map
  does not — voice map must tolerate garbage per the test).

### `n3x_bot/activity.py`
Two new symbols + wiring into the two existing voice unlock paths.

- `def voice_role_transition(newly_ids: list[str], role_map: dict[str, int]) -> tuple[int | None, list[int]]`
  — PURE, no Discord, no async. Algorithm:
  1. `mapped = [aid for aid in newly_ids if aid in role_map]`. If empty → `return (None, [])`.
  2. Rank `mapped` by `Achievement.threshold` of the matching def (look up via
     `next(a for a in ACHIEVEMENTS if a.id == aid).threshold`). Pick the id with the
     HIGHEST threshold → `grant = role_map[highest_id]`.
  3. `others = [rid for aid, rid in role_map.items() if rid != grant]` — every OTHER
     mapped role id (to be revoked). Return `(grant, others)`.
  - `test_transition_collapses_multiple_newly_tiers_to_highest`: newly `["voice_3600","voice_36000"]`
    → grant `902` (36000 outranks 3600), others `{901, 903}`.

- `async def apply_voice_roles(bot, settings: Settings, member, newly: list[Achievement]) -> None`
  — best-effort, never raises. Algorithm:
  1. `role_map = settings.voice_role_map()`. If falsy → return (noop; `test_apply_is_noop_when_role_map_empty`).
  2. `grant_id, other_ids = voice_role_transition([a.id for a in newly], role_map)`.
     If `grant_id is None` → return (noop for non-voice unlock; `test_apply_is_noop_for_non_voice_unlock`).
  3. Wrap the Discord calls in a single `try/except Exception: pass` (best-effort;
     `test_apply_is_best_effort_when_add_roles_raises`):
     - `grant_role = member.guild.get_role(grant_id)`; if not None → `await member.add_roles(grant_role)`.
     - Resolve each `other_ids` via `member.guild.get_role`; keep only those the member
       actually holds: `held = {r.id for r in member.roles}`; `to_remove = [role for rid in other_ids
       if (role := member.guild.get_role(rid)) is not None and rid in held]`.
       Only call `await member.remove_roles(*to_remove)` when `to_remove` is non-empty
       (`test_apply_does_not_remove_unheld_mapped_roles` asserts remove_roles is either
       not awaited or awaited with no args).
  - Import note: `ACHIEVEMENTS` is already imported at top (line 8 imports `Achievement,
    check_achievements`); add `ACHIEVEMENTS` to that import.

- **Wiring (call sites — alongside the existing announce, same two paths):**
  - `handle_voice_state_update` (lines 125-131): inside `if credited:` after the announce
    try/except, add a best-effort `await apply_voice_roles(bot, settings, member, newly)`
    (guarded so a role failure never breaks the announce, and vice-versa — either its own
    try/except or rely on apply_voice_roles being internally best-effort; it is, so a bare
    call is fine).
  - `flush_voice_times` (lines 163-175): inside the `for member_id in credited:` loop,
    after the resolved `member is not None` announce block, add
    `await apply_voice_roles(bot, settings, member, newly)` (member already resolved via
    the `bot.guilds` / `get_member` scan).

- **B9 skip-set (line 183):** extend the channel tuple in `handle_activity_reaction` to
  include `settings.overview_channel_id`:
  `if payload.channel_id in (settings.gate_input_channel_id, settings.gate_stats_channel_id,
  settings.overview_channel_id):` (`test_reaction_skipped_in_overview_channel`).

### `n3x_bot/achievements.py`
Five new symbols. `import discord` and `commands` already present. Add `is_admin` import
from `n3x_bot.admin` INSIDE the command-registration function (deferred, to avoid any
import-cycle risk — mirrors admin.py's deferred bot import) OR at top level if no cycle
exists (admin.py imports config/models/storage only, not achievements, so a top-level
`from n3x_bot.admin import is_admin` is safe — prefer top-level).

- `def build_overview_embed(holders: dict[int, set[str]], user_ids: list[int], page: int) -> discord.Embed`
  — PURE. Algorithm:
  1. `idx = page % len(user_ids)` (wrap; `test_..._out_of_range_page_wraps_to_first`).
  2. `uid = user_ids[idx]`; `count = len(holders.get(uid, set()))`.
  3. Render text containing `f"{count}/{TOTAL_ACHIEVEMENTS}"` somewhere in title/description/
     fields (the tests flatten all embed text via `_embed_text`).
  4. Progress bar from `count/TOTAL_ACHIEVEMENTS` (e.g. filled/empty block glyphs `█`/`░`,
     ~10 segments — cosmetic, not asserted).
  5. Page indicator using 1-BASED page and total user count:
     `f"Seite {idx + 1}/{len(user_ids)}"` (`test_..._shows_page_indicator` asserts both
     `"1"` and `"2"` appear for page 0 of 2 users).
  - Follow the `_erfolge` embed style (gold color, 🏆 title). Do NOT resolve display names
    (no Discord in a pure function — key by `uid` or a generic label).

- `async def post_overview(bot, repo: StatsRepository, settings: Settings) -> None`
  1. If `settings.overview_channel_id == 0` → return (`test_..._noop_when_channel_unset`).
  2. `holders = await repo.list_achievement_holders()`. If not holders → return
     (`test_..._noop_when_no_holders`).
  3. `user_ids = sorted(holders.keys())`. `page = 0`.
  4. `channel = bot.get_channel(settings.overview_channel_id)`; if None → return.
  5. `embed = build_overview_embed(holders, user_ids, page)`.
  6. `msg = await channel.send(embed=embed)`.
  7. `await msg.add_reaction("⬅️")`; `await msg.add_reaction("➡️")` (two reactions;
     `test_..._two_nav_reactions` asserts `add_reaction.await_count == 2`).
  8. `bot._overview_state = {"message_id": msg.id, "page": page, "user_ids": user_ids}`
     (`test_..._records_message_id_in_overview_state` asserts `msg.id in str(bot._overview_state)`).

- `async def handle_overview_reaction(bot, repo: StatsRepository, settings: Settings, payload) -> None`
  1. `state = getattr(bot, "_overview_state", None)`; if not state → return.
  2. Guard ALL of (return if any fails):
     - `payload.channel_id == settings.overview_channel_id` (`test_..._unrelated_channel_is_ignored`).
     - `payload.message_id == state["message_id"]`.
     - `str(payload.emoji) in ("⬅️", "➡️")` (`test_..._unrelated_emoji_is_ignored`).
     - `member = getattr(payload, "member", None)`; not None and not `member.bot`.
  3. `delta = 1 if str(payload.emoji) == "➡️" else -1`.
     `user_ids = state["user_ids"]`; `new_page = (state["page"] + delta) % len(user_ids)`.
  4. `holders = await repo.list_achievement_holders()`;
     `embed = build_overview_embed(holders, user_ids, new_page)`.
  5. `channel = bot.get_channel(settings.overview_channel_id)`; if None → return.
     `msg = await channel.fetch_message(state["message_id"])`.
  6. `await msg.edit(embed=embed)` (`test_..._advances_page_and_edits_embed` inspects
     `msg.edit.await_args.kwargs["embed"]` for `"1/59"`).
  7. `state["page"] = new_page`.
  8. Best-effort `try: await msg.remove_reaction(payload.emoji, member) except Exception: pass`
     (`msg.remove_reaction.assert_awaited_once`).

- `async def recompute_user_achievements(repo: StatsRepository, discord_id: int) -> list[Achievement]`
  — ADDITIVE (B17 fix). Algorithm:
  - `metrics = sorted({a.metric for a in ACHIEVEMENTS})` (or module-level derived constant).
  - `newly: list[Achievement] = []`; for each metric: `newly += await check_achievements(repo, discord_id, metric)`.
  - Return `newly`.
  - `check_achievements` is ALREADY additive: it reads `get_user_achievements`, computes only
    threshold-met-and-not-yet-owned via `newly_unlocked`, and `unlock_achievement` is an
    insert-if-absent (returns False on existing row). It NEVER deletes. This is the whole
    B17 guarantee — no DELETE anywhere. Idempotence (`test_recompute_is_idempotent_on_second_run`)
    and additivity (`test_recompute_is_additive_and_never_wipes_existing`) fall out for free.
  - `gate_d` metric yields value 0 via `user_metric_value` (no live source) — additive noop.

- `async def sync_all_achievements(repo: StatsRepository) -> dict`
  1. `snap = await repo.export_all()` (NO new repo method — reuse export_all).
  2. Build the user-id union (all discord ids, as `int`):
     - `snap["achievements"]` — dict keyed by `str(discord_id)` → `int(k)` for each key.
     - `snap["activity_counters"]` — dict keyed by `str(discord_id)` → `int(k)`.
     - `snap["streak_stats"]` — dict keyed by `str(discord_id)` → `int(k)`.
     - `snap["night_stats"]` — dict keyed by `str(discord_id)` → `int(k)`.
     - `snap["gate_entries"]` — list of rows, each with an INT `row["user_id"]`.
     Collect into `user_ids = set()` and union all four str-keyed dicts (int-cast) plus the
     gate_entries user_ids. This ensures a user with ONLY activity data and no unlock row is
     still processed (`test_sync_all_backfills_missing_row_for_over_threshold_user`,
     `test_sync_all_processes_every_user_with_data`).
  3. `users_processed = 0`; `achievements_added = 0`.
     For each `uid` in `sorted(user_ids)`: `newly = await recompute_user_achievements(repo, uid)`;
     `users_processed += 1`; `achievements_added += len(newly)`.
  4. Return `{"users_processed": users_processed, "achievements_added": achievements_added}`.
  - Second run adds zero (`test_sync_all_is_idempotent_second_run_adds_zero`) — additivity.

### `n3x_bot/bot.py`
Register the two prefix commands inside `build_bot` (NOT a second event handler) and wire
the overview reaction handler into the EXISTING `on_raw_reaction_add`.

- **Imports:** add `post_overview`, `handle_overview_reaction`, `sync_all_achievements` to
  the existing `from n3x_bot.achievements import ...` (line 24). `is_admin` already imported
  (line 8).
- **`build_bot` init:** initialize `bot._overview_state = None` near the other in-memory
  trackers (lines 83-91) so `handle_overview_reaction` can `getattr` it safely pre-post.
- **Command registration** (add a small `register_overview_and_sync_commands(bot, repo, settings)`
  helper called from `build_bot`, OR inline in `build_bot` — match the existing register_*
  pattern; a helper is cleaner). Idempotent guards via `bot.get_command(...)`:
  - `!overview` (anyone): `if bot.get_command("overview") is None:` register a command whose
    callback does `await post_overview(bot, repo, settings)`
    (`test_build_bot_registers_overview_command`, `test_overview_command_triggers_post_overview`).
  - `!sync_achievements` (admin-gated): `if bot.get_command("sync_achievements") is None:`
    register a command whose callback:
    ```
    if not is_admin(ctx.author, settings):
        await ctx.send("❌ Keine Berechtigung.", delete_after=5); return
    summary = await sync_all_achievements(repo)
    await ctx.send(f"✅ Sync: {summary['users_processed']} Nutzer, "
                   f"{summary['achievements_added']} neue Achievements.")
    ```
    Use the exact refusal string convention from admin.py (`"❌ Keine Berechtigung."`).
    (`test_build_bot_registers_sync_achievements_command`,
    `test_sync_command_refuses_non_admin_and_mutates_nothing` — non-admin must NOT call
    `sync_all_achievements` at all, `ctx.send` must be awaited;
    `test_sync_command_admin_records_threshold_met_achievements`,
    `test_sync_command_admin_second_run_adds_nothing_new`).
  - Register both via `bot.add_command(commands.Command(_cb, name="..."))` (matches
    `register_activity` / `register_achievement_commands`).
- **Event wiring** — extend the EXISTING `on_raw_reaction_add` (lines 475-477). Do NOT define
  a second one:
  ```
  @bot.event
  async def on_raw_reaction_add(payload):
      await handle_activity_reaction(bot, repo, settings, payload)
      try:
          await handle_overview_reaction(bot, repo, settings, payload)
      except Exception:
          pass
  ```
  (handle_overview_reaction is internally guarded/best-effort; the outer try is belt-and-braces.)

## Data flow

**Voice tier role grant (representative):** A member crosses `voice_36000` while connected.
`voice_flush_task` → `flush_voice_times` credits seconds → `check_achievements(...,"voice_seconds")`
returns `[Achievement(voice_36000)]` → member resolved via `bot.guilds` scan → `announce_achievements`
posts the card → `apply_voice_roles(bot, settings, member, newly)`:
`voice_role_map()` → `voice_role_transition(["voice_36000"], map)` → `(902, [901,903])` →
`member.add_roles(role_902)`, `member.remove_roles(role_901)` (903 not held → skipped). All
best-effort.

**Overview pagination:** `!overview` → `post_overview` → `list_achievement_holders()` →
`build_overview_embed(holders, sorted_uids, 0)` → `channel.send(embed=...)` → add ⬅️/➡️ →
store `_overview_state`. User clicks ➡️ → `on_raw_reaction_add` → `handle_overview_reaction`:
guards pass → `new_page = (0+1) % n` → re-fetch holders → rebuild embed → `fetch_message` →
`msg.edit(embed=...)` → `msg.remove_reaction(emoji, member)` → `state["page"] = 1`.

**Additive sync:** `!sync_achievements` (admin) → `sync_all_achievements` → `export_all()`
snapshot → union of `achievements`/`activity_counters`/`streak_stats`/`night_stats` keys
(str→int) + `gate_entries[].user_id` → per user `recompute_user_achievements` → per metric
`check_achievements` (insert-if-absent only) → summary `{users_processed, achievements_added}`
→ report to channel. No DELETE at any layer → B17 impossible.

## Dependencies

- New packages: **none**.
- Internal modules:
  - `activity.py` depends on `n3x_bot.achievements.ACHIEVEMENTS` (extend existing import),
    `n3x_bot.config.Settings`.
  - `achievements.py` depends on `check_achievements`/`user_metric_value` (already local),
    `TOTAL_ACHIEVEMENTS`/`ACHIEVEMENTS` (local), `repo.list_achievement_holders`/`export_all`
    (existing storage), `n3x_bot.admin.is_admin`.
  - `bot.py` depends on the new `post_overview`/`handle_overview_reaction`/`sync_all_achievements`.
- Reused storage methods only: `list_achievement_holders`, `export_all`, `unlock_achievement`,
  `get_user_achievements`, `add_activity`/`get_activity` (via check_achievements). NO new methods.

## Build sequence (for the Coder)

1. `config.py`: add `Settings.voice_role_map()`. Run `tests/test_voice_roles.py::test_voice_role_map_*`.
2. `activity.py`: add `voice_role_transition` (pure). Run the four `test_transition_*`.
3. `activity.py`: add `apply_voice_roles` + extend the `ACHIEVEMENTS` import. Run the five
   `test_apply_*`.
4. `activity.py`: extend the B9 skip tuple in `handle_activity_reaction`. Run
   `tests/test_activity.py::test_reaction_skipped_in_overview_channel`.
5. `achievements.py`: add `build_overview_embed` (pure). Run the four `test_build_overview_embed_*`.
6. `achievements.py`: add `post_overview` + `handle_overview_reaction`. `bot.py`: init
   `bot._overview_state`, register `!overview`, wire `handle_overview_reaction` into the
   existing `on_raw_reaction_add`. Run the rest of `tests/test_overview.py`.
7. `achievements.py`: add `recompute_user_achievements`. Run `test_recompute_*`.
8. `achievements.py`: add `sync_all_achievements`. Run `test_sync_all_*`.
9. `bot.py`: register admin-gated `!sync_achievements`. Run `test_sync_command_*` +
   `test_build_bot_registers_sync_achievements_command`.
10. Wire `apply_voice_roles` into `handle_voice_state_update` + `flush_voice_times`
    (behavioral, not directly asserted by a RED test but required by the task). Run the full
    `tests/test_activity.py` + `tests/test_voice_roles.py` to confirm no regression.
11. Full suite for the four touched test files; confirm coverage ≥ 80%.

## Risks and open questions

- **Nav-emoji literal fidelity.** `post_overview` adds `"⬅️"`/`"➡️"` and `handle_overview_reaction`
  compares `str(payload.emoji)` against the same literals. The test's `_Emoji.__str__` returns
  the exact char passed (`"➡️"` incl. U+FE0F variation selector), so exact-literal match works.
  If the coder normalizes/strips the variation selector on one side but not the other, the
  guard breaks. Keep both sides byte-identical to what `post_overview` sends.
- **Overview edit requires re-fetch, not the stored message object.** The handler must
  `channel.fetch_message(state["message_id"])` (the fake channel supports this) rather than
  hold a reference — matches v3 fidelity and the test's `_overview_channel` design.
- **`user_ids` stability across pages.** `_overview_state` stores `user_ids` at post time;
  the handler reuses that list for ordering + `len` while re-fetching holders only for counts.
  If a new holder appears between post and reaction, they won't paginate until the next
  `!overview` — acceptable (not tested), avoids index drift.
- **Enumerate-users correctness (B17 core).** The union MUST include the str-keyed
  `activity_counters`/`streak_stats`/`night_stats` dicts AND `gate_entries[].user_id`, not just
  `achievements` keys — otherwise a wiped user (activity present, no unlock row) is never
  reprocessed and the backfill test fails. Keys in the three activity/streak/night dicts are
  `str`; `gate_entries` `user_id` is `int`. Cast uniformly to `int`.
- **Additivity is guaranteed by reuse, not by new logic.** Both `recompute`/`sync` route
  through the existing `check_achievements` → `unlock_achievement` (insert-if-absent). No code
  path removes rows. The Coder must NOT add any pre-clear/DELETE step — that would resurrect B17.
- **Non-admin sync must be a hard early return.** The refusal branch must run BEFORE
  `sync_all_achievements`, so a non-admin mutates nothing (`test_sync_command_refuses_non_admin_and_mutates_nothing`).
- **Voice role resolution when guild/role missing.** `member.guild.get_role(id)` may return
  `None` (role deleted / not cached); `apply_voice_roles` must skip a `None` grant/other and
  never raise. The whole Discord section is under one try/except — a `RuntimeError` from
  `add_roles` is swallowed (`test_apply_is_best_effort_when_add_roles_raises`).
- **Idempotent registration.** `!overview` and `!sync_achievements` both guard on
  `bot.get_command(...)` so `test_reaction_skipped_in_overview_channel` (which calls
  `register_activity` a second time after `build_bot`) and any double-wire don't raise
  `CommandRegistrationError`. Do not add a second `on_raw_reaction_add` — extend the existing one.
