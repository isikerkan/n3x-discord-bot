"""Failing (RED) tests for the `!gate verlauf <gate> [von] [bis]` feature.

Three new surfaces are pinned here:

1. Date parsing helper ``n3x_bot.gates.parse_de_date(s) -> datetime.date | None``
   accepting German ``TT.MM.JJJJ`` and ISO ``JJJJ-MM-TT``; junk -> None.

2. Chart render ``n3x_bot.charts.render_gate_history_chart(
       gate_type, entries, now, von=None, bis=None) -> bytes`` — a PURE
   matplotlib (Agg backend) render to PNG bytes. ``entries`` is the list of
   dicts from ``repo.list_gate_entries`` ({"cost", "created_at", "drops"}).
   Tests use the REAL matplotlib + PIL to prove the bytes are a valid PNG.

3. Command ``!gate`` group + ``verlauf`` subcommand, wired by ``build_bot``.

Every new symbol is imported LAZILY inside the test body so the module still
collects even before ``n3x_bot.charts`` / ``matplotlib`` / the command exist:
the failures are missing-behaviour, not test-file import errors.

Pinned decisions (see report):
- Empty data (no runs / none in range) -> still render a valid "keine Daten"
  PNG chart and POST it as a discord.File (not a plain-text reply).
- ``verlauf`` is NOT admin-gated (viewing history is open).
- The date filter is asserted by wrapping ``repo.list_gate_entries`` and
  checking it was called with tz-aware ``since`` / ``until`` bracketing the
  parsed range (since at 00:00, until at 23:59:59 in settings.timezone).
- Chart tests assert structural PNG validity only, never pixel content.
- matplotlib is NOT yet installed in this env, so the chart tests RED on
  ModuleNotFoundError inside the body — an accepted RED (missing dep +
  missing module).
"""

import os
import tempfile
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import discord
import pytest
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


def _ctx():
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.channel = MagicMock()
    ctx.channel.send = AsyncMock()
    ctx.author = SimpleNamespace(id=1, display_name="Erkan")
    return ctx


def _posted_file(ctx):
    """Return the discord.File posted via ctx.send / ctx.channel.send, else None."""
    for send in (ctx.send, ctx.channel.send):
        for call in send.await_args_list:
            f = call.kwargs.get("file")
            if isinstance(f, discord.File):
                return f
    return None


def _sample_entries(gate_type: str):
    """Two entries shaped exactly like repo.list_gate_entries output."""
    tz = ZoneInfo("Europe/Berlin")
    drop_for = {
        "a": [{}, {}],
        "d": [{"laser": True}, {"laser": False}],
        "e": [{"lf4": True}, {"lf4": False}],
        "z": [{"havoc": True}, {"havoc": False}],
        "k": [{"hercules": True, "lf4u": False},
              {"hercules": False, "lf4u": True}],
    }[gate_type]
    return [
        {"cost": 46000, "created_at": datetime(2026, 7, 1, 12, 0, tzinfo=tz),
         "drops": drop_for[0]},
        {"cost": 48000, "created_at": datetime(2026, 7, 3, 18, 0, tzinfo=tz),
         "drops": drop_for[1]},
    ]


# ── 1. parse_de_date ─────────────────────────────────────────────────────────

def test_parse_de_date_accepts_german_format():
    from n3x_bot.gates import parse_de_date
    assert parse_de_date("01.07.2026") == date(2026, 7, 1)


def test_parse_de_date_accepts_iso_format():
    from n3x_bot.gates import parse_de_date
    assert parse_de_date("2026-07-01") == date(2026, 7, 1)


def test_parse_de_date_returns_none_for_junk():
    from n3x_bot.gates import parse_de_date
    assert parse_de_date("bad") is None


def test_parse_de_date_returns_none_for_empty_string():
    from n3x_bot.gates import parse_de_date
    assert parse_de_date("") is None


# ── 2. render_gate_history_chart (REAL matplotlib Agg + PIL) ─────────────────

