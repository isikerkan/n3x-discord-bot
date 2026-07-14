"""RED tests for achievements Pass C, sub-feature 1: voice-tier role automation.

Two surfaces:

1. ``Settings.voice_role_map() -> dict[str, int]`` — parses the
   ``voice_achievement_roles`` config string (``"voice_36000:111,voice_180000:222"``)
   into an ``achievement_id -> role_id`` map. Pure, no Discord.

2. Role transition logic:
   * ``voice_role_transition(newly_ids, role_map) -> (grant_role_id|None, [other_role_ids])``
     — pure: if any mapped voice achievement id is in ``newly``, grant the role
     for the HIGHEST newly-mapped tier and list every OTHER mapped role id (to
     be removed); otherwise ``(None, [])``.
   * ``async apply_voice_roles(bot, settings, member, newly) -> None`` — resolves
     the grant role via ``member.guild.get_role``, awaits ``member.add_roles`` /
     ``member.remove_roles`` (only for other mapped roles the member actually
     holds); no-op when the map is empty or ``newly`` holds no mapped voice tier;
     best-effort (never raises).

New symbols are resolved lazily INSIDE each test body so a not-yet-implemented
symbol fails that individual test with a runtime AttributeError/ModuleError (the
correct pre-impl RED "missing symbol" signal) rather than a collection-time
ImportError that would break the whole file.

Assumptions pinned here (flag for the Architect):
  * ``voice_role_transition`` and ``apply_voice_roles`` live in
    ``n3x_bot.activity`` (voice handling already lives there). If the Architect
    homes them elsewhere, only the ``_mod()`` helper changes.
  * "highest newly-mapped tier" is ranked by the ``Achievement.threshold`` of
    the mapped id (via ``ACHIEVEMENTS``), NOT by the raw role id.
  * ``apply_voice_roles`` removes ONLY the other mapped roles the member
    currently holds (``member.remove_roles(*others_member_has)``); a role the
    member doesn't have is not passed to ``remove_roles``.
"""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from n3x_bot.achievements import ACHIEVEMENTS
from n3x_bot.config import Settings

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


def _mod():
    """Lazily resolve the module hosting the (not-yet-existing) role helpers."""
    return importlib.import_module("n3x_bot.activity")


def _ach(achievement_id: str):
    return next(a for a in ACHIEVEMENTS if a.id == achievement_id)


class _Role:
    def __init__(self, role_id):
        self.id = role_id

    def __eq__(self, other):
        return isinstance(other, _Role) and other.id == self.id

    def __hash__(self):
        return hash(self.id)


def _member(*, held_role_ids=(), known_role_ids=()):
    """Fake guild member.

    ``held_role_ids`` are the roles the member currently has (``member.roles``).
    ``known_role_ids`` are every role id ``member.guild.get_role`` can resolve
    (defaults to the held roles plus nothing else; extend per-test)."""
    roles = [_Role(r) for r in held_role_ids]
    resolvable = {r: _Role(r) for r in set(known_role_ids) | set(held_role_ids)}

    guild = MagicMock()
    guild.get_role = MagicMock(side_effect=lambda rid: resolvable.get(rid))

    member = SimpleNamespace(
        id=7,
        bot=False,
        display_name="Erkan",
        roles=roles,
        guild=guild,
        add_roles=AsyncMock(),
        remove_roles=AsyncMock(),
    )
    return member


# ── Settings.voice_role_map ────────────────────────────────────────────────

def test_voice_role_map_empty_string_is_empty_dict():
    assert _settings(voice_achievement_roles="").voice_role_map() == {}


def test_voice_role_map_parses_populated_string():
    s = _settings(voice_achievement_roles="voice_36000:111,voice_180000:222")
    assert s.voice_role_map() == {"voice_36000": 111, "voice_180000": 222}


def test_voice_role_map_ignores_malformed_entries():
    # a bare token (no colon) and a non-int role id must both be skipped, while
    # the well-formed pair still parses.
    s = _settings(
        voice_achievement_roles="voice_36000:111,garbage,voice_180000:abc")
    assert s.voice_role_map() == {"voice_36000": 111}


