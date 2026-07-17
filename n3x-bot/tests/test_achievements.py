"""RED tests for Pass A of the achievements feature.

Scope (Pass A ONLY): declarative definitions + a pure detection engine +
metric resolution + detect/record + the `/erfolge` progress prefix command.
NO image cards, NO auto-posting, NO roles, NO overview UI, NO sync command.

Everything new (`n3x_bot.achievements` and the config fields) is referenced
lazily *inside* each test body so a missing module/attribute surfaces as a
runtime failure of that individual test (the correct RED "missing symbol"
signal) rather than a collection-time ImportError that would break the file.

Assumptions pinned here (flag for the Architect — see report):
  * New module: ``n3x_bot.achievements`` exposing:
      - ``ACHIEVEMENTS: list[Achievement]`` and ``TOTAL_ACHIEVEMENTS: int``
      - ``Achievement`` — object with ``.id .category .metric .threshold
        .title .secret`` attributes (a frozen dataclass is expected).
      - ``newly_unlocked(defs_for_metric, value, already) -> set[str]`` (pure)
      - ``async user_metric_value(repo, discord_id, metric) -> int``
      - ``async check_achievements(repo, discord_id, metric) -> list``
      - ``register_achievement_commands(bot, repo, settings)`` (idempotent),
        wiring a prefix command named ``erfolge``.
  * Gate achievement id scheme (ported EXACTLY from v3): ``f"{gtype}_{thr}"``
    for gtype in a,b,c,d and thr in {5,10,25,50,100,250,500,1000}.
  * Specials: ``total_1``/``total_50``/``total_100`` and ``millionaire``.
  * Tracker ids: ``voice_<thr>``, ``msg_<thr>``, ``streak_<thr>``,
    ``night_<thr>``, ``reaction_<thr>``.
  * Category values: gate / voice / message / streak / night / reaction
    (total & millionaire category deliberately NOT pinned here — flagged).
  * Metric-name scheme (mine — flag): voice→"voice_seconds", message→
    "messages", reaction→"reactions", streak→"streak" (get_streak.max_streak),
    night→"night" (get_night.night_count), gate tiers→"gate_a".."gate_d"
    (per-type entry count), total specials→"gate_total" (all gate entries),
    millionaire→"gate_cost_total" (sum of all gate costs).
  * ``/erfolge`` renders the caller's progress as "<count>/59" (v3 format).
"""

import importlib
import os
import re as _re
import tempfile

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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
    return Settings(**kwargs)


async def _flatfile_repo() -> JsonRepository:
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    repo = JsonRepository(path)
    await repo.connect()
    await seed_defaults(repo)
    return repo


def _ach():
    """Lazy import so a missing module fails the test (not collection)."""
    return importlib.import_module("n3x_bot.achievements")


def _by_id(module, aid):
    return next(a for a in module.ACHIEVEMENTS if a.id == aid)


def _metric_defs(module, metric):
    return [a for a in module.ACHIEVEMENTS if a.metric == metric]


# ── config fields (Pass A: fields only, not wired to behaviour) ────────────

def test_milestone_channel_id_defaults_to_zero():
    assert _settings().milestone_channel_id == 0


def test_overview_channel_id_defaults_to_zero():
    assert _settings().overview_channel_id == 0


def test_voice_achievement_roles_defaults_to_empty_string():
    assert _settings().voice_achievement_roles == ""


def test_milestone_channel_id_read_from_env(monkeypatch):
    monkeypatch.setenv("MILESTONE_CHANNEL_ID", "1523988483989962844")
    s = Settings(discord_token="tok", target_role_id=1, welcome_channel_id=2,
                 reminder_channel_id=3, _env_file=None)
    assert s.milestone_channel_id == 1523988483989962844


def test_overview_channel_id_read_from_env(monkeypatch):
    monkeypatch.setenv("OVERVIEW_CHANNEL_ID", "42")
    s = Settings(discord_token="tok", target_role_id=1, welcome_channel_id=2,
                 reminder_channel_id=3, _env_file=None)
    assert s.overview_channel_id == 42


# ── definitions ────────────────────────────────────────────────────────────

def test_total_achievements_constant_is_83():
    # Grew 59 -> 83 with the E/Z/K gates: +8 tiers each for gate_e/z/k (+24).
    assert _ach().TOTAL_ACHIEVEMENTS == 83


