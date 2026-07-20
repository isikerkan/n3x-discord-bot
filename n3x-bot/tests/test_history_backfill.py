"""`/backfill_history`: scan message/reaction history -> counters -> recompute."""
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from n3x_bot.history_backfill import (
    scan_history, apply_history_counts, run_history_backfill)
from n3x_bot.storage.json_repo import JsonRepository
from n3x_bot.seed import seed_defaults


async def _repo():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    r = JsonRepository(path)
    await r.connect()
    await seed_defaults(r)
    return r


# ── fakes: a guild whose channels yield messages with reactions ──────────────

def _user(uid, bot=False):
    return SimpleNamespace(id=uid, bot=bot)


class _Reaction:
    def __init__(self, reactors):
        self._reactors = reactors

    def users(self):
        async def _gen():
            for u in self._reactors:
                yield u
        return _gen()


def _msg(author, reactions=()):
    return SimpleNamespace(author=author, reactions=list(reactions))


class _Channel:
    def __init__(self, messages, raises=False):
        self._messages = messages
        self._raises = raises

    def history(self, limit=None):
        async def _gen():
            if self._raises:
                raise RuntimeError("no access")
            for m in self._messages:
                yield m
        return _gen()


def _guild(*channels):
    return SimpleNamespace(text_channels=list(channels))


# ── scan_history ─────────────────────────────────────────────────────────────

async def test_scan_counts_messages_per_non_bot_author():
    u1, u2, botu = _user(1), _user(2), _user(9, bot=True)
    guild = _guild(_Channel([_msg(u1), _msg(u1), _msg(u2), _msg(botu)]))
    messages, reactions = await scan_history(guild)
    assert messages == {1: 2, 2: 1}   # bot author excluded
    assert reactions == {}


async def test_scan_counts_reactions_given_excluding_bots():
    u1, u2, botu = _user(1), _user(2), _user(9, bot=True)
    # one message, two emojis: u1 reacted to both, u2 to one, bot ignored
    m = _msg(u1, reactions=[_Reaction([u1, u2, botu]), _Reaction([u1])])
    messages, reactions = await scan_history(_guild(_Channel([m])))
    assert reactions == {1: 2, 2: 1}   # u1 gave 2, u2 gave 1


async def test_scan_skips_channel_that_raises():
    good = _Channel([_msg(_user(1))])
    bad = _Channel([], raises=True)
    messages, _ = await scan_history(_guild(bad, good))
    assert messages == {1: 1}          # bad channel skipped, good still counted


# ── apply + full run ─────────────────────────────────────────────────────────

async def test_apply_sets_absolute_counters_idempotently():
    repo = await _repo()
    await repo.add_activity(1, "messages", 999)   # stale live value
    await apply_history_counts(repo, {1: 40}, {1: 5})
    assert await repo.get_activity(1, "messages") == 40   # SET, not added
    assert await repo.get_activity(1, "reactions") == 5
    # re-run is idempotent
    await apply_history_counts(repo, {1: 40}, {1: 5})
    assert await repo.get_activity(1, "messages") == 40
    await repo.close()


async def test_run_backfill_unlocks_message_achievement():
    repo = await _repo()
    bot = SimpleNamespace(achievement_defs=SimpleNamespace(all=lambda: None))
    author = _user(7)
    guild = _guild(_Channel([_msg(author) for _ in range(1000)]))  # 1000 messages

    summary = await run_history_backfill(bot, repo, guild)

    assert summary["total_messages"] == 1000
    assert await repo.get_activity(7, "messages") == 1000
    # msg_1000 (Tastatur-Krieger, secret) now unlocked by recompute
    assert await repo.has_achievement(7, "msg_1000")
    assert summary["achievements_added"] >= 1
    await repo.close()


# ── command wiring ───────────────────────────────────────────────────────────

def _settings():
    from n3x_bot.config import Settings
    return Settings(discord_token="t", target_role_id=1, welcome_channel_id=2,
                    reminder_channel_id=3, julez_id=4, admin_role_id=42,
                    _env_file=None, _env_prefix="NONEXISTENT_")


async def test_command_registered_and_admin_gated():
    from n3x_bot.bot import build_bot
    repo = await _repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    cmd = bot.tree.get_command("backfill_history")
    assert cmd is not None

    # non-admin -> denied, no scan
    interaction = MagicMock()
    interaction.user = SimpleNamespace(id=1, roles=[])
    interaction.response = MagicMock(send_message=AsyncMock(), defer=AsyncMock())
    await cmd.callback(interaction)
    interaction.response.send_message.assert_awaited_once()
    assert "Berechtigung" in interaction.response.send_message.await_args.args[0]
    interaction.response.defer.assert_not_awaited()
    await repo.close()
