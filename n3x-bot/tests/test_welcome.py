"""RED tests for the Welcome-cards feature (v3 port #5).

Four surfaces, all in the new (not-yet-existing) module ``n3x_bot.welcome``:

1. ``render_welcome_card(display_name: str) -> bytes`` — a PURE Pillow render
   (no Discord, no network) that opens the bundled ``assets/welcome_bg.jpg`` via
   importlib.resources (mirroring cards.py) and draws the three centered lines
   in the upper half. Returns PNG **bytes** (matching ``render_achievement_card``,
   NOT a BytesIO). The real bundled asset is used — no mock — and the output is
   re-opened with ``PIL.Image.open`` as a genuine validity assertion.

2. ``strip_prefix(display_name, prefix_str) -> str`` — strips a leading
   ``"[N3X] "`` / ``"[N3X]"`` from the display name for the card.

3. ``async send_welcome_card(bot, settings, member) -> None`` — the Discord I/O:
   resolve the welcome channel, render for the stripped name, post a
   ``discord.File`` with the mention content. Best-effort (a send failure never
   raises out). Discord is faked with AsyncMock/MagicMock.

4. ``register_welcome_commands(bot, settings)`` — idempotent registration of the
   admin-gated ``!sync_welcome`` command that posts one card per non-bot guild
   member to the welcome channel and reports a count.

Plus wiring: ``on_member_join`` posts a welcome CARD (a ``discord.File`` to the
welcome channel) instead of the old plain-text line, and ``build_bot`` registers
``sync_welcome`` — both without breaking kodex-DM / auto-register / enforce_prefix.

Symbols are imported LAZILY inside each test body so a missing module/symbol
surfaces as a runtime error (RED for the right reason), never a collection-time
ImportError.

Assumptions pinned here (flag for the Architect):
  * New module ``n3x_bot.welcome`` — NOT piled into cards.py.
  * ``render_welcome_card`` ALWAYS returns bytes (the bg is bundled and always
    present), fixing v3's "None on missing bg". No None return path is tested.
  * Because render always returns bytes, there is NO text-fallback path in
    ``send_welcome_card`` (a card is always posted). The v3 text fallback is
    therefore considered dead code and is deliberately NOT ported/tested.
  * ``strip_prefix`` is a NEW helper (grep found no reusable standalone helper;
    ``enforce_prefix`` only does an inline ``.replace``).
  * ``register_welcome_commands(bot, settings)`` takes NO repo (the feature is
    pure rendering + Discord I/O + config), unlike ``register_kodex_commands``.
  * ``!sync_welcome`` is a PREFIX command (like ``!kodex``), admin-gated via
    ``is_admin`` — NOT the v3 slash command.
  * "members" for sync_welcome == ``ctx.guild.members`` excluding ``.bot``,
    same convention as ``register_kodex_commands``.
  * The card is posted to ``settings.welcome_channel_id`` via ``bot.get_channel``,
    content ``f"Willkommen {member.mention}!"``, filename ``welcome_{id}.png``.
"""

import importlib
import os
import tempfile
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
from PIL import Image

from n3x_bot.bot import build_bot
from n3x_bot.config import Settings
from n3x_bot.seed import seed_defaults
from n3x_bot.storage.json_repo import JsonRepository

WELCOME_BG_SIZE = (1024, 572)

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


def _welcome():
    """Lazily resolve the not-yet-existing welcome module."""
    return importlib.import_module("n3x_bot.welcome")


def _fake_channel():
    """A welcome channel whose ``send`` records the message objects it returns,
    each exposing ``.file`` so the posted ``discord.File`` can be asserted."""
    sent: list = []
    counter = {"n": 0}

    def _send(*args, **kwargs):
        counter["n"] += 1
        msg = MagicMock()
        msg.id = counter["n"]
        msg.content = args[0] if args else kwargs.get("content")
        msg.file = kwargs.get("file")
        sent.append(msg)
        return msg

    channel = MagicMock()
    channel.send = AsyncMock(side_effect=_send)
    return channel, sent


def _fake_member(mid=555, name="Newbie", is_bot=False):
    return SimpleNamespace(id=mid, display_name=name, bot=is_bot,
                           mention=f"<@{mid}>")


# ── render_welcome_card: pure Pillow render ────────────────────────────────

def test_render_welcome_card_returns_nonempty_bytes():
    render = _welcome().render_welcome_card
    out = render("Max")
    assert isinstance(out, bytes)
    assert len(out) > 0


def test_render_welcome_card_is_valid_png():
    render = _welcome().render_welcome_card
    img = Image.open(BytesIO(render("Max")))
    assert img.format == "PNG"


def test_render_welcome_card_matches_welcome_bg_dimensions():
    render = _welcome().render_welcome_card
    img = Image.open(BytesIO(render("Max")))
    assert img.size == WELCOME_BG_SIZE


