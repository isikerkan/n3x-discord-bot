"""Event-reminder opt-in: /event reminder toggle + 🔔 signup reactions."""
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from datetime import datetime
from zoneinfo import ZoneInfo

from n3x_bot.events import (
    build_reminder_mentions, handle_event_signup_reaction, EVENT_EMOJI,
    EVENT_SIGNUP_KEY, EVENT_REMINDER_LAST_KEY, strip_mass_mentions,
    run_event_reminder)
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


# ── daily reminder: prune expired + strip @everyone ──────────────────────────

TZ = ZoneInfo("Europe/Berlin")


def test_strip_mass_mentions():
    assert strip_mass_mentions("Aceball @everyone heute @here!") == "Aceball  heute !"
    assert strip_mass_mentions("") == ""


class _Chan:
    def __init__(self):
        self.id = 555
        self.sent = []
        self._msgs = {}

    async def send(self, content, allowed_mentions=None):
        mid = 1000 + len(self.sent)
        m = SimpleNamespace(id=mid, content=content,
                            allowed_mentions=allowed_mentions, delete=AsyncMock())
        self.sent.append(m)
        self._msgs[mid] = m
        return m

    async def fetch_message(self, mid):
        if mid in self._msgs:
            return self._msgs[mid]
        raise RuntimeError("not found")


def _reminder_bot(chan, role_id=0, texts=None):
    return SimpleNamespace(
        get_channel=lambda cid: chan,
        runtime_config=SimpleNamespace(event_reminder_channel_id=555,
                                       event_role_id=role_id),
        content_texts=SimpleNamespace(get=lambda k: (texts or {}).get(k, "")))


async def test_reminder_posts_on_event_day_role_only_no_everyone():
    repo = await _repo()
    await repo.event_optin_set(7, True)
    chan = _Chan()
    bot = _reminder_bot(chan, role_id=777,
                        texts={"reminder_aceball": "Aceball @everyone!"})
    now = datetime(2026, 7, 22, 19, 30, tzinfo=TZ)   # Wednesday

    await run_event_reminder(bot, repo, None, now)

    assert len(chan.sent) == 1
    sent = chan.sent[0]
    assert "@everyone" not in sent.content            # stripped
    assert "<@&777>" in sent.content                  # pings the event role
    assert sent.allowed_mentions.everyone is False     # never mass-ping
    # tracked for later deletion
    assert (await repo.get_channel_message(EVENT_REMINDER_LAST_KEY))[0] == sent.id
    await repo.close()


async def test_reminder_deletes_previous_when_posting_new():
    repo = await _repo()
    chan = _Chan()
    prev = SimpleNamespace(id=900, created_at=datetime(2026, 7, 20, tzinfo=TZ),
                           delete=AsyncMock())
    chan._msgs[900] = prev
    await repo.set_channel_message(EVENT_REMINDER_LAST_KEY, 900, 555)
    bot = _reminder_bot(chan, texts={"reminder_invasion": "Invasion!"})
    now = datetime(2026, 7, 24, 19, 30, tzinfo=TZ)   # Friday -> posts

    await run_event_reminder(bot, repo, None, now)

    prev.delete.assert_awaited_once()                 # old one removed
    assert len(chan.sent) == 1
    await repo.close()


async def test_reminder_deletes_expired_on_non_event_day_without_posting():
    repo = await _repo()
    chan = _Chan()
    expired = SimpleNamespace(id=900, created_at=datetime(2026, 7, 22, tzinfo=TZ),
                              delete=AsyncMock())
    chan._msgs[900] = expired
    await repo.set_channel_message(EVENT_REMINDER_LAST_KEY, 900, 555)
    bot = _reminder_bot(chan)
    now = datetime(2026, 7, 23, 19, 30, tzinfo=TZ)   # Thursday, day after -> expired

    await run_event_reminder(bot, repo, None, now)

    expired.delete.assert_awaited_once()              # expired one deleted
    assert len(chan.sent) == 0                        # nothing posted (not event day)
    await repo.close()


async def test_reminder_keeps_current_day_message_on_non_event_day():
    repo = await _repo()
    chan = _Chan()
    same_day = SimpleNamespace(id=900, created_at=datetime(2026, 7, 23, tzinfo=TZ),
                               delete=AsyncMock())
    chan._msgs[900] = same_day
    await repo.set_channel_message(EVENT_REMINDER_LAST_KEY, 900, 555)
    bot = _reminder_bot(chan)
    now = datetime(2026, 7, 23, 23, 0, tzinfo=TZ)     # Thursday, same day -> not expired

    await run_event_reminder(bot, repo, None, now)

    same_day.delete.assert_not_awaited()              # not expired, kept
    await repo.close()