def _open_png(png_bytes):
    from io import BytesIO
    from PIL import Image
    return Image.open(BytesIO(png_bytes))


def test_render_chart_returns_non_empty_png_bytes_for_cost_gate():
    from n3x_bot.charts import render_gate_history_chart
    now = datetime(2026, 7, 15, tzinfo=ZoneInfo("Europe/Berlin"))
    png = render_gate_history_chart("a", _sample_entries("a"), now)
    assert isinstance(png, bytes)
    assert len(png) > 0
    assert _open_png(png).format == "PNG"


@pytest.mark.parametrize("gate_type", ["d", "e", "z", "k"])
def test_render_chart_handles_drop_gates(gate_type):
    from n3x_bot.charts import render_gate_history_chart
    now = datetime(2026, 7, 15, tzinfo=ZoneInfo("Europe/Berlin"))
    png = render_gate_history_chart(gate_type, _sample_entries(gate_type), now)
    assert _open_png(png).format == "PNG"


def test_render_chart_kappa_partial_drop_renders():
    from n3x_bot.charts import render_gate_history_chart
    tz = ZoneInfo("Europe/Berlin")
    entries = [{"cost": 500, "created_at": datetime(2026, 7, 2, tzinfo=tz),
                "drops": {"hercules": True, "lf4u": False}}]
    now = datetime(2026, 7, 15, tzinfo=tz)
    png = render_gate_history_chart("k", entries, now)
    assert _open_png(png).format == "PNG"


def test_render_chart_with_date_window_returns_valid_png():
    # von/bis clamp the x-axis to the requested window; structural check only
    # (can't read xlim back from PNG) — the render must still emit a valid PNG.
    from n3x_bot.charts import render_gate_history_chart
    tz = ZoneInfo("Europe/Berlin")
    now = datetime(2026, 7, 15, tzinfo=tz)
    png = render_gate_history_chart("a", _sample_entries("a"), now,
                                    von=date(2026, 7, 1), bis=date(2026, 7, 31))
    assert isinstance(png, bytes)
    assert _open_png(png).format == "PNG"


def test_render_chart_empty_data_with_date_window_returns_valid_png():
    # Empty "keine Daten" path must keep working even with a date window set.
    from n3x_bot.charts import render_gate_history_chart
    tz = ZoneInfo("Europe/Berlin")
    now = datetime(2026, 7, 15, tzinfo=tz)
    png = render_gate_history_chart("a", [], now,
                                    von=date(2026, 7, 1), bis=date(2026, 7, 31))
    assert isinstance(png, bytes)
    assert _open_png(png).format == "PNG"


def test_render_chart_empty_entries_still_returns_valid_png():
    from n3x_bot.charts import render_gate_history_chart
    now = datetime(2026, 7, 15, tzinfo=ZoneInfo("Europe/Berlin"))
    png = render_gate_history_chart("a", [], now)
    assert isinstance(png, bytes)
    assert _open_png(png).format == "PNG"


# ── 3. /gate verlauf slash command wiring + behaviour ────────────────────────
#
# Phase 2 migration: `!gate verlauf` becomes an app_commands.Group `gate` on
# `bot.tree` with a `verlauf` subcommand: `/gate verlauf gate:<choice> [von]
# [bis]`. `gate` is an app_commands.Choice over the 7 gate letters, so an
# invalid/uppercase gate can never reach the callback (the framework rejects it
# before dispatch) — hence the old prefix "invalid gate"/"uppercase resolves"
# tests are dropped. `von`/`bis` remain free-text German dates.
#
# The subcommand is reached via
#   bot.tree.get_command("gate").get_command("verlauf").callback(interaction,
#       gate="a", von=..., bis=...)
# and, for a `choices=` param, the callback receives the raw `str` value.

