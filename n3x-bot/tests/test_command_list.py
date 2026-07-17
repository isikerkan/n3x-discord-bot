"""RED spec for the self-editing German **command-list channel** (roadmap #8).

FEATURE: a bot-maintained German message in a configured channel that lists
every available bot command. It is **registry-driven** — the list is enumerated
from the live command registry (`bot.commands`, walking groups → their
subcommands), never a hardcoded static list — and **self-editing**, persisted
via the ``channel_messages`` store under a NEW key ``"command_list"`` and edited
in place across restarts, exactly like the gate-input Anleitung
(``update_gate_input_help`` / ``GATE_INPUT_HELP_KEY``).

New symbols under test (none exist yet -> RED):
  * config: ``Settings.command_list_channel_id`` (int, default 0, env
    ``COMMAND_LIST_CHANNEL_ID``).
  * resolver: ``command_list_channel_id`` in ``OVERRIDABLE_KEYS`` and the
    ``RuntimeConfig.command_list_channel_id`` int property (override wins, else
    Settings).
  * ``config_commands.CHANNEL_PURPOSES["command_list"] == "command_list_channel_id"``
    (so ``!config channel command_list`` works).
  * ``n3x_bot.bot.build_command_list(bot) -> discord.Embed`` — registry-driven,
    German, ``!``-prefixed, deterministic.
  * ``n3x_bot.bot._COMMAND_DESCRIPTIONS`` — curated ``dict[str, str]`` of short
    German blurbs, keyed by command **qualified name** (see PINNED below).
  * ``n3x_bot.bot.update_command_list(bot, repo, settings)`` — async; mirrors
    ``update_gate_input_help`` against
    ``bot.runtime_config.command_list_channel_id``.
  * ``n3x_bot.bot.COMMAND_LIST_KEY == "command_list"``.
  * ``on_ready`` best-effort wiring: posts/edits the list when the channel is
    configured.

PINNED ASSUMPTIONS (flagged in the handoff report):
  * Embed title contains the German word ``"Befehl"`` (e.g. "📋 Befehle" or
    "Befehlsübersicht"); the exact wording/emoji is the architect's call.
  * ``_COMMAND_DESCRIPTIONS`` is keyed by ``Command.qualified_name`` (e.g.
    ``"rank"``, ``"gate verlauf"``), and whatever German blurb it holds for a
    key flows verbatim into the embed text. The tests assert the map DRIVES the
    embed (map value appears in the text) rather than hardcoding German prose.
  * The list is ``!``-prefixed and sorted (deterministic).
  * Per-stat dynamic commands (tit/smart/…) ARE included once registered.
  * The built-in ``help`` command MAY be included or excluded — not asserted.

Imports of n3x_bot live INSIDE test bodies (lazy) so a not-yet-implemented
symbol fails as a per-test runtime missing-symbol (ImportError/AttributeError)
at call time, never as a collection-time ImportError.
"""

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# Pinned in the report. Hard-coded here so the assertions double as the contract.
COMMAND_LIST_KEY = "command_list"
COMMAND_LIST_CHANNEL_KEY = "command_list_channel_id"

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
    repo._test_path = path
    return repo


async def _cleanup(repo):
    path = getattr(repo, "_test_path", None)
    await repo.close()
    if path and os.path.exists(path):
        os.remove(path)


def _fake_channel(send_return_id: int = 42, channel_id: int = 777):
    """A channel whose .send returns a msg with .id and whose .fetch_message
    returns a msg with an awaitable .edit (mirrors test_gate_input_help)."""
    channel = MagicMock()
    channel.id = channel_id
    channel.send = AsyncMock(return_value=SimpleNamespace(id=send_return_id))
    fetched = MagicMock()
    fetched.edit = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=fetched)
    return channel, fetched


def _embed_text(result) -> str:
    """Flatten a build_command_list result to one searchable string.

    Works whether it returns a ``str`` or a ``discord.Embed`` (title +
    description + every field name/value) so content assertions pin only the
    substrings, not the container."""
    if isinstance(result, str):
        return result
    parts = [result.title or "", result.description or ""]
    for field in getattr(result, "fields", []):
        parts.append(field.name or "")
        parts.append(field.value or "")
    return "\n".join(parts)


