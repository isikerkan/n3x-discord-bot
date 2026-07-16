"""RED tests for achievements Pass B — pure card rendering, tier colours, and
the card-text builder in the (not-yet-existing) ``n3x_bot.cards`` module.

Everything here is Discord-free and network-free. The new module is imported
lazily inside each test via ``_cards()`` so a missing module surfaces as a
runtime ModuleNotFoundError (the correct RED "missing symbol" signal) instead
of a collection-time import error that would break the whole file.

Assumptions pinned here (flag for the Architect):
  * New module ``n3x_bot.cards`` exposes:
      - render_achievement_card(avatar_bytes: bytes | None, title: str,
            subtitle: str, footer: str, tier_color: tuple[int,int,int]) -> bytes
        returning PNG bytes, drawn onto the bundled ``assets/card_bg.webp``
        with the bundled ``assets/DejaVuSans-Bold.ttf``, loaded via
        importlib.resources (NOT a relative or hardcoded /usr path).
      - tier_color(achievement) -> tuple[int,int,int]
      - card_texts(achievement, member_display_name) -> (title, subtitle, footer)
  * Gate tier colours are ported EXACTLY from v3 get_title_color (Bronze /
    Gott are unambiguous, pinned below). The EXACT RGB for activity categories
    (voice/streak/…) is the Architect's call — these tests pin only the
    STRUCTURAL contract: deterministic, one colour per category, distinct
    across categories.
  * card_texts layout (which of title/subtitle/footer holds the achievement
    title vs the member name) is NOT pinned — only that both strings appear
    somewhere in the returned triple.
"""

import importlib
import importlib.resources as ir
from io import BytesIO

import pytest
from PIL import Image

from n3x_bot.achievements import ACHIEVEMENTS

RED = (255, 0, 0)


def _cards():
    """Lazily import the (not-yet-existing) pure-rendering module."""
    return importlib.import_module("n3x_bot.cards")


def _ach(achievement_id: str):
    return next(a for a in ACHIEVEMENTS if a.id == achievement_id)


