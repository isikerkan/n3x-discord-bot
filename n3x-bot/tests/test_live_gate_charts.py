"""RED-phase specs for the **live per-gate history charts** feature.

A persistent, auto-updating chart IMAGE for every gate (a/b/c/d/e/z/k) lives in
a configured channel and is refreshed on each new gate run + on ready. It reuses
`n3x_bot.charts.render_gate_history_chart` (PNG bytes) and the self-editing
`channel_messages` message-tracking pattern — but, unlike the gate-stats embed,
the tracked message is an ATTACHMENT, so it is edited via
`msg.edit(attachments=[discord.File(...)])` and (re)posted via
`channel.send(file=discord.File(...))`.

New symbols pinned here (imported LAZILY inside the test bodies that create them
so this module always COLLECTS — the failures are missing-behaviour / missing
attribute, never a collection-time import error):

    n3x_bot.config.Settings.gate_chart_channel_id: int = 0   (env GATE_CHART_CHANNEL_ID)
    "gate_chart_channel_id" in n3x_bot.runtime_config.OVERRIDABLE_KEYS
    n3x_bot.runtime_config.RuntimeConfig.gate_chart_channel_id  (int property, DB>env)
    n3x_bot.config_commands.CHANNEL_PURPOSES["gate_chart"] == "gate_chart_channel_id"
    n3x_bot.bot.update_gate_chart(bot, repo, settings, gate_type) -> None
    n3x_bot.bot.update_all_gate_charts(bot, repo, settings) -> None
    channel_messages key format:  f"gate_chart_{gate_type}"   (e.g. "gate_chart_a")

Wiring pinned (via existing entry-point symbols):
    handle_gate_input_message      (a/b/c) -> update_gate_chart(..., gate_type)
    handle_gate_drop_confirmation (d/e/z/k) -> update_gate_chart(..., gate_type)
    on_ready                                -> update_all_gate_charts(...)

Discord I/O is faked (AsyncMock/MagicMock); the repo is a real, connected
JsonRepository (integration over mocks for anything DB-touching). matplotlib
runs on the Agg backend (already selected inside n3x_bot.charts).
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from n3x_bot.bot import build_bot, handle_gate_input_message
from n3x_bot.config import Settings
from n3x_bot.seed import seed_defaults
from n3x_bot.storage.base import GATE_TYPES
from n3x_bot.storage.json_repo import JsonRepository

CHART_CHANNEL = 666

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


async def _seeded_repo() -> JsonRepository:
    """A repo with a couple of gate-a entries so the chart has real data."""
    repo = await _flatfile_repo()
    await repo.add_gate_entry("a", 500, 7, "Erkan")
    await repo.add_gate_entry("a", 600, 8, "Ali")
    return repo


def _fake_chart_channel(send_return_id: int = 42, channel_id: int = CHART_CHANNEL):
    """A channel whose `.send` returns a msg with `.id` and whose
    `.fetch_message` returns a msg with an awaitable `.edit` — mirrors the
    gate-embed persistence harness, but the payload is an attachment."""
    channel = MagicMock()
    channel.id = channel_id
    channel.send = AsyncMock(return_value=SimpleNamespace(id=send_return_id))
    fetched = MagicMock()
    fetched.edit = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=fetched)
    return channel, fetched


def _fake_gate_message(content: str, *, message_id: int = 8001,
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


def _fallback_guild():
    """A guild with no custom emojis, so the drop-icon resolver uses the fixed
    unicode fallbacks (added to the user's message as plain strings)."""
    guild = MagicMock()
    guild.emojis = []
    return guild


async def _seed_gate_drop(bot, repo, settings, content, *, message_id):
    """Run the input handler for a d/e/z/k message and return (message, added
    reactions in item-then-❌ order)."""
    message = _fake_gate_message(content, message_id=message_id)
    message.guild = _fallback_guild()
    await handle_gate_input_message(bot, repo, settings, message)
    added = [c.args[0] for c in message.add_reaction.await_args_list]
    return message, added


# ══ 1. Config: Settings.gate_chart_channel_id ══════════════════════════════

def test_settings_gate_chart_channel_id_defaults_to_zero():
    s = _settings()
    assert s.gate_chart_channel_id == 0


def test_settings_gate_chart_channel_id_reads_from_env(monkeypatch):
    monkeypatch.setenv("GATE_CHART_CHANNEL_ID", "1234567890")
    # No _env_prefix override here so the real env var is consulted; the .env
    # file is disabled and the required fields are passed explicitly (init args
    # win over env, but gate_chart_channel_id is left to env).
    s = Settings(discord_token="tok", target_role_id=1, welcome_channel_id=2,
                 reminder_channel_id=999, _env_file=None)
    assert s.gate_chart_channel_id == 1234567890


# ══ 2. Resolver: RuntimeConfig.gate_chart_channel_id (DB override else env) ══

def test_resolver_gate_chart_channel_id_passes_through_when_no_override():
    from n3x_bot.runtime_config import RuntimeConfig
    settings = _settings(gate_chart_channel_id=CHART_CHANNEL)
    rc = RuntimeConfig(settings)
    assert rc.gate_chart_channel_id == CHART_CHANNEL


def test_resolver_gate_chart_channel_id_override_wins_and_is_int():
    from n3x_bot.runtime_config import RuntimeConfig
    settings = _settings(gate_chart_channel_id=CHART_CHANNEL)
    rc = RuntimeConfig(settings, {"gate_chart_channel_id": "999"})
    assert rc.gate_chart_channel_id == 999
    assert isinstance(rc.gate_chart_channel_id, int)


def test_overridable_keys_includes_gate_chart_channel_id():
    from n3x_bot.runtime_config import OVERRIDABLE_KEYS
    assert "gate_chart_channel_id" in OVERRIDABLE_KEYS


# ══ 3. Config command purpose mapping ══════════════════════════════════════

def test_channel_purposes_includes_gate_chart():
    from n3x_bot.config_commands import CHANNEL_PURPOSES
    assert CHANNEL_PURPOSES.get("gate_chart") == "gate_chart_channel_id"


# ══ 4. update_gate_chart: first-post sends a File and persists the id ═══════

async def test_update_gate_chart_first_post_sends_file_and_stores():
    from n3x_bot.bot import update_gate_chart
    repo = await _seeded_repo()
    settings = _settings(gate_chart_channel_id=CHART_CHANNEL)
    bot = build_bot(settings, repo)
    channel, _ = _fake_chart_channel(send_return_id=42)
    bot.get_channel = MagicMock(return_value=channel)

    await update_gate_chart(bot, repo, settings, "a")

    channel.send.assert_awaited_once()
    file_arg = channel.send.await_args.kwargs.get("file")
    assert isinstance(file_arg, discord.File)
    assert file_arg.filename == "verlauf_a.png"
    assert await repo.get_channel_message("gate_chart_a") == (42, CHART_CHANNEL)

    await repo.close()


# ══ 5. update_gate_chart: subsequent call EDITS via attachments, no re-send ═

async def test_update_gate_chart_second_call_edits_with_attachments():
    from n3x_bot.bot import update_gate_chart
    repo = await _seeded_repo()
    settings = _settings(gate_chart_channel_id=CHART_CHANNEL)
    bot = build_bot(settings, repo)
    channel, fetched = _fake_chart_channel(send_return_id=42)
    bot.get_channel = MagicMock(return_value=channel)

    await update_gate_chart(bot, repo, settings, "a")
    await update_gate_chart(bot, repo, settings, "a")

    channel.fetch_message.assert_awaited_once_with(42)
    fetched.edit.assert_awaited_once()
    attachments = fetched.edit.await_args.kwargs.get("attachments")
    assert isinstance(attachments, list) and len(attachments) == 1
    assert isinstance(attachments[0], discord.File)
    channel.send.assert_awaited_once()  # only the first call posted
    assert await repo.get_channel_message("gate_chart_a") == (42, CHART_CHANNEL)

    await repo.close()


# ══ 6. update_gate_chart: restart regression — edits stored msg, no re-post ═

async def test_update_gate_chart_restart_edits_stored_message():
    from n3x_bot.bot import update_gate_chart
    repo = await _seeded_repo()
    settings = _settings(gate_chart_channel_id=CHART_CHANNEL)
    # A prior run already posted the chart and persisted its id.
    await repo.set_channel_message("gate_chart_a", 42, CHART_CHANNEL)

    bot = build_bot(settings, repo)  # fresh bot: no in-memory cache
    channel, fetched = _fake_chart_channel(send_return_id=999)
    bot.get_channel = MagicMock(return_value=channel)

    await update_gate_chart(bot, repo, settings, "a")

    channel.fetch_message.assert_awaited_once_with(42)
    fetched.edit.assert_awaited_once()
    channel.send.assert_not_called()  # MUST NOT re-post after restart

    await repo.close()


# ══ 7. update_gate_chart: fetch-fail re-posts and re-stores the new id ══════

async def test_update_gate_chart_fetch_fail_reposts_and_restores():
    from n3x_bot.bot import update_gate_chart
    repo = await _seeded_repo()
    settings = _settings(gate_chart_channel_id=CHART_CHANNEL)
    await repo.set_channel_message("gate_chart_a", 42, CHART_CHANNEL)

    bot = build_bot(settings, repo)
    channel, _ = _fake_chart_channel(send_return_id=99)
    channel.fetch_message = AsyncMock(side_effect=RuntimeError("gone"))
    bot.get_channel = MagicMock(return_value=channel)

    await update_gate_chart(bot, repo, settings, "a")  # must not raise

    channel.send.assert_awaited_once()
    assert await repo.get_channel_message("gate_chart_a") == (99, CHART_CHANNEL)

    await repo.close()


# ══ 8. update_gate_chart: noop paths persist nothing ═══════════════════════

async def test_update_gate_chart_noop_when_channel_unset():
    from n3x_bot.bot import update_gate_chart
    repo = await _seeded_repo()
    settings = _settings(gate_chart_channel_id=0)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock()

    await update_gate_chart(bot, repo, settings, "a")

    bot.get_channel.assert_not_called()
    assert await repo.get_channel_message("gate_chart_a") is None

    await repo.close()


async def test_update_gate_chart_noop_when_channel_missing():
    from n3x_bot.bot import update_gate_chart
    repo = await _seeded_repo()
    settings = _settings(gate_chart_channel_id=CHART_CHANNEL)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)

    await update_gate_chart(bot, repo, settings, "a")  # must not raise

    assert await repo.get_channel_message("gate_chart_a") is None

    await repo.close()


