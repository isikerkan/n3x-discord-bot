"""RED-phase specs for the Kodex (rules-acceptance) feature, ported from v3.

Flow: a new member is DM'd a code-of-conduct; they confirm by reacting ✅ in
the DM; admins can (re)send it to everyone and post a confirmed/not-confirmed
audit to a check channel.

New surface (to be implemented downstream):
  * module ``n3x_bot/kodex.py`` with:
      KODEX_TEXT: str                     # ported German rules, non-empty
      KODEX_EMOJI = "✅"
      async send_kodex_dm(bot, repo, member) -> None
      async handle_kodex_confirmation(bot, repo, payload) -> None
      build_kodex_report(confirmed: set[int], members: list) -> list[str]
      register_kodex_commands(bot, repo, settings) -> None   # !kodex, !kodex_check
  * bot.py wiring: on_member_join DMs the Kodex; on_raw_reaction_add routes a ✅
    on a tracked kodex DM message to a confirmation; build_bot registers the
    kodex commands.

The ``n3x_bot.kodex`` module does not exist yet, so it is imported lazily INSIDE
each test body: collection still succeeds, and every test fails with
ModuleNotFoundError (the correct pre-impl RED) rather than breaking the file.
Tests that touch new repo methods / new command wiring instead RED on
AttributeError / AssertionError.
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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
    # Settings has extra="ignore", so `kodex_check_channel_id` (not a field
    # pre-impl) is silently dropped — keeps construction from erroring so the
    # RED lands on the feature under test, not on Settings.
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
    roles = [SimpleNamespace(id=r) for r in role_ids]
    return SimpleNamespace(id=member_id, roles=roles, bot=is_bot)


def _dm_member(*, member_id=111, is_bot=False, send_raises=None, msg_id=9001):
    """A member whose ``.send`` returns a fake DM message carrying ``.id`` and
    an ``.add_reaction`` AsyncMock (or raises, to simulate closed DMs)."""
    msg = SimpleNamespace(id=msg_id, add_reaction=AsyncMock())
    member = SimpleNamespace(id=member_id, bot=is_bot, display_name=f"U{member_id}",
                             mention=f"<@{member_id}>")
    member.send = (AsyncMock(side_effect=send_raises) if send_raises is not None
                   else AsyncMock(return_value=msg))
    member._sent_msg = msg
    return member


def _report_member(member_id, name):
    return SimpleNamespace(id=member_id, display_name=name,
                           mention=f"<@{member_id}>", bot=False)


def _reaction_payload(message_id, user_id, emoji="✅"):
    return SimpleNamespace(message_id=message_id, user_id=user_id, emoji=emoji,
                           guild_id=None, channel_id=0, member=None)


def _fake_interaction(user, guild=None):
    """A slash-interaction fake mirroring the /admin & /config slash tests:
    ``.user`` / ``.guild`` / ``.response.send_message`` / ``.response.defer`` /
    ``.followup.send``."""
    it = MagicMock()
    it.user = user
    it.guild = guild
    it.response = MagicMock()
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.followup = MagicMock()
    it.followup.send = AsyncMock()
    return it


def _join_member(member_id=555, display_name="Newbie"):
    """A joining member wired just enough that the unconditionally-invoked
    ``enforce_prefix`` takes its early-return path, plus a DM-capable ``.send``.
    """
    guild_me = SimpleNamespace(
        guild_permissions=SimpleNamespace(manage_nicknames=False))
    guild = SimpleNamespace(owner=object(), me=guild_me)
    msg = SimpleNamespace(id=7001, add_reaction=AsyncMock())
    return SimpleNamespace(id=member_id, display_name=display_name, bot=False,
                           mention=f"<@{member_id}>", guild=guild, roles=[],
                           top_role=0, send=AsyncMock(return_value=msg))


# ── module constants ────────────────────────────────────────────────────────

async def test_kodex_text_is_non_empty_str_with_expected_keyword():
    from n3x_bot import kodex
    assert isinstance(kodex.KODEX_TEXT, str)
    assert kodex.KODEX_TEXT.strip()
    assert "Verhaltenskodex" in kodex.KODEX_TEXT


async def test_kodex_emoji_is_check_mark():
    from n3x_bot import kodex
    assert kodex.KODEX_EMOJI == "✅"


# ── send_kodex_dm ───────────────────────────────────────────────────────────

async def test_send_kodex_dm_sends_text_reacts_and_records_message():
    from n3x_bot import kodex
    from n3x_bot.content import ContentTexts
    repo = await _flatfile_repo()
    member = _dm_member(member_id=111, msg_id=9001)
    bot = MagicMock()
    bot.content_texts = ContentTexts()

    await kodex.send_kodex_dm(bot, repo, member)

    member.send.assert_awaited_once_with(kodex.KODEX_TEXT)
    member._sent_msg.add_reaction.assert_awaited_once_with(kodex.KODEX_EMOJI)
    assert await repo.get_kodex_message_user(9001) == 111

    await repo.close()


async def test_send_kodex_dm_skips_bot_member():
    from n3x_bot import kodex
    member = _dm_member(member_id=222, is_bot=True)

    await kodex.send_kodex_dm(MagicMock(), MagicMock(), member)

    member.send.assert_not_awaited()


async def test_send_kodex_dm_swallows_dm_failure_and_records_nothing():
    from n3x_bot import kodex
    repo = await _flatfile_repo()
    # A member with closed DMs: send raises. Best-effort — no crash, and since
    # no message came back, nothing is recorded and no reaction is added.
    member = _dm_member(member_id=333, send_raises=RuntimeError("DMs closed"),
                        msg_id=9003)

    await kodex.send_kodex_dm(MagicMock(), repo, member)  # must not raise

    member._sent_msg.add_reaction.assert_not_awaited()
    assert await repo.get_kodex_message_user(9003) is None

    await repo.close()


# ── handle_kodex_confirmation ───────────────────────────────────────────────

async def test_handle_kodex_confirmation_confirms_tracked_user():
    from n3x_bot import kodex
    repo = await _flatfile_repo()
    await repo.save_kodex_message(9001, 111)
    payload = _reaction_payload(9001, 111)

    await kodex.handle_kodex_confirmation(MagicMock(), repo, payload)

    assert await repo.has_confirmed_kodex(111) is True

    await repo.close()


async def test_handle_kodex_confirmation_ignores_untracked_message():
    from n3x_bot import kodex
    repo = await _flatfile_repo()
    payload = _reaction_payload(4040, 111)  # no kodex message saved for 4040

    await kodex.handle_kodex_confirmation(MagicMock(), repo, payload)

    assert await repo.has_confirmed_kodex(111) is False

    await repo.close()


async def test_handle_kodex_confirmation_ignores_bot_seed_reaction():
    # send_kodex_dm seeds the DM with the bot's own ✅; Discord dispatches that
    # back here. Only the tracked member may confirm — a reactor that is NOT the
    # tracked user (the bot, or anyone else) must record nothing.
    from n3x_bot import kodex
    repo = await _flatfile_repo()
    await repo.save_kodex_message(9001, 111)
    payload = _reaction_payload(9001, 999)  # reactor 999 != tracked member 111

    await kodex.handle_kodex_confirmation(MagicMock(), repo, payload)

    assert await repo.has_confirmed_kodex(111) is False

    await repo.close()


async def test_handle_kodex_confirmation_ignores_non_checkmark_emoji():
    from n3x_bot import kodex
    repo = await _flatfile_repo()
    await repo.save_kodex_message(9001, 111)
    payload = _reaction_payload(9001, 111, emoji="❌")

    await kodex.handle_kodex_confirmation(MagicMock(), repo, payload)

    assert await repo.has_confirmed_kodex(111) is False

    await repo.close()


# ── build_kodex_report ──────────────────────────────────────────────────────

async def test_build_kodex_report_marks_confirmed_and_unconfirmed_members():
    from n3x_bot import kodex
    members = [_report_member(1, "Alice"), _report_member(2, "Bob")]

    chunks = kodex.build_kodex_report({1}, members)

    text = "\n".join(chunks)
    lines = text.splitlines()
    a_line = next(l for l in lines if "<@1>" in l)
    b_line = next(l for l in lines if "<@2>" in l)
    assert "✅" in a_line   # 1 confirmed
    assert "❌" in b_line   # 2 not confirmed


async def test_build_kodex_report_chunks_long_member_lists_within_limit():
    from n3x_bot import kodex
    members = [_report_member(i, f"User{i}") for i in range(500)]

    chunks = kodex.build_kodex_report(set(), members)

    assert len(chunks) > 1
    assert all(len(c) <= 1900 for c in chunks)


# ── register_kodex_commands (Phase 5: slash-ONLY /kodex + /kodex_check) ──────
#
# Phase 5 migrates `!kodex` / `!kodex_check` to slash-ONLY app commands on
# `bot.tree`. Both are admin-gated; the admin path DEFERS ephemerally before the
# slow member fan-out (kodex: DM each member; kodex_check: scan + build report)
# and delivers the result via `interaction.followup`. Non-admin is refused up
# front with an ephemeral "❌ Keine Berechtigung." and does NO work / NO defer.
# The commands are addressed via the tree, e.g.
#     bot.tree.get_command("kodex").callback(interaction)

async def test_register_kodex_commands_registers_both_as_slash_only():
    from n3x_bot import kodex
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42, kodex_check_channel_id=555)
    bot = build_bot(settings, repo)

    kodex.register_kodex_commands(bot, repo, settings)

    assert bot.get_command("kodex") is None          # dropped from prefix registry
    assert bot.get_command("kodex_check") is None
    assert bot.tree.get_command("kodex") is not None  # present on the app tree
    assert bot.tree.get_command("kodex_check") is not None

    await repo.close()


async def test_register_kodex_commands_is_idempotent():
    from n3x_bot import kodex
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42, kodex_check_channel_id=555)
    bot = build_bot(settings, repo)

    kodex.register_kodex_commands(bot, repo, settings)
    kodex.register_kodex_commands(bot, repo, settings)  # must not raise/duplicate

    assert bot.tree.get_command("kodex") is not None
    assert bot.tree.get_command("kodex_check") is not None

    await repo.close()


async def test_kodex_slash_refuses_non_admin_and_sends_no_dms():
    from n3x_bot import kodex
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    kodex.register_kodex_commands(bot, repo, settings)

    m1, m2 = _dm_member(member_id=1), _dm_member(member_id=2)
    interaction = _fake_interaction(
        user=_member(member_id=9, role_ids=(999,)),  # not admin
        guild=SimpleNamespace(members=[m1, m2]))

    await bot.tree.get_command("kodex").callback(interaction)

    m1.send.assert_not_awaited()
    m2.send.assert_not_awaited()
    # refused ephemerally, and NO slow work was even deferred
    interaction.response.send_message.assert_awaited_once()
    assert "Keine Berechtigung" in interaction.response.send_message.await_args.args[0]
    assert interaction.response.send_message.await_args.kwargs.get("ephemeral") is True
    interaction.response.defer.assert_not_awaited()

    await repo.close()


async def test_kodex_slash_admin_defers_then_dms_each_non_bot_member_then_summarizes():
    from n3x_bot import kodex
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    kodex.register_kodex_commands(bot, repo, settings)

    m1, m2 = _dm_member(member_id=1, msg_id=101), _dm_member(member_id=2, msg_id=102)
    bot_m = _dm_member(member_id=3, is_bot=True)

    order: list[str] = []
    interaction = _fake_interaction(
        user=_member(member_id=9, role_ids=(42,)),  # admin
        guild=SimpleNamespace(members=[m1, m2, bot_m]))
    interaction.response.defer = AsyncMock(
        side_effect=lambda *a, **k: order.append("defer"))
    interaction.followup.send = AsyncMock(
        side_effect=lambda *a, **k: order.append("followup"))

    m1.send = AsyncMock(side_effect=lambda *a, **k: order.append("dm") or m1._sent_msg)
    m2.send = AsyncMock(side_effect=lambda *a, **k: order.append("dm") or m2._sent_msg)

    await bot.tree.get_command("kodex").callback(interaction)

    # deferred ephemerally up front, BEFORE the slow DM fan-out
    interaction.response.defer.assert_awaited_once()
    assert interaction.response.defer.await_args.kwargs.get("ephemeral") is True
    m1.send.assert_awaited_once_with(kodex.KODEX_TEXT)
    m2.send.assert_awaited_once_with(kodex.KODEX_TEXT)
    bot_m.send.assert_not_awaited()  # bots are skipped by send_kodex_dm
    # a German summary followup resolves the deferred ack, mentioning the count
    interaction.followup.send.assert_awaited()
    summary = " ".join(str(c.args[0]) for c in interaction.followup.send.await_args_list
                       if c.args)
    assert "2" in summary
    # defer FIRST, DMs in the middle, followup LAST
    assert order[0] == "defer" and order[-1] == "followup"
    assert order.count("dm") == 2

    await repo.close()


async def test_kodex_slash_continues_bulk_loop_when_one_members_reaction_fails():
    # A rate-limit / HTTPException on one member's add_reaction (or a storage
    # error on save) must NOT abort the whole /kodex loop — remaining members
    # still get DM'd. And because the mapping is saved before the reaction is
    # seeded, the failing member's message is still recorded so a manual ✅ works.
    from n3x_bot import kodex
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)
    kodex.register_kodex_commands(bot, repo, settings)

    m1 = _dm_member(member_id=1, msg_id=101)
    m1._sent_msg.add_reaction.side_effect = RuntimeError("rate limited")
    m2 = _dm_member(member_id=2, msg_id=102)
    interaction = _fake_interaction(
        user=_member(member_id=9, role_ids=(42,)),  # admin
        guild=SimpleNamespace(members=[m1, m2]))

    await bot.tree.get_command("kodex").callback(interaction)  # must not raise

    # m1's reaction failed but the loop reached m2...
    m2.send.assert_awaited_once_with(kodex.KODEX_TEXT)
    m2._sent_msg.add_reaction.assert_awaited_once_with(kodex.KODEX_EMOJI)
    # ...and m1's mapping was still persisted (durable before the seed reaction).
    assert await repo.get_kodex_message_user(101) == 1
    assert await repo.get_kodex_message_user(102) == 2

    await repo.close()


async def test_kodex_check_slash_refuses_non_admin_and_reports_nothing():
    from n3x_bot import kodex
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42, kodex_check_channel_id=555)
    bot = build_bot(settings, repo)
    kodex.register_kodex_commands(bot, repo, settings)

    interaction = _fake_interaction(
        user=_member(member_id=9, role_ids=(999,)),  # not admin
        guild=SimpleNamespace(members=[_report_member(1, "Alice")]))

    await bot.tree.get_command("kodex_check").callback(interaction)

    interaction.followup.send.assert_not_awaited()  # no report chunks delivered
    interaction.response.defer.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
    assert "Keine Berechtigung" in interaction.response.send_message.await_args.args[0]
    assert interaction.response.send_message.await_args.kwargs.get("ephemeral") is True

    await repo.close()


async def test_kodex_check_slash_admin_defers_then_followups_report():
    from n3x_bot import kodex
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42, kodex_check_channel_id=555)
    bot = build_bot(settings, repo)
    kodex.register_kodex_commands(bot, repo, settings)

    await repo.confirm_kodex(1)  # Alice confirmed, Bob not

    interaction = _fake_interaction(
        user=_member(member_id=9, role_ids=(42,)),  # admin
        guild=SimpleNamespace(
            members=[_report_member(1, "Alice"), _report_member(2, "Bob")]))

    await bot.tree.get_command("kodex_check").callback(interaction)

    # deferred ephemerally up front, then the report is delivered via followup
    interaction.response.defer.assert_awaited_once()
    assert interaction.response.defer.await_args.kwargs.get("ephemeral") is True
    interaction.followup.send.assert_awaited()
    report = "\n".join(str(c.args[0]) for c in interaction.followup.send.await_args_list
                       if c.args)
    assert "<@1>" in report and "✅" in report  # Alice confirmed
    assert "<@2>" in report and "❌" in report  # Bob not confirmed

    await repo.close()


# ── wiring: on_member_join ──────────────────────────────────────────────────

async def test_on_member_join_sends_kodex_dm_and_still_registers_user(monkeypatch):
    from n3x_bot import kodex
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)
    monkeypatch.setattr(botmod.asyncio, "sleep", AsyncMock())
    member = _join_member(member_id=555, display_name="Newbie")

    await bot.on_member_join(member)

    # Kodex DM sent...
    member.send.assert_awaited_once_with(kodex.KODEX_TEXT)
    # ...without breaking the existing auto-registration behaviour.
    user = await repo.get_user(555)
    assert user is not None
    assert user.display_name == "Newbie"

    await repo.close()


# ── wiring: on_raw_reaction_add ─────────────────────────────────────────────

async def test_on_raw_reaction_add_confirms_kodex_on_tracked_message():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    await repo.save_kodex_message(9001, 111)
    payload = _reaction_payload(9001, 111)

    await bot.on_raw_reaction_add(payload)

    assert await repo.has_confirmed_kodex(111) is True

    await repo.close()


# ── wiring: build_bot registers the kodex commands ──────────────────────────

async def test_build_bot_registers_kodex_slash_commands():
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=42)
    bot = build_bot(settings, repo)

    assert bot.get_command("kodex") is None          # slash-only, not prefix
    assert bot.get_command("kodex_check") is None
    assert bot.tree.get_command("kodex") is not None
    assert bot.tree.get_command("kodex_check") is not None

    await repo.close()