def test_there_are_exactly_83_definitions():
    assert len(_ach().ACHIEVEMENTS) == 83


def test_all_definition_ids_are_unique():
    ids = [a.id for a in _ach().ACHIEVEMENTS]
    assert len(ids) == len(set(ids))


def test_every_definition_exposes_the_required_fields():
    for a in _ach().ACHIEVEMENTS:
        assert isinstance(a.id, str)
        assert isinstance(a.category, str)
        assert isinstance(a.metric, str)
        assert isinstance(a.threshold, int)
        assert isinstance(a.title, str)
        assert isinstance(a.secret, bool)


def test_expected_sample_ids_are_present():
    ids = {a.id for a in _ach().ACHIEVEMENTS}
    expected = {
        "a_5", "d_1000", "total_1", "total_50", "total_100", "millionaire",
        "voice_3600", "voice_3600000", "msg_1000", "msg_50000",
        "streak_7", "streak_365", "night_10", "night_100",
        "reaction_100", "reaction_5000",
    }
    assert expected <= ids


def test_every_gate_type_has_all_eight_tiers():
    ids = {a.id for a in _ach().ACHIEVEMENTS}
    for gtype in ("a", "b", "c", "d"):
        for thr in (5, 10, 25, 50, 100, 250, 500, 1000):
            assert f"{gtype}_{thr}" in ids


def test_thresholds_match_the_id_suffix():
    m = _ach()
    assert _by_id(m, "voice_3600").threshold == 3600
    assert _by_id(m, "streak_365").threshold == 365
    assert _by_id(m, "night_10").threshold == 10
    assert _by_id(m, "reaction_100").threshold == 100
    assert _by_id(m, "a_5").threshold == 5
    assert _by_id(m, "d_1000").threshold == 1000


def test_titles_are_ported_from_v3():
    m = _ach()
    assert _by_id(m, "voice_3600").title == "Rookie Talker"
    assert _by_id(m, "msg_1000").title == "Tastatur-Krieger"
    assert _by_id(m, "streak_7").title == "Treuer Soldat"
    assert _by_id(m, "night_10").title == "Nachteule"
    assert _by_id(m, "reaction_100").title == "Emoji-Fan"


# ── secret flag ────────────────────────────────────────────────────────────

def test_message_category_achievements_are_secret():
    msg_defs = [a for a in _ach().ACHIEVEMENTS if a.category == "message"]
    assert msg_defs and all(a.secret for a in msg_defs)


def test_reaction_category_achievements_are_secret():
    rct_defs = [a for a in _ach().ACHIEVEMENTS if a.category == "reaction"]
    assert rct_defs and all(a.secret for a in rct_defs)


def test_gate_voice_streak_night_are_not_secret():
    non_secret = [a for a in _ach().ACHIEVEMENTS
                  if a.category in ("gate", "voice", "streak", "night")]
    assert non_secret and not any(a.secret for a in non_secret)


def test_exactly_eight_definitions_are_secret():
    assert sum(1 for a in _ach().ACHIEVEMENTS if a.secret) == 8


# ── metric-name scheme ─────────────────────────────────────────────────────

def test_tracker_metric_names_map_to_repo_sources():
    m = _ach()
    assert _by_id(m, "voice_3600").metric == "voice_seconds"
    assert _by_id(m, "msg_1000").metric == "messages"
    assert _by_id(m, "reaction_100").metric == "reactions"
    assert _by_id(m, "streak_7").metric == "streak"
    assert _by_id(m, "night_10").metric == "night"


def test_gate_metric_names_map_to_gate_sources():
    m = _ach()
    assert _by_id(m, "a_5").metric == "gate_a"
    assert _by_id(m, "d_1000").metric == "gate_d"
    assert _by_id(m, "total_50").metric == "gate_total"
    assert _by_id(m, "millionaire").metric == "gate_cost_total"


# ── pure detection engine ──────────────────────────────────────────────────

def test_newly_unlocked_crosses_multiple_tiers_at_once():
    m = _ach()
    streak_defs = _metric_defs(m, "streak")
    got = m.newly_unlocked(streak_defs, 30, set())
    assert got == {"streak_7", "streak_14", "streak_30"}