# ══ 8b. update_gate_chart: never raises — swallows send/render failures ════

async def test_update_gate_chart_swallows_send_failure_and_stores_nothing():
    # Contract: "best-effort; never raises." A discord.Forbidden/HTTPException
    # from channel.send (no perms / file too big) must NOT propagate and must
    # leave nothing persisted.
    from n3x_bot.bot import update_gate_chart
    repo = await _seeded_repo()
    settings = _settings(gate_chart_channel_id=CHART_CHANNEL)
    bot = build_bot(settings, repo)
    channel, _ = _fake_chart_channel()
    channel.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403),
                                                           "no perms"))
    bot.get_channel = MagicMock(return_value=channel)

    await update_gate_chart(bot, repo, settings, "a")  # must not raise

    assert await repo.get_channel_message("gate_chart_a") is None

    await repo.close()


async def test_update_gate_chart_swallows_render_failure_and_stores_nothing(monkeypatch):
    # A render error (e.g. matplotlib/font-cache blowup) must also be swallowed.
    from n3x_bot import bot as botmod
    from n3x_bot.bot import update_gate_chart
    repo = await _seeded_repo()
    settings = _settings(gate_chart_channel_id=CHART_CHANNEL)
    bot = build_bot(settings, repo)
    channel, _ = _fake_chart_channel()
    bot.get_channel = MagicMock(return_value=channel)

    def _boom(*_a, **_k):
        raise RuntimeError("render boom")

    monkeypatch.setattr(botmod, "render_gate_history_chart", _boom)

    await update_gate_chart(bot, repo, settings, "a")  # must not raise

    channel.send.assert_not_called()
    assert await repo.get_channel_message("gate_chart_a") is None

    await repo.close()


