"""Phase 1 resolver tests: `n3x_bot.runtime_config.RuntimeConfig`.

The resolver merges `Settings` (the `.env` base for ALL config) with a cached
dict of per-key overrides loaded from the `runtime_config` DB table. DB wins
when a key is set; otherwise the Settings value is returned. Only the
OVERRIDABLE keys may be overridden — an override stored under a non-overridable
key (e.g. `admin_role_id`, which gates the config commands and must stay
env-authoritative to avoid lockout) is IGNORED.

Pinned API (to be implemented downstream in `n3x_bot/runtime_config.py`):

    RuntimeConfig(settings: Settings, overrides: dict[str, str] | None = None)
    async RuntimeConfig.refresh(repo) -> None        # reload cache from repo.all_runtime_config()
    async classmethod RuntimeConfig.load(repo, settings) -> RuntimeConfig
    OVERRIDABLE_KEYS: collection[str]                 # module-level

    # int channel/role props mirroring Settings (override wins, int-coerced):
    welcome_channel_id, reminder_channel_id, gate_input_channel_id,
    gate_stats_channel_id, milestone_channel_id, overview_channel_id,
    kodex_check_channel_id, timer_overview_channel_id,
    timer_overview_message_id, target_role_id, gate_delete_role_id,
    base_timer_role_id

    # derived getters mirroring Settings (parse the override string if set):
    gate_rewards_map() -> dict[str, int]
    voice_role_map() -> dict[str, int]
    allowed_maps_list  (property) -> list[str]
    reminder_hm() -> tuple[int, int]

    # non-overridable pass-through (always the Settings value, no DB lookup):
    admin_role_id, command_prefix, prefix_str, timezone, julez_id,
    discord_token, storage_backend

All imports are LAZY (inside test bodies) so the RED state is a clean per-test
ModuleNotFoundError/AttributeError rather than a collection-time error.
"""

import os
import tempfile
from types import SimpleNamespace

from n3x_bot.config import Settings

BASE_SETTINGS_KWARGS = dict(
    discord_token="tok",
    target_role_id=1,
    welcome_channel_id=2,
    reminder_channel_id=999,
    julez_id=424242,
    admin_role_id=7,
    _env_file=None,
    _env_prefix="NONEXISTENT_",
)


def _settings(**overrides) -> Settings:
    kwargs = dict(BASE_SETTINGS_KWARGS)
    kwargs.update(overrides)
    return Settings(**kwargs)


async def _flatfile_repo():
    from n3x_bot.storage.json_repo import JsonRepository
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    repo = JsonRepository(path)
    await repo.connect()
    repo._test_path = path
    return repo


# ── every overridable int channel/role field, with NO overrides, == Settings ─

_INT_FIELDS = [
    "welcome_channel_id", "reminder_channel_id", "gate_input_channel_id",
    "gate_stats_channel_id", "milestone_channel_id", "overview_channel_id",
    "kodex_check_channel_id", "timer_overview_channel_id",
    "timer_overview_message_id", "target_role_id", "gate_delete_role_id",
    "base_timer_role_id",
]


async def test_int_fields_pass_through_to_settings_when_no_overrides():
    from n3x_bot.runtime_config import RuntimeConfig
    settings = _settings(
        gate_stats_channel_id=555, milestone_channel_id=42,
        base_timer_role_id=13, timer_overview_message_id=88,
    )
    rc = RuntimeConfig(settings)
    for field in _INT_FIELDS:
        assert getattr(rc, field) == getattr(settings, field), field


# ── a DB override for one int field wins (int-coerced), others still Settings ─

async def test_int_override_wins_and_is_int_coerced():
    from n3x_bot.runtime_config import RuntimeConfig
    settings = _settings(gate_stats_channel_id=555)
    rc = RuntimeConfig(settings, {"gate_stats_channel_id": "999"})
    assert rc.gate_stats_channel_id == 999
    assert isinstance(rc.gate_stats_channel_id, int)


