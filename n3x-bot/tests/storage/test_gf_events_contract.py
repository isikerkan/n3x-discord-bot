"""Contract tests for the Group Finder event surface (gf_events, gf_slots,
gf_votes, gf_participants, gf_messages), across every registered backend."""
from datetime import datetime, timedelta, timezone

import pytest

UTC = timezone.utc
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
S1, S2, S3 = (NOW + timedelta(hours=h) for h in (6, 7, 8))
EVENT_KEYS = {"id", "creator_id", "title", "min_players", "max_players",
              "origin_zone", "status", "scheduled_at", "created_at", "closed_at",
              "time_found_notified_at", "cleanup_at", "cancelled_at",
              "cleaned_at", "legacy_lfg_id", "slots"}


async def _event(repo, **overrides):
    kwargs = dict(creator_id=7, title="Satura Gruppen Gate", min_players=4,
                  max_players=8, origin_zone="Europe/Berlin",
                  slots=[S3, S1, S2], status="VOTING", created_at=NOW,
                  cleanup_at=S3 + timedelta(hours=1))
    kwargs.update(overrides)
    return await repo.gf_create_event(**kwargs)


# ── events ──────────────────────────────────────────────────────────────────

async def test_unknown_event_is_none(repo):
    assert await repo.gf_get_event(1) is None


async def test_event_roundtrip(repo):
    eid = await _event(repo)
    e = await repo.gf_get_event(eid)
    assert set(e) == EVENT_KEYS
    assert (e["title"], e["min_players"], e["max_players"]) == (
        "Satura Gruppen Gate", 4, 8)
    assert e["origin_zone"] == "Europe/Berlin" and e["status"] == "VOTING"
    assert e["slots"] == [S1, S2, S3]                 # sorted, UTC
    assert e["created_at"] == NOW
    assert e["scheduled_at"] is None and e["legacy_lfg_id"] is None


async def test_slots_are_tz_aware_utc(repo):
    e = await repo.gf_get_event(await _event(repo))
    assert all(s.tzinfo is not None and s.utcoffset() == timedelta(0)
               for s in e["slots"])


