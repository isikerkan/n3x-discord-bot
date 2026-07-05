from abc import ABC, abstractmethod
from n3x_bot.models import User, Stat, Message

GATE_TYPES: tuple[str, ...] = ("a", "b", "c")


class StatsRepository(ABC):
    @abstractmethod
    async def connect(self) -> None: ...
    @abstractmethod
    async def close(self) -> None: ...

    # messages
    @abstractmethod
    async def create_message(self, name: str, template: str) -> Message: ...
    @abstractmethod
    async def get_message(self, message_id: int) -> Message | None: ...
    @abstractmethod
    async def list_messages(self, include_archived: bool = False) -> list[Message]: ...
    @abstractmethod
    async def update_message(self, message_id: int, name: str | None = None,
                             template: str | None = None) -> Message: ...
    @abstractmethod
    async def archive_message(self, message_id: int) -> None: ...
    @abstractmethod
    async def delete_message(self, message_id: int) -> None: ...

    # stats
    @abstractmethod
    async def create_stat(self, key: str, name: str,
                          message_id: int | None = None,
                          targeted: bool = False) -> Stat: ...
    @abstractmethod
    async def get_stat(self, key: str) -> Stat | None: ...
    @abstractmethod
    async def list_stats(self, include_archived: bool = False) -> list[Stat]: ...
    @abstractmethod
    async def update_stat(self, key: str, name: str | None = None) -> Stat: ...
    @abstractmethod
    async def set_stat_message(self, key: str, message_id: int | None) -> Stat: ...
    @abstractmethod
    async def archive_stat(self, key: str) -> None: ...
    @abstractmethod
    async def delete_stat(self, key: str) -> None: ...

    # users
    @abstractmethod
    async def upsert_user(self, discord_id: int, display_name: str) -> User:
        """Create or update a user row for `discord_id`.

        For a new user, creates the row (archived_at=None). For an existing
        user, updates `display_name` AND clears `archived_at` (an active/
        present member is never archived) — so a rejoin auto-unarchives.
        """
        ...
    @abstractmethod
    async def get_user(self, discord_id: int) -> User | None: ...
    @abstractmethod
    async def list_users(self, include_archived: bool = False) -> list[User]: ...
    @abstractmethod
    async def archive_user(self, discord_id: int) -> None: ...
    @abstractmethod
    async def delete_user(self, discord_id: int) -> None: ...

    # tracking
    @abstractmethod
    async def record_use(self, discord_id: int, display_name: str,
                         stat_key: str) -> tuple[int, int]: ...
    @abstractmethod
    async def get_user_stats(self, discord_id: int) -> dict[str, int]: ...
    @abstractmethod
    async def get_total(self, stat_key: str) -> int: ...
    @abstractmethod
    async def get_last_post(self, stat_key: str) -> tuple[int, int] | None: ...
    @abstractmethod
    async def set_last_post(self, stat_key: str, discord_message_id: int,
                            channel_id: int) -> None: ...

    # target tracking
    @abstractmethod
    async def record_target_use(self, target_discord_id: int, stat_key: str) -> int:
        """Increment the per-target counter for `stat_key`, return the new count.

        Raises KeyError for an unknown stat. This only touches the target
        counter — the invoker's own `user_stats` is updated separately via
        `record_use`.
        """
        ...
    @abstractmethod
    async def get_target_total(self, target_discord_id: int, stat_key: str) -> int: ...

    # gate tracker
    @abstractmethod
    async def add_gate_entry(self, gate_type: str, cost: int, user_id: int,
                             username: str, dedup_window_seconds: int = 30) -> bool:
        """Insert a gate cost entry unless an identical (user_id, gate_type,
        cost) row was inserted within `dedup_window_seconds`. Returns True if
        inserted, False if rejected as a duplicate.
        """
        ...
    @abstractmethod
    async def list_gate_costs(self, gate_type: str) -> list[int]:
        """Costs for `gate_type`, ordered by insertion order."""
        ...
    @abstractmethod
    async def delete_gate_entry(self, gate_type: str, index: int) -> bool:
        """Delete the 1-based `index`-th entry (insertion order) for
        `gate_type`. Returns True if deleted, False if out of range.
        """
        ...
    @abstractmethod
    async def gate_totals(self) -> dict[str, dict]:
        """`{gate_type: {"count": int, "avg": int}}` for every gate type
        that has at least one entry.
        """
        ...
