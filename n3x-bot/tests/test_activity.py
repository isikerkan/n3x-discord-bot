"""RED tests for the activity-tracking feature (v1: tracking + storage + view).

Two surfaces are exercised here:

1. Pure, timezone-aware logic in ``n3x_bot.activity`` (no Discord). All new
   symbols are imported lazily inside each test via ``_act()`` so that a
   missing module surfaces as a runtime ModuleNotFoundError (the correct RED
   "missing symbol" signal) rather than a collection-time import error that
   would break the whole file.

2. Event-handler wiring in ``n3x_bot.bot`` (``register_activity`` + testable
   helpers), driven with fake Discord objects in the offline style of
   ``tests/test_bot_wiring.py``. New bot symbols are likewise imported inside
   each test.

Assumptions pinned here (flag for the Architect):
  * metric names: "voice_seconds", "messages", "reactions".
  * streak/night dict shapes carry ISO date STRINGS (matching the repo).
  * ``record_message_activity(repo, settings, member_id, now)`` — one helper
    that does message +1 / streak / night, with ``now`` injected (tz-aware).
  * ``handle_voice_state_update(bot, repo, settings, member, before, after, now)``.
  * ``handle_activity_reaction(bot, repo, settings, payload)``.
  * view command is a prefix command named "activity", callable as
    ``cmd.callback(ctx)`` for the invoking author.
"""

import importlib
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

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
    import os
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    repo = JsonRepository(path)
    await repo.connect()
    await seed_defaults(repo)
    return repo


def _act():
    """Lazily import the (not-yet-existing) pure-logic module."""
    return importlib.import_module("n3x_bot.activity")


def _vs(channel):
    """A fake VoiceState carrying just the ``.channel`` attribute the handler needs."""
    return SimpleNamespace(channel=channel)


def _send_text(send_mock) -> str:
    """Flatten everything a ``ctx.send`` call rendered (positional text + embed)."""
    args, kwargs = send_mock.await_args
    parts = [str(a) for a in args]
    embed = kwargs.get("embed")
    if embed is not None:
        parts.append(str(getattr(embed, "title", "")))
        parts.append(str(getattr(embed, "description", "")))
        for f in getattr(embed, "fields", None) or []:
            parts.append(str(getattr(f, "name", "")))
            parts.append(str(getattr(f, "value", "")))
    return " ".join(parts)


# ── pure logic: elapsed_seconds ───────────────────────────────────────────

def test_elapsed_seconds_returns_whole_seconds():
    act = _act()
    join = datetime(2026, 7, 13, 20, 0, 0)
    leave = join + timedelta(seconds=90)
    assert act.elapsed_seconds(join, leave) == 90


def test_elapsed_seconds_truncates_fractional_seconds():
    act = _act()
    join = datetime(2026, 7, 13, 20, 0, 0)
    leave = join + timedelta(seconds=90, milliseconds=900)
    assert act.elapsed_seconds(join, leave) == 90


def test_elapsed_seconds_is_zero_for_same_instant():
    act = _act()
    t = datetime(2026, 7, 13, 20, 0, 0)
    assert act.elapsed_seconds(t, t) == 0


# ── pure logic: next_streak ───────────────────────────────────────────────

def test_next_streak_first_activity_starts_at_one():
    act = _act()
    result = act.next_streak(None, date(2026, 7, 13))
    assert result == {"current_streak": 1, "last_active_date": "2026-07-13",
                      "max_streak": 1}


def test_next_streak_same_day_is_unchanged():
    act = _act()
    prev = {"current_streak": 3, "last_active_date": "2026-07-13", "max_streak": 5}
    assert act.next_streak(prev, date(2026, 7, 13)) == prev


def test_next_streak_consecutive_day_increments_current():
    act = _act()
    prev = {"current_streak": 2, "last_active_date": "2026-07-12", "max_streak": 5}
    assert act.next_streak(prev, date(2026, 7, 13)) == {
        "current_streak": 3, "last_active_date": "2026-07-13", "max_streak": 5}


def test_next_streak_consecutive_day_raises_max_when_surpassed():
    act = _act()
    prev = {"current_streak": 5, "last_active_date": "2026-07-12", "max_streak": 5}
    assert act.next_streak(prev, date(2026, 7, 13)) == {
        "current_streak": 6, "last_active_date": "2026-07-13", "max_streak": 6}


