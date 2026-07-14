"""RED-phase specs for the Base-timers feature (v3 port #6).

Role-gated per-map countdown timers with a self-editing overview embed.

New surface (to be implemented downstream in a new module ``n3x_bot/timers.py``):

    build_timer_overview_embed(timers: dict[str, datetime], now: datetime) -> discord.Embed
    has_base_timer_role(member, settings) -> bool
    async start_base_timer(repo, settings, map_name, minutes, now) -> datetime
    async update_timer_overview(bot, repo, settings, now) -> None
    register_timer_commands(bot, repo, settings) -> None          # !base, !basestop
    start_timer_overview_loop(bot, repo, settings) -> tasks.Loop  # guarded start

Plus bot.py wiring: ``build_bot`` registers the base/basestop commands.

Bugs fixed vs v3:
  * B12 — timers were in-memory (lost on restart); now persisted to the repo.
  * B4  — the overview loop was ``.start()``-ed unguarded and crashed on
          reconnect; ``start_timer_overview_loop`` starts only if not running.
  * B6  — naive ``datetime.now()``; ``now`` is tz-aware and injected into the
          pure/logic functions for deterministic tests (the 30s loop supplies
          ``datetime.now(ZoneInfo(settings.timezone))``).

The ``n3x_bot.timers`` module does not exist yet, so it is imported lazily INSIDE
each test body: collection still succeeds, and every test fails with
ModuleNotFoundError (the correct pre-impl RED) rather than breaking the file.
Tests that touch new config fields / new command wiring instead RED on
AttributeError / AssertionError.
"""

import os
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import discord

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

TZ = ZoneInfo("Europe/Berlin")


def _settings(**overrides) -> Settings:
    kwargs = dict(BASE_SETTINGS_KWARGS)
    kwargs.update(overrides)
    # Settings has extra="ignore", so pre-impl the new base-timer fields are
    # silently dropped at construction — the RED lands on the feature under
    # test (missing module / missing attribute), not on Settings itself.
    return Settings(**kwargs)


async def _flatfile_repo() -> JsonRepository:
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    repo = JsonRepository(path)
    await repo.connect()
    await seed_defaults(repo)
    return repo


def _member(*, member_id=1, role_ids=()):
    return SimpleNamespace(id=member_id,
                           roles=[SimpleNamespace(id=r) for r in role_ids])


def _now() -> datetime:
    return datetime(2026, 7, 14, 12, 0, 0, tzinfo=TZ)


def _overview_channel():
    """Fake overview channel: get_channel -> channel -> fetch_message -> msg,
    with msg.edit an AsyncMock so we can assert the self-edit happened."""
    msg = SimpleNamespace(edit=AsyncMock())
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=msg)
    channel._msg = msg
    return channel


# ── build_timer_overview_embed (pure) ──────────────────────────────────────

async def test_overview_embed_has_the_v3_title():
    from n3x_bot import timers
    embed = timers.build_timer_overview_embed({}, _now())
    assert embed.title == "🛰️ BASE TIMER ÜBERSICHT"


async def test_overview_embed_empty_shows_no_timers_line_in_red():
    from n3x_bot import timers
    embed = timers.build_timer_overview_embed({}, _now())
    assert embed.description == "Keine aktiven Base Timer."
    assert embed.color == discord.Color.red()


async def test_overview_embed_populated_is_blue():
    from n3x_bot import timers
    now = _now()
    embed = timers.build_timer_overview_embed(
        {"4-1": now + timedelta(minutes=30)}, now)
    assert embed.color == discord.Color.blue()


async def test_overview_embed_lines_are_sorted_by_end_time_ascending():
    from n3x_bot import timers
    now = _now()
    timers_map = {
        "1-5": now + timedelta(minutes=90),
        "4-1": now + timedelta(minutes=30),
    }
    embed = timers.build_timer_overview_embed(timers_map, now)
    assert embed.description == (
        "📍 **4-1** — 30 Min\n"
        "📍 **1-5** — 90 Min"
    )


async def test_overview_embed_floors_remaining_minutes():
    from n3x_bot import timers
    now = _now()
    # 95 seconds -> 1 whole minute (floor via //60).
    embed = timers.build_timer_overview_embed(
        {"2-6": now + timedelta(seconds=95)}, now)
    assert embed.description == "📍 **2-6** — 1 Min"


async def test_overview_embed_past_timer_clamps_to_zero_minutes():
    from n3x_bot import timers
    now = _now()
    # PIN: the builder renders every timer handed to it and clamps remaining to
    # max(0, ...); the CALLER (update_timer_overview) is what drops expired rows.
    embed = timers.build_timer_overview_embed(
        {"3-7": now - timedelta(minutes=5)}, now)
    assert embed.description == "📍 **3-7** — 0 Min"


# ── has_base_timer_role ─────────────────────────────────────────────────────

async def test_has_base_timer_role_true_when_member_holds_it():
    from n3x_bot import timers
    settings = _settings(base_timer_role_id=555)
    member = _member(role_ids=(111, 555))
    assert timers.has_base_timer_role(member, settings) is True


