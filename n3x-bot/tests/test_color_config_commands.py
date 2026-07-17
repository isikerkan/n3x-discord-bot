"""RED-phase specs for Phase 3 Part D: wiring the ColorConfig resolver into the
bot and adding the ``/config`` colour setters.

Pinned surface (to be implemented downstream):

  * ``build_bot`` attaches ``bot.colors = ColorConfig()`` (mirrors
    ``bot.content_texts``); ``on_ready`` refreshes it (guarded, never fatal).
  * ``cards.announce_achievements`` passes the live resolver into its
    ``tier_color(...)`` call as ``colors=getattr(bot, "colors", None)``.
  * The existing ``/config`` app-command group (config_commands.py) gains, all
    admin-gated via ``app_is_admin``:
        /config tier-color     name:<str> hex:<str>
        /config category-color name:<str> hex:<str>
        /config color-reset    key:<str>
    tier-color / category-color validate ``hex`` via ``cards._parse_hex_color``
    (invalid -> ephemeral error, NO write, NO refresh), write
    ``tier:<name.lower()>`` / ``category:<name.lower()>`` via
    ``repo.set_color_config``, then ``await bot.colors.refresh(repo)`` and reply
    ephemerally. ``color-reset`` deletes a ``color_config`` key + refreshes.

ANNOUNCE-FAKE DECISION (pinned): ``announce_achievements`` reads the resolver via
``getattr(bot, "colors", None)`` so it tolerates a bot without the attribute
(param defaults None downstream in ``cards.tier_color``). The existing
``tests/test_achievement_announce.py`` build real bots via ``build_bot`` and so
get ``bot.colors`` for free once Part D wires it — no edit to those fakes needed.

``n3x_bot.colors`` is imported lazily inside test bodies so the RED state is a
clean per-test failure rather than a collection-time ImportError.
"""

import importlib
import os
import tempfile
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from PIL import Image
from discord import app_commands

from n3x_bot import cards
from n3x_bot.achievements import ACHIEVEMENTS
from n3x_bot.bot import build_bot
from n3x_bot.config import Settings
from n3x_bot.seed import seed_defaults
from n3x_bot.storage.json_repo import JsonRepository

ADMIN_ROLE = 42

BASE_SETTINGS_KWARGS = dict(
    discord_token="tok",
    target_role_id=1,
    welcome_channel_id=2,
    reminder_channel_id=999,
    julez_id=424242,
    admin_role_id=ADMIN_ROLE,
    _env_file=None,
    _env_prefix="NONEXISTENT_",
)


def _settings(**overrides) -> Settings:
    kwargs = dict(BASE_SETTINGS_KWARGS)
    kwargs.update(overrides)
    return Settings(**kwargs)


async def _flatfile_repo() -> JsonRepository:
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    repo = JsonRepository(path)
    await repo.connect()
    await seed_defaults(repo)
    repo._test_path = path
    return repo


async def _cleanup(repo: JsonRepository) -> None:
    path = getattr(repo, "_test_path", None)
    await repo.close()
    if path and os.path.exists(path):
        os.remove(path)


def _member(*, member_id=5, role_ids=(ADMIN_ROLE,)):
    return SimpleNamespace(id=member_id,
                           roles=[SimpleNamespace(id=r) for r in role_ids],
                           bot=False)


def _admin():
    return _member(role_ids=(ADMIN_ROLE,))


def _non_admin():
    return _member(role_ids=(999,))


def _fake_interaction(user=None):
    it = MagicMock()
    it.user = user or _admin()
    it.response = MagicMock()
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.followup = MagicMock()
    it.followup.send = AsyncMock()
    return it


def _sent_text(interaction) -> str:
    parts = []
    for mock in (interaction.response.send_message, interaction.followup.send):
        for call in mock.await_args_list:
            if call.args and isinstance(call.args[0], str):
                parts.append(call.args[0])
            content = call.kwargs.get("content")
            if isinstance(content, str):
                parts.append(content)
    return "\n".join(parts)


def _last_send(interaction):
    calls = (list(interaction.response.send_message.await_args_list)
             + list(interaction.followup.send.await_args_list))
    assert calls, "the callback must reply to the caller"
    return calls[-1]


