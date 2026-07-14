from abc import ABC, abstractmethod
from datetime import datetime
from n3x_bot.models import User, Stat, Message

GATE_TYPES: tuple[str, ...] = ("a", "b", "c", "d")


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
    async def unarchive_stat(self, key: str) -> None:
        """Clear `archived_at` for `key`, reactivating an archived stat.

        Raises KeyError for an unknown key.
        """
        ...
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
                             username: str, dedup_window_seconds: int = 30,
                             laser_dropped: bool | None = None) -> bool:
        """Insert a gate cost entry unless an identical (user_id, gate_type,
        cost) row was inserted within `dedup_window_seconds`. Returns True if
        inserted, False if rejected as a duplicate.

        `laser_dropped` is only meaningful for `gate_type == "d"`; a/b/c
        entries store it as None.
        """
        ...
    @abstractmethod
    async def delta_stats(self) -> dict:
        """`{"count": int, "avg": int, "laser_rate": float}` over the "d"
        (Delta) gate. `laser_rate = 100 * (# laser_dropped True) / count`,
        0 when count is 0.
        """
        ...
    @abstractmethod
    async def gate_record(self, gate_type: str) -> dict | None:
        """`{"min_cost", "min_user", "max_cost", "max_user"}` for `gate_type`,
        computed ON-DEMAND from `gate_entries`; `min_user`/`max_user` are int
        discord user ids. None when the gate type has no entries.
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
    @abstractmethod
    async def user_gate_counts(self, discord_id: int) -> dict[str, int]:
        """`{gate_type: count}` over that user's `gate_entries` rows; only
        gate types the user actually has appear.
        """
        ...
    @abstractmethod
    async def user_gate_cost_total(self, discord_id: int) -> int:
        """Sum of `cost` over that user's `gate_entries` rows (0 if none)."""
        ...

    # activity
    @abstractmethod
    async def add_activity(self, discord_id: int, metric: str, amount: int) -> int:
        """Increment the `metric` counter for `discord_id`, return the new total."""
        ...
    @abstractmethod
    async def get_activity(self, discord_id: int, metric: str) -> int: ...
    @abstractmethod
    async def get_streak(self, discord_id: int) -> dict | None: ...
    @abstractmethod
    async def set_streak(self, discord_id: int, current_streak: int,
                         last_active_date: str, max_streak: int) -> None: ...
    @abstractmethod
    async def get_night(self, discord_id: int) -> dict | None: ...
    @abstractmethod
    async def set_night(self, discord_id: int, night_count: int,
                        last_night_date: str) -> None: ...

    # achievements
    @abstractmethod
    async def unlock_achievement(self, discord_id: int, achievement_id: str) -> bool:
        """Insert `(discord_id, achievement_id)`; return True if newly
        inserted, False if the row already existed.
        """
        ...
    @abstractmethod
    async def has_achievement(self, discord_id: int, achievement_id: str) -> bool: ...
    @abstractmethod
    async def get_user_achievements(self, discord_id: int) -> set[str]: ...
    @abstractmethod
    async def list_achievement_holders(self) -> dict[int, set[str]]:
        """Every discord_id with >=1 unlock -> its set of achievement ids."""
        ...

    # kodex
    @abstractmethod
    async def confirm_kodex(self, discord_id: int) -> None:
        """Mark `discord_id` as having confirmed the Kodex (idempotent
        insert-or-ignore).
        """
        ...
    @abstractmethod
    async def has_confirmed_kodex(self, discord_id: int) -> bool: ...
    @abstractmethod
    async def list_kodex_confirmed(self) -> set[int]: ...
    @abstractmethod
    async def save_kodex_message(self, message_id: int, discord_id: int) -> None: ...
    @abstractmethod
    async def get_kodex_message_user(self, message_id: int) -> int | None: ...

    # base timers
    @abstractmethod
    async def set_base_timer(self, map_name: str, end_time: datetime) -> None:
        """Upsert the end_time for `map_name` (tz-aware)."""
        ...
    @abstractmethod
    async def remove_base_timer(self, map_name: str) -> bool:
        """Delete the timer for `map_name`; True if a row existed."""
        ...
    @abstractmethod
    async def list_base_timers(self) -> dict[str, datetime]:
        """All timers as `{map_name: end_time}` (end_time tz-aware)."""
        ...
    @abstractmethod
    async def purge_expired_base_timers(self, now: datetime) -> list[str]:
        """Delete timers with `end_time <= now`; return the removed map names."""
        ...

    # bulk export / import
    @abstractmethod
    async def export_all(self) -> dict:
        """JSON-serializable snapshot of ALL tables."""
        ...
    @abstractmethod
    async def import_all(self, snapshot: dict) -> None:
        """Populate an EMPTY repo from a snapshot produced by `export_all`."""
        ...
    @abstractmethod
    async def clear(self) -> None:
        """Remove all data from every table."""
        ...
