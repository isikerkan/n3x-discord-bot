"""RED-phase specs for editable achievement TIER / CATEGORY colours
(de-hardcode Phase 3, Parts B + C).

The hardcoded colour tables in ``cards.py`` become runtime-overridable via a
resolver ``n3x_bot.colors.ColorConfig`` (mirroring ``content.ContentTexts`` /
``achievement_defs.AchievementDefs``), and ``cards.tier_color`` gains an
optional ``colors`` param that consults it.

Surfaces (to be implemented downstream):

  * ``n3x_bot/colors.py``:
        class ColorConfig:
            __init__(overrides: dict[str, str] | None = None)
            tier_color(title: str) -> tuple[int, int, int]
            category_color(category: str) -> tuple[int, int, int]
            async refresh(repo) -> None            # loads all_color_config()
            @classmethod async load(repo) -> ColorConfig

  * ``cards.tier_color(achievement, colors: ColorConfig | None = None)``:
        achievement.color valid hex STILL wins first (unchanged);
        else if colors is not None -> gate uses colors.tier_color(title),
        non-gate uses colors.category_color(category);
        else (colors None) -> today's module-default behaviour, unchanged.

Key convention: ``tier:<substring>`` and ``category:<name>`` -> ``#RRGGBB``.
Overrides MERGE onto the code defaults (a single tier override recolours only
that tier; every other tier/category keeps its default) — this is DIFFERENT from
AchievementDefs' total-replacement, and is pinned explicitly below.

Defaults are SOURCED from the ``cards.py`` constants (imported, not duplicated),
so the resolver's no-override output is byte-identical to today's
``_gate_tier_color`` / ``ACTIVITY_CATEGORY_COLORS``.

``n3x_bot.colors`` is imported LAZILY inside each test so a missing module REDs
as a clean per-test ModuleNotFoundError rather than a collection-time
ImportError.
"""

import os
import tempfile

import pytest

from n3x_bot import cards
from n3x_bot.achievements import Achievement
from n3x_bot.seed import seed_defaults
from n3x_bot.storage.json_repo import JsonRepository

WHITE = (255, 255, 255)


def _colors():
    """Lazily import the (not-yet-existing) resolver module."""
    import importlib
    return importlib.import_module("n3x_bot.colors")


def _ColorConfig(overrides=None):
    return _colors().ColorConfig(overrides)


async def _flatfile_repo() -> JsonRepository:
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    repo = JsonRepository(path)
    await repo.connect()
    await seed_defaults(repo)
    repo._test_path = path
    return repo


async def _cleanup(repo: JsonRepository) -> None:
    path = getattr(repo, "_test_path", None)
    await repo.close()
    if path and os.path.exists(path):
        os.remove(path)


def _gate_ach(aid, title, metric="gate_a"):
    return Achievement(id=aid, category="gate", metric=metric, threshold=5,
                       title=title, secret=False, color=None)


# ══════════════════════════════════════════════════════════════════════════
# B1. Defaults (no overrides) reproduce today's colours exactly
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("title", [
    "Alpha Bronze Pilot", "Alpha Gold Pilot", "Alpha Gott Pilot",
    "Alpha Master Pilot", "Alpha Grandmaster Pilot", "Beta Silber Pilot",
])
def test_tier_color_default_matches_gate_tier_color(title):
    cfg = _ColorConfig()
    assert cfg.tier_color(title) == cards._gate_tier_color(title)


def test_tier_color_default_no_substring_match_is_white():
    cfg = _ColorConfig()
    assert cfg.tier_color("Etwas Ohne Bekannte Stufe") == WHITE


@pytest.mark.parametrize("category", ["voice", "streak", "night",
                                      "message", "reaction"])
def test_category_color_default_matches_activity_table(category):
    cfg = _ColorConfig()
    assert cfg.category_color(category) == cards.ACTIVITY_CATEGORY_COLORS[category]


def test_category_color_unknown_category_is_white():
    cfg = _ColorConfig()
    assert cfg.category_color("does_not_exist") == WHITE


# ══════════════════════════════════════════════════════════════════════════
# B2. tier override — MERGE, order preservation, malformed fallback
# ══════════════════════════════════════════════════════════════════════════

def test_tier_override_recolours_only_that_tier():
    cfg = _ColorConfig({"tier:gold": "#010203"})
    assert cfg.tier_color("Alpha Gold Pilot") == (1, 2, 3)


def test_tier_override_leaves_other_tiers_at_default_merge():
    # MERGE (not total replacement): overriding gold must NOT reset bronze.
    cfg = _ColorConfig({"tier:gold": "#010203"})
    assert cfg.tier_color("Alpha Bronze Pilot") == cards._gate_tier_color(
        "Alpha Bronze Pilot")


def test_tier_override_preserves_substring_match_order():
    # "grandmaster" is listed before "master" so a Grandmaster title matches
    # grandmaster first; an override on tier:master must NOT hijack it (override
    # changes only the colour, never the match order).
    cfg = _ColorConfig({"tier:master": "#010203"})
    assert cfg.tier_color("Alpha Grandmaster Pilot") == cards._gate_tier_color(
        "Alpha Grandmaster Pilot")


def test_tier_override_applies_when_the_matched_substring_is_overridden():
    cfg = _ColorConfig({"tier:grandmaster": "#0A0B0C"})
    assert cfg.tier_color("Alpha Grandmaster Pilot") == (10, 11, 12)


def test_tier_malformed_override_falls_back_to_default():
    cfg = _ColorConfig({"tier:gold": "nothex"})
    assert cfg.tier_color("Alpha Gold Pilot") == cards._gate_tier_color(
        "Alpha Gold Pilot")


