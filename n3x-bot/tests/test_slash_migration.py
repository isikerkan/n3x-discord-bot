"""RED-phase specs for Phase 1 of the prefix -> slash migration.

Phase 1 is the FOUNDATION plus a small proof batch. It pins:

  * Part B — a reusable app-command admin gate ``n3x_bot.admin.app_is_admin``
    (mirrors the prefix ``is_admin`` semantics against ``interaction.user``).
  * Part C — the proof batch: ``erfolge``, ``activity`` and ``overview`` become
    slash-ONLY app commands on ``bot.tree`` (global registration; the guild copy
    is handled by ``sync_commands_to_guilds``) and are REMOVED from the prefix
    registry (``bot.commands``).

New symbols are referenced via the module object (``adminmod.app_is_admin``)
rather than imported at module scope, so a not-yet-implemented symbol fails the
individual test with ``AttributeError`` (a correct pre-impl RED) instead of
breaking collection with an ImportError.

The interaction fakes mirror the existing ``/admin`` and ``/config`` slash tests
(``interaction.user`` / ``interaction.response.send_message`` /
``interaction.response.defer`` / ``interaction.followup.send``), and the
tree-presence assertions mirror ``test_admin_commands`` (``bot.tree`` lookups).
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import n3x_bot.admin as adminmod
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


def _member(*, member_id=5, role_ids=(), display_name="Erkan", is_bot=False):
    roles = [SimpleNamespace(id=r) for r in role_ids]
    return SimpleNamespace(id=member_id, roles=roles, display_name=display_name,
                           bot=is_bot)


def _fake_interaction(user=None, guild=None):
    it = MagicMock()
    it.user = user or _member(member_id=7, display_name="Erkan")
    it.guild = guild
    it.response = MagicMock()
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.followup = MagicMock()
    it.followup.send = AsyncMock()
    return it


def _app_cmd(bot, name):
    """The global chat-input app command registered on the tree, or None."""
    return bot.tree.get_command(name)


def _embed_of(send_mock):
    for call in send_mock.await_args_list:
        embed = call.kwargs.get("embed")
        if embed is not None:
            return embed
    return None


# ── Part B: reusable app-command admin gate ──────────────────────────────────

async def test_app_is_admin_true_for_member_holding_admin_role():
    settings = _settings(admin_role_id=42)
    interaction = _fake_interaction(user=_member(member_id=5, role_ids=(7, 42)))

    assert adminmod.app_is_admin(interaction, settings) is True


async def test_app_is_admin_false_for_member_without_admin_role():
    settings = _settings(admin_role_id=42)
    interaction = _fake_interaction(user=_member(member_id=5, role_ids=(7, 8)))

    assert adminmod.app_is_admin(interaction, settings) is False


async def test_app_is_admin_false_when_admin_role_unset():
    # admin_role_id defaults to 0 (feature disabled); a role id that happens to
    # be 0 must NOT grant admin.
    settings = _settings(admin_role_id=0)
    interaction = _fake_interaction(user=_member(member_id=5, role_ids=(0,)))

    assert adminmod.app_is_admin(interaction, settings) is False


async def test_app_is_admin_false_for_user_without_roles_attribute():
    # A DM/global user has no `.roles`; the gate must return False, not raise.
    settings = _settings(admin_role_id=42)
    interaction = _fake_interaction(user=SimpleNamespace(id=5))

    assert adminmod.app_is_admin(interaction, settings) is False


# ── Part C: erfolge migrated to slash-only ───────────────────────────────────

async def test_erfolge_is_app_command_not_prefix_command():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    assert bot.get_command("erfolge") is None       # dropped from prefix registry
    assert _app_cmd(bot, "erfolge") is not None      # present on the app tree

    await repo.close()


async def test_erfolge_app_command_responds_with_embed_for_caller():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    interaction = _fake_interaction(user=_member(member_id=7, display_name="Erkan"))
    await _app_cmd(bot, "erfolge").callback(interaction)

    interaction.response.send_message.assert_awaited_once()
    assert _embed_of(interaction.response.send_message) is not None

    await repo.close()


async def test_erfolge_app_command_response_is_not_ephemeral():
    # Current prefix behavior is a public post; the slash version should stay
    # public (not ephemeral) to preserve that.
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    interaction = _fake_interaction(user=_member(member_id=7, display_name="Erkan"))
    await _app_cmd(bot, "erfolge").callback(interaction)

    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs.get("ephemeral") is not True

    await repo.close()


# ── Part C: activity migrated to slash-only ──────────────────────────────────

async def test_activity_is_app_command_not_prefix_command():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    assert bot.get_command("activity") is None
    assert _app_cmd(bot, "activity") is not None

    await repo.close()


async def test_activity_app_command_defaults_to_caller():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    await repo.add_activity(7, "messages", 17)

    interaction = _fake_interaction(user=_member(member_id=7, display_name="Erkan"))
    await _app_cmd(bot, "activity").callback(interaction)  # no member -> caller

    embed = _embed_of(interaction.response.send_message)
    assert embed is not None
    assert "Erkan" in (embed.title or "")

    await repo.close()


async def test_activity_app_command_uses_explicit_member():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    await repo.add_activity(99, "messages", 5)

    interaction = _fake_interaction(user=_member(member_id=7, display_name="Erkan"))
    target = _member(member_id=99, display_name="Ziel")
    await _app_cmd(bot, "activity").callback(interaction, target)

    embed = _embed_of(interaction.response.send_message)
    assert embed is not None
    assert "Ziel" in (embed.title or "")  # explicit member wins over caller

    await repo.close()


# ── Part C: overview migrated to slash-only ──────────────────────────────────

async def test_overview_is_app_command_not_prefix_command():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    assert bot.get_command("overview") is None
    assert _app_cmd(bot, "overview") is not None

    await repo.close()


async def test_overview_app_command_defers_before_posting():
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)
    await repo.unlock_achievement(10, "a_5")  # a holder exists so post_overview posts

    order = []
    msg = MagicMock()
    msg.id = 555
    msg.add_reaction = AsyncMock()

    async def _send(*a, **k):
        order.append("post")
        return msg

    channel = MagicMock()
    channel.send = AsyncMock(side_effect=_send)
    bot.get_channel = MagicMock(return_value=channel)

    interaction = _fake_interaction()
    interaction.response.defer = AsyncMock(
        side_effect=lambda *a, **k: order.append("defer"))
    interaction.followup.send = AsyncMock(
        side_effect=lambda *a, **k: order.append("followup"))

    await _app_cmd(bot, "overview").callback(interaction)

    interaction.response.defer.assert_awaited_once()
    # deferred ephemerally, so the ack is private to the caller.
    assert interaction.response.defer.await_args.kwargs.get("ephemeral") is True
    channel.send.assert_awaited_once()  # the slow work ran
    # a followup resolves the deferred "thinking…" state after the post.
    interaction.followup.send.assert_awaited_once()
    assert order == ["defer", "post", "followup"]  # defer FIRST, followup LAST

    await repo.close()
