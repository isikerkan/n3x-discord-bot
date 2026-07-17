"""RED-phase specs for Phase 3 of the prefix -> slash migration: the `/config`
admin group.

Phases 1-2 (merged) built the app-command admin gate `n3x_bot.admin.app_is_admin`
and migrated the proof batch + gate commands to slash-ONLY app commands on
`bot.tree`. Phase 3 migrates the `!config` prefix group to a slash-ONLY
`app_commands.Group` named `config`, and REPLACES the interactive
`ChannelConfigView` / `RoleConfigView` pickers with native channel/role options.

Pinned slash surface (to be implemented downstream in `n3x_bot/config_commands.py`),
all subcommands admin-gated via `app_is_admin(interaction, settings)`:

    /config channel  purpose:<Choice> channel:<channel>   -> write <x>_channel_id
    /config role     purpose:<Choice> role:<role>         -> write <x>_role_id
    /config message  purpose:<Choice> message_id:<str>    -> write message id
    /config gate-rewards   value:<str>                    -> write `gate_rewards`
    /config allowed-maps   value:<str>                    -> write `allowed_maps`
    /config voice-roles    value:<str>          -> write `voice_achievement_roles`
    /config reminder-time  value:<str>                    -> write `reminder_time`
    /config gate-delete-delay value:<str> -> parse_duration-validated, then write
    /config show                                          -> effective config
    /config reset key:<Choice over OVERRIDABLE_KEYS>      -> delete an override

DESIGN NOTES / PINNED ASSUMPTIONS:
  * `config` is REMOVED from the prefix registry: `bot.get_command("config")` is
    None; the group lives on `bot.tree` as an `app_commands.Group`.
  * Subcommands are addressed the same way the migrated `/gate` group is in the
    other slash tests:
        bot.tree.get_command("config").get_command("channel").callback(
            interaction, purpose="gate_stats", channel=SimpleNamespace(id=999))
  * `purpose` / `key` are `app_commands.Choice[str]` params, so the callback
    receives the raw `str` value and an invalid enumerand can never reach the
    body. The old "invalid purpose / unknown key" branches are therefore gone.
  * The channel/role options are NATIVE (`channel:` / `role:`); the direct-call
    tests pass a `SimpleNamespace(id=…)`. `str(channel.id)` is written verbatim.
  * The `ChannelConfigView` / `RoleConfigView` classes are REMOVED from the
    module (grep-confirmed to have no other users).
  * Admin refusals are ephemeral ("❌ Keine Berechtigung.") and perform NO write
    and NO resolver refresh.
  * `show` iterates OVERRIDABLE_KEYS only, so it never leaks `discord_token` /
    `database_url`.

`register_config_commands` is imported LAZILY inside test bodies / helpers so the
RED state is a clean per-test failure rather than a collection-time ImportError.
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from discord import app_commands

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
    return SimpleNamespace(id=member_id,
                           roles=[SimpleNamespace(id=r) for r in role_ids],
                           bot=False)


def _admin():
    return _member(role_ids=(ADMIN_ROLE,))


def _non_admin():
    return _member(role_ids=(999,))


def _fake_interaction(user=None):
    it = MagicMock()
    it.user = user or _admin()
    it.response = MagicMock()
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.followup = MagicMock()
    it.followup.send = AsyncMock()
    return it


def _fake_channel(channel_id):
    return SimpleNamespace(id=channel_id)


def _fake_role(role_id):
    return SimpleNamespace(id=role_id)


def _sent_text(interaction) -> str:
    """All text the callback sent the caller, via response.send_message and any
    followup.send, joined — content arg or `content=` kwarg."""
    parts = []
    for mock in (interaction.response.send_message, interaction.followup.send):
        for call in mock.await_args_list:
            if call.args and isinstance(call.args[0], str):
                parts.append(call.args[0])
            content = call.kwargs.get("content")
            if isinstance(content, str):
                parts.append(content)
    return "\n".join(parts)


def _last_send(interaction):
    """The final reply the callback made to the caller (response or followup)."""
    calls = (list(interaction.response.send_message.await_args_list)
             + list(interaction.followup.send.await_args_list))
    assert calls, "the callback must reply to the caller"
    return calls[-1]


async def _bot(settings, repo):
    """A built bot; `register_config_commands` is wired by build_bot itself."""
    return build_bot(settings, repo)


def _config_group(bot):
    assert bot.get_command("config") is None, \
        "`config` must be REMOVED from the prefix registry (slash-only)"
    group = bot.tree.get_command("config")
    assert isinstance(group, app_commands.Group), \
        "`config` must be an app_commands.Group on bot.tree"
    return group


def _config_sub(bot, name):
    sub = _config_group(bot).get_command(name)
    assert sub is not None, f"/config {name} subcommand must be registered"
    return sub


# ── 0. group presence / prefix absence / View removal ───────────────────────

async def test_config_is_app_group_not_prefix_command():
    repo = await _flatfile_repo()
    bot = await _bot(_settings(), repo)

    assert bot.get_command("config") is None
    assert isinstance(bot.tree.get_command("config"), app_commands.Group)

    await _cleanup(repo)


async def test_config_group_exposes_expected_subcommands():
    repo = await _flatfile_repo()
    bot = await _bot(_settings(), repo)

    names = {c.name for c in _config_group(bot).commands}
    expected = {"channel", "role", "message", "gate-rewards", "allowed-maps",
                "voice-roles", "reminder-time", "gate-delete-delay", "show",
                "reset"}
    assert expected <= names

    await _cleanup(repo)


async def test_config_view_classes_are_removed():
    # The native channel/role options replace the pickers; the View classes must
    # be gone (grep-confirmed no other users).
    import n3x_bot.config_commands as ccmod
    assert not hasattr(ccmod, "ChannelConfigView")
    assert not hasattr(ccmod, "RoleConfigView")


async def test_register_config_commands_is_idempotent():
    from n3x_bot.config_commands import register_config_commands
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    register_config_commands(bot, repo, settings)  # re-register must not raise

    assert isinstance(bot.tree.get_command("config"), app_commands.Group)

    await _cleanup(repo)


# ── 1. /config channel purpose:<Choice> channel:<channel> ───────────────────

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


async def test_config_channel_writes_id_and_refreshes():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot(settings, repo)
    interaction = _fake_interaction()

    await _config_sub(bot, "channel").callback(
        interaction, purpose="welcome", channel=_fake_channel(999))

    assert await repo.get_runtime_config("welcome_channel_id") == "999"
    # refresh() ran -> the live resolver reflects the override immediately.
    assert bot.runtime_config.welcome_channel_id == 999

    await _cleanup(repo)


async def test_config_channel_confirms_ephemerally():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot(settings, repo)
    interaction = _fake_interaction()

    await _config_sub(bot, "channel").callback(
        interaction, purpose="gate_stats", channel=_fake_channel(321))

    assert _last_send(interaction).kwargs.get("ephemeral") is True

    await _cleanup(repo)


async def test_all_channel_purposes_map_to_expected_keys():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot(settings, repo)

    for i, (purpose, key) in enumerate(CHANNEL_MAP.items(), start=1):
        chan_id = 1000 + i
        await _config_sub(bot, "channel").callback(
            _fake_interaction(), purpose=purpose, channel=_fake_channel(chan_id))
        assert await repo.get_runtime_config(key) == str(chan_id), purpose

    await _cleanup(repo)


async def test_config_channel_non_admin_refused_no_write():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot(settings, repo)
    interaction = _fake_interaction(user=_non_admin())

    await _config_sub(bot, "channel").callback(
        interaction, purpose="welcome", channel=_fake_channel(999))

    assert "Berechtigung" in _sent_text(interaction)
    assert _last_send(interaction).kwargs.get("ephemeral") is True
    assert await repo.all_runtime_config() == {}  # no write
    assert bot.runtime_config.welcome_channel_id == settings.welcome_channel_id

    await _cleanup(repo)


# ── 2. /config role purpose:<Choice> role:<role> ────────────────────────────

ROLE_MAP = {
    "target": "target_role_id",
    "gate_delete": "gate_delete_role_id",
    "base_timer": "base_timer_role_id",
}


async def test_config_role_writes_id_and_refreshes():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot(settings, repo)
    interaction = _fake_interaction()

    await _config_sub(bot, "role").callback(
        interaction, purpose="target", role=_fake_role(777))

    assert await repo.get_runtime_config("target_role_id") == "777"
    assert bot.runtime_config.target_role_id == 777
    assert _last_send(interaction).kwargs.get("ephemeral") is True

    await _cleanup(repo)


async def test_all_role_purposes_map_to_expected_keys():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot(settings, repo)

    for i, (purpose, key) in enumerate(ROLE_MAP.items(), start=1):
        role_id = 500 + i
        await _config_sub(bot, "role").callback(
            _fake_interaction(), purpose=purpose, role=_fake_role(role_id))
        assert await repo.get_runtime_config(key) == str(role_id), purpose

    await _cleanup(repo)


async def test_config_role_non_admin_refused_no_write():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot(settings, repo)
    interaction = _fake_interaction(user=_non_admin())

    await _config_sub(bot, "role").callback(
        interaction, purpose="target", role=_fake_role(777))

    assert "Berechtigung" in _sent_text(interaction)
    assert await repo.all_runtime_config() == {}
    # MIGRATED for multi-role: the field is now a str; compare the list accessors
    # (with no override, the resolver mirrors the .env base).
    assert bot.runtime_config.target_role_ids == settings.target_role_ids

    await _cleanup(repo)


# ── 3. /config message purpose:<Choice> message_id:<str> ────────────────────


async def test_config_message_writes_id_and_refreshes():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot(settings, repo)
    interaction = _fake_interaction()

    await _config_sub(bot, "message").callback(
        interaction, purpose="timer_overview", message_id="555")

    assert await repo.get_runtime_config("timer_overview_message_id") == "555"
    assert bot.runtime_config.timer_overview_message_id == 555
    assert _last_send(interaction).kwargs.get("ephemeral") is True

    await _cleanup(repo)


async def test_config_message_non_numeric_id_rejected_no_write():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot(settings, repo)
    interaction = _fake_interaction()

    await _config_sub(bot, "message").callback(
        interaction, purpose="timer_overview", message_id="55x")

    interaction.response.send_message.assert_awaited()
    assert await repo.get_runtime_config("timer_overview_message_id") is None
    assert _last_send(interaction).kwargs.get("ephemeral") is True

    await _cleanup(repo)


async def test_config_message_non_admin_refused():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot(settings, repo)
    interaction = _fake_interaction(user=_non_admin())

    await _config_sub(bot, "message").callback(
        interaction, purpose="timer_overview", message_id="555")

    assert "Berechtigung" in _sent_text(interaction)
    assert await repo.all_runtime_config() == {}

    await _cleanup(repo)


# ── 4. verbatim content setters ─────────────────────────────────────────────


async def test_config_gate_rewards_sets_value_and_refreshes():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot(settings, repo)
    interaction = _fake_interaction()

    await _config_sub(bot, "gate-rewards").callback(interaction, value="a:1,b:2")

    assert await repo.get_runtime_config("gate_rewards") == "a:1,b:2"
    assert bot.runtime_config.gate_rewards_map() == {"a": 1, "b": 2}
    assert _last_send(interaction).kwargs.get("ephemeral") is True

    await _cleanup(repo)


async def test_config_allowed_maps_sets_value_and_refreshes():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot(settings, repo)
    interaction = _fake_interaction()

    await _config_sub(bot, "allowed-maps").callback(interaction, value="1-1,2-2,3-3")

    assert await repo.get_runtime_config("allowed_maps") == "1-1,2-2,3-3"
    assert bot.runtime_config.allowed_maps_list == ["1-1", "2-2", "3-3"]

    await _cleanup(repo)


async def test_config_voice_roles_sets_value_and_refreshes():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot(settings, repo)
    interaction = _fake_interaction()

    await _config_sub(bot, "voice-roles").callback(interaction, value="x:1,y:2")

    assert await repo.get_runtime_config("voice_achievement_roles") == "x:1,y:2"
    assert bot.runtime_config.voice_role_map() == {"x": 1, "y": 2}

    await _cleanup(repo)


async def test_config_reminder_time_sets_value_and_refreshes():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot(settings, repo)
    interaction = _fake_interaction()

    await _config_sub(bot, "reminder-time").callback(interaction, value="20:15")

    assert await repo.get_runtime_config("reminder_time") == "20:15"
    assert bot.runtime_config.reminder_hm() == (20, 15)

    await _cleanup(repo)


async def test_config_gate_delete_delay_sets_value_verbatim_and_refreshes():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot(settings, repo)
    interaction = _fake_interaction()

    await _config_sub(bot, "gate-delete-delay").callback(interaction, value="2m")

    assert await repo.get_runtime_config("gate_message_delete_delay") == "2m"
    assert bot.runtime_config.gate_delete_delay_seconds == 120

    await _cleanup(repo)


async def test_config_gate_delete_delay_rejects_invalid_duration_no_write():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot(settings, repo)
    interaction = _fake_interaction()

    await _config_sub(bot, "gate-delete-delay").callback(interaction, value="banana")

    assert await repo.get_runtime_config("gate_message_delete_delay") is None
    assert "Ungültige Dauer" in _sent_text(interaction)
    assert _last_send(interaction).kwargs.get("ephemeral") is True

    await _cleanup(repo)


async def test_config_content_setter_non_admin_refused_no_write():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot(settings, repo)
    interaction = _fake_interaction(user=_non_admin())

    await _config_sub(bot, "gate-rewards").callback(interaction, value="a:1,b:2")

    assert "Berechtigung" in _sent_text(interaction)
    assert await repo.get_runtime_config("gate_rewards") is None

    await _cleanup(repo)


# ── 5. /config show ─────────────────────────────────────────────────────────


async def test_config_show_includes_overridden_key_and_db_value():
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=555)
    bot = await _bot(settings, repo)
    await repo.set_runtime_config("gate_stats_channel_id", "999")
    await bot.runtime_config.refresh(repo)
    interaction = _fake_interaction()

    await _config_sub(bot, "show").callback(interaction)

    text = _sent_text(interaction)
    assert "gate_stats_channel_id" in text
    assert "999" in text

    await _cleanup(repo)


async def test_config_show_lists_non_overridden_key_at_env_default():
    repo = await _flatfile_repo()
    settings = _settings(welcome_channel_id=222)
    bot = await _bot(settings, repo)
    interaction = _fake_interaction()

    await _config_sub(bot, "show").callback(interaction)

    text = _sent_text(interaction)
    assert "welcome_channel_id" in text
    assert "222" in text

    await _cleanup(repo)


async def test_config_show_is_ephemeral():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot(settings, repo)
    interaction = _fake_interaction()

    await _config_sub(bot, "show").callback(interaction)

    assert _last_send(interaction).kwargs.get("ephemeral") is True

    await _cleanup(repo)


async def test_config_show_never_leaks_token_or_db_url():
    # show iterates OVERRIDABLE_KEYS only; token / db-url are non-overridable and
    # must never appear in the operator-facing output.
    repo = await _flatfile_repo()
    settings = _settings(discord_token="SUPER_SECRET_TOKEN_XYZ",
                         database_url="postgres://user:pw@host/secretdb")
    bot = await _bot(settings, repo)
    interaction = _fake_interaction()

    await _config_sub(bot, "show").callback(interaction)

    text = _sent_text(interaction)
    assert "SUPER_SECRET_TOKEN_XYZ" not in text
    assert "secretdb" not in text
    assert "discord_token" not in text
    assert "database_url" not in text

    await _cleanup(repo)


async def test_config_show_non_admin_refused():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot(settings, repo)
    interaction = _fake_interaction(user=_non_admin())

    await _config_sub(bot, "show").callback(interaction)

    assert "Berechtigung" in _sent_text(interaction)

    await _cleanup(repo)


# ── 6. /config reset key:<Choice over OVERRIDABLE_KEYS> ─────────────────────


async def test_config_reset_reverts_override_to_env_default():
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=555)
    bot = await _bot(settings, repo)
    await repo.set_runtime_config("gate_stats_channel_id", "999")
    await bot.runtime_config.refresh(repo)
    assert bot.runtime_config.gate_stats_channel_id == 999  # override active
    interaction = _fake_interaction()

    await _config_sub(bot, "reset").callback(interaction, key="gate_stats_channel_id")

    assert await repo.get_runtime_config("gate_stats_channel_id") is None
    assert bot.runtime_config.gate_stats_channel_id == 555  # back to .env base
    assert _last_send(interaction).kwargs.get("ephemeral") is True

    await _cleanup(repo)


async def test_config_reset_key_choices_are_exactly_overridable_keys():
    # The `key` param is a Choice enumerated over OVERRIDABLE_KEYS, so an
    # env-authoritative key (admin_role_id / discord_token) can never be reset.
    repo = await _flatfile_repo()
    settings = _settings()
    bot = await _bot(settings, repo)

    reset = _config_sub(bot, "reset")
    param = next(p for p in reset.parameters if p.name == "key")
    choice_values = {c.value for c in param.choices}
    assert choice_values == set(OVERRIDABLE_KEYS)
    assert "admin_role_id" not in choice_values
    assert "discord_token" not in choice_values

    await _cleanup(repo)


async def test_config_reset_non_admin_refused():
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=555)
    bot = await _bot(settings, repo)
    await repo.set_runtime_config("gate_stats_channel_id", "999")
    await bot.runtime_config.refresh(repo)
    interaction = _fake_interaction(user=_non_admin())

    await _config_sub(bot, "reset").callback(interaction, key="gate_stats_channel_id")

    assert "Berechtigung" in _sent_text(interaction)
    assert await repo.get_runtime_config("gate_stats_channel_id") == "999"

    await _cleanup(repo)


# ── 7. wiring ───────────────────────────────────────────────────────────────


async def test_register_config_commands_entrypoint_exists():
    import n3x_bot.config_commands as ccmod
    assert callable(getattr(ccmod, "register_config_commands", None))


async def test_build_bot_registers_config_group_on_tree():
    repo = await _flatfile_repo()
    settings = _settings()

    bot = build_bot(settings, repo)  # build_bot alone must wire the slash group

    assert bot.get_command("config") is None
    assert isinstance(bot.tree.get_command("config"), app_commands.Group)

    await _cleanup(repo)
