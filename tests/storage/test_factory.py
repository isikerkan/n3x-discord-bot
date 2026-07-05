from n3x_bot.config import Settings
from n3x_bot.storage.factory import create_repository
from n3x_bot.storage.json_repo import JsonRepository
from n3x_bot.storage.sql_repo import SqlRepository

BASE = dict(discord_token="t", target_role_id=1,
            welcome_channel_id=2, reminder_channel_id=3)


def test_flatfile_returns_json_repo():
    s = Settings(**BASE, storage_backend="flatfile", data_file="x.json")
    assert isinstance(create_repository(s), JsonRepository)


def test_sqlite_returns_sql_repo():
    s = Settings(**BASE, storage_backend="sqlite",
                 database_url="sqlite+aiosqlite:///x.db")
    assert isinstance(create_repository(s), SqlRepository)


def test_postgres_returns_sql_repo():
    s = Settings(**BASE, storage_backend="postgres",
                 database_url="postgresql+asyncpg://u:p@h/d")
    assert isinstance(create_repository(s), SqlRepository)
