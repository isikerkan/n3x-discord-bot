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


def test_sqlite_without_url_auto_derives_default():
    # Selecting sqlite without a DATABASE_URL must not crash — it auto-fills a
    # sensible local file URL under data/ (survives AMP updates).
    s = Settings(**BASE, storage_backend="sqlite")
    assert s.database_url == "sqlite+aiosqlite:///data/n3x.db"


def test_sqlite_respects_explicit_url():
    s = Settings(**BASE, storage_backend="sqlite",
                 database_url="sqlite+aiosqlite:///data/custom.db")
    assert s.database_url.endswith("custom.db")


def test_postgres_requires_database_url():
    with pytest.raises(ValidationError):
        Settings(**BASE, storage_backend="postgres")


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


# ── Gate rewards: Epsilon / Zeta / Kappa now carry rewards ──────────────────


def test_gate_rewards_default_includes_ezk():
    s = Settings(**BASE)
    assert s.gate_rewards == (
        "a:46892,b:93820,c:139522,d:75361,e:46719,z:66661,k:62955"
    )


def test_gate_rewards_map_includes_epsilon_value():
    s = Settings(**BASE)
    assert s.gate_rewards_map()["e"] == 46719


def test_gate_rewards_map_includes_zeta_value():
    s = Settings(**BASE)
    assert s.gate_rewards_map()["z"] == 66661


def test_gate_rewards_map_includes_kappa_value():
    s = Settings(**BASE)
    assert s.gate_rewards_map()["k"] == 62955


def test_gate_rewards_map_keeps_existing_abcd_unchanged():
    # Adding e/z/k must not disturb the already-shipped a/b/c/d rewards.
    s = Settings(**BASE)
    m = s.gate_rewards_map()
    assert m["a"] == 46892
    assert m["b"] == 93820
    assert m["c"] == 139522
    assert m["d"] == 75361


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


# ── Base-timer config (v3 port #6) ─────────────────────────────────────────

DEFAULT_ALLOWED_MAPS = [
    "4-1", "4-2", "4-3", "4-4",
    "1-5", "1-6", "1-7",
    "2-5", "2-6", "2-7",
    "3-5", "3-6", "3-7",
]


def test_base_timer_role_id_defaults_to_zero():
    s = Settings(**BASE)
    assert s.base_timer_role_id == 0


def test_base_timer_role_id_read_from_env(monkeypatch):
    monkeypatch.setenv("BASE_TIMER_ROLE_ID", "1525938679581900930")
    s = Settings(
        discord_token="tok",
        target_role_id=1,
        welcome_channel_id=2,
        reminder_channel_id=3,
        _env_file=None,
    )
    assert s.base_timer_role_id == 1525938679581900930


def test_timer_overview_channel_id_defaults_to_zero():
    s = Settings(**BASE)
    assert s.timer_overview_channel_id == 0


def test_timer_overview_channel_id_read_from_env(monkeypatch):
    monkeypatch.setenv("TIMER_OVERVIEW_CHANNEL_ID", "112233")
    s = Settings(
        discord_token="tok",
        target_role_id=1,
        welcome_channel_id=2,
        reminder_channel_id=3,
        _env_file=None,
    )
    assert s.timer_overview_channel_id == 112233


def test_timer_overview_message_id_defaults_to_zero():
    s = Settings(**BASE)
    assert s.timer_overview_message_id == 0


def test_timer_overview_message_id_read_from_env(monkeypatch):
    monkeypatch.setenv("TIMER_OVERVIEW_MESSAGE_ID", "445566")
    s = Settings(
        discord_token="tok",
        target_role_id=1,
        welcome_channel_id=2,
        reminder_channel_id=3,
        _env_file=None,
    )
    assert s.timer_overview_message_id == 445566


def test_allowed_maps_defaults_to_the_v3_map_list():
    s = Settings(**BASE)
    assert s.allowed_maps == "4-1,4-2,4-3,4-4,1-5,1-6,1-7,2-5,2-6,2-7,3-5,3-6,3-7"


def test_allowed_maps_list_parses_default_into_thirteen_maps():
    s = Settings(**BASE)
    assert s.allowed_maps_list == DEFAULT_ALLOWED_MAPS


def test_allowed_maps_list_strips_whitespace_and_drops_empties():
    s = Settings(**BASE, allowed_maps=" 4-1 , 4-2 ,, 4-3 ,")
    assert s.allowed_maps_list == ["4-1", "4-2", "4-3"]


def test_allowed_maps_read_from_env(monkeypatch):
    monkeypatch.setenv("ALLOWED_MAPS", "4-1,1-5")
    s = Settings(
        discord_token="tok",
        target_role_id=1,
        welcome_channel_id=2,
        reminder_channel_id=3,
        _env_file=None,
    )
    assert s.allowed_maps_list == ["4-1", "1-5"]


def test_blank_env_string_falls_back_to_default(monkeypatch):
    """AMP injects an env var for every GUI field; an unset field arrives as
    "". Empty strings must fall back to the field default, not crash (a bad
    TIMEZONE="" previously bricked startup via ZoneInfo(""))."""
    from n3x_bot.config import Settings
    for k in ("DISCORD_TOKEN", "TARGET_ROLE_ID", "WELCOME_CHANNEL_ID",
              "REMINDER_CHANNEL_ID", "TIMEZONE", "ALLOWED_MAPS",
              "TIMER_OVERVIEW_MESSAGE_ID", "GATE_REWARDS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("TARGET_ROLE_ID", "1")
    monkeypatch.setenv("WELCOME_CHANNEL_ID", "2")
    monkeypatch.setenv("REMINDER_CHANNEL_ID", "3")
    # unset GUI fields, injected empty by AMP:
    monkeypatch.setenv("TIMEZONE", "")
    monkeypatch.setenv("ALLOWED_MAPS", "")
    monkeypatch.setenv("TIMER_OVERVIEW_MESSAGE_ID", "")
    monkeypatch.setenv("GATE_REWARDS", "")
    s = Settings(_env_file=None)
    assert s.timezone == "Europe/Berlin"
    assert s.timer_overview_message_id == 0
    assert s.allowed_maps.startswith("4-1")
    assert s.gate_rewards.startswith("a:")
