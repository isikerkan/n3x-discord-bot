"""🔄 reload control on the live gate-stats + base-timer overview embeds."""
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from n3x_bot.bot import build_bot, handle_reload_reaction
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


def _payload(*, message_id, emoji="🔄", user_id=7, channel_id=99):
    return SimpleNamespace(message_id=message_id, emoji=emoji, user_id=user_id,
                           channel_id=channel_id, guild_id=1, member=None)


def _reactable_msg():
    m = MagicMock()
    m.remove_reaction = AsyncMock()
    return m


async def test_gate_reload_reaction_refreshes_gate_embed(monkeypatch):
    repo = await _repo()
    bot = build_bot(_settings(), repo)
    bot._gate_embed_msg_id = 5001
    called = {}
    async def _fake_update(b, r, s):
        called["gate"] = True
    monkeypatch.setattr("n3x_bot.bot.update_gate_stats_embed", _fake_update)
    ch = MagicMock()
    ch.guild = None
    ch.fetch_message = AsyncMock(return_value=_reactable_msg())
    bot.get_channel = MagicMock(return_value=ch)

    await handle_reload_reaction(bot, repo, bot.n3x_settings,
                                 _payload(message_id=5001))
    assert called.get("gate") is True
    await repo.close()


async def test_timer_reload_reaction_refreshes_overview(monkeypatch):
    repo = await _repo()
    bot = build_bot(_settings(), repo)
    await repo.set_runtime_config("timer_overview_message_id", "6002")
    await bot.runtime_config.refresh(repo)
    called = {}
    async def _fake_timer(b, r, s, now):
        called["timer"] = True
    monkeypatch.setattr("n3x_bot.bot.update_timer_overview", _fake_timer)
    ch = MagicMock()
    ch.guild = None
    ch.fetch_message = AsyncMock(return_value=_reactable_msg())
    bot.get_channel = MagicMock(return_value=ch)

    await handle_reload_reaction(bot, repo, bot.n3x_settings,
                                 _payload(message_id=6002))
    assert called.get("timer") is True
    await repo.close()


async def test_reload_ignores_bots_own_reaction(monkeypatch):
    repo = await _repo()
    bot = build_bot(_settings(), repo)
    monkeypatch.setattr(type(bot), "user", SimpleNamespace(id=999))
    bot._gate_embed_msg_id = 5001
    called = {}
    monkeypatch.setattr("n3x_bot.bot.update_gate_stats_embed",
                        AsyncMock(side_effect=lambda *a: called.setdefault("x", 1)))
    await handle_reload_reaction(bot, repo, bot.n3x_settings,
                                 _payload(message_id=5001, user_id=999))
    assert "x" not in called
    await repo.close()


async def test_reload_ignores_non_reload_emoji(monkeypatch):
    repo = await _repo()
    bot = build_bot(_settings(), repo)
    bot._gate_embed_msg_id = 5001
    hit = {}
    monkeypatch.setattr("n3x_bot.bot.update_gate_stats_embed",
                        AsyncMock(side_effect=lambda *a: hit.setdefault("x", 1)))
    await handle_reload_reaction(bot, repo, bot.n3x_settings,
                                 _payload(message_id=5001, emoji="❌"))
    assert "x" not in hit
    await repo.close()


async def test_reload_ignores_untracked_message(monkeypatch):
    repo = await _repo()
    bot = build_bot(_settings(), repo)
    bot._gate_embed_msg_id = 5001
    hit = {}
    monkeypatch.setattr("n3x_bot.bot.update_gate_stats_embed",
                        AsyncMock(side_effect=lambda *a: hit.setdefault("x", 1)))
    await handle_reload_reaction(bot, repo, bot.n3x_settings,
                                 _payload(message_id=8888))
    assert "x" not in hit
    await repo.close()
