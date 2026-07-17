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

import asyncio
import time

import pytest


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


# ── atomic unlock (double-post root cause) ──────────────────────────────────
#
# Bug: unlock_achievement on the SQL backends is CHECK-THEN-ACT (SELECT, then
# INSERT, in that order within one transaction). Two concurrent unlocks for the
# SAME (discord_id, achievement_id) can both SELECT "absent" and then both try
# to INSERT — with the composite PK the loser raises IntegrityError instead of
# cleanly returning False (and, absent the PK, both would return True → the
# caller announces twice → the double achievement card).
#
# Target contract: unlock_achievement is ATOMIC — under concurrency for the
# same pair EXACTLY ONE call returns True, every other returns False (never an
# exception), and exactly one row exists afterwards. The json backend is
# already atomic (single-threaded list append) and stays green; the sqlite /
# postgres backends are the RED targets.


async def test_unlock_achievement_issues_single_atomic_insert(repo):
    """Deterministic implementation pin (SQL backends only).

    Record every SQL statement issued during ONE unlock_achievement call and
    assert the call touches the achievements table with a single INSERT and no
    preceding SELECT — i.e. it is a single atomic insert (postgres
    ``ON CONFLICT DO NOTHING`` / sqlite ``INSERT OR IGNORE``), not a
    SELECT-then-INSERT check-then-act. This is the strongest deterministic pin
    for the race: it fails today for the RIGHT reason (a SELECT on achievements
    precedes the INSERT) without depending on non-deterministic task
    scheduling. Impl-agnostic: it keys off statement text, so it holds whether
    the fix lands in unlock_achievement or in the shared insert helper.

    Skipped on the json backend, which has no SQL engine (its atomicity is
    covered by the concurrency behaviour test below).
    """
    engine = getattr(repo, "engine", None)
    if engine is None:
        pytest.skip("statement-shape pin applies only to SQL backends")

    from sqlalchemy import event

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _record)
    try:
        assert await repo.unlock_achievement(1, "msg_1000") is True
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _record)

    touching = [s for s in statements if "achievements" in s.lower()]
    inserts = [s for s in touching if s.lstrip().lower().startswith("insert")]
    selects = [s for s in touching if s.lstrip().lower().startswith("select")]

    assert len(inserts) == 1, touching
    # No SELECT against achievements at all within the call, and in particular
    # none before the INSERT — the check-then-act window must be gone.
    assert selects == [], touching


async def test_unlock_achievement_concurrent_yields_exactly_one_winner(repo):
    """Behaviour contract across ALL backends.

    Fire many concurrent unlocks for the same pair; exactly one returns True,
    the rest return False (never raise), and the achievement is stored exactly
    once. On the SQL backends a real check-then-act loses the race: the
    ``before_cursor_execute`` hook widens the window between the current SELECT
    and INSERT so the race is exercised, and is a pure no-op once the impl
    issues no SELECT before its INSERT (nothing matches) — so it stays correct
    for the atomic implementation. The json backend has no engine and is
    already atomic.
    """
    engine = getattr(repo, "engine", None)
    hook = None
    n = 12
    if engine is not None:
        from sqlalchemy import event

        # Pre-warm the pool so all N connections already exist: otherwise the
        # first task can check out a connection and run its whole transaction
        # before its rivals even acquire one, and the race never happens
        # (making this test flaky-green on the buggy code). With the pool warm,
        # every task reaches the SELECT before any INSERT commits.
        conns = await asyncio.gather(*[engine.connect() for _ in range(n)])
        await asyncio.gather(*[c.close() for c in conns])

        def _widen(conn, cursor, statement, parameters, context, executemany):
            s = statement.lower()
            if s.lstrip().startswith("select") and "achievements" in s:
                time.sleep(0.03)

        event.listen(engine.sync_engine, "before_cursor_execute", _widen)
        hook = _widen

    try:
        results = await asyncio.gather(
            *[repo.unlock_achievement(1, "msg_1000") for _ in range(n)],
            return_exceptions=True)
    finally:
        if hook is not None:
            from sqlalchemy import event
            event.remove(engine.sync_engine, "before_cursor_execute", hook)

    assert all(not isinstance(r, Exception) for r in results), results
    assert sum(1 for r in results if r is True) == 1, results
    assert sum(1 for r in results if r is False) == n - 1, results
    assert await repo.get_user_achievements(1) == {"msg_1000"}
    assert await repo.list_achievement_holders() == {1: {"msg_1000"}}


async def test_reunlock_is_idempotent_and_does_not_duplicate_holder(repo):
    """Sequential idempotency must survive the atomic rewrite (stay-green guard).

    A second unlock of the same pair returns False, and neither
    get_user_achievements nor list_achievement_holders gains a duplicate entry.
    """
    assert await repo.unlock_achievement(1, "msg_1000") is True
    assert await repo.unlock_achievement(1, "msg_1000") is False
    assert await repo.get_user_achievements(1) == {"msg_1000"}
    assert await repo.list_achievement_holders() == {1: {"msg_1000"}}


# ── per-user gate aggregates (backs check_achievements on SQL in prod) ──────

async def test_user_gate_counts_are_isolated_and_grouped_per_user(repo):
    await repo.add_gate_entry("a", 100, 1, "u1")
    await repo.add_gate_entry("a", 200, 1, "u1")
    await repo.add_gate_entry("b", 300, 1, "u1")
    await repo.add_gate_entry("a", 400, 2, "u2")
    assert await repo.user_gate_counts(1) == {"a": 2, "b": 1}
    assert await repo.user_gate_counts(2) == {"a": 1}


async def test_user_gate_counts_empty_without_entries(repo):
    assert await repo.user_gate_counts(99) == {}


async def test_user_gate_cost_total_sums_only_that_users_costs(repo):
    await repo.add_gate_entry("a", 400000, 1, "u1")
    await repo.add_gate_entry("b", 600000, 1, "u1")
    await repo.add_gate_entry("a", 999, 2, "u2")
    assert await repo.user_gate_cost_total(1) == 1000000
    assert await repo.user_gate_cost_total(2) == 999


async def test_user_gate_cost_total_zero_without_entries(repo):
    assert await repo.user_gate_cost_total(99) == 0
