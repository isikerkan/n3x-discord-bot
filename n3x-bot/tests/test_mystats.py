"""`/meinestats` — personal stats table (gates + counters)."""
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from n3x_bot.mystats import build_mystats_embed
from n3x_bot.storage.json_repo import JsonRepository
from n3x_bot.seed import seed_defaults


async def _repo():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    r = JsonRepository(path)
    await r.connect()
    await seed_defaults(r)
    return r


def _flat(embed) -> str:
    parts = [embed.title or "", embed.description or ""]
    for f in embed.fields:
        parts += [f.name or "", f.value or ""]
    return "\n".join(parts)


# ── pure builder ─────────────────────────────────────────────────────────────

def test_build_shows_gate_runs_total_cost_and_counters():
    embed = build_mystats_embed(
        "Erkan",
        user_stats={"tit": 12, "cry": 5},
        gate_counts={"a": 6, "b": 53},
        gate_cost=1234567,
        stat_names={"tit": "Tit", "cry": "Cry"})
    text = _flat(embed)
    assert "Erkan" in text
    assert "Alpha Gate" in text and "53" in text and "6" in text
    assert "Gesamt" in text and "59" in text          # 6 + 53 total runs
    assert "1.234.567" in text                          # formatted cost
    assert "Tit" in text and "12" in text               # counter row
    # counters sorted highest-first: tit (12) before cry (5)
    assert text.index("Tit") < text.index("Cry")


def test_build_handles_empty_stats_gracefully():
    embed = build_mystats_embed("Neu", user_stats={}, gate_counts={},
                                gate_cost=0, stat_names={})
    text = _flat(embed)
    assert "Noch keine Gates" in text
    assert "Noch keine Befehle" in text


# ── command wiring ───────────────────────────────────────────────────────────

def _settings():
    from n3x_bot.config import Settings
    return Settings(discord_token="t", target_role_id=1, welcome_channel_id=2,
                    reminder_channel_id=3, julez_id=4, _env_file=None,
                    _env_prefix="NONEXISTENT_")


async def test_command_registered_and_sends_embed_with_live_data():
    from n3x_bot.bot import build_bot
    repo = await _repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    # seed some data for user 7
    await repo.record_use(7, "Erkan", "tit")
    await repo.record_use(7, "Erkan", "tit")
    await repo.add_gate_entry("a", 500, 7, "Erkan")

    cmd = bot.tree.get_command("meinestats")
    assert cmd is not None

    interaction = MagicMock()
    interaction.user = SimpleNamespace(id=7, display_name="Erkan")
    interaction.response = MagicMock(send_message=AsyncMock())
    await cmd.callback(interaction)

    embed = interaction.response.send_message.await_args.kwargs["embed"]
    text = _flat(embed)
    assert "Erkan" in text
    assert "Tit" in text and "2" in text          # counter
    assert "Alpha Gate" in text and "1" in text   # gate run
    await repo.close()


# ── /statme <gate> input history ─────────────────────────────────────────────

def test_resolve_gate_by_letter_and_name():
    from n3x_bot.mystats import resolve_gate
    assert resolve_gate("d") == "d"
    assert resolve_gate("delta") == "d"
    assert resolve_gate("Delta Gate") == "d"
    assert resolve_gate("a") == "a"
    assert resolve_gate("nonsense") is None


def test_build_gate_history_lists_entries_with_cost_and_drops():
    from zoneinfo import ZoneInfo
    from datetime import datetime, timezone
    from n3x_bot.mystats import build_gate_history_embed
    tz = ZoneInfo("Europe/Berlin")
    entries = [
        {"cost": 100, "created_at": datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc),
         "drops": {"laser": False}},
        {"cost": 250, "created_at": datetime(2026, 7, 20, 11, 0, tzinfo=timezone.utc),
         "drops": {"laser": True}},
    ]
    embed = build_gate_history_embed("Erkan", "d", entries, tz)
    text = _flat(embed)
    assert "Delta Gate" in text and "Erkan" in text
    assert "100" in text and "250" in text
    assert "Laser" in text                # the drop on entry 2
    assert "2" in text                    # 2 Einträge


def test_build_gate_history_empty_state():
    from zoneinfo import ZoneInfo
    from n3x_bot.mystats import build_gate_history_embed
    embed = build_gate_history_embed("Neu", "a", [], ZoneInfo("Europe/Berlin"))
    assert "Noch keine Einträge" in _flat(embed)


async def test_statme_with_gate_shows_only_callers_own_entries():
    from n3x_bot.bot import build_bot
    repo = await _repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    await repo.add_gate_entry("d", 111, 7, "Erkan")
    await repo.add_gate_entry("d", 222, 7, "Erkan")
    await repo.add_gate_entry("d", 999, 8, "Other")   # different user

    cmd = bot.tree.get_command("statme")
    assert cmd is not None
    interaction = MagicMock()
    interaction.user = SimpleNamespace(id=7, display_name="Erkan")
    interaction.response = MagicMock(send_message=AsyncMock())
    await cmd.callback(interaction, "delta")

    text = _flat(interaction.response.send_message.await_args.kwargs["embed"])
    assert "111" in text and "222" in text
    assert "999" not in text              # other user's entry excluded
    await repo.close()


async def test_statme_without_gate_shows_overview():
    from n3x_bot.bot import build_bot
    repo = await _repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    cmd = bot.tree.get_command("statme")
    interaction = MagicMock()
    interaction.user = SimpleNamespace(id=7, display_name="Erkan")
    interaction.response = MagicMock(send_message=AsyncMock())
    await cmd.callback(interaction, None)
    text = _flat(interaction.response.send_message.await_args.kwargs["embed"])
    assert "Stats von Erkan" in text      # the overview embed
    await repo.close()


async def test_statme_rejects_unknown_gate():
    from n3x_bot.bot import build_bot
    repo = await _repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    cmd = bot.tree.get_command("statme")
    interaction = MagicMock()
    interaction.user = SimpleNamespace(id=7, display_name="Erkan")
    interaction.response = MagicMock(send_message=AsyncMock())
    await cmd.callback(interaction, "banana")
    call = interaction.response.send_message.await_args
    assert call.kwargs.get("ephemeral") is True
    assert "Unbekanntes Gate" in call.args[0]
    await repo.close()