def _slash_interaction(user=None):
    """Interaction fake mirroring the Phase-1 slash tests, plus a followup
    message stub so the ❌-react-to-remove tracking has an id to key on."""
    it = MagicMock()
    it.user = user or SimpleNamespace(id=1, display_name="Erkan", roles=[])
    it.guild = None
    it.response = MagicMock()
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    followup_msg = MagicMock()
    followup_msg.id = 9001
    followup_msg.add_reaction = AsyncMock()
    it.followup = MagicMock()
    it.followup.send = AsyncMock(return_value=followup_msg)
    it._followup_msg = followup_msg
    return it


def _followup_file(interaction):
    """The discord.File posted via followup.send (the slow-render path), else None."""
    for call in interaction.followup.send.await_args_list:
        f = call.kwargs.get("file")
        if isinstance(f, discord.File):
            return f
    return None


def _all_sends(interaction):
    return list(interaction.response.send_message.await_args_list) + \
        list(interaction.followup.send.await_args_list)


def _verlauf_cmd(bot):
    assert bot.get_command("gate") is None, \
        "gate must no longer be a prefix command after Phase 2"
    group = bot.tree.get_command("gate")
    assert group is not None, "build_bot must wire a `gate` app-command group on the tree"
    assert isinstance(group, app_commands.Group)
    cmd = group.get_command("verlauf")
    assert cmd is not None, "`gate` group must expose a `verlauf` subcommand"
    return cmd


async def test_gate_group_and_verlauf_subcommand_are_registered_on_tree():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    _verlauf_cmd(bot)  # asserts existence + type
    await repo.close()


async def test_verlauf_defers_before_rendering_then_posts_via_followup():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    await repo.add_gate_entry("a", 46000, 1, "u1")
    interaction = _slash_interaction()

    order = []
    interaction.response.defer = AsyncMock(
        side_effect=lambda *a, **k: order.append("defer"))
    interaction.followup.send = AsyncMock(
        side_effect=lambda *a, **k: (order.append("followup"),
                                     interaction._followup_msg)[1])

    cmd = _verlauf_cmd(bot)
    with patch("n3x_bot.bot.render_gate_history_chart", return_value=b"PNG"):
        await cmd.callback(interaction, gate="a")

    interaction.response.defer.assert_awaited_once()  # slow work -> defer first
    assert order[:2] == ["defer", "followup"]  # defer strictly before the post

    await repo.close()


async def test_verlauf_valid_gate_posts_png_file_via_followup():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    await repo.add_gate_entry("a", 46000, 1, "u1")
    await repo.add_gate_entry("a", 48000, 2, "u2")
    interaction = _slash_interaction()

    cmd = _verlauf_cmd(bot)
    with patch("n3x_bot.bot.render_gate_history_chart", return_value=b"PNG"):
        await cmd.callback(interaction, gate="a")

    posted = _followup_file(interaction)
    assert posted is not None, "a valid gate must post a discord.File via followup"
    assert posted.filename.endswith(".png")

    await repo.close()


async def test_verlauf_records_followup_message_for_reaction_removal():
    # handle_verlauf_removal deletes a posted chart when its ORIGINAL invoker
    # reacts ❌; it looks the message up in bot._verlauf_msgs keyed by the
    # posted message id -> invoker id. The slash version must still populate it
    # with the followup message's id and interaction.user.id.
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    await repo.add_gate_entry("a", 46000, 1, "u1")
    invoker = SimpleNamespace(id=4242, display_name="Erkan", roles=[])
    interaction = _slash_interaction(user=invoker)

    cmd = _verlauf_cmd(bot)
    with patch("n3x_bot.bot.render_gate_history_chart", return_value=b"PNG"):
        await cmd.callback(interaction, gate="a")

    assert bot._verlauf_msgs.get(interaction._followup_msg.id) == 4242

    await repo.close()