async def test_has_base_timer_role_false_without_it():
    from n3x_bot import timers
    settings = _settings(base_timer_role_id=555)
    member = _member(role_ids=(111, 222))
    assert timers.has_base_timer_role(member, settings) is False


async def test_has_base_timer_role_false_when_unconfigured():
    from n3x_bot import timers
    settings = _settings(base_timer_role_id=0)
    member = _member(role_ids=(0,))
    assert timers.has_base_timer_role(member, settings) is False


# ── start_base_timer ────────────────────────────────────────────────────────

async def test_start_base_timer_stores_end_time_now_plus_minutes():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings()
    now = _now()

    end = await timers.start_base_timer(repo, settings, "4-1", 30, now)

    assert end == now + timedelta(minutes=30)
    stored = await repo.list_base_timers()
    assert stored["4-1"] == now + timedelta(minutes=30)
    await repo.close()


async def test_start_base_timer_end_time_is_tz_aware():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings()

    end = await timers.start_base_timer(repo, settings, "4-1", 30, _now())

    assert end.tzinfo is not None
    await repo.close()


async def test_start_base_timer_rejects_map_outside_allowed_list():
    from n3x_bot import timers
    import pytest
    repo = await _flatfile_repo()
    settings = _settings()

    # PIN: an invalid map raises ValueError (mirrors the admin CRUD helpers,
    # surfaced to the user via on_command_error) and stores nothing.
    with pytest.raises(ValueError):
        await timers.start_base_timer(repo, settings, "9-9", 30, _now())
    assert await repo.list_base_timers() == {}
    await repo.close()


# ── update_timer_overview (self-editing embed, best-effort) ─────────────────

async def test_update_timer_overview_edits_the_fixed_message():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings(timer_overview_channel_id=222,
                         timer_overview_message_id=333)
    now = _now()
    await repo.set_base_timer("4-1", now + timedelta(minutes=30))

    channel = _overview_channel()
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)

    await timers.update_timer_overview(bot, repo, settings, now)

    channel.fetch_message.assert_awaited_once_with(333)
    channel._msg.edit.assert_awaited_once()
    # edited with a discord.Embed
    _, kwargs = channel._msg.edit.call_args
    assert isinstance(kwargs.get("embed"), discord.Embed)
    await repo.close()


async def test_update_timer_overview_purges_expired_before_rendering():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings(timer_overview_channel_id=222,
                         timer_overview_message_id=333)
    now = _now()
    await repo.set_base_timer("4-1", now - timedelta(minutes=5))   # expired
    await repo.set_base_timer("1-5", now + timedelta(minutes=10))  # active

    channel = _overview_channel()
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)

    await timers.update_timer_overview(bot, repo, settings, now)

    # expired row is gone from storage after the render (B12 reconcile)
    assert set(await repo.list_base_timers()) == {"1-5"}
    # and the rendered embed only mentions the active map
    _, kwargs = channel._msg.edit.call_args
    assert "1-5" in kwargs["embed"].description
    assert "4-1" not in kwargs["embed"].description
    await repo.close()


async def test_update_timer_overview_noop_when_channel_missing():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings(timer_overview_channel_id=222,
                         timer_overview_message_id=333)
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=None)

    # must not raise
    await timers.update_timer_overview(bot, repo, settings, _now())
    await repo.close()


async def test_update_timer_overview_swallows_fetch_failure():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings(timer_overview_channel_id=222,
                         timer_overview_message_id=333)
    channel = MagicMock()
    channel.fetch_message = AsyncMock(side_effect=RuntimeError("gone"))
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)

    # missing/deleted overview message -> best-effort, no raise
    await timers.update_timer_overview(bot, repo, settings, _now())
    await repo.close()


async def test_update_timer_overview_swallows_edit_failure():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings(timer_overview_channel_id=222,
                         timer_overview_message_id=333)
    channel = _overview_channel()
    channel._msg.edit = AsyncMock(side_effect=RuntimeError("forbidden"))
    channel.fetch_message = AsyncMock(return_value=channel._msg)
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)

    await timers.update_timer_overview(bot, repo, settings, _now())  # no raise
    await repo.close()


# ── register_timer_commands ─────────────────────────────────────────────────

async def test_register_timer_commands_registers_base_and_basestop():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    timers.register_timer_commands(bot, repo, settings)

    assert bot.get_command("base") is not None
    assert bot.get_command("basestop") is not None
    await repo.close()


async def test_register_timer_commands_is_idempotent():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    timers.register_timer_commands(bot, repo, settings)
    timers.register_timer_commands(bot, repo, settings)  # must not raise/dup

    assert bot.get_command("base") is not None
    assert bot.get_command("basestop") is not None
    await repo.close()


