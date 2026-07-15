"""RED tests for the Epsilon / Zeta / Kappa (e/z/k) gates.

Three new gates extending the reaction-confirmed Delta pattern:
  * Epsilon `e` — item LF4 (1 drop), reaction-confirmed ✅/❎ like Delta.
  * Zeta `z`    — item Havoc (1 drop), reaction-confirmed ✅/❎ like Delta.
  * Kappa `k`   — items Hercules + LF4-U (2 drops), confirmed via a discord.py
                  button panel (KappaConfirmView): Hercules toggle, LF4-U
                  toggle, Submit. Author-only; on Submit stores both drop bools.

Discord I/O is faked (AsyncMock/MagicMock); the repo is a real, connected
JsonRepository (integration over mocks for anything DB-touching). All new
symbols are imported lazily inside the test bodies that need them so this
module always collects (failures are missing-behaviour, not import errors).

New symbols/behaviour pinned here (see report for signatures + assumptions):

  n3x_bot.gates.KappaConfirmView(repo, bot, settings, *, cost, user_id, username)
      discord.ui.View with >=3 children (Hercules toggle / LF4-U toggle /
      Submit). State attrs: view.hercules_dropped, view.lf4u_dropped (both
      default False). Async methods (the button callbacks delegate to these):
        await view.on_toggle_hercules(interaction)  -> flips hercules_dropped
        await view.on_toggle_lf4u(interaction)       -> flips lf4u_dropped
        await view.on_submit(interaction)            -> author-only store of
            gate "k" with drops={"hercules": ..., "lf4u": ...}

  handle_gate_input_message:
      e/z -> seed bot._pending_delta[message.id] (incl. "gate_type") and react
             ✅ + ❎ WITHOUT storing (like Delta).
      k   -> send a KappaConfirmView on the message's channel WITHOUT storing.

  handle_delta_confirmation now routes d/e/z off pending["gate_type"] (default
  "d") and stores the item drop for that gate (d->laser, e->lf4, z->havoc).
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from n3x_bot.bot import build_bot, handle_gate_input_message
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


def _fake_gate_message(content: str, *, message_id: int = 6001,
                       author_id: int = 7, author_name: str = "Erkan",
                       channel_id: int = 777):
    message = MagicMock()
    message.id = message_id
    message.content = content
    message.author = SimpleNamespace(id=author_id, name=author_name)
    message.add_reaction = AsyncMock()
    message.delete = AsyncMock()
    channel = MagicMock()
    channel.id = channel_id
    channel.send = AsyncMock()
    message.channel = channel
    return message


def _fake_reaction_payload(*, message_id: int, user_id: int, emoji: str,
                           channel_id: int):
    return SimpleNamespace(message_id=message_id, user_id=user_id,
                           emoji=emoji, channel_id=channel_id, guild_id=1)


def _fake_interaction(user_id: int):
    interaction = MagicMock()
    interaction.user = SimpleNamespace(id=user_id)
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.message = MagicMock()
    interaction.message.edit = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


# ── Epsilon / Zeta input seeds a pending reaction-confirmation ───────────────

async def test_epsilon_input_registers_pending_with_gate_type_and_reacts():
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)
    message = _fake_gate_message("e 46.892", message_id=6001, author_id=7,
                                 author_name="Erkan")

    await handle_gate_input_message(bot, repo, settings, message)

    assert await repo.list_gate_costs("e") == []  # not stored yet
    pending = bot._pending_delta[6001]
    assert pending["cost"] == 46892
    assert pending["user_id"] == 7
    assert pending["username"] == "Erkan"
    assert pending["gate_type"] == "e"
    reacted = {c.args[0] for c in message.add_reaction.await_args_list}
    assert reacted == {"✅", "❎"}

    await repo.close()


async def test_zeta_input_registers_pending_with_gate_type_and_reacts():
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)
    message = _fake_gate_message("z 1.234", message_id=6002, author_id=7)

    await handle_gate_input_message(bot, repo, settings, message)

    assert await repo.list_gate_costs("z") == []
    assert bot._pending_delta[6002]["gate_type"] == "z"

    await repo.close()


# ── Epsilon / Zeta confirmation stores the right item drop ───────────────────

async def test_epsilon_confirmation_check_stores_lf4_drop_true():
    from n3x_bot.bot import handle_delta_confirmation
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)
    bot._pending_delta = {6001: {"cost": 46892, "user_id": 7,
                                 "username": "Erkan", "gate_type": "e"}}

    payload = _fake_reaction_payload(message_id=6001, user_id=7,
                                     emoji="✅", channel_id=777)
    await handle_delta_confirmation(bot, repo, settings, payload)

    assert await repo.list_gate_costs("e") == [46892]
    stats = await repo.gate_drop_stats("e")
    assert stats["count"] == 1
    assert stats["rates"]["lf4"] == 100.0
    assert 6001 not in bot._pending_delta

    await repo.close()


async def test_zeta_confirmation_cross_stores_havoc_drop_false():
    from n3x_bot.bot import handle_delta_confirmation
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)
    bot._pending_delta = {6002: {"cost": 1234, "user_id": 7,
                                 "username": "Erkan", "gate_type": "z"}}

    payload = _fake_reaction_payload(message_id=6002, user_id=7,
                                     emoji="❎", channel_id=777)
    await handle_delta_confirmation(bot, repo, settings, payload)

    assert await repo.list_gate_costs("z") == [1234]
    assert (await repo.gate_drop_stats("z"))["rates"]["havoc"] == 0.0

    await repo.close()


async def test_epsilon_confirmation_by_non_author_is_ignored_then_author_stores():
    # Non-author reaction is ignored AND leaves the pending intact, so the
    # author can still confirm afterwards and get the epsilon entry stored.
    from n3x_bot.bot import handle_delta_confirmation
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)
    bot._pending_delta = {6001: {"cost": 46892, "user_id": 7,
                                 "username": "Erkan", "gate_type": "e"}}

    non_author = _fake_reaction_payload(message_id=6001, user_id=8,
                                        emoji="✅", channel_id=777)
    await handle_delta_confirmation(bot, repo, settings, non_author)
    assert await repo.list_gate_costs("e") == []      # nothing stored
    assert 6001 in bot._pending_delta                 # pending preserved

    author = _fake_reaction_payload(message_id=6001, user_id=7,
                                    emoji="✅", channel_id=777)
    await handle_delta_confirmation(bot, repo, settings, author)
    assert await repo.list_gate_costs("e") == [46892]  # now stored as epsilon

    await repo.close()


# ── Kappa input posts a button panel, stores nothing yet ─────────────────────

async def test_kappa_input_sends_button_view_and_stores_nothing():
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)
    message = _fake_gate_message("k 500", message_id=6003, author_id=7)

    await handle_gate_input_message(bot, repo, settings, message)

    assert await repo.list_gate_costs("k") == []  # awaits the panel
    message.channel.send.assert_awaited()
    view = message.channel.send.await_args.kwargs.get("view")
    assert isinstance(view, discord.ui.View)
    # Hercules toggle + LF4-U toggle + Submit
    assert len(view.children) >= 3

    await repo.close()


# ── KappaConfirmView toggles + author-only submit stores both drop bools ─────

async def test_kappa_view_defaults_both_drops_to_false():
    from n3x_bot.gates import KappaConfirmView
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    view = KappaConfirmView(repo, bot, settings, cost=500, user_id=7,
                            username="Erkan")
    assert view.hercules_dropped is False
    assert view.lf4u_dropped is False
    await repo.close()


async def test_kappa_submit_stores_toggled_drops_for_author():
    from n3x_bot.gates import KappaConfirmView
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)
    view = KappaConfirmView(repo, bot, settings, cost=500, user_id=7,
                            username="Erkan")

    # author toggles Hercules on, leaves LF4-U off, then submits
    await view.on_toggle_hercules(_fake_interaction(7))
    await view.on_submit(_fake_interaction(7))

    assert await repo.list_gate_costs("k") == [500]
    rates = (await repo.gate_drop_stats("k"))["rates"]
    assert rates["hercules"] == 100.0
    assert rates["lf4u"] == 0.0

    await repo.close()


async def test_kappa_submit_stores_both_drops_true_when_both_toggled():
    from n3x_bot.gates import KappaConfirmView
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)
    view = KappaConfirmView(repo, bot, settings, cost=750, user_id=7,
                            username="Erkan")

    await view.on_toggle_hercules(_fake_interaction(7))
    await view.on_toggle_lf4u(_fake_interaction(7))
    await view.on_submit(_fake_interaction(7))

    rates = (await repo.gate_drop_stats("k"))["rates"]
    assert rates["hercules"] == 100.0
    assert rates["lf4u"] == 100.0

    await repo.close()


async def test_kappa_second_submit_by_author_does_not_double_store():
    # timeout=None keeps the panel live, so the author can click "Bestätigen"
    # again. A second submit — even past add_gate_entry's 30s dedup window —
    # must be a no-op: exactly ONE "k" entry stays stored (the panel is
    # consumed on the first submit).
    from n3x_bot.gates import KappaConfirmView
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)
    view = KappaConfirmView(repo, bot, settings, cost=500, user_id=7,
                            username="Erkan")

    await view.on_toggle_hercules(_fake_interaction(7))
    await view.on_submit(_fake_interaction(7))
    assert await repo.list_gate_costs("k") == [500]

    # Age the stored row past the dedup window so storage-level dedup can't be
    # what saves us — the view's own consume guard must.
    for r in repo._db["gate_entries"]:
        r["created_at"] = "2000-01-01T00:00:00+00:00"

    await view.on_submit(_fake_interaction(7))  # second click

    assert await repo.list_gate_costs("k") == [500]  # still exactly one
    assert (await repo.gate_drop_stats("k"))["count"] == 1

    await repo.close()


async def test_kappa_submit_by_non_author_stores_nothing():
    from n3x_bot.gates import KappaConfirmView
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)
    view = KappaConfirmView(repo, bot, settings, cost=500, user_id=7,
                            username="Erkan")

    await view.on_toggle_hercules(_fake_interaction(7))
    await view.on_submit(_fake_interaction(999))  # not the author

    assert await repo.list_gate_costs("k") == []

    await repo.close()


async def test_kappa_toggle_by_non_author_does_not_change_state():
    from n3x_bot.gates import KappaConfirmView
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    view = KappaConfirmView(repo, bot, settings, cost=500, user_id=7,
                            username="Erkan")

    await view.on_toggle_hercules(_fake_interaction(999))  # not the author

    assert view.hercules_dropped is False

    await repo.close()


# ── !stat accepts e/z/k (via GATE_TYPES, not gate_rewards) ───────────────────

async def test_stat_command_accepts_kappa_and_lists_costs():
    # ASSUMPTION / needed change: _handle_gate_stat currently gates on
    # gate_rewards_map(), which has no e/z/k -> "!stat k" is rejected today.
    # e/z/k should be accepted as valid gate types (they simply have no reward).
    from n3x_bot.bot import _handle_gate_stat
    repo = await _flatfile_repo()
    settings = _settings()
    await repo.add_gate_entry("k", 500, 7, "Erkan",
                              drops={"hercules": True, "lf4u": False})

    ctx = MagicMock()
    ctx.send = AsyncMock()
    await _handle_gate_stat(ctx, repo, settings, "k")

    sent = ""
    for call in ctx.send.await_args_list:
        if call.args:
            sent += str(call.args[0])
        embed = call.kwargs.get("embed")
        if embed is not None:
            sent += str(getattr(embed, "title", "")) + str(getattr(embed, "description", ""))
    assert "Ungültiger Gate-Typ" not in sent
    assert "500" in sent

    await repo.close()
