"""Delta ('d') gate: parsing, config default, reaction-confirmed store,
record-change detection, and achievement un-inerting.

Discord I/O is faked (AsyncMock/MagicMock) and the repo is a real, connected
JsonRepository (integration over mocks for anything DB-touching).

New symbols pinned here (imported lazily inside the test bodies that need them
so this module always collects — the failures are missing-behaviour, not
test-file import errors):

    n3x_bot.gates.changed_records(before, after) -> set[str]
        Pure record-change detector. Given the gate_record dict BEFORE an add
        (or None) and the gate_record dict AFTER, returns the subset of
        {"min", "max"} that newly changed.

    n3x_bot.bot.handle_delta_confirmation(bot, repo, settings, payload) -> None
        Reaction handler for a pending delta. ✅ -> laser_dropped=True,
        ❎ -> laser_dropped=False. Stores via add_gate_entry("d", ...) and
        clears bot._pending_delta[payload.message_id].

Behaviour also pinned (via existing symbols):
    handle_gate_input_message on a `d <cost>` message registers
    bot._pending_delta[message.id] = {"cost", "user_id", "username"} and reacts
    ✅ + ❎ WITHOUT storing; a/b/c still store immediately.
"""

import asyncio
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from n3x_bot.achievements import check_achievements, user_metric_value
from n3x_bot.bot import build_bot, handle_gate_input_message
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


def _fake_delta_message(content: str, *, message_id: int = 5001,
                        author_id: int = 7, author_name: str = "Erkan"):
    message = MagicMock()
    message.id = message_id
    message.content = content
    message.author = SimpleNamespace(id=author_id, name=author_name)
    message.add_reaction = AsyncMock()
    message.clear_reactions = AsyncMock()
    message.delete = AsyncMock()
    message.channel = SimpleNamespace(id=777)
    return message


def _fake_reaction_payload(*, message_id: int, user_id: int, emoji: str,
                           channel_id: int):
    return SimpleNamespace(message_id=message_id, user_id=user_id,
                           emoji=emoji, channel_id=channel_id, guild_id=1)


# ── parsing: d <cost> ────────────────────────────────────────────────────────

def test_parse_gate_message_recognizes_delta():
    assert parse_gate_message("d 75361") == ("d", 75361)


def test_parse_gate_message_delta_strips_dotted_thousands():
    assert parse_gate_message("d 250.000") == ("d", 250000)


def test_parse_gate_message_delta_uppercase_normalizes():
    assert parse_gate_message("D 100") == ("d", 100)


# NOTE: a/b/c parsing is already covered (and must stay green) by
# tests/test_gates.py; no redundant regression guard is added here so this
# module stays RED-only.


# ── config default reward ────────────────────────────────────────────────────

def test_gate_rewards_default_includes_delta():
    s = _settings()
    assert s.gate_rewards_map()["d"] == 75361


# ── delta input registers a pending confirmation, does not store ─────────────

async def test_delta_input_registers_pending_and_reacts_check_and_cross():
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)
    message = _fake_delta_message("d 250.000", message_id=5001,
                                  author_id=7, author_name="Erkan")

    await handle_gate_input_message(bot, repo, settings, message)

    # not stored yet — awaits reaction confirmation
    assert await repo.list_gate_costs("d") == []
    # registered as pending under the message id
    assert bot._pending_delta[5001] == {"cost": 250000, "user_id": 7,
                                         "username": "Erkan"}
    # both confirm reactions offered
    reacted = {c.args[0] for c in message.add_reaction.await_args_list}
    assert reacted == {"✅", "❎"}

    await repo.close()


# NOTE: a/b/c input storing immediately (unchanged behaviour) is already
# covered by tests/test_bot_wiring.py; no redundant regression guard here.


# ── delta confirmation reaction stores with laser flag ───────────────────────

async def test_delta_confirmation_check_stores_with_laser_true():
    from n3x_bot.bot import handle_delta_confirmation
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    channel = MagicMock()
    channel.fetch_message = AsyncMock(
        return_value=_fake_delta_message("d 250.000", message_id=5001))
    bot.get_channel = MagicMock(return_value=channel)
    bot._pending_delta = {5001: {"cost": 250000, "user_id": 7,
                                 "username": "Erkan"}}

    payload = _fake_reaction_payload(message_id=5001, user_id=7,
                                     emoji="✅", channel_id=777)
    await handle_delta_confirmation(bot, repo, settings, payload)

    assert await repo.list_gate_costs("d") == [250000]
    rows = [r for r in (await repo.export_all())["gate_entries"]
            if r["gate_type"] == "d"]
    assert rows[0]["laser_dropped"] is True
    assert 5001 not in bot._pending_delta  # pending cleared

    await repo.close()


async def test_delta_confirmation_cross_stores_with_laser_false():
    from n3x_bot.bot import handle_delta_confirmation
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    channel = MagicMock()
    channel.fetch_message = AsyncMock(
        return_value=_fake_delta_message("d 250.000", message_id=5001))
    bot.get_channel = MagicMock(return_value=channel)
    bot._pending_delta = {5001: {"cost": 250000, "user_id": 7,
                                 "username": "Erkan"}}

    payload = _fake_reaction_payload(message_id=5001, user_id=7,
                                     emoji="❎", channel_id=777)
    await handle_delta_confirmation(bot, repo, settings, payload)

    rows = [r for r in (await repo.export_all())["gate_entries"]
            if r["gate_type"] == "d"]
    assert rows[0]["laser_dropped"] is False

    await repo.close()


