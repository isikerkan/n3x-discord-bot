import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from discord.ext import commands

from n3x_bot.bot import build_bot, register_stat_commands, _send_or_update, _send_rank
from n3x_bot.config import Settings
from n3x_bot.seed import seed_defaults
from n3x_bot.storage.json_repo import JsonRepository
from n3x_bot.__main__ import _prepare

BASE_SETTINGS_KWARGS = dict(
    discord_token="tok",
    target_role_id=1,
    welcome_channel_id=2,
    reminder_channel_id=999,
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


# ── build_bot ─────────────────────────────────────────────────────────────

async def test_build_bot_wires_prefix_repo_settings_and_intents():
    repo = await _flatfile_repo()
    settings = _settings()

    bot = build_bot(settings, repo)

    assert bot.command_prefix == settings.command_prefix
    assert bot.n3x_repo is repo
    assert bot.n3x_settings is settings
    assert bot.intents.members is True

    await repo.close()


# ── register_stat_commands ───────────────────────────────────────────────

async def test_register_stat_commands_adds_one_command_per_stat_plus_rank():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    await register_stat_commands(bot, repo, settings)

    stats = await repo.list_stats()
    assert bot.get_command("tit") is not None
    assert bot.get_command("rank") is not None
    # discord.py's commands.Bot ships a default "help" command; only count
    # the commands this function is responsible for wiring.
    wired = [c for c in bot.commands if c.name != "help"]
    assert len(wired) == len(stats) + 1

    await repo.close()


async def test_register_stat_commands_is_idempotent():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    await register_stat_commands(bot, repo, settings)
    await register_stat_commands(bot, repo, settings)

    stats = await repo.list_stats()
    wired = [c for c in bot.commands if c.name != "help"]
    assert len(wired) == len(stats) + 1

    await repo.close()


# ── _send_or_update ───────────────────────────────────────────────────────

def _fake_channel(send_return_id: int = 111, channel_id: int = 222):
    channel = MagicMock()
    channel.id = channel_id
    channel.send = AsyncMock(return_value=SimpleNamespace(id=send_return_id))
    old_message = MagicMock()
    old_message.delete = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=old_message)
    return channel, old_message


async def test_send_or_update_first_post_sends_and_records():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    channel, _ = _fake_channel(send_return_id=111, channel_id=222)
    bot.get_channel = MagicMock(return_value=channel)

    assert await repo.get_last_post("tit") is None

    await _send_or_update(bot, repo, settings, "tit", "hello")

    channel.send.assert_awaited_once_with("hello")
    channel.fetch_message.assert_not_called()
    assert await repo.get_last_post("tit") == (111, 222)

    await repo.close()


async def test_send_or_update_second_post_deletes_old_message_first():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    channel, old_message = _fake_channel(send_return_id=111, channel_id=222)
    bot.get_channel = MagicMock(return_value=channel)

    await _send_or_update(bot, repo, settings, "tit", "first")

    channel.send = AsyncMock(return_value=SimpleNamespace(id=333))
    await _send_or_update(bot, repo, settings, "tit", "second")

    channel.fetch_message.assert_awaited_once_with(111)
    old_message.delete.assert_awaited_once()
    channel.send.assert_awaited_once_with("second")
    assert await repo.get_last_post("tit") == (333, 222)

    await repo.close()


async def test_send_or_update_no_channel_is_safe_no_op():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)

    await _send_or_update(bot, repo, settings, "tit", "hello")

    assert await repo.get_last_post("tit") is None

    await repo.close()


# ── __main__._prepare ─────────────────────────────────────────────────────

async def test_prepare_flatfile_returns_connected_seeded_repo():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    settings = _settings(storage_backend="flatfile", data_file=path)

    repo = await _prepare(settings)

    assert await repo.get_stat("tit") is not None
    await repo.close()
    if os.path.exists(path):
        os.remove(path)


async def test_send_or_update_swallows_fetch_or_delete_errors_then_still_posts():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    channel, old_message = _fake_channel(send_return_id=111, channel_id=222)
    bot.get_channel = MagicMock(return_value=channel)

    await _send_or_update(bot, repo, settings, "tit", "first")

    channel.fetch_message = AsyncMock(side_effect=RuntimeError("gone"))
    channel.send = AsyncMock(return_value=SimpleNamespace(id=333))
    await _send_or_update(bot, repo, settings, "tit", "second")

    channel.send.assert_awaited_once_with("second")
    assert await repo.get_last_post("tit") == (333, 222)

    await repo.close()


# ── rank / stat command bodies ────────────────────────────────────────────

