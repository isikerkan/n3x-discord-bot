import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from discord.ext import commands

from n3x_bot.bot import (
    build_bot, register_stat_commands, _send_or_update, _send_rank,
    handle_gate_input_message, update_gate_stats_embed,
)
from n3x_bot.config import Settings
from n3x_bot.seed import seed_defaults
from n3x_bot.storage.json_repo import JsonRepository
from n3x_bot.__main__ import _prepare

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
    # Phase 4: `admin` is slash-only, so it is no longer a prefix command and is
    # deliberately NOT in this exclusion list — a lingering prefix `!admin` group
    # would inflate `wired` past `len(stats)+1` and fail this test.
    # discord.py's commands.Bot ships a default "help" command; "stat"/"del"
    # are wired by register_gate_commands (called from build_bot itself, not
    # register_stat_commands) — only count what THIS function wires.
    assert bot.get_command("admin") is None
    wired = [c for c in bot.commands if c.name not in ("help", "stat", "del", "gate", "config", "content", "activity", "erfolge", "overview", "sync_achievements", "kodex", "kodex_check", "sync_welcome", "base", "basestop")]
    assert len(wired) == len(stats) + 1

    await repo.close()


async def test_register_stat_commands_is_idempotent():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    await register_stat_commands(bot, repo, settings)
    await register_stat_commands(bot, repo, settings)

    stats = await repo.list_stats()
    # Phase 4: `admin` is slash-only — omitted from the exclusion list on purpose.
    assert bot.get_command("admin") is None
    wired = [c for c in bot.commands if c.name not in ("help", "stat", "del", "gate", "config", "content", "activity", "erfolge", "overview", "sync_achievements", "kodex", "kodex_check", "sync_welcome", "base", "basestop")]
    assert len(wired) == len(stats) + 1

    await repo.close()


async def test_build_bot_attaches_achievement_defs_baseline():
    # Phase 2a: build_bot attaches an AchievementDefs resolver defaulting to the
    # 83 code-default achievements (no new command is registered by this slice).
    from n3x_bot.achievement_defs import AchievementDefs
    repo = await _flatfile_repo()
    settings = _settings()

    bot = build_bot(settings, repo)

    assert isinstance(bot.achievement_defs, AchievementDefs)
    assert bot.achievement_defs.total == 83

    await repo.close()


async def test_build_bot_wires_gate_verlauf_group():
    # Phase 2: `gate` migrated to an app_commands.Group on the tree; it is no
    # longer a prefix command group. The `verlauf` subcommand hangs off the
    # app group, reached via group.get_command("verlauf").
    from discord import app_commands
    repo = await _flatfile_repo()
    settings = _settings()

    bot = build_bot(settings, repo)

    assert bot.get_command("gate") is None       # dropped from prefix registry
    group = bot.tree.get_command("gate")
    assert group is not None
    assert isinstance(group, app_commands.Group)
    assert group.get_command("verlauf") is not None

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


async def test_on_command_error_missing_arg_is_generic_for_gate_commands():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.command = SimpleNamespace(name="stat")
    error = commands.MissingRequiredArgument.__new__(commands.MissingRequiredArgument)

    await bot.on_command_error(ctx, error)

    ctx.send.assert_awaited_once()
    assert ctx.send.await_args.args[0] == "❌ Fehlendes Argument."

    await repo.close()


async def test_on_command_error_missing_arg_asks_for_user_on_targeted_commands():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.command = SimpleNamespace(name="smart")
    error = commands.MissingRequiredArgument.__new__(commands.MissingRequiredArgument)

    await bot.on_command_error(ctx, error)

    ctx.send.assert_awaited_once()
    assert ctx.send.await_args.args[0] == "❌ Bitte gib einen Nutzer an."

    await repo.close()


async def test_on_command_error_reports_bad_argument():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.command = SimpleNamespace(name="del")
    error = commands.BadArgument("not an int")

    await bot.on_command_error(ctx, error)

    ctx.send.assert_awaited_once()
    assert ctx.send.await_args.args[0] == "❌ Ungültiges Argument."

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


