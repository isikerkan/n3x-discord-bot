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
    expected = {"list", "show", "set", "reset", "reset-all"}
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
    assert str(TOTAL_ACHIEVEMENTS) in text        # resolver total (83)
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

    assert "84" in _sent_text(interaction)  # 83 seeds + 1 custom row

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


# ── 3. /achievement set — SEEDING rule + validations ────────────────────────


async def test_set_on_empty_table_seeds_all_defaults_then_adds_new_def():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    interaction = _fake_interaction()

    await _ach_sub(bot, "set").callback(
        interaction, id="custom_new", title="Neu", threshold=5,
        category="voice", metric="voice_seconds")

    rows = await repo.all_achievement_defs()
    assert len(rows) == TOTAL_ACHIEVEMENTS + 1     # 83 seeds + the new one
    assert bot.achievement_defs.total == TOTAL_ACHIEVEMENTS + 1
    assert bot.achievement_defs.by_id("custom_new") is not None
    # the seed guarantees the other 82 survive rather than being shadowed.
    assert bot.achievement_defs.by_id("voice_3600") is not None

    await _cleanup(repo)


async def test_set_on_empty_table_editing_existing_id_stays_at_baseline():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    interaction = _fake_interaction()

    await _ach_sub(bot, "set").callback(
        interaction, id="voice_3600", title="Rookie Talker", threshold=9999,
        category="voice", metric="voice_seconds")

    rows = await repo.all_achievement_defs()
    assert len(rows) == TOTAL_ACHIEVEMENTS         # override, not a 84th row
    assert bot.achievement_defs.total == TOTAL_ACHIEVEMENTS
    assert bot.achievement_defs.by_id("voice_3600").threshold == 9999

    await _cleanup(repo)


async def test_set_on_non_empty_table_is_plain_upsert_no_reseed():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    # table already non-empty (one custom row) -> set must NOT re-seed the 83.
    await repo.set_achievement_def("existing", category="voice",
                                   metric="voice_seconds", threshold=1,
                                   title="Existing", secret=False)
    await bot.achievement_defs.refresh(repo)
    interaction = _fake_interaction()

    await _ach_sub(bot, "set").callback(
        interaction, id="another", title="Zwei", threshold=2,
        category="voice", metric="voice_seconds")

    rows = await repo.all_achievement_defs()
    assert len(rows) == 2                          # no 83-row reseed happened
    assert bot.achievement_defs.total == 2

    await _cleanup(repo)


async def test_set_writes_def_and_refreshes_resolver():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    interaction = _fake_interaction()

    await _ach_sub(bot, "set").callback(
        interaction, id="custom_new", title="Neu", threshold=42,
        category="message", metric="messages")

    stored = await repo.get_achievement_def("custom_new")
    assert stored is not None
    assert stored["threshold"] == 42
    ach = bot.achievement_defs.by_id("custom_new")
    assert ach is not None and ach.threshold == 42

    await _cleanup(repo)


async def test_set_accepts_valid_hex_color():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    interaction = _fake_interaction()

    await _ach_sub(bot, "set").callback(
        interaction, id="custom_new", title="Neu", threshold=5,
        category="voice", metric="voice_seconds", color="#AABBCC")

    assert bot.achievement_defs.by_id("custom_new").color == "#AABBCC"

    await _cleanup(repo)


async def test_set_rejects_invalid_color_no_write():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    interaction = _fake_interaction()

    await _ach_sub(bot, "set").callback(
        interaction, id="custom_new", title="Neu", threshold=5,
        category="voice", metric="voice_seconds", color="not-a-color")

    assert await repo.all_achievement_defs() == []          # no write at all
    assert "❌" in _sent_text(interaction)
    assert _last_send(interaction).kwargs.get("ephemeral") is True

    await _cleanup(repo)


async def test_set_rejects_threshold_below_one_no_write():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    interaction = _fake_interaction()

    await _ach_sub(bot, "set").callback(
        interaction, id="custom_new", title="Neu", threshold=0,
        category="voice", metric="voice_seconds")

    assert await repo.all_achievement_defs() == []
    assert "❌" in _sent_text(interaction)

    await _cleanup(repo)


async def test_set_confirms_ephemerally():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    interaction = _fake_interaction()

    await _ach_sub(bot, "set").callback(
        interaction, id="custom_new", title="Neu", threshold=5,
        category="voice", metric="voice_seconds")

    assert _last_send(interaction).kwargs.get("ephemeral") is True

    await _cleanup(repo)


async def test_set_non_admin_refused_no_write():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    interaction = _fake_interaction(user=_non_admin())

    await _ach_sub(bot, "set").callback(
        interaction, id="custom_new", title="Neu", threshold=5,
        category="voice", metric="voice_seconds")

    assert "Berechtigung" in _sent_text(interaction)
    assert await repo.all_achievement_defs() == []
    assert bot.achievement_defs.total == TOTAL_ACHIEVEMENTS  # resolver untouched

    await _cleanup(repo)


# ── 4. /achievement reset id:<str> ──────────────────────────────────────────