async def test_unset_int_fields_still_equal_settings_when_another_is_overridden():
    from n3x_bot.runtime_config import RuntimeConfig
    settings = _settings(gate_stats_channel_id=555, welcome_channel_id=222)
    rc = RuntimeConfig(settings, {"gate_stats_channel_id": "999"})
    assert rc.welcome_channel_id == 222


# ── derived getters: gate_rewards_map ────────────────────────────────────────

async def test_gate_rewards_map_no_override_equals_settings():
    from n3x_bot.runtime_config import RuntimeConfig
    settings = _settings()  # default 7-gate reward map
    rc = RuntimeConfig(settings)
    assert rc.gate_rewards_map() == settings.gate_rewards_map()
    assert len(rc.gate_rewards_map()) == 7


async def test_gate_rewards_map_override_is_parsed():
    from n3x_bot.runtime_config import RuntimeConfig
    settings = _settings()
    rc = RuntimeConfig(settings, {"gate_rewards": "a:1,b:2"})
    assert rc.gate_rewards_map() == {"a": 1, "b": 2}


# ── derived getters: voice_role_map ──────────────────────────────────────────

async def test_voice_role_map_no_override_equals_settings():
    from n3x_bot.runtime_config import RuntimeConfig
    settings = _settings(voice_achievement_roles="base:5")
    rc = RuntimeConfig(settings)
    assert rc.voice_role_map() == {"base": 5}


async def test_voice_role_map_override_is_parsed():
    from n3x_bot.runtime_config import RuntimeConfig
    settings = _settings(voice_achievement_roles="base:5")
    rc = RuntimeConfig(settings, {"voice_achievement_roles": "x:1,y:2"})
    assert rc.voice_role_map() == {"x": 1, "y": 2}


# ── derived getters: allowed_maps_list (property, mirrors Settings) ──────────

async def test_allowed_maps_list_no_override_equals_settings():
    from n3x_bot.runtime_config import RuntimeConfig
    settings = _settings()
    rc = RuntimeConfig(settings)
    assert rc.allowed_maps_list == settings.allowed_maps_list


async def test_allowed_maps_list_override_is_parsed():
    from n3x_bot.runtime_config import RuntimeConfig
    settings = _settings()
    rc = RuntimeConfig(settings, {"allowed_maps": "1-1,2-2,3-3"})
    assert rc.allowed_maps_list == ["1-1", "2-2", "3-3"]


# ── derived getters: reminder_hm ─────────────────────────────────────────────

async def test_reminder_hm_no_override_equals_settings():
    from n3x_bot.runtime_config import RuntimeConfig
    settings = _settings(reminder_time="19:30")
    rc = RuntimeConfig(settings)
    assert rc.reminder_hm() == (19, 30)


async def test_reminder_hm_override_is_parsed():
    from n3x_bot.runtime_config import RuntimeConfig
    settings = _settings(reminder_time="19:30")
    rc = RuntimeConfig(settings, {"reminder_time": "20:15"})
    assert rc.reminder_hm() == (20, 15)


# ── malformed overrides fall back to Settings (never crash a read-site) ──────
# A bad DB row must not propagate a ValueError to a hot read path — e.g.
# `gate_input_channel_id` is read on every `on_message`, so one bad row would
# otherwise break command dispatch server-wide. The resolver logs and falls
# back to the .env base; the .env base itself still parses strictly at startup.

async def test_malformed_int_override_falls_back_to_settings():
    from n3x_bot.runtime_config import RuntimeConfig
    settings = _settings(gate_stats_channel_id=555)
    rc = RuntimeConfig(settings, {"gate_stats_channel_id": "notanumber"})
    # no raise; resolves to the Settings value.
    assert rc.gate_stats_channel_id == settings.gate_stats_channel_id
    assert rc.gate_stats_channel_id == 555


async def test_malformed_gate_rewards_override_falls_back_to_settings():
    from n3x_bot.runtime_config import RuntimeConfig
    settings = _settings()  # default 7-gate reward map
    # "a:notanumber" parses far enough to raise on int(v) -> must fall back.
    rc = RuntimeConfig(settings, {"gate_rewards": "a:notanumber"})
    assert rc.gate_rewards_map() == settings.gate_rewards_map()


