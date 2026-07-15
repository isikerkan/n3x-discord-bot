"""RED tests for two additions to the `!gate verlauf` gate-history chart
(branch ``feature/verlauf-caption-remove``):

1. A German command-usage caption baked into the rendered chart image
   (``n3x_bot.charts``). Pinned as a module constant ``_CHART_CAPTION`` so it is
   testable without reading pixels: the constant must contain the command
   ``!gate verlauf`` and a gate-letters hint, and the render must still emit a
   valid, non-empty PNG (proven with real matplotlib Agg + PIL) for both a
   populated chart and the empty "keine Daten" chart.

2. A ❌-reaction remove flow: after ``verlauf`` posts the chart it seeds a ❌
   reaction on the sent message and tracks it in an in-memory map
   ``bot._verlauf_msgs = {message_id: invoker_user_id}`` (invoker =
   ``ctx.author.id``). A new best-effort handler
   ``handle_verlauf_removal(bot, payload)`` deletes the tracked message when the
   ORIGINAL invoker reacts ❌ (and only then), untracking it. It is wired into
   ``on_raw_reaction_add`` alongside the existing side-channel handlers.

Every new symbol is imported LAZILY inside the test body that needs it so this
module always collects — the failures are missing-behaviour/missing-symbol, not
test-file import errors. Discord I/O is faked (AsyncMock/MagicMock); the repo is
a real, connected JsonRepository for anything DB-touching.

Pinned decisions (see report):
- Removal authority: ONLY the invoker who ran the command may remove the chart.
  A ❌ from any other user — including the bot's own seed reaction — is ignored.
- Tracking map shape: ``bot._verlauf_msgs[message.id] == ctx.author.id``.
- The sent message is whatever ``ctx.send(...)`` returns; ❌ is added to it.
- Handler signature ``handle_verlauf_removal(bot, payload)`` (no repo/settings —
  it only needs the bot's tracker + Discord channel access), best-effort.
- Caption content asserted on the CONSTANT only; the render is asserted to be a
  structurally valid PNG, never on pixel content.
"""

import os
import tempfile
from datetime import date, datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from n3x_bot.bot import build_bot
from n3x_bot.config import Settings
from n3x_bot.seed import seed_defaults
from n3x_bot.storage.json_repo import JsonRepository
from discord.ext import commands


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


def _open_png(png_bytes):
    from PIL import Image
    return Image.open(BytesIO(png_bytes))


def _sample_entries():
    tz = ZoneInfo("Europe/Berlin")
    return [
        {"cost": 46000, "created_at": datetime(2026, 7, 1, 12, 0, tzinfo=tz),
         "drops": {}},
        {"cost": 48000, "created_at": datetime(2026, 7, 3, 18, 0, tzinfo=tz),
         "drops": {}},
    ]


def _sent_message(message_id: int = 8001):
    """The message object ctx.send(...) returns for a posted chart."""
    msg = MagicMock()
    msg.id = message_id
    msg.add_reaction = AsyncMock()
    msg.delete = AsyncMock()
    return msg


def _ctx(sent_message, *, author_id: int = 1):
    ctx = MagicMock()
    ctx.send = AsyncMock(return_value=sent_message)
    ctx.author = SimpleNamespace(id=author_id, display_name="Erkan")
    return ctx


def _payload(*, message_id: int, user_id: int, emoji: str, channel_id: int = 555):
    return SimpleNamespace(message_id=message_id, user_id=user_id, emoji=emoji,
                           channel_id=channel_id, guild_id=1, member=None)


def _verlauf_cmd(bot):
    group = bot.get_command("gate")
    assert isinstance(group, commands.Group)
    cmd = group.get_command("verlauf")
    assert cmd is not None, "`gate` group must expose a `verlauf` subcommand"
    return cmd


def _bot_with_fetch(msg):
    """A built bot whose get_channel returns a channel that fetches `msg`."""
    bot = build_bot(_settings(), MagicMock())
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=msg)
    bot.get_channel = MagicMock(return_value=channel)
    return bot, channel


# ── 1. chart caption constant + render still valid PNG ───────────────────────

def test_chart_caption_constant_mentions_the_command():
    from n3x_bot.charts import _CHART_CAPTION
    assert "!gate verlauf" in _CHART_CAPTION


def test_chart_caption_constant_includes_a_gate_hint():
    from n3x_bot.charts import _CHART_CAPTION
    assert ("<gate>" in _CHART_CAPTION) or ("a b c d e z k" in _CHART_CAPTION)


def test_render_with_caption_returns_valid_png_for_populated_chart():
    # Importing the caption constant ties this render check to the new feature:
    # RED until the caption exists; then proves the render still emits a PNG.
    from n3x_bot.charts import render_gate_history_chart, _CHART_CAPTION  # noqa: F401
    now = datetime(2026, 7, 15, tzinfo=ZoneInfo("Europe/Berlin"))
    png = render_gate_history_chart("a", _sample_entries(), now)
    assert isinstance(png, bytes) and len(png) > 0
    assert _open_png(png).format == "PNG"


