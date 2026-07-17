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
from discord import app_commands

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


def _fake_interaction(user):
    """A fake slash Interaction: `.user` + an ephemeral `response.send_message`.

    Mirrors the interaction fakes in test_slash_migration / the /config slash
    tests: `interaction.user` is the invoker (gated via `is_admin`), and
    `interaction.response.send_message` is an AsyncMock so admin subcommands can
    ack/refuse ephemerally without a live gateway.
    """
    it = MagicMock()
    it.user = user
    it.response = MagicMock()
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.followup = MagicMock()
    it.followup.send = AsyncMock()
    return it


def _slash_admin_sub(bot, group_name, sub_name):
    """Reach a `/admin <group> <sub>` app-command callback via the tree.

    discord.py nests app-command groups: `bot.tree.get_command("admin")` is the
    top `app_commands.Group`, `.get_command("stat")` its `stat` subgroup, and
    `.get_command("add")` the leaf `app_commands.Command`. The leaf's `.callback`
    is the raw coroutine `(interaction, **params)` (defined module-level, so no
    `self` binding).
    """
    admin_g = bot.tree.get_command("admin")
    assert isinstance(admin_g, app_commands.Group), \
        "`/admin` must be an app_commands.Group on the tree"
    sub_g = admin_g.get_command(group_name)
    assert isinstance(sub_g, app_commands.Group), \
        f"`/admin {group_name}` must be an app-command subgroup"
    leaf = sub_g.get_command(sub_name)
    assert leaf is not None, f"`/admin {group_name} {sub_name}` must exist"
    return leaf


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


# ── is_admin honors MULTIPLE configured admin roles (ANY-match) ───────────────
# The five role-gating settings accept comma-separated ids; a member holding ANY
# listed role passes. is_admin reads `settings.admin_role_ids` (the list
# accessor). These use a lightweight settings fake exposing only `admin_role_ids`
# so the pre-impl RED is a clean AttributeError (is_admin still reading the old
# single-int `admin_role_id`), not a Settings int-coercion ValidationError.

def _multi_admin_settings(*role_ids):
    return SimpleNamespace(admin_role_ids=list(role_ids))


async def test_is_admin_true_for_member_holding_any_of_multiple_admin_roles():
    settings = _multi_admin_settings(111, 222)
    member = _member(member_id=5, role_ids=(333, 222))  # holds 222

    assert botmod.is_admin(member, settings) is True


async def test_is_admin_false_for_member_holding_none_of_multiple_admin_roles():
    settings = _multi_admin_settings(111, 222)
    member = _member(member_id=5, role_ids=(333, 444))

    assert botmod.is_admin(member, settings) is False


async def test_is_admin_false_when_no_admin_roles_configured():
    settings = _multi_admin_settings()  # admin_role_ids == []
    member = _member(member_id=5, role_ids=(0,))

    assert botmod.is_admin(member, settings) is False


async def test_slash_admin_stat_add_refuses_non_admin_and_mutates_nothing():
    # Phase 4: admin is slash-only. A non-admin invoking `/admin stat add` is
    # refused ephemerally and causes no DB / command-registry mutation.
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    add_cmd = _slash_admin_sub(bot, "stat", "add")
    interaction = _fake_interaction(_member(member_id=5, role_ids=(999,)))  # not admin

    before = {s.key for s in await repo.list_stats()}
    await add_cmd.callback(interaction, key="newkey", name="New Stat")
    after = {s.key for s in await repo.list_stats()}

    assert after == before  # non-admin caused no DB mutation
    assert bot.tree.get_command("newkey") is None  # and no live command registered
    assert bot.get_command("newkey") is None
    interaction.response.send_message.assert_awaited_once()  # a refusal was sent
    assert interaction.response.send_message.await_args.kwargs.get("ephemeral") is True

    await repo.close()


async def test_prefix_admin_group_is_not_registered():
    # Phase 4: the redundant prefix `!admin` group is gone; only `/admin` remains.
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    assert bot.get_command("admin") is None

    await repo.close()


# ── admin stat helpers ────────────────────────────────────────────────────

def _param_names(cmd):
    """Option names of an app command (Phase 6: stats are app commands)."""
    return [p.name for p in getattr(cmd, "parameters", [])]


async def test_admin_create_stat_adds_row_and_registers_live_tree_command():
    # Phase 6: the live counter command created on the fly is a SLASH app command
    # on `bot.tree`, not a prefix command.
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    stat = await botmod.admin_create_stat(bot, repo, settings, "boop", "Boop")

    assert stat.key == "boop"
    assert await repo.get_stat("boop") is not None
    assert bot.tree.get_command("boop") is not None  # live app command, no restart
    assert bot.get_command("boop") is None           # not on the prefix registry

    await repo.close()