def test_next_streak_gap_resets_current_but_keeps_max():
    act = _act()
    prev = {"current_streak": 5, "last_active_date": "2026-07-10", "max_streak": 5}
    assert act.next_streak(prev, date(2026, 7, 13)) == {
        "current_streak": 1, "last_active_date": "2026-07-13", "max_streak": 5}


# ── pure logic: is_night ──────────────────────────────────────────────────

def test_is_night_true_at_midnight():
    act = _act()
    assert act.is_night(datetime(2026, 7, 13, 0, 0)) is True


def test_is_night_true_at_0459():
    act = _act()
    assert act.is_night(datetime(2026, 7, 13, 4, 59)) is True


def test_is_night_false_at_0500():
    act = _act()
    assert act.is_night(datetime(2026, 7, 13, 5, 0)) is False


def test_is_night_false_at_2359():
    act = _act()
    assert act.is_night(datetime(2026, 7, 13, 23, 59)) is False


# ── pure logic: next_night ────────────────────────────────────────────────

def test_next_night_first_time_starts_at_one():
    act = _act()
    assert act.next_night(None, date(2026, 7, 13)) == {
        "night_count": 1, "last_night_date": "2026-07-13"}


def test_next_night_already_counted_today_is_noop():
    act = _act()
    prev = {"night_count": 4, "last_night_date": "2026-07-13"}
    # spec allows either "unchanged dict" or None for the already-counted case
    assert act.next_night(prev, date(2026, 7, 13)) in (None, prev)


def test_next_night_new_day_increments():
    act = _act()
    prev = {"night_count": 4, "last_night_date": "2026-07-12"}
    assert act.next_night(prev, date(2026, 7, 13)) == {
        "night_count": 5, "last_night_date": "2026-07-13"}


# ── pure logic: tz helpers ────────────────────────────────────────────────

def test_now_local_is_tz_aware_and_honors_settings_timezone():
    act = _act()
    settings = _settings(timezone="America/New_York")
    now = act.now_local(settings)
    assert now.tzinfo is not None
    expected = datetime.now(ZoneInfo("America/New_York")).utcoffset()
    assert now.utcoffset() == expected


def test_today_local_matches_settings_timezone_date():
    act = _act()
    settings = _settings(timezone="Europe/Berlin")
    assert act.today_local(settings) == datetime.now(ZoneInfo("Europe/Berlin")).date()


# ── handler: register_activity + build_bot wiring ─────────────────────────

async def test_build_bot_registers_activity_view_command():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    assert bot.get_command("activity") is not None
    await repo.close()


async def test_register_activity_registers_command_on_bot():
    from n3x_bot.bot import register_activity
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    register_activity(bot, repo, settings)
    assert bot.get_command("activity") is not None
    await repo.close()


# ── handler: on_message activity (message / streak / night) ───────────────

async def test_record_message_activity_increments_message_count():
    act = _act()
    repo = await _flatfile_repo()
    settings = _settings()
    now = datetime(2026, 7, 13, 12, 0, tzinfo=ZoneInfo(settings.timezone))
    await act.record_message_activity(repo, settings, 7, now)
    assert await repo.get_activity(7, "messages") == 1
    await repo.close()


async def test_record_message_activity_starts_streak_for_today():
    act = _act()
    repo = await _flatfile_repo()
    settings = _settings()
    now = datetime(2026, 7, 13, 12, 0, tzinfo=ZoneInfo(settings.timezone))
    await act.record_message_activity(repo, settings, 7, now)
    assert await repo.get_streak(7) == {
        "current_streak": 1, "last_active_date": "2026-07-13", "max_streak": 1}
    await repo.close()


async def test_record_message_activity_counts_night_inside_window():
    act = _act()
    repo = await _flatfile_repo()
    settings = _settings()
    now = datetime(2026, 7, 13, 2, 0, tzinfo=ZoneInfo(settings.timezone))
    await act.record_message_activity(repo, settings, 7, now)
    night = await repo.get_night(7)
    assert night == {"night_count": 1, "last_night_date": "2026-07-13"}
    await repo.close()


