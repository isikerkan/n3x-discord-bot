import os

import pytest

from n3x_bot.storage.json_repo import JsonRepository
from n3x_bot.storage.sql_repo import SqlRepository


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


def _cleanup_repo_files(r) -> None:
    path = getattr(r, "path", None)
    if path and os.path.exists(path):
        os.remove(path)
    db_path = getattr(r, "_test_db_path", None)
    if db_path and os.path.exists(db_path):
        os.remove(db_path)


@pytest.fixture
async def repo(repo_factory):
    r = await repo_factory()
    try:
        yield r
    finally:
        await r.close()
        _cleanup_repo_files(r)


@pytest.fixture
async def make_repo(repo_factory):
    """Factory yielding freshly-connected, EMPTY repos of the same backend.

    Used by the export/import round-trip contract tests to obtain a
    destination repo distinct from the seeded ``repo``. For the SQL backends
    the underlying ``repo_factory`` starts from a clean schema on each call
    (a new temp file for sqlite; a drop/create for postgres), so every repo
    handed out here is empty. Callers must capture any needed snapshot of the
    source BEFORE requesting a fresh repo, since the postgres factory shares a
    single physical database across calls.
    """
    created = []

    async def _make():
        r = await repo_factory()
        created.append(r)
        return r

    try:
        yield _make
    finally:
        for r in created:
            await r.close()
            _cleanup_repo_files(r)


async def _make_sqlite():
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    r = SqlRepository(f"sqlite+aiosqlite:///{path}")
    await r.connect()
    r._test_db_path = path
    return r


BACKENDS.append(("sqlite", _make_sqlite))

# Postgres only if a test DSN is provided; otherwise the id never registers
# and postgres is silently skipped (not failed).
_PG = os.environ.get("TEST_POSTGRES_URL")
if _PG:
    # Per-xdist-worker database so parallel workers don't collide on one shared
    # DB (each still drops/creates the schema per test). Requires the role to
    # have CREATEDB. Falls back to a single "_main" DB when not run under xdist.
    _worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    _pg_base_url, _pg_base_db = _PG.rsplit("/", 1)
    _pg_db = f"{_pg_base_db}_{_worker}"
    _pg_url = f"{_pg_base_url}/{_pg_db}"
    _pg_db_ready = False

    async def _ensure_pg_db():
        global _pg_db_ready
        if _pg_db_ready:
            return
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        admin = create_async_engine(_PG, isolation_level="AUTOCOMMIT")
        try:
            async with admin.connect() as conn:
                found = await conn.scalar(
                    text("SELECT 1 FROM pg_database WHERE datname = :n"),
                    {"n": _pg_db})
                if not found:
                    await conn.exec_driver_sql(f'CREATE DATABASE "{_pg_db}"')
        finally:
            await admin.dispose()
        _pg_db_ready = True

    async def _make_postgres():
        await _ensure_pg_db()
        r = SqlRepository(_pg_url)
        await r.connect()
        # clean slate each test
        from n3x_bot.storage import schema as sc
        async with r.engine.begin() as conn:
            await conn.run_sync(sc.metadata.drop_all)
            await conn.run_sync(sc.metadata.create_all)
        return r

    BACKENDS.append(("postgres", _make_postgres))
