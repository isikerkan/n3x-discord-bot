"""RED-phase specs for the role-gated admin CRUD feature.

These tests are written BEFORE the implementation exists. They target:

  * new config field ``Settings.admin_role_id``
  * a set of unit-testable async core helpers on ``n3x_bot.bot`` that both the
    prefix ``!admin ...`` commands and the ``/admin ...`` slash commands
    delegate to (mirroring how ``build_output`` / ``_send_*`` are testable),
  * a permission helper ``is_admin`` mirroring the ``gate_delete_role_id``
    check pattern,
  * live (re)registration of dynamic stat commands on create / archive / rm,
  * registration/structure of both command surfaces after ``build_bot``.

New symbols are referenced via the module object (``botmod.<name>``) rather
than imported at module scope, so a not-yet-implemented symbol fails the
individual test with ``AttributeError`` (a correct pre-impl RED) instead of
breaking collection with an ImportError.
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from discord.ext import commands

import n3x_bot.bot as botmod
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
    # NB: pre-impl, `admin_role_id` is an unknown field; Settings has
    # extra="ignore", so passing it here is silently dropped (no construction
    # error) until the field exists. That keeps these tests failing on the
    # feature under test, never on Settings construction.
    return Settings(**kwargs)


async def _flatfile_repo() -> JsonRepository:
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    repo = JsonRepository(path)
    await repo.connect()
    await seed_defaults(repo)
    return repo


def _member(*, member_id=1, role_ids=(), is_bot=False):
    """A fake guild member: SimpleNamespace with `.id`, `.roles`, `.bot`."""
    roles = [SimpleNamespace(id=r) for r in role_ids]
    return SimpleNamespace(id=member_id, roles=roles, bot=is_bot)


# ── permission gating: is_admin ───────────────────────────────────────────

async def test_is_admin_true_for_member_holding_admin_role():
    settings = _settings(admin_role_id=42)
    member = _member(member_id=5, role_ids=(7, 42))

    assert botmod.is_admin(member, settings) is True


async def test_is_admin_false_for_member_without_admin_role():
    settings = _settings(admin_role_id=42)
    member = _member(member_id=5, role_ids=(7, 8))

    assert botmod.is_admin(member, settings) is False


async def test_is_admin_false_when_admin_role_unset_even_if_member_has_role_zero():
    # admin_role_id defaults to 0 (feature disabled). A member whose role id
    # happens to be 0 must NOT be treated as an admin.
    settings = _settings(admin_role_id=0)
    member = _member(member_id=5, role_ids=(0,))

    assert botmod.is_admin(member, settings) is False


async def test_admin_stat_add_command_refuses_non_admin_and_mutates_nothing():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    group = bot.get_command("admin")
    assert group is not None, "prefix `admin` group must be registered"
    stat_group = group.get_command("stat")
    assert stat_group is not None, "`admin stat` subgroup must be registered"
    add_cmd = stat_group.get_command("add")
    assert add_cmd is not None, "`admin stat add` subcommand must be registered"

    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.author = _member(member_id=5, role_ids=(999,))  # NOT the admin role

    before = {s.key for s in await repo.list_stats()}
    await add_cmd.callback(ctx, "newkey", "New Stat")
    after = {s.key for s in await repo.list_stats()}

    assert after == before  # non-admin caused no DB mutation
    assert bot.get_command("newkey") is None  # and no live command registered
    ctx.send.assert_awaited()  # a refusal was sent

    await repo.close()


# ── admin stat helpers ────────────────────────────────────────────────────

async def test_admin_create_stat_adds_row_and_registers_live_command():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    stat = await botmod.admin_create_stat(bot, repo, settings, "boop", "Boop")

    assert stat.key == "boop"
    assert await repo.get_stat("boop") is not None
    assert bot.get_command("boop") is not None  # live command, no restart

    await repo.close()


async def test_admin_create_targeted_stat_command_takes_member_argument():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    await botmod.admin_create_stat(bot, repo, settings, "poke", "Poke",
                                   targeted=True)

    cmd = bot.get_command("poke")
    assert cmd is not None
    assert "member" in cmd.clean_params  # targeted -> takes a member arg

    await repo.close()


async def test_admin_create_non_targeted_stat_command_has_no_member_argument():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    await botmod.admin_create_stat(bot, repo, settings, "boop", "Boop")

    cmd = bot.get_command("boop")
    assert cmd is not None
    assert "member" not in cmd.clean_params

    await repo.close()


async def test_admin_create_stat_links_message_by_name():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    msg = await botmod.admin_create_message(repo, "boop_msg", "boop {count}")
    stat = await botmod.admin_create_stat(bot, repo, settings, "boop", "Boop",
                                          message_name="boop_msg")

    assert stat.message_id == msg.id

    await repo.close()


async def test_admin_create_stat_duplicate_key_raises_value_error():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    # "tit" is a seeded stat; recreating it must be a clear, refused error
    # rather than silently appending a duplicate stat row.
    with pytest.raises(ValueError):
        await botmod.admin_create_stat(bot, repo, settings, "tit", "Dup")

    await repo.close()


async def test_admin_edit_stat_renames():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    await botmod.admin_create_stat(bot, repo, settings, "boop", "Boop")

    await botmod.admin_edit_stat(bot, repo, settings, "boop", name="Boop2")

    assert (await repo.get_stat("boop")).name == "Boop2"

    await repo.close()


async def test_admin_edit_stat_relinks_message():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    await botmod.admin_create_message(repo, "m1", "a {count}")
    m2 = await botmod.admin_create_message(repo, "m2", "b {count}")
    await botmod.admin_create_stat(bot, repo, settings, "boop", "Boop",
                                   message_name="m1")

    await botmod.admin_edit_stat(bot, repo, settings, "boop", message_name="m2")

    assert (await repo.get_stat("boop")).message_id == m2.id

    await repo.close()


async def test_admin_archive_stat_unregisters_live_command():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    await botmod.admin_create_stat(bot, repo, settings, "boop", "Boop")
    assert bot.get_command("boop") is not None

    await botmod.admin_archive_stat(bot, repo, settings, "boop")

    assert bot.get_command("boop") is None  # command removed
    assert (await repo.get_stat("boop")).archived_at is not None  # archived, kept

    await repo.close()


async def test_admin_delete_stat_unregisters_command_and_removes_row():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    await botmod.admin_create_stat(bot, repo, settings, "boop", "Boop")

    await botmod.admin_delete_stat(bot, repo, settings, "boop")

    assert bot.get_command("boop") is None
    assert await repo.get_stat("boop") is None

    await repo.close()


async def test_admin_list_stats_excludes_archived_by_default():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    await botmod.admin_create_stat(bot, repo, settings, "boop", "Boop")
    await botmod.admin_archive_stat(bot, repo, settings, "boop")

    active_keys = {s.key for s in await botmod.admin_list_stats(repo)}
    all_keys = {s.key for s in
                await botmod.admin_list_stats(repo, include_archived=True)}

    assert "boop" not in active_keys
    assert "boop" in all_keys

    await repo.close()


# ── admin message helpers ─────────────────────────────────────────────────

async def test_admin_create_message_adds_message():
    repo = await _flatfile_repo()

    msg = await botmod.admin_create_message(repo, "greet", "hi {count}")

    assert msg.name == "greet"
    names = {m.name for m in await repo.list_messages()}
    assert "greet" in names

    await repo.close()


async def test_admin_create_message_duplicate_name_raises_value_error():
    repo = await _flatfile_repo()

    # "tit_msg" is created by seed_defaults; a duplicate name must be refused.
    with pytest.raises(ValueError):
        await botmod.admin_create_message(repo, "tit_msg", "dup template")

    await repo.close()


async def test_admin_edit_message_updates_template():
    repo = await _flatfile_repo()
    msg = await botmod.admin_create_message(repo, "greet", "hi {count}")

    await botmod.admin_edit_message(repo, msg.id, template="yo {count}")

    assert (await repo.get_message(msg.id)).template == "yo {count}"

    await repo.close()


async def test_admin_edit_message_renames():
    repo = await _flatfile_repo()
    msg = await botmod.admin_create_message(repo, "greet", "hi {count}")

    await botmod.admin_edit_message(repo, msg.id, name="greet2")

    assert (await repo.get_message(msg.id)).name == "greet2"

    await repo.close()


async def test_admin_archive_message_hides_from_default_list():
    repo = await _flatfile_repo()
    msg = await botmod.admin_create_message(repo, "greet", "hi {count}")

    await botmod.admin_archive_message(repo, msg.id)

    assert msg.id not in {m.id for m in await repo.list_messages()}
    assert msg.id in {m.id for m in await repo.list_messages(include_archived=True)}

    await repo.close()


async def test_admin_delete_message_removes_row():
    repo = await _flatfile_repo()
    msg = await botmod.admin_create_message(repo, "greet", "hi {count}")

    await botmod.admin_delete_message(repo, msg.id)

    assert await repo.get_message(msg.id) is None

    await repo.close()


async def test_admin_list_messages_returns_non_archived():
    repo = await _flatfile_repo()

    names = {m.name for m in await botmod.admin_list_messages(repo)}

    assert "tit_msg" in names  # seeded message present

    await repo.close()


# ── both command surfaces exist (structure / registration) ────────────────

async def test_build_bot_registers_admin_prefix_group_with_subgroups():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    group = bot.get_command("admin")
    assert isinstance(group, commands.Group)
    assert group.get_command("stat") is not None
    assert group.get_command("msg") is not None

    await repo.close()


async def test_admin_stat_subgroup_exposes_crud_subcommands():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    group = bot.get_command("admin")
    assert group is not None, "prefix `admin` group must be registered"
    stat_group = group.get_command("stat")
    assert stat_group is not None, "`admin stat` subgroup must be registered"
    names = {c.name for c in stat_group.commands}
    assert {"add", "edit", "archive", "rm", "list"} <= names

    await repo.close()


async def test_admin_msg_subgroup_exposes_crud_subcommands():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    group = bot.get_command("admin")
    assert group is not None, "prefix `admin` group must be registered"
    msg_group = group.get_command("msg")
    assert msg_group is not None, "`admin msg` subgroup must be registered"
    names = {c.name for c in msg_group.commands}
    assert {"add", "edit", "archive", "rm", "list"} <= names

    await repo.close()


async def test_build_bot_registers_admin_slash_group_on_tree():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    tree_names = {c.name for c in bot.tree.get_commands()}
    assert "admin" in tree_names  # `/admin ...` slash group present

    await repo.close()


async def test_register_admin_commands_entrypoint_exists():
    # At minimum the intended registration entrypoint must exist, so both
    # surfaces can be wired from build_bot.
    assert callable(getattr(botmod, "register_admin_commands", None))
