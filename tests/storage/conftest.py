import os

import pytest

from n3x_bot.storage.json_repo import JsonRepository


async def _make_json():
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)  # start clean; connect() will create it
    r = JsonRepository(path)
    await r.connect()
    return r


# Backends are appended by Task 5 (json) and Task 6 (sql).
# Each entry: (id, async factory returning a *connected* StatsRepository).
BACKENDS: list = [("json", _make_json)]


def pytest_generate_tests(metafunc):
    if "repo" in metafunc.fixturenames:
        if not BACKENDS:
            pytest.skip("no storage backends registered yet")
        ids = [b[0] for b in BACKENDS]
        metafunc.parametrize("repo_factory", [b[1] for b in BACKENDS], ids=ids)


@pytest.fixture
async def repo(repo_factory):
    r = await repo_factory()
    try:
        yield r
    finally:
        await r.close()
        path = getattr(r, "path", None)
        if path and os.path.exists(path):
            os.remove(path)


from n3x_bot.storage.sql_repo import SqlRepository


async def _make_sqlite():
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    r = SqlRepository(f"sqlite+aiosqlite:///{path}")
    await r.connect()
    return r


BACKENDS.append(("sqlite", _make_sqlite))

# Postgres only if a test DSN is provided; otherwise the id never registers
# and postgres is silently skipped (not failed).
_PG = os.environ.get("TEST_POSTGRES_URL")
if _PG:
    async def _make_postgres():
        r = SqlRepository(_PG)
        await r.connect()
        # clean slate each test
        from n3x_bot.storage import schema as sc
        async with r.engine.begin() as conn:
            await conn.run_sync(sc.metadata.drop_all)
            await conn.run_sync(sc.metadata.create_all)
        return r

    BACKENDS.append(("postgres", _make_postgres))
