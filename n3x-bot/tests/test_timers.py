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
from discord import app_commands

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


OVERVIEW_CHANNEL_ID = 222
OVERVIEW_MESSAGE_ID = 333


def _overview_channel():
    """Fake overview channel: get_channel -> channel -> fetch_message -> msg,
    with msg.edit an AsyncMock so we can assert the self-edit happened.
    `send` returns the same msg, so a (re)post is observable too."""
    msg = SimpleNamespace(id=OVERVIEW_MESSAGE_ID, edit=AsyncMock(),
                          add_reaction=AsyncMock())
    channel = MagicMock()
    channel.id = OVERVIEW_CHANNEL_ID
    channel.fetch_message = AsyncMock(return_value=msg)
    channel.send = AsyncMock(return_value=msg)
    channel._msg = msg
    return channel


async def _track_overview(repo, message_id=OVERVIEW_MESSAGE_ID,
                          channel_id=OVERVIEW_CHANNEL_ID):
    """The overview message is DB-tracked (channel_messages), not configured."""
    from n3x_bot.timers import TIMER_OVERVIEW_KEY
    await repo.set_channel_message(TIMER_OVERVIEW_KEY, message_id, channel_id)


def _fake_interaction(user, guild=None):
    """A slash-interaction fake mirroring the /admin & /config slash tests."""
    it = MagicMock()
    it.user = user
    it.guild = guild
    it.response = MagicMock()
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.followup = MagicMock()
    it.followup.send = AsyncMock()
    it.delete_original_response = AsyncMock()
    return it


def _map_autocomplete(bot, command_name):
    """The autocomplete callback bound to the ``map`` param of a tree command.

    discord.py stores it on the command's parameter; its signature is
    ``(interaction, current: str)`` and it returns a list of
    ``app_commands.Choice``. Calling it directly is the unit under test for the
    live map suggestions (no re-sync needed when `/config allowed-maps` changes).
    """
    cmd = bot.tree.get_command(command_name)
    return cmd._params["map"].autocomplete


def _sent_text(interaction) -> str:
    """All first-positional strings the callback sent, via either
    ``response.send_message`` or ``followup.send`` (base/basestop reply either
    way depending on whether they defer)."""
    parts = []
    for mock in (interaction.response.send_message, interaction.followup.send):
        for call in mock.await_args_list:
            if call.args:
                parts.append(str(call.args[0]))
    return " ".join(parts)


# ── build_timer_overview_embed (pure) ──────────────────────────────────────

async def test_overview_embed_has_the_english_title():
    from n3x_bot import timers
    embed = timers.build_timer_overview_embed({}, _now())
    assert embed.title == "🛰️ BASE TIMER OVERVIEW"


async def test_overview_embed_empty_shows_no_timers_line_in_red():
    from n3x_bot import timers
    embed = timers.build_timer_overview_embed({}, _now())
    assert embed.description == "No active base timers."
    assert embed.color == discord.Color.red()


async def test_overview_embed_populated_is_blue():
    from n3x_bot import timers
    now = _now()
    embed = timers.build_timer_overview_embed(
        {"4-1": now + timedelta(minutes=30)}, now)
    assert embed.color == discord.Color.blue()


async def test_overview_embed_line_renders_countdown_with_seconds():
    # The countdown is rendered bot-side to the SECOND. Discord's <t:UNIX:R>
    # stamp collapses to one unit ("in 24 minutes") and can never show seconds,
    # so it is gone -- pin its absence alongside the new MM:SS text.
    from n3x_bot import timers
    now = _now()
    end = now + timedelta(minutes=24, seconds=30)
    embed = timers.build_timer_overview_embed({"2-6": end}, now)
    assert "24:30 remaining" in embed.description
    assert ":R>" not in embed.description


async def test_overview_embed_countdown_uses_h_mm_ss_past_an_hour():
    from n3x_bot import timers
    now = _now()
    end = now + timedelta(hours=2, minutes=5, seconds=9)
    embed = timers.build_timer_overview_embed({"2-6": end}, now)
    assert "2:05:09 remaining" in embed.description


async def test_overview_embed_countdown_rounds_up_so_first_paint_is_exact():
    # A 30-minute timer painted a hair after it started must read 30:00, not
    # 29:59 -- the countdown ceils rather than truncates.
    from n3x_bot import timers
    now = _now()
    end = now + timedelta(minutes=30) - timedelta(milliseconds=1)
    embed = timers.build_timer_overview_embed({"2-6": end}, now)
    assert "30:00 remaining" in embed.description


