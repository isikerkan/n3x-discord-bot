"""Contract tests for the timestamped + drops gate-history read view backing
the ``!gate verlauf`` chart, parametrized across every registered backend via
the shared ``repo`` / ``make_repo`` fixtures (json, sqlite, and postgres when
TEST_POSTGRES_URL is set) — see tests/storage/conftest.py.

New interface being pinned (to be implemented on StatsRepository + json_repo +
sql_repo):

    async def list_gate_entries(
        gate_type: str,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict]

Each returned dict has exactly the render-relevant fields:

        {"cost": int,
         "created_at": datetime (tz-aware),
         "drops": dict[str, bool]}

Ordered by ``created_at`` ASCENDING. ``since`` / ``until`` are an INCLUSIVE
tz-aware filter over ``created_at`` (tz-aware compare, like
``purge_expired_base_timers``). ``drops`` reuses the generalized drop read:
d -> {"laser": bool} (from the legacy ``laser_dropped`` column OR the ``drops``
map), e -> {"lf4": bool}, z -> {"havoc": bool},
k -> {"hercules": bool, "lf4u": bool}, and a/b/c -> {} (no drops).

This is a READ VIEW only: it is NOT part of export_all / import_all.

RED until ``list_gate_entries`` lands: calling it raises AttributeError on the
repo. Every new symbol is referenced through repo methods so the module still
collects cleanly.
"""

from datetime import timedelta


# ── ordering + field shape ───────────────────────────────────────────────────

async def test_list_gate_entries_empty_gate_is_empty_list(repo):
    assert await repo.list_gate_entries("a") == []


async def test_list_gate_entries_returns_cost_created_at_and_drops(repo):
    await repo.add_gate_entry("a", 46892, 1, "u1")
    entries = await repo.list_gate_entries("a")
    assert len(entries) == 1
    entry = entries[0]
    assert entry["cost"] == 46892
    assert entry["created_at"] is not None
    assert entry["drops"] == {}


async def test_list_gate_entries_created_at_is_tz_aware(repo):
    await repo.add_gate_entry("a", 100, 1, "u1")
    entry = (await repo.list_gate_entries("a"))[0]
    assert entry["created_at"].tzinfo is not None


async def test_list_gate_entries_ordered_by_created_at_ascending(repo):
    await repo.add_gate_entry("a", 100, 1, "u1")
    await repo.add_gate_entry("a", 200, 2, "u2")
    await repo.add_gate_entry("a", 300, 3, "u3")
    entries = await repo.list_gate_entries("a")
    assert [e["cost"] for e in entries] == [100, 200, 300]
    stamps = [e["created_at"] for e in entries]
    assert stamps == sorted(stamps)


# ── drops shape per gate type ────────────────────────────────────────────────

async def test_list_gate_entries_abc_have_empty_drops(repo):
    await repo.add_gate_entry("b", 500, 1, "u1")
    assert (await repo.list_gate_entries("b"))[0]["drops"] == {}


async def test_list_gate_entries_delta_exposes_laser_drop(repo):
    await repo.add_gate_entry("d", 70000, 1, "u1", laser_dropped=True)
    assert (await repo.list_gate_entries("d"))[0]["drops"] == {"laser": True}


async def test_list_gate_entries_epsilon_exposes_lf4_drop(repo):
    await repo.add_gate_entry("e", 40000, 1, "u1", drops={"lf4": False})
    assert (await repo.list_gate_entries("e"))[0]["drops"] == {"lf4": False}


async def test_list_gate_entries_zeta_exposes_havoc_drop(repo):
    await repo.add_gate_entry("z", 2000, 1, "u1", drops={"havoc": True})
    assert (await repo.list_gate_entries("z"))[0]["drops"] == {"havoc": True}


async def test_list_gate_entries_kappa_exposes_both_items(repo):
    await repo.add_gate_entry("k", 500, 1, "u1",
                              drops={"hercules": True, "lf4u": False})
    drops = (await repo.list_gate_entries("k"))[0]["drops"]
    assert drops == {"hercules": True, "lf4u": False}


# ── inclusive since / until filter ───────────────────────────────────────────

async def test_list_gate_entries_since_is_inclusive(repo):
    await repo.add_gate_entry("a", 100, 1, "u1")
    await repo.add_gate_entry("a", 200, 2, "u2")
    entries = await repo.list_gate_entries("a")
    t_last = entries[-1]["created_at"]
    # since == the last entry's own timestamp keeps that entry (inclusive)
    filtered = await repo.list_gate_entries("a", since=t_last)
    assert 200 in [e["cost"] for e in filtered]


async def test_list_gate_entries_until_is_inclusive(repo):
    await repo.add_gate_entry("a", 100, 1, "u1")
    await repo.add_gate_entry("a", 200, 2, "u2")
    entries = await repo.list_gate_entries("a")
    t_first = entries[0]["created_at"]
    # until == the first entry's own timestamp keeps that entry (inclusive)
    filtered = await repo.list_gate_entries("a", until=t_first)
    assert 100 in [e["cost"] for e in filtered]


async def test_list_gate_entries_window_fully_in_future_is_empty(repo):
    await repo.add_gate_entry("a", 100, 1, "u1")
    entries = await repo.list_gate_entries("a")
    future = entries[-1]["created_at"] + timedelta(days=1)
    assert await repo.list_gate_entries("a", since=future) == []


async def test_list_gate_entries_window_fully_in_past_is_empty(repo):
    await repo.add_gate_entry("a", 100, 1, "u1")
    entries = await repo.list_gate_entries("a")
    past = entries[0]["created_at"] - timedelta(days=1)
    assert await repo.list_gate_entries("a", until=past) == []


async def test_list_gate_entries_only_returns_requested_gate_type(repo):
    await repo.add_gate_entry("a", 100, 1, "u1")
    await repo.add_gate_entry("b", 200, 2, "u2")
    entries = await repo.list_gate_entries("a")
    assert [e["cost"] for e in entries] == [100]
