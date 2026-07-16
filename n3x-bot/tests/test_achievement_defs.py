"""RED-phase specs for the DB-backed achievement-definition resolver
(Phase 2a foundation slice).

Mirrors ``n3x_bot/content.py`` ``ContentTexts`` and its tests: achievement
DEFINITIONS resolve to DB overrides (``achievement_defs`` table) else the code
default ``ACHIEVEMENTS`` list. Behaviour is preserved when the table is empty.

Surface (to be implemented downstream):

  * ``n3x_bot/achievement_defs.py``:
        class AchievementDefs:
            __init__(defs: list[Achievement] | None = None)  # None -> code list
            all() -> list[Achievement]
            total (property) -> int
            for_metric(metric) -> list[Achievement]
            by_id(aid) -> Achievement | None
            metrics() -> list[str]                # sorted unique
            async refresh(repo) -> None           # DB rows else code defaults
            @classmethod async load(repo) -> AchievementDefs

  * ``build_bot`` attaches ``bot.achievement_defs`` (an ``AchievementDefs``)
    and ``on_ready`` refreshes it inside a try/except falling back to defaults.

Imports of the not-yet-existing module are LAZY (inside test bodies) so
collection succeeds and each test REDs cleanly on ModuleNotFoundError /
AttributeError / AssertionError.
"""

import os
import tempfile

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from n3x_bot.achievements import ACHIEVEMENTS, TOTAL_ACHIEVEMENTS
from n3x_bot.bot import build_bot
from n3x_bot.config import Settings
from n3x_bot.seed import seed_defaults
from n3x_bot.storage.json_repo import JsonRepository

BASE_SETTINGS_KWARGS = dict(
    discord_token="tok",
    target_role_id=1,
    welcome_channel_id=2,
    reminder_channel_id=999,
    julez_id=424242,
    _env_file=None,
    _env_prefix="NONEXISTENT_",
)


def _settings(**overrides) -> Settings:
    kwargs = dict(BASE_SETTINGS_KWARGS)
    kwargs.update(overrides)
    return Settings(**kwargs)


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


def _defs_cls():
    from n3x_bot.achievement_defs import AchievementDefs
    return AchievementDefs


async def _seed_code_defaults(repo) -> None:
    """Persist the 83 code-default achievements into ``achievement_defs``."""
    for a in ACHIEVEMENTS:
        await repo.set_achievement_def(
            a.id, category=a.category, metric=a.metric, threshold=a.threshold,
            title=a.title, secret=a.secret, color=getattr(a, "color", None))


class _StubRepo:
    """Minimal repo exposing only ``all_achievement_defs`` — used to inject a
    deliberately malformed row without going through the typed storage layer."""
    def __init__(self, rows):
        self._rows = rows

    async def all_achievement_defs(self):
        return list(self._rows)


# ══════════════════════════════════════════════════════════════════════════
# 1. defaults: an unrefreshed resolver mirrors the code ACHIEVEMENTS list
# ══════════════════════════════════════════════════════════════════════════

def test_default_resolver_holds_code_default_achievements():
    resolver = _defs_cls()()
    assert [a.id for a in resolver.all()] == [a.id for a in ACHIEVEMENTS]


def test_default_resolver_total_matches_code_baseline():
    resolver = _defs_cls()()
    assert resolver.total == TOTAL_ACHIEVEMENTS == 83


def test_total_equals_len_of_all():
    resolver = _defs_cls()()
    assert resolver.total == len(resolver.all())


def test_for_metric_filters_to_that_metric():
    resolver = _defs_cls()()
    got = resolver.for_metric("streak")
    assert got
    assert {a.metric for a in got} == {"streak"}


def test_for_metric_unknown_metric_returns_empty_list():
    resolver = _defs_cls()()
    assert resolver.for_metric("does_not_exist") == []


def test_by_id_returns_the_matching_achievement():
    resolver = _defs_cls()()
    ach = resolver.by_id("voice_3600")
    assert ach is not None
    assert ach.id == "voice_3600"
    assert ach.threshold == 3600


def test_by_id_unknown_returns_none():
    resolver = _defs_cls()()
    assert resolver.by_id("nope") is None


def test_metrics_returns_sorted_unique_metrics():
    resolver = _defs_cls()()
    metrics = resolver.metrics()
    assert metrics == sorted(set(metrics))
    assert "streak" in metrics and "voice_seconds" in metrics


# ══════════════════════════════════════════════════════════════════════════
# 2. explicit-defs constructor
# ══════════════════════════════════════════════════════════════════════════

def test_explicit_defs_constructor_uses_given_list():
    from n3x_bot.achievements import Achievement
    one = Achievement(id="x_1", category="voice", metric="voice_seconds",
                      threshold=1, title="T", secret=False)
    resolver = _defs_cls()([one])
    assert resolver.total == 1
    assert resolver.by_id("x_1") is one