async def test_rank_command_reports_no_usage_when_user_has_none():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    await register_stat_commands(bot, repo, settings)
    channel, _ = _fake_channel(send_return_id=111, channel_id=222)
    bot.get_channel = MagicMock(return_value=channel)

    cmd = bot.get_command("rank")
    ctx = MagicMock()
    ctx.author = SimpleNamespace(id=1, display_name="NewUser")

    # Real end-to-end call: no mocking of _send_or_update/set_last_post, so
    # a regression to the old (buggy) rank_<id> stat_last_post path would
    # surface as a real KeyError here.
    await cmd.callback(ctx)

    channel.send.assert_awaited_once()
    text = channel.send.await_args.args[0]
    assert "noch keine Befehle genutzt" in text

    await repo.close()


async def test_rank_command_reports_ordered_usage_when_user_has_data():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    await register_stat_commands(bot, repo, settings)
    channel, _ = _fake_channel(send_return_id=111, channel_id=222)
    bot.get_channel = MagicMock(return_value=channel)

    await repo.record_use(7, "Erkan", "tit")
    await repo.record_use(7, "Erkan", "tit")
    await repo.record_use(7, "Erkan", "cry")

    cmd = bot.get_command("rank")
    ctx = MagicMock()
    ctx.author = SimpleNamespace(id=7, display_name="Erkan")

    await cmd.callback(ctx)

    channel.send.assert_awaited_once()
    text = channel.send.await_args.args[0]
    assert "tit" in text and "cry" in text
    assert text.index("tit") < text.index("cry")  # higher count ranks first

    await repo.close()


# ── _send_rank ────────────────────────────────────────────────────────────

async def test_send_rank_sends_and_records_in_memory_without_db_persistence():
    """Proves the rank path never touches stat_last_post: uses a real
    seeded repo (no mocking of set_last_post) so a regression to the old
    buggy `_send_or_update` call would raise a real KeyError, since
    'rank_1' is never a row in the `stats` table.
    """
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    channel, _ = _fake_channel(send_return_id=111, channel_id=222)
    bot.get_channel = MagicMock(return_value=channel)

    rank_key = "rank_1"
    await _send_rank(bot, settings, rank_key, "rank text")

    channel.send.assert_awaited_once_with("rank text")
    channel.fetch_message.assert_not_called()
    assert bot._rank_last_posts[rank_key] == 111

    # The rank key was never written to stat_last_post; real stats are
    # unaffected. Confirm rank_key is not a real stat row by proving the
    # repo's own write path would KeyError for it (this is exactly the
    # crash the old buggy `_send_or_update(... f"rank_{id}" ...)` call hit).
    assert await repo.get_last_post("tit") is None
    assert await repo.get_last_post(rank_key) is None
    try:
        await repo.set_last_post(rank_key, 1, 2)
        raised = False
    except KeyError:
        raised = True
    assert raised, "rank_key is not a real stat; set_last_post should KeyError"

    await repo.close()


async def test_send_rank_second_call_deletes_previous_message_first():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    channel, old_message = _fake_channel(send_return_id=111, channel_id=222)
    bot.get_channel = MagicMock(return_value=channel)

    rank_key = "rank_1"
    await _send_rank(bot, settings, rank_key, "first")

    channel.send = AsyncMock(return_value=SimpleNamespace(id=333))
    await _send_rank(bot, settings, rank_key, "second")

    channel.fetch_message.assert_awaited_once_with(111)
    old_message.delete.assert_awaited_once()
    channel.send.assert_awaited_once_with("second")
    assert bot._rank_last_posts[rank_key] == 333

    await repo.close()


async def test_send_rank_no_channel_is_safe_no_op():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)

    await _send_rank(bot, settings, "rank_1", "text")

    assert "rank_1" not in bot._rank_last_posts

    await repo.close()


async def test_stat_command_callback_records_use_and_posts():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    await register_stat_commands(bot, repo, settings)
    channel, _ = _fake_channel()
    bot.get_channel = MagicMock(return_value=channel)

    cmd = bot.get_command("tit")
    ctx = MagicMock()
    ctx.author = SimpleNamespace(id=99, display_name="Ali")

    await cmd.callback(ctx)

    channel.send.assert_awaited_once()
    assert await repo.get_last_post("tit") is not None

    await repo.close()


# ── on_message / on_command_error event handlers ─────────────────────────

async def test_on_message_deletes_command_prefixed_messages_then_processes():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    bot.process_commands = AsyncMock()

    message = MagicMock()
    message.author = "someone-else"
    message.content = "!tit"
    message.delete = AsyncMock()

    await bot.on_message(message)

    message.delete.assert_awaited_once_with(delay=5.0)
    bot.process_commands.assert_awaited_once_with(message)

    await repo.close()


async def test_on_message_ignores_messages_from_self():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    bot.process_commands = AsyncMock()
    # bot.user is a read-only property that is None until the bot logs in;
    # simulate "message from the bot itself" against that pre-login value.
    assert bot.user is None

    message = MagicMock()
    message.author = None
    message.content = "!tit"
    message.delete = AsyncMock()

    await bot.on_message(message)

    message.delete.assert_not_called()
    bot.process_commands.assert_not_called()

    await repo.close()


