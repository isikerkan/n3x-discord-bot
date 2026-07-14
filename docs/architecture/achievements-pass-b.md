# Architecture: Achievements Pass B — Pillow cards + auto-post on unlock

Scope: turn `tests/test_cards.py` and `tests/test_achievement_announce.py` GREEN.
NEW module `n3x_bot/cards.py` (rendering + announce). `activity.py` + `bot.py`
wired to feed newly-unlocked achievements into the announcer. No config change
(`milestone_channel_id` already exists). No roles/overview/sync (later pass).

## Tests this design satisfies

### tests/test_cards.py (pure, Discord-free, network-free)
- `test_render_returns_valid_png_bytes` — `render_achievement_card(...)` returns PNG bytes.
- `test_render_output_matches_bundled_card_background_size` — output size == `card_bg.webp` size (2528×732), no resize.
- `test_render_with_none_avatar_does_not_crash_and_is_valid_png` — `avatar_bytes=None` still yields a valid PNG.
- `test_render_with_real_png_avatar_is_valid_png` — real PNG avatar path.
- `test_render_loads_bundled_assets_regardless_of_cwd` — assets loaded via `importlib.resources`, cwd-independent.
- `test_tier_color_is_deterministic` — same input → same tuple.
- `test_tier_color_gott_gate_achievement_is_v3_gott_red` — `a_1000` → `(255, 0, 0)`.
- `test_tier_color_bronze_gate_achievement_is_v3_bronze` — `a_5` → `(205, 127, 50)`.
- `test_tier_color_voice_achievements_share_one_category_colour` — `voice_3600` == `voice_36000`, 3-tuple, 0..255.
- `test_tier_color_streak_category_differs_from_voice_category` — streak color ≠ voice color.
- `test_card_texts_returns_three_strings` — 3-tuple of `str`.
- `test_card_texts_includes_achievement_title` — `ach.title` appears in the triple.
- `test_card_texts_includes_member_display_name` — member name appears in the triple.

### tests/test_achievement_announce.py (Discord faked; render NOT mocked)
- `test_announce_posts_one_card_per_new_achievement` — 2 achievements → `channel.send` awaited 2×.
- `test_announce_posts_a_discord_file` — sent with `file=` that is a `discord.File`.
- `test_announce_reads_avatar_via_display_avatar_read` — avatar via `member.display_avatar.read()` (no aiohttp; v3-B11 fix).
- `test_announce_deletes_prior_same_category_card_before_sending` — prior same-category card `.delete()` awaited before 2nd send.
- `test_announce_is_noop_when_milestone_channel_unset` — `milestone_channel_id==0` → no send, avatar NOT read.
- `test_announce_is_noop_for_empty_newly_list` — empty `newly` → no send, avatar NOT read.
- `test_announce_is_noop_when_channel_missing` — `get_channel` returns None → no raise, no send.
- `test_announce_skips_bot_member` — `member.bot` → no send.
- `test_message_event_posts_card_when_achievement_unlocks` — `on_message` crossing `msg_1000` posts exactly one `discord.File` card.
- `test_message_event_posts_exactly_one_card_across_repeat_messages` — 2nd (already-unlocked) message posts nothing; total send count == 1.

## Files to create

- `n3x_bot/cards.py` — pure rendering + announce. Imports only: `io.BytesIO`,
  `importlib.resources as ir`, `PIL.{Image,ImageDraw,ImageFont}`, `discord`,
  `from n3x_bot.achievements import Achievement, GATE_NAMES` (type + gate labels),
  `from n3x_bot.config import Settings` (type hint), `from n3x_bot.format import format_number`.
  MUST NOT import `n3x_bot.bot` (or `n3x_bot.activity`) — no cycle.
  Public symbols:
  - `render_achievement_card(avatar_bytes: bytes | None, title: str, subtitle: str, footer: str, tier_color: tuple[int, int, int]) -> bytes`
  - `tier_color(achievement: Achievement) -> tuple[int, int, int]`
  - `card_texts(achievement: Achievement, member_display_name: str) -> tuple[str, str, str]`
  - `async announce_achievements(bot, settings: Settings, member, newly: list[Achievement]) -> None`
  Module-level constants: `_AVATAR_SIZE`, `_AVATAR_POS`, font sizes, `GATE_TIER_COLORS` helper, `ACTIVITY_CATEGORY_COLORS` dict.

## Files to modify

