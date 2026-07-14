# Architecture: Welcome cards (v3 port #5)

Branch: `feature/welcome-cards`. RED tests: `n3x-bot/tests/test_welcome.py`
(20 tests) + exclusion-tuple edits in `n3x-bot/tests/test_bot_wiring.py`
(already applied — verified: `"sync_welcome"` present in both tuples at lines
75 and 90).

All new code lives in a NEW module `n3x_bot/welcome.py`, modeled on
`n3x_bot/cards.py` (asset/font load via `importlib.resources`, PNG **bytes**
return, best-effort Discord I/O) and `n3x_bot/kodex.py` (idempotent prefix
command registration, `is_admin` gate). Two-line wiring change in
`n3x_bot/bot.py`.

## Tests this design satisfies

Pure render (`render_welcome_card`):
- `test_render_welcome_card_returns_nonempty_bytes` — returns `bytes`, non-empty
- `test_render_welcome_card_is_valid_png` — `Image.open(...).format == "PNG"`
- `test_render_welcome_card_matches_welcome_bg_dimensions` — size `(1024, 572)`
- `test_render_welcome_card_long_name_does_not_raise` — 120-char name OK
- `test_render_welcome_card_empty_name_is_safe` — `""` renders to valid bytes

Prefix helper (`strip_prefix`):
- `test_strip_prefix_removes_bracket_and_space` — `"[N3X] Max"` → `"Max"`
- `test_strip_prefix_removes_bracket_without_space` — `"[N3X]Max"` → `"Max"`
- `test_strip_prefix_leaves_plain_name_untouched` — `"Max"` → `"Max"`
- `test_strip_prefix_empty_string_is_safe` — `""` → `""`

Discord I/O (`send_welcome_card`):
- `test_send_welcome_card_posts_file_with_mention_content` — resolves channel via
  `bot.get_channel(settings.welcome_channel_id)`; content `"Willkommen <@555>!"`;
  `file` is a `discord.File`
- `test_send_welcome_card_filename_uses_member_id` — filename `"welcome_777.png"`
- `test_send_welcome_card_strips_prefix_before_rendering` — monkeypatches
  `welcome.render_welcome_card`; the value passed to render is the STRIPPED name
  (`"[N3X] Max"` → render called with `"Max"`). Pins that `send_welcome_card`
  calls `render_welcome_card` as a MODULE-LEVEL name (not a local import binding),
  so the monkeypatch takes effect.
- `test_send_welcome_card_noop_when_channel_missing` — `get_channel` → `None`;
  must not raise, must not call `channel.send`
- `test_send_welcome_card_swallows_send_error` — `channel.send` raises
  `RuntimeError`; `send_welcome_card` must not propagate; `send` still awaited once

Command (`register_welcome_commands` / `!sync_welcome`):
- `test_register_welcome_commands_registers_sync_welcome` — `get_command("sync_welcome")` set
- `test_register_welcome_commands_is_idempotent` — second call no-ops, no double register
- `test_sync_welcome_non_admin_refused_posts_no_cards` — non-admin: `ctx.send`
  called once with a message containing `"Keine Berechtigung"`; `channel.send`
  never called
- `test_sync_welcome_admin_posts_one_card_per_non_bot_member` — 2 members →
  `channel.send.await_count == 2`; every posted message `.file` is a `discord.File`
- `test_sync_welcome_admin_reports_count` — some `ctx.send` call's first arg
  (stringified) contains `"2"`
- `test_sync_welcome_skips_bot_members` — one `.bot=True` member skipped →
  `channel.send.await_count == 1`

Wiring (`on_member_join` / `build_bot`):
- `test_on_member_join_posts_welcome_card_file` — join posts a `discord.File`
  (card), not plain text
- `test_on_member_join_still_registers_user_when_posting_card` — join still
  `upsert_user`s (repo has the user afterward with correct `display_name`) AND
  posts a card
- `test_build_bot_registers_sync_welcome_command` — `build_bot` wires `sync_welcome`

## Files to create

### `n3x-bot/n3x_bot/welcome.py`

