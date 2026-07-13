"""Contract tests for the activity-tracking repository surface.

Parametrized across every registered backend via the shared ``repo`` fixture
(json, sqlite, and postgres when TEST_POSTGRES_URL is set), mirroring
``tests/storage/test_repository_contract.py``.

New interface (to be implemented downstream on StatsRepository + json_repo +
sql_repo + schema):

    async def add_activity(discord_id: int, metric: str, amount: int) -> int
    async def get_activity(discord_id: int, metric: str) -> int
    async def get_streak(discord_id: int) -> dict | None
    async def set_streak(discord_id, current_streak, last_active_date, max_streak) -> None
    async def get_night(discord_id: int) -> dict | None
    async def set_night(discord_id, night_count, last_night_date) -> None

metric ∈ {"voice_seconds", "messages", "reactions"}.

RED until then: calling these raises AttributeError on the repo.
"""


# ── cumulative metric counters ─────────────────────────────────────────────

async def test_get_activity_defaults_to_zero(repo):
    assert await repo.get_activity(123, "messages") == 0


async def test_add_activity_returns_new_running_total(repo):
    assert await repo.add_activity(123, "messages", 1) == 1
    assert await repo.add_activity(123, "messages", 4) == 5


async def test_get_activity_reads_back_accumulated_total(repo):
    await repo.add_activity(123, "voice_seconds", 90)
    await repo.add_activity(123, "voice_seconds", 10)
    assert await repo.get_activity(123, "voice_seconds") == 100


async def test_activity_metrics_are_independent(repo):
    await repo.add_activity(1, "messages", 3)
    await repo.add_activity(1, "reactions", 5)
    await repo.add_activity(1, "voice_seconds", 7)
    assert await repo.get_activity(1, "messages") == 3
    assert await repo.get_activity(1, "reactions") == 5
    assert await repo.get_activity(1, "voice_seconds") == 7


async def test_activity_counters_are_per_user(repo):
    await repo.add_activity(1, "messages", 2)
    await repo.add_activity(2, "messages", 9)
    assert await repo.get_activity(1, "messages") == 2
    assert await repo.get_activity(2, "messages") == 9


# ── streak ─────────────────────────────────────────────────────────────────

async def test_get_streak_missing_returns_none(repo):
    assert await repo.get_streak(1) is None


async def test_set_and_get_streak_roundtrip(repo):
    await repo.set_streak(1, 3, "2026-07-13", 9)
    assert await repo.get_streak(1) == {
        "current_streak": 3, "last_active_date": "2026-07-13", "max_streak": 9}


async def test_set_streak_overwrites_previous(repo):
    await repo.set_streak(1, 1, "2026-07-12", 1)
    await repo.set_streak(1, 2, "2026-07-13", 2)
    assert await repo.get_streak(1) == {
        "current_streak": 2, "last_active_date": "2026-07-13", "max_streak": 2}


async def test_streak_is_per_user(repo):
    await repo.set_streak(1, 3, "2026-07-13", 9)
    assert await repo.get_streak(2) is None


# ── night ──────────────────────────────────────────────────────────────────

async def test_get_night_missing_returns_none(repo):
    assert await repo.get_night(1) is None


async def test_set_and_get_night_roundtrip(repo):
    await repo.set_night(1, 4, "2026-07-13")
    assert await repo.get_night(1) == {
        "night_count": 4, "last_night_date": "2026-07-13"}


async def test_set_night_overwrites_previous(repo):
    await repo.set_night(1, 4, "2026-07-12")
    await repo.set_night(1, 5, "2026-07-13")
    assert await repo.get_night(1) == {
        "night_count": 5, "last_night_date": "2026-07-13"}