- `n3x_bot/activity.py`
  - `record_message_activity(repo, settings, member_id, now) -> list[Achievement]`
    (signature keeps its 4 args — it has no `bot`/member object, so it does NOT
    announce; it only aggregates and RETURNS the newly-unlocked list). Change the
    three `await check_achievements(...)` calls (lines 84–88) to accumulate into a
    `newly` list and `return newly`.
  - `handle_voice_state_update(...)` — replace the bare `check_achievements` call
    (line 124) with: capture `newly`, and if non-empty call
    `announce_achievements(bot, settings, member, newly)` wrapped in try/except.
    Add `from n3x_bot.cards import announce_achievements` at top.
  - `flush_voice_times(...)` — in the credited loop (lines 155–156), capture
    `newly`; if non-empty, resolve the member via `bot.guilds` (`guild.get_member(member_id)`);
    if found, best-effort `announce_achievements(bot, settings, member, newly)`.
    If not found, the unlock is still recorded (already done by `check_achievements`);
    no card that cycle.
  - `handle_activity_reaction(...)` — replace line 170 with: capture `newly`; if
    non-empty, best-effort `announce_achievements(bot, settings, member, newly)`
    (uses the already-validated non-bot `payload.member`).
  - Add `from n3x_bot.achievements import Achievement` for the return type hint
    (already imports `check_achievements`).

- `n3x_bot/bot.py`
  - `build_bot` (near line 82–88): add `bot._milestone_cards = {}` alongside the
    other in-memory trackers (`_rank_last_posts`, `_target_last_posts`,
    `_gate_embed_msg_id`). Keyed `(member_id, category) -> message_id`.
  - Add `from n3x_bot.cards import announce_achievements` to the imports.
  - `handle_gate_input_message` (lines 328–332): after `update_gate_stats_embed`,
    accumulate `newly` from the three gate `check_achievements` calls; if non-empty,
    best-effort `announce_achievements(bot, settings, message.author, newly)`.
  - `on_message` (lines 445–448): capture the return of `record_message_activity`
    as `newly`; if non-empty, best-effort
    `announce_achievements(bot, settings, author, newly)`. Keep the existing bot/id
    guard. The announce runs before/independent of gate + `process_commands` and
    must be wrapped so a Discord failure never breaks message recording.

## render_achievement_card — algorithm (mechanical)

Arg → line mapping (ported from v3 `generate_achievement_card` l1/l2/l3):
`title` = line 1 (small, white), `subtitle` = line 2 (huge, white — member name),
`footer` = line 3 (medium, drawn in `tier_color`). The test call
`render_achievement_card(_png, "5 Alpha Gates", "Erkan", "Alpha Bronze Pilot", (205,127,50))`
confirms footer is the tier-colored achievement title.

1. Load background: `with ir.files("n3x_bot").joinpath("assets/card_bg.webp").open("rb") as f: bg = Image.open(f).convert("RGBA")`. `.convert` forces a full in-memory load so `bg` is safe after the handle closes. `draw = ImageDraw.Draw(bg)`.
2. Load font bytes once: `font_bytes = ir.files("n3x_bot").joinpath("assets/DejaVuSans-Bold.ttf").read_bytes()`. Build three fonts from independent `BytesIO(font_bytes)` (a font object consumes the stream): `font_s = ImageFont.truetype(BytesIO(font_bytes), 46)`, `font_h = ...143`, `font_m = ...72` (v3 sizes).
3. Avatar (defensive — never crash):
   - `avatar = None`. If `avatar_bytes is not None`: `try: avatar = Image.open(BytesIO(avatar_bytes)).convert("RGBA") except Exception: avatar = None`.
   - If `avatar is None`: `avatar = Image.new("RGBA", (_AVATAR_SIZE, _AVATAR_SIZE), (100, 100, 100, 255))` (grey placeholder — matches v3 fallback; satisfies the None test).
   - Resize to `_AVATAR_SIZE = 455` (`Image.Resampling.LANCZOS`). Circular mask: `mask = Image.new("L", (455,455), 0); ImageDraw.Draw(mask).ellipse((0,0,455,455), fill=255); avatar.putalpha(mask)`.
   - Paste: `avatar_x = -70`, `avatar_y = (bg.height // 2) - (455 // 2)`; `bg.paste(avatar, (avatar_x, avatar_y), avatar)`.