Imports:
- `import importlib.resources as ir`
- `from io import BytesIO`
- `import discord`
- `from discord.ext import commands`
- `from PIL import Image, ImageDraw, ImageFont`
- `from n3x_bot.admin import is_admin`
- `from n3x_bot.cards import _font_bytes`  ← reuse the bundled-font loader (see Risks)
- `from n3x_bot.config import Settings`

Module constants (mirror `cards.py` naming; port v3 values exactly):
- `_FONT_SIZE_LINE1 = 42` (line "Willkommen")
- `_FONT_SIZE_LINE2 = 72` (display name)
- `_FONT_SIZE_LINE3 = 36` (line "bei")
- `_COLOR_WHITE = (255, 255, 255)`
- `_COLOR_GOLD = (255, 215, 0)`
- `_COLOR_GREY = (200, 200, 200)`
- `_GAP = 12`
- Module-global cache `_WELCOME_BG: Image.Image | None = None` (mirrors
  `cards._BG_TEMPLATE`).

Helper `_welcome_bg() -> Image.Image` (mirror `cards._bg_template`, ~lines 95-102):
- `global _WELCOME_BG`; if `None`, open bundled asset and cache:
  `with ir.files("n3x_bot").joinpath("assets/welcome_bg.jpg").open("rb") as f:`
  `img = Image.open(f).convert("RGBA"); img.load(); _WELCOME_BG = img`
- return `_WELCOME_BG.copy()` (Pillow draws mutate in place — hand out a fresh copy).

`render_welcome_card(display_name: str) -> bytes` — pure Pillow render, no Discord.
Port of v3 `generate_welcome_card` (lines 649-714) with the `try/except`/`None`
path and the hardcoded `/usr/share/fonts/...` path DROPPED (asset always bundled;
font via `_font_bytes()`). Steps:
1. `bg = _welcome_bg()`; `draw = ImageDraw.Draw(bg)`
2. Build three fonts from the bundled TTF bytes (mirror `cards.py` lines 119-122):
   `fb = _font_bytes()`
   `font1 = ImageFont.truetype(BytesIO(fb), _FONT_SIZE_LINE1)`
   `font2 = ImageFont.truetype(BytesIO(fb), _FONT_SIZE_LINE2)`
   `font3 = ImageFont.truetype(BytesIO(fb), _FONT_SIZE_LINE3)`
3. Lines: `line1 = "Willkommen"`, `line2 = display_name`, `line3 = "bei"`.
4. Local `center_x(text, font) -> int` (port v3 lines 680-682, identical to
   `cards.py` lines 142-144): `bbox = draw.textbbox((0, 0), text, font=font)`;
   `return (bg.width // 2) - ((bbox[2] - bbox[0]) // 2)`.
5. Heights via `bbox[3] - bbox[1]` per line (v3 lines 685-690):
   `h1 = height(line1, font1)`, `h2 = height(line2, font2)`, `h3 = height(line3, font3)`.
   (An empty `line2` yields `h2 == 0`, which is safe.)
6. Vertical layout (v3 lines 692-706 EXACTLY):
   - `total_height = h1 + _GAP + h2 + _GAP + h3`
   - `y_start = (bg.height // 2) // 2 - total_height // 2`  (upper-half centering)
   - `y1 = y_start`
   - `y2 = y1 + h1 + _GAP`
   - `y3 = y2 + h2 + _GAP`
7. Draw:
   - `draw.text((center_x(line1, font1), y1), line1, font=font1, fill=_COLOR_WHITE)`
   - `draw.text((center_x(line2, font2), y2), line2, font=font2, fill=_COLOR_GOLD)`
   - `draw.text((center_x(line3, font3), y3), line3, font=font3, fill=_COLOR_GREY)`
8. Return PNG bytes (mirror `cards.py` lines 167-169, NOT a BytesIO):
   `buf = BytesIO(); bg.save(buf, format="PNG"); return buf.getvalue()`

`strip_prefix(display_name: str, prefix_str: str) -> str` — exact-case leading
prefix removal, empty-safe. Logic: if `display_name.startswith(prefix_str)`,
slice off `len(prefix_str)` and `.lstrip()` the remainder (removes the optional
space after `"[N3X]"` — handles both `"[N3X] Max"` and `"[N3X]Max"`); else return
`display_name` unchanged. `""` → `""` (no prefix match). Exact-case only (see Risks).