async def test_non_utc_input_is_stored_as_the_same_instant(repo):
    from zoneinfo import ZoneInfo
    berlin = datetime(2026, 9, 26, 20, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    e = await repo.gf_get_event(await _event(repo, slots=[berlin]))
    assert e["slots"] == [datetime(2026, 9, 26, 18, 0, tzinfo=UTC)]


async def test_event_ids_distinct(repo):
    assert await _event(repo) != await _event(repo, title="Other")


async def test_legacy_id_roundtrip(repo):
    e = await repo.gf_get_event(await _event(repo, legacy_lfg_id=11))
    assert e["legacy_lfg_id"] == 11


async def test_events_with_status(repo):
    a = await _event(repo)
    b = await _event(repo, title="b")
    await repo.gf_update_event(b, status="CANCELLED")
    assert [e["id"] for e in await repo.gf_events_with_status(["VOTING"])] == [a]
    assert [e["id"] for e in await repo.gf_events_with_status(
        ["VOTING", "CANCELLED"])] == [a, b]


async def test_events_with_status_uncleaned_only(repo):
    a = await _event(repo)
    b = await _event(repo, title="b")
    await repo.gf_update_event(b, cleaned_at=NOW)
    assert [e["id"] for e in await repo.gf_events_with_status(
        ["VOTING"], uncleaned_only=True)] == [a]
    assert (await repo.gf_get_event(b))["cleaned_at"] == NOW


async def test_update_event_fields(repo):
    eid = await _event(repo)
    assert await repo.gf_update_event(eid, status="SCHEDULED", scheduled_at=S2)
    e = await repo.gf_get_event(eid)
    assert e["status"] == "SCHEDULED" and e["scheduled_at"] == S2


async def test_update_event_cas_refuses_on_wrong_status(repo):
    eid = await _event(repo)
    assert await repo.gf_update_event(eid, expect_status="SCHEDULED",
                                      status="CLOSED") is False
    assert (await repo.gf_get_event(eid))["status"] == "VOTING"


async def test_update_event_rejects_unknown_fields(repo):
    eid = await _event(repo)
    with pytest.raises(ValueError):
        await repo.gf_update_event(eid, title="nope")


# ── votes ───────────────────────────────────────────────────────────────────

async def test_votes_start_empty(repo):
    assert await repo.gf_get_votes(await _event(repo)) == []


async def test_votes_roundtrip_with_zone(repo):
    eid = await _event(repo)
    await repo.gf_set_votes(eid, 1, [S1, S2], "Asia/Karachi", NOW)
    votes = await repo.gf_get_votes(eid)
    assert [(v["discord_id"], v["starts_at"], v["zone"]) for v in votes] == [
        (1, S1, "Asia/Karachi"), (1, S2, "Asia/Karachi")]
    assert votes[0]["voted_at"] == NOW


async def test_set_votes_replaces(repo):
    eid = await _event(repo)
    await repo.gf_set_votes(eid, 1, [S1, S2], "Europe/Berlin", NOW)
    await repo.gf_set_votes(eid, 1, [S3], "Europe/Berlin", NOW)
    assert [v["starts_at"] for v in await repo.gf_get_votes(eid)] == [S3]


async def test_empty_votes_remove_member(repo):
    eid = await _event(repo)
    await repo.gf_set_votes(eid, 1, [S1], "Europe/Berlin", NOW)
    await repo.gf_set_votes(eid, 1, [], "Europe/Berlin", NOW)
    assert await repo.gf_get_votes(eid) == []


async def test_votes_ordered_by_time(repo):
    eid = await _event(repo)
    await repo.gf_set_votes(eid, 2, [S1], "Europe/Berlin", NOW + timedelta(minutes=5))
    await repo.gf_set_votes(eid, 1, [S1], "Europe/Berlin", NOW)
    assert [v["discord_id"] for v in await repo.gf_get_votes(eid)] == [1, 2]


# ── scheduling (compare-and-swap) ───────────────────────────────────────────

async def _schedule(repo, eid, participants, *, slot=S2, expect="VOTING"):
    return await repo.gf_schedule_event(
        eid, starts_at=slot, status="SCHEDULED",
        cleanup_at=slot + timedelta(hours=1), closed_at=None,
        participants=participants, joined_at=NOW, expect_status=expect)


async def test_schedule_sets_time_and_roster(repo):
    eid = await _event(repo)
    assert await _schedule(repo, eid, [(1, "Europe/Berlin"), (2, "Asia/Tokyo")])
    e = await repo.gf_get_event(eid)
    assert e["status"] == "SCHEDULED" and e["scheduled_at"] == S2
    assert e["cleanup_at"] == S2 + timedelta(hours=1)
    roster = await repo.gf_get_participants(eid)
    assert [(p["discord_id"], p["zone"], p["source"]) for p in roster] == [
        (1, "Europe/Berlin", "VOTE"), (2, "Asia/Tokyo", "VOTE")]


async def test_schedule_only_first_caller_wins(repo):
    eid = await _event(repo)
    assert await _schedule(repo, eid, [(1, "Europe/Berlin")], slot=S1) is True
    assert await _schedule(repo, eid, [(9, "Europe/Berlin")], slot=S3) is False
    assert (await repo.gf_get_event(eid))["scheduled_at"] == S1
    assert [p["discord_id"] for p in await repo.gf_get_participants(eid)] == [1]


async def test_schedule_keeps_votes(repo):
    # availability and the final time are stored separately
    eid = await _event(repo)
    await repo.gf_set_votes(eid, 1, [S1, S2], "Europe/Berlin", NOW)
    await _schedule(repo, eid, [(1, "Europe/Berlin")])
    assert len(await repo.gf_get_votes(eid)) == 2


# ── roster ──────────────────────────────────────────────────────────────────

async def test_add_participant_and_capacity(repo):
    eid = await _event(repo)
    assert await repo.gf_add_participant(eid, 1, "Europe/Berlin", NOW,
                                         max_players=2, source="JOIN") == "added"
    assert await repo.gf_add_participant(eid, 1, "Europe/Berlin", NOW,
                                         max_players=2, source="JOIN") == "already"
    await repo.gf_add_participant(eid, 2, "Europe/Berlin", NOW,
                                  max_players=2, source="JOIN")
    assert await repo.gf_add_participant(eid, 3, "Europe/Berlin", NOW,
                                         max_players=2, source="JOIN") == "full"


async def test_remove_participant(repo):
    eid = await _event(repo)
    await repo.gf_add_participant(eid, 1, "Europe/Berlin", NOW, max_players=8,
                                  source="JOIN")
    assert await repo.gf_remove_participant(eid, 1) is True
    assert await repo.gf_remove_participant(eid, 1) is False
    assert await repo.gf_get_participants(eid) == []


async def test_roster_in_join_order(repo):
    eid = await _event(repo)
    await repo.gf_add_participant(eid, 5, "Europe/Berlin",
                                  NOW + timedelta(minutes=1), max_players=8,
                                  source="JOIN")
    await repo.gf_add_participant(eid, 9, "Europe/Berlin", NOW, max_players=8,
                                  source="JOIN")
    assert [p["discord_id"] for p in await repo.gf_get_participants(eid)] == [9, 5]


# ── messages ────────────────────────────────────────────────────────────────

async def test_event_messages_upsert_and_lookup(repo):
    eid = await _event(repo)
    await repo.gf_set_event_message(eid, "Europe/Berlin", 100, 1000, NOW)
    await repo.gf_set_event_message(eid, "Asia/Tokyo", 200, 2000, NOW)
    await repo.gf_set_event_message(eid, "Europe/Berlin", 100, 1001, NOW)
    msgs = await repo.gf_get_event_messages(eid)
    assert [(m["zone"], m["message_id"]) for m in msgs] == [
        ("Asia/Tokyo", 2000), ("Europe/Berlin", 1001)]
    assert await repo.gf_message_lookup(1001) == {
        "event_id": eid, "zone": "Europe/Berlin", "channel_id": 100}
    assert await repo.gf_message_lookup(1000) is None


async def test_delete_event_message(repo):
    eid = await _event(repo)
    await repo.gf_set_event_message(eid, "Europe/Berlin", 100, 1000, NOW)
    await repo.gf_delete_event_message(eid, "Europe/Berlin")
    assert await repo.gf_get_event_messages(eid) == []


# ── one-time notifications ──────────────────────────────────────────────────

async def test_time_found_claim_succeeds_exactly_once(repo):
    eid = await _event(repo)
    assert await repo.gf_claim_time_found(eid, NOW) is True
    assert await repo.gf_claim_time_found(eid, NOW + timedelta(minutes=1)) is False
    assert (await repo.gf_get_event(eid))["time_found_notified_at"] == NOW


async def test_reminder_claim_succeeds_exactly_once_per_kind(repo):
    eid = await _event(repo)
    assert await repo.gf_claim_reminder(eid, 1, "REMINDER_15M", NOW) is True
    assert await repo.gf_claim_reminder(eid, 1, "REMINDER_15M", NOW) is False
    assert await repo.gf_claim_reminder(eid, 1, "CANCELLED", NOW) is True
    assert await repo.gf_claim_reminder(eid, 2, "REMINDER_15M", NOW) is True


async def test_reminder_outcome_is_recorded(repo):
    eid = await _event(repo)
    await repo.gf_claim_reminder(eid, 1, "REMINDER_15M", NOW)
    await repo.gf_claim_reminder(eid, 2, "REMINDER_15M", NOW)
    await repo.gf_mark_reminder(eid, 1, "REMINDER_15M", "SENT", NOW)
    await repo.gf_mark_reminder(eid, 2, "REMINDER_15M", "FAILED", NOW)
    rows = await repo.gf_get_reminders(eid)
    assert [(r["discord_id"], r["status"]) for r in rows] == [(1, "SENT"), (2, "FAILED")]
    assert rows[0]["sent_at"] == NOW and rows[1]["sent_at"] is None


async def test_reminders_survive_export_import(repo, make_repo):
    eid = await _event(repo)
    await repo.gf_claim_reminder(eid, 1, "REMINDER_15M", NOW)
    await repo.gf_mark_reminder(eid, 1, "REMINDER_15M", "SENT", NOW)
    snapshot = await repo.export_all()
    dest = await make_repo()
    try:
        await dest.import_all(snapshot)
        # a restart on the imported data must not send it again
        assert await dest.gf_claim_reminder(eid, 1, "REMINDER_15M", NOW) is False
    finally:
        await dest.close()


# ── migration fidelity ──────────────────────────────────────────────────────

async def test_event_tables_in_migrate_data_tables():
    from n3x_bot import migrate
    for t in ("gf_events", "gf_slots", "gf_votes", "gf_participants",
              "gf_messages", "gf_reminders"):
        assert t in migrate._DATA_TABLES


async def test_export_import_roundtrip(repo, make_repo):
    eid = await _event(repo, legacy_lfg_id=3)
    await repo.gf_set_votes(eid, 1, [S1, S2], "Asia/Karachi", NOW)
    await _schedule(repo, eid, [(1, "Asia/Karachi")])
    await repo.gf_set_event_message(eid, "Asia/Karachi", 100, 1000, NOW)
    snapshot = await repo.export_all()
    dest = await make_repo()
    try:
        await dest.import_all(snapshot)
        e = await dest.gf_get_event(eid)
        assert e["slots"] == [S1, S2, S3]
        assert e["status"] == "SCHEDULED" and e["scheduled_at"] == S2
        assert e["legacy_lfg_id"] == 3
        assert len(await dest.gf_get_votes(eid)) == 2
        assert [p["discord_id"] for p in await dest.gf_get_participants(eid)] == [1]
        assert (await dest.gf_message_lookup(1000))["event_id"] == eid
        # the id sequence continues after the imported rows
        assert await _event(dest, title="next") > eid
    finally:
        await dest.close()


async def test_clear_removes_events(repo):
    eid = await _event(repo)
    await repo.gf_set_votes(eid, 1, [S1], "Europe/Berlin", NOW)
    await repo.clear()
    assert await repo.gf_get_event(eid) is None
    assert await repo.gf_get_votes(eid) == []
