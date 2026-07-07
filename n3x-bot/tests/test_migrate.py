"""Tests for the generic repo->repo migration and its CLI wrapper.

Expected NEW symbols (to be implemented downstream in ``n3x_bot/migrate.py``):

    async def migrate(source: StatsRepository, dest: StatsRepository,
                      *, overwrite: bool = False) -> None
        Copy ALL data from a connected ``source`` repo into a connected
        ``dest`` repo with full fidelity (all tables).

    async def run_migration(*, from_backend: str, from_location: str,
                            to_backend: str, to_location: str,
                            overwrite: bool = False) -> None
        The callable the ``python -m n3x_bot.migrate`` CLI wraps. Builds
        source/dest repos from a backend name ("flatfile" | "sqlite" |
        "postgres") + a location (data_file path for flatfile, database_url
        for sqlite/postgres), reusing Settings/factory, runs ``migrate``, and
        closes both repos.

    class DestinationNotEmptyError(Exception)
        Raised by ``run_migration`` when the destination already holds data
        and ``overwrite`` is False (default: refuse to clobber).

    def main() -> None
        Entrypoint for ``python -m n3x_bot.migrate``.

All of the above live in the not-yet-created module ``n3x_bot.migrate``, so
these tests are RED with ModuleNotFoundError until it is implemented.
"""

import os
import tempfile

import pytest

from n3x_bot.storage.json_repo import JsonRepository
from n3x_bot.storage.sql_repo import SqlRepository
from tests._seed import seed_everything

_PG = os.environ.get("TEST_POSTGRES_URL")


async def _seeded_json_source():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)  # start clean; connect() creates it
    repo = JsonRepository(path)
    await repo.connect()
    await seed_everything(repo)
    return repo, path


def _new_sqlite_url():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    return f"sqlite+aiosqlite:///{path}", path


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
async def json_source():
    repo, path = await _seeded_json_source()
    try:
        yield repo
    finally:
        await repo.close()
        if os.path.exists(path):
            os.remove(path)


@pytest.fixture
async def sqlite_dest():
    url, path = _new_sqlite_url()
    repo = SqlRepository(url)
    await repo.connect()
    try:
        yield repo
    finally:
        await repo.close()
        if os.path.exists(path):
            os.remove(path)


@pytest.fixture
async def postgres_dest():
    from n3x_bot.storage import schema as sc
    repo = SqlRepository(_PG)
    await repo.connect()
    async with repo.engine.begin() as conn:
        await conn.run_sync(sc.metadata.drop_all)
        await conn.run_sync(sc.metadata.create_all)
    try:
        yield repo
    finally:
        await repo.close()


# ── migrate(source_repo, dest_repo) ─────────────────────────────────────────

async def test_migrate_flatfile_to_sqlite_preserves_message_link(json_source, sqlite_dest):
    from n3x_bot.migrate import migrate
    await migrate(json_source, sqlite_dest)
    greet = next(m for m in await sqlite_dest.list_messages(include_archived=True)
                 if m.name == "greet")
    assert (await sqlite_dest.get_stat("tit")).message_id == greet.id


async def test_migrate_flatfile_to_sqlite_preserves_stat_flags(json_source, sqlite_dest):
    from n3x_bot.migrate import migrate
    await migrate(json_source, sqlite_dest)
    assert (await sqlite_dest.get_stat("smart")).targeted is True
    dead = next(s for s in await sqlite_dest.list_stats(include_archived=True)
                if s.key == "dead")
    assert dead.archived_at is not None


async def test_migrate_flatfile_to_sqlite_preserves_user_counts(json_source, sqlite_dest):
    from n3x_bot.migrate import migrate
    await migrate(json_source, sqlite_dest)
    assert await sqlite_dest.get_user_stats(1001) == {"tit": 2}
    assert await sqlite_dest.get_user_stats(1002) == {"tit": 1}
    assert await sqlite_dest.get_total("tit") == 3