async def test_reset_custom_id_deletes_the_row():
    # A custom (non-code-default) id resets by DELETING its row; the seeded
    # code defaults are left untouched.
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    await _seed_code_defaults(repo)
    await repo.set_achievement_def("custom_new", category="voice",
                                   metric="voice_seconds", threshold=5,
                                   title="Neu", secret=False)
    await bot.achievement_defs.refresh(repo)
    interaction = _fake_interaction()

    await _ach_sub(bot, "reset").callback(interaction, id="custom_new")

    assert await repo.get_achievement_def("custom_new") is None
    assert bot.achievement_defs.by_id("custom_new") is None
    assert len(await repo.all_achievement_defs()) == TOTAL_ACHIEVEMENTS
    assert _last_send(interaction).kwargs.get("ephemeral") is True

    await _cleanup(repo)


async def test_reset_code_default_id_reverts_override():
    # A code-default id resets by RE-UPSERTING its original values (reverting
    # any override) while keeping the row and the rest of the seeded table.
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    await _seed_code_defaults(repo)
    await repo.set_achievement_def("a_5", category="gate", metric="gate_a",
                                   threshold=10, title="Alpha Bronze Pilot",
                                   secret=False)
    await bot.achievement_defs.refresh(repo)
    assert bot.achievement_defs.by_id("a_5").threshold == 10  # override in place
    interaction = _fake_interaction()

    await _ach_sub(bot, "reset").callback(interaction, id="a_5")

    assert bot.achievement_defs.by_id("a_5").threshold == 5   # back to default
    assert len(await repo.all_achievement_defs()) >= TOTAL_ACHIEVEMENTS  # still seeded
    assert _last_send(interaction).kwargs.get("ephemeral") is True

    await _cleanup(repo)


async def test_reset_last_custom_row_falls_back_to_code_defaults():
    # Deleting the last CUSTOM row empties the table, so the resolver falls back
    # to the code defaults.
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    await repo.set_achievement_def("only_one", category="voice",
                                   metric="voice_seconds", threshold=5,
                                   title="Solo", secret=False)
    await bot.achievement_defs.refresh(repo)
    assert bot.achievement_defs.total == 1
    interaction = _fake_interaction()

    await _ach_sub(bot, "reset").callback(interaction, id="only_one")

    assert await repo.all_achievement_defs() == []
    assert bot.achievement_defs.total == TOTAL_ACHIEVEMENTS  # back to code defaults

    await _cleanup(repo)


async def test_reset_unknown_id_reports_german_error():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    interaction = _fake_interaction()

    await _ach_sub(bot, "reset").callback(interaction, id="does_not_exist")

    assert "❌" in _sent_text(interaction)
    assert _last_send(interaction).kwargs.get("ephemeral") is True

    await _cleanup(repo)


async def test_reset_non_admin_refused_no_write():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    await repo.set_achievement_def("only_one", category="voice",
                                   metric="voice_seconds", threshold=5,
                                   title="Solo", secret=False)
    await bot.achievement_defs.refresh(repo)
    interaction = _fake_interaction(user=_non_admin())

    await _ach_sub(bot, "reset").callback(interaction, id="only_one")

    assert "Berechtigung" in _sent_text(interaction)
    assert await repo.get_achievement_def("only_one") is not None

    await _cleanup(repo)


async def test_reset_id_param_has_autocomplete():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    await repo.set_achievement_def("voice_custom", category="voice",
                                   metric="voice_seconds", threshold=5,
                                   title="Solo", secret=False)
    await bot.achievement_defs.refresh(repo)
    ac = _ach_sub(bot, "reset")._params["id"].autocomplete
    assert ac is not None
    interaction = _fake_interaction()

    choices = await ac(interaction, "voice")

    assert all(isinstance(c, app_commands.Choice) for c in choices)
    assert "voice_custom" in {c.value for c in choices}

    await _cleanup(repo)


# ── 5. /achievement reset-all ───────────────────────────────────────────────


async def test_reset_all_wipes_every_row_back_to_defaults():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    await _seed_code_defaults(repo)
    await repo.set_achievement_def("voice_9999999", category="voice",
                                   metric="voice_seconds", threshold=9999999,
                                   title="Test Legende", secret=False)
    await bot.achievement_defs.refresh(repo)
    assert bot.achievement_defs.total == 84
    interaction = _fake_interaction()

    await _ach_sub(bot, "reset-all").callback(interaction)

    assert await repo.all_achievement_defs() == []
    assert bot.achievement_defs.total == TOTAL_ACHIEVEMENTS  # code defaults
    assert _last_send(interaction).kwargs.get("ephemeral") is True

    await _cleanup(repo)


async def test_reset_all_non_admin_refused_no_write():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    await _seed_code_defaults(repo)
    await bot.achievement_defs.refresh(repo)
    interaction = _fake_interaction(user=_non_admin())

    await _ach_sub(bot, "reset-all").callback(interaction)

    assert "Berechtigung" in _sent_text(interaction)
    assert len(await repo.all_achievement_defs()) == TOTAL_ACHIEVEMENTS  # intact

    await _cleanup(repo)
