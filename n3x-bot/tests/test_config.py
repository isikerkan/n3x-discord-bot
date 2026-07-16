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


def test_dotenv_overrides_environment_variables(tmp_path, monkeypatch):
    """The managed .env must WIN over process env vars — AMP re-injects stale
    config as env and we can't stop it, so .env is the source of truth."""
    from n3x_bot.config import Settings
    envfile = tmp_path / ".env"
    envfile.write_text(
        "DISCORD_TOKEN=from_dotenv\n"
        "TARGET_ROLE_ID=1\nWELCOME_CHANNEL_ID=2\nREMINDER_CHANNEL_ID=3\n"
        "GATE_STATS_CHANNEL_ID=222\n"
        "GATE_REWARDS=a:1,b:2\n"
    )
    # AMP-style stale injection via process env:
    monkeypatch.setenv("DISCORD_TOKEN", "from_amp_env")
    monkeypatch.setenv("GATE_STATS_CHANNEL_ID", "999")
    monkeypatch.setenv("GATE_REWARDS", "a:9")
    monkeypatch.setenv("MILESTONE_CHANNEL_ID", "777")  # only in env, not .env
    s = Settings(_env_file=str(envfile))
    assert s.discord_token == "from_dotenv"          # .env beats env
    assert s.gate_stats_channel_id == 222            # .env beats env
    assert s.gate_rewards_map() == {"a": 1, "b": 2}  # .env beats env
    assert s.milestone_channel_id == 777             # env-only field still read


# ── parse_duration: seconds parser with s/m/h suffixes and combos ────────────
# Mirrors the style of parse_reminder_hm / parse_gate_rewards in config.py.
# Returns whole seconds; bad/empty input raises ValueError so the caller
# (RuntimeConfig) can fall back.


def test_parse_duration_plain_integer_is_seconds():
    from n3x_bot.config import parse_duration
    assert parse_duration("90") == 90


def test_parse_duration_seconds_suffix():
    from n3x_bot.config import parse_duration
    assert parse_duration("30s") == 30


def test_parse_duration_minutes_suffix():
    from n3x_bot.config import parse_duration
    assert parse_duration("1m") == 60
    assert parse_duration("5m") == 300


def test_parse_duration_hours_suffix():
    from n3x_bot.config import parse_duration
    assert parse_duration("2h") == 7200


def test_parse_duration_combined_minutes_and_seconds():
    from n3x_bot.config import parse_duration
    assert parse_duration("1m30s") == 90


def test_parse_duration_combined_hours_minutes_seconds():
    from n3x_bot.config import parse_duration
    assert parse_duration("1h1m1s") == 3661


@pytest.mark.parametrize("bad", ["", "   ", "abc", "1x", "m", "1m1x"])
def test_parse_duration_rejects_malformed_input(bad):
    from n3x_bot.config import parse_duration
    with pytest.raises(ValueError):
        parse_duration(bad)


def test_parse_duration_rejects_non_ascii_digits():
    # str.isdigit()/`\d` accept Unicode digits (Arabic-Indic, fullwidth); the
    # parser is ASCII-strict, so these must raise like any other junk.
    from n3x_bot.config import parse_duration
    with pytest.raises(ValueError):
        parse_duration("١٢")


# ── Settings.gate_message_delete_delay field ────────────────────────────────


def test_gate_message_delete_delay_defaults_to_one_minute():
    s = Settings(**BASE)
    assert s.gate_message_delete_delay == "1m"


def test_gate_message_delete_delay_read_from_env(monkeypatch):
    monkeypatch.setenv("GATE_MESSAGE_DELETE_DELAY", "5m")
    s = Settings(
        discord_token="tok",
        target_role_id=1,
        welcome_channel_id=2,
        reminder_channel_id=3,
        _env_file=None,
    )
    assert s.gate_message_delete_delay == "5m"


def test_env_example_documents_gate_message_delete_delay():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    text = (root / ".env.example").read_text()
    assert "GATE_MESSAGE_DELETE_DELAY" in text