async def test_base_command_stores_timer_and_refreshes_overview_for_role_holder():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings(base_timer_role_id=555,
                         timer_overview_channel_id=222,
                         timer_overview_message_id=333)
    bot = build_bot(settings, repo)
    channel = _overview_channel()
    bot.get_channel = MagicMock(return_value=channel)
    timers.register_timer_commands(bot, repo, settings)

    ctx = MagicMock()
    ctx.author = _member(role_ids=(555,))
    ctx.send = AsyncMock()

    await bot.get_command("base").callback(ctx, "4-1", 30)

    stored = await repo.list_base_timers()
    assert "4-1" in stored
    assert stored["4-1"].tzinfo is not None  # B6: tz-aware
    channel._msg.edit.assert_awaited()       # overview refreshed
    await repo.close()


async def test_base_command_refused_for_non_role_holder():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings(base_timer_role_id=555,
                         timer_overview_channel_id=222,
                         timer_overview_message_id=333)
    bot = build_bot(settings, repo)
    channel = _overview_channel()
    bot.get_channel = MagicMock(return_value=channel)
    timers.register_timer_commands(bot, repo, settings)

    ctx = MagicMock()
    ctx.author = _member(role_ids=(111,))  # lacks the base-timer role
    ctx.send = AsyncMock()

    await bot.get_command("base").callback(ctx, "4-1", 30)

    assert await repo.list_base_timers() == {}   # nothing stored
    channel._msg.edit.assert_not_awaited()        # overview not touched
    ctx.send.assert_awaited()                     # user got a refusal
    await repo.close()


async def test_base_command_rejects_invalid_map_and_names_allowed_maps():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings(base_timer_role_id=555,
                         timer_overview_channel_id=222,
                         timer_overview_message_id=333)
    bot = build_bot(settings, repo)
    timers.register_timer_commands(bot, repo, settings)

    ctx = MagicMock()
    ctx.author = _member(role_ids=(555,))
    ctx.send = AsyncMock()

    await bot.get_command("base").callback(ctx, "9-9", 30)  # must not crash

    assert await repo.list_base_timers() == {}
    ctx.send.assert_awaited()
    sent = " ".join(str(a) for c in ctx.send.call_args_list for a in c.args)
    assert "4-1" in sent  # the allowed-map list is surfaced to the user
    await repo.close()


async def test_basestop_command_removes_timer_and_refreshes_overview():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings(base_timer_role_id=555,
                         timer_overview_channel_id=222,
                         timer_overview_message_id=333)
    bot = build_bot(settings, repo)
    channel = _overview_channel()
    bot.get_channel = MagicMock(return_value=channel)
    timers.register_timer_commands(bot, repo, settings)
    await repo.set_base_timer("4-1", _now() + timedelta(minutes=30))

    ctx = MagicMock()
    ctx.author = _member(role_ids=(555,))
    ctx.send = AsyncMock()

    await bot.get_command("basestop").callback(ctx, "4-1")

    assert await repo.list_base_timers() == {}
    channel._msg.edit.assert_awaited()
    await repo.close()


async def test_basestop_command_on_unknown_map_reports_no_active_timer():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings(base_timer_role_id=555,
                         timer_overview_channel_id=222,
                         timer_overview_message_id=333)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=_overview_channel())
    timers.register_timer_commands(bot, repo, settings)

    ctx = MagicMock()
    ctx.author = _member(role_ids=(555,))
    ctx.send = AsyncMock()

    await bot.get_command("basestop").callback(ctx, "4-1")  # not running

    ctx.send.assert_awaited()  # a "kein aktiver Timer" style reply, no crash
    await repo.close()


async def test_basestop_command_refused_for_non_role_holder():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings(base_timer_role_id=555)
    bot = build_bot(settings, repo)
    timers.register_timer_commands(bot, repo, settings)
    await repo.set_base_timer("4-1", _now() + timedelta(minutes=30))

    ctx = MagicMock()
    ctx.author = _member(role_ids=(111,))
    ctx.send = AsyncMock()

    await bot.get_command("basestop").callback(ctx, "4-1")

    assert set(await repo.list_base_timers()) == {"4-1"}  # untouched
    await repo.close()


# ── start_timer_overview_loop (B4: guarded start) ───────────────────────────

async def test_start_timer_overview_loop_starts_the_loop():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings(timer_overview_channel_id=0)  # get_channel None -> noop body
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=None)

    loop = timers.start_timer_overview_loop(bot, repo, settings)
    try:
        assert loop.is_running() is True
    finally:
        loop.cancel()
    await repo.close()


async def test_start_timer_overview_loop_is_guarded_against_double_start():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings(timer_overview_channel_id=0)
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=None)

    loop = timers.start_timer_overview_loop(bot, repo, settings)
    # B4: a second call (e.g. a reconnect) must NOT raise "already running".
    loop2 = timers.start_timer_overview_loop(bot, repo, settings)
    try:
        assert loop2.is_running() is True
    finally:
        loop.cancel()
        loop2.cancel()
    await repo.close()


# ── bot.py wiring ───────────────────────────────────────────────────────────

async def test_build_bot_registers_base_and_basestop():
    repo = await _flatfile_repo()
    settings = _settings(base_timer_role_id=555)

    bot = build_bot(settings, repo)

    assert bot.get_command("base") is not None
    assert bot.get_command("basestop") is not None
    await repo.close()