async def test_delta_confirmation_on_non_pending_message_does_nothing():
    from n3x_bot.bot import handle_delta_confirmation
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)
    bot._pending_delta = {}

    payload = _fake_reaction_payload(message_id=9999, user_id=7,
                                     emoji="✅", channel_id=777)
    await handle_delta_confirmation(bot, repo, settings, payload)

    assert await repo.list_gate_costs("d") == []

    await repo.close()


async def test_delta_confirmation_ignores_non_confirm_emoji():
    from n3x_bot.bot import handle_delta_confirmation
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    channel = MagicMock()
    channel.fetch_message = AsyncMock(
        return_value=_fake_delta_message("d 250.000", message_id=5001))
    bot.get_channel = MagicMock(return_value=channel)
    bot._pending_delta = {5001: {"cost": 250000, "user_id": 7,
                                 "username": "Erkan"}}

    payload = _fake_reaction_payload(message_id=5001, user_id=7,
                                     emoji="🎉", channel_id=777)
    await handle_delta_confirmation(bot, repo, settings, payload)

    assert await repo.list_gate_costs("d") == []  # nothing stored

    await repo.close()


async def test_delta_confirmation_by_non_author_is_ignored():
    # assumption: only the message author may confirm their own delta (v3 flow)
    from n3x_bot.bot import handle_delta_confirmation
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    channel = MagicMock()
    channel.fetch_message = AsyncMock(
        return_value=_fake_delta_message("d 250.000", message_id=5001,
                                         author_id=7))
    bot.get_channel = MagicMock(return_value=channel)
    bot._pending_delta = {5001: {"cost": 250000, "user_id": 7,
                                 "username": "Erkan"}}

    payload = _fake_reaction_payload(message_id=5001, user_id=8,  # not author
                                     emoji="✅", channel_id=777)
    await handle_delta_confirmation(bot, repo, settings, payload)

    assert await repo.list_gate_costs("d") == []

    await repo.close()


# ── atomic-claim: concurrent confirmations store exactly one delta ───────────

class _SuspendingDeltaRepo:
    """add_gate_entry suspends between the dedup-check and the insert, mimicking
    a SQL backend's I/O so two concurrent handle_delta_confirmation calls for
    the same pending message can genuinely interleave (the SHOULD-1 race)."""
    def __init__(self):
        self.rows: list = []

    async def gate_record(self, gate_type):
        rows = [r for r in self.rows if r[0] == gate_type]
        if not rows:
            return None
        mn = min(rows, key=lambda r: r[1])
        mx = max(rows, key=lambda r: r[1])
        return {"min_cost": mn[1], "min_user": mn[2],
                "max_cost": mx[1], "max_user": mx[2]}

    async def add_gate_entry(self, gate_type, cost, user_id, username,
                             dedup_window_seconds=30, laser_dropped=None):
        for r in self.rows:
            if r[0] == gate_type and r[1] == cost and r[2] == user_id:
                return False
        await asyncio.sleep(0.005)  # real suspension point (SQL I/O)
        self.rows.append((gate_type, cost, user_id, username, laser_dropped))
        return True

    # minimal stubs so the post-insert path resolves
    async def get_user_achievements(self, discord_id):
        return set()

    async def unlock_achievement(self, *a):
        return True

    async def user_gate_counts(self, uid):
        return {}

    async def user_gate_cost_total(self, uid):
        return 0

    async def gate_totals(self):
        return {}


async def test_concurrent_delta_confirmations_store_exactly_one(monkeypatch):
    # SHOULD-1: check-then-act race — two confirmations (e.g. ✅ then ❎ within
    # the window, or a redelivered event) for the SAME pending message must
    # store exactly ONE delta, not two gate_entries rows, and the losing
    # dispatch must be a clean no-op (no swallowed KeyError).
    from n3x_bot import bot as botmod
    from n3x_bot.bot import handle_delta_confirmation
    monkeypatch.setattr(botmod, "update_gate_stats_embed", AsyncMock())
    monkeypatch.setattr(botmod, "check_achievements", AsyncMock(return_value=[]))
    monkeypatch.setattr(botmod, "announce_achievements", AsyncMock())

    settings = _settings(gate_input_channel_id=777)
    repo = _SuspendingDeltaRepo()
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)
    bot._pending_delta = {5001: {"cost": 250000, "user_id": 7,
                                 "username": "Erkan"}}

    p_check = _fake_reaction_payload(message_id=5001, user_id=7,
                                     emoji="✅", channel_id=777)
    p_cross = _fake_reaction_payload(message_id=5001, user_id=7,
                                     emoji="❎", channel_id=777)

    await asyncio.gather(
        handle_delta_confirmation(bot, repo, settings, p_check),
        handle_delta_confirmation(bot, repo, settings, p_cross),
    )

    assert len(repo.rows) == 1                # exactly one delta stored
    assert 5001 not in bot._pending_delta     # pending claimed exactly once


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
