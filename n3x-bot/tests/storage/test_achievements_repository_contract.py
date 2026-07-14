"""Contract tests for the achievements repository surface (Pass A).

Parametrized across every registered backend via the shared ``repo`` fixture
(json, sqlite, and postgres when TEST_POSTGRES_URL is set), mirroring
``tests/storage/test_activity_repository_contract.py``.

New interface (to be implemented downstream on StatsRepository + json_repo +
sql_repo + schema — a new ``achievements`` table):

    async def unlock_achievement(discord_id: int, achievement_id: str) -> bool
        # True if newly unlocked, False if the user already had it.
    async def has_achievement(discord_id: int, achievement_id: str) -> bool
    async def get_user_achievements(discord_id: int) -> set[str]
    async def list_achievement_holders() -> dict[int, set[str]]
        # every user that has >=1 unlock -> their set of achievement ids.

RED until then: calling these raises AttributeError on the repo.
"""


# ── unlock / has ───────────────────────────────────────────────────────────

async def test_unlock_achievement_returns_true_when_newly_unlocked(repo):
    assert await repo.unlock_achievement(1, "msg_1000") is True


async def test_unlock_achievement_returns_false_when_already_unlocked(repo):
    await repo.unlock_achievement(1, "msg_1000")
    assert await repo.unlock_achievement(1, "msg_1000") is False


async def test_has_achievement_false_before_unlock(repo):
    assert await repo.has_achievement(1, "msg_1000") is False


async def test_has_achievement_true_after_unlock(repo):
    await repo.unlock_achievement(1, "msg_1000")
    assert await repo.has_achievement(1, "msg_1000") is True


# ── get_user_achievements ──────────────────────────────────────────────────

async def test_get_user_achievements_empty_by_default(repo):
    assert await repo.get_user_achievements(1) == set()


async def test_get_user_achievements_returns_all_unlocked_ids(repo):
    await repo.unlock_achievement(1, "msg_1000")
    await repo.unlock_achievement(1, "voice_3600")
    assert await repo.get_user_achievements(1) == {"msg_1000", "voice_3600"}


async def test_achievements_are_isolated_per_user(repo):
    await repo.unlock_achievement(1, "msg_1000")
    assert await repo.get_user_achievements(2) == set()
    assert await repo.has_achievement(2, "msg_1000") is False


# ── list_achievement_holders ───────────────────────────────────────────────

async def test_list_achievement_holders_empty_when_none_unlocked(repo):
    assert await repo.list_achievement_holders() == {}


async def test_list_achievement_holders_maps_every_user_to_their_unlocks(repo):
    await repo.unlock_achievement(1, "msg_1000")
    await repo.unlock_achievement(1, "voice_3600")
    await repo.unlock_achievement(2, "streak_7")
    holders = await repo.list_achievement_holders()
    assert holders == {1: {"msg_1000", "voice_3600"}, 2: {"streak_7"}}