async def test_overview_embed_countdown_correct_across_timezones():
    # B6 aware-correctness, now expressed through the countdown: 2026-07-14
    # 12:24 Europe/Berlin (CEST, +02:00) IS 10:24 UTC, so an end_time given in
    # UTC must yield the true 24-minute delta -- not a +2h-wrong one from
    # comparing wall-clock numbers.
    from n3x_bot import timers
    now = datetime(2026, 7, 14, 12, 0, 0, tzinfo=TZ)
    end = datetime(2026, 7, 14, 10, 24, 0, tzinfo=ZoneInfo("UTC"))
    embed = timers.build_timer_overview_embed({"2-6": end}, now)
    assert "24:00 remaining" in embed.description


async def test_overview_embed_line_keeps_map_marker_and_name():
    from n3x_bot import timers
    now = _now()
    embed = timers.build_timer_overview_embed(
        {"2-6": now + timedelta(minutes=30)}, now)
    assert "📍 **2-6**" in embed.description


async def test_overview_embed_line_drops_static_minute_remainder():
    # The old "— {n} Min" German static text is GONE (an English MM:SS
    # countdown replaces it); pin its absence so it can't creep back.
    from n3x_bot import timers
    now = _now()
    embed = timers.build_timer_overview_embed(
        {"2-6": now + timedelta(minutes=30)}, now)
    assert " Min" not in embed.description


async def test_overview_embed_lines_are_sorted_by_end_time_ascending():
    from n3x_bot import timers
    now = _now()
    early = now + timedelta(minutes=30)
    late = now + timedelta(minutes=90)
    timers_map = {"1-5": late, "4-1": early}
    embed = timers.build_timer_overview_embed(timers_map, now)
    lines = embed.description.split("\n")
    assert len(lines) == 2
    # first line = earliest end_time, second = latest (sorted ascending)
    assert "**4-1**" in lines[0]
    assert "30:00 remaining" in lines[0]
    assert "**1-5**" in lines[1]
    assert "1:30:00 remaining" in lines[1]
    assert early < late


async def test_overview_embed_renders_past_timer_line_without_dropping_it():
    from n3x_bot import timers
    now = _now()
    # PIN: the builder renders every timer handed to it (a past end_time reads
    # "expired"); the CALLER (update_timer_overview) is what drops expired rows,
    # not the builder.
    end = now - timedelta(minutes=5)
    embed = timers.build_timer_overview_embed({"3-7": end}, now)
    assert "📍 **3-7**" in embed.description
    assert "expired" in embed.description


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


# ── has_base_timer_role honors MULTIPLE configured roles (ANY-match) ──────────
# The check must iterate `base_timer_role_ids`. A settings/config fake exposing
# only the list accessor keeps BOTH the positive and negative pre-impl RED a
# clean AttributeError (the check still reading the single-int
# `base_timer_role_id`). The real RuntimeConfig `_ids` resolution is covered
# separately in test_runtime_config.py.

def _multi_timer_config(*role_ids):
    return SimpleNamespace(base_timer_role_ids=list(role_ids))


async def test_has_base_timer_role_true_when_member_holds_any_of_multiple():
    from n3x_bot import timers
    cfg = _multi_timer_config(111, 222)
    member = _member(role_ids=(333, 222))  # holds 222
    assert timers.has_base_timer_role(member, cfg) is True


async def test_has_base_timer_role_false_when_member_holds_none_of_multiple():
    from n3x_bot import timers
    cfg = _multi_timer_config(111, 222)
    member = _member(role_ids=(333, 444))
    assert timers.has_base_timer_role(member, cfg) is False


async def test_has_base_timer_role_false_when_no_roles_configured():
    from n3x_bot import timers
    cfg = _multi_timer_config()  # base_timer_role_ids == []
    member = _member(role_ids=(0,))
    assert timers.has_base_timer_role(member, cfg) is False


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
    # surfaced to the user by the /base callback's ephemeral error) and stores
    # nothing.
    with pytest.raises(ValueError):
        await timers.start_base_timer(repo, settings, "9-9", 30, _now())
    assert await repo.list_base_timers() == {}
    await repo.close()


# ── update_timer_overview (self-editing embed, best-effort) ─────────────────

