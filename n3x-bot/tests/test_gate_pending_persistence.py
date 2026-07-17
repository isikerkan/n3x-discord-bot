"""RED spec: persist the gate drop-confirm pending state to the DB.

THE BUG: for drop gates (d/e/z/k) ``handle_gate_input_message`` stores the
pending entry ONLY in the in-memory ``bot._pending_gate`` dict (initialised in
``build_bot``). ``handle_gate_drop_confirmation`` reads and atomically pops it.
On restart/deploy the dict is wiped, so a user who clicks a drop reaction after
a restart hits a silent no-op and the stat is NEVER recorded.

THE FIX (asserted here):
  * Part B1 — seeding a d/e/z/k input ALSO persists the pending entry via
    ``repo.set_gate_pending(...)`` (in addition to the in-memory dict).
  * Part B2 — a successful drop-confirm ALSO deletes the DB row via
    ``repo.delete_gate_pending(...)``.
  * Part B3 (the key fix) — a confirm whose in-memory ``_pending_gate`` MISSES
    (empty after a restart) but whose ``gate_pending`` DB row is PRESENT still
    loads it, stores the gate, and deletes the row.
  * Part B4 — the atomic single-store guard still holds when BOTH memory and DB
    hold the pending: a single dispatch stores exactly one row.
  * Part C — a ``load_pending_gate(bot, repo)`` helper (called from on_ready)
    repopulates ``bot._pending_gate`` from the DB rows so live clicks use the
    fast in-memory path again.

Discord I/O is faked; the repo is a real, connected JsonRepository (integration
over mocks for anything DB-touching). Not-yet-existing symbols
(``load_pending_gate``) are imported lazily inside the test bodies that need
them so this module always collects (failures are missing behaviour / missing
repo methods, never a test-file import error).

RED reasons:
  * ``repo.set_gate_pending`` / ``get_gate_pending`` / ``delete_gate_pending``
    don't exist yet -> AttributeError.
  * ``handle_gate_input_message`` doesn't persist; ``handle_gate_drop_confirmation``
    doesn't fall back to the DB row nor delete it.
  * ``n3x_bot.bot.load_pending_gate`` doesn't exist yet -> ImportError raised
    inside the test body (assertion-level failure, not collection).
"""

from unittest.mock import AsyncMock, MagicMock

# Reuse the faithful Discord fakes + harness helpers from the reaction spec.
from tests.test_gate_drop_reactions import (
    NOTHING,
    _dispatch,
    _fake_gate_message,
    _fake_guild,
    _flatfile_repo,
    _seed_input,
    _settings,
)

from n3x_bot.bot import build_bot
from n3x_bot.gates import resolve_drop_emoji


def _emoji_key(emoji) -> str:
    """Mirror of ``n3x_bot.bot._emoji_key`` so a directly-persisted DB options
    map keys exactly like the live handler would."""
    emoji_id = getattr(emoji, "id", None)
    return str(emoji_id) if emoji_id else str(emoji)


# ── Part B1: seeding a d/e/z/k input persists a gate_pending row ─────────────

async def test_delta_seed_persists_gate_pending_row_fields():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=8101, author_id=7,
        author_name="Erkan", channel_id=777)

    row = await repo.get_gate_pending(8101)
    assert row is not None
    assert row["gate_type"] == "d"
    assert row["cost"] == 250000
    assert row["user_id"] == 7
    assert row["username"] == "Erkan"
    assert row["channel_id"] == 777

    await repo.close()


async def test_delta_seed_persists_options_matching_in_memory():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=8102)

    row = await repo.get_gate_pending(8102)
    assert row["options"] == bot._pending_gate[8102]["options"]
    assert row["options"][added[0]] == "laser"   # the drop icon -> item
    assert row["options"][NOTHING] is None        # ❌ -> nothing

    await repo.close()


async def test_kappa_seed_persists_two_drop_options():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "k 500", guild=guild, message_id=8103)

    row = await repo.get_gate_pending(8103)
    assert row["gate_type"] == "k"
    assert row["options"][added[0]] == "hercules"
    assert row["options"][added[1]] == "lf4u"
    assert row["options"][NOTHING] is None

    await repo.close()


async def test_non_drop_gate_input_persists_no_gate_pending_row():
    # a/b/c gates store immediately and have no pending-confirm step.
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "a 5", guild=guild, message_id=8104)

    assert await repo.get_gate_pending(8104) is None

    await repo.close()


# ── Part B2: a successful drop-confirm deletes the persisted row ────────────

async def test_successful_drop_confirm_deletes_gate_pending_row():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=8201)
    assert await repo.get_gate_pending(8201) is not None   # persisted on seed

    await _dispatch(bot, repo, settings, message_id=8201, user_id=7,
                    emoji=added[0])

    assert await repo.list_gate_costs("d") == [250000]     # stored
    assert await repo.get_gate_pending(8201) is None       # row cleaned up

    await repo.close()


