"""RED tests for achievements Pass C, sub-feature 3: the additive rebuild.

This fixes v3 bug B17: the old ``/sync_achievements`` did
``DELETE FROM achievements`` first, so an interrupted run WIPED unlocks. The
Pass C rebuild is strictly ADDITIVE — it only inserts threshold-met unlock rows
that are missing and never removes an existing one.

Surfaces:

1. ``async recompute_user_achievements(repo, discord_id) -> list[Achievement]``
   — for every metric, unlock any threshold-met achievement not already
   recorded; additive only; returns the newly-recorded achievements.

2. ``async sync_all_achievements(repo) -> dict`` — runs ``recompute`` for every
   known user (union of ``list_achievement_holders`` keys and users with
   activity/gate data) and returns ``{"users_processed": int,
   "achievements_added": int}``.

3. A standalone ``!sync_achievements`` prefix command, gated on ``is_admin``:
   non-admins are refused and mutate nothing; admins trigger the additive sync.

New symbols are resolved lazily inside test bodies so a missing symbol fails the
individual test (correct pre-impl RED) rather than breaking collection.

Assumptions pinned here (flag for the Architect):
  * ``recompute_user_achievements`` / ``sync_all_achievements`` live in
    ``n3x_bot.achievements``. If homed elsewhere, only ``_mod`` changes.
  * ``sync_all_achievements`` enumerates users from ALL data sources (it uses
    ``export_all`` or equivalent), so a user with only activity data — no unlock
    row yet — is still processed. This is the B17 additive-recovery case.
  * ``sync_all_achievements`` return dict has keys ``users_processed`` and
    ``achievements_added``.
  * ``!sync_achievements`` is registered by ``build_bot`` and gated exactly like
    the other admin surfaces via ``n3x_bot.admin.is_admin``.
"""

import importlib
import os
import tempfile

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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
    return repo


def _mod():
    return importlib.import_module("n3x_bot.achievements")


def _member(*, member_id=1, role_ids=()):
    roles = [SimpleNamespace(id=r) for r in role_ids]
    return SimpleNamespace(id=member_id, roles=roles, bot=False)


# ── recompute_user_achievements ────────────────────────────────────────────

async def test_recompute_records_all_threshold_met_metrics():
    repo = await _flatfile_repo()
    await repo.add_activity(7, "messages", 1500)     # crosses msg_1000
    await repo.add_activity(7, "voice_seconds", 3700)  # crosses voice_3600

    newly = await _mod().recompute_user_achievements(repo, 7)

    got = {a.id for a in newly}
    assert {"msg_1000", "voice_3600"} <= got
    assert await repo.has_achievement(7, "msg_1000") is True
    assert await repo.has_achievement(7, "voice_3600") is True
    await repo.close()


async def test_recompute_is_idempotent_on_second_run():
    repo = await _flatfile_repo()
    await repo.add_activity(7, "messages", 1500)

    await _mod().recompute_user_achievements(repo, 7)
    again = await _mod().recompute_user_achievements(repo, 7)

    assert again == []
    await repo.close()


async def test_recompute_is_additive_and_never_wipes_existing():
    # A pre-existing unlock must survive; only the missing threshold-met row is
    # added. This is the core B17 invariant at the per-user level.
    repo = await _flatfile_repo()
    await repo.add_activity(7, "messages", 1500)      # msg_1000 met
    await repo.add_activity(7, "voice_seconds", 3700)   # voice_3600 met
    await repo.unlock_achievement(7, "msg_1000")       # already recorded

    newly = await _mod().recompute_user_achievements(repo, 7)

    assert {a.id for a in newly} == {"voice_3600"}     # only the missing one
    assert await repo.has_achievement(7, "msg_1000") is True   # NOT wiped
    assert await repo.has_achievement(7, "voice_3600") is True
    await repo.close()


# ── sync_all_achievements ──────────────────────────────────────────────────

async def test_sync_all_processes_every_user_with_data():
    repo = await _flatfile_repo()
    await repo.add_activity(7, "messages", 1500)      # user 7 -> msg_1000
    await repo.add_activity(8, "voice_seconds", 3700)   # user 8 -> voice_3600

    summary = await _mod().sync_all_achievements(repo)

    assert summary["users_processed"] >= 2
    assert summary["achievements_added"] >= 2
    assert await repo.has_achievement(7, "msg_1000") is True
    assert await repo.has_achievement(8, "voice_3600") is True
    await repo.close()


async def test_sync_all_is_idempotent_second_run_adds_zero():
    repo = await _flatfile_repo()
    await repo.add_activity(7, "messages", 1500)

    await _mod().sync_all_achievements(repo)
    summary = await _mod().sync_all_achievements(repo)

    assert summary["achievements_added"] == 0
    await repo.close()


async def test_sync_all_backfills_missing_row_for_over_threshold_user():
    # B17 recovery: a user whose metric is over-threshold but who has NO unlock
    # row (e.g. lost to the old wipe) gets the row back after a sync.
    repo = await _flatfile_repo()
    await repo.add_activity(7, "voice_seconds", 200000)  # crosses voice_180000
    assert await repo.get_user_achievements(7) == set()

    await _mod().sync_all_achievements(repo)

    assert await repo.has_achievement(7, "voice_180000") is True
    await repo.close()


# ── !sync_achievements command ─────────────────────────────────────────────

async def test_build_bot_registers_sync_achievements_command():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    assert bot.get_command("sync_achievements") is not None
    await repo.close()


async def test_sync_command_refuses_non_admin_and_mutates_nothing():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    await repo.add_activity(7, "messages", 1500)  # would unlock msg_1000

    cmd = bot.get_command("sync_achievements")
    assert cmd is not None
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.author = _member(member_id=5, role_ids=(999,))  # NOT the admin role

    await cmd.callback(ctx)

    assert await repo.has_achievement(7, "msg_1000") is False  # no mutation
    ctx.send.assert_awaited()  # a refusal was sent
    await repo.close()


async def test_sync_command_admin_records_threshold_met_achievements():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    await repo.add_activity(7, "messages", 1500)

    cmd = bot.get_command("sync_achievements")
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.author = _member(member_id=5, role_ids=(42,))  # admin role

    await cmd.callback(ctx)

    assert await repo.has_achievement(7, "msg_1000") is True
    await repo.close()


async def test_sync_command_admin_second_run_adds_nothing_new():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    await repo.add_activity(7, "messages", 1500)

    cmd = bot.get_command("sync_achievements")
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.author = _member(member_id=5, role_ids=(42,))

    await cmd.callback(ctx)
    before = await repo.list_achievement_holders()
    await cmd.callback(ctx)
    after = await repo.list_achievement_holders()

    assert after == before  # idempotent: the second run changed nothing
    await repo.close()
