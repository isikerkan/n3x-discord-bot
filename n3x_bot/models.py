from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class User:
    id: int
    discord_id: int
    display_name: str
    archived_at: datetime | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class Message:
    id: int
    name: str
    template: str
    archived_at: datetime | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class Stat:
    id: int
    key: str
    name: str
    message_id: int | None = None
    archived_at: datetime | None = None
    created_at: datetime | None = None


def render_output(stat: Stat, message: Message | None,
                  user_display: str, count: int) -> str:
    if message is not None:
        try:
            return message.template.format(user=user_display, count=count, stat=stat.name)
        except (KeyError, IndexError, ValueError):
            pass  # bad template -> fall back to default render below
    return f"{stat.name} — {user_display} — {count}"
