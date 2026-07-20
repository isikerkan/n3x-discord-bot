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
  * ``record_message_activity(bot, repo, settings, member_id, now)`` — one helper
    that does message +1 / streak / night, with ``now`` injected (tz-aware).
  * ``handle_voice_state_update(bot, repo, settings, member, before, after, now)``.
  * ``handle_activity_reaction(bot, repo, settings, payload)``.
  * view command is a prefix command named "activity", callable as
    ``cmd.callback(ctx)`` for the invoking author.
"""

import asyncio
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


def _defs_bot():
    """Minimal bot stand-in carrying the code-default achievement resolver
    (record_message_activity reads bot.achievement_defs for live defs)."""
    from types import SimpleNamespace
    from n3x_bot.achievement_defs import AchievementDefs
    return SimpleNamespace(achievement_defs=AchievementDefs())

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

async def test_build_bot_registers_activity_app_command():
    # Phase 1: activity is slash-ONLY — an app command on the tree, not a
    # prefix command in bot.commands.
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    assert bot.get_command("activity") is None
    assert bot.tree.get_command("activity") is not None
    await repo.close()


async def test_register_activity_registers_app_command_on_tree():
    from n3x_bot.bot import register_activity
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    register_activity(bot, repo, settings)
    assert bot.get_command("activity") is None
    assert bot.tree.get_command("activity") is not None
    await repo.close()


# ── handler: on_message activity (message / streak / night) ───────────────

async def test_record_message_activity_increments_message_count():
    act = _act()
    repo = await _flatfile_repo()
    settings = _settings()
    now = datetime(2026, 7, 13, 12, 0, tzinfo=ZoneInfo(settings.timezone))
    await act.record_message_activity(_defs_bot(), repo, settings, 7, now)
    assert await repo.get_activity(7, "messages") == 1
    await repo.close()


async def test_record_message_activity_starts_streak_for_today():
    act = _act()
    repo = await _flatfile_repo()
    settings = _settings()
    now = datetime(2026, 7, 13, 12, 0, tzinfo=ZoneInfo(settings.timezone))
    await act.record_message_activity(_defs_bot(), repo, settings, 7, now)
    assert await repo.get_streak(7) == {
        "current_streak": 1, "last_active_date": "2026-07-13", "max_streak": 1}
    await repo.close()


async def test_record_message_activity_counts_night_inside_window():
    act = _act()
    repo = await _flatfile_repo()
    settings = _settings()
    now = datetime(2026, 7, 13, 2, 0, tzinfo=ZoneInfo(settings.timezone))
    await act.record_message_activity(_defs_bot(), repo, settings, 7, now)
    night = await repo.get_night(7)
    assert night == {"night_count": 1, "last_night_date": "2026-07-13"}
    await repo.close()


async def test_record_message_activity_skips_night_outside_window():
    act = _act()
    repo = await _flatfile_repo()
    settings = _settings()
    now = datetime(2026, 7, 13, 12, 0, tzinfo=ZoneInfo(settings.timezone))
    await act.record_message_activity(_defs_bot(), repo, settings, 7, now)
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


async def test_reaction_skipped_in_overview_channel():
    # Pass C / B9: the overview channel's ⬅️/➡️ nav reactions are UI controls,
    # not engagement, so a reaction there must NOT bump the reaction counter.
    from n3x_bot.bot import register_activity, handle_activity_reaction
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777, gate_stats_channel_id=888,
                         overview_channel_id=1234)
    bot = build_bot(settings, repo)
    register_activity(bot, repo, settings)

    payload = SimpleNamespace(user_id=7, channel_id=1234,
                              member=SimpleNamespace(id=7, bot=False))
    await handle_activity_reaction(bot, repo, settings, payload)

    assert await repo.get_activity(7, "reactions") == 0
    await repo.close()


# ── handler: voice MOVE + bot skip ─────────────────────────────────────────

async def test_voice_move_credits_both_segments():
    from n3x_bot.bot import handle_voice_state_update
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    member = SimpleNamespace(id=7, bot=False)
    chan_a = SimpleNamespace(id=100)
    chan_b = SimpleNamespace(id=200)
    t0 = datetime(2026, 7, 13, 20, 0, tzinfo=ZoneInfo(settings.timezone))
    t1 = t0 + timedelta(seconds=30)   # move A -> B
    t2 = t1 + timedelta(seconds=60)   # leave B

    await handle_voice_state_update(bot, repo, settings, member,
                                    _vs(None), _vs(chan_a), t0)      # join A
    await handle_voice_state_update(bot, repo, settings, member,
                                    _vs(chan_a), _vs(chan_b), t1)    # move A->B
    await handle_voice_state_update(bot, repo, settings, member,
                                    _vs(chan_b), _vs(None), t2)      # leave B

    assert await repo.get_activity(7, "voice_seconds") == 90  # 30 + 60
    assert 7 not in bot.voice_join_times
    await repo.close()


async def test_voice_state_update_noops_for_bot_member():
    from n3x_bot.bot import handle_voice_state_update
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    member = SimpleNamespace(id=9, bot=True)
    chan = SimpleNamespace(id=100)
    t0 = datetime(2026, 7, 13, 20, 0, tzinfo=ZoneInfo(settings.timezone))

    await handle_voice_state_update(bot, repo, settings, member,
                                    _vs(None), _vs(chan), t0)

    assert 9 not in bot.voice_join_times
    assert await repo.get_activity(9, "voice_seconds") == 0
    await repo.close()


# ── shutdown flush: credit in-progress voice time on exit ────────────────────

async def test_flush_voice_on_shutdown_credits_in_progress_time():
    from n3x_bot.__main__ import _flush_voice_on_shutdown
    from n3x_bot.activity import now_local
    from datetime import timedelta
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    # a member connected ~120s ago with no flush yet
    bot.voice_join_times[7] = now_local(settings) - timedelta(seconds=120)

    await _flush_voice_on_shutdown(bot, repo, settings)

    secs = await repo.get_activity(7, "voice_seconds")
    assert 120 <= secs <= 123          # credited the in-progress interval
    assert bot.voice_join_times.get(7) is not None  # reset, not dropped
    await repo.close()


async def test_flush_voice_on_shutdown_noop_when_nobody_connected():
    from n3x_bot.__main__ import _flush_voice_on_shutdown
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    # no voice_join_times -> nothing to flush, must not raise
    await _flush_voice_on_shutdown(bot, repo, settings)
    assert await repo.get_activity(7, "voice_seconds") == 0
    await repo.close()


# ── voice-session persistence + recovery ─────────────────────────────────────

def _vbot(*guilds):
    import asyncio
    return SimpleNamespace(guilds=list(guilds), voice_lock=asyncio.Lock(),
                           voice_join_times={})


def _vguild(*member_ids):
    ch = SimpleNamespace(members=[SimpleNamespace(id=i, bot=False)
                                  for i in member_ids])
    return SimpleNamespace(voice_channels=[ch])


async def test_voice_session_repo_roundtrip():
    repo = await _flatfile_repo()
    settings = _settings()
    t = datetime(2026, 7, 13, 20, 0, tzinfo=ZoneInfo(settings.timezone))
    await repo.voice_session_set(7, t)
    assert (await repo.voice_sessions_all()) == {7: t}
    assert await repo.voice_session_end(7) == t
    assert (await repo.voice_sessions_all()) == {}
    await repo.close()


async def test_recover_credits_in_progress_for_member_still_in_voice():
    from n3x_bot.activity import recover_voice_sessions
    repo = await _flatfile_repo()
    settings = _settings()
    now = datetime(2026, 7, 13, 20, 5, tzinfo=ZoneInfo(settings.timezone))
    await repo.voice_session_set(7, now - timedelta(seconds=90))
    bot = _vbot(_vguild(7))            # member 7 still in voice

    recovered = await recover_voice_sessions(bot, repo, settings, now)

    assert recovered == 90
    assert await repo.get_activity(7, "voice_seconds") == 90
    assert bot.voice_join_times[7] == now             # re-checkpointed
    assert (await repo.voice_sessions_all())[7] == now
    await repo.close()


async def test_recover_discards_session_when_member_left_during_downtime():
    from n3x_bot.activity import recover_voice_sessions
    repo = await _flatfile_repo()
    settings = _settings()
    now = datetime(2026, 7, 13, 20, 5, tzinfo=ZoneInfo(settings.timezone))
    await repo.voice_session_set(8, now - timedelta(seconds=90))
    bot = _vbot(_vguild())            # nobody in voice now

    recovered = await recover_voice_sessions(bot, repo, settings, now)

    assert recovered == 0
    assert await repo.get_activity(8, "voice_seconds") == 0   # not credited
    assert (await repo.voice_sessions_all()) == {}            # session discarded
    await repo.close()


async def test_recover_caps_long_outage():
    from n3x_bot.activity import recover_voice_sessions, VOICE_RECOVERY_CAP_SECONDS
    repo = await _flatfile_repo()
    settings = _settings()
    now = datetime(2026, 7, 13, 20, 5, tzinfo=ZoneInfo(settings.timezone))
    await repo.voice_session_set(9, now - timedelta(hours=6))   # long gap
    bot = _vbot(_vguild(9))

    recovered = await recover_voice_sessions(bot, repo, settings, now)

    assert recovered == VOICE_RECOVERY_CAP_SECONDS   # capped, not 6h
    await repo.close()


async def test_recover_seeds_new_in_voice_member_without_session():
    from n3x_bot.activity import recover_voice_sessions
    repo = await _flatfile_repo()
    settings = _settings()
    now = datetime(2026, 7, 13, 20, 5, tzinfo=ZoneInfo(settings.timezone))
    bot = _vbot(_vguild(10))          # in voice, no persisted session

    recovered = await recover_voice_sessions(bot, repo, settings, now)

    assert recovered == 0
    assert bot.voice_join_times[10] == now
    assert (await repo.voice_sessions_all())[10] == now
    await repo.close()


async def test_leave_ends_persisted_session():
    from n3x_bot.bot import handle_voice_state_update
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    member = SimpleNamespace(id=7, bot=False)
    chan = SimpleNamespace(id=100)
    t0 = datetime(2026, 7, 13, 20, 0, tzinfo=ZoneInfo(settings.timezone))
    t1 = t0 + timedelta(seconds=30)

    await handle_voice_state_update(bot, repo, settings, member,
                                    _vs(None), _vs(chan), t0)   # join
    assert (await repo.voice_sessions_all()) == {7: t0}         # persisted
    await handle_voice_state_update(bot, repo, settings, member,
                                    _vs(chan), _vs(None), t1)   # leave
    assert (await repo.voice_sessions_all()) == {}             # ended
    assert await repo.get_activity(7, "voice_seconds") == 30
    await repo.close()


# ── handler: reaction skips (member None / bot) ────────────────────────────

async def test_reaction_skipped_when_member_is_none():
    from n3x_bot.bot import handle_activity_reaction
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777, gate_stats_channel_id=888)
    bot = build_bot(settings, repo)

    payload = SimpleNamespace(user_id=7, channel_id=555, member=None)
    await handle_activity_reaction(bot, repo, settings, payload)

    assert await repo.get_activity(7, "reactions") == 0
    await repo.close()


async def test_reaction_skipped_for_bot_member():
    from n3x_bot.bot import handle_activity_reaction
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777, gate_stats_channel_id=888)
    bot = build_bot(settings, repo)

    payload = SimpleNamespace(user_id=7, channel_id=555,
                              member=SimpleNamespace(id=7, bot=True))
    await handle_activity_reaction(bot, repo, settings, payload)

    assert await repo.get_activity(7, "reactions") == 0
    await repo.close()


# ── flush helper: credit + reset invariant, no race, no phantom ────────────

class _AwaitingRepo:
    """A repo whose add_activity actually suspends (mimics SQL backend I/O),
    so the flush task and leave handler can genuinely interleave."""
    def __init__(self):
        self.credited: dict = {}

    async def add_activity(self, member_id, metric, amount):
        await asyncio.sleep(0.005)  # real suspension point
        key = (member_id, metric)
        self.credited[key] = self.credited.get(key, 0) + amount
        return self.credited[key]

    # Minimal stubs so the voice handler's post-credit check_achievements() call
    # resolves; they don't affect the double-count/phantom race invariant.
    async def get_activity(self, member_id, metric):
        return self.credited.get((member_id, metric), 0)

    async def get_user_achievements(self, discord_id):
        return set()

    async def unlock_achievement(self, discord_id, achievement_id):
        return True

    # voice-session mirror stubs (no-ops for the race invariant)
    async def voice_session_set(self, discord_id, since):
        return None

    async def voice_session_end(self, discord_id):
        return None


async def test_flush_voice_times_credits_elapsed_and_resets_join():
    from n3x_bot.activity import flush_voice_times
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    t0 = datetime(2026, 7, 13, 20, 0, tzinfo=ZoneInfo(settings.timezone))
    bot.voice_join_times[7] = t0                 # a live session (e.g. seeded on_ready)
    flush_now = t0 + timedelta(seconds=120)

    await flush_voice_times(bot, repo, flush_now)

    assert await repo.get_activity(7, "voice_seconds") == 120   # elapsed credited
    assert bot.voice_join_times[7] == flush_now                 # reset, still tracked
    await repo.close()


async def test_flush_unlocks_voice_achievement_when_threshold_crossed():
    # SHOULD-1: a continuously-connected member crosses a voice threshold via
    # the 5-min flush loop (never leaving), so the flush itself must unlock the
    # achievement — otherwise !erfolge omits it until the member leaves/moves.
    from n3x_bot.activity import flush_voice_times
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    t0 = datetime(2026, 7, 13, 20, 0, tzinfo=ZoneInfo(settings.timezone))
    bot.voice_join_times[7] = t0
    flush_now = t0 + timedelta(seconds=3700)   # crosses the 3600s voice_3600 tier

    await flush_voice_times(bot, repo, flush_now)

    assert await repo.get_activity(7, "voice_seconds") == 3700
    assert await repo.has_achievement(7, "voice_3600") is True
    await repo.close()


async def test_message_rechecks_streak_night_only_when_they_change(monkeypatch):
    # SHOULD-2: streak/night change at most once per day, so their achievement
    # check must fire only on the message that actually moved the value — a
    # same-day repeat message re-runs neither (behaviour-preserving optimisation).
    act = _act()
    repo = await _flatfile_repo()
    settings = _settings()

    checked: list[str] = []

    async def _spy(_repo, _member_id, metric, defs=None):
        checked.append(metric)
        return []

    monkeypatch.setattr(act, "check_achievements", _spy)

    # night-window instant so both streak and night can move on the first message
    now = datetime(2026, 7, 13, 2, 0, tzinfo=ZoneInfo(settings.timezone))
    await act.record_message_activity(_defs_bot(), repo, settings, 7, now)
    # first message: streak starts (change) + night counted (change) + messages
    assert checked == ["messages", "streak", "night"]

    checked.clear()
    later = now + timedelta(minutes=1)  # same day, same night window
    await act.record_message_activity(_defs_bot(), repo, settings, 7, later)
    # second same-day message: only the message counter moved
    assert checked == ["messages"]
    await repo.close()


async def test_flush_does_not_double_count_or_phantom_on_concurrent_leave():
    # Reproduces MUST-1: without the lock, a leave landing during the flush's
    # add_activity await double-counted the interval AND resurrected the popped
    # key as a phantom session. With the lock the leave serialises after flush.
    from n3x_bot.activity import flush_voice_times
    from n3x_bot.bot import handle_voice_state_update
    repo = await _flatfile_repo()          # real bot (for voice_lock)
    settings = _settings()
    bot = build_bot(settings, repo)
    fake = _AwaitingRepo()

    t0 = datetime(2026, 7, 13, 20, 0, tzinfo=ZoneInfo(settings.timezone))
    bot.voice_join_times[7] = t0
    flush_now = t0 + timedelta(seconds=300)
    leave_now = t0 + timedelta(seconds=301)
    member = SimpleNamespace(id=7, bot=False)
    chan = SimpleNamespace(id=100)

    task = asyncio.create_task(flush_voice_times(bot, fake, flush_now))
    await asyncio.sleep(0)  # let flush acquire the lock and enter its await
    # leave arrives mid-flush; it must block on voice_lock, not interleave
    await handle_voice_state_update(bot, fake, settings, member,
                                    _vs(chan), _vs(None), leave_now)
    await task

    total = fake.credited.get((7, "voice_seconds"), 0)
    assert total == 301          # 300 (flush) + 1 (leave), NOT ~601 (double count)
    assert 7 not in bot.voice_join_times   # no phantom session left behind
    await repo.close()


# ── on_ready voice seeding is idempotent (setdefault) ──────────────────────

def _voice_member(mid):
    guild_me = SimpleNamespace(
        guild_permissions=SimpleNamespace(manage_nicknames=False))
    return SimpleNamespace(id=mid, bot=False, display_name=f"M{mid}",
                           roles=[], top_role=0, guild=None, _me=guild_me)


def _fake_guild_with_voice(members):
    guild_me = SimpleNamespace(
        guild_permissions=SimpleNamespace(manage_nicknames=False))
    guild = SimpleNamespace(
        me=guild_me, owner=object(),
        voice_channels=[SimpleNamespace(members=members)],
        members=members)

    def fetch_members(limit=None):
        raise RuntimeError("no gateway")  # force fallback to guild.members

    guild.fetch_members = fetch_members
    for m in members:
        m.guild = guild
    return guild


async def test_on_ready_voice_seeding_is_idempotent(monkeypatch):
    # A reconnect re-runs on_ready; seeding must NOT overwrite an existing join
    # time (that would drop un-flushed voice seconds) but must seed new members.
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=0)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)
    bot.tree.sync = AsyncMock()

    guild = _fake_guild_with_voice([_voice_member(7), _voice_member(8)])
    monkeypatch.setattr(type(bot), "guilds", property(lambda self: [guild]))

    sentinel = datetime(2000, 1, 1, tzinfo=ZoneInfo(settings.timezone))
    bot.voice_join_times[7] = sentinel   # a member already tracked (un-flushed)

    await bot.on_ready()

    assert bot.voice_join_times[7] == sentinel  # existing join preserved
    assert 8 in bot.voice_join_times            # new member seeded
    await repo.close()


# ── view command: /activity ───────────────────────────────────────────────

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

    cmd = bot.tree.get_command("activity")
    interaction = MagicMock()
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.user = SimpleNamespace(id=7, display_name="Erkan")

    await cmd.callback(interaction)  # no member -> defaults to the caller

    interaction.response.send_message.assert_awaited_once()
    blob = _send_text(interaction.response.send_message)
    for token in ("17", "23", "13", "29", "8"):
        assert token in blob, f"expected {token!r} in activity summary: {blob!r}"

    await repo.close()
