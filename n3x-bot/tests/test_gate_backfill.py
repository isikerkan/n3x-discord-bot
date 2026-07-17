"""Backfill: process gate-input messages the bot missed while offline."""
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from n3x_bot.bot import backfill_gate_input
from n3x_bot.config import Settings
from n3x_bot.seed import seed_defaults
from n3x_bot.storage.json_repo import JsonRepository

BASE = dict(discord_token="t", target_role_id=1, welcome_channel_id=2,
            reminder_channel_id=3, julez_id=4, _env_file=None,
            _env_prefix="NONEXISTENT_")


def _settings(**o):
    return Settings(**{**BASE, **o})


async def _repo():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    r = JsonRepository(path)
    await r.connect()
    await seed_defaults(r)
    return r


def _reaction(me):
    return SimpleNamespace(me=me)


def _msg(content, *, is_bot=False, reactions=()):
    return SimpleNamespace(content=content, id=abs(hash(content)) % 10**9,
                           author=SimpleNamespace(bot=is_bot),
                           reactions=list(reactions))


class _History:
    def __init__(self, msgs):
        self._msgs = msgs

    def __call__(self, limit=200):
        async def _gen():
            for m in self._msgs:
                yield m
        return _gen()


async def _bot_with_history(msgs):
    repo = await _repo()
    settings = _settings(gate_input_channel_id=42)
    from n3x_bot.bot import build_bot
    bot = build_bot(settings, repo)
    await repo.set_runtime_config("gate_input_channel_id", "42")
    await bot.runtime_config.refresh(repo)
    channel = MagicMock()
    channel.history = _History(msgs)
    bot.get_channel = MagicMock(return_value=channel)
    return bot, repo, settings


async def test_backfill_processes_unreacted_parseable_message(monkeypatch):
    import n3x_bot.bot as botmod
    handled = []

    async def _fake_handle(bot, repo, settings, message):
        handled.append(message.content)
    monkeypatch.setattr(botmod, "handle_gate_input_message", _fake_handle)

    bot, repo, settings = await _bot_with_history([_msg("a 46892")])
    n = await backfill_gate_input(bot, repo, settings)
    assert n == 1
    assert handled == ["a 46892"]
    await repo.close()


async def test_backfill_skips_already_reacted_message(monkeypatch):
    import n3x_bot.bot as botmod
    handled = []
    monkeypatch.setattr(botmod, "handle_gate_input_message",
                        AsyncMock(side_effect=lambda *a: handled.append(1)))
    bot, repo, settings = await _bot_with_history(
        [_msg("a 46892", reactions=[_reaction(True)])])  # bot already reacted
    n = await backfill_gate_input(bot, repo, settings)
    assert n == 0 and handled == []
    await repo.close()


async def test_backfill_skips_bot_authored_and_non_parseable(monkeypatch):
    import n3x_bot.bot as botmod
    handled = []

    async def _fake_handle(bot, repo, settings, message):
        handled.append(message.content)
    monkeypatch.setattr(botmod, "handle_gate_input_message", _fake_handle)
    bot, repo, settings = await _bot_with_history([
        _msg("a 46892", is_bot=True),      # bot-authored → skip
        _msg("hallo leute"),               # not a gate message → skip
        _msg("d 75000"),                   # parseable, unreacted → process
    ])
    n = await backfill_gate_input(bot, repo, settings)
    assert n == 1 and handled == ["d 75000"]
    await repo.close()


async def test_backfill_noop_when_channel_unset(monkeypatch):
    repo = await _repo()
    settings = _settings()
    from n3x_bot.bot import build_bot
    bot = build_bot(settings, repo)  # gate_input_channel_id defaults 0
    n = await backfill_gate_input(bot, repo, settings)
    assert n == 0
    await repo.close()
