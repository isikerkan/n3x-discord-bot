from n3x_bot.config import (
    Settings,
    parse_allowed_maps,
    parse_gate_rewards,
    parse_reminder_hm,
    parse_voice_roles,
)

OVERRIDABLE_KEYS: frozenset[str] = frozenset({
    "welcome_channel_id", "reminder_channel_id", "gate_input_channel_id",
    "gate_stats_channel_id", "milestone_channel_id", "overview_channel_id",
    "kodex_check_channel_id", "timer_overview_channel_id",
    "timer_overview_message_id", "target_role_id", "gate_delete_role_id",
    "base_timer_role_id",
    "gate_rewards", "voice_achievement_roles", "allowed_maps", "reminder_time",
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
        if key in self._overrides:
            return int(self._overrides[key])
        return getattr(self._settings, key)

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
        v = self._overrides.get("gate_rewards")
        return parse_gate_rewards(v) if v is not None else self._settings.gate_rewards_map()

    def voice_role_map(self) -> dict[str, int]:
        v = self._overrides.get("voice_achievement_roles")
        return parse_voice_roles(v) if v is not None else self._settings.voice_role_map()

    @property
    def allowed_maps_list(self) -> list[str]:
        v = self._overrides.get("allowed_maps")
        return parse_allowed_maps(v) if v is not None else self._settings.allowed_maps_list

    def reminder_hm(self) -> tuple[int, int]:
        v = self._overrides.get("reminder_time")
        return parse_reminder_hm(v) if v is not None else self._settings.reminder_hm()

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