def _config_group(bot):
    group = bot.tree.get_command("config")
    assert isinstance(group, app_commands.Group), \
        "`config` must be an app_commands.Group on bot.tree"
    return group


def _config_sub(bot, name):
    sub = _config_group(bot).get_command(name)
    assert sub is not None, f"/config {name} subcommand must be registered"
    return sub


# ══════════════════════════════════════════════════════════════════════════
# 1. build_bot wiring: bot.colors + on_ready refresh
# ══════════════════════════════════════════════════════════════════════════

async def test_build_bot_attaches_colors_resolver():
    colors_mod = importlib.import_module("n3x_bot.colors")
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)

    assert isinstance(bot.colors, colors_mod.ColorConfig)
    await _cleanup(repo)


async def test_build_bot_colors_behaviour_preserving_without_overrides():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)

    assert bot.colors.tier_color("Alpha Gold Pilot") == cards._gate_tier_color(
        "Alpha Gold Pilot")
    assert bot.colors.category_color("voice") == \
        cards.ACTIVITY_CATEGORY_COLORS["voice"]
    await _cleanup(repo)


async def test_on_ready_refreshes_colors():
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=0)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)
    bot.tree.sync = AsyncMock()
    await repo.set_color_config("tier:gold", "#010203")

    await bot.on_ready()

    assert bot.colors.tier_color("Alpha Gold Pilot") == (1, 2, 3)
    await _cleanup(repo)


# ══════════════════════════════════════════════════════════════════════════
# 2. /config colour subcommands are present on the group
# ══════════════════════════════════════════════════════════════════════════

async def test_config_group_exposes_colour_subcommands():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)

    names = {c.name for c in _config_group(bot).commands}
    assert {"tier-color", "category-color", "color-reset"} <= names
    await _cleanup(repo)


# ══════════════════════════════════════════════════════════════════════════
# 3. /config tier-color
# ══════════════════════════════════════════════════════════════════════════

async def test_tier_color_writes_key_and_refreshes_live_resolver():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    interaction = _fake_interaction()

    await _config_sub(bot, "tier-color").callback(
        interaction, name="gold", hex="#010203")

    assert await repo.get_color_config("tier:gold") == "#010203"
    # refresh() ran -> the live resolver reflects the override immediately.
    assert bot.colors.tier_color("Alpha Gold Pilot") == (1, 2, 3)
    assert _last_send(interaction).kwargs.get("ephemeral") is True
    await _cleanup(repo)


async def test_tier_color_lowercases_the_name():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    interaction = _fake_interaction()

    await _config_sub(bot, "tier-color").callback(
        interaction, name="GOLD", hex="#010203")

    assert await repo.get_color_config("tier:gold") == "#010203"
    assert await repo.get_color_config("tier:GOLD") is None
    await _cleanup(repo)


async def test_tier_color_invalid_hex_rejected_no_write():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    interaction = _fake_interaction()

    await _config_sub(bot, "tier-color").callback(
        interaction, name="gold", hex="nothex")

    assert await repo.get_color_config("tier:gold") is None
    assert _last_send(interaction).kwargs.get("ephemeral") is True
    # live resolver still shows the default, untouched
    assert bot.colors.tier_color("Alpha Gold Pilot") == cards._gate_tier_color(
        "Alpha Gold Pilot")
    await _cleanup(repo)


async def test_tier_color_non_admin_refused_no_write():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    interaction = _fake_interaction(user=_non_admin())

    await _config_sub(bot, "tier-color").callback(
        interaction, name="gold", hex="#010203")

    assert "Berechtigung" in _sent_text(interaction)
    assert await repo.get_color_config("tier:gold") is None
    await _cleanup(repo)


# ══════════════════════════════════════════════════════════════════════════
# 4. /config category-color
# ══════════════════════════════════════════════════════════════════════════

async def test_category_color_writes_key_and_refreshes():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    interaction = _fake_interaction()

    await _config_sub(bot, "category-color").callback(
        interaction, name="voice", hex="#010203")

    assert await repo.get_color_config("category:voice") == "#010203"
    assert bot.colors.category_color("voice") == (1, 2, 3)
    assert _last_send(interaction).kwargs.get("ephemeral") is True
    await _cleanup(repo)


