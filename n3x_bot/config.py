from typing import Literal
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

    prefix_str: str = "[N3X]"
    command_prefix: str = "!"
    reminder_time: str = "19:30"

    @model_validator(mode="after")
    def _require_db_url(self) -> "Settings":
        if self.storage_backend in ("sqlite", "postgres") and not self.database_url:
            raise ValueError(
                f"database_url is required for storage_backend={self.storage_backend}"
            )
        return self

    def reminder_hm(self) -> tuple[int, int]:
        hh, mm = self.reminder_time.split(":")
        return int(hh), int(mm)
