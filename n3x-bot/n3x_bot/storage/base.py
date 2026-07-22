from abc import ABC, abstractmethod
from datetime import datetime
from n3x_bot.models import User, Stat, Message

GATE_TYPES: tuple[str, ...] = ("a", "b", "c", "d", "e", "z", "k")


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

    # channel messages
    @abstractmethod
    async def set_channel_message(self, key: str, message_id: int,
                                  channel_id: int) -> None:
        """Upsert the (message_id, channel_id) tracked under `key`.

        A NON-FK keyed store for live single-message embeds whose key is not a
        real `stats` row (e.g. "gate_stats"), so `set_last_post` can't hold it.
        """
        ...
    @abstractmethod
    async def get_channel_message(self, key: str) -> tuple[int, int] | None:
        """`(message_id, channel_id)` for `key`, or None if unset. Both ints."""
        ...

    # gate pending
    @abstractmethod
    async def set_gate_pending(self, message_id: int, *, channel_id: int,
                               gate_type: str, cost: int, user_id: int,
                               username: str, options: dict) -> None:
        """Upsert the in-flight drop-confirm pending state for `message_id`.

        Persists the d/e/z/k pending entry that otherwise lives only in the
        in-memory `bot._pending_gate` dict, so a restart doesn't drop an
        in-flight confirmation. `options` is the emoji_key -> item map (values
        `str`, or `None` for the ❌ "nothing" choice).
        """
        ...
    @abstractmethod
    async def get_gate_pending(self, message_id: int) -> dict | None:
        """The 7-key pending dict for `message_id`, or None if absent.

        Keys: message_id/channel_id/cost/user_id (ints), gate_type/username
        (str), options (dict with str keys and `str | None` values).
        """
        ...
    @abstractmethod
    async def delete_gate_pending(self, message_id: int) -> bool:
        """Delete the pending row for `message_id`; True if a row existed."""
        ...
    @abstractmethod
    async def all_gate_pending(self) -> list[dict]:
        """All pending rows as a list of 7-key dicts."""
        ...

    # runtime config
    @abstractmethod
    async def set_runtime_config(self, key: str, value: str) -> None:
        """Upsert the raw override string stored under `key`."""
        ...
    @abstractmethod
    async def get_runtime_config(self, key: str) -> str | None:
        """The raw override string for `key`, or None if unset."""
        ...
    @abstractmethod
    async def delete_runtime_config(self, key: str) -> bool:
        """Delete the override for `key`; True if a row existed."""
        ...
    @abstractmethod
    async def all_runtime_config(self) -> dict[str, str]:
        """All overrides as `{key: value}`."""
        ...

    # content texts
    @abstractmethod
    async def set_content_text(self, key: str, value: str) -> None:
        """Upsert the raw narrative-copy string stored under `key`."""
        ...
    @abstractmethod
    async def get_content_text(self, key: str) -> str | None:
        """The raw copy string for `key`, or None if unset."""
        ...
    @abstractmethod
    async def delete_content_text(self, key: str) -> bool:
        """Delete the override for `key`; True if a row existed."""
        ...
    @abstractmethod
    async def all_content_texts(self) -> dict[str, str]:
        """All content overrides as `{key: value}`."""
        ...

    # color config
    @abstractmethod
    async def set_color_config(self, key: str, value: str) -> None:
        """Upsert the raw colour-override string stored under `key`."""
        ...
    @abstractmethod
    async def get_color_config(self, key: str) -> str | None:
        """The raw colour string for `key`, or None if unset."""
        ...
    @abstractmethod
    async def delete_color_config(self, key: str) -> bool:
        """Delete the override for `key`; True if a row existed."""
        ...
    @abstractmethod
    async def all_color_config(self) -> dict[str, str]:
        """All colour overrides as `{key: value}`."""
        ...

    # achievement definitions
    @abstractmethod
    async def set_achievement_def(self, id: str, *, category: str, metric: str,
                                  threshold: int, title: str, secret: bool,
                                  color: str | None = None) -> None:
        """Upsert the achievement definition stored under `id`."""
        ...
    @abstractmethod
    async def get_achievement_def(self, id: str) -> dict | None:
        """The 7-key definition dict for `id`, or None if absent."""
        ...
    @abstractmethod
    async def delete_achievement_def(self, id: str) -> bool:
        """Delete the definition for `id`; True if a row existed."""
        ...
    @abstractmethod
    async def all_achievement_defs(self) -> list[dict]:
        """All definitions as a list of 7-key dicts, ordered by id."""
        ...
    @abstractmethod
    async def replace_achievement_defs(self, defs: list[dict]) -> None:
        """Atomically replace ALL definitions with `defs`.

        A single all-or-nothing write: the existing rows are dropped and `defs`
        inserted in one transaction, so a mid-write failure never leaves a
        partial table (which the total-replacement resolver would surface as
        silently missing achievements). Each dict carries the 7 keys
        id/category/metric/threshold/title/secret/color (`color` optional,
        defaults to None). Passing `[]` atomically wipes the table.
        """
        ...

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
                             laser_dropped: bool | None = None,
                             drops: dict[str, bool] | None = None) -> bool:
        """Insert a gate cost entry unless an identical (user_id, gate_type,
        cost) row was inserted within `dedup_window_seconds`. Returns True if
        inserted, False if rejected as a duplicate.

        `drops` is a per-item drop map `{item: bool}` (d->laser, e->lf4,
        z->havoc, k->hercules+lf4u; a/b/c->None). `laser_dropped` is a legacy
        compat alias for the Delta laser drop: when given (and `drops` is None)
        it is normalized to `drops={"laser": bool}`. For every d-entry both the
        `drops` map and the legacy `laser_dropped` column are populated; e/z/k
        populate `drops` only.
        """
        ...
    @abstractmethod
    async def gate_drop_stats(self, gate_type: str) -> dict:
        """`{"count": int, "avg": int, "rates": {item: float_pct}}` for
        `gate_type`. `rates[item] = 100 * (# entries with that item True) /
        count`, one key per distinct drop-item observed among that gate's
        entries. Empty gate -> count 0, avg 0, rates {}.
        """
        ...
    @abstractmethod
    async def delta_stats(self) -> dict:
        """`{"count": int, "avg": int, "laser_rate": float}` over the "d"
        (Delta) gate. `laser_rate = 100 * (# laser_dropped True) / count`,
        0 when count is 0. Delegates to `gate_drop_stats("d")`.
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
    @abstractmethod
    async def list_gate_entries(self, gate_type: str,
                                since: datetime | None = None,
                                until: datetime | None = None) -> list[dict]:
        """Timestamped gate-history read view backing the `/gate verlauf` chart.

        Returns `{"cost": int, "created_at": tz-aware datetime,
        "drops": dict[str, bool]}` for each `gate_type` entry, ordered by
        `created_at` ASCENDING. `since`/`until` are an INCLUSIVE tz-aware
        filter over `created_at`. `drops` reuses the generalized drop read
        (d->{"laser": bool}, e->{"lf4": bool}, z->{"havoc": bool},
        k->{"hercules": bool, "lf4u": bool}, a/b/c->{}). READ VIEW only:
        NOT part of export_all/import_all/clear.
        """
        ...

    # activity
    @abstractmethod
    async def add_activity(self, discord_id: int, metric: str, amount: int) -> int:
        """Increment the `metric` counter for `discord_id`, return the new total."""
        ...
    @abstractmethod
    async def set_activity(self, discord_id: int, metric: str, value: int) -> int:
        """Set the `metric` counter for `discord_id` to an ABSOLUTE value.

        Used by the history backfill, which computes true totals from Discord
        history and must be idempotent across re-runs (unlike add_activity)."""
        ...
    @abstractmethod
    async def get_activity(self, discord_id: int, metric: str) -> int: ...

    @abstractmethod
    async def list_user_gate_entries(self, discord_id: int, gate_type: str) -> list:
        """A single user's gate entries for `gate_type`, oldest first
        (``[{cost, created_at, drops}]``)."""
        ...
    @abstractmethod
    async def list_gate_entries_full(self, gate_type: str | None = None) -> list:
        """All gate entries (optionally one gate), oldest first, with the user
        (``[{gate_type, cost, user_id, username, drops, created_at}]``)."""
        ...

    # event-reminder opt-in
    @abstractmethod
    async def event_optin_set(self, discord_id: int, opted_in: bool) -> None:
        """Add or remove `discord_id` from the event-reminder opt-in list."""
        ...
    @abstractmethod
    async def event_optin_is(self, discord_id: int) -> bool: ...
    @abstractmethod
    async def event_optin_all(self) -> list: ...

    # voice sessions (durable in-progress checkpoints)
    @abstractmethod
    async def voice_session_set(self, discord_id: int, since) -> None:
        """Upsert the active session's `since` (uncredited-from) timestamp."""
        ...
    @abstractmethod
    async def voice_session_end(self, discord_id: int):
        """Remove the session; return its `since` timestamp (or None)."""
        ...
    @abstractmethod
    async def voice_sessions_all(self) -> dict:
        """All active sessions as ``{discord_id: since}``."""
        ...
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
