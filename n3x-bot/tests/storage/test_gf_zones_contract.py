"""Contract tests for the Group Finder zone surface (gf_settings, gf_zones,
gf_members). Parametrized across every registered backend via the shared
``repo`` / ``make_repo`` fixtures, like the other storage contracts.
"""
from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
ZONE_KEYS = {"zone", "role_id", "channel_id", "status", "created_at",
             "updated_at", "deactivated_at"}


# ── settings ────────────────────────────────────────────────────────────────

async def test_setting_unknown_key_is_none(repo):
    assert await repo.gf_get_setting("category_id") is None


async def test_setting_roundtrip_and_overwrite(repo):
    await repo.gf_set_setting("category_id", "111")
    await repo.gf_set_setting("category_id", "222")
    assert await repo.gf_get_setting("category_id") == "222"


async def test_setting_can_be_cleared(repo):
    await repo.gf_set_setting("hub_channel_id", "5")
    await repo.gf_set_setting("hub_channel_id", None)
    assert await repo.gf_get_setting("hub_channel_id") is None


# ── zones ───────────────────────────────────────────────────────────────────

async def test_unknown_zone_is_none(repo):
    assert await repo.gf_get_zone("Europe/Berlin") is None


async def test_save_zone_roundtrips_all_fields(repo):
    await repo.gf_save_zone("Europe/Berlin", role_id=10, channel_id=20,
                            status="ACTIVE", now=NOW)
    z = await repo.gf_get_zone("Europe/Berlin")
    assert set(z) == ZONE_KEYS
    assert (z["role_id"], z["channel_id"], z["status"]) == (10, 20, "ACTIVE")
    assert z["created_at"] == NOW and z["updated_at"] == NOW
    assert z["deactivated_at"] is None


async def test_ids_roundtrip_as_int(repo):
    await repo.gf_save_zone("Asia/Tokyo", role_id=1531288209730830447,
                            channel_id=1525916870857723984, status="ACTIVE", now=NOW)
    z = await repo.gf_get_zone("Asia/Tokyo")
    assert isinstance(z["role_id"], int) and z["role_id"] == 1531288209730830447
    assert isinstance(z["channel_id"], int)


async def test_resave_keeps_created_at_and_bumps_updated_at(repo):
    await repo.gf_save_zone("Europe/Berlin", role_id=10, channel_id=20,
                            status="ACTIVE", now=NOW)
    later = NOW + timedelta(hours=3)
    await repo.gf_save_zone("Europe/Berlin", role_id=10, channel_id=21,
                            status="ACTIVE", now=later)
    z = await repo.gf_get_zone("Europe/Berlin")
    assert z["created_at"] == NOW
    assert z["updated_at"] == later
    assert z["channel_id"] == 21


async def test_deactivation_sets_and_reactivation_clears_deactivated_at(repo):
    await repo.gf_save_zone("Europe/Berlin", role_id=10, channel_id=20,
                            status="ACTIVE", now=NOW)
    off = NOW + timedelta(days=1)
    await repo.gf_save_zone("Europe/Berlin", role_id=10, channel_id=None,
                            status="DEACTIVATED", now=off)
    z = await repo.gf_get_zone("Europe/Berlin")
    assert z["status"] == "DEACTIVATED"
    assert z["deactivated_at"] == off
    assert z["channel_id"] is None and z["role_id"] == 10   # role kept for reuse
    await repo.gf_save_zone("Europe/Berlin", role_id=10, channel_id=30,
                            status="ACTIVE", now=off + timedelta(days=1))
    assert (await repo.gf_get_zone("Europe/Berlin"))["deactivated_at"] is None


async def test_all_zones_sorted_by_id(repo):
    for z in ("Europe/London", "America/New_York", "Asia/Tokyo"):
        await repo.gf_save_zone(z, role_id=1, channel_id=2, status="ACTIVE", now=NOW)
    assert [z["zone"] for z in await repo.gf_all_zones()] == [
        "America/New_York", "Asia/Tokyo", "Europe/London"]


async def test_zone_by_channel(repo):
    await repo.gf_save_zone("Europe/Berlin", role_id=1, channel_id=777,
                            status="ACTIVE", now=NOW)
    assert (await repo.gf_zone_by_channel(777))["zone"] == "Europe/Berlin"
    assert await repo.gf_zone_by_channel(778) is None


# ── member zones ────────────────────────────────────────────────────────────

async def test_member_zone_unset_is_none(repo):
    assert await repo.gf_get_member_zone(42) is None


async def test_member_has_exactly_one_zone(repo):
    await repo.gf_set_member_zone(42, "Europe/Berlin", NOW)
    await repo.gf_set_member_zone(42, "Asia/Karachi", NOW)
    assert await repo.gf_get_member_zone(42) == "Asia/Karachi"


async def test_clear_member_zone(repo):
    await repo.gf_set_member_zone(42, "Europe/Berlin", NOW)
    await repo.gf_set_member_zone(43, "Europe/Berlin", NOW)
    assert await repo.gf_clear_member_zone(42) == "Europe/Berlin"
    assert await repo.gf_get_member_zone(42) is None
    assert await repo.gf_get_member_zone(43) == "Europe/Berlin"   # others kept
    assert await repo.gf_clear_member_zone(42) is None


async def test_clear_zone_members_removes_only_that_zone(repo):
    await repo.gf_set_member_zone(1, "Europe/Berlin", NOW)
    await repo.gf_set_member_zone(2, "Europe/Berlin", NOW)
    await repo.gf_set_member_zone(3, "Asia/Tokyo", NOW)
    assert await repo.gf_clear_zone_members("Europe/Berlin") == [1, 2]
    assert await repo.gf_get_member_zone(1) is None
    assert await repo.gf_get_member_zone(3) == "Asia/Tokyo"


# ── migration fidelity ──────────────────────────────────────────────────────

async def test_gf_tables_in_migrate_data_tables():
    from n3x_bot import migrate
    for t in ("gf_settings", "gf_zones", "gf_members"):
        assert t in migrate._DATA_TABLES


async def test_export_import_roundtrip(repo, make_repo):
    await repo.gf_set_setting("category_id", "900")
    await repo.gf_save_zone("Europe/Berlin", role_id=10, channel_id=20,
                            status="ACTIVE", now=NOW)
    await repo.gf_save_zone("Asia/Tokyo", role_id=11, channel_id=None,
                            status="DEACTIVATED", now=NOW)
    await repo.gf_set_member_zone(42, "Europe/Berlin", NOW)
    snapshot = await repo.export_all()
    dest = await make_repo()
    try:
        await dest.import_all(snapshot)
        assert await dest.gf_get_setting("category_id") == "900"
        assert (await dest.gf_get_zone("Europe/Berlin"))["channel_id"] == 20
        tokyo = await dest.gf_get_zone("Asia/Tokyo")
        assert tokyo["status"] == "DEACTIVATED" and tokyo["deactivated_at"] == NOW
        assert await dest.gf_get_member_zone(42) == "Europe/Berlin"
    finally:
        await dest.close()


async def test_clear_removes_gf_data(repo):
    await repo.gf_set_setting("category_id", "900")
    await repo.gf_save_zone("Europe/Berlin", role_id=1, channel_id=2,
                            status="ACTIVE", now=NOW)
    await repo.gf_set_member_zone(42, "Europe/Berlin", NOW)
    await repo.clear()
    assert await repo.gf_get_setting("category_id") is None
    assert await repo.gf_all_zones() == []
    assert await repo.gf_get_member_zone(42) is None
