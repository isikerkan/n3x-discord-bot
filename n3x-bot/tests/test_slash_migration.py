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
    # `.send` is the DM channel entry point discord exposes on a User/Member;
    # /erfolge now DMs its embed, so the fake caller needs an awaitable one.
    return SimpleNamespace(id=member_id, roles=roles, display_name=display_name,
                           bot=is_bot, send=AsyncMock())


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


async def test_erfolge_app_command_dms_the_embed_to_the_caller():
    # /erfolge now delivers the achievements embed to the caller's DMs
    # (interaction.user.send) rather than posting it in-channel.
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    interaction = _fake_interaction(user=_member(member_id=7, display_name="Erkan"))
    await _app_cmd(bot, "erfolge").callback(interaction)

    interaction.user.send.assert_awaited_once()
    assert _embed_of(interaction.user.send) is not None

    await repo.close()


async def test_erfolge_app_command_acks_interaction_ephemerally():
    # The in-channel interaction reply is now an ephemeral acknowledgement (the
    # embed itself went via DM), reversing the previous public behaviour.
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    interaction = _fake_interaction(user=_member(member_id=7, display_name="Erkan"))
    await _app_cmd(bot, "erfolge").callback(interaction)

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs.get("ephemeral") is True

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


# ── Phase 2: gate GATE commands migrated to slash-only ───────────────────────
#
# `stat` and `del` were prefix gate commands wired by register_gate_commands.
# Phase 2 makes them slash-ONLY app commands on `bot.tree`:
#   /stat gate:<choice>            -> gate stats embed
#   /del  gate:<choice> index:<int> -> role-gated delete + embed refresh
# `gate` is an app_commands.Choice over the 7 gate letters, so the callback
# receives the raw `str` value ("a".."k") and an invalid gate can never reach
# it. `del` is a Python keyword, so the command NAME is "del" but the callback
# function is named differently; tests only ever address it by name via the
# tree, so the function name is irrelevant here.

def _fake_channel(send_return_id=1, channel_id=555):
    channel = MagicMock()
    channel.id = channel_id
    channel.send = AsyncMock(return_value=SimpleNamespace(id=send_return_id))
    old = MagicMock()
    old.delete = AsyncMock()
    old.edit = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=old)
    return channel


# ── /stat ────────────────────────────────────────────────────────────────────

async def test_stat_is_app_command_not_prefix_command():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)

    assert bot.get_command("stat") is None       # dropped from prefix registry
    assert _app_cmd(bot, "stat") is not None      # present on the app tree

    await repo.close()


async def test_stat_app_command_sends_costs_embed():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    await repo.add_gate_entry("a", 46892, 1, "u1")
    await repo.add_gate_entry("a", 47000, 2, "u2")

    interaction = _fake_interaction()
    await _app_cmd(bot, "stat").callback(interaction, gate="a")

    embed = _embed_of(interaction.response.send_message)
    assert embed is not None
    assert "46.892" in embed.description
    assert "47.000" in embed.description

    await repo.close()


async def test_stat_app_command_sends_extra_embeds_via_followup_on_overflow():
    # Enough entries that build_gate_stat_embeds chunks into >1 embed: the first
    # goes through response.send_message, the remainder through followup.send.
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    for i in range(300):
        await repo.add_gate_entry("a", 46000 + i, i, f"u{i}")

    from n3x_bot.bot import build_gate_stat_embeds
    costs = await repo.list_gate_costs("a")
    assert len(build_gate_stat_embeds("a", costs)) > 1  # precondition: overflow

    interaction = _fake_interaction()
    await _app_cmd(bot, "stat").callback(interaction, gate="a")

    # first embed acks the interaction
    assert _embed_of(interaction.response.send_message) is not None
    # at least one continuation embed is delivered via followup
    followup_embeds = [c.kwargs.get("embed")
                       for c in interaction.followup.send.await_args_list
                       if c.kwargs.get("embed") is not None]
    assert len(followup_embeds) >= 1

    await repo.close()


async def test_stat_app_command_reports_no_data_when_empty():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)

    interaction = _fake_interaction()
    await _app_cmd(bot, "stat").callback(interaction, gate="b")

    # a "no data" reply is a plain-text message, not an embed
    sent = interaction.response.send_message.await_args
    text = sent.args[0] if sent.args else sent.kwargs.get("content", "")
    assert "Noch keine Daten" in text
    assert _embed_of(interaction.response.send_message) is None

    await repo.close()


# ── /del ─────────────────────────────────────────────────────────────────────

async def test_del_is_app_command_not_prefix_command():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)

    assert bot.get_command("del") is None
    assert _app_cmd(bot, "del") is not None

    await repo.close()


async def test_del_app_command_denies_without_configured_role():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(gate_delete_role_id=42), repo)
    await repo.add_gate_entry("a", 46892, 1, "u1")

    # invoker holds role 1, not the required 42
    interaction = _fake_interaction(user=_member(member_id=5, role_ids=(1,)))
    await _app_cmd(bot, "del").callback(interaction, gate="a", index=1)

    # deferred ephemerally up front to avoid the 3s interaction timeout
    interaction.response.defer.assert_awaited_once()
    assert interaction.response.defer.await_args.kwargs.get("ephemeral") is True
    interaction.followup.send.assert_awaited_once()
    text = interaction.followup.send.await_args.args[0]
    assert "Keine Berechtigung" in text
    # refusal is private to the caller
    assert interaction.followup.send.await_args.kwargs.get("ephemeral") is True
    # nothing deleted
    assert await repo.list_gate_costs("a") == [46892]

    await repo.close()


async def test_del_app_command_with_role_deletes_and_refreshes_embed():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(gate_delete_role_id=42, gate_stats_channel_id=555),
                    repo)
    channel = _fake_channel(channel_id=555)
    bot.get_channel = MagicMock(return_value=channel)
    await repo.add_gate_entry("a", 46892, 1, "u1")

    interaction = _fake_interaction(user=_member(member_id=5, role_ids=(42,)))
    await _app_cmd(bot, "del").callback(interaction, gate="a", index=1)

    # deferred up front, before the delete + embed-refresh round-trips
    interaction.response.defer.assert_awaited_once()
    assert interaction.response.defer.await_args.kwargs.get("ephemeral") is True
    assert await repo.list_gate_costs("a") == []       # entry removed
    channel.send.assert_awaited()                       # gate embed refreshed
    interaction.followup.send.assert_awaited_once()

    await repo.close()


async def test_del_app_command_reports_index_not_found():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(gate_delete_role_id=42), repo)

    interaction = _fake_interaction(user=_member(member_id=5, role_ids=(42,)))
    await _app_cmd(bot, "del").callback(interaction, gate="a", index=5)

    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()
    text = interaction.followup.send.await_args.args[0]
    assert "nicht gefunden" in text

    await repo.close()