def test_newly_unlocked_excludes_already_unlocked():
    m = _ach()
    streak_defs = _metric_defs(m, "streak")
    got = m.newly_unlocked(streak_defs, 30, {"streak_7", "streak_14", "streak_30"})
    assert got == set()


def test_newly_unlocked_returns_nothing_below_lowest_threshold():
    m = _ach()
    streak_defs = _metric_defs(m, "streak")
    assert m.newly_unlocked(streak_defs, 6, set()) == set()


def test_newly_unlocked_returns_only_the_newly_crossed_tier():
    m = _ach()
    streak_defs = _metric_defs(m, "streak")
    got = m.newly_unlocked(streak_defs, 14, {"streak_7"})
    assert got == {"streak_14"}


# ── metric resolution (async, real JSON repo) ──────────────────────────────

async def test_user_metric_value_reads_message_counter():
    repo = await _flatfile_repo()
    await repo.add_activity(7, "messages", 1500)
    assert await _ach().user_metric_value(repo, 7, "messages") == 1500
    await repo.close()


async def test_user_metric_value_reads_voice_seconds():
    repo = await _flatfile_repo()
    await repo.add_activity(7, "voice_seconds", 3600)
    assert await _ach().user_metric_value(repo, 7, "voice_seconds") == 3600
    await repo.close()


async def test_user_metric_value_reads_reactions():
    repo = await _flatfile_repo()
    await repo.add_activity(7, "reactions", 250)
    assert await _ach().user_metric_value(repo, 7, "reactions") == 250
    await repo.close()


async def test_user_metric_value_streak_uses_max_streak():
    repo = await _flatfile_repo()
    await repo.set_streak(7, current_streak=3, last_active_date="2026-07-13",
                          max_streak=40)
    assert await _ach().user_metric_value(repo, 7, "streak") == 40
    await repo.close()


async def test_user_metric_value_night_uses_night_count():
    repo = await _flatfile_repo()
    await repo.set_night(7, night_count=12, last_night_date="2026-07-13")
    assert await _ach().user_metric_value(repo, 7, "night") == 12
    await repo.close()


async def test_user_metric_value_defaults_to_zero_without_streak():
    repo = await _flatfile_repo()
    assert await _ach().user_metric_value(repo, 7, "streak") == 0
    await repo.close()


async def test_user_metric_value_gate_type_counts_entries_of_that_type():
    repo = await _flatfile_repo()
    await repo.add_gate_entry("a", 100, 7, "u")
    await repo.add_gate_entry("a", 200, 7, "u")
    await repo.add_gate_entry("b", 300, 7, "u")
    assert await _ach().user_metric_value(repo, 7, "gate_a") == 2
    await repo.close()


async def test_user_metric_value_gate_total_sums_all_gate_counts():
    repo = await _flatfile_repo()
    await repo.add_gate_entry("a", 100, 7, "u")
    await repo.add_gate_entry("b", 200, 7, "u")
    await repo.add_gate_entry("c", 300, 7, "u")
    assert await _ach().user_metric_value(repo, 7, "gate_total") == 3
    await repo.close()


async def test_user_metric_value_gate_cost_total_sums_all_costs():
    repo = await _flatfile_repo()
    await repo.add_gate_entry("a", 400000, 7, "u")
    await repo.add_gate_entry("b", 600000, 7, "u")
    assert await _ach().user_metric_value(repo, 7, "gate_cost_total") == 1000000
    await repo.close()


# ── detect + record ────────────────────────────────────────────────────────

async def test_check_achievements_records_newly_unlocked():
    repo = await _flatfile_repo()
    await repo.add_activity(7, "messages", 1500)
    unlocked = await _ach().check_achievements(repo, 7, "messages")
    got_ids = {a.id for a in unlocked}
    assert "msg_1000" in got_ids
    assert await repo.has_achievement(7, "msg_1000") is True
    await repo.close()


async def test_check_achievements_is_idempotent_on_second_call():
    repo = await _flatfile_repo()
    await repo.add_activity(7, "messages", 1500)
    await _ach().check_achievements(repo, 7, "messages")
    again = await _ach().check_achievements(repo, 7, "messages")
    assert again == []
    await repo.close()


