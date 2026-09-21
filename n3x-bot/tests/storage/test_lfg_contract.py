"""Contract tests for the ``lfg_*`` repository surface.

Backs the LFG feature: user-created group-finding posts with per-user
availability per start time, one atomically-confirmed start time, and a roster
that diverges from the availability once a date is fixed.

Parametrized across every registered backend via the shared ``repo`` /
``make_repo`` fixtures (json, sqlite, and postgres when TEST_POSTGRES_URL is
set), mirroring ``tests/storage/test_gate_pending_contract.py``.

Three tables (``lfg_posts``, ``lfg_availability``, ``lfg_participants``) plus
export/import fidelity and ``n3x_bot.migrate._DATA_TABLES`` membership.
"""
from datetime import datetime, timedelta, timezone

TZ = timezone.utc
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=TZ)

POST_KEYS = {"id", "creator_id", "title", "event_date", "min_players",
             "max_players", "start_times", "confirmed_time", "status",
             "channel_id", "message_id", "created_at", "event_at",
             "cleanup_at"}

TIMES = ["19:00", "20:00", "21:00"]


async def _seed(repo, **overrides):
    kwargs = dict(creator_id=42, title="Satura Gruppen Gate",
                  event_date="2026-09-21", min_players=4, max_players=8,
                  start_times=list(TIMES), status="OPEN", channel_id=999,
                  created_at=NOW, cleanup_at=NOW + timedelta(hours=10))
    kwargs.update(overrides)
    return await repo.create_lfg(**kwargs)


# ── create / read ───────────────────────────────────────────────────────────

async def test_get_lfg_unknown_id_returns_none(repo):
    assert await repo.get_lfg(1) is None


async def test_create_lfg_returns_an_int_id(repo):
    lfg_id = await _seed(repo)
    assert isinstance(lfg_id, int)
    assert lfg_id > 0


async def test_create_lfg_ids_are_distinct(repo):
    first = await _seed(repo)
    second = await _seed(repo, title="DarkOrbit Gruppe")
    assert first != second


async def test_lfg_row_has_all_keys(repo):
    lfg_id = await _seed(repo)
    assert set(await repo.get_lfg(lfg_id)) == POST_KEYS


async def test_lfg_row_roundtrips_scalar_fields(repo):
    lfg_id = await _seed(repo)
    row = await repo.get_lfg(lfg_id)
    assert row["creator_id"] == 42
    assert row["title"] == "Satura Gruppen Gate"
    assert row["event_date"] == "2026-09-21"
    assert row["min_players"] == 4
    assert row["max_players"] == 8
    assert row["status"] == "OPEN"
    assert row["channel_id"] == 999


async def test_start_times_roundtrip_as_a_list(repo):
    lfg_id = await _seed(repo)
    row = await repo.get_lfg(lfg_id)
    assert isinstance(row["start_times"], list)
    assert row["start_times"] == TIMES


async def test_fresh_lfg_has_no_confirmed_time_message_or_event_at(repo):
    row = await repo.get_lfg(await _seed(repo))
    assert row["confirmed_time"] is None
    assert row["message_id"] is None
    assert row["event_at"] is None


async def test_timestamps_round_trip_tz_aware(repo):
    # PIN B6-style awareness: a naive value read back would blow up every
    # comparison against an aware `now` in the cleanup loop.
    row = await repo.get_lfg(await _seed(repo))
    assert row["created_at"].tzinfo is not None
    assert row["cleanup_at"].tzinfo is not None
    assert row["cleanup_at"] == NOW + timedelta(hours=10)


async def test_create_lfg_creates_no_participants(repo):
    # The creator is NOT automatically a participant.
    lfg_id = await _seed(repo)
    assert await repo.get_lfg_participants(lfg_id) == []


# ── message binding (how a persistent view finds its LFG) ───────────────────

async def test_get_lfg_by_message_unknown_returns_none(repo):
    await _seed(repo)
    assert await repo.get_lfg_by_message(123456) is None


async def test_set_lfg_message_binds_the_message(repo):
    lfg_id = await _seed(repo)
    await repo.set_lfg_message(lfg_id, 555001, 999)
    row = await repo.get_lfg_by_message(555001)
    assert row is not None
    assert row["id"] == lfg_id
    assert row["message_id"] == 555001


