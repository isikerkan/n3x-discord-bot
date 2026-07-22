"""Event-reminder opt-in: /event reminder toggle + 🔔 signup reactions."""
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from n3x_bot.events import (
    build_reminder_mentions, handle_event_signup_reaction, EVENT_EMOJI,
    EVENT_SIGNUP_KEY)
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


def _settings(**over):
    from n3x_bot.config import Settings
    kw = dict(discord_token="t", target_role_id=1, welcome_channel_id=2,
              reminder_channel_id=3, julez_id=4, admin_role_id=42,
              _env_file=None, _env_prefix="NONEXISTENT_")
    kw.update(over)
    return Settings(**kw)


# ── config ───────────────────────────────────────────────────────────────────

def test_event_reminder_channel_falls_back_to_reminder_channel():
    from n3x_bot.runtime_config import RuntimeConfig
    s = _settings(reminder_channel_id=111, event_reminder_channel_id=0)
    assert RuntimeConfig(s).event_reminder_channel_id == 111       # fallback
    s2 = _settings(reminder_channel_id=111, event_reminder_channel_id=222)
    assert RuntimeConfig(s2).event_reminder_channel_id == 222       # explicit wins


def test_event_reminder_channel_is_a_config_purpose():
    from n3x_bot.config_commands import CHANNEL_PURPOSES
    assert CHANNEL_PURPOSES["event_reminder"] == "event_reminder_channel_id"


# ── opt-in storage + mentions ────────────────────────────────────────────────

async def test_optin_roundtrip():
    repo = await _repo()
    assert await repo.event_optin_is(7) is False
    await repo.event_optin_set(7, True)
    assert await repo.event_optin_is(7) is True
    assert await repo.event_optin_all() == [7]
    await repo.event_optin_set(7, False)
    assert await repo.event_optin_is(7) is False
    await repo.close()


def test_build_reminder_mentions():
    assert build_reminder_mentions([]) == ""
    out = build_reminder_mentions([1, 2])
    assert "<@1>" in out and "<@2>" in out and EVENT_EMOJI in out


def test_build_reminder_mentions_uses_role_when_configured():
    # A configured event role is mentioned (covers everyone with it), not the
    # individual user list.
    out = build_reminder_mentions([1, 2], event_role_id=555)
    assert "<@&555>" in out
    assert "<@1>" not in out
    # even with nobody in the opt-in list, the role is still pinged
    assert "<@&555>" in build_reminder_mentions([], event_role_id=555)


# ── /event reminder toggle ───────────────────────────────────────────────────

async def test_event_reminder_toggles_optin():
    from n3x_bot.bot import build_bot
    repo = await _repo()
    bot = build_bot(_settings(), repo)
    cmd = bot.tree.get_command("event").get_command("reminder")

    def _it():
        it = MagicMock()
        it.user = SimpleNamespace(id=7)
        it.response = MagicMock(send_message=AsyncMock())
        return it

    it1 = _it()
    await cmd.callback(it1)                       # opt IN
    assert await repo.event_optin_is(7) is True
    assert "jetzt" in it1.response.send_message.await_args.args[0]

    it2 = _it()
    await cmd.callback(it2)                       # opt OUT
    assert await repo.event_optin_is(7) is False
    assert "keine" in it2.response.send_message.await_args.args[0].lower()
    await repo.close()


# ── 🔔 signup reactions ───────────────────────────────────────────────────────

def _fake_bot(user_id=1, event_role_id=0):
    return SimpleNamespace(user=SimpleNamespace(id=user_id),
                           runtime_config=SimpleNamespace(event_role_id=event_role_id))


async def test_signup_reaction_opts_in_and_out():
    repo = await _repo()
    await repo.set_channel_message(EVENT_SIGNUP_KEY, 555, 999)  # the signup msg
    bot = _fake_bot()

    p_in = SimpleNamespace(message_id=555, user_id=7, emoji=EVENT_EMOJI)
    await handle_event_signup_reaction(bot, repo, p_in, added=True)
    assert await repo.event_optin_is(7) is True

    p_out = SimpleNamespace(message_id=555, user_id=7, emoji=EVENT_EMOJI)
    await handle_event_signup_reaction(bot, repo, p_out, added=False)
    assert await repo.event_optin_is(7) is False
    await repo.close()


async def test_signup_reaction_grants_and_removes_role():
    repo = await _repo()
    await repo.set_channel_message(EVENT_SIGNUP_KEY, 555, 999)
    role = SimpleNamespace(id=777)
    added_roles, removed_roles = [], []
    member = SimpleNamespace(
        guild=SimpleNamespace(get_role=lambda rid: role if rid == 777 else None),
        add_roles=AsyncMock(side_effect=lambda r, reason=None: added_roles.append(r.id)),
        remove_roles=AsyncMock(side_effect=lambda r, reason=None: removed_roles.append(r.id)))
    bot = _fake_bot(event_role_id=777)

    p_in = SimpleNamespace(message_id=555, user_id=7, emoji=EVENT_EMOJI, member=member)
    await handle_event_signup_reaction(bot, repo, p_in, added=True)
    assert added_roles == [777]

    # remove path: no payload.member -> resolve via guild.get_member
    guild = SimpleNamespace(get_member=lambda uid: member,
                            get_role=member.guild.get_role)
    bot.get_guild = lambda gid: guild
    p_out = SimpleNamespace(message_id=555, user_id=7, emoji=EVENT_EMOJI,
                            guild_id=1, member=None)
    await handle_event_signup_reaction(bot, repo, p_out, added=False)
    assert removed_roles == [777]
    await repo.close()


async def test_signup_reaction_ignores_other_message_and_bot():
    repo = await _repo()
    await repo.set_channel_message(EVENT_SIGNUP_KEY, 555, 999)
    bot = _fake_bot()

    # wrong message
    await handle_event_signup_reaction(
        bot, repo, SimpleNamespace(message_id=999, user_id=7, emoji=EVENT_EMOJI),
        added=True)
    assert await repo.event_optin_is(7) is False
    # bot's own reaction
    await handle_event_signup_reaction(
        bot, repo, SimpleNamespace(message_id=555, user_id=1, emoji=EVENT_EMOJI),
        added=True)
    assert await repo.event_optin_is(1) is False
    # wrong emoji
    await handle_event_signup_reaction(
        bot, repo, SimpleNamespace(message_id=555, user_id=7, emoji="❌"),
        added=True)
    assert await repo.event_optin_is(7) is False
    await repo.close()
