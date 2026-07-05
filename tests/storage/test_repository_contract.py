import pytest


async def test_create_and_get_stat(repo):
    await repo.create_stat("tit", "Tit")
    s = await repo.get_stat("tit")
    assert s is not None and s.key == "tit" and s.name == "Tit"
    assert s.message_id is None


async def test_get_missing_stat_returns_none(repo):
    assert await repo.get_stat("nope") is None


async def test_list_stats_excludes_archived_by_default(repo):
    await repo.create_stat("a", "A")
    await repo.create_stat("b", "B")
    await repo.archive_stat("a")
    keys = {s.key for s in await repo.list_stats()}
    assert keys == {"b"}
    all_keys = {s.key for s in await repo.list_stats(include_archived=True)}
    assert all_keys == {"a", "b"}


async def test_update_and_delete_stat(repo):
    await repo.create_stat("x", "X")
    updated = await repo.update_stat("x", name="X2")
    assert updated.name == "X2"
    await repo.delete_stat("x")
    assert await repo.get_stat("x") is None


async def test_message_crud_and_link(repo):
    m = await repo.create_message("greet", "hi {user}")
    assert m.id > 0
    await repo.create_stat("k", "K")
    linked = await repo.set_stat_message("k", m.id)
    assert linked.message_id == m.id
    unlinked = await repo.set_stat_message("k", None)
    assert unlinked.message_id is None


async def test_upsert_user_is_idempotent(repo):
    u1 = await repo.upsert_user(42, "Erkan")
    u2 = await repo.upsert_user(42, "Erkan Renamed")
    assert u1.id == u2.id
    assert (await repo.get_user(42)).display_name == "Erkan Renamed"
    assert len(await repo.list_users()) == 1


async def test_upsert_user_unarchives(repo):
    await repo.upsert_user(42, "Erkan")
    await repo.archive_user(42)
    assert 42 not in {u.discord_id for u in await repo.list_users()}
    # re-upsert (rejoin) must un-archive
    u = await repo.upsert_user(42, "Erkan Back")
    assert u.archived_at is None
    assert u.display_name == "Erkan Back"
    assert 42 in {u.discord_id for u in await repo.list_users()}


async def test_record_use_increments_user_and_total(repo):
    await repo.create_stat("tit", "Tit")
    uc1, tc1 = await repo.record_use(42, "Erkan", "tit")
    uc2, tc2 = await repo.record_use(42, "Erkan", "tit")
    uc3, tc3 = await repo.record_use(99, "Ali", "tit")
    assert (uc1, tc1) == (1, 1)
    assert (uc2, tc2) == (2, 2)
    assert (uc3, tc3) == (1, 3)
    assert await repo.get_total("tit") == 3
    assert await repo.get_user_stats(42) == {"tit": 2}


async def test_record_use_unknown_stat_raises(repo):
    with pytest.raises(KeyError):
        await repo.record_use(1, "Nobody", "ghost")


async def test_last_post_roundtrip(repo):
    await repo.create_stat("tit", "Tit")
    assert await repo.get_last_post("tit") is None
    await repo.set_last_post("tit", 123, 456)
    assert await repo.get_last_post("tit") == (123, 456)
    await repo.set_last_post("tit", 789, 456)
    assert await repo.get_last_post("tit") == (789, 456)
