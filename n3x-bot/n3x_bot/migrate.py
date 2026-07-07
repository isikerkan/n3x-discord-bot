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


def _has_data(snapshot: dict) -> bool:
    return bool(snapshot["users"] or snapshot["messages"]
                or snapshot["stats"] or snapshot["gate_entries"])


async def migrate(source: StatsRepository, dest: StatsRepository,
                  *, overwrite: bool = False) -> None:
    snapshot = await source.export_all()
    if _has_data(await dest.export_all()):
        if not overwrite:
            raise DestinationNotEmptyError(
                "destination already holds data; pass overwrite=True to replace it")
        await dest.clear()
    await dest.import_all(snapshot)


async def run_migration(*, from_backend: str, from_location: str,
                        to_backend: str, to_location: str,
                        overwrite: bool = False) -> None:
    source = _build_repo(from_backend, from_location)
    dest = _build_repo(to_backend, to_location)
    await source.connect()
    await dest.connect()
    try:
        await migrate(source, dest, overwrite=overwrite)
    finally:
        await source.close()
        await dest.close()


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
    asyncio.run(run_migration(
        from_backend=args.from_backend, from_location=args.from_location,
        to_backend=args.to_backend, to_location=args.to_location,
        overwrite=args.overwrite))


if __name__ == "__main__":
    main()