4. Text (centered horizontally, stacked bottom-up — port v3):
   - `center_x(text, font) = (bg.width // 2) - ((bbox[2]-bbox[0]) // 2)` using `draw.textbbox((0,0), text, font)`.
   - `y_bottom = 380`, `gap1 = 10`, `gap2 = 25`. Heights via textbbox `[3]-[1]` per line.
   - `y_line3 = y_bottom - h3`; `y_line2 = y_line3 - gap2 - h2`; `y_line1 = y_line2 - gap1 - h1`.
   - Draw: line1=`title` white `(255,255,255)` font_s; line2=`subtitle` white font_h; line3=`footer` `tier_color` font_m.
5. Output: `buf = BytesIO(); bg.save(buf, format="PNG"); return buf.getvalue()` (bytes at bg size — no resize).

Note: v3's layout constants were tuned for v3's background; the bundled bg is
2528×732. Constants are ported verbatim (tests only assert PNG validity + size).
Visual tuning is cosmetic and out of the test surface — flag to TDD if pixel
layout ever becomes a requirement.

## tier_color — full mapping

```
def tier_color(achievement):
    if achievement.category == "gate":
        return _gate_tier_color(achievement.title)   # v3 get_title_color, alpha dropped
    return ACTIVITY_CATEGORY_COLORS[achievement.category]
```

`_gate_tier_color(title)` — port of v3 `get_title_color`, keyed on `title.lower()`,
return 3-tuple (drop the trailing 255). Order matters (grandmaster before master):
- "bronze" → (205,127,50)   ← pinned by test (a_5)
- "silber" → (192,192,192)
- "gold"   → (255,215,0)
- "platin" → (229,228,226)
- "diamant"→ (185,242,255)
- "grandmaster" → (148,0,211)   (check BEFORE master)
- "master" (and not "grand") → (255,69,0)
- "gott"   → (255,0,0)       ← pinned by test (a_1000)
- "einsteiger" → (0,255,127)
- "profi"  → (30,144,255)
- "veteran"→ (255,20,147)
- "millionär"/"million" → (255,215,0)
- fallback → (255,255,255)

`ACTIVITY_CATEGORY_COLORS` (deterministic, one-per-category, all distinct; drawn
from the v3 palette for visual consistency; only hard test constraint is
voice≠streak):
- "voice"    → (30,144,255)   dodger blue
- "streak"   → (255,69,0)     orange-red
- "night"    → (148,0,211)    purple
- "message"  → (0,255,127)    spring green
- "reaction" → (255,165,0)    orange

Determinism: pure dict/keyword lookups on frozen `Achievement` fields → same
input always yields same tuple (satisfies `test_tier_color_is_deterministic`).

## card_texts — exact 3-line content

```
def card_texts(achievement, member_display_name):
    return (_milestone_line(achievement), member_display_name, achievement.title)
```
- title (line1) = `_milestone_line(achievement)` — descriptor.
- subtitle (line2) = `member_display_name` (satisfies member-name test).
- footer (line3) = `achievement.title` (satisfies achievement-title test; drawn in tier_color).

`_milestone_line(achievement)` (ported from v3 gate scheme; sensible descriptors
for activity categories — content NOT pinned by tests, only that title+name land
somewhere, which footer+subtitle already guarantee):
- metric `gate_a|gate_b|gate_c|gate_d` → `f"{threshold} {GATE_NAMES[gtype]} Gates"` (e.g. "5 Alpha Gates")
- metric `gate_total` → `"Erster Gate"` if threshold==1 else `f"{threshold} Gates Gesamt"`
- metric `gate_cost_total` → `f"{format_number(threshold)} Uridium"` (e.g. "1.000.000 Uridium")
- category `voice` → `f"{threshold // 3600}h Voice"`
- category `message` → `f"{threshold} Nachrichten"`
- category `streak` → `f"{threshold} Tage Streak"`
- category `night` → `f"{threshold} Nächte aktiv"`
- category `reaction` → `f"{threshold} Reaktionen"`

`GATE_NAMES` and `format_number` are imported from existing modules (no new logic).

## announce_achievements — flow + prior-card tracking

