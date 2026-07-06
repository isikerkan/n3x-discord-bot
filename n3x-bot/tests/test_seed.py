import json
import os
import tempfile

from n3x_bot.storage.json_repo import JsonRepository
from n3x_bot.seed import seed_defaults, migrate_legacy_json, LEGACY_STATS, TARGETED_STATS


async def _repo():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    r = JsonRepository(path)
    await r.connect()
    return r


async def test_seed_is_idempotent():
    r = await _repo()
    await seed_defaults(r)
    await seed_defaults(r)
    stats = await r.list_stats()
    assert len(stats) == len(LEGACY_STATS)
    tit = await r.get_stat("tit")
    assert tit.message_id is not None
    msg = await r.get_message(tit.message_id)
    assert "{count}" in msg.template
    await r.close()


async def test_seed_marks_targeted_stats_correctly():
    r = await _repo()
    await seed_defaults(r)
    for key, _, _ in LEGACY_STATS:
        stat = await r.get_stat(key)
        assert stat.targeted == (key in TARGETED_STATS), key
    assert TARGETED_STATS == {"smart", "crash", "home"}
    await r.close()


async def test_migrate_legacy_counts():
    fd, legacy = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump({
            "tit_count": 3, "cry_count": 0,
            "user_stats": {"42": {"tit": 2}},
        }, f)
    r = await _repo()
    await seed_defaults(r)
    await migrate_legacy_json(r, legacy)
    assert await r.get_total("tit") == 3
    assert await r.get_user_stats(42) == {"tit": 2}
    os.remove(legacy)
    await r.close()


async def test_migrate_legacy_json_is_idempotent():
    fd, legacy = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump({
            "tit_count": 3, "cry_count": 0,
            "user_stats": {"42": {"tit": 2}},
        }, f)
    r = await _repo()
    await seed_defaults(r)
    await migrate_legacy_json(r, legacy)
    await migrate_legacy_json(r, legacy)
    assert await r.get_total("tit") == 3
    assert await r.get_user_stats(42) == {"tit": 2}
    os.remove(legacy)
    await r.close()


async def test_migrate_legacy_json_zero_count_key():
    fd, legacy = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump({
            "tit_count": 3, "cry_count": 0,
            "user_stats": {"42": {"tit": 2}},
        }, f)
    r = await _repo()
    await seed_defaults(r)
    await migrate_legacy_json(r, legacy)
    assert await r.get_total("cry") == 0
    os.remove(legacy)
    await r.close()


async def test_migrate_legacy_json_attributes_remainder_to_user_zero():
    fd, legacy = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump({
            "wahab_count": 5,
            "user_stats": {},
        }, f)
    r = await _repo()
    await seed_defaults(r)
    await migrate_legacy_json(r, legacy)
    assert await r.get_total("wahab") == 5
    assert await r.get_user_stats(0) == {"wahab": 5}
    os.remove(legacy)
    await r.close()
