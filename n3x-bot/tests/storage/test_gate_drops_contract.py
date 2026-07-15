"""Contract tests for the GENERALIZED per-item gate drop tracking (Epsilon /
Zeta / Kappa + the existing Delta laser drop), parametrized across every
registered backend via the shared ``repo`` / ``make_repo`` fixtures (json,
sqlite, and postgres when TEST_POSTGRES_URL is set) — see
tests/storage/conftest.py.

RED until the E/Z/K gate feature lands. The new interface being pinned:

    GATE_TYPES includes "e", "z", "k".

    add_gate_entry(gate_type, cost, user_id, username,
                   dedup_window_seconds=30, drops: dict[str, bool] | None = None) -> bool
        Generalizes the single ``laser_dropped`` bool into a per-entry item
        drop mapping. Callers pass:
            d -> {"laser": bool}      e -> {"lf4": bool}
            z -> {"havoc": bool}      k -> {"hercules": bool, "lf4u": bool}
            a/b/c -> None
        (Whether ``laser_dropped=`` is kept as a compat alias is the
        architect's call — see report. These tests use ``drops=``.)

    gate_drop_stats(gate_type) -> {"count": int, "avg": int,
                                   "rates": {item: float_pct}}
        rates[item] = 100 * (# entries with that item True) / count, one key
        per distinct drop-item observed among that gate's entries. Empty gate
        -> count 0, avg 0, rates {} (pinned shape).

Every new symbol is referenced through repo methods / a lazy import of
GATE_TYPES inside the test body, so the module still collects cleanly (the
failures are missing-behaviour, not test-file import errors).
"""

import pytest


# ── GATE_TYPES gains e / z / k ───────────────────────────────────────────────

async def test_gate_types_includes_epsilon_zeta_kappa(repo):
    from n3x_bot.storage.base import GATE_TYPES
    assert "e" in GATE_TYPES
    assert "z" in GATE_TYPES
    assert "k" in GATE_TYPES


# ── gate_drop_stats: empty shape ─────────────────────────────────────────────

async def test_gate_drop_stats_empty_is_zeroed_with_no_rates(repo):
    stats = await repo.gate_drop_stats("e")
    assert stats["count"] == 0
    assert stats["avg"] == 0
    assert stats["rates"] == {}


# ── Epsilon: single item (lf4) ───────────────────────────────────────────────

async def test_epsilon_drop_rate_two_of_four(repo):
    await repo.add_gate_entry("e", 40000, 1, "u1", drops={"lf4": True})
    await repo.add_gate_entry("e", 42000, 2, "u2", drops={"lf4": True})
    await repo.add_gate_entry("e", 44000, 3, "u3", drops={"lf4": False})
    await repo.add_gate_entry("e", 46000, 4, "u4", drops={"lf4": False})
    stats = await repo.gate_drop_stats("e")
    assert stats["count"] == 4
    assert stats["avg"] == 43000
    assert stats["rates"]["lf4"] == pytest.approx(50.0, abs=0.1)


# ── Zeta: single item (havoc) ────────────────────────────────────────────────

async def test_zeta_drop_rate_one_of_three(repo):
    await repo.add_gate_entry("z", 1000, 1, "u1", drops={"havoc": True})
    await repo.add_gate_entry("z", 2000, 2, "u2", drops={"havoc": False})
    await repo.add_gate_entry("z", 3000, 3, "u3", drops={"havoc": False})
    stats = await repo.gate_drop_stats("z")
    assert stats["count"] == 3
    assert stats["rates"]["havoc"] == pytest.approx(33.3, abs=0.1)


# ── Kappa: two independent items (hercules, lf4u) ────────────────────────────

async def test_kappa_two_items_have_independent_rates(repo):
    # hercules drops on entry 1 only; lf4u drops on entry 2 only -> 50 / 50.
    await repo.add_gate_entry("k", 500, 1, "u1",
                              drops={"hercules": True, "lf4u": False})
    await repo.add_gate_entry("k", 600, 2, "u2",
                              drops={"hercules": False, "lf4u": True})
    stats = await repo.gate_drop_stats("k")
    assert stats["count"] == 2
    assert stats["rates"]["hercules"] == pytest.approx(50.0, abs=0.1)
    assert stats["rates"]["lf4u"] == pytest.approx(50.0, abs=0.1)


async def test_kappa_both_items_can_drop_on_same_entry(repo):
    await repo.add_gate_entry("k", 500, 1, "u1",
                              drops={"hercules": True, "lf4u": True})
    stats = await repo.gate_drop_stats("k")
    assert stats["rates"]["hercules"] == pytest.approx(100.0, abs=0.1)
    assert stats["rates"]["lf4u"] == pytest.approx(100.0, abs=0.1)


# ── a/b/c: None drops contribute no rates ────────────────────────────────────