async def _populated_bot(settings, repo):
    """A built bot whose registry is fully populated — build_bot wires the
    static commands/groups, and register_stat_commands (run in on_ready IRL)
    adds `rank` + the dynamic per-stat commands."""
    from n3x_bot.bot import build_bot, register_stat_commands
    bot = build_bot(settings, repo)
    await register_stat_commands(bot, repo, settings)
    return bot


# ── Settings: command_list_channel_id ────────────────────────────────────────

def test_command_list_channel_id_defaults_to_zero():
    s = _settings()
    assert s.command_list_channel_id == 0


def test_command_list_channel_id_read_from_env(monkeypatch):
    monkeypatch.setenv("COMMAND_LIST_CHANNEL_ID", "556677")
    from n3x_bot.config import Settings
    s = Settings(
        discord_token="tok",
        target_role_id=1,
        welcome_channel_id=2,
        reminder_channel_id=3,
        _env_file=None,
    )
    assert s.command_list_channel_id == 556677


# ── RuntimeConfig resolver: command_list_channel_id ─────────────────────────

def test_command_list_channel_id_in_overridable_keys():
    from n3x_bot.runtime_config import OVERRIDABLE_KEYS
    assert COMMAND_LIST_CHANNEL_KEY in OVERRIDABLE_KEYS


def test_resolver_command_list_channel_id_no_override_equals_settings():
    from n3x_bot.runtime_config import RuntimeConfig
    settings = _settings(command_list_channel_id=555)
    rc = RuntimeConfig(settings)
    assert rc.command_list_channel_id == 555


def test_resolver_command_list_channel_id_override_wins_and_is_int():
    from n3x_bot.runtime_config import RuntimeConfig
    settings = _settings(command_list_channel_id=555)
    rc = RuntimeConfig(settings, {COMMAND_LIST_CHANNEL_KEY: "999"})
    assert rc.command_list_channel_id == 999
    assert isinstance(rc.command_list_channel_id, int)


# ── config_commands purpose mapping (enables !config channel command_list) ───

def test_command_list_channel_purpose_maps_to_key():
    from n3x_bot.config_commands import CHANNEL_PURPOSES
    assert CHANNEL_PURPOSES["command_list"] == COMMAND_LIST_CHANNEL_KEY


# ── .env.example documents the new toggle ────────────────────────────────────

def test_env_example_documents_command_list_channel_id():
    root = Path(__file__).resolve().parent.parent
    text = (root / ".env.example").read_text()
    assert "COMMAND_LIST_CHANNEL_ID" in text


# ── COMMAND_LIST_KEY contract ────────────────────────────────────────────────

def test_command_list_key_value():
    from n3x_bot.bot import COMMAND_LIST_KEY as key
    assert key == COMMAND_LIST_KEY


# ── build_command_list: German title ─────────────────────────────────────────

async def test_command_list_title_is_german():
    from n3x_bot.bot import build_command_list
    repo = await _flatfile_repo()
    bot = await _populated_bot(_settings(), repo)
    result = build_command_list(bot)
    title = result if isinstance(result, str) else (result.title or "")
    assert "Befehl" in title
    await _cleanup(repo)


# ── build_command_list: names registered top-level commands, !-prefixed ──────

async def test_command_list_contains_prefixed_top_level_commands():
    from n3x_bot.bot import build_command_list
    repo = await _flatfile_repo()
    bot = await _populated_bot(_settings(), repo)
    text = _embed_text(build_command_list(bot))
    # overview/stat/gate/config/content/admin and (Phase 5) kodex/base/basestop/
    # sync_welcome migrated to slash-only app commands, so they no longer appear
    # in the prefix-derived list. `rank` and `sync_achievements` remain prefix.
    for name in ("rank", "sync_achievements"):
        assert f"!{name}" in text, name
    await _cleanup(repo)