async def test_verlauf_invalid_date_refuses_ephemeral_no_file():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    await repo.add_gate_entry("a", 46000, 1, "u1")
    interaction = _slash_interaction()

    cmd = _verlauf_cmd(bot)
    with patch("n3x_bot.bot.render_gate_history_chart", return_value=b"PNG"):
        await cmd.callback(interaction, gate="a", von="bad")

    assert _followup_file(interaction) is None  # no chart on a bad date
    error_calls = [c for c in _all_sends(interaction)
                   if c.args and "Datum" in str(c.args[0])]
    assert error_calls, "a bad date must produce a German 'Datum' error"
    assert error_calls[0].kwargs.get("ephemeral") is True

    await repo.close()


async def test_verlauf_date_range_filters_entries_by_parsed_window():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    await repo.add_gate_entry("a", 46000, 1, "u1")
    interaction = _slash_interaction()
    tz = ZoneInfo("Europe/Berlin")

    cmd = _verlauf_cmd(bot)
    with patch("n3x_bot.bot.render_gate_history_chart", return_value=b"PNG"), \
            patch.object(repo, "list_gate_entries",
                         wraps=repo.list_gate_entries) as spy:
        await cmd.callback(interaction, gate="a", von="01.07.2026",
                           bis="15.07.2026")

    spy.assert_awaited()
    call = spy.await_args
    since = call.kwargs.get("since",
                            call.args[1] if len(call.args) > 1 else None)
    until = call.kwargs.get("until",
                            call.args[2] if len(call.args) > 2 else None)
    assert since is not None and until is not None
    assert since.tzinfo is not None and until.tzinfo is not None
    since_local = since.astimezone(tz)
    until_local = until.astimezone(tz)
    # inclusive window: von at 00:00:00, bis at end-of-day
    assert since_local.date() == date(2026, 7, 1)
    assert (since_local.hour, since_local.minute, since_local.second) == (0, 0, 0)
    assert until_local.date() == date(2026, 7, 15)
    assert until_local.hour == 23 and until_local.minute == 59

    await repo.close()


async def test_verlauf_until_covers_whole_bis_day_to_last_microsecond():
    # The `bis` bound must include the ENTIRE day: an entry recorded at
    # 23:59:59.5 on `bis` is inside the inclusive window. list_gate_entries
    # filters with `created > until`, so `until` has to reach 23:59:59.999999
    # (not 23:59:59.000000, which would drop sub-second entries).
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    await repo.add_gate_entry("a", 46000, 1, "u1")
    interaction = _slash_interaction()
    tz = ZoneInfo("Europe/Berlin")

    cmd = _verlauf_cmd(bot)
    with patch("n3x_bot.bot.render_gate_history_chart", return_value=b"PNG"), \
            patch.object(repo, "list_gate_entries",
                         wraps=repo.list_gate_entries) as spy:
        await cmd.callback(interaction, gate="a", von="01.07.2026",
                           bis="15.07.2026")

    until = spy.await_args.kwargs.get(
        "until", spy.await_args.args[2] if len(spy.await_args.args) > 2 else None)
    assert until is not None
    until_local = until.astimezone(tz)
    assert until_local.date() == date(2026, 7, 15)
    assert (until_local.hour, until_local.minute, until_local.second,
            until_local.microsecond) == (23, 59, 59, 999999)
    # An entry at 23:59:59.5 on the bis day is therefore included, not dropped.
    entry_ts = datetime(2026, 7, 15, 23, 59, 59, 500000, tzinfo=tz)
    assert entry_ts <= until

    await repo.close()


async def test_verlauf_no_data_in_range_still_posts_empty_chart():
    # Pinned empty behaviour: render a "keine Daten" chart and POST it (a
    # discord.File via followup), rather than replying with plain text.
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    # entry exists, but the requested window is entirely before it
    await repo.add_gate_entry("a", 46000, 1, "u1")
    interaction = _slash_interaction()

    cmd = _verlauf_cmd(bot)
    with patch("n3x_bot.bot.render_gate_history_chart", return_value=b"PNG"):
        await cmd.callback(interaction, gate="a", von="01.01.2000",
                           bis="02.01.2000")

    assert _followup_file(interaction) is not None

    await repo.close()
