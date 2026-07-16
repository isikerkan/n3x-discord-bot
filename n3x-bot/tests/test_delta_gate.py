"""Delta ('d') gate: parsing, config default, record-change detection, the
milestone announce path, and achievement un-inerting.

The reaction-confirmed STORE flow for d (and e/z/k) now lives in
``tests/test_gate_drop_reactions.py`` (the drop-icon reaction rework): the old
✅/❎ ``handle_delta_confirmation`` + ``bot._pending_delta`` tests were removed
from this module when that handler was replaced by
``handle_gate_drop_confirmation`` + ``bot._pending_gate``.

Discord I/O is faked (AsyncMock/MagicMock) and the repo is a real, connected
JsonRepository (integration over mocks for anything DB-touching).
"""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

from n3x_bot.achievements import check_achievements, user_metric_value
from n3x_bot.bot import build_bot
from n3x_bot.config import Settings
from n3x_bot.gates import parse_gate_message
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


# ── parsing: d <cost> ────────────────────────────────────────────────────────

def test_parse_gate_message_recognizes_delta():
    assert parse_gate_message("d 75361") == ("d", 75361)


def test_parse_gate_message_delta_strips_dotted_thousands():
    assert parse_gate_message("d 250.000") == ("d", 250000)


def test_parse_gate_message_delta_uppercase_normalizes():
    assert parse_gate_message("D 100") == ("d", 100)


# ── config default reward ────────────────────────────────────────────────────

def test_gate_rewards_default_includes_delta():
    s = _settings()
    assert s.gate_rewards_map()["d"] == 75361


# ── _announce_records: milestone Discord path ────────────────────────────────

def _fake_milestone(settings, *, channel=None):
    """Build a bot whose get_channel returns `channel` for the milestone id."""
    repo = MagicMock()
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=channel)
    return bot


def _milestone_channel():
    channel = MagicMock()
    channel.send = AsyncMock()
    return channel


async def test_announce_new_low_sends_gluckspilz_only():
    from n3x_bot.bot import _announce_records
    settings = _settings(milestone_channel_id=888)
    channel = _milestone_channel()
    bot = _fake_milestone(settings, channel=channel)
    record = {"min_cost": 50, "min_user": 3, "max_cost": 500, "max_user": 2}

    await _announce_records(bot, settings, "d", {"min"}, record)

    channel.send.assert_awaited_once()
    msg = channel.send.await_args.args[0]
    assert "Glückspilz" in msg and "<@3>" in msg


async def test_announce_new_high_sends_pechvogel_only():
    from n3x_bot.bot import _announce_records
    settings = _settings(milestone_channel_id=888)
    channel = _milestone_channel()
    bot = _fake_milestone(settings, channel=channel)
    record = {"min_cost": 100, "min_user": 1, "max_cost": 600, "max_user": 4}

    await _announce_records(bot, settings, "d", {"max"}, record)

    channel.send.assert_awaited_once()
    msg = channel.send.await_args.args[0]
    assert "Pechvogel" in msg and "<@4>" in msg


async def test_announce_first_entry_single_holder_sends_once():
    # CONSIDER-2: first entry moves BOTH records to the same user; announce
    # once (the Glückspilz), not two messages naming the same person.
    from n3x_bot.bot import _announce_records
    settings = _settings(milestone_channel_id=888)
    channel = _milestone_channel()
    bot = _fake_milestone(settings, channel=channel)
    record = {"min_cost": 250000, "min_user": 7, "max_cost": 250000,
              "max_user": 7}

    await _announce_records(bot, settings, "d", {"min", "max"}, record)

    channel.send.assert_awaited_once()
    msg = channel.send.await_args.args[0]
    assert "Glückspilz" in msg and "<@7>" in msg


async def test_announce_both_records_distinct_holders_sends_two():
    # Both records move in the same add but to DIFFERENT users -> two messages.
    from n3x_bot.bot import _announce_records
    settings = _settings(milestone_channel_id=888)
    channel = _milestone_channel()
    bot = _fake_milestone(settings, channel=channel)
    record = {"min_cost": 40, "min_user": 3, "max_cost": 900, "max_user": 9}

    await _announce_records(bot, settings, "d", {"min", "max"}, record)

    assert channel.send.await_count == 2
    sent = [c.args[0] for c in channel.send.await_args_list]
    assert any("Glückspilz" in m and "<@3>" in m for m in sent)
    assert any("Pechvogel" in m and "<@9>" in m for m in sent)


async def test_announce_no_send_when_milestone_channel_unset():
    from n3x_bot.bot import _announce_records
    settings = _settings(milestone_channel_id=0)
    channel = _milestone_channel()
    bot = _fake_milestone(settings, channel=channel)
    record = {"min_cost": 50, "min_user": 3, "max_cost": 500, "max_user": 2}

    await _announce_records(bot, settings, "d", {"min", "max"}, record)

    channel.send.assert_not_awaited()


# ── pure record-change detection (changed_records) ───────────────────────────

def test_changed_records_first_entry_sets_both():
    from n3x_bot.gates import changed_records
    before = None
    after = {"min_cost": 100, "min_user": 1, "max_cost": 100, "max_user": 1}
    assert changed_records(before, after) == {"min", "max"}


def test_changed_records_new_min_only():
    from n3x_bot.gates import changed_records
    before = {"min_cost": 100, "min_user": 1, "max_cost": 500, "max_user": 2}
    after = {"min_cost": 50, "min_user": 3, "max_cost": 500, "max_user": 2}
    assert changed_records(before, after) == {"min"}


def test_changed_records_new_max_only():
    from n3x_bot.gates import changed_records
    before = {"min_cost": 100, "min_user": 1, "max_cost": 500, "max_user": 2}
    after = {"min_cost": 100, "min_user": 1, "max_cost": 600, "max_user": 3}
    assert changed_records(before, after) == {"max"}


def test_changed_records_no_change_is_empty():
    from n3x_bot.gates import changed_records
    before = {"min_cost": 100, "min_user": 1, "max_cost": 500, "max_user": 2}
    after = {"min_cost": 100, "min_user": 1, "max_cost": 500, "max_user": 2}
    assert changed_records(before, after) == set()


# ── achievements un-inert: gate_d becomes real ───────────────────────────────

async def test_five_delta_entries_unlock_d_5_achievement():
    repo = await _flatfile_repo()
    for i, cost in enumerate((60000, 61000, 62000, 63000, 64000)):
        await repo.add_gate_entry("d", cost, 7, "Erkan",
                                  laser_dropped=(i % 2 == 0))

    unlocked = await check_achievements(repo, 7, "gate_d")

    assert "d_5" in {a.id for a in unlocked}

    await repo.close()


async def test_gate_total_metric_includes_delta_entries():
    repo = await _flatfile_repo()
    await repo.add_gate_entry("d", 60000, 7, "Erkan", laser_dropped=True)
    await repo.add_gate_entry("d", 61000, 7, "Erkan", laser_dropped=False)
    await repo.add_gate_entry("d", 62000, 7, "Erkan", laser_dropped=True)

    assert await user_metric_value(repo, 7, "gate_total") == 3

    await repo.close()