`async send_welcome_card(bot, settings, member) -> None` — best-effort Discord I/O:
1. `channel = bot.get_channel(settings.welcome_channel_id)`
2. `if channel is None: return`  (noop when channel missing)
3. `try:` block:
   - `name = strip_prefix(member.display_name, settings.prefix_str)`
   - `png = render_welcome_card(name)`  ← bare module-level call so monkeypatch works
   - `await channel.send(f"Willkommen {member.mention}!",`
     `file=discord.File(BytesIO(png), filename=f"welcome_{member.id}.png"))`
   - `except Exception: return`  (swallow render/Discord failure — never raises out)
   No text-fallback path (render always returns bytes — see Risks).

`register_welcome_commands(bot, settings) -> None` — idempotent, admin-gated
prefix command (mirror `kodex.register_kodex_commands` lines 66-91; note: NO repo):
1. `if bot.get_command("sync_welcome") is not None: return`  (idempotent)
2. Define `async def _sync_welcome_cmd(ctx):`
   - `if not is_admin(ctx.author, settings):`
     `await ctx.send("❌ Keine Berechtigung.", delete_after=5); return`
   - `count = 0`
   - `for member in ctx.guild.members:`
     `if getattr(member, "bot", False): continue`
     `await send_welcome_card(bot, settings, member)`
     `count += 1`
   - `await ctx.send(f"✅ {count} Willkommenskarten verschickt.", delete_after=5)`
     (message must contain the number as a substring — `str(count)`)
3. `bot.add_command(commands.Command(_sync_welcome_cmd, name="sync_welcome"))`

Note on the refuse message: use the SAME `"❌ Keine Berechtigung."` string used
everywhere else (bot.py 123, kodex.py 72). The test asserts substring
`"Keine Berechtigung"`, which this satisfies.

## Files to modify

### `n3x-bot/n3x_bot/bot.py`

1. Imports (after the `from n3x_bot.models import render_output` block, ~line 37):
   add `from n3x_bot.welcome import register_welcome_commands, send_welcome_card`.

2. `build_bot` (~line 109, immediately after
   `register_kodex_commands(bot, repo, settings)`): add
   `register_welcome_commands(bot, settings)`  ← NO repo argument (feature is pure
   render + Discord I/O + config).

3. `on_member_join` (lines 621-637) — REPLACE only the plain-text welcome block
   (lines 625-631) with a best-effort card post. Keep the other three steps
   byte-for-byte:
   - KEEP lines 623-624 (`if not member.bot: await repo.upsert_user(...)`)
   - REPLACE lines 625-631 (the `channel = bot.get_channel(...)` +
     `channel.send(f"Willkommen ... Night Shadow!")` try/except) with:
     `try:`
     `    await send_welcome_card(bot, settings, member)`
     `except Exception:`
     `    pass`
     (Belt-and-suspenders: `send_welcome_card` is already best-effort, but wrap it
     like the adjacent kodex call so a future non-swallowed error can't skip the
     kodex DM / enforce_prefix steps.)
   - KEEP lines 632-635 (`send_kodex_dm` try/except)
   - KEEP lines 636-637 (`await asyncio.sleep(5); await enforce_prefix(member)`)

### `n3x-bot/tests/test_bot_wiring.py` — NO CHANGE NEEDED

Already edited by TDD: `"sync_welcome"` is present in the command-count exclusion
tuples at lines 75 and 90. Verified — do not re-touch.

## Data flow

Member joins → `on_member_join(member)`:
1. `repo.upsert_user(member.id, member.display_name)` (non-bot) — registration.
2. `send_welcome_card(bot, settings, member)`:
   - `bot.get_channel(settings.welcome_channel_id)` → channel (or `None` → noop).
   - `strip_prefix(member.display_name, settings.prefix_str)` → clean name.
   - `render_welcome_card(name)` → decodes cached `welcome_bg.jpg`, draws 3 lines
     in the upper half with bundled DejaVuSans-Bold, returns PNG bytes.
   - `channel.send("Willkommen <@id>!", file=discord.File(BytesIO(png), "welcome_<id>.png"))`.
   - Any exception in render/send is swallowed.
