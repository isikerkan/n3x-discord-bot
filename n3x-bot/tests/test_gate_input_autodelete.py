"""RED spec: auto-delete completed gate-input messages after a CONFIGURABLE delay.

FEATURE: after a completed gate store the bot schedules deletion of the user's
original message after a configurable delay (default 1 minute / 60s) via
``message.delete(delay=<seconds>)`` — best-effort, guarded so a failure never
crashes the handler. The seconds come from
``bot.runtime_config.gate_delete_delay_seconds`` (a resolver property).

This module covers the a/b/c (no-drop) path in ``handle_gate_input_message``:
  * successful store (``inserted`` truthy) -> ``message.delete(delay=<seconds>)``
  * dedup reject (``inserted`` falsy -> ⏳) -> NO delete
  * regression: ✅ + post-processing still run on success
The d/e/z/k (drop-confirm) deletion lives in ``handle_gate_drop_confirmation``
and is covered in ``tests/test_gate_drop_reactions.py``; here we only pin that
the drop-gate INPUT handler never deletes.

Discord I/O is faked (AsyncMock/MagicMock); the repo is a real, connected
JsonRepository (integration over mocks for anything DB-touching). The delay is
driven by replacing ``bot.runtime_config`` with a resolver stub whose
``gate_delete_delay_seconds`` returns a known value, and asserting that exact
value is passed as the ``delay`` kwarg.
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

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


def _fallback_guild():
    """A guild with no custom emojis (only needed for the d/e/z/k drop path)."""
    guild = MagicMock()
    guild.emojis = []
    return guild


def _fake_gate_message(content: str, *, message_id: int = 8001,
                       author_id: int = 7, author_name: str = "Erkan",
                       channel_id: int = 777):
    message = MagicMock()
    message.id = message_id
    message.content = content
    message.author = SimpleNamespace(id=author_id, name=author_name,
                                     mention=f"<@{author_id}>")
    message.guild = _fallback_guild()
    message.add_reaction = AsyncMock()
    message.delete = AsyncMock()
    channel = MagicMock()
    channel.id = channel_id
    channel.send = AsyncMock()
    message.channel = channel
    return message


def _quiet_postprocessing(monkeypatch):
    """Stub the a/b/c post-processing so the handler runs without live channels;
    returns the embed/records mocks for the regression assertions."""
    from n3x_bot import bot as botmod
    embed = AsyncMock()
    announce_rec = AsyncMock()
    monkeypatch.setattr(botmod, "update_gate_stats_embed", embed)
    monkeypatch.setattr(botmod, "update_gate_chart", AsyncMock())
    monkeypatch.setattr(botmod, "_announce_records", announce_rec)
    monkeypatch.setattr(botmod, "check_achievements", AsyncMock(return_value=[]))
    monkeypatch.setattr(botmod, "announce_achievements", AsyncMock())
    return embed, announce_rec


def _with_delay(bot, seconds: int):
    """Replace the resolver with a stub exposing the configured delete delay.
    The a/b/c store path reads no other runtime_config attribute once the
    post-processing is stubbed, so a minimal stub is sufficient."""
    bot.runtime_config = SimpleNamespace(gate_delete_delay_seconds=seconds)
    return bot


# ── successful a/b/c store schedules delete with the CONFIGURED delay ─────────

@pytest.mark.parametrize("gate_type,cost", [("a", 500), ("b", 600), ("c", 700)])
async def test_abc_success_schedules_delete_with_configured_delay(monkeypatch,
                                                                 gate_type, cost):
    _quiet_postprocessing(monkeypatch)
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = _with_delay(build_bot(settings, repo), 60)
    message = _fake_gate_message(f"{gate_type} {cost}")

    await handle_gate_input_message(bot, repo, settings, message)

    # scheduled delete carries the resolver's configured seconds as `delay`
    message.delete.assert_awaited_once_with(delay=60)

    await repo.close()


async def test_abc_success_delay_reflects_resolver_override_value(monkeypatch):
    # A different configured value (e.g. from a "2m" override) must flow through
    # to the delete call unchanged — the delay is not hardcoded.
    _quiet_postprocessing(monkeypatch)
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = _with_delay(build_bot(settings, repo), 120)
    message = _fake_gate_message("a 500")

    await handle_gate_input_message(bot, repo, settings, message)

    message.delete.assert_awaited_once_with(delay=120)

    await repo.close()


async def test_abc_success_delete_uses_keyword_delay_not_positional(monkeypatch):
    # discord.py's Message.delete(self, *, delay=None) is keyword-only.
    _quiet_postprocessing(monkeypatch)
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = _with_delay(build_bot(settings, repo), 60)
    message = _fake_gate_message("a 500")

    await handle_gate_input_message(bot, repo, settings, message)

    call = message.delete.await_args
    assert call.args == ()
    assert call.kwargs == {"delay": 60}

    await repo.close()


# ── dedup reject keeps the message (⏳ stays as the duplicate signal) ─────────

async def test_abc_dedup_reject_does_not_delete_and_adds_hourglass(monkeypatch):
    _quiet_postprocessing(monkeypatch)
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = _with_delay(build_bot(settings, repo), 60)
    # Pre-insert the identical entry so the second store is a dedup rejection.
    await repo.add_gate_entry("a", 500, 7, "Erkan")
    message = _fake_gate_message("a 500", author_id=7, author_name="Erkan")

    await handle_gate_input_message(bot, repo, settings, message)

    message.delete.assert_not_awaited()  # ⏳ stays, message kept
    reacts = [c.args[0] for c in message.add_reaction.await_args_list]
    assert "⏳" in reacts
    assert "✅" not in reacts

    await repo.close()


# ── regression: ✅ + post-processing still happen on a successful store ───────

async def test_abc_success_still_reacts_check_and_runs_post_processing(monkeypatch):
    embed, announce_rec = _quiet_postprocessing(monkeypatch)
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = _with_delay(build_bot(settings, repo), 60)
    message = _fake_gate_message("a 500")

    await handle_gate_input_message(bot, repo, settings, message)

    reacts = [c.args[0] for c in message.add_reaction.await_args_list]
    assert "✅" in reacts
    assert await repo.list_gate_costs("a") == [500]  # actually stored
    embed.assert_awaited()          # stats embed refreshed
    announce_rec.assert_awaited()   # record announcement ran

    await repo.close()


# ── guard: a delete failure never crashes the handler ────────────────────────

async def test_abc_success_survives_delete_failure(monkeypatch):
    _quiet_postprocessing(monkeypatch)
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = _with_delay(build_bot(settings, repo), 60)
    message = _fake_gate_message("a 500")
    message.delete = AsyncMock(side_effect=RuntimeError("no perms"))

    # must not raise despite the delayed-delete scheduling blowing up
    await handle_gate_input_message(bot, repo, settings, message)

    assert await repo.list_gate_costs("a") == [500]  # store still happened
    message.delete.assert_awaited_once_with(delay=60)

    await repo.close()


# ── out of scope: d/e/z/k input handler never deletes the message ────────────

@pytest.mark.parametrize("content", ["d 250.000", "e 46.892", "z 1.234", "k 500"])
async def test_dezk_input_does_not_delete_message(content):
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    message = _fake_gate_message(content)

    await handle_gate_input_message(bot, repo, settings, message)

    # deletion for drop gates happens later, on the drop-icon click confirmation
    message.delete.assert_not_called()

    await repo.close()


# ── German in-channel confirmation for a/b/c registered stats ─────────────────
#
# After a SUCCESSFUL a/b/c store the bot posts a German confirmation into the
# channel, addressed to the user (mention), naming the gate and the formatted
# cost, and schedules it to delete with the SAME configured delay as the
# original message. The confirmation is best-effort/guarded and never fires on a
# dedup rejection.


def _confirm_message():
    """A stand-in for the sent confirmation message: awaitable ``.delete``."""
    confirm = MagicMock()
    confirm.delete = AsyncMock()
    return confirm


@pytest.mark.parametrize("delay", [60, 120])
async def test_abc_success_sends_german_confirmation_deleted_with_delay(
        monkeypatch, delay):
    _quiet_postprocessing(monkeypatch)
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = _with_delay(build_bot(settings, repo), delay)
    message = _fake_gate_message("a 75000")
    confirm = _confirm_message()
    message.channel.send = AsyncMock(return_value=confirm)

    await handle_gate_input_message(bot, repo, settings, message)

    message.channel.send.assert_awaited_once()
    text = message.channel.send.await_args.args[0]
    assert message.author.mention in text        # addressed to the user
    assert "registriert" in text                 # German confirmation wording
    assert "75.000" in text                       # format_number(75000)
    # deleted with the SAME configured delay as the original message
    confirm.delete.assert_awaited_once_with(delay=delay)

    await repo.close()


async def test_abc_confirmation_names_the_gate(monkeypatch):
    _quiet_postprocessing(monkeypatch)
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = _with_delay(build_bot(settings, repo), 60)
    message = _fake_gate_message("b 600")
    confirm = _confirm_message()
    message.channel.send = AsyncMock(return_value=confirm)

    await handle_gate_input_message(bot, repo, settings, message)

    text = message.channel.send.await_args.args[0]
    assert "Beta Gate" in text  # GATE_NAMES["b"]

    await repo.close()


async def test_abc_dedup_reject_sends_no_confirmation(monkeypatch):
    _quiet_postprocessing(monkeypatch)
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = _with_delay(build_bot(settings, repo), 60)
    # Pre-insert the identical entry so the second store is a dedup rejection.
    await repo.add_gate_entry("a", 500, 7, "Erkan")
    message = _fake_gate_message("a 500", author_id=7, author_name="Erkan")
    message.channel.send = AsyncMock()

    await handle_gate_input_message(bot, repo, settings, message)

    message.channel.send.assert_not_awaited()  # ⏳ path: no confirmation

    await repo.close()


async def test_abc_confirmation_send_failure_swallowed_store_and_delete_intact(
        monkeypatch):
    # A failing channel.send must not crash the handler nor skip the store /
    # original-message delete scheduling.
    _quiet_postprocessing(monkeypatch)
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = _with_delay(build_bot(settings, repo), 60)
    message = _fake_gate_message("a 500")
    message.channel.send = AsyncMock(side_effect=RuntimeError("no perms"))

    await handle_gate_input_message(bot, repo, settings, message)  # no raise

    assert await repo.list_gate_costs("a") == [500]        # store still happened
    message.delete.assert_awaited_once_with(delay=60)      # original delete intact

    await repo.close()
