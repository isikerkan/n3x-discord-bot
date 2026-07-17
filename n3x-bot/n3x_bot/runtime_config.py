import logging

from n3x_bot.config import (
    Settings,
    parse_allowed_maps,
    parse_duration,
    parse_gate_rewards,
    parse_reminder_hm,
    parse_voice_roles,
)

log = logging.getLogger("N3X-Bot")

OVERRIDABLE_KEYS: frozenset[str] = frozenset({
    "welcome_channel_id", "reminder_channel_id", "gate_input_channel_id",
    "gate_stats_channel_id", "gate_chart_channel_id",
    "command_list_channel_id",
    "milestone_channel_id", "overview_channel_id",
    "kodex_check_channel_id", "timer_overview_channel_id",
    "timer_overview_message_id", "voice_log_channel_id", "target_role_id", "gate_delete_role_id",
    "base_timer_role_id",
    "gate_rewards", "voice_achievement_roles", "allowed_maps", "reminder_time",
    "gate_message_delete_delay",
})


class RuntimeConfig:
    """Resolves overridable config by merging `Settings` (the `.env` base) with
    per-key DB overrides. A DB override wins for OVERRIDABLE keys; every other
    field passes through to `Settings` unchanged.
    """

    def __init__(self, settings: Settings, overrides: dict[str, str] | None = None):
        self._settings = settings
        self._overrides = {k: v for k, v in (overrides or {}).items()
                           if k in OVERRIDABLE_KEYS}

    async def refresh(self, repo) -> None:
        raw = await repo.all_runtime_config()
        self._overrides = {k: v for k, v in raw.items() if k in OVERRIDABLE_KEYS}

    @classmethod
    async def load(cls, repo, settings: Settings) -> "RuntimeConfig":
        rc = cls(settings)
        await rc.refresh(repo)
        return rc

    def _int(self, key: str) -> int:
        override = self._overrides.get(key)
        if override is not None:
            try:
                return int(override)
            except (ValueError, TypeError):
                log.warning("runtime_config: malformed override %s=%r; "
                            "falling back to .env value", key, override)
        return getattr(self._settings, key)

    def _derived(self, key: str, parse, fallback):
        """Resolve a derived getter: parse the override if set, else delegate to
        Settings. A malformed override must never crash a read-site — on a parse
        failure log and fall back to the Settings value (the .env base stays
        strict at startup; only the DB-override boundary is tolerant)."""
        override = self._overrides.get(key)
        if override is None:
            return fallback()
        try:
            return parse(override)
        except (ValueError, TypeError):
            log.warning("runtime_config: malformed override %s=%r; "
                        "falling back to .env value", key, override)
            return fallback()

    # ── overridable int channel/role fields ─────────────────────────────────
    @property
    def welcome_channel_id(self) -> int:
        return self._int("welcome_channel_id")

    @property
    def reminder_channel_id(self) -> int:
        return self._int("reminder_channel_id")

    @property
    def gate_input_channel_id(self) -> int:
        return self._int("gate_input_channel_id")

    @property
    def gate_stats_channel_id(self) -> int:
        return self._int("gate_stats_channel_id")

    @property
    def gate_chart_channel_id(self) -> int:
        return self._int("gate_chart_channel_id")

    @property
    def command_list_channel_id(self) -> int:
        return self._int("command_list_channel_id")

    @property
    def milestone_channel_id(self) -> int:
        return self._int("milestone_channel_id")

    @property
    def overview_channel_id(self) -> int:
        return self._int("overview_channel_id")

    @property
    def kodex_check_channel_id(self) -> int:
        return self._int("kodex_check_channel_id")

    @property
    def timer_overview_channel_id(self) -> int:
        return self._int("timer_overview_channel_id")

    @property
    def timer_overview_message_id(self) -> int:
        return self._int("timer_overview_message_id")

    @property
    def voice_log_channel_id(self) -> int:
        return self._int("voice_log_channel_id")

    @property
    def target_role_id(self) -> int:
        return self._int("target_role_id")

    @property
    def gate_delete_role_id(self) -> int:
        return self._int("gate_delete_role_id")

    @property
    def base_timer_role_id(self) -> int:
        return self._int("base_timer_role_id")

    # ── overridable derived getters (parse override, else delegate) ──────────
    def gate_rewards_map(self) -> dict[str, int]:
        return self._derived("gate_rewards", parse_gate_rewards,
                             self._settings.gate_rewards_map)

    def voice_role_map(self) -> dict[str, int]:
        return self._derived("voice_achievement_roles", parse_voice_roles,
                             self._settings.voice_role_map)

    @property
    def allowed_maps_list(self) -> list[str]:
        return self._derived("allowed_maps", parse_allowed_maps,
                             lambda: self._settings.allowed_maps_list)

    def reminder_hm(self) -> tuple[int, int]:
        return self._derived("reminder_time", parse_reminder_hm,
                             self._settings.reminder_hm)

    @property
    def gate_delete_delay_seconds(self) -> int:
        # This resolves on the hot gate-store path; a malformed DB override OR a
        # malformed .env base must never raise there. `_derived` already tolerates
        # a bad override, but its fallback would still raise on a malformed base,
        # so the whole body is guarded to a safe 60s default.
        try:
            return self._derived(
                "gate_message_delete_delay", parse_duration,
                lambda: parse_duration(self._settings.gate_message_delete_delay))
        except Exception:
            return 60

    # ── non-overridable pass-through (never consults the cache) ──────────────
    @property
    def admin_role_id(self) -> int:
        return self._settings.admin_role_id

    @property
    def command_prefix(self) -> str:
        return self._settings.command_prefix

    @property
    def prefix_str(self) -> str:
        return self._settings.prefix_str

    @property
    def timezone(self) -> str:
        return self._settings.timezone

    @property
    def julez_id(self) -> int:
        return self._settings.julez_id

    @property
    def discord_token(self) -> str:
        return self._settings.discord_token

    @property
    def storage_backend(self) -> str:
        return self._settings.storage_backend
