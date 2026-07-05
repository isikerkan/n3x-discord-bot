import pytest
from pydantic import ValidationError
from n3x_bot.config import Settings

BASE = dict(
    discord_token="tok",
    target_role_id=1,
    welcome_channel_id=2,
    reminder_channel_id=3,
)


def test_defaults_flatfile():
    s = Settings(**BASE)
    assert s.storage_backend == "flatfile"
    assert s.data_file == "stats.json"
    assert s.command_prefix == "!"
    assert s.reminder_hm() == (19, 30)


def test_sqlite_requires_database_url():
    with pytest.raises(ValidationError):
        Settings(**BASE, storage_backend="sqlite")


def test_postgres_with_url_ok():
    s = Settings(**BASE, storage_backend="postgres",
                 database_url="postgresql+asyncpg://u:p@h/d")
    assert s.database_url.endswith("/d")


def test_reminder_hm_parses():
    s = Settings(**BASE, reminder_time="07:05")
    assert s.reminder_hm() == (7, 5)