async def test_update_timer_overview_edits_the_fixed_message():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    await _track_overview(repo)
    settings = _settings(timer_overview_channel_id=222)
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
    await _track_overview(repo)
    settings = _settings(timer_overview_channel_id=222)
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
    await _track_overview(repo)
    settings = _settings(timer_overview_channel_id=222)
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=None)

    # must not raise
    await timers.update_timer_overview(bot, repo, settings, _now())
    await repo.close()


async def test_update_timer_overview_swallows_fetch_failure():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    await _track_overview(repo)
    settings = _settings(timer_overview_channel_id=222)
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
    await _track_overview(repo)
    settings = _settings(timer_overview_channel_id=222)
    channel = _overview_channel()
    channel._msg.edit = AsyncMock(side_effect=RuntimeError("forbidden"))
    channel.fetch_message = AsyncMock(return_value=channel._msg)
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)

    await timers.update_timer_overview(bot, repo, settings, _now())  # no raise
    await repo.close()


# ── format_countdown (pure) ────────────────────────────────────────────────

async def test_format_countdown_renders_mm_ss_under_an_hour():
    from n3x_bot import timers
    assert timers.format_countdown(timedelta(minutes=24, seconds=30)) == "24:30"
    assert timers.format_countdown(timedelta(seconds=9)) == "00:09"
    assert timers.format_countdown(timedelta(minutes=1)) == "01:00"


async def test_format_countdown_renders_h_mm_ss_at_or_past_an_hour():
    from n3x_bot import timers
    assert timers.format_countdown(timedelta(hours=1)) == "1:00:00"
    assert timers.format_countdown(
        timedelta(hours=2, minutes=5, seconds=9)) == "2:05:09"


async def test_format_countdown_rounds_up_sub_second_remainders():
    from n3x_bot import timers
    # ceil, not truncate: 29:59.001 remaining still reads 30:00
    assert timers.format_countdown(
        timedelta(minutes=30) - timedelta(milliseconds=1)) == "30:00"


async def test_format_countdown_reports_expired_at_or_below_zero():
    from n3x_bot import timers
    assert timers.format_countdown(timedelta(0)) == "expired"
    assert timers.format_countdown(timedelta(minutes=-5)) == "expired"


# ── update_timer_overview: rate-limit containment for the 1s loop ───────────

async def test_update_timer_overview_skips_edit_when_nothing_changed():
    # The 1s loop would otherwise burn an API call every second on an idle
    # overview. Two passes at the same `now` must produce exactly one edit.
    from n3x_bot import timers
    repo = await _flatfile_repo()
    await _track_overview(repo)
    settings = _settings(timer_overview_channel_id=222)
    now = _now()
    channel = _overview_channel()
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)

    await timers.update_timer_overview(bot, repo, settings, now)
    await timers.update_timer_overview(bot, repo, settings, now)

    assert channel._msg.edit.await_count == 1
    await repo.close()


async def test_update_timer_overview_repaints_when_the_countdown_ticks():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    await _track_overview(repo)
    settings = _settings(timer_overview_channel_id=222)
    now = _now()
    await repo.set_base_timer("4-1", now + timedelta(minutes=30))
    channel = _overview_channel()
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)

    await timers.update_timer_overview(bot, repo, settings, now)
    # one second later the rendered seconds differ -> a real repaint is due
    await timers.update_timer_overview(bot, repo, settings,
                                      now + timedelta(seconds=1))

    assert channel._msg.edit.await_count == 2
    descriptions = [c.kwargs["embed"].description
                    for c in channel._msg.edit.call_args_list]
    assert "30:00 remaining" in descriptions[0]
    assert "29:59 remaining" in descriptions[1]
    await repo.close()


async def test_update_timer_overview_seeds_reload_reaction_only_once():
    # add_reaction is idempotent server-side but still costs a request on the
    # rate limiter; at 1 tick/s it must not fire on every pass.
    from n3x_bot import timers
    repo = await _flatfile_repo()
    await _track_overview(repo)
    settings = _settings(timer_overview_channel_id=222)
    now = _now()
    await repo.set_base_timer("4-1", now + timedelta(minutes=30))
    channel = _overview_channel()
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)

    await timers.update_timer_overview(bot, repo, settings, now)
    await timers.update_timer_overview(bot, repo, settings,
                                       now + timedelta(seconds=1))
    await timers.update_timer_overview(bot, repo, settings,
                                       now + timedelta(seconds=2))

    assert channel._msg.edit.await_count == 3      # countdown repainted
    assert channel._msg.add_reaction.await_count == 1  # control seeded once
    await repo.close()


