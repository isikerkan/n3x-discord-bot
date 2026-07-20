"""RED tests for Nick/prefix enforcement (v3 port #7).

Behavior-preserving REFACTOR + HARDENING: extract the inline ``enforce_prefix``
closure out of ``bot.py`` into a new module ``n3x_bot.nicknames`` with

1. ``desired_nick(display_name: str, has_role: bool, prefix_str: str) -> str | None``
   — a PURE decision helper (no Discord, no member object). Returns the target
   nickname, or ``None`` when no change is needed (the B5 "only edit when
   needed" formalization).

2. ``async enforce_nick(member, settings) -> bool`` — the Discord side. Same
   guards as today's closure; computes ``has_role``, delegates the decision to
   ``desired_nick``; edits only when a string is returned; returns whether an
   edit was effectively performed.

Symbols are imported LAZILY inside each test body so a missing module/symbol
surfaces as a runtime error (RED for the right reason), never a collection-time
ImportError. Pure-helper tests need no mocks; only Discord I/O is faked.

Assumptions pinned here (flagged in the handoff for the Architect):
  * ``prefix_str`` is ``"[N3X]"`` (len 5) — the NO-SPACE prefix. The current
    observable nick format ``"[N3X]Player"`` is PRESERVED. v3's B18 stray-space
    bug does not apply (we never introduce a space).
  * None-on-no-change: an already-correct member (role-holder already prefixed,
    or non-holder with no prefix) yields ``None`` — the caller must NOT edit.
  * Safe truncation: the result is truncated to 32 chars total by trimming the
    BASE, never the prefix. A 40-char name yields ``prefix + base[:32-len(prefix)]``.
  * Removal strips leading/trailing whitespace, so de-prefixing never leaves a
    stray leading space.
  * ``enforce_nick`` returns True only when an edit was performed successfully;
    a swallowed edit failure returns False (see test + handoff note).
  * NO new config, NO storage changes are in scope.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock


PREFIX = "[N3X]"  # matches Settings.prefix_str default (no space)


# ── local Discord fakes (mirroring test_bot_wiring._FakeMember style) ─────────

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


def _settings():
    # MIGRATED for multi-role: enforce_nick now reads the list accessor
    # `target_role_ids`, so the fake exposes it alongside the legacy
    # `target_role_id` (which the tests still use to build a matching role).
    return SimpleNamespace(prefix_str=PREFIX, target_role_id=1, target_role_ids=[1])


def _multi_settings(*role_ids):
    """Settings fake with several configured target roles. `target_role_id` (the
    single-int accessor) is the FIRST id; `target_role_ids` is the full list."""
    return SimpleNamespace(prefix_str=PREFIX, target_role_id=role_ids[0],
                           target_role_ids=list(role_ids))


def _member(*, display_name="Player", roles=None, top_role=1,
            bot=False, manage_nicknames=True, bot_top_role=10):
    """Build a member whose guild is wired so enforce_nick can run its guards."""
    bot_member = _FakeMember(top_role=bot_top_role,
                             manage_nicknames=manage_nicknames)
    owner = _FakeMember(display_name="Owner")
    guild = _FakeGuild(owner=owner, me=bot_member)
    member = _FakeMember(display_name=display_name, roles=roles, top_role=top_role,
                         bot=bot, guild=guild)
    return member


# ── desired_nick: the PURE decision helper ───────────────────────────────────

def test_desired_nick_adds_prefix_with_space_for_role_holder_without_prefix():
    from n3x_bot.nicknames import desired_nick
    assert desired_nick("Player", True, PREFIX) == "[N3X] Player"


def test_desired_nick_returns_none_for_already_correctly_prefixed_role_holder():
    from n3x_bot.nicknames import desired_nick
    # "[N3X] Player" (tag + space) is already correct
    assert desired_nick("[N3X] Player", True, PREFIX) is None


def test_desired_nick_inserts_space_into_old_no_space_prefix():
    from n3x_bot.nicknames import desired_nick
    # legacy "[N3X]Player" (no space) is corrected to "[N3X] Player"
    assert desired_nick("[N3X]Player", True, PREFIX) == "[N3X] Player"


def test_desired_nick_removes_prefix_for_non_role_holder():
    from n3x_bot.nicknames import desired_nick
    assert desired_nick("[N3X]Player", False, PREFIX) == "Player"


def test_desired_nick_returns_none_for_unprefixed_non_role_holder():
    from n3x_bot.nicknames import desired_nick
    assert desired_nick("Player", False, PREFIX) is None


def test_desired_nick_strips_legacy_r3x_marker():
    from n3x_bot.nicknames import desired_nick
    assert desired_nick("R3XPlayer", True, PREFIX) == "[N3X] Player"


def test_desired_nick_truncates_base_keeping_full_prefix_within_32():
    from n3x_bot.nicknames import desired_nick
    long_name = "X" * 40
    result = desired_nick(long_name, True, PREFIX)
    tag = PREFIX + " "
    assert result.startswith(tag)
    assert len(result) <= 32
    assert result == tag + "X" * (32 - len(tag))


def test_desired_nick_handles_whitespace_only_name():
    from n3x_bot.nicknames import desired_nick
    # base collapses to empty; result is the bare prefix, no crash
    assert desired_nick("   ", True, PREFIX) == "[N3X]"


def test_desired_nick_role_holder_named_only_prefix_is_noop():
    from n3x_bot.nicknames import desired_nick
    assert desired_nick("[N3X]", True, PREFIX) is None


def test_desired_nick_removal_strips_stray_leading_space():
    from n3x_bot.nicknames import desired_nick
    # a "[N3X] Player" (with a space) de-prefixes to "Player", never " Player"
    assert desired_nick("[N3X] Player", False, PREFIX) == "Player"


# ── enforce_nick: the Discord side ───────────────────────────────────────────

async def test_enforce_nick_edits_and_returns_true_when_role_granted():
    from n3x_bot.nicknames import enforce_nick
    settings = _settings()
    member = _member(display_name="Player", roles=[_FakeRole(settings.target_role_id)])

    result = await enforce_nick(member, settings)

    member.edit.assert_awaited_once()
    assert member.edit.await_args.kwargs["nick"] == "[N3X] Player"
    assert result is True


async def test_enforce_nick_edits_and_returns_true_when_role_revoked():
    from n3x_bot.nicknames import enforce_nick
    settings = _settings()
    member = _member(display_name="[N3X]Player", roles=[_FakeRole(999)])

    result = await enforce_nick(member, settings)

    member.edit.assert_awaited_once()
    assert member.edit.await_args.kwargs["nick"] == "Player"
    assert result is True


async def test_enforce_nick_does_not_edit_correct_role_holder():
    # The B5 win: an already-correct role-holder is never re-edited.
    from n3x_bot.nicknames import enforce_nick
    settings = _settings()
    member = _member(display_name="[N3X] Player",
                     roles=[_FakeRole(settings.target_role_id)])

    result = await enforce_nick(member, settings)

    member.edit.assert_not_called()
    assert result is False


async def test_enforce_nick_skips_bot_member():
    from n3x_bot.nicknames import enforce_nick
    settings = _settings()
    member = _member(display_name="Player", bot=True,
                     roles=[_FakeRole(settings.target_role_id)])

    result = await enforce_nick(member, settings)

    member.edit.assert_not_called()
    assert result is False


async def test_enforce_nick_skips_guild_owner():
    from n3x_bot.nicknames import enforce_nick
    settings = _settings()
    member = _member(display_name="Player",
                     roles=[_FakeRole(settings.target_role_id)])
    member.guild.owner = member  # member IS the owner

    result = await enforce_nick(member, settings)

    member.edit.assert_not_called()
    assert result is False


async def test_enforce_nick_skips_when_bot_lacks_manage_nicknames():
    from n3x_bot.nicknames import enforce_nick
    settings = _settings()
    member = _member(display_name="Player", manage_nicknames=False,
                     roles=[_FakeRole(settings.target_role_id)])

    result = await enforce_nick(member, settings)

    member.edit.assert_not_called()
    assert result is False


async def test_enforce_nick_skips_when_member_outranks_bot():
    from n3x_bot.nicknames import enforce_nick
    settings = _settings()
    # member top_role (10) >= bot top_role (10) → guard trips
    member = _member(display_name="Player", top_role=10, bot_top_role=10,
                     roles=[_FakeRole(settings.target_role_id)])

    result = await enforce_nick(member, settings)

    member.edit.assert_not_called()
    assert result is False


# ── enforce_nick honors MULTIPLE configured target roles (ANY-match) ──────────
# The "has the target role" test passes when the member holds ANY of the
# configured target roles. enforce_nick computes this over `target_role_ids`;
# pre-impl it reads only the single-int `target_role_id` (the first id), so a
# member holding a LATER id is treated as a non-holder = RED (no prefix added).

async def test_enforce_nick_adds_prefix_for_member_holding_a_later_target_role():
    from n3x_bot.nicknames import enforce_nick
    settings = _multi_settings(111, 222)  # single-int accessor == 111
    member = _member(display_name="Player", roles=[_FakeRole(222)])  # holds 222

    result = await enforce_nick(member, settings)

    member.edit.assert_awaited_once()
    assert member.edit.await_args.kwargs["nick"] == "[N3X] Player"
    assert result is True


async def test_enforce_nick_swallows_edit_failure_and_returns_false():
    from n3x_bot.nicknames import enforce_nick
    settings = _settings()
    member = _member(display_name="Player",
                     roles=[_FakeRole(settings.target_role_id)])
    member.edit = AsyncMock(side_effect=RuntimeError("discord 403"))

    # must not raise
    result = await enforce_nick(member, settings)

    member.edit.assert_awaited_once()
    assert result is False