async def test_command_list_has_no_prefix_groups_after_admin_migration():
    # Phase 4 removed the last prefix command GROUP (`!admin`): `gate`/`config`/
    # `content`/`admin` are all slash app-command groups now. So the prefix-derived
    # command list contains no group subcommands — in particular no `!admin msg`
    # line — and every remaining prefix command is flat (not a commands.Group).
    from discord.ext import commands
    from n3x_bot.bot import build_command_list
    repo = await _flatfile_repo()
    bot = await _populated_bot(_settings(), repo)

    assert bot.get_command("admin") is None  # prefix admin group gone
    assert not any(isinstance(c, commands.Group) for c in bot.commands)

    text = _embed_text(build_command_list(bot))
    assert "!admin msg" not in text  # no group subcommand lines remain

    await _cleanup(repo)


async def test_command_list_includes_dynamic_per_stat_commands():
    # Per-stat commands are registered dynamically by register_stat_commands;
    # a registry-driven list must surface them too.
    from n3x_bot.bot import build_command_list
    repo = await _flatfile_repo()
    bot = await _populated_bot(_settings(), repo)
    stat_names = {c.name for c in bot.commands} & {"tit", "smart", "afk", "cry"}
    assert stat_names, "precondition: at least one per-stat command registered"
    text = _embed_text(build_command_list(bot))
    for name in stat_names:
        assert f"!{name}" in text, name
    await _cleanup(repo)


# ── build_command_list: PROVABLY registry-derived (not a hardcoded list) ─────

async def test_command_list_is_derived_from_the_live_registry():
    # Enumerate the live registry and assert every top-level command surfaces.
    # (`help`, discord.py's built-in, is exempt — its inclusion is the coder's
    # call.) This fails if the list is hardcoded and drifts from the registry.
    from n3x_bot.bot import build_command_list
    repo = await _flatfile_repo()
    bot = await _populated_bot(_settings(), repo)
    text = _embed_text(build_command_list(bot))
    for command in bot.commands:
        if command.name == "help":
            continue
        assert f"!{command.name}" in text, command.name
    await _cleanup(repo)


# ── build_command_list: the curated description map DRIVES the embed ─────────

async def test_curated_description_map_has_rank_blurb():
    from n3x_bot.bot import _COMMAND_DESCRIPTIONS
    assert isinstance(_COMMAND_DESCRIPTIONS, dict)
    assert _COMMAND_DESCRIPTIONS.get("rank")  # non-empty German blurb


async def test_curated_rank_description_appears_in_embed():
    # Whatever blurb the map holds for `rank`, it must flow into the embed —
    # proving the description column is map-driven, not invented per-render.
    from n3x_bot.bot import build_command_list, _COMMAND_DESCRIPTIONS
    repo = await _flatfile_repo()
    bot = await _populated_bot(_settings(), repo)
    text = _embed_text(build_command_list(bot))
    assert _COMMAND_DESCRIPTIONS["rank"] in text
    await _cleanup(repo)


# ── build_command_list: deterministic + sorted ──────────────────────────────

async def test_command_list_is_deterministic():
    from n3x_bot.bot import build_command_list
    repo = await _flatfile_repo()
    bot = await _populated_bot(_settings(), repo)
    assert _embed_text(build_command_list(bot)) == _embed_text(build_command_list(bot))
    await _cleanup(repo)


async def test_command_list_top_level_commands_are_sorted():
    # `rank` sorts before `sync_achievements`; a sorted render places it earlier.
    # (admin/activity/stat/gate/config/content and Phase-5 kodex/base migrated to
    # slash-only app commands and are no longer in the prefix-derived list.)
    from n3x_bot.bot import build_command_list
    repo = await _flatfile_repo()
    bot = await _populated_bot(_settings(), repo)
    text = _embed_text(build_command_list(bot))
    assert text.index("!rank") < text.index("!sync_achievements")
    await _cleanup(repo)


# ── update_command_list: first post sends AND persists the id ────────────────

async def test_first_post_persists_command_list_in_repo():
    from n3x_bot.bot import build_bot, update_command_list
    repo = await _flatfile_repo()
    settings = _settings(command_list_channel_id=777)
    bot = build_bot(settings, repo)
    channel, _ = _fake_channel(send_return_id=42, channel_id=777)
    bot.get_channel = MagicMock(return_value=channel)

    await update_command_list(bot, repo, settings)

    channel.send.assert_awaited_once()
    assert await repo.get_channel_message(COMMAND_LIST_KEY) == (42, 777)
    await _cleanup(repo)


# ── second call in the same run edits the persisted message ──────────────────

