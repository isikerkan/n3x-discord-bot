"""`/gatelog` — admin listing of all users' gate entries, sorted."""
import os
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from n3x_bot.gatelog import build_gatelog_embeds, _sort_entries
from n3x_bot.storage.json_repo import JsonRepository
from n3x_bot.seed import seed_defaults

TZ = ZoneInfo("Europe/Berlin")


async def _repo():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    r = JsonRepository(path)
    await r.connect()
    await seed_defaults(r)
    return r


def _flat(embeds) -> str:
    parts = []
    for e in embeds:
        parts += [e.title or "", e.description or ""]
    return "\n".join(parts)


def _e(cost, user, gate="d", drops=None, minute=0):
    return {"gate_type": gate, "cost": cost, "user_id": 1, "username": user,
            "drops": drops or {},
            "created_at": datetime(2026, 7, 20, 10, minute, tzinfo=timezone.utc)}


# ── sorting ──────────────────────────────────────────────────────────────────

def test_sort_by_cost_desc():
    entries = [_e(100, "A"), _e(500, "B"), _e(250, "C")]
    got = [e["cost"] for e in _sort_entries(entries, "cost")]
    assert got == [500, 250, 100]


def test_sort_by_date_asc():
    entries = [_e(1, "A", minute=5), _e(1, "B", minute=1), _e(1, "C", minute=3)]
    got = [e["username"] for e in _sort_entries(entries, "date")]
    assert got == ["B", "C", "A"]


def test_sort_by_user():
    entries = [_e(1, "Zoe"), _e(1, "amy"), _e(1, "Ben")]
    got = [e["username"] for e in _sort_entries(entries, "user")]
    assert got == ["amy", "Ben", "Zoe"]


# ── embed building ───────────────────────────────────────────────────────────

def test_build_lists_all_users_with_cost_and_gate_tag():
    entries = [_e(100, "Alice", gate="a"), _e(999, "Bob", gate="d",
                                             drops={"laser": True})]
    embeds = build_gatelog_embeds(entries, None, "cost", TZ)   # all gates
    text = _flat(embeds)
    assert "Alle Gates" in text
    assert "Alice" in text and "Bob" in text
    assert "100" in text and "999" in text
    assert "[A]" in text and "[D]" in text     # gate tag when listing all
    assert "Laser" in text
    assert "2" in text                          # 2 Einträge


def test_build_single_gate_omits_gate_tag():
    embeds = build_gatelog_embeds([_e(100, "Alice", gate="d")], "d", "date", TZ)
    text = _flat(embeds)
    assert "Delta Gate" in text
    assert "[D]" not in text                     # single gate -> no tag


def test_build_chunks_into_multiple_embeds():
    entries = [_e(i, f"U{i}", minute=i % 60) for i in range(1, 46)]  # 45 entries
    embeds = build_gatelog_embeds(entries, "d", "date", TZ)
    assert len(embeds) == 3                       # 20 + 20 + 5


def test_build_empty():
    embeds = build_gatelog_embeds([], "d", "date", TZ)
    assert "Keine Einträge" in _flat(embeds)


# ── command wiring ───────────────────────────────────────────────────────────

def _settings():
    from n3x_bot.config import Settings
    return Settings(discord_token="t", target_role_id=1, welcome_channel_id=2,
                    reminder_channel_id=3, julez_id=4, admin_role_id=42,
                    _env_file=None, _env_prefix="NONEXISTENT_")


async def test_gatelog_admin_gated():
    from n3x_bot.bot import build_bot
    repo = await _repo()
    bot = build_bot(_settings(), repo)
    cmd = bot.tree.get_command("gatelog")
    assert cmd is not None
    interaction = MagicMock()
    interaction.user = SimpleNamespace(id=1, roles=[])       # not admin
    interaction.response = MagicMock(send_message=AsyncMock(), defer=AsyncMock())
    await cmd.callback(interaction, None, None)
    interaction.response.send_message.assert_awaited_once()
    assert "Berechtigung" in interaction.response.send_message.await_args.args[0]
    interaction.response.defer.assert_not_awaited()
    await repo.close()


async def test_gatelog_admin_lists_entries():
    from n3x_bot.bot import build_bot
    repo = await _repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    await repo.add_gate_entry("d", 111, 7, "Erkan")
    await repo.add_gate_entry("a", 222, 8, "Muneeb")

    cmd = bot.tree.get_command("gatelog")
    interaction = MagicMock()
    interaction.user = SimpleNamespace(id=1, roles=[SimpleNamespace(id=42)])  # admin
    interaction.response = MagicMock(defer=AsyncMock(), send_message=AsyncMock())
    interaction.followup = MagicMock(send=AsyncMock())
    await cmd.callback(interaction, None, None)   # all gates, default sort

    interaction.response.defer.assert_awaited_once()
    sent = "\n".join(str(c.kwargs["embed"].description)
                     for c in interaction.followup.send.await_args_list)
    assert "111" in sent and "222" in sent
    assert "Erkan" in sent and "Muneeb" in sent
    await repo.close()