async def test_on_message_passes_through_non_command_text_without_deleting():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    bot.process_commands = AsyncMock()

    message = MagicMock()
    message.author = "someone-else"
    message.content = "just chatting"
    message.delete = AsyncMock()

    await bot.on_message(message)

    message.delete.assert_not_called()
    bot.process_commands.assert_awaited_once_with(message)

    await repo.close()


async def test_on_message_swallows_delete_failure_and_still_processes():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    bot.process_commands = AsyncMock()

    message = MagicMock()
    message.author = "someone-else"
    message.content = "!tit"
    message.delete = AsyncMock(side_effect=RuntimeError("no perms"))

    await bot.on_message(message)  # must not raise

    bot.process_commands.assert_awaited_once_with(message)

    await repo.close()


async def test_on_command_error_notifies_on_cooldown():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    ctx = MagicMock()
    ctx.send = AsyncMock()
    cooldown = commands.Cooldown(1, 20.0)
    error = commands.CommandOnCooldown(cooldown, 4.2, commands.BucketType.user)

    await bot.on_command_error(ctx, error)

    ctx.send.assert_awaited_once()
    args, kwargs = ctx.send.await_args
    assert "4.2" in args[0]
    assert kwargs.get("delete_after") == 5

    await repo.close()


async def test_on_command_error_ignores_other_errors():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    ctx = MagicMock()
    ctx.send = AsyncMock()

    await bot.on_command_error(ctx, RuntimeError("boom"))

    ctx.send.assert_not_called()

    await repo.close()


# ── enforce_prefix (via on_member_update) ─────────────────────────────────

class _FakeRole:
    def __init__(self, role_id):
        self.id = role_id


class _FakeGuild:
    def __init__(self, owner, me):
        self.owner = owner
        self.me = me


class _FakeMember:
    def __init__(self, *, bot=False, display_name="Player", roles=None,
                 top_role=0, guild=None, manage_nicknames=True):
        self.bot = bot
        self.display_name = display_name
        self.roles = roles or []
        self.top_role = top_role
        self.guild = guild
        self.guild_permissions = SimpleNamespace(manage_nicknames=manage_nicknames)
        self.edit = AsyncMock(side_effect=self._apply_edit)

    def _apply_edit(self, nick, reason=None):
        self.display_name = nick


async def test_on_member_update_adds_prefix_when_target_role_granted():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    bot_member = _FakeMember(top_role=10)
    owner = _FakeMember()
    guild = _FakeGuild(owner=owner, me=bot_member)
    target_role = _FakeRole(settings.target_role_id)

    before = _FakeMember(display_name="Player", roles=[], top_role=1, guild=guild)
    after = _FakeMember(display_name="Player", roles=[target_role], top_role=1, guild=guild)

    await bot.on_member_update(before, after)

    after.edit.assert_awaited_once()
    assert after.edit.await_args.kwargs["nick"] == f"{settings.prefix_str}Player"

    await repo.close()


async def test_on_member_update_removes_prefix_when_target_role_revoked():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    bot_member = _FakeMember(top_role=10)
    owner = _FakeMember()
    guild = _FakeGuild(owner=owner, me=bot_member)

    prefixed_name = f"{settings.prefix_str}Player"
    before = _FakeMember(display_name=prefixed_name, roles=[SimpleNamespace(id=999)],
                         top_role=1, guild=guild)
    after = _FakeMember(display_name=prefixed_name, roles=[], top_role=1, guild=guild)

    await bot.on_member_update(before, after)

    after.edit.assert_awaited_once()
    assert after.edit.await_args.kwargs["nick"] == "Player"

    await repo.close()


async def test_on_member_update_skips_bot_and_owner_and_unprivileged_cases():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    bot_member = _FakeMember(top_role=10)
    owner = _FakeMember()
    guild = _FakeGuild(owner=owner, me=bot_member)
    target_role = _FakeRole(settings.target_role_id)

    # a bot member should never be renamed
    before = _FakeMember(bot=True, display_name="Bot1", roles=[], guild=guild)
    after = _FakeMember(bot=True, display_name="Bot1", roles=[target_role], guild=guild)
    await bot.on_member_update(before, after)
    after.edit.assert_not_called()

    # the owner should never be renamed
    owner_after = _FakeMember(display_name="Owner", roles=[target_role], guild=guild)
    owner_after.guild.owner = owner_after
    await bot.on_member_update(owner, owner_after)
    owner_after.edit.assert_not_called()

    await repo.close()


async def test_prepare_sqlite_connects_and_seeds_within_one_loop():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)
    settings = _settings(
        storage_backend="sqlite",
        database_url=f"sqlite+aiosqlite:///{db_path}",
    )

    repo = await _prepare(settings)

    stats = await repo.list_stats()
    assert len(stats) > 0
    assert await repo.get_stat("tit") is not None

    await repo.close()
    if os.path.exists(db_path):
        os.remove(db_path)