3. `send_kodex_dm(bot, repo, member)` (best-effort).
4. `asyncio.sleep(5)` then `enforce_prefix(member)`.

Admin runs `!sync_welcome` → `_sync_welcome_cmd(ctx)`:
1. `is_admin(ctx.author, settings)` gate; non-admin → refuse + return.
2. Iterate `ctx.guild.members`, skip `.bot`, call `send_welcome_card` for each,
   count non-bot members.
3. Reply `"✅ <count> Willkommenskarten verschickt."`.

## Dependencies

- New packages: NONE. Pillow (`PIL`), `discord`, `discord.ext.commands` are
  already project deps (used by `cards.py`/`kodex.py`).
- Internal modules `welcome.py` depends on: `n3x_bot.cards._font_bytes`
  (bundled font loader), `n3x_bot.admin.is_admin`, `n3x_bot.config.Settings`.
- Bundled asset `n3x_bot/assets/welcome_bg.jpg` (1024×572) — present and in
  pyproject `force-include`. Font `assets/DejaVuSans-Bold.ttf` — present, reused
  via `cards._font_bytes`.

## Build sequence (for the Coder)

1. Create `n3x_bot/welcome.py` with imports, constants, `_welcome_bg()`, and
   `render_welcome_card`. → greens the 5 `test_render_welcome_card_*` tests.
2. Add `strip_prefix`. → greens the 4 `test_strip_prefix_*` tests.
3. Add `send_welcome_card` (bare module-level `render_welcome_card` call, best-effort
   try/except). → greens the 5 `test_send_welcome_card_*` tests.
4. Add `register_welcome_commands`. → greens the 6 `test_register_welcome_commands_*`
   / `test_sync_welcome_*` tests.
5. Wire `bot.py`: import, `register_welcome_commands(bot, settings)` in `build_bot`,
   and the `on_member_join` block replacement. → greens
   `test_build_bot_registers_sync_welcome_command`, the two
   `test_on_member_join_*` tests, and keeps `test_bot_wiring.py` green.

Run only the focused suite: `pytest n3x-bot/tests/test_welcome.py n3x-bot/tests/test_bot_wiring.py`.

## Risks and open questions

- **Font-loader reuse (agree with TDD handoff).** Importing the underscore-private
  `cards._font_bytes` is a deliberate intra-package reuse: it avoids a second
  asset read (the font bytes are cached in `cards._FONT_BYTES`) and guarantees the
  welcome module never resurrects v3's `/usr/share/fonts/...` hardcoded path.
  Acceptable trade-off — a private symbol crossing module lines within the same
  package. Alternative (a local `_font_bytes` clone in `welcome.py`) would
  duplicate the loader and read the TTF twice; rejected.
- **`strip_prefix` is exact-case (agree with TDD handoff).** All four tests use the
  exact `"[N3X]"` casing; v3 and `enforce_prefix` are exact-case. No case-folding —
  matches existing convention. If a lowercased `"[n3x] max"` ever needs stripping,
  that is a separate, untested requirement — do NOT design for it now.
- **No text-fallback path (agree with TDD handoff).** `render_welcome_card` always
  returns bytes (asset bundled), so v3's "post text if card is None" branch is dead
  code and is intentionally not ported. `send_welcome_card`'s try/except swallows a
  render failure into a silent noop rather than falling back to text — this is what
  `test_send_welcome_card_swallows_send_error` pins.
- **`on_member_join` wrapping.** `send_welcome_card` already swallows all
  exceptions internally, so the extra try/except in `on_member_join` is redundant
  today. Kept for parity with the adjacent `send_kodex_dm` call and to defend the
  downstream kodex/enforce_prefix steps against any future non-swallowed error.
  No test forces this either way.
- **Message wording for the sync reply** (`"✅ {count} Willkommenskarten
  verschickt."`) is a free choice — the only constraint from
  `test_sync_welcome_admin_reports_count` is that `str(count)` appears as a
  substring in some `ctx.send` call. Chosen to match the German +
  emoji-prefixed style of the existing `sync_achievements` / `kodex` replies.
- No contradictions found between the tests and the codebase conventions.
```