"""Generic repo->repo data migration and its CLI wrapper."""

import argparse
import asyncio

from n3x_bot.storage.base import StatsRepository
from n3x_bot.storage.json_repo import JsonRepository
from n3x_bot.storage.sql_repo import SqlRepository


class DestinationNotEmptyError(Exception):
    """Raised when the destination already holds data and overwrite is False."""


def _build_repo(backend: str, location: str) -> StatsRepository:
    if backend == "flatfile":
        return JsonRepository(location)
    return SqlRepository(location)


# Every data-bearing table in an export_all() snapshot (excludes "seq", which
# is bookkeeping). A destination is non-empty if ANY of these is populated —
# otherwise a dest holding only activity/streak/night data would be seen as
# empty and import_all would collide on PKs / corrupt it.
_DATA_TABLES = (
    "users", "messages", "stats", "gate_entries",
    "user_stats", "stat_totals", "target_stats", "stat_last_post",
    "activity_counters", "streak_stats", "night_stats", "achievements",
    "kodex_confirmations", "kodex_messages", "base_timers",
)


def _has_data(snapshot: dict) -> bool:
    return any(snapshot.get(t) for t in _DATA_TABLES)


async def migrate(source: StatsRepository, dest: StatsRepository,
                  *, overwrite: bool = False) -> None:
    snapshot = await source.export_all()
    dest_snapshot = await dest.export_all()
    if _has_data(dest_snapshot):
        if not overwrite:
            raise DestinationNotEmptyError(
                "destination already holds data; pass overwrite=True to replace it")
        # clear() and import_all() are separate transactions, so a failed
        # import must not leave the destination wiped: snapshot the dest
        # before clearing and restore it if the import fails.
        # NOTE: this holds the whole pre-existing destination in memory; for
        # very large destinations a streaming/backup strategy would be
        # preferable (flagged for human review).
        await dest.clear()
        try:
            await dest.import_all(snapshot)
        except BaseException:
            await dest.clear()
            await dest.import_all(dest_snapshot)
            raise
    else:
        await dest.import_all(snapshot)


async def run_migration(*, from_backend: str, from_location: str,
                        to_backend: str, to_location: str,
                        overwrite: bool = False) -> None:
    source = _build_repo(from_backend, from_location)
    dest = _build_repo(to_backend, to_location)
    await source.connect()
    try:
        await dest.connect()
        try:
            await migrate(source, dest, overwrite=overwrite)
        finally:
            await dest.close()
    finally:
        await source.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate n3x-bot data between storage backends.")
    parser.add_argument("--from", dest="from_backend", required=True,
                        choices=["flatfile", "sqlite", "postgres"],
                        help="source backend")
    parser.add_argument("--to", dest="to_backend", required=True,
                        choices=["flatfile", "sqlite", "postgres"],
                        help="destination backend")
    parser.add_argument("--from-location", dest="from_location", required=True,
                        help="source data_file path (flatfile) or database_url")
    parser.add_argument("--to-location", dest="to_location", required=True,
                        help="destination data_file path (flatfile) or database_url")
    parser.add_argument("--overwrite", action="store_true",
                        help="clear a non-empty destination before migrating")
    args = parser.parse_args()
    try:
        asyncio.run(run_migration(
            from_backend=args.from_backend, from_location=args.from_location,
            to_backend=args.to_backend, to_location=args.to_location,
            overwrite=args.overwrite))
    except DestinationNotEmptyError as e:
        raise SystemExit(f"error: {e}")


if __name__ == "__main__":
    main()