async def test_update_timer_overview_skips_write_transaction_when_none_expired():
    # At 1 tick/s an unconditional purge would open a write transaction every
    # second for nothing; purge only when something actually expired.
    from n3x_bot import timers
    repo = await _flatfile_repo()
    await _track_overview(repo)
    settings = _settings(timer_overview_channel_id=222)
    now = _now()
    await repo.set_base_timer("4-1", now + timedelta(minutes=30))
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=_overview_channel())

    repo.purge_expired_base_timers = AsyncMock(
        wraps=repo.purge_expired_base_timers)
    await timers.update_timer_overview(bot, repo, settings, now)
    repo.purge_expired_base_timers.assert_not_awaited()

    # ...but an expired row still gets purged
    await repo.set_base_timer("1-5", now - timedelta(minutes=1))
    await timers.update_timer_overview(bot, repo, settings, now)
    repo.purge_expired_base_timers.assert_awaited_once()
    assert set(await repo.list_base_timers()) == {"4-1"}
    await repo.close()


# ── overview message: DB-tracked, self-healing ──────────────────────────────

def _not_found():
    import discord
    return discord.NotFound(MagicMock(status=404, reason="Not Found"),
                            "Unknown Message")


def _overview_bot(channel):
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)
    return bot


async def test_overview_is_posted_and_tracked_when_none_is_tracked():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings(timer_overview_channel_id=OVERVIEW_CHANNEL_ID)
    channel = _overview_channel()

    await timers.update_timer_overview(_overview_bot(channel), repo, settings,
                                       _now())

    channel.send.assert_awaited_once()
    channel.fetch_message.assert_not_awaited()
    assert await repo.get_channel_message(timers.TIMER_OVERVIEW_KEY) == (
        OVERVIEW_MESSAGE_ID, OVERVIEW_CHANNEL_ID)
    await repo.close()


async def test_deleted_overview_is_reposted_and_the_new_id_tracked():
    # The production failure: the channel was purged, the tracked message is
    # gone. The next pass must post a replacement, not edit into the void.
    from n3x_bot import timers
    repo = await _flatfile_repo()
    await _track_overview(repo, message_id=111)
    settings = _settings(timer_overview_channel_id=OVERVIEW_CHANNEL_ID)
    channel = _overview_channel()
    channel.fetch_message = AsyncMock(side_effect=_not_found())

    await timers.update_timer_overview(_overview_bot(channel), repo, settings,
                                       _now())

    channel.send.assert_awaited_once()
    assert (await repo.get_channel_message(timers.TIMER_OVERVIEW_KEY))[0] == \
        OVERVIEW_MESSAGE_ID
    await repo.close()


async def test_non_notfound_error_never_reposts():
    # Permissions / 5xx / rate limit are not proof the message is gone. At one
    # pass per second, reposting on them would spam a message every second.
    from n3x_bot import timers
    repo = await _flatfile_repo()
    await _track_overview(repo)
    settings = _settings(timer_overview_channel_id=OVERVIEW_CHANNEL_ID)
    channel = _overview_channel()
    channel.fetch_message = AsyncMock(side_effect=RuntimeError("503"))

    await timers.update_timer_overview(_overview_bot(channel), repo, settings,
                                       _now())

    channel.send.assert_not_awaited()
    assert (await repo.get_channel_message(timers.TIMER_OVERVIEW_KEY))[0] == \
        OVERVIEW_MESSAGE_ID
    await repo.close()


async def test_edit_hitting_notfound_also_reposts():
    # Deleted between fetch and edit.
    from n3x_bot import timers
    repo = await _flatfile_repo()
    await _track_overview(repo, message_id=111)
    settings = _settings(timer_overview_channel_id=OVERVIEW_CHANNEL_ID)
    channel = _overview_channel()
    stale = SimpleNamespace(id=111, edit=AsyncMock(side_effect=_not_found()),
                            add_reaction=AsyncMock())
    channel.fetch_message = AsyncMock(return_value=stale)

    await timers.update_timer_overview(_overview_bot(channel), repo, settings,
                                       _now())

    channel.send.assert_awaited_once()
    await repo.close()