async def test_nothing_confirm_also_deletes_gate_pending_row():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=8202)

    await _dispatch(bot, repo, settings, message_id=8202, user_id=7,
                    emoji=NOTHING)

    assert await repo.get_gate_pending(8202) is None

    await repo.close()


# ── Part B3: THE KEY FIX — confirm survives an empty in-memory dict ──────────

async def _restart_bot(guild, *, message_id, channel_id=777):
    """A FRESH bot + repo (empty ``_pending_gate``, as after a restart) whose
    ``get_channel`` fetches back a deletable message — but with NO seeded
    in-memory pending. The caller persists the ``gate_pending`` row directly to
    simulate a pre-restart seed that only survives in the DB."""
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=channel_id)
    bot = build_bot(settings, repo)
    message = _fake_gate_message("d 250.000", guild=guild, message_id=message_id,
                                 author_id=7, author_name="Erkan",
                                 channel_id=channel_id)
    channel = MagicMock()
    channel.id = channel_id
    channel.fetch_message = AsyncMock(return_value=message)
    bot.get_channel = MagicMock(return_value=channel)
    return bot, repo, settings, message


async def test_confirm_with_empty_memory_but_db_row_still_stores():
    guild = _fake_guild()  # unicode-fallback drop icons (plain str keys)
    bot, repo, settings, message = await _restart_bot(guild, message_id=8301)

    laser = resolve_drop_emoji(guild, "laser")
    options = {_emoji_key(laser): "laser", NOTHING: None}
    await repo.set_gate_pending(8301, channel_id=777, gate_type="d",
                                cost=250000, user_id=7, username="Erkan",
                                options=options)
    assert 8301 not in bot._pending_gate  # restart: nothing in memory

    await _dispatch(bot, repo, settings, message_id=8301, user_id=7,
                    emoji=laser)

    assert await repo.list_gate_costs("d") == [250000]   # stored despite the miss
    assert await repo.get_gate_pending(8301) is None     # DB row cleaned up

    await repo.close()


async def test_confirm_with_empty_memory_but_db_row_stores_drop_flag():
    guild = _fake_guild()
    bot, repo, settings, message = await _restart_bot(guild, message_id=8302)

    laser = resolve_drop_emoji(guild, "laser")
    options = {_emoji_key(laser): "laser", NOTHING: None}
    await repo.set_gate_pending(8302, channel_id=777, gate_type="d",
                                cost=250000, user_id=7, username="Erkan",
                                options=options)

    await _dispatch(bot, repo, settings, message_id=8302, user_id=7,
                    emoji=laser)

    rows = [r for r in (await repo.export_all())["gate_entries"]
            if r["gate_type"] == "d"]
    assert rows[0]["laser_dropped"] is True

    await repo.close()


# ── Part B4: atomic single-store guard when BOTH memory and DB hold it ──────

async def test_single_dispatch_with_memory_and_db_stores_exactly_one():
    # A normal seed populates BOTH the in-memory dict and (Part B1) the DB row.
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=8401)
    assert 8401 in bot._pending_gate
    assert await repo.get_gate_pending(8401) is not None

    await _dispatch(bot, repo, settings, message_id=8401, user_id=7,
                    emoji=added[0])

    assert await repo.list_gate_costs("d") == [250000]   # exactly one row
    assert 8401 not in bot._pending_gate                 # claimed once
    assert await repo.get_gate_pending(8401) is None     # row removed once

    await repo.close()


# ── Part C: startup load helper repopulates the in-memory dict ──────────────

async def test_load_pending_gate_populates_in_memory_dict_from_db():
    from n3x_bot.bot import load_pending_gate   # lazy: does not exist yet -> RED

    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    await repo.set_gate_pending(9001, channel_id=777, gate_type="d",
                                cost=250000, user_id=7, username="Erkan",
                                options={"111": "laser", NOTHING: None})
    await repo.set_gate_pending(9002, channel_id=777, gate_type="e",
                                cost=46892, user_id=8, username="Ali",
                                options={"222": "lf4", NOTHING: None})

    await load_pending_gate(bot, repo)

    assert set(bot._pending_gate) == {9001, 9002}
    await repo.close()


async def test_load_pending_gate_restores_the_pending_shape():
    from n3x_bot.bot import load_pending_gate

    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    await repo.set_gate_pending(9101, channel_id=777, gate_type="d",
                                cost=250000, user_id=7, username="Erkan",
                                options={"111": "laser", NOTHING: None})

    await load_pending_gate(bot, repo)

    entry = bot._pending_gate[9101]
    assert entry["cost"] == 250000
    assert entry["user_id"] == 7
    assert entry["username"] == "Erkan"
    assert entry["gate_type"] == "d"
    assert entry["options"] == {"111": "laser", NOTHING: None}

    await repo.close()


async def test_load_pending_gate_empty_repo_leaves_dict_empty():
    from n3x_bot.bot import load_pending_gate

    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    await load_pending_gate(bot, repo)

    assert bot._pending_gate == {}

    await repo.close()