# ── voice_role_transition (pure) ───────────────────────────────────────────

def test_transition_grants_highest_newly_mapped_and_lists_others():
    role_map = {"voice_3600": 901, "voice_36000": 902, "voice_180000": 903}
    grant, others = _mod().voice_role_transition(["voice_36000"], role_map)
    assert grant == 902
    assert set(others) == {901, 903}


def test_transition_collapses_multiple_newly_tiers_to_highest():
    role_map = {"voice_3600": 901, "voice_36000": 902, "voice_180000": 903}
    grant, others = _mod().voice_role_transition(
        ["voice_3600", "voice_36000"], role_map)
    assert grant == 902  # 36000 threshold outranks 3600
    assert set(others) == {901, 903}


def test_transition_returns_none_when_no_mapped_voice_in_newly():
    role_map = {"voice_3600": 901, "voice_36000": 902}
    grant, others = _mod().voice_role_transition(["msg_1000"], role_map)
    assert grant is None
    assert others == []


def test_transition_returns_none_for_empty_role_map():
    grant, others = _mod().voice_role_transition(["voice_36000"], {})
    assert grant is None
    assert others == []


# ── apply_voice_roles (async, fake member) ─────────────────────────────────

async def test_apply_grants_new_role_and_revokes_lower_held_role():
    settings = _settings(
        voice_achievement_roles="voice_3600:901,voice_36000:902,voice_180000:903")
    bot = MagicMock()
    # member currently holds the lower tier role 901; 902/903 resolvable.
    member = _member(held_role_ids=(901,), known_role_ids=(901, 902, 903))

    await _mod().apply_voice_roles(bot, settings, member, [_ach("voice_36000")])

    member.add_roles.assert_awaited_once()
    granted = member.add_roles.await_args.args[0]
    assert getattr(granted, "id", None) == 902

    member.remove_roles.assert_awaited_once()
    removed_ids = {getattr(r, "id", None) for r in member.remove_roles.await_args.args}
    assert removed_ids == {901}  # only the OTHER mapped role the member held


async def test_apply_does_not_remove_unheld_mapped_roles():
    settings = _settings(
        voice_achievement_roles="voice_3600:901,voice_36000:902,voice_180000:903")
    bot = MagicMock()
    # member holds NONE of the mapped roles yet.
    member = _member(held_role_ids=(), known_role_ids=(901, 902, 903))

    await _mod().apply_voice_roles(bot, settings, member, [_ach("voice_36000")])

    member.add_roles.assert_awaited_once()
    # nothing to revoke -> remove_roles not called (or called with no roles).
    if member.remove_roles.await_count:
        assert member.remove_roles.await_args.args == ()


async def test_apply_is_noop_for_non_voice_unlock():
    settings = _settings(
        voice_achievement_roles="voice_3600:901,voice_36000:902")
    bot = MagicMock()
    member = _member(held_role_ids=(901,), known_role_ids=(901, 902))

    await _mod().apply_voice_roles(bot, settings, member, [_ach("msg_1000")])

    member.add_roles.assert_not_awaited()
    member.remove_roles.assert_not_awaited()


async def test_apply_is_noop_when_role_map_empty():
    settings = _settings(voice_achievement_roles="")
    bot = MagicMock()
    member = _member(held_role_ids=(901,), known_role_ids=(901,))

    await _mod().apply_voice_roles(bot, settings, member, [_ach("voice_36000")])

    member.add_roles.assert_not_awaited()
    member.remove_roles.assert_not_awaited()


async def test_apply_is_best_effort_when_add_roles_raises():
    settings = _settings(voice_achievement_roles="voice_36000:902")
    bot = MagicMock()
    member = _member(held_role_ids=(), known_role_ids=(902,))
    member.add_roles = AsyncMock(side_effect=RuntimeError("missing perms"))

    # must swallow the error rather than propagate.
    await _mod().apply_voice_roles(bot, settings, member, [_ach("voice_36000")])