async def test_check_achievements_records_all_crossed_streak_tiers():
    repo = await _flatfile_repo()
    await repo.set_streak(7, current_streak=30, last_active_date="2026-07-13",
                          max_streak=30)
    await _ach().check_achievements(repo, 7, "streak")
    unlocked = await repo.get_user_achievements(7)
    assert {"streak_7", "streak_14", "streak_30"} <= unlocked
    await repo.close()


async def test_check_achievements_records_nothing_below_threshold():
    repo = await _flatfile_repo()
    await repo.add_activity(7, "messages", 500)  # below msg_1000
    unlocked = await _ach().check_achievements(repo, 7, "messages")
    assert unlocked == []
    assert await repo.get_user_achievements(7) == set()
    await repo.close()


# ── /erfolge progress command ──────────────────────────────────────────────

def _collect_text(*mocks) -> str:
    """Flatten every send call (positional str args + embed text) into one
    string so the assertion is agnostic to plain-text-vs-embed / channel-vs-DM.
    """
    parts: list[str] = []
    for mock in mocks:
        for call in mock.await_args_list:
            for a in call.args:
                if isinstance(a, str):
                    parts.append(a)
            embed = call.kwargs.get("embed")
            if embed is not None:
                parts.append(str(getattr(embed, "title", "") or ""))
                parts.append(str(getattr(embed, "description", "") or ""))
                for field in getattr(embed, "fields", []):
                    parts.append(str(getattr(field, "name", "") or ""))
                    parts.append(str(getattr(field, "value", "") or ""))
    return "\n".join(parts)


async def test_register_achievement_commands_wires_erfolge_app_command():
    # Phase 1: erfolge is slash-ONLY — an app command on the tree, no longer a
    # prefix command in bot.commands.
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    _ach().register_achievement_commands(bot, repo, settings)
    assert bot.get_command("erfolge") is None            # not a prefix command
    assert bot.tree.get_command("erfolge") is not None    # app command on tree
    await repo.close()


async def test_register_achievement_commands_is_idempotent():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    _ach().register_achievement_commands(bot, repo, settings)
    _ach().register_achievement_commands(bot, repo, settings)  # must not raise
    assert bot.tree.get_command("erfolge") is not None
    await repo.close()


async def test_erfolge_reports_unlocked_count_and_total():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    _ach().register_achievement_commands(bot, repo, settings)

    await repo.unlock_achievement(7, "msg_1000")
    await repo.unlock_achievement(7, "voice_3600")

    interaction = MagicMock()
    interaction.user = SimpleNamespace(id=7, display_name="Erkan",
                                       send=AsyncMock(), mention="<@7>")
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    cmd = bot.tree.get_command("erfolge")
    await cmd.callback(interaction)

    text = _collect_text(interaction.response.send_message,
                         interaction.followup.send)
    assert "2/83" in text  # total grew 59 -> 83 with the E/Z/K gates
    await repo.close()


# ── E/Z/K gate milestone achievements ───────────────────────────────────────

def test_every_ezk_gate_type_has_all_eight_tiers():
    ids = {a.id for a in _ach().ACHIEVEMENTS}
    for gtype in ("e", "z", "k"):
        for thr in (5, 10, 25, 50, 100, 250, 500, 1000):
            assert f"{gtype}_{thr}" in ids


def test_ezk_gate_metrics_map_to_gate_sources():
    m = _ach()
    assert _by_id(m, "e_5").metric == "gate_e"
    assert _by_id(m, "z_1000").metric == "gate_z"
    assert _by_id(m, "k_25").metric == "gate_k"


def test_ezk_gate_achievements_are_not_secret():
    ezk = [a for a in _ach().ACHIEVEMENTS
           if a.metric in ("gate_e", "gate_z", "gate_k")]
    assert ezk and not any(a.secret for a in ezk)


def test_ezk_gate_titles_use_gate_display_names():
    m = _ach()
    assert "Epsilon" in _by_id(m, "e_5").title
    assert "Zeta" in _by_id(m, "z_5").title
    assert "Kappa" in _by_id(m, "k_5").title


async def test_user_metric_value_reads_gate_e_count():
    repo = await _flatfile_repo()
    await repo.add_gate_entry("e", 100, 7, "u", drops={"lf4": True})
    await repo.add_gate_entry("e", 200, 7, "u", drops={"lf4": False})
    assert await _ach().user_metric_value(repo, 7, "gate_e") == 2
    await repo.close()