async def test_idle_overview_is_not_fetched_again_within_verify_interval():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    await _track_overview(repo)
    settings = _settings(timer_overview_channel_id=OVERVIEW_CHANNEL_ID)
    channel = _overview_channel()
    bot = _overview_bot(channel)
    now = _now()

    await timers.update_timer_overview(bot, repo, settings, now)
    await timers.update_timer_overview(bot, repo, settings,
                                       now + timedelta(seconds=30))

    assert channel.fetch_message.await_count == 1   # second pass: no API call
    await repo.close()


async def test_idle_overview_deletion_is_detected_after_verify_interval():
    # Idle text never changes, so without the periodic check a purge while no
    # timer runs would go unnoticed until the next /base.
    from n3x_bot import timers
    repo = await _flatfile_repo()
    await _track_overview(repo)
    settings = _settings(timer_overview_channel_id=OVERVIEW_CHANNEL_ID)
    channel = _overview_channel()
    bot = _overview_bot(channel)
    now = _now()

    await timers.update_timer_overview(bot, repo, settings, now)
    channel.send.assert_not_awaited()
    channel.fetch_message = AsyncMock(side_effect=_not_found())   # purged
    await timers.update_timer_overview(
        bot, repo, settings, now + timers.VERIFY_INTERVAL + timedelta(seconds=1))

    channel.send.assert_awaited_once()
    await repo.close()


async def test_idle_verification_does_not_edit_unchanged_text():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    await _track_overview(repo)
    settings = _settings(timer_overview_channel_id=OVERVIEW_CHANNEL_ID)
    channel = _overview_channel()
    bot = _overview_bot(channel)
    now = _now()

    await timers.update_timer_overview(bot, repo, settings, now)
    await timers.update_timer_overview(
        bot, repo, settings, now + timers.VERIFY_INTERVAL + timedelta(seconds=1))

    assert channel.fetch_message.await_count == 2   # verified twice
    assert channel._msg.edit.await_count == 1       # but painted only once
    await repo.close()


async def test_message_tracked_in_another_channel_is_replaced():
    # The channel was reconfigured in .env: post into the configured one.
    from n3x_bot import timers
    repo = await _flatfile_repo()
    await _track_overview(repo, message_id=999, channel_id=12345)
    settings = _settings(timer_overview_channel_id=OVERVIEW_CHANNEL_ID)
    channel = _overview_channel()

    await timers.update_timer_overview(_overview_bot(channel), repo, settings,
                                       _now())

    channel.fetch_message.assert_not_awaited()
    channel.send.assert_awaited_once()
    assert await repo.get_channel_message(timers.TIMER_OVERVIEW_KEY) == (
        OVERVIEW_MESSAGE_ID, OVERVIEW_CHANNEL_ID)
    await repo.close()


async def test_concurrent_updates_post_only_one_overview():
    # The 1s loop and a /base can run update_timer_overview at the same time;
    # both would see "nothing tracked" and each post one. The lock prevents it.
    import asyncio
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings(timer_overview_channel_id=OVERVIEW_CHANNEL_ID)
    channel = _overview_channel()
    gate = asyncio.Event()

    async def _slow_send(*args, **kwargs):
        await gate.wait()
        return channel._msg
    channel.send = AsyncMock(side_effect=_slow_send)
    bot = _overview_bot(channel)
    now = _now()

    first = asyncio.ensure_future(
        timers.update_timer_overview(bot, repo, settings, now))
    second = asyncio.ensure_future(
        timers.update_timer_overview(bot, repo, settings, now))
    await asyncio.wait({first, second}, timeout=0.2)   # both parked
    gate.set()
    await asyncio.gather(first, second)

    assert channel.send.await_count == 1
    await repo.close()


async def test_timer_loop_survives_a_failing_tick():
    # The production outage: a Postgres restart made one tick raise
    # InterfaceError and tasks.loop stopped for good. The body must swallow it.
    from n3x_bot import timers
    repo = await _flatfile_repo()
    repo.list_base_timers = AsyncMock(side_effect=RuntimeError("connection is closed"))
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=None)
    loop = timers.start_timer_overview_loop(bot, repo, _settings())
    try:
        await loop.coro()           # must not raise
        assert loop.is_running() is True
    finally:
        loop.cancel()
    await repo.close()


async def test_sql_engine_pings_pooled_connections():
    # Root cause of the outage: without pre-ping a Postgres restart leaves dead
    # connections in the pool and the next checkout raises InterfaceError.
    import tempfile
    from n3x_bot.storage.sql_repo import SqlRepository
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    repo = SqlRepository(f"sqlite+aiosqlite:///{path}")
    await repo.connect()
    try:
        assert repo.engine.sync_engine.pool._pre_ping is True
    finally:
        await repo.close()
        os.remove(path)


