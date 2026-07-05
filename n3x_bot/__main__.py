import asyncio
import logging

from n3x_bot.config import Settings
from n3x_bot.storage.factory import create_repository
from n3x_bot.seed import seed_defaults, migrate_legacy_json
from n3x_bot.bot import build_bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


async def _prepare(settings: Settings):
    repo = create_repository(settings)
    await repo.connect()
    await seed_defaults(repo)
    # Only the SQL backends need to import the legacy flat file; the flatfile
    # backend already reads stats.json natively.
    if settings.storage_backend != "flatfile":
        await migrate_legacy_json(repo, "stats.json")
    return repo


def main() -> None:
    settings = Settings()
    repo = asyncio.run(_prepare(settings))
    bot = build_bot(settings, repo)
    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()
