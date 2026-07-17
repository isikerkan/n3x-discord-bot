"""Tests for the stale-guild-slash-command cleanup + hardened app-command
error handler (bug: 22 phantom guild-scoped slash commands trigger
``CommandNotFound`` → the tree error handler then tries to answer an expired
interaction and raises ``discord.NotFound`` 10062).

Two surfaces are pinned here:

* Part A — ``n3x_bot.bot.sync_commands_to_guilds(bot)``: a module-level helper
  (supersedes the old ``clear_stale_guild_commands``) that, per connected guild,
  clears any guild-scoped leftovers, copies the globally-registered app commands
  into the guild (so they appear INSTANTLY) and syncs — registering ours and
  removing phantoms not in our tree. Guarded per-guild so one failure doesn't
  abort the rest, and never raises. Also wired into ``on_ready`` after the
  global ``bot.tree.sync()``.
* Part B — the ``@bot.tree.error`` handler (reachable as ``bot.tree.on_error``):
  must ignore ``CommandNotFound`` (phantom/expired interaction) and swallow any
  Discord send failure (``NotFound``/``HTTPException``) instead of raising.

``sync_commands_to_guilds`` is imported *inside* each test (deferred import)
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
    """Minimal stand-in for ``bot.tree`` exposing the calls the helper uses.
    ``clear_commands``, ``copy_global_to``, ``get_commands`` and ``add_command``
    are sync (matches discord.py); ``sync`` is async. ``get_commands`` returns
    an empty snapshot by default so the trailing global reset is a no-op here —
    the real snapshot/restore behaviour is pinned by the recording-tree test."""

    def __init__(self, sync_side_effect=None):
        self.clear_commands = MagicMock()
        self.copy_global_to = MagicMock()
        self.get_commands = MagicMock(return_value=[])
        self.add_command = MagicMock()
        self.sync = AsyncMock(side_effect=sync_side_effect)


class _FakeGuild:
    def __init__(self, guild_id):
        self.id = guild_id


def _fake_ready_guild(guild_id):
    """A guild rich enough to survive on_ready's member/voice seeding loops:
    fetch_members raises (forcing the guild.members fallback), and both
    members and voice_channels are empty so the loops are trivial no-ops."""
    guild = MagicMock()
    guild.id = guild_id

    def fetch_members(limit=None):
        raise RuntimeError("no gateway")

    guild.fetch_members = fetch_members
    guild.members = []
    guild.voice_channels = []
    return guild


def _fake_bot(guilds, sync_side_effect=None):
    return SimpleNamespace(tree=_FakeTree(sync_side_effect=sync_side_effect),
                           guilds=list(guilds))


def _fake_interaction(is_done=False):
    interaction = MagicMock()
    interaction.response.is_done = MagicMock(return_value=is_done)
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


# ── Part A: sync_commands_to_guilds ──────────────────────────────────────────

async def test_sync_commands_to_guilds_clears_copies_and_syncs_each_guild():
    from n3x_bot.bot import sync_commands_to_guilds

    g1, g2 = _FakeGuild(11), _FakeGuild(22)
    bot = _fake_bot([g1, g2])

    await sync_commands_to_guilds(bot)

    # clear_commands(guild=g) once per guild, THEN a trailing clear of the
    # GLOBAL scope (guild=None) so previously-published global commands don't
    # double-list alongside the guild-scoped copies.
    cleared = [c.kwargs.get("guild") for c in bot.tree.clear_commands.call_args_list]
    assert cleared == [g1, g2, None]
    # copy_global_to(guild=g) once per guild — makes the app commands appear
    # instantly in the guild rather than waiting on global propagation. No
    # trailing copy: the global scope is only emptied, never repopulated.
    copied = [c.kwargs.get("guild") for c in bot.tree.copy_global_to.call_args_list]
    assert copied == [g1, g2]
    # sync(guild=g) awaited once per guild, THEN a trailing global sync()
    # (guild=None) that publishes the now-empty global scope.
    synced = [c.kwargs.get("guild") for c in bot.tree.sync.await_args_list]
    assert synced == [g1, g2, None]


async def test_sync_commands_to_guilds_clears_then_copies_then_syncs_per_guild():
    from n3x_bot.bot import sync_commands_to_guilds

    order = []
    g = _FakeGuild(11)
    bot = _fake_bot([g])
    bot.tree.clear_commands.side_effect = lambda *a, **k: order.append("clear")
    bot.tree.copy_global_to.side_effect = lambda *a, **k: order.append("copy")
    bot.tree.sync.side_effect = lambda *a, **k: order.append("sync")

    await sync_commands_to_guilds(bot)

    # per-guild: clear -> copy -> sync; then the trailing global clear -> sync
    # that empties the published global scope.
    assert order == ["clear", "copy", "sync", "clear", "sync"]


async def test_sync_commands_to_guilds_processes_all_guilds_despite_one_failing():
    from n3x_bot.bot import sync_commands_to_guilds

    g1, g2, g3 = _FakeGuild(11), _FakeGuild(22), _FakeGuild(33)
    bot = _fake_bot([g1, g2, g3])

    def _sync(*args, guild=None, **kwargs):
        if guild is g2:
            raise discord.HTTPException(MagicMock(status=403, reason="Forbidden"),
                                        "missing access")

    bot.tree.sync.side_effect = _sync

    # must not raise even though guild g2's sync blows up
    await sync_commands_to_guilds(bot)

    synced = [c.kwargs.get("guild") for c in bot.tree.sync.await_args_list]
    # all guilds attempted (g2's failure didn't abort), plus the trailing
    # global sync (guild=None).
    assert synced == [g1, g2, g3, None]


async def test_sync_commands_to_guilds_is_noop_with_no_guilds():
    from n3x_bot.bot import sync_commands_to_guilds

    bot = _fake_bot([])

    await sync_commands_to_guilds(bot)  # must not raise

    bot.tree.clear_commands.assert_not_called()
    bot.tree.copy_global_to.assert_not_called()
    bot.tree.sync.assert_not_awaited()


class _RecordingTree:
    """A fake tree that models the in-memory global command set and records what
    gets *published* to each scope, so we can assert real behaviour rather than
    just call order. Mirrors the discord.py surface the helper touches:
    ``get_commands()`` / ``get_commands(guild=g)``, ``clear_commands(guild=…)``,
    ``copy_global_to(guild=g)`` and the async ``sync(guild=…)``."""

    def __init__(self):
        self._global = []            # in-memory global (guild=None) commands
        self._per_guild = {}         # gid -> in-memory guild-scoped commands
        self.published = {}          # scope key (gid or None) -> published names

    def add_command(self, cmd, *, guild=None):
        if guild is None:
            self._global.append(cmd)
        else:
            self._per_guild.setdefault(guild.id, []).append(cmd)

    def get_commands(self, *, guild=None):
        if guild is None:
            return list(self._global)
        return list(self._per_guild.get(guild.id, []))

    def clear_commands(self, *, guild=None):
        if guild is None:
            self._global = []
        else:
            self._per_guild[guild.id] = []

    def copy_global_to(self, *, guild):
        self._per_guild[guild.id] = list(self._global)

    async def sync(self, *, guild=None):
        key = guild.id if guild is not None else None
        cmds = self._global if guild is None else self._per_guild.get(guild.id, [])
        self.published[key] = [c.name for c in cmds]
        return []


async def test_sync_publishes_full_set_and_leaves_global_tree_intact():
    # This is the regression that a call-order-only test misses: after
    # sync_commands_to_guilds the guild must receive the FULL command set, and
    # the in-memory global tree must survive so a SECOND sync (as triggered by
    # admin CRUD via resync_stat_commands) still publishes everything again.
    from n3x_bot.bot import sync_commands_to_guilds

    tree = _RecordingTree()
    for name in ("admin", "gate", "config", "content", "overview", "rank"):
        tree.add_command(SimpleNamespace(name=name))

    guild = _FakeGuild(11)
    bot = SimpleNamespace(tree=tree, guilds=[guild])
    full = {"admin", "gate", "config", "content", "overview", "rank"}

    await sync_commands_to_guilds(bot)

    # (a) the guild received the full command set…
    assert set(tree.published[11]) == full
    # …and the published GLOBAL scope was emptied (no double-listing).
    assert tree.published[None] == []
    # (b) the in-memory global tree survived the global reset.
    assert {c.name for c in tree.get_commands()} == full

    # Simulate an admin CRUD: a new stat is added to the global tree, then a
    # re-sync fires. The guild must get the full set PLUS the new stat — not
    # get wiped down to just the newly-added command.
    tree.add_command(SimpleNamespace(name="newstat"))
    await sync_commands_to_guilds(bot)

    assert set(tree.published[11]) == full | {"newstat"}
    assert {c.name for c in tree.get_commands()} == full | {"newstat"}


# ── Part A wiring: on_ready invokes the helper (no standalone global sync) ────

async def test_on_ready_syncs_commands_to_guilds_via_helper(monkeypatch):
    # The old standalone global `bot.tree.sync()` in on_ready is GONE — global
    # publishing now happens inside sync_commands_to_guilds. on_ready must
    # invoke the guild-sync helper (and NOT do its own separate global sync)
    # once there is at least one connected guild.
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=0)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)

    guild = _fake_ready_guild(11)
    monkeypatch.setattr(type(bot), "guilds", property(lambda self: [guild]))

    bot.tree.sync = AsyncMock()  # would flag a stray standalone global sync
    fake_sync = AsyncMock()
    monkeypatch.setattr("n3x_bot.bot.sync_commands_to_guilds", fake_sync)

    await bot.on_ready()

    fake_sync.assert_awaited_once_with(bot)
    # on_ready itself no longer runs a standalone global sync; publishing is
    # entirely delegated to sync_commands_to_guilds (mocked out here).
    bot.tree.sync.assert_not_awaited()

    await repo.close()


async def test_on_ready_skips_guild_sync_when_no_guilds(monkeypatch):
    # A first on_ready that fires before any guild is available must NOT run
    # (or flag as done) the guild sync — otherwise the one-shot guard would
    # wedge and the commands would never publish. It retries on a later ready.
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=0)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)

    bot.tree.sync = AsyncMock()
    fake_sync = AsyncMock()
    monkeypatch.setattr("n3x_bot.bot.sync_commands_to_guilds", fake_sync)

    await bot.on_ready()  # bot.guilds is empty

    fake_sync.assert_not_awaited()
    assert bot._stale_guild_commands_cleared is False  # guard not wedged

    await repo.close()


async def test_on_ready_syncs_commands_to_guilds_only_once_across_reconnects(monkeypatch):
    # on_ready re-fires on every gateway reconnect; the guild command sync must
    # be one-shot per process, not repeated on each reconnect. First on_ready
    # syncs; a SECOND on_ready on the same bot must NOT sync again.
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=0)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)

    guild = _fake_ready_guild(11)
    monkeypatch.setattr(type(bot), "guilds", property(lambda self: [guild]))

    bot.tree.sync = AsyncMock()
    fake_sync = AsyncMock()
    monkeypatch.setattr("n3x_bot.bot.sync_commands_to_guilds", fake_sync)

    await bot.on_ready()
    await bot.on_ready()

    fake_sync.assert_awaited_once_with(bot)

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
