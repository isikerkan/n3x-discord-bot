import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import model_validator
from pydantic_settings import (BaseSettings, PydanticBaseSettingsSource,
                               SettingsConfigDict)


def parse_reminder_hm(raw: str) -> tuple[int, int]:
    hh, mm = raw.split(":")
    return int(hh), int(mm)


def parse_gate_rewards(raw: str) -> dict[str, int]:
    out = {}
    for pair in raw.split(","):
        if ":" in pair:
            k, v = pair.split(":", 1)
            out[k.strip()] = int(v)
    return out


def parse_allowed_maps(raw: str) -> list[str]:
    return [m.strip() for m in raw.split(",") if m.strip()]


def parse_role_ids(raw: str) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError:
            continue
        if value == 0 or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def parse_voice_roles(raw: str) -> dict[str, int]:
    out = {}
    for pair in raw.split(","):
        if ":" in pair:
            k, v = pair.split(":", 1)
            try:
                out[k.strip()] = int(v)
            except ValueError:
                continue
    return out


def parse_duration(raw: str) -> int:
    raw = raw.strip()
    if not raw:
        raise ValueError("empty duration")
    if re.fullmatch(r"[0-9]+", raw):
        return int(raw)
    if not re.fullmatch(r"(?:[0-9]+[hms])+", raw):
        raise ValueError(f"invalid duration: {raw!r}")
    total = 0
    for value, unit in re.findall(r"([0-9]+)([hms])", raw):
        total += int(value) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls, settings_cls,
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ):
        # The managed `.env` is the config source of truth and must WIN over
        # process environment variables. AMP re-injects its (often stale) bot
        # config as env vars on every launch and we can't reliably stop it, so
        # `.env` is placed ABOVE env here (default pydantic order is env >
        # dotenv). Order = highest priority first. Live overrides still come
        # from the runtime_config DB via the RuntimeConfig resolver (DB > .env).
        return (init_settings, dotenv_settings, env_settings,
                file_secret_settings)

    discord_token: str
    storage_backend: Literal["flatfile", "sqlite", "postgres"] = "flatfile"
    database_url: str | None = None
    data_file: str = "stats.json"
    migration_dir: str = "migration"

    target_role_id: int | str = 0
    welcome_channel_id: int
    reminder_channel_id: int
    julez_id: int = 0
    admin_role_id: int | str = 0
    timezone: str = "Europe/Berlin"

    prefix_str: str = "[N3X]"
    command_prefix: str = "!"
    reminder_time: str = "19:30"

    gate_input_channel_id: int = 0
    gate_stats_channel_id: int = 0
    gate_chart_channel_id: int = 0
    command_list_channel_id: int = 0
    gate_delete_role_id: int | str = 0
    stat_override_role_id: int | str = 0
    gate_rewards: str = "a:46892,b:93820,c:139522,d:75361,e:46719,z:66661,k:62955"
    gate_message_delete_delay: str = "1m"

    milestone_channel_id: int = 0
    overview_channel_id: int = 0
    kodex_check_channel_id: int = 0
    # Where event reminders + the opt-in signup message go. 0 falls back to
    # reminder_channel_id.
    event_reminder_channel_id: int = 0
    voice_achievement_roles: str = ""

    base_timer_role_id: int | str = 0
    timer_overview_channel_id: int = 0
    voice_log_channel_id: int = 0
    timer_overview_message_id: int = 0
    allowed_maps: str = "4-1,4-2,4-3,4-4,1-5,1-6,1-7,2-5,2-6,2-7,3-5,3-6,3-7"

    @model_validator(mode="before")
    @classmethod
    def _blank_env_to_default(cls, data):
        # AMP injects an environment variable for every GUI config field; a
        # field the operator left unset arrives as an empty string "". Drop
        # empty/whitespace-only string inputs so the field's declared default
        # applies instead of an invalid empty value that would fail validation
        # (e.g. TIMEZONE="" -> ZoneInfo("") -> crash on startup, bricking the
        # whole instance). Required fields left blank still fail (as they must).
        if isinstance(data, dict):
            return {k: v for k, v in data.items()
                    if not (isinstance(v, str) and v.strip() == "")}
        return data

    # Default sqlite location — kept under data/ so it survives AMP git-pull
    # updates (which force-reset the tracked tree but leave data/ alone).
    _DEFAULT_SQLITE_URL = "sqlite+aiosqlite:///data/n3x.db"

    @model_validator(mode="after")
    def _require_db_url(self) -> "Settings":
        if not self.database_url:
            if self.storage_backend == "sqlite":
                # Selecting sqlite without a DATABASE_URL "just works": auto-fill
                # a sensible local file URL instead of failing to start. Postgres
                # can't be guessed (host/credentials), so it stays required.
                self.database_url = self._DEFAULT_SQLITE_URL
            elif self.storage_backend == "postgres":
                raise ValueError(
                    "database_url is required for storage_backend=postgres"
                )
        return self

    @model_validator(mode="after")
    def _validate_timezone(self) -> "Settings":
        # A bad tz would otherwise only surface at runtime inside now_local()
        # (called on every message in on_message), silently bricking activity
        # tracking with a logged traceback. Fail fast at config load instead.
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"invalid timezone: {self.timezone!r}") from e
        return self

    def reminder_hm(self) -> tuple[int, int]:
        return parse_reminder_hm(self.reminder_time)

    def gate_rewards_map(self) -> dict[str, int]:
        return parse_gate_rewards(self.gate_rewards)

    @property
    def allowed_maps_list(self) -> list[str]:
        return parse_allowed_maps(self.allowed_maps)

    def voice_role_map(self) -> dict[str, int]:
        return parse_voice_roles(self.voice_achievement_roles)

    @property
    def admin_role_ids(self) -> list[int]:
        return parse_role_ids(str(self.admin_role_id))

    @property
    def target_role_ids(self) -> list[int]:
        return parse_role_ids(str(self.target_role_id))

    @property
    def gate_delete_role_ids(self) -> list[int]:
        return parse_role_ids(str(self.gate_delete_role_id))

    @property
    def base_timer_role_ids(self) -> list[int]:
        return parse_role_ids(str(self.base_timer_role_id))

    @property
    def stat_override_role_ids(self) -> list[int]:
        return parse_role_ids(str(self.stat_override_role_id))