async def test_user_metric_value_gate_total_includes_ezk_entries():
    repo = await _flatfile_repo()
    await repo.add_gate_entry("e", 100, 7, "u", drops={"lf4": True})
    await repo.add_gate_entry("z", 200, 7, "u", drops={"havoc": False})
    await repo.add_gate_entry("k", 300, 7, "u",
                              drops={"hercules": True, "lf4u": True})
    assert await _ach().user_metric_value(repo, 7, "gate_total") == 3
    await repo.close()


async def test_user_metric_value_gate_cost_total_includes_ezk_costs():
    repo = await _flatfile_repo()
    await repo.add_gate_entry("k", 400000, 7, "u",
                              drops={"hercules": True, "lf4u": False})
    await repo.add_gate_entry("e", 600000, 7, "u", drops={"lf4": True})
    assert await _ach().user_metric_value(repo, 7, "gate_cost_total") == 1000000
    await repo.close()


async def test_five_kappa_entries_unlock_k_5_achievement():
    repo = await _flatfile_repo()
    for cost in (500, 600, 700, 800, 900):
        await repo.add_gate_entry("k", cost, 7, "Erkan",
                                  drops={"hercules": True, "lf4u": False})
    unlocked = await _ach().check_achievements(repo, 7, "gate_k")
    assert "k_5" in {a.id for a in unlocked}
    await repo.close()


async def test_five_epsilon_entries_unlock_e_5_achievement():
    repo = await _flatfile_repo()
    for cost in (10, 20, 30, 40, 50):
        await repo.add_gate_entry("e", cost, 7, "Erkan", drops={"lf4": True})
    unlocked = await _ach().check_achievements(repo, 7, "gate_e")
    assert "e_5" in {a.id for a in unlocked}
    await repo.close()


# ── _milestone_line renders E/Z/K gate lines (cards.py) ─────────────────────

def test_milestone_line_renders_ezk_gate_names():
    from n3x_bot.cards import _milestone_line
    m = _ach()
    assert "Epsilon" in _milestone_line(_by_id(m, "e_5"))
    assert "Zeta" in _milestone_line(_by_id(m, "z_10"))
    assert "Kappa" in _milestone_line(_by_id(m, "k_25"))


# ── Phase 2a: Achievement.color field (trailing, default None) ──────────────

def test_achievement_color_field_defaults_to_none():
    m = _ach()
    a = m.Achievement(id="x", category="voice", metric="voice_seconds",
                      threshold=1, title="T", secret=False)
    assert a.color is None


def test_achievement_accepts_explicit_color():
    m = _ach()
    a = m.Achievement(id="x", category="voice", metric="voice_seconds",
                      threshold=1, title="T", secret=False, color="#010203")
    assert a.color == "#010203"


def test_code_default_achievements_have_no_color():
    # The code seed carries no explicit colour; colours stay derived (cards.py).
    for a in _ach().ACHIEVEMENTS:
        assert a.color is None


# ── Phase 2a: check_achievements / recompute optional `defs` param ──────────

async def test_check_achievements_defs_none_matches_default_behaviour():
    repo = await _flatfile_repo()
    await repo.add_activity(7, "messages", 1500)
    unlocked = await _ach().check_achievements(repo, 7, "messages", defs=None)
    assert "msg_1000" in {a.id for a in unlocked}
    await repo.close()


async def test_check_achievements_custom_defs_can_unlock_new_definition():
    m = _ach()
    repo = await _flatfile_repo()
    await repo.add_activity(7, "voice_seconds", 7200000)
    new = m.Achievement(id="voice_7200000", category="voice",
                        metric="voice_seconds", threshold=7200000,
                        title="Test Legende", secret=False)
    unlocked = await m.check_achievements(repo, 7, "voice_seconds", defs=[new])
    assert "voice_7200000" in {a.id for a in unlocked}
    assert await repo.has_achievement(7, "voice_7200000") is True
    await repo.close()


async def test_recompute_user_achievements_accepts_defs_param():
    m = _ach()
    repo = await _flatfile_repo()
    await repo.add_activity(7, "voice_seconds", 7200000)
    new = m.Achievement(id="voice_7200000", category="voice",
                        metric="voice_seconds", threshold=7200000,
                        title="Test Legende", secret=False)
    defs = list(m.ACHIEVEMENTS) + [new]
    newly = await m.recompute_user_achievements(repo, 7, defs=defs)
    assert "voice_7200000" in {a.id for a in newly}
    await repo.close()


