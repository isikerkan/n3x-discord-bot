from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    discord_token: str
    storage_backend: Literal["flatfile", "sqlite", "postgres"] = "flatfile"
    database_url: str | None = None
    data_file: str = "stats.json"

    target_role_id: int
    welcome_channel_id: int
    reminder_channel_id: int
    julez_id: int = 0
    admin_role_id: int = 0
    timezone: str = "Europe/Berlin"

    prefix_str: str = "[N3X]"
    command_prefix: str = "!"
    reminder_time: str = "19:30"

    gate_input_channel_id: int = 0
    gate_stats_channel_id: int = 0
    gate_delete_role_id: int = 0
    gate_rewards: str = "a:46892,b:93820,c:139522"

    @model_validator(mode="after")
    def _require_db_url(self) -> "Settings":
        if self.storage_backend in ("sqlite", "postgres") and not self.database_url:
            raise ValueError(
                f"database_url is required for storage_backend={self.storage_backend}"
            )
        return self

    @model_validator(mode="after")
    def _validate_timezone(self) -> "Settings":
        # A bad tz would otherwise only surface at runtime inside now_local()
        # (called before process_commands in on_message), silently bricking all
        # commands with a logged traceback. Fail fast at config load instead.
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"invalid timezone: {self.timezone!r}") from e
        return self

    def reminder_hm(self) -> tuple[int, int]:
        hh, mm = self.reminder_time.split(":")
        return int(hh), int(mm)

    def gate_rewards_map(self) -> dict[str, int]:
        out = {}
        for pair in self.gate_rewards.split(","):
            if ":" in pair:
                k, v = pair.split(":", 1)
                out[k.strip()] = int(v)
        return out