async def test_admin_create_targeted_stat_command_takes_member_argument():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    await botmod.admin_create_stat(bot, repo, settings, "poke", "Poke",
                                   targeted=True)

    cmd = bot.tree.get_command("poke")
    assert cmd is not None
    assert "member" in _param_names(cmd)  # targeted -> takes a member option

    await repo.close()


async def test_admin_create_non_targeted_stat_command_has_no_member_argument():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    await botmod.admin_create_stat(bot, repo, settings, "boop", "Boop")

    cmd = bot.tree.get_command("boop")
    assert cmd is not None
    assert "member" not in _param_names(cmd)

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


@pytest.mark.parametrize("bad_key", ["Boop", "has space", "a" * 33, "bäd"])
async def test_admin_create_stat_invalid_key_rejected_without_writing_row(bad_key):
    # A stat key becomes a Discord app-command name, which must be lowercase
    # [a-z0-9_-] and 1-32 chars. An invalid key must be refused BEFORE the DB
    # row is written — otherwise repo.create_stat persists an orphan row and the
    # later Command(name=key) build raises, leaving an unusable, unrecoverable
    # stat with no command and no success message.
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    before = {s.key for s in await repo.list_stats()}

    with pytest.raises(ValueError):
        await botmod.admin_create_stat(bot, repo, settings, bad_key, "Bad")

    after = {s.key for s in await repo.list_stats()}
    assert after == before                       # no orphan row written
    assert bot.tree.get_command(bad_key) is None  # no command registered

    await repo.close()


async def test_register_stat_commands_skips_invalid_key_without_aborting():
    # A single invalid key already present in the repo (e.g. an orphan row from
    # the v3 flatfile→SQLite migration) must not abort register_stat_commands
    # (which runs inside on_ready) — the bad key is skipped and logged while all
    # valid stats + the rank command still register.
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    # Inject a bad key at the repo layer, bypassing admin_create_stat's guard,
    # to simulate a pre-existing invalid row.
    await repo.create_stat("Bad Key", "X")

    await botmod.register_stat_commands(bot, repo, settings)  # must not raise

    assert bot.tree.get_command("Bad Key") is None   # bad key skipped
    assert bot.tree.get_command("rank") is not None   # valid registration continued
    assert bot.tree.get_command("tit") is not None    # a seeded stat still registered

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


async def test_admin_archive_stat_unregisters_live_tree_command():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    await botmod.admin_create_stat(bot, repo, settings, "boop", "Boop")
    assert bot.tree.get_command("boop") is not None

    await botmod.admin_archive_stat(bot, repo, settings, "boop")

    assert bot.tree.get_command("boop") is None  # app command removed from tree
    assert (await repo.get_stat("boop")).archived_at is not None  # archived, kept

    await repo.close()


async def test_admin_delete_stat_unregisters_tree_command_and_removes_row():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    await botmod.admin_create_stat(bot, repo, settings, "boop", "Boop")
    assert bot.tree.get_command("boop") is not None  # live app command exists first

    await botmod.admin_delete_stat(bot, repo, settings, "boop")

    assert bot.tree.get_command("boop") is None
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

async def test_build_bot_registers_admin_slash_group_with_subgroups():
    # Phase 4: `/admin` is the sole admin surface — an app_commands.Group on the
    # tree with `stat` and `msg` subgroups; no prefix `!admin` group exists.
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    assert bot.get_command("admin") is None  # prefix group removed
    group = bot.tree.get_command("admin")
    assert isinstance(group, app_commands.Group)
    assert isinstance(group.get_command("stat"), app_commands.Group)
    assert isinstance(group.get_command("msg"), app_commands.Group)

    await repo.close()


async def test_admin_stat_slash_subgroup_exposes_crud_subcommands():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    stat_group = bot.tree.get_command("admin").get_command("stat")
    assert isinstance(stat_group, app_commands.Group)
    names = {c.name for c in stat_group.commands}
    assert {"add", "edit", "archive", "rm", "list"} <= names

    await repo.close()


async def test_admin_msg_slash_subgroup_exposes_crud_subcommands():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    msg_group = bot.tree.get_command("admin").get_command("msg")
    assert isinstance(msg_group, app_commands.Group)
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


# ── error-path / edge-case regressions ────────────────────────────────────