async def test_on_command_error_missing_arg_is_generic_for_admin_commands():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.command = SimpleNamespace(name="add")  # e.g. `!admin stat add`
    error = commands.MissingRequiredArgument.__new__(commands.MissingRequiredArgument)

    await bot.on_command_error(ctx, error)

    ctx.send.assert_awaited_once()
    assert ctx.send.await_args.args[0] == "❌ Fehlendes Argument."

    await repo.close()


async def test_on_command_error_missing_arg_is_generic_for_gate_verlauf():
    # `!gate verlauf` with no gate takes a gate token, never a user — a missing
    # arg must get the generic hint, not the "specify a user" message.
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.command = SimpleNamespace(name="verlauf")
    error = commands.MissingRequiredArgument.__new__(
        commands.MissingRequiredArgument)

    await bot.on_command_error(ctx, error)

    ctx.send.assert_awaited_once()
    assert ctx.send.await_args.args[0] == "❌ Fehlendes Argument."
    assert ctx.send.await_args.args[0] != "❌ Bitte gib einen Nutzer an."

    await repo.close()


# NOTE: the former `test_on_command_error_missing_arg_is_generic_for_config_
# subcommands` was removed in Phase 3: `config` is now a slash-only
# app_commands.Group and has no prefix subcommands, so a prefix MissingRequired
# Argument can never originate from it. Slash param handling is Discord-side.


async def test_on_command_error_surfaces_admin_helper_valueerror():
    # A ValueError raised by an admin helper is wrapped by discord.py in a
    # CommandInvokeError; the admin must see the reason, not silence.
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.command = SimpleNamespace(name="add")
    error = commands.CommandInvokeError(ValueError("stat 'tit' already exists"))

    await bot.on_command_error(ctx, error)

    ctx.send.assert_awaited_once()
    assert "already exists" in ctx.send.await_args.args[0]
    assert ctx.send.await_args.kwargs.get("delete_after") == 5

    await repo.close()


async def test_tree_on_error_surfaces_helper_error_to_interaction():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    interaction = MagicMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    error = SimpleNamespace(original=ValueError("no message named 'x'"))

    await bot.tree.on_error(interaction, error)

    interaction.response.send_message.assert_awaited_once()
    assert "no message named" in interaction.response.send_message.await_args.args[0]
    assert interaction.response.send_message.await_args.kwargs.get("ephemeral") is True

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


# ── on_member_join / on_member_remove (auto-registration) ────────────────

def _fake_member(*, member_id, display_name="Player", is_bot=False):
    """A member whose `.guild` is wired just enough that `enforce_prefix`
    (unconditionally invoked by the real `on_member_join`) takes its
    early-return path (bot's own `manage_nicknames` permission is False)
    instead of crashing on missing guild attributes.
    """
    guild_me = SimpleNamespace(
        guild_permissions=SimpleNamespace(manage_nicknames=False))
    guild = SimpleNamespace(owner=object(), me=guild_me)
    return SimpleNamespace(id=member_id, display_name=display_name, bot=is_bot,
                           mention=f"<@{member_id}>", guild=guild, roles=[],
                           top_role=0)


async def test_on_member_join_registers_non_bot_member():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)

    member = _fake_member(member_id=555, display_name="Newbie")

    await bot.on_member_join(member)

    user = await repo.get_user(555)
    assert user is not None
    assert user.display_name == "Newbie"

    await repo.close()


async def test_on_member_join_skips_bot_members():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)

    member = _fake_member(member_id=666, display_name="SomeBot", is_bot=True)

    await bot.on_member_join(member)

    assert await repo.get_user(666) is None

    await repo.close()


async def test_on_member_remove_archives_existing_user():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)

    member = _fake_member(member_id=777, display_name="Leaver")
    await bot.on_member_join(member)
    assert await repo.get_user(777) is not None

    await bot.on_member_remove(member)

    user = await repo.get_user(777)
    assert user is not None
    assert user.archived_at is not None
    assert 777 not in {u.discord_id for u in await repo.list_users()}

    await repo.close()