```
async def announce_achievements(bot, settings, member, newly):
    if settings.milestone_channel_id == 0:      # noop: channel unset
        return
    if not newly:                               # noop: nothing new
        return
    if getattr(member, "bot", False):           # noop: bot member
        return
    channel = bot.get_channel(settings.milestone_channel_id)
    if channel is None:                         # noop: channel missing
        return

    try:
        avatar_bytes = await member.display_avatar.read()   # v3-B11 fix: built-in
    except Exception:
        avatar_bytes = None

    store = getattr(bot, "_milestone_cards", None)
    if store is None:
        store = bot._milestone_cards = {}

    for ach in newly:
        title, subtitle, footer = card_texts(ach, member.display_name)
        png = render_achievement_card(avatar_bytes, title, subtitle, footer, tier_color(ach))

        key = (member.id, ach.category)
        old_id = store.get(key)
        if old_id is not None:                  # delete prior SAME-category card first
            try:
                old = await channel.fetch_message(old_id)
                await old.delete()
            except Exception:                   # best-effort (NotFound/Forbidden)
                pass

        msg = await channel.send(
            file=discord.File(BytesIO(png), filename=f"achievement_{member.id}_{ach.category}.png"))
        store[key] = msg.id                     # record new prior-card for that category
```

Guard order is load-bearing: the `milestone==0`, empty-`newly`, and `member.bot`
returns all precede `display_avatar.read()` so the two "avatar NOT read" noop
tests hold. `fetch_message`+`delete` (not a kept message object) matches the
existing `_send_ephemeral` / `update_gate_stats_embed` pattern; the fake channel
in the test resolves ids via `fetch_message`, so this satisfies
`test_announce_deletes_prior_same_category_card_before_sending`.

Prior-card tracking: in-memory `bot._milestone_cards` dict, mirroring the
`_rank_last_posts` / `_gate_embed_msg_id` convention (cosmetic; lost on restart is
acceptable — the first post-restart card of a category won't delete a
pre-restart one). No repo methods, no schema, no export/import. Initialized in
`build_bot`, with a defensive `getattr` fallback so the helper is safe if ever
called on a bot not built by `build_bot`.

## Wiring — exact call sites

- **on_message** (`bot.py` ~445): existing non-bot/id guard →
  `newly = await record_message_activity(repo, settings, author.id, now_local(settings))`;
  `if newly: try: await announce_achievements(bot, settings, author, newly) except Exception: pass`.
  Member object = `message.author` (`author`).
- **gate input** (`bot.py` `handle_gate_input_message` ~328): inside `if inserted:`,
  after `update_gate_stats_embed`, accumulate
  `newly = (await check_achievements(...,f"gate_{gate_type}")) + (await check_achievements(...,"gate_total")) + (await check_achievements(...,"gate_cost_total"))`;
  `if newly:` best-effort announce with `message.author`.
- **voice leave/move** (`activity.py` `handle_voice_state_update` ~124): under `if credited:`,
  `newly = await check_achievements(repo, member.id, "voice_seconds")`;
  `if newly:` best-effort announce with `member`.
- **reaction** (`activity.py` `handle_activity_reaction` ~170):
  `newly = await check_achievements(repo, payload.user_id, "reactions")`;
  `if newly:` best-effort announce with the validated `member` (`payload.member`).
- **voice flush** (`activity.py` `flush_voice_times` ~155): per credited `member_id`,
  `newly = await check_achievements(repo, member_id, "voice_seconds")`; if non-empty,
  resolve `member = next((g.get_member(member_id) for g in bot.guilds if g.get_member(member_id)), None)`;
  if found, best-effort announce; else record-only (no card this cycle).
- **record_message_activity** (`activity.py` ~84): does NOT announce (no member/bot
  object); accumulates the three `check_achievements` results into `newly` and
  `return newly`.

Every announce call is wrapped `try/except Exception: pass` so a Discord failure
never breaks activity/gate recording. In all Pass A tests `milestone_channel_id`
defaults to 0, so `announce_achievements` returns at guard 1 before touching
`bot.get_channel`, `bot._milestone_cards`, `bot.guilds`, or member avatar/name
attributes — those tests keep passing unchanged (e.g. voice test members are
`SimpleNamespace(id, bot)` with no `display_name`).

## Data flow (representative: message crosses msg_1000)

1. `on_message(message)` — author non-bot with id.
2. `record_message_activity` bumps `messages` to 1000, checks metric `messages`,
   `check_achievements` unlocks `msg_1000`, returns `[Achievement(msg_1000)]`.
3. `on_message` receives `newly=[msg_1000]`, calls `announce_achievements(bot, settings, author, newly)`.
4. Guards pass (channel set, newly non-empty, author not bot, channel resolved).
5. `avatar = await author.display_avatar.read()`.
6. `card_texts(msg_1000, "Erkan")` → `("1000 Nachrichten", "Erkan", "Tastatur-Krieger")`;
   `tier_color(msg_1000)` → `(0,255,127)` (message category).