async def test_malformed_reminder_time_override_falls_back_to_settings():
    from n3x_bot.runtime_config import RuntimeConfig
    settings = _settings(reminder_time="19:30")
    # "garbage" has no ':' -> parse_reminder_hm raises on unpack -> fall back.
    rc = RuntimeConfig(settings, {"reminder_time": "garbage"})
    assert rc.reminder_hm() == settings.reminder_hm()
    assert rc.reminder_hm() == (19, 30)


# ── non-overridable keys: pass-through, and DB override is IGNORED ───────────

async def test_non_overridable_field_passes_through_to_settings():
    from n3x_bot.runtime_config import RuntimeConfig
    settings = _settings(admin_role_id=7)
    rc = RuntimeConfig(settings)
    assert rc.admin_role_id == 7
    assert rc.command_prefix == settings.command_prefix
    assert rc.prefix_str == settings.prefix_str


async def test_override_for_non_overridable_key_is_ignored():
    # An operator (or a bug) storing a DB override under a non-overridable key
    # must NOT change the resolved value — admin_role_id gates the config
    # commands, so a stray override could lock everyone out.
    from n3x_bot.runtime_config import RuntimeConfig
    settings = _settings(admin_role_id=7)
    rc = RuntimeConfig(settings, {"admin_role_id": "999"})
    assert rc.admin_role_id == 7


async def test_overridable_keys_membership_guard():
    from n3x_bot.runtime_config import OVERRIDABLE_KEYS
    # overridable fields ARE members
    assert "gate_stats_channel_id" in OVERRIDABLE_KEYS
    assert "gate_rewards" in OVERRIDABLE_KEYS
    assert "reminder_time" in OVERRIDABLE_KEYS
    # env-authoritative fields are NOT
    assert "admin_role_id" not in OVERRIDABLE_KEYS
    assert "discord_token" not in OVERRIDABLE_KEYS
    assert "storage_backend" not in OVERRIDABLE_KEYS


# ── refresh: picks up a newly-set DB value (real repo, no mocks) ─────────────

async def test_refresh_reloads_overrides_from_repo():
    from n3x_bot.runtime_config import RuntimeConfig
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=555)
    rc = RuntimeConfig(settings)
    assert rc.gate_stats_channel_id == 555  # base value before any override

    await repo.set_runtime_config("gate_stats_channel_id", "999")
    await rc.refresh(repo)

    assert rc.gate_stats_channel_id == 999
    await repo.close()
    if os.path.exists(repo._test_path):
        os.remove(repo._test_path)


# ── load: classmethod factory reads overrides from the repo ─────────────────

async def test_load_builds_resolver_with_overrides_from_repo():
    from n3x_bot.runtime_config import RuntimeConfig
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=555)
    await repo.set_runtime_config("gate_stats_channel_id", "999")

    rc = await RuntimeConfig.load(repo, settings)

    assert rc.gate_stats_channel_id == 999
    await repo.close()
    if os.path.exists(repo._test_path):
        os.remove(repo._test_path)


async def test_load_with_no_overrides_is_behavior_preserving():
    from n3x_bot.runtime_config import RuntimeConfig
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=555)

    rc = await RuntimeConfig.load(repo, settings)

    assert rc.gate_stats_channel_id == 555
    assert rc.gate_rewards_map() == settings.gate_rewards_map()
    await repo.close()
    if os.path.exists(repo._test_path):
        os.remove(repo._test_path)


# ── wiring smoke: build_bot attaches a behavior-preserving RuntimeConfig ─────

async def test_build_bot_attaches_runtime_config():
    from n3x_bot.bot import build_bot
    from n3x_bot.runtime_config import RuntimeConfig
    from n3x_bot.seed import seed_defaults
    repo = await _flatfile_repo()
    await seed_defaults(repo)
    settings = _settings(gate_stats_channel_id=555)

    bot = build_bot(settings, repo)

    assert isinstance(bot.runtime_config, RuntimeConfig)
    await repo.close()
    if os.path.exists(repo._test_path):
        os.remove(repo._test_path)