def test_render_with_caption_returns_valid_png_for_empty_chart():
    from n3x_bot.charts import render_gate_history_chart, _CHART_CAPTION  # noqa: F401
    now = datetime(2026, 7, 15, tzinfo=ZoneInfo("Europe/Berlin"))
    png = render_gate_history_chart("a", [], now)
    assert isinstance(png, bytes) and len(png) > 0
    assert _open_png(png).format == "PNG"


# ── 2a. build_bot inits the tracker map ──────────────────────────────────────

async def test_build_bot_inits_verlauf_msgs_map():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    assert bot._verlauf_msgs == {}
    await repo.close()


# ── 2b. verlauf seeds ❌ and tracks the invoker ──────────────────────────────

async def test_verlauf_adds_cross_reaction_to_posted_message():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    await repo.add_gate_entry("a", 46000, 1, "u1")
    msg = _sent_message(8001)
    ctx = _ctx(msg, author_id=1)

    cmd = _verlauf_cmd(bot)
    await cmd.callback(ctx, "a")

    msg.add_reaction.assert_awaited_once_with("❌")
    await repo.close()


async def test_verlauf_tracks_message_to_invoker_id():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    await repo.add_gate_entry("a", 46000, 1, "u1")
    msg = _sent_message(8001)
    ctx = _ctx(msg, author_id=42)

    cmd = _verlauf_cmd(bot)
    await cmd.callback(ctx, "a")

    assert bot._verlauf_msgs[8001] == 42
    await repo.close()


# ── 2c. handle_verlauf_removal behaviour ─────────────────────────────────────

async def test_removal_by_invoker_deletes_and_untracks():
    from n3x_bot.bot import handle_verlauf_removal
    msg = _sent_message(8001)
    bot, _ = _bot_with_fetch(msg)
    bot._verlauf_msgs = {8001: 1}

    await handle_verlauf_removal(bot, _payload(message_id=8001, user_id=1,
                                               emoji="❌"))

    msg.delete.assert_awaited_once()
    assert 8001 not in bot._verlauf_msgs


async def test_removal_by_different_user_does_not_delete():
    from n3x_bot.bot import handle_verlauf_removal
    msg = _sent_message(8001)
    bot, _ = _bot_with_fetch(msg)
    bot._verlauf_msgs = {8001: 1}

    await handle_verlauf_removal(bot, _payload(message_id=8001, user_id=2,
                                               emoji="❌"))

    msg.delete.assert_not_awaited()
    assert bot._verlauf_msgs == {8001: 1}  # still tracked


async def test_removal_of_the_bot_seed_reaction_does_not_delete():
    # The bot seeds ❌ itself; that reaction (user_id != invoker) must be ignored.
    from n3x_bot.bot import handle_verlauf_removal
    msg = _sent_message(8001)
    bot, _ = _bot_with_fetch(msg)
    bot._verlauf_msgs = {8001: 1}
    bot_seed_user_id = 999999  # the bot's own id, not the invoker

    await handle_verlauf_removal(bot, _payload(message_id=8001,
                                               user_id=bot_seed_user_id,
                                               emoji="❌"))

    msg.delete.assert_not_awaited()
    assert bot._verlauf_msgs == {8001: 1}


async def test_removal_on_untracked_message_is_noop():
    from n3x_bot.bot import handle_verlauf_removal
    msg = _sent_message(8001)
    bot, _ = _bot_with_fetch(msg)
    bot._verlauf_msgs = {}

    await handle_verlauf_removal(bot, _payload(message_id=8001, user_id=1,
                                               emoji="❌"))

    msg.delete.assert_not_awaited()


async def test_removal_ignores_non_cross_emoji():
    from n3x_bot.bot import handle_verlauf_removal
    msg = _sent_message(8001)
    bot, _ = _bot_with_fetch(msg)
    bot._verlauf_msgs = {8001: 1}

    await handle_verlauf_removal(bot, _payload(message_id=8001, user_id=1,
                                               emoji="✅"))

    msg.delete.assert_not_awaited()
    assert bot._verlauf_msgs == {8001: 1}


# ── 2d. on_raw_reaction_add routes ❌ on a tracked verlauf chart to removal ───

async def test_on_raw_reaction_add_routes_cross_to_removal():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    msg = _sent_message(8001)
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=msg)
    bot.get_channel = MagicMock(return_value=channel)
    bot._verlauf_msgs = {8001: 1}

    await bot.on_raw_reaction_add(_payload(message_id=8001, user_id=1,
                                           emoji="❌"))

    msg.delete.assert_awaited_once()
    assert 8001 not in bot._verlauf_msgs
    await repo.close()
