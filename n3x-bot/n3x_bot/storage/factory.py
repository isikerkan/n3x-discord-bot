from n3x_bot.config import Settings
from n3x_bot.storage.base import StatsRepository
from n3x_bot.storage.json_repo import JsonRepository
from n3x_bot.storage.sql_repo import SqlRepository


def create_repository(settings: Settings) -> StatsRepository:
    if settings.storage_backend == "flatfile":
        return JsonRepository(settings.data_file)
    return SqlRepository(settings.database_url)