async def test_category_color_invalid_hex_rejected_no_write():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    interaction = _fake_interaction()

    await _config_sub(bot, "category-color").callback(
        interaction, name="voice", hex="12FF00")  # missing leading '#'

    assert await repo.get_color_config("category:voice") is None
    assert _last_send(interaction).kwargs.get("ephemeral") is True
    await _cleanup(repo)


async def test_category_color_non_admin_refused_no_write():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    interaction = _fake_interaction(user=_non_admin())

    await _config_sub(bot, "category-color").callback(
        interaction, name="voice", hex="#010203")

    assert "Berechtigung" in _sent_text(interaction)
    assert await repo.get_color_config("category:voice") is None
    await _cleanup(repo)


# ══════════════════════════════════════════════════════════════════════════
# 5. /config color-reset
# ══════════════════════════════════════════════════════════════════════════

async def test_color_reset_deletes_key_and_refreshes():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    await repo.set_color_config("tier:gold", "#010203")
    await bot.colors.refresh(repo)
    assert bot.colors.tier_color("Alpha Gold Pilot") == (1, 2, 3)  # override active

    interaction = _fake_interaction()
    await _config_sub(bot, "color-reset").callback(interaction, key="tier:gold")

    assert await repo.get_color_config("tier:gold") is None
    assert bot.colors.tier_color("Alpha Gold Pilot") == cards._gate_tier_color(
        "Alpha Gold Pilot")
    assert _last_send(interaction).kwargs.get("ephemeral") is True
    await _cleanup(repo)


async def test_color_reset_non_admin_refused():
    repo = await _flatfile_repo()
    bot = build_bot(_settings(), repo)
    await repo.set_color_config("tier:gold", "#010203")
    await bot.colors.refresh(repo)
    interaction = _fake_interaction(user=_non_admin())

    await _config_sub(bot, "color-reset").callback(interaction, key="tier:gold")

    assert "Berechtigung" in _sent_text(interaction)
    assert await repo.get_color_config("tier:gold") == "#010203"
    await _cleanup(repo)


# ══════════════════════════════════════════════════════════════════════════
# 6. announce_achievements consumes bot.colors (integration)
# ══════════════════════════════════════════════════════════════════════════

def _png_bytes(size=(64, 64), color=(12, 34, 56)) -> bytes:
    buf = BytesIO()
    Image.new("RGBA", size, (*color, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _avatar_member(mid=7, name="Erkan"):
    m = MagicMock()
    m.id = mid
    m.bot = False
    m.display_name = name
    m.display_avatar = MagicMock()
    m.display_avatar.read = AsyncMock(return_value=_png_bytes())
    return m


def _milestone_channel():
    sent: list = []
    counter = {"n": 100}

    def _send(*args, **kwargs):
        counter["n"] += 1
        msg = MagicMock()
        msg.id = counter["n"]
        msg.delete = AsyncMock()
        sent.append(msg)
        return msg

    channel = MagicMock()
    channel.send = AsyncMock(side_effect=_send)
    channel.fetch_message = AsyncMock()
    return channel, sent


def _ach(achievement_id: str):
    return next(a for a in ACHIEVEMENTS if a.id == achievement_id)


def _tier_capturing_render(store: dict):
    def _render(avatar_bytes, title, subtitle, footer, tier):
        store["tier"] = tier
        return _png_bytes()
    return _render


async def test_announce_uses_bot_colors_tier_override(monkeypatch):
    # With bot.colors overriding tier:gold, the gold gate card is drawn with the
    # override colour — proving announce_achievements passes colors=bot.colors
    # into tier_color.
    captured: dict = {}
    monkeypatch.setattr("n3x_bot.cards.render_achievement_card",
                        _tier_capturing_render(captured))
    repo = await _flatfile_repo()
    settings = _settings(milestone_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, _ = _milestone_channel()
    bot.get_channel = MagicMock(return_value=channel)
    await repo.set_color_config("tier:gold", "#010203")
    await bot.colors.refresh(repo)

    await cards.announce_achievements(bot, settings, _avatar_member(),
                                      [_ach("a_25")])  # Alpha Gold Pilot

    assert captured["tier"] == (1, 2, 3)
    await _cleanup(repo)