async def test_on_member_remove_never_registered_member_does_not_raise():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)

    member = _fake_member(member_id=888, display_name="Ghost")

    await bot.on_member_remove(member)  # must not raise

    assert await repo.get_user(888) is None

    await repo.close()


async def test_on_member_join_after_remove_unarchives_rejoining_member():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)

    member = _fake_member(member_id=999, display_name="Boomerang")
    await bot.on_member_join(member)
    await bot.on_member_remove(member)
    assert 999 not in {u.discord_id for u in await repo.list_users()}

    rejoined = _fake_member(member_id=999, display_name="Boomerang Back")
    await bot.on_member_join(rejoined)

    user = await repo.get_user(999)
    assert user is not None
    assert user.archived_at is None
    assert user.display_name == "Boomerang Back"
    assert 999 in {u.discord_id for u in await repo.list_users()}

    await repo.close()


# ── targeted stat commands (smart/crash/home) ────────────────────────────

async def test_targeted_stat_command_increments_target_and_invoker():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    await register_stat_commands(bot, repo, settings)
    channel, _ = _fake_channel()
    bot.get_channel = MagicMock(return_value=channel)

    cmd = bot.get_command("smart")
    ctx = MagicMock()
    ctx.author = SimpleNamespace(id=1, display_name="Invoker")
    member = SimpleNamespace(id=42, display_name="Target", mention="<@42>")

    await cmd.callback(ctx, member)

    # target's per-target counter increments, not the invoker's user_stats
    # under the target's id
    assert await repo.get_target_total(42, "smart") == 1
    # the invoker's own user_stats is still updated via record_use
    assert await repo.get_user_stats(1) == {"smart": 1}
    channel.send.assert_awaited_once()
    text = channel.send.await_args.args[0]
    assert "<@42>" in text

    await repo.close()


async def test_targeted_stat_command_counts_per_target_separately():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    await register_stat_commands(bot, repo, settings)
    channel, _ = _fake_channel()
    bot.get_channel = MagicMock(return_value=channel)

    cmd = bot.get_command("crash")
    ctx = MagicMock()
    ctx.author = SimpleNamespace(id=1, display_name="Invoker")
    member_a = SimpleNamespace(id=10, display_name="A", mention="<@10>")
    member_b = SimpleNamespace(id=20, display_name="B", mention="<@20>")

    await cmd.callback(ctx, member_a)
    await cmd.callback(ctx, member_a)
    await cmd.callback(ctx, member_b)

    assert await repo.get_target_total(10, "crash") == 2
    assert await repo.get_target_total(20, "crash") == 1
    assert await repo.get_user_stats(1) == {"crash": 3}

    await repo.close()


async def test_non_targeted_stats_are_unaffected_by_targeted_wiring():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    await register_stat_commands(bot, repo, settings)
    channel, _ = _fake_channel()
    bot.get_channel = MagicMock(return_value=channel)

    cmd = bot.get_command("tit")
    ctx = MagicMock()
    ctx.author = SimpleNamespace(id=1, display_name="Invoker")

    await cmd.callback(ctx)

    channel.send.assert_awaited_once()
    assert await repo.get_user_stats(1) == {"tit": 1}

    await repo.close()


async def test_home_command_targets_configured_julez_id_with_no_argument():
    repo = await _flatfile_repo()
    settings = _settings(julez_id=999)
    bot = build_bot(settings, repo)
    await register_stat_commands(bot, repo, settings)
    channel, _ = _fake_channel()
    bot.get_channel = MagicMock(return_value=channel)

    cmd = bot.get_command("home")
    assert cmd is not None
    ctx = MagicMock()
    ctx.author = SimpleNamespace(id=1, display_name="Invoker")

    await cmd.callback(ctx)

    assert await repo.get_target_total(999, "home") == 1
    assert await repo.get_user_stats(1) == {"home": 1}
    channel.send.assert_awaited_once()
    text = channel.send.await_args.args[0]
    assert "<@999>" in text

    await repo.close()


