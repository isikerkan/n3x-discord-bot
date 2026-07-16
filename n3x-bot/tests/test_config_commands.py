"""RED-phase specs for Phase 2: Discord commands that write `runtime_config`
DB overrides.

Phase 1 (merged) built the `runtime_config` table + the `RuntimeConfig`
resolver (DB override else `Settings`). Phase 2 adds admin-gated commands that
write those overrides and call `bot.runtime_config.refresh(repo)` so a change
goes live without a restart.

Pinned API (to be implemented downstream in `n3x_bot/config_commands.py`):

    register_config_commands(bot, repo, settings) -> None
        # idempotent; wired from build_bot. Registers a single top-level
        # prefix group command `config` with subcommands.

    # Prefix command surface, all gated on `n3x_bot.admin.is_admin`:
    #   !config channel  <purpose>            -> posts a View w/ ChannelSelect
    #   !config role     <purpose>            -> posts a View w/ RoleSelect
    #   !config message  <purpose> <id>       -> plain arg; writes message id
    #   !config gate-rewards   <value>        -> writes `gate_rewards`
    #   !config allowed-maps   <value>        -> writes `allowed_maps`
    #   !config voice-roles    <value>        -> writes `voice_achievement_roles`
    #   !config reminder-time  <hh:mm>        -> writes `reminder_time`
    #   !config show                          -> effective config + overrides
    #   !config reset <key>                   -> deletes an override (env wins)

    CHANNEL_PURPOSES: dict[str, str]   # purpose -> "<x>_channel_id"
    ROLE_PURPOSES:    dict[str, str]   # purpose -> "<x>_role_id"
    MESSAGE_PURPOSES: dict[str, str]   # purpose -> "timer_overview_message_id"

The purpose->key maps and the select/view mechanics are documented inline at
each test. `register_config_commands` is imported LAZILY inside test bodies so
the RED state is a clean per-test ModuleNotFoundError rather than a
collection-time ImportError.

DESIGN NOTES / PINNED ASSUMPTIONS (see the handoff report):
  * Prefix `!config` group (not slash). Prefix is the tested contract: a View
    can be posted from a prefix command via `ctx.send(view=...)`, and the
    select's own confirmation reply is the ephemeral interaction response.
    Slash is left to the architect's discretion (not asserted here).
  * The channel/role select value is driven in tests via the real discord.py
    mechanic: `BaseSelect.values` falls back to `self._values` (verified in
    discord.py 2.7.1), so a test sets `select._values = [fake]` then awaits
    `select.callback(interaction)` directly (mirroring the KappaConfirmView
    `on_submit(interaction)` tests).
  * Message id is a PLAIN command arg (no Modal); a non-numeric id is rejected
    with no write.
  * `config reset` rejects any key not in OVERRIDABLE_KEYS (guards against
    resetting env-authoritative keys like admin_role_id / discord_token).
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from n3x_bot.bot import build_bot
from n3x_bot.config import Settings
from n3x_bot.runtime_config import OVERRIDABLE_KEYS
from n3x_bot.seed import seed_defaults
from n3x_bot.storage.json_repo import JsonRepository

ADMIN_ROLE = 42

BASE_SETTINGS_KWARGS = dict(
    discord_token="tok",
    target_role_id=1,
    welcome_channel_id=2,
    reminder_channel_id=999,
    julez_id=424242,
    admin_role_id=ADMIN_ROLE,
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
    repo._test_path = path
    return repo


async def _cleanup(repo: JsonRepository) -> None:
    path = getattr(repo, "_test_path", None)
    await repo.close()
    if path and os.path.exists(path):
        os.remove(path)


def _member(*, member_id=5, role_ids=(ADMIN_ROLE,)):
    return SimpleNamespace(id=member_id, roles=[SimpleNamespace(id=r) for r in role_ids],
                           bot=False)


def _admin():
    return _member(role_ids=(ADMIN_ROLE,))


def _non_admin():
    return _member(role_ids=(999,))


def _ctx(author):
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.author = author
    return ctx


def _fake_interaction(user=None):
    it = MagicMock()
    it.user = user or _admin()
    it.response = MagicMock()
    it.response.send_message = AsyncMock()
    return it


def _fake_channel(channel_id):
    return SimpleNamespace(id=channel_id)


def _fake_role(role_id):
    return SimpleNamespace(id=role_id)


async def _bot_with_config(settings, repo):
    """A built bot with the config commands registered. `register_config_commands`
    is idempotent, so this is safe even once build_bot wires it too."""
    from n3x_bot.config_commands import register_config_commands
    bot = build_bot(settings, repo)
    register_config_commands(bot, repo, settings)
    return bot


def _config_sub(bot, name):
    group = bot.get_command("config")
    assert group is not None, "prefix `config` group must be registered"
    sub = group.get_command(name)
    assert sub is not None, f"`config {name}` subcommand must be registered"
    return sub


def _posted_view(send_mock):
    """The View posted via any `ctx.send(..., view=...)` call, else None."""
    for call in send_mock.await_args_list:
        view = call.kwargs.get("view")
        if view is not None:
            return view
    return None


def _select_of(view, select_type):
    return next((c for c in view.children if isinstance(c, select_type)), None)


def _sent_text(send_mock):
    """All text posted, from plain args and any embeds, joined — format-agnostic."""
    parts = []
    for call in send_mock.await_args_list:
        if call.args and isinstance(call.args[0], str):
            parts.append(call.args[0])
        embed = call.kwargs.get("embed")
        if embed is not None:
            parts.append(str(getattr(embed, "description", "") or ""))
            for field in getattr(embed, "fields", []):
                parts.append(f"{field.name} {field.value}")
    return "\n".join(parts)


# ── 1. config channel <purpose> ────────────────────────────────────────────

# purpose -> runtime_config key (pinned):
CHANNEL_MAP = {
    "welcome": "welcome_channel_id",
    "reminder": "reminder_channel_id",
    "gate_input": "gate_input_channel_id",
    "gate_stats": "gate_stats_channel_id",
    "milestone": "milestone_channel_id",
    "overview": "overview_channel_id",
    "kodex_check": "kodex_check_channel_id",
    "timer_overview": "timer_overview_channel_id",
}


async def test_config_channel_valid_purpose_posts_view_with_channel_select():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_admin())

    await _config_sub(bot, "channel").callback(ctx, "welcome")

    view = _posted_view(ctx.send)
    assert view is not None, "a View must be posted for a valid channel purpose"
    assert _select_of(view, discord.ui.ChannelSelect) is not None

    await _cleanup(repo)


async def test_config_channel_select_callback_stores_id_and_refreshes():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_admin())
    await _config_sub(bot, "channel").callback(ctx, "welcome")
    select = _select_of(_posted_view(ctx.send), discord.ui.ChannelSelect)

    # Drive the real select: BaseSelect.values falls back to self._values.
    select._values = [_fake_channel(999)]
    await select.callback(_fake_interaction())

    assert await repo.get_runtime_config("welcome_channel_id") == "999"
    # refresh() ran -> the live resolver reflects the override immediately.
    assert bot.runtime_config.welcome_channel_id == 999

    await _cleanup(repo)


async def test_config_channel_select_confirms_ephemerally():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_admin())
    await _config_sub(bot, "channel").callback(ctx, "gate_stats")
    select = _select_of(_posted_view(ctx.send), discord.ui.ChannelSelect)
    select._values = [_fake_channel(321)]
    interaction = _fake_interaction()

    await select.callback(interaction)

    interaction.response.send_message.assert_awaited()
    assert interaction.response.send_message.await_args.kwargs.get("ephemeral") is True

    await _cleanup(repo)


async def test_all_channel_purposes_map_to_expected_keys():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)

    for i, (purpose, key) in enumerate(CHANNEL_MAP.items(), start=1):
        ctx = _ctx(_admin())
        await _config_sub(bot, "channel").callback(ctx, purpose)
        select = _select_of(_posted_view(ctx.send), discord.ui.ChannelSelect)
        assert select is not None, purpose
        chan_id = 1000 + i
        select._values = [_fake_channel(chan_id)]
        await select.callback(_fake_interaction())
        assert await repo.get_runtime_config(key) == str(chan_id), purpose

    await _cleanup(repo)


async def test_config_channel_invalid_purpose_rejected_no_view():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_admin())

    await _config_sub(bot, "channel").callback(ctx, "bogus")

    ctx.send.assert_awaited()
    assert _posted_view(ctx.send) is None  # no select view for an invalid purpose
    assert await repo.all_runtime_config() == {}

    await _cleanup(repo)


async def test_config_channel_non_admin_refused_no_view():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_non_admin())

    await _config_sub(bot, "channel").callback(ctx, "welcome")

    assert "Berechtigung" in _sent_text(ctx.send)
    assert _posted_view(ctx.send) is None
    assert await repo.all_runtime_config() == {}

    await _cleanup(repo)


async def test_config_channel_select_refuses_non_author_no_write():
    # The picker is author-locked: a non-author driving the admin's posted
    # select must be refused ephemerally, with NO write and NO resolver refresh.
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_admin())  # author id == 5
    await _config_sub(bot, "channel").callback(ctx, "welcome")
    select = _select_of(_posted_view(ctx.send), discord.ui.ChannelSelect)
    select._values = [_fake_channel(999)]
    intruder = _fake_interaction(user=_member(member_id=99))

    await select.callback(intruder)

    intruder.response.send_message.assert_awaited_once()
    assert "Nicht für dich" in intruder.response.send_message.await_args.args[0]
    assert intruder.response.send_message.await_args.kwargs.get("ephemeral") is True
    assert await repo.all_runtime_config() == {}  # no write
    assert bot.runtime_config.welcome_channel_id == settings.welcome_channel_id

    await _cleanup(repo)


# ── 2. config role <purpose> ────────────────────────────────────────────────

ROLE_MAP = {
    "target": "target_role_id",
    "gate_delete": "gate_delete_role_id",
    "base_timer": "base_timer_role_id",
}


async def test_config_role_valid_purpose_posts_view_with_role_select():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_admin())

    await _config_sub(bot, "role").callback(ctx, "target")

    view = _posted_view(ctx.send)
    assert view is not None
    assert _select_of(view, discord.ui.RoleSelect) is not None

    await _cleanup(repo)


async def test_config_role_select_callback_stores_id_and_refreshes():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_admin())
    await _config_sub(bot, "role").callback(ctx, "target")
    select = _select_of(_posted_view(ctx.send), discord.ui.RoleSelect)

    select._values = [_fake_role(777)]
    await select.callback(_fake_interaction())

    assert await repo.get_runtime_config("target_role_id") == "777"
    assert bot.runtime_config.target_role_id == 777

    await _cleanup(repo)


async def test_all_role_purposes_map_to_expected_keys():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)

    for i, (purpose, key) in enumerate(ROLE_MAP.items(), start=1):
        ctx = _ctx(_admin())
        await _config_sub(bot, "role").callback(ctx, purpose)
        select = _select_of(_posted_view(ctx.send), discord.ui.RoleSelect)
        assert select is not None, purpose
        role_id = 500 + i
        select._values = [_fake_role(role_id)]
        await select.callback(_fake_interaction())
        assert await repo.get_runtime_config(key) == str(role_id), purpose

    await _cleanup(repo)


async def test_config_role_select_refuses_non_author_no_write():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_admin())  # author id == 5
    await _config_sub(bot, "role").callback(ctx, "target")
    select = _select_of(_posted_view(ctx.send), discord.ui.RoleSelect)
    select._values = [_fake_role(777)]
    intruder = _fake_interaction(user=_member(member_id=99))

    await select.callback(intruder)

    intruder.response.send_message.assert_awaited_once()
    assert "Nicht für dich" in intruder.response.send_message.await_args.args[0]
    assert intruder.response.send_message.await_args.kwargs.get("ephemeral") is True
    assert await repo.all_runtime_config() == {}  # no write
    assert bot.runtime_config.target_role_id == settings.target_role_id

    await _cleanup(repo)


async def test_config_role_invalid_purpose_rejected_no_view():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_admin())

    await _config_sub(bot, "role").callback(ctx, "nope")

    ctx.send.assert_awaited()
    assert _posted_view(ctx.send) is None
    assert await repo.all_runtime_config() == {}

    await _cleanup(repo)


async def test_config_role_non_admin_refused_no_view():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_non_admin())

    await _config_sub(bot, "role").callback(ctx, "target")

    assert "Berechtigung" in _sent_text(ctx.send)
    assert _posted_view(ctx.send) is None
    assert await repo.all_runtime_config() == {}

    await _cleanup(repo)


# ── 3. config message <purpose> <id> (plain arg) ────────────────────────────


async def test_config_message_stores_message_id_and_refreshes():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_admin())

    await _config_sub(bot, "message").callback(ctx, "timer_overview", "555")

    assert await repo.get_runtime_config("timer_overview_message_id") == "555"
    assert bot.runtime_config.timer_overview_message_id == 555

    await _cleanup(repo)


async def test_config_message_non_numeric_id_rejected_no_write():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_admin())

    await _config_sub(bot, "message").callback(ctx, "timer_overview", "55x")

    ctx.send.assert_awaited()
    assert await repo.get_runtime_config("timer_overview_message_id") is None

    await _cleanup(repo)


async def test_config_message_invalid_purpose_rejected_no_write():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_admin())

    await _config_sub(bot, "message").callback(ctx, "bogus", "555")

    ctx.send.assert_awaited()
    assert await repo.all_runtime_config() == {}

    await _cleanup(repo)


async def test_config_message_non_admin_refused():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_non_admin())

    await _config_sub(bot, "message").callback(ctx, "timer_overview", "555")

    assert "Berechtigung" in _sent_text(ctx.send)
    assert await repo.all_runtime_config() == {}

    await _cleanup(repo)


# ── 4. content setters ──────────────────────────────────────────────────────


async def test_config_gate_rewards_sets_value_and_refreshes():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_admin())

    await _config_sub(bot, "gate-rewards").callback(ctx, "a:1,b:2")

    assert await repo.get_runtime_config("gate_rewards") == "a:1,b:2"
    assert bot.runtime_config.gate_rewards_map() == {"a": 1, "b": 2}

    await _cleanup(repo)


async def test_config_allowed_maps_sets_value_and_refreshes():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_admin())

    await _config_sub(bot, "allowed-maps").callback(ctx, "1-1,2-2,3-3")

    assert await repo.get_runtime_config("allowed_maps") == "1-1,2-2,3-3"
    assert bot.runtime_config.allowed_maps_list == ["1-1", "2-2", "3-3"]

    await _cleanup(repo)


async def test_config_voice_roles_sets_value_and_refreshes():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_admin())

    await _config_sub(bot, "voice-roles").callback(ctx, "x:1,y:2")

    assert await repo.get_runtime_config("voice_achievement_roles") == "x:1,y:2"
    assert bot.runtime_config.voice_role_map() == {"x": 1, "y": 2}

    await _cleanup(repo)


async def test_config_reminder_time_sets_value_and_refreshes():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_admin())

    await _config_sub(bot, "reminder-time").callback(ctx, "20:15")

    assert await repo.get_runtime_config("reminder_time") == "20:15"
    assert bot.runtime_config.reminder_hm() == (20, 15)

    await _cleanup(repo)


async def test_config_gate_delete_delay_sets_value_verbatim_and_refreshes():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_admin())

    await _config_sub(bot, "gate-delete-delay").callback(ctx, "2m")

    # the raw value is written verbatim (parsing happens at the resolver)…
    assert await repo.get_runtime_config("gate_message_delete_delay") == "2m"
    # …and the live resolver reflects it immediately after refresh().
    assert bot.runtime_config.gate_delete_delay_seconds == 120

    await _cleanup(repo)


async def test_config_gate_delete_delay_rejects_invalid_duration():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_admin())

    await _config_sub(bot, "gate-delete-delay").callback(ctx, "banana")

    # nothing written; the operator gets an error instead of a false "gesetzt".
    assert await repo.get_runtime_config("gate_message_delete_delay") is None
    assert "Ungültige Dauer" in _sent_text(ctx.send)

    await _cleanup(repo)


async def test_config_content_setter_non_admin_refused():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_non_admin())

    await _config_sub(bot, "gate-rewards").callback(ctx, "a:1,b:2")

    assert "Berechtigung" in _sent_text(ctx.send)
    assert await repo.get_runtime_config("gate_rewards") is None

    await _cleanup(repo)


# ── 5. config show ──────────────────────────────────────────────────────────


async def test_config_show_includes_overridden_key_and_db_value():
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=555)
    bot = await _bot_with_config(settings, repo)
    await repo.set_runtime_config("gate_stats_channel_id", "999")
    await bot.runtime_config.refresh(repo)
    ctx = _ctx(_admin())

    await _config_sub(bot, "show").callback(ctx)

    text = _sent_text(ctx.send)
    assert "gate_stats_channel_id" in text  # the key is listed
    assert "999" in text                    # its DB-override value is shown

    await _cleanup(repo)


async def test_config_show_lists_a_non_overridden_key_at_env_default():
    repo = await _flatfile_repo()
    settings = _settings(welcome_channel_id=222)
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_admin())

    await _config_sub(bot, "show").callback(ctx)

    text = _sent_text(ctx.send)
    assert "welcome_channel_id" in text
    assert "222" in text  # env-default value shown when no override exists

    await _cleanup(repo)


async def test_config_show_non_admin_refused():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_non_admin())

    await _config_sub(bot, "show").callback(ctx)

    assert "Berechtigung" in _sent_text(ctx.send)

    await _cleanup(repo)


# ── 6. config reset <key> ───────────────────────────────────────────────────


async def test_config_reset_reverts_override_to_env_default():
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=555)
    bot = await _bot_with_config(settings, repo)
    await repo.set_runtime_config("gate_stats_channel_id", "999")
    await bot.runtime_config.refresh(repo)
    assert bot.runtime_config.gate_stats_channel_id == 999  # override active

    ctx = _ctx(_admin())
    await _config_sub(bot, "reset").callback(ctx, "gate_stats_channel_id")

    assert await repo.get_runtime_config("gate_stats_channel_id") is None
    assert bot.runtime_config.gate_stats_channel_id == 555  # back to .env base

    await _cleanup(repo)


async def test_config_reset_non_overridable_key_rejected():
    # admin_role_id gates the config commands; it must NOT be resettable via
    # this command (it is env-authoritative, never a runtime override).
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_admin())

    await _config_sub(bot, "reset").callback(ctx, "admin_role_id")

    ctx.send.assert_awaited()
    assert "admin_role_id" not in OVERRIDABLE_KEYS  # guard precondition
    assert bot.runtime_config.admin_role_id == settings.admin_role_id

    await _cleanup(repo)


async def test_config_reset_unknown_key_rejected():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)
    ctx = _ctx(_admin())

    await _config_sub(bot, "reset").callback(ctx, "not_a_real_key")

    ctx.send.assert_awaited()
    assert await repo.all_runtime_config() == {}

    await _cleanup(repo)


async def test_config_reset_non_admin_refused():
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=555)
    bot = await _bot_with_config(settings, repo)
    await repo.set_runtime_config("gate_stats_channel_id", "999")
    await bot.runtime_config.refresh(repo)
    ctx = _ctx(_non_admin())

    await _config_sub(bot, "reset").callback(ctx, "gate_stats_channel_id")

    assert "Berechtigung" in _sent_text(ctx.send)
    # a non-admin reset must not delete the override
    assert await repo.get_runtime_config("gate_stats_channel_id") == "999"

    await _cleanup(repo)


# ── 7. wiring ───────────────────────────────────────────────────────────────


async def test_register_config_commands_entrypoint_exists():
    import n3x_bot.config_commands as ccmod
    assert callable(getattr(ccmod, "register_config_commands", None))


async def test_build_bot_registers_config_group():
    from discord.ext import commands
    repo = await _flatfile_repo()
    settings = _settings()

    bot = build_bot(settings, repo)  # build_bot alone must wire config

    group = bot.get_command("config")
    assert isinstance(group, commands.Group)

    await _cleanup(repo)


async def test_config_group_exposes_expected_subcommands():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot_with_config(settings, repo)

    group = bot.get_command("config")
    names = {c.name for c in group.commands}
    expected = {"channel", "role", "message", "gate-rewards", "allowed-maps",
                "voice-roles", "reminder-time", "gate-delete-delay", "show",
                "reset"}
    assert expected <= names

    await _cleanup(repo)


async def test_register_config_commands_is_idempotent():
    from n3x_bot.config_commands import register_config_commands
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    register_config_commands(bot, repo, settings)
    register_config_commands(bot, repo, settings)  # must not raise on re-register

    assert bot.get_command("config") is not None

    await _cleanup(repo)
