"""Regression + wiring tests for the gate-embed persistence bug.

THE BUG: ``update_gate_stats_embed`` (n3x_bot/bot.py) tracks the last-posted
gate-stats embed message id in-memory on ``bot._gate_embed_msg_id`` (init None
in ``build_bot``). On restart that resets to None, so the bot ``channel.send``s
a BRAND NEW embed instead of editing the existing one — the embed re-posts
after every restart.

THE FIX (asserted here): persist the message id in the DB via the new
``channel_messages`` store under the key "gate_stats" and edit-in-place across
restarts.

RED reasons:
  * The persistence assertions call ``repo.set_channel_message`` /
    ``repo.get_channel_message`` which don't exist yet -> AttributeError.
  * The restart regression proves the actual bug: a FRESH build_bot with a repo
    that already stored "gate_stats" must EDIT the stored message, not post a
    new one. The current in-memory implementation posts a new one.

Imports of n3x_bot live inside test/helper bodies so a not-yet-implemented
symbol never turns into a module-collection ImportError (fail on assertion /
missing attribute, never on import).
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

GATE_STATS_KEY = "gate_stats"

BASE_SETTINGS_KWARGS = dict(
    discord_token="tok",
    target_role_id=1,
    welcome_channel_id=2,
    reminder_channel_id=999,
    julez_id=424242,
    _env_file=None,
    _env_prefix="NONEXISTENT_",
)


def _settings(**overrides):
    from n3x_bot.config import Settings
    kwargs = dict(BASE_SETTINGS_KWARGS)
    kwargs.update(overrides)
    return Settings(**kwargs)


async def _flatfile_repo():
    from n3x_bot.storage.json_repo import JsonRepository
    from n3x_bot.seed import seed_defaults
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    repo = JsonRepository(path)
    await repo.connect()
    await seed_defaults(repo)
    return repo


def _fake_channel(send_return_id: int = 42, channel_id: int = 555):
    """A channel whose .send returns a msg with .id and whose .fetch_message
    returns a msg with an awaitable .edit."""
    channel = MagicMock()
    channel.id = channel_id
    channel.send = AsyncMock(return_value=SimpleNamespace(id=send_return_id))
    fetched = MagicMock()
    fetched.edit = AsyncMock()
    fetched.add_reaction = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=fetched)
    return channel, fetched


# ── first post: sends AND persists the id under "gate_stats" ────────────────

async def test_first_post_persists_channel_message_in_repo():
    from n3x_bot.bot import build_bot, update_gate_stats_embed
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=555)
    bot = build_bot(settings, repo)
    channel, _ = _fake_channel(send_return_id=42, channel_id=555)
    bot.get_channel = MagicMock(return_value=channel)

    await update_gate_stats_embed(bot, repo, settings)

    channel.send.assert_awaited_once()
    assert await repo.get_channel_message(GATE_STATS_KEY) == (42, 555)

    await repo.close()


# ── second call within the same run edits the persisted message ─────────────

async def test_second_call_edits_persisted_message_without_new_send():
    from n3x_bot.bot import build_bot, update_gate_stats_embed
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=555)
    bot = build_bot(settings, repo)
    channel, fetched = _fake_channel(send_return_id=42, channel_id=555)
    bot.get_channel = MagicMock(return_value=channel)

    await update_gate_stats_embed(bot, repo, settings)
    await update_gate_stats_embed(bot, repo, settings)

    channel.fetch_message.assert_awaited_once_with(42)
    fetched.edit.assert_awaited_once()
    channel.send.assert_awaited_once()  # only the first call posted
    # editing must NOT churn the persisted id.
    assert await repo.get_channel_message(GATE_STATS_KEY) == (42, 555)

    await repo.close()


# ── THE REGRESSION: a restart (fresh bot, same repo) edits, not re-posts ────

async def test_restart_edits_persisted_message_instead_of_reposting():
    from n3x_bot.bot import build_bot, update_gate_stats_embed
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=555)

    # A prior run already posted the embed and persisted its id.
    await repo.set_channel_message(GATE_STATS_KEY, 42, 555)

    # Restart: a brand-new bot whose in-memory _gate_embed_msg_id is None again.
    bot = build_bot(settings, repo)
    channel, fetched = _fake_channel(send_return_id=999, channel_id=555)
    bot.get_channel = MagicMock(return_value=channel)

    await update_gate_stats_embed(bot, repo, settings)

    channel.fetch_message.assert_awaited_once_with(42)
    fetched.edit.assert_awaited_once()
    channel.send.assert_not_called()  # MUST NOT re-post after restart

    await repo.close()


# ── fetch-fail (stored message deleted): re-post and re-persist the new id ──

async def test_restart_reposts_and_repersists_when_stored_message_gone():
    from n3x_bot.bot import build_bot, update_gate_stats_embed
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=555)
    await repo.set_channel_message(GATE_STATS_KEY, 42, 555)

    bot = build_bot(settings, repo)
    channel, _ = _fake_channel(send_return_id=99, channel_id=555)
    channel.fetch_message = AsyncMock(side_effect=RuntimeError("gone"))
    bot.get_channel = MagicMock(return_value=channel)

    await update_gate_stats_embed(bot, repo, settings)  # must not raise

    channel.send.assert_awaited_once()
    assert await repo.get_channel_message(GATE_STATS_KEY) == (99, 555)

    await repo.close()


# ── noop paths persist nothing ──────────────────────────────────────────────

async def test_noop_when_channel_unset_persists_nothing():
    from n3x_bot.bot import build_bot, update_gate_stats_embed
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=0)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock()

    await update_gate_stats_embed(bot, repo, settings)

    bot.get_channel.assert_not_called()
    assert await repo.get_channel_message(GATE_STATS_KEY) is None

    await repo.close()


async def test_noop_when_channel_missing_persists_nothing():
    from n3x_bot.bot import build_bot, update_gate_stats_embed
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=555)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)

    await update_gate_stats_embed(bot, repo, settings)  # must not raise

    assert await repo.get_channel_message(GATE_STATS_KEY) is None

    await repo.close()


async def test_edit_path_seeds_reload_reaction_on_existing_message():
    from n3x_bot.bot import build_bot, update_gate_stats_embed
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=555)
    bot = build_bot(settings, repo)
    channel, fetched = _fake_channel(send_return_id=42, channel_id=555)
    bot.get_channel = MagicMock(return_value=channel)

    await update_gate_stats_embed(bot, repo, settings)   # first post
    await update_gate_stats_embed(bot, repo, settings)   # edit path
    fetched.add_reaction.assert_any_await("🔄")
    await repo.close()