async def test_record_message_activity_skips_night_outside_window():
    act = _act()
    repo = await _flatfile_repo()
    settings = _settings()
    now = datetime(2026, 7, 13, 12, 0, tzinfo=ZoneInfo(settings.timezone))
    await act.record_message_activity(repo, settings, 7, now)
    assert await repo.get_night(7) is None
    await repo.close()


# ── handler: on_voice_state_update ────────────────────────────────────────

async def test_voice_join_then_leave_persists_elapsed_seconds():
    from n3x_bot.bot import register_activity, handle_voice_state_update
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    register_activity(bot, repo, settings)

    member = SimpleNamespace(id=7, bot=False)
    channel = SimpleNamespace(id=100)
    t0 = datetime(2026, 7, 13, 20, 0, tzinfo=ZoneInfo(settings.timezone))
    t1 = t0 + timedelta(seconds=90)

    await handle_voice_state_update(bot, repo, settings, member,
                                    _vs(None), _vs(channel), t0)   # join
    await handle_voice_state_update(bot, repo, settings, member,
                                    _vs(channel), _vs(None), t1)   # leave

    assert await repo.get_activity(7, "voice_seconds") == 90
    await repo.close()


async def test_voice_join_alone_persists_nothing():
    from n3x_bot.bot import register_activity, handle_voice_state_update
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    register_activity(bot, repo, settings)

    member = SimpleNamespace(id=7, bot=False)
    channel = SimpleNamespace(id=100)
    t0 = datetime(2026, 7, 13, 20, 0, tzinfo=ZoneInfo(settings.timezone))

    await handle_voice_state_update(bot, repo, settings, member,
                                    _vs(None), _vs(channel), t0)   # join only

    assert await repo.get_activity(7, "voice_seconds") == 0
    await repo.close()


# ── handler: on_raw_reaction_add ──────────────────────────────────────────

async def test_reaction_increments_reaction_count():
    from n3x_bot.bot import register_activity, handle_activity_reaction
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777, gate_stats_channel_id=888)
    bot = build_bot(settings, repo)
    register_activity(bot, repo, settings)

    payload = SimpleNamespace(user_id=7, channel_id=555,
                              member=SimpleNamespace(id=7, bot=False))
    await handle_activity_reaction(bot, repo, settings, payload)

    assert await repo.get_activity(7, "reactions") == 1
    await repo.close()


async def test_reaction_skipped_in_gate_input_channel():
    from n3x_bot.bot import register_activity, handle_activity_reaction
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777, gate_stats_channel_id=888)
    bot = build_bot(settings, repo)
    register_activity(bot, repo, settings)

    payload = SimpleNamespace(user_id=7, channel_id=777,
                              member=SimpleNamespace(id=7, bot=False))
    await handle_activity_reaction(bot, repo, settings, payload)

    assert await repo.get_activity(7, "reactions") == 0
    await repo.close()


async def test_reaction_skipped_in_gate_stats_channel():
    from n3x_bot.bot import register_activity, handle_activity_reaction
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777, gate_stats_channel_id=888)
    bot = build_bot(settings, repo)
    register_activity(bot, repo, settings)

    payload = SimpleNamespace(user_id=7, channel_id=888,
                              member=SimpleNamespace(id=7, bot=False))
    await handle_activity_reaction(bot, repo, settings, payload)

    assert await repo.get_activity(7, "reactions") == 0
    await repo.close()


# ── view command: !activity ───────────────────────────────────────────────

async def test_activity_command_reports_tracked_values():
    from n3x_bot.bot import register_activity
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    register_activity(bot, repo, settings)

    await repo.add_activity(7, "messages", 17)
    await repo.add_activity(7, "reactions", 23)
    await repo.add_activity(7, "voice_seconds", 3661)
    await repo.set_streak(7, 13, "2026-07-13", 29)
    await repo.set_night(7, 8, "2026-07-13")

    cmd = bot.get_command("activity")
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.author = SimpleNamespace(id=7, display_name="Erkan")

    await cmd.callback(ctx)

    ctx.send.assert_awaited_once()
    blob = _send_text(ctx.send)
    for token in ("17", "23", "13", "29", "8"):
        assert token in blob, f"expected {token!r} in activity summary: {blob!r}"

    await repo.close()