# ══ 9. update_all_gate_charts: one chart per gate type ═════════════════════

async def test_update_all_gate_charts_posts_and_stores_for_every_gate():
    from n3x_bot.bot import update_all_gate_charts
    repo = await _seeded_repo()
    settings = _settings(gate_chart_channel_id=CHART_CHANNEL)
    bot = build_bot(settings, repo)
    channel, _ = _fake_chart_channel()
    bot.get_channel = MagicMock(return_value=channel)

    await update_all_gate_charts(bot, repo, settings)

    for gate_type in GATE_TYPES:
        stored = await repo.get_channel_message(f"gate_chart_{gate_type}")
        assert stored is not None, gate_type
    assert len(GATE_TYPES) == 7

    await repo.close()


async def test_update_all_gate_charts_is_best_effort_per_gate(monkeypatch):
    # One gate's chart failing must NOT stop the others: every gate is still
    # attempted (the loop wraps each call), and the call itself never raises.
    from n3x_bot import bot as botmod
    from n3x_bot.bot import update_all_gate_charts
    repo = await _seeded_repo()
    settings = _settings(gate_chart_channel_id=CHART_CHANNEL)
    bot = build_bot(settings, repo)

    attempted = []

    async def _flaky(_bot, _repo, _settings, gate_type):
        attempted.append(gate_type)
        if gate_type == "a":
            raise RuntimeError("boom")

    monkeypatch.setattr(botmod, "update_gate_chart", _flaky, raising=False)

    await update_all_gate_charts(bot, repo, settings)  # must not raise

    assert set(attempted) == set(GATE_TYPES)

    await repo.close()