# ── register_timer_commands (Phase 5: slash-ONLY /base + /basestop) ──────────
#
# Phase 5 migrates `!base` / `!basestop` to slash-ONLY app commands on
# `bot.tree`. Both are role-gated by `has_base_timer_role(interaction.user, ...)`
# and take a `map` param with a LIVE autocomplete (sourced from
# `bot.runtime_config.allowed_maps_list` for /base, and active timers for
# /basestop, so `/config allowed-maps` changes reflect without a re-sync). The
# `map` value is validated in-callback (autocomplete is non-binding). Commands
# are addressed via the tree and invoked with the `map`/`minutes` params by name:
#     bot.tree.get_command("base").callback(interaction, map="4-1", minutes=30)

async def test_register_timer_commands_registers_base_and_basestop_as_slash_only():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    timers.register_timer_commands(bot, repo, settings)

    assert bot.get_command("base") is None          # dropped from prefix registry
    assert bot.get_command("basestop") is None
    assert bot.tree.get_command("base") is not None  # present on the app tree
    assert bot.tree.get_command("basestop") is not None
    await repo.close()


async def test_register_timer_commands_is_idempotent():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    timers.register_timer_commands(bot, repo, settings)
    timers.register_timer_commands(bot, repo, settings)  # must not raise/dup

    assert bot.tree.get_command("base") is not None
    assert bot.tree.get_command("basestop") is not None
    await repo.close()


# ── /base autocomplete (live map suggestions) ───────────────────────────────

async def test_base_map_autocomplete_returns_choices_from_allowed_maps():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings(base_timer_role_id=555)
    bot = build_bot(settings, repo)
    timers.register_timer_commands(bot, repo, settings)

    ac = _map_autocomplete(bot, "base")
    interaction = _fake_interaction(_member(role_ids=(555,)))

    choices = await ac(interaction, "")

    assert choices  # non-empty
    assert all(isinstance(c, app_commands.Choice) for c in choices)
    values = {c.value for c in choices}
    allowed = set(bot.runtime_config.allowed_maps_list)
    assert values <= allowed          # sourced live from allowed_maps_list
    assert "4-1" in values            # a default allowed map is suggested
    await repo.close()


async def test_base_map_autocomplete_filters_by_current_input():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings(base_timer_role_id=555)
    bot = build_bot(settings, repo)
    timers.register_timer_commands(bot, repo, settings)

    ac = _map_autocomplete(bot, "base")
    interaction = _fake_interaction(_member(role_ids=(555,)))

    choices = await ac(interaction, "4-")

    values = {c.value for c in choices}
    assert values  # the "4-" maps match
    assert all("4-" in c.value for c in choices)  # filtered by the partial input
    assert "1-5" not in values                     # non-matching maps excluded
    await repo.close()


# ── /base callback (role gate + in-callback map validation) ─────────────────

async def test_base_slash_stores_timer_and_refreshes_overview_for_role_holder():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    await _track_overview(repo)
    settings = _settings(base_timer_role_id=555,
                         timer_overview_channel_id=222)
    bot = build_bot(settings, repo)
    channel = _overview_channel()
    bot.get_channel = MagicMock(return_value=channel)
    timers.register_timer_commands(bot, repo, settings)

    interaction = _fake_interaction(_member(role_ids=(555,)))

    await bot.tree.get_command("base").callback(interaction, map="4-1", minutes=30)

    stored = await repo.list_base_timers()
    assert "4-1" in stored
    assert stored["4-1"].tzinfo is not None  # B6: tz-aware
    channel._msg.edit.assert_awaited()       # overview refreshed
    # No success message (overview is the feedback); the deferred ack is removed.
    interaction.delete_original_response.assert_awaited()
    assert not _sent_text(interaction)
    await repo.close()


async def test_base_slash_refused_for_non_role_holder_does_no_work():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    await _track_overview(repo)
    settings = _settings(base_timer_role_id=555,
                         timer_overview_channel_id=222)
    bot = build_bot(settings, repo)
    channel = _overview_channel()
    bot.get_channel = MagicMock(return_value=channel)
    timers.register_timer_commands(bot, repo, settings)

    interaction = _fake_interaction(_member(role_ids=(111,)))  # lacks the role

    await bot.tree.get_command("base").callback(interaction, map="4-1", minutes=30)

    assert await repo.list_base_timers() == {}   # nothing stored
    channel._msg.edit.assert_not_awaited()        # overview not touched
    assert "No permission" in _sent_text(interaction)  # ephemeral refusal
    await repo.close()