async def test_build_bot_runtime_config_is_behavior_preserving_without_overrides():
    from n3x_bot.bot import build_bot
    from n3x_bot.seed import seed_defaults
    repo = await _flatfile_repo()
    await seed_defaults(repo)
    settings = _settings(gate_stats_channel_id=555, welcome_channel_id=222)

    bot = build_bot(settings, repo)

    # No DB overrides exist -> resolver mirrors Settings exactly.
    assert bot.runtime_config.gate_stats_channel_id == settings.gate_stats_channel_id
    assert bot.runtime_config.welcome_channel_id == settings.welcome_channel_id
    assert bot.runtime_config.gate_rewards_map() == settings.gate_rewards_map()
    await repo.close()
    if os.path.exists(repo._test_path):
        os.remove(repo._test_path)


# ── end-to-end: a DB override actually reaches a read-site (routing proof) ────

class _CapturingChannel:
    """A minimal gate-stats channel that records the embed posted to it."""

    def __init__(self, channel_id: int):
        self.id = channel_id
        self.embeds: list = []

    async def send(self, *args, embed=None, **kwargs):
        self.embeds.append(embed)
        return SimpleNamespace(id=123456789)


def _alpha_reward_line(embed) -> str:
    """The `Belohnung: …` line from the embed's Alpha (a) gate field."""
    alpha = next(f for f in embed.fields if "Alpha" in f.name)
    return next(ln for ln in alpha.value.splitlines() if ln.startswith("Belohnung:"))


async def test_gate_rewards_override_reaches_update_gate_stats_embed():
    # Prove routing end-to-end: a `gate_rewards` DB override, once refreshed,
    # is what `update_gate_stats_embed` renders — NOT the Settings default.
    from n3x_bot.bot import build_bot, update_gate_stats_embed
    from n3x_bot.format import format_number
    from n3x_bot.seed import seed_defaults
    repo = await _flatfile_repo()
    await seed_defaults(repo)
    settings = _settings(gate_stats_channel_id=555)
    await repo.set_runtime_config(
        "gate_rewards", "a:1,b:2,c:3,d:4,e:5,z:6,k:7")

    bot = build_bot(settings, repo)
    await bot.runtime_config.refresh(repo)

    channel = _CapturingChannel(555)
    bot.get_channel = lambda cid: channel if cid == 555 else None

    await update_gate_stats_embed(bot, repo, settings)

    assert len(channel.embeds) == 1
    # Override wins: Alpha reward is 1, not the Settings default (46892).
    assert _alpha_reward_line(channel.embeds[0]) == f"Belohnung: {format_number(1)}"
    assert format_number(1) != format_number(settings.gate_rewards_map()["a"])
    await repo.close()
    if os.path.exists(repo._test_path):
        os.remove(repo._test_path)


async def test_gate_rewards_no_override_uses_settings_default_at_read_site():
    # Behavior-preserving counterpart: with NO override, the SAME read-site
    # renders the Settings default reward.
    from n3x_bot.bot import build_bot, update_gate_stats_embed
    from n3x_bot.format import format_number
    from n3x_bot.seed import seed_defaults
    repo = await _flatfile_repo()
    await seed_defaults(repo)
    settings = _settings(gate_stats_channel_id=555)

    bot = build_bot(settings, repo)
    await bot.runtime_config.refresh(repo)

    channel = _CapturingChannel(555)
    bot.get_channel = lambda cid: channel if cid == 555 else None

    await update_gate_stats_embed(bot, repo, settings)

    assert len(channel.embeds) == 1
    default_alpha = settings.gate_rewards_map()["a"]
    assert _alpha_reward_line(channel.embeds[0]) == f"Belohnung: {format_number(default_alpha)}"
    await repo.close()
    if os.path.exists(repo._test_path):
        os.remove(repo._test_path)