async def test_home_command_is_skipped_when_julez_id_unset():
    repo = await _flatfile_repo()
    settings = _settings(julez_id=0)
    bot = build_bot(settings, repo)

    await register_stat_commands(bot, repo, settings)

    assert bot.get_command("home") is None
    # other targeted stats still register normally
    assert bot.get_command("smart") is not None
    assert bot.get_command("crash") is not None

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


# ── gate tracker: update_gate_stats_embed ─────────────────────────────────

async def test_update_gate_stats_embed_noop_when_channel_unset():
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=0)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock()

    await update_gate_stats_embed(bot, repo, settings)

    bot.get_channel.assert_not_called()

    await repo.close()


async def test_update_gate_stats_embed_noop_when_channel_missing():
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=555)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)

    await update_gate_stats_embed(bot, repo, settings)  # must not raise

    await repo.close()


async def test_update_gate_stats_embed_first_post_sends_and_records_id():
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=555)
    bot = build_bot(settings, repo)
    channel, _ = _fake_channel(send_return_id=42, channel_id=555)
    bot.get_channel = MagicMock(return_value=channel)

    await update_gate_stats_embed(bot, repo, settings)

    channel.send.assert_awaited_once()
    embed = channel.send.await_args.kwargs["embed"]
    assert embed.title == "📊 Gate Statistik"
    assert bot._gate_embed_msg_id == 42

    await repo.close()


async def test_update_gate_stats_embed_second_call_edits_existing_message():
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=555)
    bot = build_bot(settings, repo)
    channel, old_message = _fake_channel(send_return_id=42, channel_id=555)
    old_message.edit = AsyncMock()
    bot.get_channel = MagicMock(return_value=channel)

    await update_gate_stats_embed(bot, repo, settings)
    await update_gate_stats_embed(bot, repo, settings)

    channel.fetch_message.assert_awaited_once_with(42)
    old_message.edit.assert_awaited_once()
    channel.send.assert_awaited_once()  # only the first call sent a new message

    await repo.close()


async def test_update_gate_stats_embed_falls_back_to_new_post_if_edit_fails():
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=555)
    bot = build_bot(settings, repo)
    channel, _ = _fake_channel(send_return_id=42, channel_id=555)
    bot.get_channel = MagicMock(return_value=channel)

    await update_gate_stats_embed(bot, repo, settings)

    channel.fetch_message = AsyncMock(side_effect=RuntimeError("gone"))
    channel.send = AsyncMock(return_value=SimpleNamespace(id=99))
    await update_gate_stats_embed(bot, repo, settings)

    assert bot._gate_embed_msg_id == 99

    await repo.close()


# ── gate tracker: handle_gate_input_message ────────────────────────────────

def _fake_gate_message(content: str, author_id: int = 1, author_name: str = "Erkan"):
    message = MagicMock()
    message.content = content
    message.author = SimpleNamespace(id=author_id, name=author_name)
    message.add_reaction = AsyncMock()
    return message


async def test_handle_gate_input_valid_entry_reacts_check_and_refreshes_embed():
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=555)
    bot = build_bot(settings, repo)
    channel, _ = _fake_channel(send_return_id=1, channel_id=555)
    bot.get_channel = MagicMock(return_value=channel)
    message = _fake_gate_message("a 46892")

    await handle_gate_input_message(bot, repo, settings, message)

    message.add_reaction.assert_awaited_once_with("✅")
    assert await repo.list_gate_costs("a") == [46892]
    channel.send.assert_awaited_once()  # embed refreshed

    await repo.close()