def test_tier_override_does_not_change_no_match_white():
    cfg = _ColorConfig({"tier:gold": "#010203"})
    assert cfg.tier_color("Keine Bekannte Stufe") == WHITE


# ══════════════════════════════════════════════════════════════════════════
# B3. category override — merge + malformed fallback + unknown white
# ══════════════════════════════════════════════════════════════════════════

def test_category_override_recolours_only_that_category():
    cfg = _ColorConfig({"category:voice": "#010203"})
    assert cfg.category_color("voice") == (1, 2, 3)


def test_category_override_leaves_other_categories_default_merge():
    cfg = _ColorConfig({"category:voice": "#010203"})
    assert cfg.category_color("streak") == cards.ACTIVITY_CATEGORY_COLORS["streak"]


def test_category_malformed_override_falls_back_to_default():
    cfg = _ColorConfig({"category:voice": "nothex"})
    assert cfg.category_color("voice") == cards.ACTIVITY_CATEGORY_COLORS["voice"]


def test_category_override_for_unknown_category_still_resolves():
    # An override keyed to a category with no code default is still honoured.
    cfg = _ColorConfig({"category:custom": "#0A141E"})
    assert cfg.category_color("custom") == (10, 20, 30)


def test_never_raises_on_malformed_overrides():
    # Construction + resolution with garbage overrides must never raise.
    cfg = _ColorConfig({"tier:gold": "zzz", "category:voice": "###",
                        "bogus_key_without_prefix": "#010203"})
    cfg.tier_color("Alpha Gold Pilot")
    cfg.category_color("voice")


# ══════════════════════════════════════════════════════════════════════════
# B4. refresh / load from a seeded repo
# ══════════════════════════════════════════════════════════════════════════

async def test_refresh_loads_overrides_from_repo():
    repo = await _flatfile_repo()
    cfg = _ColorConfig()
    assert cfg.tier_color("Alpha Gold Pilot") == cards._gate_tier_color(
        "Alpha Gold Pilot")  # default first

    await repo.set_color_config("tier:gold", "#010203")
    await cfg.refresh(repo)

    assert cfg.tier_color("Alpha Gold Pilot") == (1, 2, 3)
    await _cleanup(repo)


async def test_refresh_merges_leaving_unset_tiers_default():
    repo = await _flatfile_repo()
    await repo.set_color_config("tier:gold", "#010203")
    cfg = _ColorConfig()
    await cfg.refresh(repo)

    assert cfg.tier_color("Alpha Gold Pilot") == (1, 2, 3)
    assert cfg.tier_color("Alpha Bronze Pilot") == cards._gate_tier_color(
        "Alpha Bronze Pilot")
    await _cleanup(repo)


async def test_load_classmethod_builds_resolver_with_repo_overrides():
    repo = await _flatfile_repo()
    await repo.set_color_config("category:voice", "#010203")

    cfg = await _colors().ColorConfig.load(repo)

    assert cfg.category_color("voice") == (1, 2, 3)
    await _cleanup(repo)


async def test_load_with_no_overrides_is_behaviour_preserving():
    repo = await _flatfile_repo()
    cfg = await _colors().ColorConfig.load(repo)

    for title in ("Alpha Bronze Pilot", "Alpha Gold Pilot", "Alpha Gott Pilot"):
        assert cfg.tier_color(title) == cards._gate_tier_color(title)
    for category in cards.ACTIVITY_CATEGORY_COLORS:
        assert cfg.category_color(category) == cards.ACTIVITY_CATEGORY_COLORS[category]
    await _cleanup(repo)


# ══════════════════════════════════════════════════════════════════════════
# C. cards.tier_color gains an optional `colors` param
# ══════════════════════════════════════════════════════════════════════════

def test_tier_color_colors_none_gate_byte_identical_to_today():
    # The colors=None path (default) must be unchanged from today's behaviour.
    ach = _gate_ach("a_5", "Alpha Bronze Pilot")
    assert cards.tier_color(ach, colors=None) == cards._gate_tier_color(
        "Alpha Bronze Pilot")


def test_tier_color_colors_none_non_gate_byte_identical_to_today():
    ach = Achievement(id="voice_3600", category="voice", metric="voice_seconds",
                      threshold=3600, title="Rookie Talker", secret=False,
                      color=None)
    assert cards.tier_color(ach, colors=None) == \
        cards.ACTIVITY_CATEGORY_COLORS["voice"]


def test_tier_color_gate_uses_colors_tier_override():
    cfg = _ColorConfig({"tier:gold": "#010203"})
    ach = _gate_ach("a_25", "Alpha Gold Pilot")
    assert cards.tier_color(ach, colors=cfg) == (1, 2, 3)


def test_tier_color_non_gate_uses_colors_category_override():
    cfg = _ColorConfig({"category:voice": "#010203"})
    ach = Achievement(id="voice_3600", category="voice", metric="voice_seconds",
                      threshold=3600, title="Rookie Talker", secret=False,
                      color=None)
    assert cards.tier_color(ach, colors=cfg) == (1, 2, 3)


def test_tier_color_explicit_achievement_color_still_beats_colors_override():
    # Per-achievement `.color` (Phase 2b) STILL WINS, even over a colors override.
    cfg = _ColorConfig({"tier:gold": "#010203"})
    ach = Achievement(id="a_25", category="gate", metric="gate_a", threshold=25,
                      title="Alpha Gold Pilot", secret=False, color="#0A0B0C")
    assert cards.tier_color(ach, colors=cfg) == (10, 11, 12)


def test_tier_color_gate_with_colors_but_no_override_matches_default():
    cfg = _ColorConfig()  # no overrides
    ach = _gate_ach("a_5", "Alpha Bronze Pilot")
    assert cards.tier_color(ach, colors=cfg) == cards._gate_tier_color(
        "Alpha Bronze Pilot")