async def test_admin_create_stat_reactivates_archived_key():
    # Re-creating an ARCHIVED key must reactivate it in place (not dead-end
    # with a duplicate-key ValueError) and re-register the live command.
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    await botmod.admin_create_stat(bot, repo, settings, "boop", "Boop")
    await botmod.admin_archive_stat(bot, repo, settings, "boop")
    assert (await repo.get_stat("boop")).archived_at is not None
    assert bot.tree.get_command("boop") is None

    stat = await botmod.admin_create_stat(bot, repo, settings, "boop", "Boop Reborn")

    assert stat.key == "boop"
    assert (await repo.get_stat("boop")).archived_at is None   # reactivated
    assert (await repo.get_stat("boop")).name == "Boop Reborn"  # new name applied
    assert bot.tree.get_command("boop") is not None            # command re-registered

    await repo.close()


async def test_admin_create_stat_active_duplicate_still_raises():
    # A NON-archived existing key is a true duplicate and must still raise.
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    await botmod.admin_create_stat(bot, repo, settings, "boop", "Boop")

    with pytest.raises(ValueError):
        await botmod.admin_create_stat(bot, repo, settings, "boop", "Dup")

    await repo.close()


async def test_admin_create_stat_reserved_key_raises_and_writes_nothing():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    with pytest.raises(ValueError):
        await botmod.admin_create_stat(bot, repo, settings, "rank", "Rank")

    assert await repo.get_stat("rank") is None  # no dead row written

    await repo.close()


async def test_admin_edit_stat_unknown_key_raises():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    with pytest.raises(ValueError):
        await botmod.admin_edit_stat(bot, repo, settings, "ghost", name="X")

    await repo.close()


async def test_admin_edit_stat_no_fields_raises():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    await botmod.admin_create_stat(bot, repo, settings, "boop", "Boop")

    with pytest.raises(ValueError):
        await botmod.admin_edit_stat(bot, repo, settings, "boop")

    await repo.close()


async def test_is_admin_false_for_member_without_roles_attribute():
    # A DM author is a `User`, not a `Member`, and has no `.roles` — is_admin
    # must return False rather than raising AttributeError.
    settings = _settings(admin_role_id=42)
    member = SimpleNamespace(id=5)  # no `.roles`

    assert botmod.is_admin(member, settings) is False


# ── dynamic stat CRUD driven through the SLASH callbacks ──────────────────
#
# Phase 4 removed the prefix `!admin` group; the `/admin stat ...` callbacks are
# the only way an admin mutates the stat registry. Phase 6 makes the per-stat
# counters SLASH app commands on `bot.tree`, so the add/rm/archive callbacks now
# (un)register the live app command AND trigger a guild re-sync (via the module
# helper `n3x_bot.bot.resync_stat_commands`) so the picker reflects the change.

def _admin_interaction(admin_role_id=42):
    return _fake_interaction(_member(member_id=1, role_ids=(admin_role_id,)))


async def test_slash_admin_stat_add_registers_live_tree_counter_and_resyncs(monkeypatch):
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    add_cmd = _slash_admin_sub(bot, "stat", "add")

    resync = AsyncMock()
    monkeypatch.setattr(botmod, "resync_stat_commands", resync, raising=False)

    await add_cmd.callback(_admin_interaction(), key="boop", name="Boop")

    assert await repo.get_stat("boop") is not None      # row written
    assert bot.tree.get_command("boop") is not None      # live app command on tree
    assert bot.get_command("boop") is None               # not on prefix
    resync.assert_awaited()                              # guild re-sync triggered

    await repo.close()


async def test_slash_admin_stat_rm_unregisters_command_and_removes_row(monkeypatch):
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    add_cmd = _slash_admin_sub(bot, "stat", "add")
    rm_cmd = _slash_admin_sub(bot, "stat", "rm")
    await add_cmd.callback(_admin_interaction(), key="boop", name="Boop")
    assert bot.tree.get_command("boop") is not None

    resync = AsyncMock()
    monkeypatch.setattr(botmod, "resync_stat_commands", resync, raising=False)

    await rm_cmd.callback(_admin_interaction(), key="boop")

    assert bot.tree.get_command("boop") is None
    assert await repo.get_stat("boop") is None
    resync.assert_awaited()  # re-sync triggered on removal

    await repo.close()


async def test_slash_admin_stat_archive_unregisters_command_and_keeps_row(monkeypatch):
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    add_cmd = _slash_admin_sub(bot, "stat", "add")
    archive_cmd = _slash_admin_sub(bot, "stat", "archive")
    await add_cmd.callback(_admin_interaction(), key="boop", name="Boop")

    resync = AsyncMock()
    monkeypatch.setattr(botmod, "resync_stat_commands", resync, raising=False)

    await archive_cmd.callback(_admin_interaction(), key="boop")

    assert bot.tree.get_command("boop") is None
    assert (await repo.get_stat("boop")).archived_at is not None
    resync.assert_awaited()  # re-sync triggered on archive

    await repo.close()