async def test_handle_gate_input_duplicate_within_window_reacts_hourglass():
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=555)
    bot = build_bot(settings, repo)
    channel, _ = _fake_channel(send_return_id=1, channel_id=555)
    bot.get_channel = MagicMock(return_value=channel)
    message = _fake_gate_message("a 46892")

    await handle_gate_input_message(bot, repo, settings, message)
    channel.send.reset_mock()
    dup_message = _fake_gate_message("a 46892")

    await handle_gate_input_message(bot, repo, settings, dup_message)

    dup_message.add_reaction.assert_awaited_once_with("⏳")
    channel.send.assert_not_called()  # rejected entries don't refresh the embed
    assert await repo.list_gate_costs("a") == [46892]

    await repo.close()


async def test_handle_gate_input_non_matching_non_command_text_reacts_cross():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    message = _fake_gate_message("just chatting")

    await handle_gate_input_message(bot, repo, settings, message)

    message.add_reaction.assert_awaited_once_with("❌")

    await repo.close()


async def test_handle_gate_input_command_prefixed_text_does_not_react():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    message = _fake_gate_message("!stat a")

    await handle_gate_input_message(bot, repo, settings, message)

    message.add_reaction.assert_not_called()

    await repo.close()


async def test_on_message_routes_gate_input_channel_to_gate_handler():
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    bot.process_commands = AsyncMock()
    bot.get_channel = MagicMock(return_value=None)  # no stats embed channel configured

    message = _fake_gate_message("a 100")
    message.channel = SimpleNamespace(id=777)

    await bot.on_message(message)

    message.add_reaction.assert_awaited_once_with("✅")
    assert await repo.list_gate_costs("a") == [100]
    bot.process_commands.assert_awaited_once_with(message)

    await repo.close()


async def test_on_message_ignores_other_channels_for_gate_parsing():
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    bot.process_commands = AsyncMock()

    message = _fake_gate_message("a 100")
    message.channel = SimpleNamespace(id=888)  # different channel

    await bot.on_message(message)

    message.add_reaction.assert_not_called()
    assert await repo.list_gate_costs("a") == []

    await repo.close()


# ── gate tracker: on_ready refreshes the embed once ────────────────────────

async def test_on_ready_refreshes_gate_embed_when_channel_configured():
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=555)
    bot = build_bot(settings, repo)
    channel, _ = _fake_channel(send_return_id=1, channel_id=555)
    bot.get_channel = MagicMock(return_value=channel)
    bot.tree.sync = AsyncMock()

    await bot.on_ready()

    channel.send.assert_awaited_once()

    await repo.close()


async def test_on_ready_skips_gate_embed_when_channel_unset():
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=0)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)
    bot.tree.sync = AsyncMock()

    await bot.on_ready()  # must not raise; no channel lookup for the gate embed

    await repo.close()


async def test_on_ready_syncs_command_tree(monkeypatch):
    # Publishing is GUILD-SCOPED ONLY now: on_ready no longer runs a standalone
    # global sync — it delegates to sync_commands_to_guilds, which per guild
    # copies the global tree and syncs, then empties the published global scope.
    # With one connected guild that means the tree IS synced (guild sync +
    # trailing global sync), so bot.tree.sync is awaited.
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=0)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)
    bot.tree.sync = AsyncMock()

    guild = MagicMock()
    guild.id = 11

    def fetch_members(limit=None):
        raise RuntimeError("no gateway")

    guild.fetch_members = fetch_members
    guild.members = []
    guild.voice_channels = []
    monkeypatch.setattr(type(bot), "guilds", property(lambda self: [guild]))

    await bot.on_ready()

    bot.tree.sync.assert_awaited()  # tree published via the guild-sync helper

    await repo.close()


# ── gate tracker: /stat and /del command ─────────────────────────────────────
#
# Phase 2 migrated `!stat` and `!del` to slash-ONLY app commands on `bot.tree`.
# Their presence/absence and behaviour specs now live in
# tests/test_slash_migration.py (Phase 2 section), invoked as app commands with
# the `gate` Choice parameter. The old prefix `bot.get_command("stat")` /
# `bot.get_command("del")` callbacks no longer exist, so the previous prefix
# tests were removed here rather than left to AttributeError on `None.callback`.