async def test_message_id_round_trips_as_int(repo):
    lfg_id = await _seed(repo)
    await repo.set_lfg_message(lfg_id, 555001, 999)
    assert isinstance((await repo.get_lfg(lfg_id))["message_id"], int)


# ── availability (replace-set per user) ─────────────────────────────────────

async def test_availability_starts_empty(repo):
    assert await repo.get_lfg_availability(await _seed(repo)) == {}


async def test_set_availability_single_time(repo):
    lfg_id = await _seed(repo)
    await repo.set_lfg_availability(lfg_id, 1, ["20:00"])
    assert await repo.get_lfg_availability(lfg_id) == {"20:00": [1]}


async def test_set_availability_multiple_times(repo):
    lfg_id = await _seed(repo)
    await repo.set_lfg_availability(lfg_id, 1, ["19:00", "20:00"])
    assert await repo.get_lfg_availability(lfg_id) == {
        "19:00": [1], "20:00": [1]}


async def test_availability_groups_users_per_time(repo):
    lfg_id = await _seed(repo)
    await repo.set_lfg_availability(lfg_id, 1, ["19:00", "20:00"])
    await repo.set_lfg_availability(lfg_id, 2, ["20:00"])
    await repo.set_lfg_availability(lfg_id, 3, ["20:00"])
    assert await repo.get_lfg_availability(lfg_id) == {
        "19:00": [1], "20:00": [1, 2, 3]}


async def test_set_availability_replaces_the_previous_selection(repo):
    lfg_id = await _seed(repo)
    await repo.set_lfg_availability(lfg_id, 1, ["19:00", "20:00"])
    await repo.set_lfg_availability(lfg_id, 1, ["21:00"])
    assert await repo.get_lfg_availability(lfg_id) == {"21:00": [1]}


async def test_set_availability_empty_list_clears_the_user(repo):
    lfg_id = await _seed(repo)
    await repo.set_lfg_availability(lfg_id, 1, ["20:00"])
    await repo.set_lfg_availability(lfg_id, 1, [])
    assert await repo.get_lfg_availability(lfg_id) == {}


async def test_set_availability_is_per_lfg(repo):
    first = await _seed(repo)
    second = await _seed(repo, title="Minecraft Abend")
    await repo.set_lfg_availability(first, 1, ["19:00"])
    assert await repo.get_lfg_availability(second) == {}


async def test_set_availability_dedupes_repeated_times(repo):
    lfg_id = await _seed(repo)
    await repo.set_lfg_availability(lfg_id, 1, ["20:00", "20:00"])
    assert await repo.get_lfg_availability(lfg_id) == {"20:00": [1]}


# ── confirm: compare-and-swap ──────────────────────────────────────────────

async def _confirm(repo, lfg_id, participants, *, start_time="20:00",
                   status="CONFIRMED", expect_status="OPEN"):
    return await repo.confirm_lfg(
        lfg_id, start_time=start_time,
        event_at=NOW + timedelta(hours=8),
        cleanup_at=NOW + timedelta(hours=9),
        status=status, participants=participants, joined_at=NOW,
        expect_status=expect_status)


async def test_confirm_lfg_sets_time_status_and_participants(repo):
    lfg_id = await _seed(repo)
    assert await _confirm(repo, lfg_id, [1, 2, 3, 4]) is True
    row = await repo.get_lfg(lfg_id)
    assert row["status"] == "CONFIRMED"
    assert row["confirmed_time"] == "20:00"
    assert row["event_at"] == NOW + timedelta(hours=8)
    assert row["cleanup_at"] == NOW + timedelta(hours=9)
    assert await repo.get_lfg_participants(lfg_id) == [1, 2, 3, 4]


async def test_confirm_lfg_twice_only_the_first_wins(repo):
    # THE race guard: an LFG must never get a second confirmed time.
    lfg_id = await _seed(repo)
    assert await _confirm(repo, lfg_id, [1, 2, 3, 4], start_time="20:00") is True
    assert await _confirm(repo, lfg_id, [5, 6, 7, 8], start_time="21:00") is False
    row = await repo.get_lfg(lfg_id)
    assert row["confirmed_time"] == "20:00"


