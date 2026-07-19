"""Voice join/leave/move announcements to the configured voice-log channel."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


from n3x_bot.activity import announce_voice_change


def _bot(*, log_channel_id=555, channel=None):
    bot = MagicMock()
    bot.runtime_config = SimpleNamespace(voice_log_channel_id=log_channel_id)
    bot.get_channel = MagicMock(return_value=channel)
    return bot


def _vc(cid, name):
    return SimpleNamespace(id=cid, name=name)


def _state(channel):
    return SimpleNamespace(channel=channel)


def _member(name="Erkan", is_bot=False, id=7):
    return SimpleNamespace(display_name=name, bot=is_bot, id=id)


async def _sent(bot):
    ch = bot.get_channel.return_value
    return ch.send.await_args.args[0] if ch.send.await_args else None


async def test_join_announced():
    ch = MagicMock()
    ch.send = AsyncMock()
    bot = _bot(channel=ch)
    await announce_voice_change(bot, _member(), _state(None), _state(_vc(1, "Lobby")))
    text = await _sent(bot)
    assert "Erkan" in text and "Lobby" in text and "beigetreten" in text


async def test_leave_announced():
    ch = MagicMock()
    ch.send = AsyncMock()
    bot = _bot(channel=ch)
    await announce_voice_change(bot, _member(), _state(_vc(1, "Lobby")), _state(None))
    text = await _sent(bot)
    assert "verlassen" in text and "Lobby" in text


async def test_move_announced():
    ch = MagicMock()
    ch.send = AsyncMock()
    bot = _bot(channel=ch)
    await announce_voice_change(bot, _member(),
                                _state(_vc(1, "Lobby")), _state(_vc(2, "Games")))
    text = await _sent(bot)
    assert "Lobby" in text and "Games" in text and "→" in text


async def test_mute_toggle_same_channel_not_announced():
    ch = MagicMock()
    ch.send = AsyncMock()
    bot = _bot(channel=ch)
    await announce_voice_change(bot, _member(),
                                _state(_vc(1, "Lobby")), _state(_vc(1, "Lobby")))
    ch.send.assert_not_awaited()


async def test_bot_member_not_announced():
    ch = MagicMock()
    ch.send = AsyncMock()
    bot = _bot(channel=ch)
    await announce_voice_change(bot, _member(is_bot=True),
                                _state(None), _state(_vc(1, "Lobby")))
    ch.send.assert_not_awaited()


async def test_no_log_channel_configured_noops():
    ch = MagicMock()
    ch.send = AsyncMock()
    bot = _bot(log_channel_id=0, channel=ch)
    await announce_voice_change(bot, _member(), _state(None), _state(_vc(1, "Lobby")))
    ch.send.assert_not_awaited()


def test_voice_log_is_a_config_channel_purpose():
    from n3x_bot.config_commands import CHANNEL_PURPOSES
    assert CHANNEL_PURPOSES["voice_log"] == "voice_log_channel_id"


def test_voice_log_is_overridable_and_resolves():
    from n3x_bot.runtime_config import RuntimeConfig, OVERRIDABLE_KEYS
    from n3x_bot.config import Settings
    assert "voice_log_channel_id" in OVERRIDABLE_KEYS
    s = Settings(discord_token="t", target_role_id=1, welcome_channel_id=2,
                 reminder_channel_id=3, julez_id=4, voice_log_channel_id=777,
                 _env_file=None, _env_prefix="NONEXISTENT_")
    rc = RuntimeConfig(s)
    assert rc.voice_log_channel_id == 777
    rc2 = RuntimeConfig(s, {"voice_log_channel_id": "888"})
    assert rc2.voice_log_channel_id == 888


async def test_announcement_suppresses_mentions_from_display_name():
    import discord
    ch = MagicMock()
    ch.send = AsyncMock()
    bot = _bot(channel=ch)
    # Malicious display name that would ping @everyone if mentions resolved.
    await announce_voice_change(bot, _member(name="@everyone"),
                                _state(None), _state(_vc(1, "Lobby")))
    am = ch.send.await_args.kwargs.get("allowed_mentions")
    assert am is not None
    # No mentions of any kind are allowed.
    none = discord.AllowedMentions.none()
    assert (am.everyone, am.users, am.roles) == (none.everyone, none.users, none.roles)


class _AuditLogs:
    """Callable returning an async iterator over fake audit entries."""
    def __init__(self, entries, raises=False):
        self._entries = entries
        self._raises = raises

    def __call__(self, *, limit=5, action=None):
        entries = self._entries

        async def _gen():
            if self._raises:
                raise RuntimeError("no audit access")
            for e in entries:
                yield e
        return _gen()


def _audit_entry(*, channel_id, actor_id, actor_name="Mod"):
    import discord
    return SimpleNamespace(
        created_at=discord.utils.utcnow(),
        extra=SimpleNamespace(channel=SimpleNamespace(id=channel_id)),
        user=SimpleNamespace(id=actor_id, display_name=actor_name))


def _guild(audit):
    g = SimpleNamespace()
    g.audit_logs = audit
    return g


async def test_force_move_records_the_mover():
    ch = MagicMock()
    ch.send = AsyncMock()
    bot = _bot(channel=ch)
    guild = _guild(_AuditLogs([_audit_entry(channel_id=2, actor_id=42, actor_name="Mod")]))
    member = SimpleNamespace(display_name="Erkan", bot=False, id=7, guild=guild)
    await announce_voice_change(bot, member, _state(_vc(1, "Lobby")), _state(_vc(2, "Games")))
    text = ch.send.await_args.args[0]
    # voice log prints plain names, never mentions
    assert "wurde von" in text and "Mod" in text and "verschoben" in text and "Games" in text
    assert "<@" not in text


async def test_self_move_not_attributed_when_no_audit_entry():
    ch = MagicMock()
    ch.send = AsyncMock()
    bot = _bot(channel=ch)
    guild = _guild(_AuditLogs([]))  # no member_move entries
    member = SimpleNamespace(display_name="Erkan", bot=False, id=7, guild=guild)
    await announce_voice_change(bot, member, _state(_vc(1, "Lobby")), _state(_vc(2, "Games")))
    text = ch.send.await_args.args[0]
    assert "→" in text and "wurde von" not in text


async def test_audit_failure_falls_back_to_plain_move():
    ch = MagicMock()
    ch.send = AsyncMock()
    bot = _bot(channel=ch)
    guild = _guild(_AuditLogs([], raises=True))
    member = SimpleNamespace(display_name="Erkan", bot=False, id=7, guild=guild)
    await announce_voice_change(bot, member, _state(_vc(1, "Lobby")), _state(_vc(2, "Games")))
    text = ch.send.await_args.args[0]
    assert "→" in text and "wurde von" not in text


async def test_move_by_self_actor_id_equals_member_not_attributed():
    ch = MagicMock()
    ch.send = AsyncMock()
    bot = _bot(channel=ch)
    # audit entry exists but actor == the member (self) -> not a force move
    guild = _guild(_AuditLogs([_audit_entry(channel_id=2, actor_id=7)]))
    member = SimpleNamespace(display_name="Erkan", bot=False, id=7, guild=guild)
    await announce_voice_change(bot, member, _state(_vc(1, "Lobby")), _state(_vc(2, "Games")))
    text = ch.send.await_args.args[0]
    assert "wurde von" not in text