def test_render_welcome_card_long_name_does_not_raise():
    render = _welcome().render_welcome_card
    out = render("X" * 120)
    assert isinstance(out, bytes) and len(out) > 0


def test_render_welcome_card_empty_name_is_safe():
    render = _welcome().render_welcome_card
    out = render("")
    assert isinstance(out, bytes) and len(out) > 0


# ── strip_prefix ────────────────────────────────────────────────────────────

def test_strip_prefix_removes_bracket_and_space():
    strip = _welcome().strip_prefix
    assert strip("[N3X] Max", "[N3X]") == "Max"


def test_strip_prefix_removes_bracket_without_space():
    strip = _welcome().strip_prefix
    assert strip("[N3X]Max", "[N3X]") == "Max"


def test_strip_prefix_leaves_plain_name_untouched():
    strip = _welcome().strip_prefix
    assert strip("Max", "[N3X]") == "Max"


def test_strip_prefix_empty_string_is_safe():
    strip = _welcome().strip_prefix
    assert strip("", "[N3X]") == ""


# ── send_welcome_card: Discord I/O ──────────────────────────────────────────

async def test_send_welcome_card_posts_file_with_mention_content():
    send_welcome_card = _welcome().send_welcome_card
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    channel, sent = _fake_channel()
    bot.get_channel = MagicMock(return_value=channel)

    await send_welcome_card(bot, settings, _fake_member(mid=555))

    bot.get_channel.assert_called_once_with(settings.welcome_channel_id)
    channel.send.assert_awaited_once()
    assert sent[0].content == "Willkommen <@555>!"
    assert isinstance(sent[0].file, discord.File)

    await repo.close()


async def test_send_welcome_card_filename_uses_member_id():
    send_welcome_card = _welcome().send_welcome_card
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    channel, sent = _fake_channel()
    bot.get_channel = MagicMock(return_value=channel)

    await send_welcome_card(bot, settings, _fake_member(mid=777))

    assert sent[0].file.filename == "welcome_777.png"

    await repo.close()


async def test_send_welcome_card_strips_prefix_before_rendering(monkeypatch):
    welcome = _welcome()
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    channel, _ = _fake_channel()
    bot.get_channel = MagicMock(return_value=channel)

    captured: list[str] = []

    def _spy_render(display_name):
        captured.append(display_name)
        return b"\x89PNG\r\n"  # any bytes; render itself is unit-tested elsewhere

    monkeypatch.setattr(welcome, "render_welcome_card", _spy_render)

    await welcome.send_welcome_card(bot, settings,
                                    _fake_member(mid=1, name="[N3X] Max"))

    assert captured == ["Max"]

    await repo.close()


async def test_send_welcome_card_noop_when_channel_missing():
    send_welcome_card = _welcome().send_welcome_card
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)

    await send_welcome_card(bot, settings, _fake_member())  # must not raise

    await repo.close()


async def test_send_welcome_card_swallows_send_error():
    send_welcome_card = _welcome().send_welcome_card
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=RuntimeError("discord down"))
    bot.get_channel = MagicMock(return_value=channel)

    await send_welcome_card(bot, settings, _fake_member())  # must not raise

    channel.send.assert_awaited_once()

    await repo.close()


async def test_send_welcome_card_returns_true_on_success():
    send_welcome_card = _welcome().send_welcome_card
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    channel, _ = _fake_channel()
    bot.get_channel = MagicMock(return_value=channel)

    assert await send_welcome_card(bot, settings, _fake_member()) is True

    await repo.close()


async def test_send_welcome_card_returns_false_when_channel_missing():
    send_welcome_card = _welcome().send_welcome_card
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)

    assert await send_welcome_card(bot, settings, _fake_member()) is False

    await repo.close()


async def test_send_welcome_card_returns_false_on_send_error():
    send_welcome_card = _welcome().send_welcome_card
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=RuntimeError("discord down"))
    bot.get_channel = MagicMock(return_value=channel)

    assert await send_welcome_card(bot, settings, _fake_member()) is False

    await repo.close()


async def test_send_welcome_card_bot_member_returns_false_posts_nothing():
    send_welcome_card = _welcome().send_welcome_card
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    channel, sent = _fake_channel()
    bot.get_channel = MagicMock(return_value=channel)

    result = await send_welcome_card(bot, settings, _fake_member(is_bot=True))

    assert result is False
    channel.send.assert_not_called()

    await repo.close()