async def test_losing_confirm_writes_no_participants(repo):
    lfg_id = await _seed(repo)
    await _confirm(repo, lfg_id, [1, 2, 3, 4], start_time="20:00")
    await _confirm(repo, lfg_id, [5, 6], start_time="21:00")
    assert await repo.get_lfg_participants(lfg_id) == [1, 2, 3, 4]


async def test_confirm_lfg_on_wrong_expected_status_is_a_noop(repo):
    lfg_id = await _seed(repo)
    assert await _confirm(repo, lfg_id, [1], expect_status="CONFIRMED") is False
    assert (await repo.get_lfg(lfg_id))["status"] == "OPEN"


async def test_confirm_lfg_can_report_full_straight_away(repo):
    lfg_id = await _seed(repo, min_players=2, max_players=2)
    await _confirm(repo, lfg_id, [1, 2], status="FULL")
    assert (await repo.get_lfg(lfg_id))["status"] == "FULL"


# ── roster: join / leave with capacity enforced in the repo ────────────────

async def test_add_participant_adds(repo):
    lfg_id = await _seed(repo)
    assert await repo.add_lfg_participant(lfg_id, 1, NOW, max_players=8) == "added"
    assert await repo.get_lfg_participants(lfg_id) == [1]


async def test_add_participant_twice_reports_already(repo):
    lfg_id = await _seed(repo)
    await repo.add_lfg_participant(lfg_id, 1, NOW, max_players=8)
    assert await repo.add_lfg_participant(lfg_id, 1, NOW, max_players=8) == "already"
    assert await repo.get_lfg_participants(lfg_id) == [1]


async def test_add_participant_at_capacity_reports_full(repo):
    lfg_id = await _seed(repo, max_players=2)
    await repo.add_lfg_participant(lfg_id, 1, NOW, max_players=2)
    await repo.add_lfg_participant(lfg_id, 2, NOW, max_players=2)
    assert await repo.add_lfg_participant(lfg_id, 3, NOW, max_players=2) == "full"
    assert await repo.get_lfg_participants(lfg_id) == [1, 2]


async def test_remove_participant(repo):
    lfg_id = await _seed(repo)
    await repo.add_lfg_participant(lfg_id, 1, NOW, max_players=8)
    assert await repo.remove_lfg_participant(lfg_id, 1) is True
    assert await repo.get_lfg_participants(lfg_id) == []


async def test_remove_participant_who_is_not_on_the_roster(repo):
    lfg_id = await _seed(repo)
    assert await repo.remove_lfg_participant(lfg_id, 99) is False


async def test_freed_seat_can_be_taken_again(repo):
    lfg_id = await _seed(repo, max_players=2)
    await repo.add_lfg_participant(lfg_id, 1, NOW, max_players=2)
    await repo.add_lfg_participant(lfg_id, 2, NOW, max_players=2)
    await repo.remove_lfg_participant(lfg_id, 1)
    assert await repo.add_lfg_participant(lfg_id, 3, NOW, max_players=2) == "added"


async def test_participants_are_per_lfg(repo):
    first = await _seed(repo)
    second = await _seed(repo, title="Filmabend")
    await repo.add_lfg_participant(first, 1, NOW, max_players=8)
    assert await repo.get_lfg_participants(second) == []


# ── status ─────────────────────────────────────────────────────────────────

async def test_set_lfg_status(repo):
    lfg_id = await _seed(repo)
    await repo.set_lfg_status(lfg_id, "FULL")
    assert (await repo.get_lfg(lfg_id))["status"] == "FULL"


# ── cleanup selection ──────────────────────────────────────────────────────

async def test_due_for_cleanup_empty_when_deadline_ahead(repo):
    await _seed(repo, cleanup_at=NOW + timedelta(hours=1))
    assert await repo.lfg_due_for_cleanup(NOW) == []


async def test_due_for_cleanup_returns_passed_deadline(repo):
    lfg_id = await _seed(repo, cleanup_at=NOW - timedelta(minutes=1))
    due = await repo.lfg_due_for_cleanup(NOW)
    assert [d["id"] for d in due] == [lfg_id]