7. `render_achievement_card(...)` → PNG bytes at bg size.
8. No prior `(7,"message")` card → send `discord.File`; store `_milestone_cards[(7,"message")] = msg.id`.
9. A repeat message → `check_achievements` returns `[]` → `newly` empty → announce noop → no second card.

## Dependencies

- New packages: NONE. Pillow (`pillow>=10.0`), discord.py, pydantic-settings all present.
- Internal deps of `cards.py`: `n3x_bot.achievements` (`Achievement`, `GATE_NAMES`),
  `n3x_bot.config` (`Settings` type), `n3x_bot.format` (`format_number`), PIL, discord.
- `activity.py` and `bot.py` add `from n3x_bot.cards import announce_achievements`.
- Bundled assets already present: `n3x_bot/assets/card_bg.webp`, `n3x_bot/assets/DejaVuSans-Bold.ttf`.

## Build sequence (for the Coder)

1. Create `n3x_bot/cards.py` with `ACTIVITY_CATEGORY_COLORS`, `_gate_tier_color`,
   `tier_color`, `_milestone_line`, `card_texts`. (Pure — unblocks all
   `tier_color`/`card_texts` tests immediately.)
2. Add `render_achievement_card` (asset loading, avatar, text, PNG). Run
   `tests/test_cards.py` → GREEN.
3. Add `announce_achievements` to `cards.py`. (Imports only stdlib/discord/PIL +
   `card_texts`/`tier_color`/`render_achievement_card` — no bot import.)
4. `bot.py`: add `bot._milestone_cards = {}` in `build_bot`; add cards import.
   Run the `announce_*` unit tests → GREEN (they build a bot + fake channel).
5. `activity.py`: change `record_message_activity` to return `newly`; add
   announce calls in voice/reaction/flush; add cards + Achievement imports.
6. `bot.py`: wire `on_message` and `handle_gate_input_message` to announce.
   Run `tests/test_achievement_announce.py` wiring tests → GREEN.
7. Full run of `tests/test_cards.py`, `tests/test_achievement_announce.py`,
   `tests/test_activity.py`, `tests/test_bot_wiring.py` → all GREEN, coverage ≥ 80%.

## Risks and open questions

- **importlib.resources under Docker/AMP wheel** — `ir.files("n3x_bot")` resolves
  correctly from the source tree (all tests), so the RED suite goes GREEN. For a
  built wheel, hatchling must ship `n3x_bot/assets/*`. The `.webp`/`.ttf` are
  git-tracked under the package dir, which hatchling's default wheel target
  includes — but this is unverified for the AMP image. Out of the test surface;
  flag: if a runtime `FileNotFoundError` appears, add
  `[tool.hatch.build.targets.wheel] force-include`/`artifacts` for `assets/`
  (a pyproject change, explicitly out of Pass B scope).
- **Avatar decode failures** — non-PNG/corrupt avatar bytes are caught and fall
  back to the grey placeholder; render never raises. Covered by the None test;
  corrupt-bytes path is defensive (untested — acceptable).
- **Layout constants vs 2528×732 bg** — v3 constants (`y_bottom=380`, avatar 455,
  fonts 46/143/72) were tuned for a different bg; text may not sit exactly over
  the red line here. Cosmetic, not asserted. Flag to TDD if pixel-accurate layout
  becomes a requirement.
- **Per-message announce overhead** — cheap: `announce_achievements` returns at
  guard 1 when `newly` is empty (the common case) and before any I/O when the
  channel is unset; render only runs on an actual unlock.
- **Double-posting** — prevented by `check_achievements`/`unlock_achievement`
  being the single source of "newly": an already-unlocked achievement returns
  `[]`, so `newly` is empty and nothing posts (pinned by the repeat-message test).
- **Voice-flush announce gap** — a threshold crossed purely via the 5-min flush
  is announced only if the member is currently resolvable in `bot.guilds`;
  otherwise the unlock is recorded silently (no card that cycle). Simplest honest
  behavior given `flush_voice_times` has no member object. Flag if a guaranteed
  card is required.
- **Circular import** — avoided by construction: `cards.py` imports achievements/
  config/format/PIL/discord only; `activity.py` and `bot.py` import from `cards`.
  Nothing imports back into `bot`.
- **Restart loses prior-card ids** — in-memory `_milestone_cards`; first
  post-restart card of a category won't delete its pre-restart predecessor.
  Accepted (matches existing ephemeral-tracking trade-offs).