# ── register_welcome_commands / /sync_welcome (Phase 5: slash-ONLY) ─────────
#
# Phase 5 migrates `!sync_welcome` to a slash-ONLY app command on `bot.tree`.
# It is admin-gated; the admin path DEFERS ephemerally before the slow
# per-member card backfill (send_welcome_card + asyncio.sleep(1) each) and
# reports the count via `interaction.followup`. Non-admin is refused up front
# with an ephemeral "❌ Keine Berechtigung." and posts NO cards / does NO defer.
# The command is addressed via the tree:
#     bot.tree.get_command("sync_welcome").callback(interaction)

def _fake_interaction(members, is_admin=True, admin_role_id=4242):
    role_id = admin_role_id if is_admin else (admin_role_id + 1)
    it = MagicMock()
    it.user = SimpleNamespace(roles=[SimpleNamespace(id=role_id)], bot=False)
    it.guild = SimpleNamespace(members=members)
    it.response = MagicMock()
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.followup = MagicMock()
    it.followup.send = AsyncMock()
    return it


async def test_register_welcome_commands_registers_sync_welcome_slash():
    register = _welcome().register_welcome_commands
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    register(bot, settings)

    assert bot.get_command("sync_welcome") is None          # not a prefix command
    assert bot.tree.get_command("sync_welcome") is not None  # on the app tree

    await repo.close()


async def test_register_welcome_commands_is_idempotent():
    register = _welcome().register_welcome_commands
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    register(bot, settings)
    register(bot, settings)  # must not raise / double-register

    assert bot.tree.get_command("sync_welcome") is not None

    await repo.close()


async def test_sync_welcome_non_admin_refused_posts_no_cards():
    register = _welcome().register_welcome_commands
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _fake_channel()
    bot.get_channel = MagicMock(return_value=channel)
    register(bot, settings)

    members = [_fake_member(mid=1), _fake_member(mid=2)]
    interaction = _fake_interaction(members, is_admin=False, admin_role_id=4242)

    await bot.tree.get_command("sync_welcome").callback(interaction)

    channel.send.assert_not_called()
    interaction.response.defer.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
    assert "Keine Berechtigung" in interaction.response.send_message.await_args.args[0]
    assert interaction.response.send_message.await_args.kwargs.get("ephemeral") is True

    await repo.close()


async def test_sync_welcome_admin_defers_then_posts_one_card_per_non_bot_member(monkeypatch):
    monkeypatch.setattr("n3x_bot.welcome.asyncio.sleep", AsyncMock())
    register = _welcome().register_welcome_commands
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _fake_channel()
    bot.get_channel = MagicMock(return_value=channel)
    register(bot, settings)

    members = [_fake_member(mid=1, name="Ann"), _fake_member(mid=2, name="Bob")]
    interaction = _fake_interaction(members, is_admin=True, admin_role_id=4242)

    await bot.tree.get_command("sync_welcome").callback(interaction)

    # deferred ephemerally up front, before the slow backfill
    interaction.response.defer.assert_awaited_once()
    assert interaction.response.defer.await_args.kwargs.get("ephemeral") is True
    assert channel.send.await_count == 2
    assert all(isinstance(m.file, discord.File) for m in sent)

    await repo.close()


async def test_sync_welcome_admin_reports_count_via_followup(monkeypatch):
    monkeypatch.setattr("n3x_bot.welcome.asyncio.sleep", AsyncMock())
    register = _welcome().register_welcome_commands
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=4242)
    bot = build_bot(settings, repo)
    channel, _ = _fake_channel()
    bot.get_channel = MagicMock(return_value=channel)
    register(bot, settings)

    members = [_fake_member(mid=1), _fake_member(mid=2)]
    interaction = _fake_interaction(members, is_admin=True, admin_role_id=4242)

    await bot.tree.get_command("sync_welcome").callback(interaction)

    interaction.followup.send.assert_awaited()
    reported = " ".join(str(c.args[0]) for c in interaction.followup.send.await_args_list
                        if c.args)
    assert "2" in reported

    await repo.close()


async def test_sync_welcome_missing_channel_reports_zero(monkeypatch):
    """A misconfigured/deleted welcome channel must NOT report a false success:
    send_welcome_card returns False for every member, so the count is 0."""
    monkeypatch.setattr("n3x_bot.welcome.asyncio.sleep", AsyncMock())
    register = _welcome().register_welcome_commands
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=4242)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)  # channel missing
    register(bot, settings)

    members = [_fake_member(mid=1), _fake_member(mid=2)]
    interaction = _fake_interaction(members, is_admin=True, admin_role_id=4242)

    await bot.tree.get_command("sync_welcome").callback(interaction)

    reported = " ".join(str(c.args[0]) for c in interaction.followup.send.await_args_list
                        if c.args)
    assert "0" in reported and "2" not in reported

    await repo.close()