async def test_due_for_cleanup_is_inclusive_of_the_exact_deadline(repo):
    lfg_id = await _seed(repo, cleanup_at=NOW)
    assert [d["id"] for d in await repo.lfg_due_for_cleanup(NOW)] == [lfg_id]


async def test_due_for_cleanup_skips_already_expired(repo):
    lfg_id = await _seed(repo, cleanup_at=NOW - timedelta(hours=1))
    await repo.set_lfg_status(lfg_id, "EXPIRED")
    assert await repo.lfg_due_for_cleanup(NOW) == []


async def test_all_active_lfgs_excludes_expired_and_cancelled(repo):
    keep = await _seed(repo)
    gone = await _seed(repo, title="weg")
    cancelled = await _seed(repo, title="abgesagt")
    await repo.set_lfg_status(gone, "EXPIRED")
    await repo.set_lfg_status(cancelled, "CANCELLED")
    assert [r["id"] for r in await repo.all_active_lfgs()] == [keep]


# ── delete ─────────────────────────────────────────────────────────────────

async def test_delete_lfg_removes_row_and_children(repo):
    lfg_id = await _seed(repo)
    await repo.set_lfg_availability(lfg_id, 1, ["20:00"])
    await repo.add_lfg_participant(lfg_id, 1, NOW, max_players=8)
    assert await repo.delete_lfg(lfg_id) is True
    assert await repo.get_lfg(lfg_id) is None
    assert await repo.get_lfg_availability(lfg_id) == {}
    assert await repo.get_lfg_participants(lfg_id) == []


async def test_delete_unknown_lfg_returns_false(repo):
    assert await repo.delete_lfg(404) is False


# ── migration fidelity ─────────────────────────────────────────────────────

async def test_lfg_tables_are_in_the_migrate_data_table_list():
    from n3x_bot import migrate
    for table in ("lfg_posts", "lfg_availability", "lfg_participants"):
        assert table in migrate._DATA_TABLES


async def test_export_all_includes_the_lfg_tables(repo):
    lfg_id = await _seed(repo)
    await repo.set_lfg_availability(lfg_id, 1, ["20:00"])
    await repo.add_lfg_participant(lfg_id, 1, NOW, max_players=8)
    snapshot = await repo.export_all()
    assert len(snapshot["lfg_posts"]) == 1
    assert len(snapshot["lfg_availability"]) == 1
    assert len(snapshot["lfg_participants"]) == 1


async def test_export_import_roundtrip_preserves_lfg(repo, make_repo):
    lfg_id = await _seed(repo)
    await repo.set_lfg_message(lfg_id, 555001, 999)
    await repo.set_lfg_availability(lfg_id, 1, ["19:00", "20:00"])
    await _confirm(repo, lfg_id, [1, 2, 3, 4])
    snapshot = await repo.export_all()

    dest = await make_repo()
    try:
        await dest.import_all(snapshot)
        row = await dest.get_lfg(lfg_id)
        assert row["title"] == "Satura Gruppen Gate"
        assert row["start_times"] == TIMES
        assert row["confirmed_time"] == "20:00"
        assert row["status"] == "CONFIRMED"
        assert row["cleanup_at"] == NOW + timedelta(hours=9)
        assert await dest.get_lfg_participants(lfg_id) == [1, 2, 3, 4]
        assert await dest.get_lfg_availability(lfg_id) == {
            "19:00": [1], "20:00": [1]}
        # the message binding must survive, or restored views lose their LFG
        assert (await dest.get_lfg_by_message(555001))["id"] == lfg_id
    finally:
        await dest.close()


async def test_clear_removes_lfg_data(repo):
    lfg_id = await _seed(repo)
    await repo.set_lfg_availability(lfg_id, 1, ["20:00"])
    await repo.add_lfg_participant(lfg_id, 1, NOW, max_players=8)
    await repo.clear()
    assert await repo.get_lfg(lfg_id) is None
    assert await repo.lfg_due_for_cleanup(NOW + timedelta(days=9)) == []