async def test_abc_entries_have_no_drop_rates(repo):
    await repo.add_gate_entry("a", 46000, 1, "u1")
    stats = await repo.gate_drop_stats("a")
    assert stats["count"] == 1
    assert stats["rates"] == {}


# ── Delta keeps working through the generalized read path ────────────────────

async def test_delta_generalized_rates_match_laser_rate(repo):
    await repo.add_gate_entry("d", 70000, 1, "u1", drops={"laser": True})
    await repo.add_gate_entry("d", 72000, 2, "u2", drops={"laser": True})
    await repo.add_gate_entry("d", 74000, 3, "u3", drops={"laser": False})
    generalized = await repo.gate_drop_stats("d")
    delta = await repo.delta_stats()
    # The generalized "laser" rate must agree with the legacy delta_stats rate,
    # so the existing Delta embed keeps rendering the same number.
    assert generalized["rates"]["laser"] == pytest.approx(delta["laser_rate"],
                                                          abs=0.1)


async def test_delta_legacy_laser_dropped_param_still_reads_as_laser_rate(repo):
    # Back-compat: data written via the old laser_dropped= param must surface as
    # the generalized drops["laser"] rate unchanged (the migration contract).
    await repo.add_gate_entry("d", 60000, 1, "u1", laser_dropped=True)
    await repo.add_gate_entry("d", 62000, 2, "u2", laser_dropped=False)
    stats = await repo.gate_drop_stats("d")
    assert stats["rates"]["laser"] == pytest.approx(50.0, abs=0.1)


# ── gate_record works for e / z / k ──────────────────────────────────────────

async def test_gate_record_covers_epsilon(repo):
    await repo.add_gate_entry("e", 30000, 5, "cheap", drops={"lf4": True})
    await repo.add_gate_entry("e", 90000, 6, "pricey", drops={"lf4": False})
    record = await repo.gate_record("e")
    assert record["min_cost"] == 30000 and record["min_user"] == 5
    assert record["max_cost"] == 90000 and record["max_user"] == 6


async def test_gate_record_covers_zeta(repo):
    await repo.add_gate_entry("z", 1000, 5, "cheap", drops={"havoc": True})
    await repo.add_gate_entry("z", 5000, 6, "pricey", drops={"havoc": False})
    record = await repo.gate_record("z")
    assert record["min_cost"] == 1000 and record["max_cost"] == 5000


async def test_gate_record_covers_kappa(repo):
    await repo.add_gate_entry("k", 400, 5, "cheap",
                              drops={"hercules": True, "lf4u": False})
    await repo.add_gate_entry("k", 900, 6, "pricey",
                              drops={"hercules": False, "lf4u": True})
    record = await repo.gate_record("k")
    assert record["min_cost"] == 400 and record["min_user"] == 5
    assert record["max_cost"] == 900 and record["max_user"] == 6


# ── user aggregates include e / z / k ────────────────────────────────────────

async def test_user_gate_counts_includes_ezk(repo):
    await repo.add_gate_entry("e", 40000, 7, "erkan", drops={"lf4": True})
    await repo.add_gate_entry("z", 2000, 7, "erkan", drops={"havoc": False})
    await repo.add_gate_entry("k", 500, 7, "erkan",
                              drops={"hercules": True, "lf4u": True})
    counts = await repo.user_gate_counts(7)
    assert counts.get("e") == 1
    assert counts.get("z") == 1
    assert counts.get("k") == 1


# ── export / import round-trip preserves per-item drops ───────────────────────

async def test_round_trip_preserves_ezk_drops(repo, make_repo):
    await repo.add_gate_entry("e", 40000, 1, "u1", drops={"lf4": True})
    await repo.add_gate_entry("z", 2000, 2, "u2", drops={"havoc": False})
    await repo.add_gate_entry("k", 500, 3, "u3",
                              drops={"hercules": True, "lf4u": False})
    snapshot = await repo.export_all()

    dest = await make_repo()
    await dest.import_all(snapshot)

    assert (await dest.gate_drop_stats("e"))["rates"]["lf4"] == pytest.approx(100.0, abs=0.1)
    assert (await dest.gate_drop_stats("z"))["rates"]["havoc"] == pytest.approx(0.0, abs=0.1)
    k_rates = (await dest.gate_drop_stats("k"))["rates"]
    assert k_rates["hercules"] == pytest.approx(100.0, abs=0.1)
    assert k_rates["lf4u"] == pytest.approx(0.0, abs=0.1)


# ── clear wipes e/z/k entries ────────────────────────────────────────────────

async def test_clear_removes_ezk_entries(repo):
    await repo.add_gate_entry("e", 40000, 1, "u1", drops={"lf4": True})
    await repo.add_gate_entry("k", 500, 2, "u2",
                              drops={"hercules": True, "lf4u": True})
    await repo.clear()
    assert await repo.list_gate_costs("e") == []
    assert await repo.list_gate_costs("k") == []