async def test_update_all_gate_charts_noop_when_channel_unset():
    from n3x_bot.bot import update_all_gate_charts
    repo = await _seeded_repo()
    settings = _settings(gate_chart_channel_id=0)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)

    await update_all_gate_charts(bot, repo, settings)

    for gate_type in GATE_TYPES:
        assert await repo.get_channel_message(f"gate_chart_{gate_type}") is None

    await repo.close()


# ══ 10. Auto-refresh on entry: only the affected gate's chart refreshes ═════

async def test_gate_input_entry_refreshes_affected_gate_chart():
    # A valid `a 500` gate input writes the live chart for gate "a".
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777, gate_chart_channel_id=CHART_CHANNEL)
    bot = build_bot(settings, repo)
    channel, _ = _fake_chart_channel()
    bot.get_channel = MagicMock(return_value=channel)
    message = _fake_gate_message("a 500")

    await handle_gate_input_message(bot, repo, settings, message)

    assert await repo.get_channel_message("gate_chart_a") is not None

    await repo.close()


async def test_gate_drop_confirmation_refreshes_gate_chart_d():
    from n3x_bot.bot import handle_gate_drop_confirmation
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777, gate_chart_channel_id=CHART_CHANNEL)
    bot = build_bot(settings, repo)
    channel, fetched = _fake_chart_channel()
    fetched.delete = AsyncMock()
    bot.get_channel = MagicMock(return_value=channel)
    _msg, added = await _seed_gate_drop(bot, repo, settings, "d 250.000",
                                        message_id=5001)

    payload = _fake_reaction_payload(message_id=5001, user_id=7,
                                     emoji=added[0], channel_id=777)
    await handle_gate_drop_confirmation(bot, repo, settings, payload)

    assert await repo.get_channel_message("gate_chart_d") is not None

    await repo.close()