async def test_second_call_edits_persisted_list_without_new_send():
    from n3x_bot.bot import build_bot, update_command_list
    repo = await _flatfile_repo()
    settings = _settings(command_list_channel_id=777)
    bot = build_bot(settings, repo)
    channel, fetched = _fake_channel(send_return_id=42, channel_id=777)
    bot.get_channel = MagicMock(return_value=channel)

    await update_command_list(bot, repo, settings)
    await update_command_list(bot, repo, settings)

    channel.fetch_message.assert_awaited_once_with(42)
    fetched.edit.assert_awaited_once()
    channel.send.assert_awaited_once()  # only the first call posted
    assert await repo.get_channel_message(COMMAND_LIST_KEY) == (42, 777)
    await _cleanup(repo)


# ── THE REGRESSION: restart (fresh bot, same repo) edits, not re-posts ───────

async def test_restart_edits_persisted_list_instead_of_reposting():
    from n3x_bot.bot import build_bot, update_command_list
    repo = await _flatfile_repo()
    settings = _settings(command_list_channel_id=777)

    # A prior run already posted the list and persisted its id.
    await repo.set_channel_message(COMMAND_LIST_KEY, 42, 777)

    # Restart: a brand-new bot with no in-memory cache.
    bot = build_bot(settings, repo)
    channel, fetched = _fake_channel(send_return_id=999, channel_id=777)
    bot.get_channel = MagicMock(return_value=channel)

    await update_command_list(bot, repo, settings)

    channel.fetch_message.assert_awaited_once_with(42)
    fetched.edit.assert_awaited_once()
    channel.send.assert_not_called()  # MUST NOT re-post after restart
    await _cleanup(repo)


# ── fetch-fail (stored message deleted): re-post + re-persist the new id ─────

async def test_restart_reposts_and_repersists_when_stored_list_gone():
    from n3x_bot.bot import build_bot, update_command_list
    repo = await _flatfile_repo()
    settings = _settings(command_list_channel_id=777)
    await repo.set_channel_message(COMMAND_LIST_KEY, 42, 777)

    bot = build_bot(settings, repo)
    channel, _ = _fake_channel(send_return_id=99, channel_id=777)
    channel.fetch_message = AsyncMock(side_effect=RuntimeError("gone"))
    bot.get_channel = MagicMock(return_value=channel)

    await update_command_list(bot, repo, settings)  # must not raise

    channel.send.assert_awaited_once()
    assert await repo.get_channel_message(COMMAND_LIST_KEY) == (99, 777)
    await _cleanup(repo)


# ── noop paths persist nothing ───────────────────────────────────────────────

async def test_noop_when_channel_unset_persists_nothing():
    from n3x_bot.bot import build_bot, update_command_list
    repo = await _flatfile_repo()
    settings = _settings(command_list_channel_id=0)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock()

    await update_command_list(bot, repo, settings)

    bot.get_channel.assert_not_called()
    assert await repo.get_channel_message(COMMAND_LIST_KEY) is None
    await _cleanup(repo)


async def test_noop_when_channel_missing_persists_nothing():
    from n3x_bot.bot import build_bot, update_command_list
    repo = await _flatfile_repo()
    settings = _settings(command_list_channel_id=777)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)

    await update_command_list(bot, repo, settings)  # must not raise

    assert await repo.get_channel_message(COMMAND_LIST_KEY) is None
    await _cleanup(repo)


# ── on_ready wiring: best-effort posts the list when configured ──────────────

async def test_on_ready_posts_command_list_when_channel_configured():
    from n3x_bot.bot import build_bot
    repo = await _flatfile_repo()
    # every other self-editing channel off -> only the command-list path posts.
    settings = _settings(
        gate_stats_channel_id=0, gate_input_channel_id=0,
        gate_chart_channel_id=0, command_list_channel_id=777,
    )
    bot = build_bot(settings, repo)
    channel, _ = _fake_channel(send_return_id=7, channel_id=777)
    bot.get_channel = MagicMock(return_value=channel)
    bot.tree.sync = AsyncMock()

    await bot.on_ready()

    assert await repo.get_channel_message(COMMAND_LIST_KEY) == (7, 777)
    await _cleanup(repo)