async def test_sync_welcome_skips_bot_members(monkeypatch):
    monkeypatch.setattr("n3x_bot.welcome.asyncio.sleep", AsyncMock())
    register = _welcome().register_welcome_commands
    repo = await _flatfile_repo()
    settings = _settings(admin_role_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _fake_channel()
    bot.get_channel = MagicMock(return_value=channel)
    register(bot, settings)

    members = [_fake_member(mid=1), _fake_member(mid=2, is_bot=True)]
    interaction = _fake_interaction(members, is_admin=True, admin_role_id=4242)

    await bot.tree.get_command("sync_welcome").callback(interaction)

    assert channel.send.await_count == 1  # the bot member was skipped

    await repo.close()


# ── wiring: on_member_join posts a card; build_bot registers sync_welcome ──

def _join_member(*, member_id=1234, display_name="Joiner", is_bot=False):
    """A member wired just enough that the real ``on_member_join`` runs end to
    end: ``enforce_prefix`` early-returns (bot's own manage_nicknames False),
    ``send_kodex_dm`` has an awaitable ``.send``."""
    guild_me = SimpleNamespace(
        guild_permissions=SimpleNamespace(manage_nicknames=False))
    guild = SimpleNamespace(owner=object(), me=guild_me)
    return SimpleNamespace(id=member_id, display_name=display_name, bot=is_bot,
                           mention=f"<@{member_id}>", guild=guild, roles=[],
                           top_role=0, send=AsyncMock())


async def test_on_member_join_posts_welcome_card_file(monkeypatch):
    monkeypatch.setattr("n3x_bot.bot.asyncio.sleep", AsyncMock())
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    channel, sent = _fake_channel()
    bot.get_channel = MagicMock(return_value=channel)

    member = _join_member(member_id=1234)
    await bot.on_member_join(member)

    channel.send.assert_awaited()
    assert any(isinstance(m.file, discord.File) for m in sent), \
        "on_member_join should post a welcome CARD (discord.File), not plain text"

    await repo.close()


async def test_on_member_join_still_registers_user_when_posting_card(monkeypatch):
    monkeypatch.setattr("n3x_bot.bot.asyncio.sleep", AsyncMock())
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    channel, sent = _fake_channel()
    bot.get_channel = MagicMock(return_value=channel)

    member = _join_member(member_id=4321, display_name="Registered")
    await bot.on_member_join(member)

    user = await repo.get_user(4321)
    assert user is not None and user.display_name == "Registered"
    assert any(isinstance(m.file, discord.File) for m in sent)

    await repo.close()


async def test_on_member_join_bot_posts_no_welcome_card(monkeypatch):
    monkeypatch.setattr("n3x_bot.bot.asyncio.sleep", AsyncMock())
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    channel, sent = _fake_channel()
    bot.get_channel = MagicMock(return_value=channel)

    member = _join_member(member_id=99, is_bot=True)
    await bot.on_member_join(member)

    channel.send.assert_not_called()  # bots get no public welcome card
    assert sent == []

    await repo.close()


async def test_on_member_join_still_sends_kodex_dm(monkeypatch):
    monkeypatch.setattr("n3x_bot.bot.asyncio.sleep", AsyncMock())
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    channel, _ = _fake_channel()
    bot.get_channel = MagicMock(return_value=channel)

    member = _join_member(member_id=5150)
    await bot.on_member_join(member)

    # send_kodex_dm DMs the joining (non-bot) member.
    member.send.assert_awaited()

    await repo.close()


async def test_on_member_join_still_runs_enforce_prefix(monkeypatch):
    """Pin that the welcome-card replacement did not drop enforce_prefix from the
    join path: a role-holder without the prefix gets nicked end to end."""
    monkeypatch.setattr("n3x_bot.bot.asyncio.sleep", AsyncMock())
    repo = await _flatfile_repo()
    settings = _settings()  # target_role_id=1, prefix_str="[N3X]"
    bot = build_bot(settings, repo)
    channel, _ = _fake_channel()
    bot.get_channel = MagicMock(return_value=channel)

    guild_me = SimpleNamespace(
        guild_permissions=SimpleNamespace(manage_nicknames=True), top_role=10)
    guild = SimpleNamespace(owner=object(), me=guild_me)
    member = SimpleNamespace(
        id=6161, display_name="Rookie", bot=False, mention="<@6161>",
        guild=guild, roles=[SimpleNamespace(id=settings.target_role_id)],
        top_role=1, send=AsyncMock(), edit=AsyncMock())

    await bot.on_member_join(member)

    member.edit.assert_awaited_once()
    assert member.edit.await_args.kwargs["nick"].startswith(settings.prefix_str)

    await repo.close()


async def test_build_bot_registers_sync_welcome_slash_command():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    assert bot.get_command("sync_welcome") is None          # slash-only, not prefix
    assert bot.tree.get_command("sync_welcome") is not None

    await repo.close()