async def test_gate_drop_confirmation_refreshes_gate_chart_k():
    from n3x_bot.bot import handle_gate_drop_confirmation
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777, gate_chart_channel_id=CHART_CHANNEL)
    bot = build_bot(settings, repo)
    channel, fetched = _fake_chart_channel()
    fetched.delete = AsyncMock()
    bot.get_channel = MagicMock(return_value=channel)
    _msg, added = await _seed_gate_drop(bot, repo, settings, "k 500",
                                        message_id=5003)

    payload = _fake_reaction_payload(message_id=5003, user_id=7,
                                     emoji=added[0], channel_id=777)
    await handle_gate_drop_confirmation(bot, repo, settings, payload)

    assert await repo.get_channel_message("gate_chart_k") is not None

    await repo.close()


async def test_kappa_store_processes_achievements_even_when_chart_fails(monkeypatch):
    # The kappa chart refresh has its OWN try/except (mirroring the a/b/c site):
    # a chart failure must NOT skip the kappa entry store or the achievement
    # award/announcement that follow it.
    from n3x_bot.bot import handle_gate_drop_confirmation
    from n3x_bot import bot as botmod
    from n3x_bot import achievements as achmod
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777, gate_chart_channel_id=CHART_CHANNEL)
    bot = build_bot(settings, repo)
    channel, fetched = _fake_chart_channel()
    fetched.delete = AsyncMock()
    bot.get_channel = MagicMock(return_value=channel)

    chart = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(botmod, "update_gate_chart", chart, raising=False)

    checked = []
    real_check = achmod.check_achievements

    async def _tracking_check(_repo, user_id, category, defs=None):
        checked.append(category)
        return await real_check(_repo, user_id, category)

    monkeypatch.setattr(botmod, "check_achievements", _tracking_check)

    _msg, added = await _seed_gate_drop(bot, repo, settings, "k 500",
                                        message_id=5005)
    payload = _fake_reaction_payload(message_id=5005, user_id=7,
                                     emoji=added[0], channel_id=777)

    await handle_gate_drop_confirmation(bot, repo, settings, payload)  # no raise

    assert await repo.list_gate_costs("k") == [500]  # kappa entry survived
    chart.assert_awaited()  # chart refresh was attempted
    # achievements were still processed despite the chart failure
    assert set(checked) == {"gate_k", "gate_total", "gate_cost_total"}

    await repo.close()


async def test_gate_entry_records_even_when_chart_update_fails(monkeypatch):
    # Best-effort: a chart-refresh failure must NOT break the entry recording,
    # and the affected gate's chart refresh is still attempted.
    from n3x_bot import bot as botmod
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777, gate_chart_channel_id=CHART_CHANNEL)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)
    chart = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(botmod, "update_gate_chart", chart, raising=False)
    message = _fake_gate_message("a 500")

    await handle_gate_input_message(bot, repo, settings, message)  # must not raise

    assert await repo.list_gate_costs("a") == [500]  # entry survived
    chart.assert_awaited()  # affected gate's chart refresh was attempted

    await repo.close()


# ══ 11. Wiring: on_ready posts every live chart ════════════════════════════

async def test_on_ready_updates_all_gate_charts_when_channel_configured():
    repo = await _seeded_repo()
    settings = _settings(gate_chart_channel_id=CHART_CHANNEL)
    bot = build_bot(settings, repo)
    channel, _ = _fake_chart_channel()
    bot.get_channel = MagicMock(return_value=channel)
    bot.tree.sync = AsyncMock()

    await bot.on_ready()

    for gate_type in GATE_TYPES:
        assert await repo.get_channel_message(f"gate_chart_{gate_type}") is not None, gate_type

    await repo.close()
