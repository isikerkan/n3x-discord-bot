import asyncio
import json
import logging
import os

from n3x_bot.config import Settings
from n3x_bot.storage.factory import create_repository
from n3x_bot.seed import seed_defaults, migrate_legacy_json
from n3x_bot.legacy_migrate import run_migration_folder
from n3x_bot.bot import build_bot
from n3x_bot.singleton import kill_stale_instances

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _is_legacy_flatfile(path: str) -> bool:
    """Detect whether `path` holds the old pre-migration stats.json shape.

    Legacy files store counters as "<key>_count" fields directly at the top
    level and have no "seq" bookkeeping (which only the new repository
    format writes).
    """
    if not os.path.exists(path):
        return False
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return False
    has_legacy_counts = any(k.endswith("_count") for k in data)
    return has_legacy_counts and "seq" not in data


async def _prepare(settings: Settings):
    # The flatfile backend's live data file IS the legacy file on first run.
    # If we let connect() open it directly, JsonRepository.connect() only
    # setdefaults new-format keys onto it and the old "<key>_count" /
    # "user_stats" counters are never converted - they're silently stranded
    # and the first counter increment overwrites them. Move the legacy file
    # aside first so connect() starts fresh, then migrate from the copy.
    legacy_src = None
    if settings.storage_backend == "flatfile" and _is_legacy_flatfile(settings.data_file):
        legacy_src = settings.data_file + ".legacy"
        os.replace(settings.data_file, legacy_src)  # move aside; connect() creates fresh

    repo = create_repository(settings)
    await repo.connect()
    await seed_defaults(repo)
    if settings.storage_backend != "flatfile":
        # SQL backends read the legacy flat file directly since their live
        # store was never the legacy file to begin with.
        await migrate_legacy_json(repo, "stats.json")
    elif legacy_src is not None:
        await migrate_legacy_json(repo, legacy_src)
    summary = await run_migration_folder(repo, settings)
    if summary:
        logging.info("legacy migration folder imported: %s", summary)
    return repo


async def amain() -> None:
    # Kill any leftover instance from a botched Stop/Restart BEFORE connecting,
    # so exactly one bot holds the Discord gateway (no duplicated events).
    kill_stale_instances()
    settings = Settings()
    repo = await _prepare(settings)
    bot = build_bot(settings, repo)
    async with bot:
        await bot.start(settings.discord_token)


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
