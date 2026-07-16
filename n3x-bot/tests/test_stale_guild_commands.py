"""Tests for the stale-guild-slash-command cleanup + hardened app-command
error handler (bug: 22 phantom guild-scoped slash commands trigger
``CommandNotFound`` → the tree error handler then tries to answer an expired
interaction and raises ``discord.NotFound`` 10062).

Two surfaces are pinned here:

* Part A — ``n3x_bot.bot.clear_stale_guild_commands(bot)``: a module-level
  helper that, per connected guild, clears the (empty) guild command set and
  syncs it, removing the phantoms. Guarded per-guild so one failure doesn't
  abort the rest, and never raises. Also wired into ``on_ready`` after the
  global ``bot.tree.sync()``.
* Part B — the ``@bot.tree.error`` handler (reachable as ``bot.tree.on_error``):
  must ignore ``CommandNotFound`` (phantom/expired interaction) and swallow any
  Discord send failure (``NotFound``/``HTTPException``) instead of raising.

``clear_stale_guild_commands`` is imported *inside* each test (deferred import)
so that, before the coder adds it, each test fails at reference time with a
clear missing-symbol error rather than breaking collection of the whole file.
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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


# ── fakes ────────────────────────────────────────────────────────────────────

class _FakeTree:
    """Minimal stand-in for ``bot.tree`` exposing the two calls the helper
    uses. ``clear_commands`` is sync (matches discord.py); ``sync`` is async."""

    def __init__(self, sync_side_effect=None):
        self.clear_commands = MagicMock()
        self.sync = AsyncMock(side_effect=sync_side_effect)


class _FakeGuild:
    def __init__(self, guild_id):
        self.id = guild_id


def _fake_bot(guilds, sync_side_effect=None):
    return SimpleNamespace(tree=_FakeTree(sync_side_effect=sync_side_effect),
                           guilds=list(guilds))


def _fake_interaction(is_done=False):
    interaction = MagicMock()
    interaction.response.is_done = MagicMock(return_value=is_done)
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


# ── Part A: clear_stale_guild_commands ───────────────────────────────────────

async def test_clear_stale_guild_commands_clears_and_syncs_each_guild():
    from n3x_bot.bot import clear_stale_guild_commands

    g1, g2 = _FakeGuild(11), _FakeGuild(22)
    bot = _fake_bot([g1, g2])

    await clear_stale_guild_commands(bot)

    # clear_commands(guild=g) once per guild
    cleared = [c.kwargs.get("guild") for c in bot.tree.clear_commands.call_args_list]
    assert cleared == [g1, g2]
    # sync(guild=g) awaited once per guild
    synced = [c.kwargs.get("guild") for c in bot.tree.sync.await_args_list]
    assert synced == [g1, g2]


async def test_clear_stale_guild_commands_syncs_after_clearing_per_guild():
    from n3x_bot.bot import clear_stale_guild_commands

    order = []
    g = _FakeGuild(11)
    bot = _fake_bot([g])
    bot.tree.clear_commands.side_effect = lambda *a, **k: order.append("clear")
    bot.tree.sync.side_effect = lambda *a, **k: order.append("sync")

    await clear_stale_guild_commands(bot)

    assert order == ["clear", "sync"]


async def test_clear_stale_guild_commands_processes_all_guilds_despite_one_failing():
    from n3x_bot.bot import clear_stale_guild_commands

    g1, g2, g3 = _FakeGuild(11), _FakeGuild(22), _FakeGuild(33)
    bot = _fake_bot([g1, g2, g3])

    def _sync(*args, guild=None, **kwargs):
        if guild is g2:
            raise discord.HTTPException(MagicMock(status=403, reason="Forbidden"),
                                        "missing access")

    bot.tree.sync.side_effect = _sync

    # must not raise even though guild g2's sync blows up
    await clear_stale_guild_commands(bot)

    synced = [c.kwargs.get("guild") for c in bot.tree.sync.await_args_list]
    assert synced == [g1, g2, g3]  # all attempted, g2's failure didn't abort


async def test_clear_stale_guild_commands_is_noop_with_no_guilds():
    from n3x_bot.bot import clear_stale_guild_commands

    bot = _fake_bot([])

    await clear_stale_guild_commands(bot)  # must not raise

    bot.tree.clear_commands.assert_not_called()
    bot.tree.sync.assert_not_awaited()


# ── Part A wiring: on_ready invokes the helper after the global sync ─────────

async def test_on_ready_clears_stale_guild_commands_after_global_sync(monkeypatch):
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=0)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)

    order = []
    bot.tree.sync = AsyncMock(side_effect=lambda *a, **k: order.append("global_sync"))
    fake_clear = AsyncMock(side_effect=lambda *a, **k: order.append("clear_stale"))
    monkeypatch.setattr("n3x_bot.bot.clear_stale_guild_commands", fake_clear)

    await bot.on_ready()

    fake_clear.assert_awaited_once_with(bot)
    assert order.index("global_sync") < order.index("clear_stale")

    await repo.close()


async def test_on_ready_clears_stale_guild_commands_only_once_across_reconnects(monkeypatch):
    # on_ready re-fires on every gateway reconnect; the stale-command clear must
    # be one-shot per process, not repeated on each reconnect. First on_ready
    # clears; a SECOND on_ready on the same bot must NOT clear again.
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=0)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)

    bot.tree.sync = AsyncMock()
    fake_clear = AsyncMock()
    monkeypatch.setattr("n3x_bot.bot.clear_stale_guild_commands", fake_clear)

    await bot.on_ready()
    await bot.on_ready()

    fake_clear.assert_awaited_once_with(bot)

    await repo.close()


# ── Part B: hardened on_app_command_error ────────────────────────────────────

async def test_on_app_command_error_ignores_command_not_found():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    interaction = _fake_interaction(is_done=False)
    error = discord.app_commands.CommandNotFound("base", [])

    # phantom command → must NOT attempt to message the (expired) interaction
    await bot.tree.on_error(interaction, error)

    interaction.response.send_message.assert_not_called()
    interaction.followup.send.assert_not_called()

    await repo.close()


async def test_on_app_command_error_ignores_wrapped_command_not_found():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    interaction = _fake_interaction(is_done=False)
    error = SimpleNamespace(original=discord.app_commands.CommandNotFound("stat", []))

    await bot.tree.on_error(interaction, error)

    interaction.response.send_message.assert_not_called()
    interaction.followup.send.assert_not_called()

    await repo.close()


async def test_on_app_command_error_swallows_notfound_on_send():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    interaction = _fake_interaction(is_done=False)
    interaction.response.send_message = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404, reason="Not Found"),
                                     "10062 Unknown interaction"))
    error = SimpleNamespace(original=RuntimeError("boom"))

    # a live interaction whose send fails with 10062 must be swallowed, not raised
    await bot.tree.on_error(interaction, error)

    interaction.response.send_message.assert_awaited_once()

    await repo.close()


async def test_on_app_command_error_swallows_httpexception_on_followup():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    interaction = _fake_interaction(is_done=True)  # already responded → followup path
    interaction.followup.send = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(status=400, reason="Bad Request"),
                                          "cannot send"))
    error = SimpleNamespace(original=RuntimeError("boom"))

    await bot.tree.on_error(interaction, error)  # must not raise

    interaction.followup.send.assert_awaited_once()

    await repo.close()


# ── Part B regression guards: happy-path behavior must be preserved ──────────

async def test_on_app_command_error_still_surfaces_valueerror_reason():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    interaction = _fake_interaction(is_done=False)
    error = SimpleNamespace(original=ValueError("no message named 'x'"))

    await bot.tree.on_error(interaction, error)

    interaction.response.send_message.assert_awaited_once()
    assert "no message named" in interaction.response.send_message.await_args.args[0]
    assert interaction.response.send_message.await_args.kwargs.get("ephemeral") is True

    await repo.close()


async def test_on_app_command_error_still_sends_german_fallback_for_generic_error():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    interaction = _fake_interaction(is_done=False)
    error = SimpleNamespace(original=RuntimeError("unexpected"))

    await bot.tree.on_error(interaction, error)

    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.args[0] == "❌ Ein Fehler ist aufgetreten."

    await repo.close()