# ══════════════════════════════════════════════════════════════════════════
# 3. refresh: DB rows override, empty table falls back to code defaults
# ══════════════════════════════════════════════════════════════════════════

async def test_refresh_empty_table_falls_back_to_code_defaults():
    repo = await _flatfile_repo()
    resolver = _defs_cls()()

    await resolver.refresh(repo)  # table is empty

    assert [a.id for a in resolver.all()] == [a.id for a in ACHIEVEMENTS]
    assert resolver.total == 83
    await _cleanup(repo)


async def test_refresh_loads_rows_from_db_when_present():
    repo = await _flatfile_repo()
    await repo.set_achievement_def("voice_7200000", category="voice",
                                   metric="voice_seconds", threshold=7200000,
                                   title="Test Legende", secret=False,
                                   color="#ABCDEF")
    resolver = _defs_cls()()

    await resolver.refresh(repo)

    # Only the single DB row is present -> DB fully overrides the code list.
    assert resolver.total == 1
    ach = resolver.by_id("voice_7200000")
    assert ach is not None
    assert ach.threshold == 7200000
    assert ach.title == "Test Legende"
    assert ach.color == "#ABCDEF"
    await _cleanup(repo)


async def test_refresh_never_raises_and_skips_malformed_rows():
    # One good row + one broken row (threshold not an int / missing a required
    # field): refresh must NOT raise and must yield only the good row.
    good = {"id": "voice_3600", "category": "voice", "metric": "voice_seconds",
            "threshold": 3600, "title": "Rookie Talker", "secret": False,
            "color": None}
    broken = {"id": "broken", "category": "voice", "metric": "voice_seconds",
              "threshold": "not-an-int", "title": "Broken", "secret": False,
              "color": None}
    resolver = _defs_cls()()

    await resolver.refresh(_StubRepo([good, broken]))  # must not raise

    ids = [a.id for a in resolver.all()]
    assert ids == ["voice_3600"]
    assert resolver.by_id("broken") is None


async def test_load_classmethod_builds_refreshed_resolver():
    repo = await _flatfile_repo()
    await repo.set_achievement_def("voice_7200000", category="voice",
                                   metric="voice_seconds", threshold=7200000,
                                   title="Test Legende", secret=False)

    resolver = await _defs_cls().load(repo)

    assert resolver.by_id("voice_7200000") is not None
    await _cleanup(repo)


async def test_load_with_empty_table_is_behaviour_preserving():
    repo = await _flatfile_repo()

    resolver = await _defs_cls().load(repo)

    assert resolver.total == 83
    await _cleanup(repo)


# ══════════════════════════════════════════════════════════════════════════
# 4. end-to-end override: 83 defaults + one extra def -> total grows to 84
# ══════════════════════════════════════════════════════════════════════════

async def test_end_to_end_override_grows_total_to_84():
    from n3x_bot.achievements import build_overview_embed
    repo = await _flatfile_repo()
    await _seed_code_defaults(repo)
    await repo.set_achievement_def("voice_7200000", category="voice",
                                   metric="voice_seconds", threshold=7200000,
                                   title="Test Legende", secret=False,
                                   color="#123456")
    resolver = _defs_cls()()

    await resolver.refresh(repo)

    assert resolver.total == 84
    assert resolver.by_id("voice_7200000") is not None
    # a big signed-int64 snowflake holder id
    holders = {9223372036854775807: {"a_5"}}
    embed = build_overview_embed(holders, [9223372036854775807], 0,
                                 total=resolver.total)
    text = str(getattr(embed, "description", "") or "")
    assert "/84" in text
    await _cleanup(repo)


# ══════════════════════════════════════════════════════════════════════════
# 5. build_bot wiring + on_ready refresh
# ══════════════════════════════════════════════════════════════════════════

async def test_build_bot_attaches_achievement_defs_defaulting_to_baseline():
    from n3x_bot.achievement_defs import AchievementDefs
    repo = await _flatfile_repo()
    settings = _settings()

    bot = build_bot(settings, repo)

    assert isinstance(bot.achievement_defs, AchievementDefs)
    assert bot.achievement_defs.total == 83
    await _cleanup(repo)


async def test_on_ready_refreshes_achievement_defs_from_db():
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=0)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)
    bot.tree.sync = AsyncMock()
    await _seed_code_defaults(repo)
    await repo.set_achievement_def("voice_7200000", category="voice",
                                   metric="voice_seconds", threshold=7200000,
                                   title="Test Legende", secret=False)

    await bot.on_ready()

    assert bot.achievement_defs.total == 84
    assert bot.achievement_defs.by_id("voice_7200000") is not None
    await _cleanup(repo)


async def test_on_ready_keeps_defaults_when_table_empty():
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=0)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)
    bot.tree.sync = AsyncMock()

    await bot.on_ready()

    assert bot.achievement_defs.total == 83
    await _cleanup(repo)