async def test_migrate_flatfile_to_sqlite_preserves_target_stats(json_source, sqlite_dest):
    from n3x_bot.migrate import migrate
    await migrate(json_source, sqlite_dest)
    assert await sqlite_dest.get_target_total(2001, "smart") == 2
    assert await sqlite_dest.get_target_total(2002, "smart") == 1


async def test_migrate_flatfile_to_sqlite_preserves_gate_entries(json_source, sqlite_dest):
    from n3x_bot.migrate import migrate
    await migrate(json_source, sqlite_dest)
    assert await sqlite_dest.list_gate_costs("a") == [46000, 48000]
    assert (await sqlite_dest.gate_totals())["a"]["count"] == 2


async def test_migrate_flatfile_to_sqlite_full_snapshot_equal(json_source, sqlite_dest):
    # KEY property: read-back equals the source across ALL tables.
    from n3x_bot.migrate import migrate
    before = await json_source.export_all()
    await migrate(json_source, sqlite_dest)
    assert await sqlite_dest.export_all() == before


@pytest.mark.skipif(not _PG, reason="TEST_POSTGRES_URL not set")
async def test_migrate_flatfile_to_postgres_full_snapshot_equal(json_source, postgres_dest):
    from n3x_bot.migrate import migrate
    before = await json_source.export_all()
    await migrate(json_source, postgres_dest)
    assert await postgres_dest.export_all() == before


# ── run_migration(...) — the CLI-wrapped callable ───────────────────────────

async def test_run_migration_flatfile_to_sqlite_copies_all_data():
    from n3x_bot.migrate import run_migration
    src_repo, src_path = await _seeded_json_source()
    await src_repo.close()
    url, db_path = _new_sqlite_url()
    try:
        await run_migration(from_backend="flatfile", from_location=src_path,
                            to_backend="sqlite", to_location=url)
        dest = SqlRepository(url)
        await dest.connect()
        try:
            assert await dest.get_total("tit") == 3
            assert await dest.get_user_stats(1001) == {"tit": 2}
            assert (await dest.get_stat("smart")).targeted is True
        finally:
            await dest.close()
    finally:
        for p in (src_path, db_path):
            if os.path.exists(p):
                os.remove(p)


async def test_run_migration_refuses_nonempty_dest_without_overwrite():
    from n3x_bot.migrate import run_migration, DestinationNotEmptyError
    src_repo, src_path = await _seeded_json_source()
    await src_repo.close()
    url, db_path = _new_sqlite_url()
    pre = SqlRepository(url)
    await pre.connect()
    await pre.create_stat("existing", "Existing")
    await pre.close()
    try:
        with pytest.raises(DestinationNotEmptyError):
            await run_migration(from_backend="flatfile", from_location=src_path,
                                to_backend="sqlite", to_location=url,
                                overwrite=False)
    finally:
        for p in (src_path, db_path):
            if os.path.exists(p):
                os.remove(p)


async def test_run_migration_overwrite_replaces_nonempty_dest():
    from n3x_bot.migrate import run_migration
    src_repo, src_path = await _seeded_json_source()
    await src_repo.close()
    url, db_path = _new_sqlite_url()
    pre = SqlRepository(url)
    await pre.connect()
    await pre.create_stat("existing", "Existing")
    await pre.close()
    try:
        await run_migration(from_backend="flatfile", from_location=src_path,
                            to_backend="sqlite", to_location=url, overwrite=True)
        dest = SqlRepository(url)
        await dest.connect()
        try:
            assert await dest.get_stat("existing") is None  # old data cleared
            assert await dest.get_total("tit") == 3          # source data present
        finally:
            await dest.close()
    finally:
        for p in (src_path, db_path):
            if os.path.exists(p):
                os.remove(p)


def test_migrate_module_exposes_cli_entrypoint():
    import n3x_bot.migrate as m
    assert callable(m.main)
    assert callable(m.run_migration)
    assert callable(m.migrate)
