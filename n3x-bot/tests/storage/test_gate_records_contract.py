"""Contract tests for the Delta ('d') gate + gate min/max records.

Parametrized across every registered backend via the shared ``repo`` /
``make_repo`` fixtures (json, sqlite, and postgres when TEST_POSTGRES_URL is
set) — see tests/storage/conftest.py.

These are RED until the delta-gate feature lands. The new interface being
pinned here:

    GATE_TYPES includes "d"

    add_gate_entry(gate_type, cost, user_id, username,
                   dedup_window_seconds=30, laser_dropped: bool | None = None) -> bool
        laser_dropped is only meaningful for gate_type == "d"; a/b/c entries
        store it as None.

    delta_stats() -> {"count": int, "avg": int, "laser_rate": float}
        laser_rate = 100 * (# d entries with laser_dropped True) / count.

    gate_record(gate_type) -> {"min_cost": int, "min_user": int,
                               "max_cost": int, "max_user": int} | None
        Computed ON-DEMAND from gate_entries (the v3 B7 fix: never stored-stale).
        None when the gate type has no entries.

New nullable column: gate_entries.laser_dropped (Boolean, nullable).

All new symbols are referenced through repo methods / a lazy import of
GATE_TYPES inside the test body, so the module still collects cleanly (the
failures are missing-behaviour, not test-file import errors).
"""

import pytest


# ── GATE_TYPES gains "d" ─────────────────────────────────────────────────────

async def test_gate_types_includes_delta(repo):
    from n3x_bot.storage.base import GATE_TYPES
    assert "d" in GATE_TYPES


# ── add_gate_entry laser_dropped ─────────────────────────────────────────────

async def test_add_delta_entry_with_laser_true_is_counted(repo):
    assert await repo.add_gate_entry("d", 75000, 1, "u1", laser_dropped=True) is True
    assert await repo.list_gate_costs("d") == [75000]
    totals = await repo.gate_totals()
    assert totals["d"]["count"] == 1
    assert totals["d"]["avg"] == 75000


async def test_add_delta_entry_with_laser_false_is_counted(repo):
    assert await repo.add_gate_entry("d", 80000, 2, "u2", laser_dropped=False) is True
    assert await repo.list_gate_costs("d") == [80000]


async def test_abc_entries_store_laser_dropped_as_none(repo):
    await repo.add_gate_entry("a", 46000, 1, "u1")
    snapshot = await repo.export_all()
    a_rows = [r for r in snapshot["gate_entries"] if r["gate_type"] == "a"]
    assert len(a_rows) == 1
    assert a_rows[0]["laser_dropped"] is None


# ── delta_stats laser rate ───────────────────────────────────────────────────

async def test_delta_stats_laser_rate_two_of_three(repo):
    await repo.add_gate_entry("d", 70000, 1, "u1", laser_dropped=True)
    await repo.add_gate_entry("d", 72000, 2, "u2", laser_dropped=True)
    await repo.add_gate_entry("d", 74000, 3, "u3", laser_dropped=False)
    stats = await repo.delta_stats()
    assert stats["count"] == 3
    assert stats["avg"] == 72000
    assert stats["laser_rate"] == pytest.approx(66.7, abs=0.1)


async def test_delta_stats_laser_rate_zero_when_no_entries(repo):
    stats = await repo.delta_stats()
    assert stats["count"] == 0
    assert stats["laser_rate"] == 0


# ── gate_record: min/max holders, on-demand ──────────────────────────────────

async def test_gate_record_returns_min_and_max_with_holders(repo):
    await repo.add_gate_entry("a", 100, 11, "low")
    await repo.add_gate_entry("a", 500, 22, "high")
    await repo.add_gate_entry("a", 300, 33, "mid")
    record = await repo.gate_record("a")
    assert record["min_cost"] == 100
    assert record["min_user"] == 11
    assert record["max_cost"] == 500
    assert record["max_user"] == 22


async def test_gate_record_none_when_no_entries(repo):
    assert await repo.gate_record("a") is None


async def test_gate_record_recomputes_after_deleting_record_holder(repo):
    # B7 fix: deleting the min-holding entry must yield the NEW min, not a
    # stale stored value.
    await repo.add_gate_entry("a", 100, 11, "low")   # insertion index 1
    await repo.add_gate_entry("a", 500, 22, "high")  # insertion index 2
    await repo.add_gate_entry("a", 300, 33, "mid")   # insertion index 3

    assert (await repo.gate_record("a"))["min_cost"] == 100

    # delete the 100-cost entry (1-based insertion index 1)
    assert await repo.delete_gate_entry("a", 1) is True

    record = await repo.gate_record("a")
    assert record["min_cost"] == 300
    assert record["min_user"] == 33
    assert record["max_cost"] == 500  # unchanged


async def test_gate_record_covers_delta_gate(repo):
    await repo.add_gate_entry("d", 60000, 5, "cheap", laser_dropped=True)
    await repo.add_gate_entry("d", 90000, 6, "pricey", laser_dropped=False)
    record = await repo.gate_record("d")
    assert record["min_cost"] == 60000 and record["min_user"] == 5
    assert record["max_cost"] == 90000 and record["max_user"] == 6


# ── user aggregates include delta ────────────────────────────────────────────

async def test_user_gate_counts_includes_delta(repo):
    await repo.add_gate_entry("d", 70000, 7, "erkan", laser_dropped=True)
    await repo.add_gate_entry("d", 71000, 7, "erkan", laser_dropped=False)
    counts = await repo.user_gate_counts(7)
    assert counts.get("d") == 2


async def test_user_gate_cost_total_includes_delta(repo):
    await repo.add_gate_entry("d", 70000, 7, "erkan", laser_dropped=True)
    assert await repo.user_gate_cost_total(7) == 70000


# ── export / import round-trip preserves laser_dropped ───────────────────────

async def test_round_trip_preserves_laser_dropped(repo, make_repo):
    await repo.add_gate_entry("d", 75000, 1, "u1", laser_dropped=True)
    await repo.add_gate_entry("d", 76000, 2, "u2", laser_dropped=False)
    await repo.add_gate_entry("a", 46000, 3, "u3")
    snapshot = await repo.export_all()

    dest = await make_repo()
    await dest.import_all(snapshot)

    dest_rows = {(r["gate_type"], r["cost"]): r
                 for r in (await dest.export_all())["gate_entries"]}
    assert dest_rows[("d", 75000)]["laser_dropped"] is True
    assert dest_rows[("d", 76000)]["laser_dropped"] is False
    assert dest_rows[("a", 46000)]["laser_dropped"] is None