# ── /erfolge richer layout (progress bars + LIVE metric values) ─────────────
#
# Approved design pins BEHAVIOUR through the slash callback (the internal split
# — async builder vs pre-fetched dict — stays free for the Architect):
#
#   🏆 Achievements — Erkan          5/83
#   █░░░░░░░░░ 6 %
#   🚀 Gates 1/60  …/ Nächstes: Alpha Bronze Pilot — 3/5 Läufe
#   🎙️ Voice 3/6   …/ Nächstes: Veteran — 82h/100h
#   🔥 Streak 2/6  …/ Nächstes: Monats-Krieger — 18/30 Tage
#   🌙 Nachtaktiv… / 🔒 Secret 1/8  ???
#
# Deterministic seed recipe (see report): unlock the LOWER tiers directly so the
# category "unlocked" count and the "next" milestone are known, while seeding the
# raw metric so the LIVE value toward that milestone is known and independent of
# the owned set:
#   voice  — unlock voice_3600/36000/180000, set voice_seconds=295200 → next is
#            Veteran (360000) with live 295200s = 82h toward 100h.
#   streak — unlock streak_7/14, set max_streak=18 → next Monats-Krieger (30),
#            live 18/30 Tage.
#   gate   — unlock total_1, record 3 gate_a entries → next Alpha Bronze Pilot
#            (a_5), live gate_a count 3/5 Läufe.


def _bar_run(text: str, segments: int = 10) -> bool:
    """True if `text` contains a run of >= `segments` consecutive block/shade
    chars — i.e. a progress bar. Robust to the fill level (round vs int)."""
    longest = 0
    run = 0
    for ch in text:
        if ch in "█░":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return longest >= segments


def _field_by_emoji(embed, emoji: str) -> str:
    """Flattened name+value of the first field whose name contains `emoji`."""
    for f in getattr(embed, "fields", []):
        name = str(getattr(f, "name", "") or "")
        if emoji in name:
            return name + "\n" + str(getattr(f, "value", "") or "")
    return ""


async def _erfolge_embed(uid: int = 7, display_name: str = "Erkan"):
    """Seed nothing extra; caller seeds `repo` first. Returns (repo, invoke)
    where invoke() drives the real /erfolge slash callback and returns the
    Embed the command sent. Behaviour-level pin — internal builder split free."""
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    _ach().register_achievement_commands(bot, repo, settings)

    async def invoke():
        interaction = MagicMock()
        interaction.user = SimpleNamespace(id=uid, display_name=display_name,
                                           send=AsyncMock(), mention=f"<@{uid}>")
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()
        cmd = bot.tree.get_command("erfolge")
        await cmd.callback(interaction)
        for mock in (interaction.response.send_message, interaction.followup.send):
            for call in mock.await_args_list:
                embed = call.kwargs.get("embed")
                if embed is not None:
                    return embed
        raise AssertionError("erfolge sent no embed")

    return repo, invoke


async def _seed_erfolge_default(repo, uid: int = 7):
    """Two live-value categories (voice hours + streak days) + a gate Läufe
    category, with a deterministic owned set → overall 5/83."""
    await repo.add_activity(uid, "voice_seconds", 295200)          # 82h live
    for aid in ("voice_3600", "voice_36000", "voice_180000"):
        await repo.unlock_achievement(uid, aid)                    # → next Veteran
    await repo.set_streak(uid, current_streak=18,
                          last_active_date="2026-07-13", max_streak=18)
    for aid in ("streak_7", "streak_14"):
        await repo.unlock_achievement(uid, aid)                    # → next Monats-Krieger


async def test_erfolge_header_shows_count_bar_and_percent():
    # Header summary line: overall count/total (preserved), a 10-segment bar
    # (new) and the completion percent (new). round(5/83*100) == 6 (int() too).
    repo, invoke = await _erfolge_embed()
    await _seed_erfolge_default(repo)
    embed = await invoke()
    desc = str(embed.description)
    assert "5/83" in desc                                    # preserved invariant
    assert _bar_run(desc), "header must render a 10-segment █/░ progress bar"
    assert _re.search(r"6\s*%", desc), "header must show the completion percent"
    await repo.close()


