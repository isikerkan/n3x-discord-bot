import pytest
from pydantic import ValidationError
from n3x_bot.config import Settings

BASE = dict(
    discord_token="tok",
    target_role_id=1,
    welcome_channel_id=2,
    reminder_channel_id=3,
    _env_file=None,
    _env_prefix="NONEXISTENT_",
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


def test_admin_role_id_defaults_to_zero():
    s = Settings(**BASE)
    assert s.admin_role_id == 0


def test_admin_role_id_read_from_env(monkeypatch):
    monkeypatch.setenv("ADMIN_ROLE_ID", "778899")
    s = Settings(
        discord_token="tok",
        target_role_id=1,
        welcome_channel_id=2,
        reminder_channel_id=3,
        _env_file=None,
    )
    assert s.admin_role_id == 778899


def test_timezone_defaults_to_europe_berlin():
    s = Settings(**BASE)
    assert s.timezone == "Europe/Berlin"


def test_timezone_read_from_env(monkeypatch):
    monkeypatch.setenv("TIMEZONE", "America/New_York")
    s = Settings(
        discord_token="tok",
        target_role_id=1,
        welcome_channel_id=2,
        reminder_channel_id=3,
        _env_file=None,
    )
    assert s.timezone == "America/New_York"


def test_bad_timezone_is_rejected_at_load():
    # A typo would otherwise only blow up at runtime inside now_local(),
    # bricking every command. Fail fast at config load instead.
    with pytest.raises(ValidationError):
        Settings(**BASE, timezone="Nonsense/Foo")


def test_good_timezone_passes_validation():
    s = Settings(**BASE, timezone="America/New_York")
    assert s.timezone == "America/New_York"


# ── Kodex (rules-acceptance) config ────────────────────────────────────────

def test_kodex_check_channel_id_defaults_to_zero():
    s = Settings(**BASE)
    assert s.kodex_check_channel_id == 0


def test_kodex_check_channel_id_read_from_env(monkeypatch):
    monkeypatch.setenv("KODEX_CHECK_CHANNEL_ID", "667788")
    s = Settings(
        discord_token="tok",
        target_role_id=1,
        welcome_channel_id=2,
        reminder_channel_id=3,
        _env_file=None,
    )
    assert s.kodex_check_channel_id == 667788