def _png_bytes(size=(64, 64), color=(12, 34, 56)) -> bytes:
    """A real, small PNG produced in-test with PIL (a plausible avatar)."""
    buf = BytesIO()
    Image.new("RGBA", size, (*color, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _bg_size() -> tuple[int, int]:
    with ir.files("n3x_bot").joinpath("assets/card_bg.webp").open("rb") as f:
        im = Image.open(f)
        im.load()
        return im.size


# ── render_achievement_card ────────────────────────────────────────────────

def test_render_returns_valid_png_bytes():
    cards = _cards()
    out = cards.render_achievement_card(
        _png_bytes(), "5 Alpha Gates", "Erkan", "Alpha Bronze Pilot",
        (205, 127, 50))
    assert isinstance(out, (bytes, bytearray))
    img = Image.open(BytesIO(out))
    img.load()
    assert img.format == "PNG"


def test_render_output_matches_bundled_card_background_size():
    cards = _cards()
    out = cards.render_achievement_card(
        _png_bytes(), "5 Alpha Gates", "Erkan", "Alpha Bronze Pilot", RED)
    img = Image.open(BytesIO(out))
    img.load()
    # v3 draws onto card_bg without resizing it, so the card matches the bg.
    assert img.size == _bg_size()


def test_render_with_none_avatar_does_not_crash_and_is_valid_png():
    cards = _cards()
    out = cards.render_achievement_card(
        None, "5 Alpha Gates", "Erkan", "Alpha Bronze Pilot", RED)
    img = Image.open(BytesIO(out))
    img.load()
    assert img.format == "PNG"


def test_render_with_garbage_avatar_bytes_falls_back_to_placeholder():
    # Corrupt/non-image avatar bytes must not raise: the render swallows the
    # decode error and draws the grey placeholder, still returning a valid PNG.
    cards = _cards()
    out = cards.render_achievement_card(
        b"this is definitely not a PNG", "5 Alpha Gates", "Erkan",
        "Alpha Bronze Pilot", (205, 127, 50))
    img = Image.open(BytesIO(out))
    img.load()
    assert img.format == "PNG"


def test_render_with_real_png_avatar_is_valid_png():
    cards = _cards()
    avatar = _png_bytes(size=(128, 128), color=(200, 10, 10))
    out = cards.render_achievement_card(
        avatar, "1.000.000 Uridium", "Erkan", "Millionärs-Club Pilot",
        (255, 215, 0))
    img = Image.open(BytesIO(out))
    img.load()
    assert img.format == "PNG"


def test_render_loads_bundled_assets_regardless_of_cwd(monkeypatch, tmp_path):
    # Must work under Docker/AMP where cwd is arbitrary: this fails if the impl
    # uses a relative path ("card_bg.webp") or a hardcoded /usr font path
    # instead of importlib.resources against the n3x_bot package.
    cards = _cards()
    monkeypatch.chdir(tmp_path)
    out = cards.render_achievement_card(
        _png_bytes(), "5 Alpha Gates", "Erkan", "Alpha Bronze Pilot", RED)
    img = Image.open(BytesIO(out))
    img.load()
    assert img.format == "PNG"


# ── tier_color ─────────────────────────────────────────────────────────────

def test_tier_color_is_deterministic():
    cards = _cards()
    ach = _ach("a_5")
    assert cards.tier_color(ach) == cards.tier_color(ach)


def test_tier_color_gott_gate_achievement_is_v3_gott_red():
    cards = _cards()
    ach = _ach("a_1000")            # "Alpha Gott Pilot"
    assert cards.tier_color(ach) == (255, 0, 0)


def test_tier_color_bronze_gate_achievement_is_v3_bronze():
    cards = _cards()
    ach = _ach("a_5")              # "Alpha Bronze Pilot"
    assert cards.tier_color(ach) == (205, 127, 50)


def test_tier_color_voice_achievements_share_one_category_colour():
    cards = _cards()
    c1 = cards.tier_color(_ach("voice_3600"))
    c2 = cards.tier_color(_ach("voice_36000"))
    assert c1 == c2
    assert len(c1) == 3
    assert all(0 <= v <= 255 for v in c1)


def test_tier_color_streak_category_differs_from_voice_category():
    cards = _cards()
    voice = cards.tier_color(_ach("voice_3600"))
    streak = cards.tier_color(_ach("streak_7"))
    assert streak != voice


# ── card_texts ─────────────────────────────────────────────────────────────

def test_card_texts_returns_three_strings():
    cards = _cards()
    triple = cards.card_texts(_ach("a_5"), "Erkan")
    assert len(triple) == 3
    assert all(isinstance(s, str) for s in triple)


def test_card_texts_includes_achievement_title():
    cards = _cards()
    title, subtitle, footer = cards.card_texts(_ach("a_5"), "Erkan")
    blob = " ".join((title, subtitle, footer))
    assert "Alpha Bronze Pilot" in blob


def test_card_texts_includes_member_display_name():
    cards = _cards()
    title, subtitle, footer = cards.card_texts(_ach("a_5"), "Erkan")
    blob = " ".join((title, subtitle, footer))
    assert "Erkan" in blob


# ── Phase 2a: tier_color honours an explicit Achievement.color ──────────────

def test_tier_color_explicit_valid_hex_color_wins():
    from n3x_bot.achievements import Achievement
    cards = _cards()
    ach = Achievement(id="a_5", category="gate", metric="gate_a", threshold=5,
                      title="Alpha Bronze Pilot", secret=False, color="#010203")
    # explicit colour overrides the substring-derived bronze colour
    assert cards.tier_color(ach) == (1, 2, 3)


def test_tier_color_none_color_falls_back_for_gate():
    from n3x_bot.achievements import Achievement
    cards = _cards()
    ach = Achievement(id="a_5", category="gate", metric="gate_a", threshold=5,
                      title="Alpha Bronze Pilot", secret=False, color=None)
    assert cards.tier_color(ach) == (205, 127, 50)  # unchanged from today


def test_tier_color_none_color_falls_back_for_non_gate():
    from n3x_bot.achievements import Achievement
    cards = _cards()
    ach = Achievement(id="voice_3600", category="voice", metric="voice_seconds",
                      threshold=3600, title="Rookie Talker", secret=False,
                      color=None)
    assert cards.tier_color(ach) == cards.ACTIVITY_CATEGORY_COLORS["voice"]


def test_tier_color_malformed_color_falls_back_without_raising():
    from n3x_bot.achievements import Achievement
    cards = _cards()
    ach = Achievement(id="a_5", category="gate", metric="gate_a", threshold=5,
                      title="Alpha Bronze Pilot", secret=False, color="nothex")
    # malformed hex must not raise; derivation still yields bronze
    assert cards.tier_color(ach) == (205, 127, 50)


@pytest.mark.parametrize("bad", ["#-f0203", "#+f0203", "#01 203", "nothex"])
def test_tier_color_sign_or_whitespace_hex_falls_back(bad):
    # int(part, 16) accepts a leading +/- and strips internal whitespace, so
    # these are NOT valid #RRGGBB and must fall back to the derived colour
    # (bronze here) rather than yielding a bad/negative tuple.
    from n3x_bot.achievements import Achievement
    cards = _cards()
    ach = Achievement(id="a_5", category="gate", metric="gate_a", threshold=5,
                      title="Alpha Bronze Pilot", secret=False, color=bad)
    assert cards.tier_color(ach) == (205, 127, 50)


@pytest.mark.parametrize("bad", ["#-f0203", "#+f0203", "#01 203", "# f0203"])
def test_parse_hex_color_rejects_sign_and_whitespace(bad):
    cards = _cards()
    assert cards._parse_hex_color(bad) is None