async def test_erfolge_voice_field_shows_count_next_and_live_hours():
    # Voice line: unlocked/total (3/6, preserved), next title (Veteran,
    # preserved) and the LIVE value rendered in HOURS (new): voice_seconds=295200
    # → 82h toward Veteran (360000 → 100h).
    repo, invoke = await _erfolge_embed()
    await _seed_erfolge_default(repo)
    embed = await invoke()
    voice = _field_by_emoji(embed, "🎙️")
    assert "3/6" in voice          # 3 voice tiers of 6 unlocked (preserved)
    assert "Veteran" in voice      # next milestone title (preserved)
    assert "82h" in voice and "100h" in voice   # live hours (new)
    await repo.close()


async def test_erfolge_category_field_shows_a_progress_bar():
    # New layout: every category field carries a progress bar (shown via voice).
    repo, invoke = await _erfolge_embed()
    await _seed_erfolge_default(repo)
    embed = await invoke()
    assert _bar_run(_field_by_emoji(embed, "🎙️")), \
        "each category field must render a progress bar"
    await repo.close()


async def test_erfolge_streak_field_shows_next_title_and_live_days():
    # Streak line: next title (preserved) + LIVE days toward it (new).
    # max_streak=18 toward Monats-Krieger (30) → "18/30 Tage".
    repo, invoke = await _erfolge_embed()
    await _seed_erfolge_default(repo)
    embed = await invoke()
    streak = _field_by_emoji(embed, "🔥")
    assert "Monats-Krieger" in streak   # next title (preserved)
    assert "18/30" in streak            # live days (new)
    assert "Tage" in streak             # unit (new)
    await repo.close()


async def test_erfolge_gate_field_shows_live_runs_with_laeufe_unit():
    # Gate metrics render live counts in "Läufe": total_1 owned + 3 gate_a
    # entries → next Alpha Bronze Pilot (a_5), live 3/5 Läufe.
    repo, invoke = await _erfolge_embed()
    await repo.unlock_achievement(7, "total_1")
    for cost in (100, 200, 300):
        await repo.add_gate_entry("a", cost, 7, "u")
    embed = await invoke()
    gate = _field_by_emoji(embed, "🚀")
    assert "3/5" in gate      # live gate_a count toward a_5 threshold (new)
    assert "Läufe" in gate    # gate unit (new)
    await repo.close()


async def test_erfolge_completed_category_shows_alle_freigeschaltet_with_bar():
    # A fully-unlocked category keeps "Alle freigeschaltet" (preserved) and, in
    # the new layout, still renders a (full) progress bar.
    repo, invoke = await _erfolge_embed()
    for thr in (3600, 36000, 180000, 360000, 1800000, 3600000):
        await repo.unlock_achievement(7, f"voice_{thr}")   # all 6 voice tiers
    embed = await invoke()
    voice = _field_by_emoji(embed, "🎙️")
    assert "Alle freigeschaltet" in voice   # preserved invariant
    assert _bar_run(voice), "completed category still renders a bar"   # new
    await repo.close()


async def test_erfolge_secret_row_shows_count_teaser_and_hides_titles():
    # Secret row: count/total (1/8, preserved), a "???" teaser (new) and NEVER
    # the secret titles (security invariant preserved).
    repo, invoke = await _erfolge_embed()
    await repo.unlock_achievement(7, "msg_1000")   # one secret unlocked of 8
    embed = await invoke()
    secret = _field_by_emoji(embed, "🔒")
    assert "1/8" in secret       # count/total (preserved)
    assert "???" in secret       # teaser (new)
    flat = _collect_text_of_embed(embed)
    assert "Tastatur-Krieger" not in flat   # msg_1000 title never revealed
    assert "Emoji-Fan" not in flat          # reaction_100 title never revealed
    await repo.close()


def _collect_text_of_embed(embed) -> str:
    parts = [str(getattr(embed, "title", "") or ""),
             str(getattr(embed, "description", "") or "")]
    for f in getattr(embed, "fields", []):
        parts.append(str(getattr(f, "name", "") or ""))
        parts.append(str(getattr(f, "value", "") or ""))
    return "\n".join(parts)
