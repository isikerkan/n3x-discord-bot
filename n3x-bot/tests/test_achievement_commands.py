"""RED-phase specs for de-hardcode Phase 2b, Part A: the `/achievement` admin
editor slash group.

Phase 2a (merged) built the ``achievement_defs`` table, the repo CRUD methods
(``set_achievement_def`` / ``get_achievement_def`` / ``delete_achievement_def`` /
``all_achievement_defs``) and the total-replacement resolver
``bot.achievement_defs`` (``AchievementDefs``). Phase 2b adds the WRITE surface:
a slash-ONLY ``app_commands.Group`` named ``achievement`` that edits the table
and refreshes the live resolver, mirroring ``n3x_bot/config_commands.py``.

Pinned surface (to be implemented downstream in
``n3x_bot/achievement_commands.py`` and wired by ``build_bot`` via
``register_achievement_def_commands(bot, repo, settings)``), every subcommand
admin-gated with an ephemeral "❌ Keine Berechtigung." refusal and NO write:

    /achievement list                                  -> resolver + DB summary
    /achievement show  id:<str>                        -> resolver detail (+ AC)
    /achievement set   id title threshold category metric [secret] [color]
    /achievement reset id:<str>                        -> delete one row (+ AC)
    /achievement reset-all                             -> wipe every row

CRITICAL SEEDING RULE (pinned): the resolver is total-replacement, so on an
EMPTY table ``set`` must FIRST seed all code defaults (``ACHIEVEMENTS``) and THEN
upsert the given def, so one edit never shadows the other 82. On a NON-empty
table it is a plain single upsert (no re-seed).

New symbols are imported LAZILY inside test bodies / helpers so the RED state is
a clean per-test failure rather than a collection-time ImportError.
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from discord import app_commands

from n3x_bot.achievements import ACHIEVEMENTS, TOTAL_ACHIEVEMENTS
from n3x_bot.bot import build_bot
from n3x_bot.config import Settings
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


async def _seed_code_defaults(repo) -> None:
    """Persist the code-default achievements into ``achievement_defs``."""
    for a in ACHIEVEMENTS:
        await repo.set_achievement_def(
            a.id, category=a.category, metric=a.metric, threshold=a.threshold,
            title=a.title, secret=a.secret, color=getattr(a, "color", None))


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


def _sent_text(interaction) -> str:
    """All text the callback sent the caller (response + any followup)."""
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
    calls = (list(interaction.response.send_message.await_args_list)
             + list(interaction.followup.send.await_args_list))
    assert calls, "the callback must reply to the caller"
    return calls[-1]


def _ach_group(bot):
    assert bot.get_command("achievement") is None, \
        "`achievement` must be slash-only (absent from the prefix registry)"
    group = bot.tree.get_command("achievement")
    assert isinstance(group, app_commands.Group), \
        "`achievement` must be an app_commands.Group on bot.tree"
    return group


def _ach_sub(bot, name):
    sub = _ach_group(bot).get_command(name)
    assert sub is not None, f"/achievement {name} subcommand must be registered"
    return sub


# ── 0. group presence / prefix absence / wiring ─────────────────────────────


async def test_achievement_is_app_group_not_prefix_command():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)

    assert bot.get_command("achievement") is None
    assert isinstance(bot.tree.get_command("achievement"), app_commands.Group)

    await _cleanup(repo)


async def test_achievement_group_exposes_expected_subcommands():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)

    names = {c.name for c in _ach_group(bot).commands}
    expected = {"list", "show"}
    assert not ({"set", "reset", "reset-all"} & names)
    assert expected <= names

    await _cleanup(repo)


async def test_register_achievement_def_commands_entrypoint_exists():
    import n3x_bot.achievement_commands as acmod
    assert callable(getattr(acmod, "register_achievement_def_commands", None))


async def test_register_achievement_def_commands_is_idempotent():
    from n3x_bot.achievement_commands import register_achievement_def_commands
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    register_achievement_def_commands(bot, repo, settings)  # re-register: no raise

    assert isinstance(bot.tree.get_command("achievement"), app_commands.Group)

    await _cleanup(repo)


async def test_build_bot_registers_achievement_group_on_tree():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)  # build_bot alone must wire the group

    assert bot.get_command("achievement") is None
    assert isinstance(bot.tree.get_command("achievement"), app_commands.Group)

    await _cleanup(repo)


# ── 1. /achievement list ────────────────────────────────────────────────────


async def test_list_empty_table_reports_defaults_active():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    interaction = _fake_interaction()

    await _ach_sub(bot, "list").callback(interaction)

    text = _sent_text(interaction)
    assert str(TOTAL_ACHIEVEMENTS) in text        # resolver total (92)
    assert "Code-Defaults aktiv" in text          # 0 DB rows -> defaults hint

    await _cleanup(repo)


async def test_list_with_db_rows_reports_resolver_total():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    await _seed_code_defaults(repo)
    await repo.set_achievement_def("voice_9999999", category="voice",
                                   metric="voice_seconds", threshold=9999999,
                                   title="Test Legende", secret=False)
    await bot.achievement_defs.refresh(repo)
    interaction = _fake_interaction()

    await _ach_sub(bot, "list").callback(interaction)

    assert "93" in _sent_text(interaction)  # 92 seeds + 1 custom row

    await _cleanup(repo)


async def test_list_shows_drift_when_code_has_ids_db_lacks():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    # Seed every code default EXCEPT the first, so the DB is non-empty yet the
    # code carries an id the DB lacks -> the list must flag the drift.
    for a in ACHIEVEMENTS[1:]:
        await repo.set_achievement_def(
            a.id, category=a.category, metric=a.metric, threshold=a.threshold,
            title=a.title, secret=a.secret, color=getattr(a, "color", None))
    await bot.achievement_defs.refresh(repo)
    interaction = _fake_interaction()

    await _ach_sub(bot, "list").callback(interaction)

    assert "nicht in der DB" in _sent_text(interaction)

    await _cleanup(repo)


async def test_list_is_ephemeral():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    interaction = _fake_interaction()

    await _ach_sub(bot, "list").callback(interaction)

    assert _last_send(interaction).kwargs.get("ephemeral") is True

    await _cleanup(repo)


async def test_list_non_admin_refused():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    interaction = _fake_interaction(user=_non_admin())

    await _ach_sub(bot, "list").callback(interaction)

    assert "Berechtigung" in _sent_text(interaction)
    assert _last_send(interaction).kwargs.get("ephemeral") is True

    await _cleanup(repo)


# ── 2. /achievement show id:<str> ───────────────────────────────────────────


async def test_show_returns_detail_from_resolver():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)  # resolver holds code defaults
    interaction = _fake_interaction()

    await _ach_sub(bot, "show").callback(interaction, id="voice_3600")

    text = _sent_text(interaction)
    assert "voice_3600" in text
    assert "3600" in text                 # threshold surfaced
    assert "Rookie Talker" in text        # title surfaced

    await _cleanup(repo)


async def test_show_reflects_a_db_override_via_resolver():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    await repo.set_achievement_def("only_one", category="voice",
                                   metric="voice_seconds", threshold=7,
                                   title="Solo", secret=False, color="#ABCDEF")
    await bot.achievement_defs.refresh(repo)
    interaction = _fake_interaction()

    await _ach_sub(bot, "show").callback(interaction, id="only_one")

    text = _sent_text(interaction)
    assert "only_one" in text
    assert "Solo" in text
    assert "#ABCDEF" in text

    await _cleanup(repo)


async def test_show_unknown_id_reports_german_error_ephemeral():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    interaction = _fake_interaction()

    await _ach_sub(bot, "show").callback(interaction, id="does_not_exist")

    assert "❌" in _sent_text(interaction)
    assert _last_send(interaction).kwargs.get("ephemeral") is True

    await _cleanup(repo)


async def test_show_is_ephemeral():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    interaction = _fake_interaction()

    await _ach_sub(bot, "show").callback(interaction, id="voice_3600")

    assert _last_send(interaction).kwargs.get("ephemeral") is True

    await _cleanup(repo)


async def test_show_non_admin_refused():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    interaction = _fake_interaction(user=_non_admin())

    await _ach_sub(bot, "show").callback(interaction, id="voice_3600")

    assert "Berechtigung" in _sent_text(interaction)

    await _cleanup(repo)


async def test_show_id_param_has_substring_filtered_autocomplete():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)  # resolver holds code defaults
    ac = _ach_sub(bot, "show")._params["id"].autocomplete
    assert ac is not None, "the `id` param must expose an autocomplete callback"
    interaction = _fake_interaction()

    choices = await ac(interaction, "voice")

    assert choices
    assert all(isinstance(c, app_commands.Choice) for c in choices)
    assert len(choices) <= 25
    assert all("voice" in c.value for c in choices)          # substring filtered
    assert "voice_3600" in {c.value for c in choices}        # a real resolver id

    await _cleanup(repo)