# ── re-sync helper is invoked by the CRUD core helpers ────────────────────
#
# Phase 6 pins a module-level `n3x_bot.bot.resync_stat_commands(bot)` coroutine
# that republishes the current tree to the guilds. The stat CRUD helpers must
# invoke it so a newly added/removed slash command shows up without a restart.

async def test_admin_create_stat_triggers_resync(monkeypatch):
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    resync = AsyncMock()
    monkeypatch.setattr(botmod, "resync_stat_commands", resync, raising=False)

    await botmod.admin_create_stat(bot, repo, settings, "boop", "Boop")

    resync.assert_awaited()

    await repo.close()


async def test_admin_archive_stat_triggers_resync(monkeypatch):
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    await botmod.admin_create_stat(bot, repo, settings, "boop", "Boop")

    resync = AsyncMock()
    monkeypatch.setattr(botmod, "resync_stat_commands", resync, raising=False)

    await botmod.admin_archive_stat(bot, repo, settings, "boop")

    resync.assert_awaited()

    await repo.close()


async def test_admin_delete_stat_triggers_resync(monkeypatch):
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    await botmod.admin_create_stat(bot, repo, settings, "boop", "Boop")

    resync = AsyncMock()
    monkeypatch.setattr(botmod, "resync_stat_commands", resync, raising=False)

    await botmod.admin_delete_stat(bot, repo, settings, "boop")

    resync.assert_awaited()

    await repo.close()


async def test_slash_admin_stat_edit_renames_via_callback():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    add_cmd = _slash_admin_sub(bot, "stat", "add")
    edit_cmd = _slash_admin_sub(bot, "stat", "edit")
    await add_cmd.callback(_admin_interaction(), key="boop", name="Boop")

    await edit_cmd.callback(_admin_interaction(), key="boop", name="Boop2")

    assert (await repo.get_stat("boop")).name == "Boop2"

    await repo.close()


# ── message CRUD driven through the SLASH callbacks ───────────────────────

async def test_slash_admin_msg_add_creates_message_row():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    add_cmd = _slash_admin_sub(bot, "msg", "add")

    await add_cmd.callback(_admin_interaction(), name="greet", template="hi {count}")

    assert "greet" in {m.name for m in await repo.list_messages()}

    await repo.close()


async def test_slash_admin_msg_edit_updates_template():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    msg = await botmod.admin_create_message(repo, "greet", "hi {count}")
    edit_cmd = _slash_admin_sub(bot, "msg", "edit")

    await edit_cmd.callback(_admin_interaction(), message_id=msg.id,
                            template="yo {count}")

    assert (await repo.get_message(msg.id)).template == "yo {count}"

    await repo.close()


async def test_slash_admin_msg_archive_hides_from_default_list():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    msg = await botmod.admin_create_message(repo, "greet", "hi {count}")
    archive_cmd = _slash_admin_sub(bot, "msg", "archive")

    await archive_cmd.callback(_admin_interaction(), message_id=msg.id)

    assert msg.id not in {m.id for m in await repo.list_messages()}
    assert msg.id in {m.id for m in await repo.list_messages(include_archived=True)}

    await repo.close()


async def test_slash_admin_msg_rm_removes_row():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    msg = await botmod.admin_create_message(repo, "greet", "hi {count}")
    rm_cmd = _slash_admin_sub(bot, "msg", "rm")

    await rm_cmd.callback(_admin_interaction(), message_id=msg.id)

    assert await repo.get_message(msg.id) is None

    await repo.close()


async def test_slash_admin_msg_list_reports_seeded_message():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    list_cmd = _slash_admin_sub(bot, "msg", "list")

    interaction = _admin_interaction()
    await list_cmd.callback(interaction)

    interaction.response.send_message.assert_awaited_once()
    text = interaction.response.send_message.await_args.args[0]
    assert "tit_msg" in text  # seeded message surfaced

    await repo.close()


async def test_slash_admin_msg_add_refuses_non_admin_and_mutates_nothing():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    add_cmd = _slash_admin_sub(bot, "msg", "add")
    interaction = _fake_interaction(_member(member_id=5, role_ids=(999,)))

    before = {m.name for m in await repo.list_messages(include_archived=True)}
    await add_cmd.callback(interaction, name="sneaky", template="x {count}")
    after = {m.name for m in await repo.list_messages(include_archived=True)}

    assert after == before  # non-admin wrote nothing
    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.kwargs.get("ephemeral") is True

    await repo.close()