async def test_base_slash_rejects_invalid_map_and_names_allowed_maps():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    await _track_overview(repo)
    settings = _settings(base_timer_role_id=555,
                         timer_overview_channel_id=222)
    bot = build_bot(settings, repo)
    timers.register_timer_commands(bot, repo, settings)

    interaction = _fake_interaction(_member(role_ids=(555,)))

    # autocomplete is non-binding, so a bogus map can still arrive; the callback
    # must validate it in-body, store nothing, and list the allowed maps.
    await bot.tree.get_command("base").callback(interaction, map="9-9", minutes=30)

    assert await repo.list_base_timers() == {}
    assert "4-1" in _sent_text(interaction)  # allowed-map list surfaced
    await repo.close()


# ── /basestop autocomplete + callback ───────────────────────────────────────

async def test_basestop_map_autocomplete_lists_only_active_timers():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings(base_timer_role_id=555)
    bot = build_bot(settings, repo)
    timers.register_timer_commands(bot, repo, settings)
    await repo.set_base_timer("4-1", _now() + timedelta(minutes=30))

    ac = _map_autocomplete(bot, "basestop")
    interaction = _fake_interaction(_member(role_ids=(555,)))

    choices = await ac(interaction, "")

    values = {c.value for c in choices}
    assert "4-1" in values       # a map with a running timer is suggested
    assert "4-2" not in values   # maps without an active timer are not
    await repo.close()


async def test_basestop_slash_removes_timer_and_refreshes_overview():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    await _track_overview(repo)
    settings = _settings(base_timer_role_id=555,
                         timer_overview_channel_id=222)
    bot = build_bot(settings, repo)
    channel = _overview_channel()
    bot.get_channel = MagicMock(return_value=channel)
    timers.register_timer_commands(bot, repo, settings)
    await repo.set_base_timer("4-1", _now() + timedelta(minutes=30))

    interaction = _fake_interaction(_member(role_ids=(555,)))

    await bot.tree.get_command("basestop").callback(interaction, map="4-1")

    assert await repo.list_base_timers() == {}
    channel._msg.edit.assert_awaited()
    interaction.delete_original_response.assert_awaited()
    assert not _sent_text(interaction)
    await repo.close()


async def test_basestop_slash_on_unknown_map_reports_no_active_timer():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    await _track_overview(repo)
    settings = _settings(base_timer_role_id=555,
                         timer_overview_channel_id=222)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=_overview_channel())
    timers.register_timer_commands(bot, repo, settings)

    interaction = _fake_interaction(_member(role_ids=(555,)))

    await bot.tree.get_command("basestop").callback(interaction, map="4-1")  # not running

    assert "No active timer" in _sent_text(interaction)
    await repo.close()


async def test_basestop_slash_refused_for_non_role_holder():
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings(base_timer_role_id=555)
    bot = build_bot(settings, repo)
    timers.register_timer_commands(bot, repo, settings)
    await repo.set_base_timer("4-1", _now() + timedelta(minutes=30))

    interaction = _fake_interaction(_member(role_ids=(111,)))

    await bot.tree.get_command("basestop").callback(interaction, map="4-1")

    assert set(await repo.list_base_timers()) == {"4-1"}  # untouched
    assert "No permission" in _sent_text(interaction)
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


async def test_timer_overview_loop_ticks_once_per_second():
    # The countdown shows seconds, so the repaint cadence must be 1s (was 30s).
    from n3x_bot import timers
    repo = await _flatfile_repo()
    settings = _settings(timer_overview_channel_id=0)
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=None)

    loop = timers.start_timer_overview_loop(bot, repo, settings)
    try:
        assert loop.seconds == 1
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

async def test_build_bot_registers_base_and_basestop_as_slash_only():
    repo = await _flatfile_repo()
    settings = _settings(base_timer_role_id=555)

    bot = build_bot(settings, repo)

    assert bot.get_command("base") is None          # slash-only, not prefix
    assert bot.get_command("basestop") is None
    assert bot.tree.get_command("base") is not None
    assert bot.tree.get_command("basestop") is not None
    await repo.close()
