import json
import os
import tempfile

from n3x_bot.storage.json_repo import JsonRepository
from n3x_bot.seed import seed_defaults, migrate_legacy_json, LEGACY_STATS


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
