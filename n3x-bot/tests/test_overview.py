"""RED tests for the achievement overview page, ◀ ▶ BUTTON navigation.

The reaction-based overview nav is replaced by a persistent ``discord.ui.View``
(the modern Discord way). Anyone may page; each page still shows exactly one
user (mention / count/denom / bar / Platz / breakdown / Seite x/y).

Surfaces under test:

1. ``build_overview_embed(holders, user_ids, page, total=None, defs=None)`` —
   UNCHANGED pure fn. Its existing specs (page/wrap/rank/breakdown) stay green.

2. ``OverviewView(bot, repo, settings)`` — a persistent ``discord.ui.View``
   (``timeout is None``) with two buttons: "◀" (custom_id ``n3x:overview:prev``)
   and "▶" (custom_id ``n3x:overview:next``). Each button callback recomputes
   holders live, reads ``bot._overview_state["page"]``, advances it by ±1 mod
   ``len(user_ids)`` (guarding empty), rebuilds the embed for the new page and
   ``interaction.response.edit_message(embed=..., view=self)`` in place.

3. ``async post_overview(bot, repo, settings)`` — posts the embed WITH the view
   (``channel.send(embed=..., view=OverviewView(...))``); NO reactions. The
   edit-existing path edits the tracked message in place (view persists).

4. ``on_ready`` registers the persistent view once via
   ``bot.add_view(OverviewView(bot, repo, settings))`` so button clicks survive a
   restart, and the old ``handle_overview_reaction`` is NO LONGER wired into
   ``on_raw_reaction_add`` (reacting ⬅️/➡️ on the overview message no longer
   edits it).

New symbols are resolved lazily inside test bodies (``_mod().OverviewView``) so a
missing symbol fails the individual test (correct pre-impl RED) rather than
breaking collection.

Invoking a ``@discord.ui.button`` callback in tests (pinned against discord.py
2.7): the button object lives in ``view.children``; calling ``button.callback``
runs the decorated coroutine and passes the button automatically, so
``await button.callback(interaction)`` is all that's needed.

Assumptions pinned here (flag for the Architect):
  * ``build_overview_embed`` / ``post_overview`` / ``OverviewView`` live in
    ``n3x_bot.achievements``. If homed elsewhere, only ``_mod`` changes.
  * ``bot._overview_state`` remains a dict with ``page`` / ``user_ids`` /
    ``message_id`` keys; the button reads ``["page"]`` and updates it in place.
  * ``user_ids = sorted(holders)`` so page order is deterministic.
"""

import importlib
import os
import tempfile

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from n3x_bot.achievements import TOTAL_ACHIEVEMENTS
from n3x_bot.bot import build_bot
from n3x_bot.config import Settings
from n3x_bot.seed import seed_defaults
from n3x_bot.storage.json_repo import JsonRepository

BASE_SETTINGS_KWARGS = dict(
    discord_token="tok",
    target_role_id=1,
    welcome_channel_id=2,
    reminder_channel_id=999,
    julez_id=424242,
    _env_file=None,
    _env_prefix="NONEXISTENT_",
)

PREV_CUSTOM_ID = "n3x:overview:prev"
NEXT_CUSTOM_ID = "n3x:overview:next"


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
    return repo


def _mod():
    return importlib.import_module("n3x_bot.achievements")


def _embed_text(embed) -> str:
    parts = [str(getattr(embed, "title", "") or ""),
             str(getattr(embed, "description", "") or "")]
    for f in getattr(embed, "fields", None) or []:
        parts.append(str(getattr(f, "name", "") or ""))
        parts.append(str(getattr(f, "value", "") or ""))
    return "\n".join(parts)


class _Emoji:
    """Stands in for a discord PartialEmoji: str() and .name both return the
    unicode, so any handler matches whether it compares str(emoji) or .name."""
    def __init__(self, char):
        self.name = char
        self._char = char

    def __str__(self):
        return self._char


def _overview_channel():
    """A fake overview channel: send() returns a message that also answers
    fetch_message(id) so the edit-existing path can re-fetch it."""
    counter = {"n": 500}
    sent = []

    def _send(*args, **kwargs):
        counter["n"] += 1
        msg = MagicMock()
        msg.id = counter["n"]
        msg.embed = kwargs.get("embed")
        msg.add_reaction = AsyncMock()
        msg.edit = AsyncMock()
        msg.remove_reaction = AsyncMock()
        sent.append(msg)
        return msg

    def _fetch(message_id):
        for m in sent:
            if m.id == message_id:
                return m
        raise RuntimeError(f"unknown message {message_id}")

    channel = MagicMock()
    channel.send = AsyncMock(side_effect=_send)
    channel.fetch_message = AsyncMock(side_effect=_fetch)
    return channel, sent


def _button(view, custom_id):
    """The child button on the view carrying `custom_id` (discord.py exposes the
    @discord.ui.button items via view.children)."""
    for child in getattr(view, "children", []):
        if getattr(child, "custom_id", None) == custom_id:
            return child
    raise AssertionError(f"no button with custom_id {custom_id!r} on {view!r}")


def _btn_interaction():
    """A fake interaction for a button click: response.edit_message is the in-
    place editor the callback must call."""
    it = MagicMock()
    it.response = MagicMock()
    it.response.edit_message = AsyncMock()
    it.user = SimpleNamespace(id=7, bot=False)
    return it


# ── build_overview_embed (pure, UNCHANGED) ──────────────────────────────────

def test_build_overview_embed_page_zero_shows_first_user_count_over_total():
    holders = {10: {"a_5", "voice_3600"}, 20: {"msg_1000"}}
    embed = _mod().build_overview_embed(holders, [10, 20], 0)
    assert f"2/{TOTAL_ACHIEVEMENTS}" in _embed_text(embed)  # user 10 has 2


def test_build_overview_embed_page_one_shows_second_user_count():
    holders = {10: {"a_5", "voice_3600"}, 20: {"msg_1000"}}
    embed = _mod().build_overview_embed(holders, [10, 20], 1)
    assert f"1/{TOTAL_ACHIEVEMENTS}" in _embed_text(embed)  # user 20 has 1


def test_build_overview_embed_out_of_range_page_wraps_to_first():
    holders = {10: {"a_5", "voice_3600"}, 20: {"msg_1000"}}
    embed = _mod().build_overview_embed(holders, [10, 20], 2)  # 2 % 2 == 0
    assert f"2/{TOTAL_ACHIEVEMENTS}" in _embed_text(embed)  # wrapped to user 10


def test_build_overview_embed_shows_page_indicator():
    holders = {10: {"a_5"}, 20: {"msg_1000"}}
    text = _embed_text(_mod().build_overview_embed(holders, [10, 20], 0))
    assert "1" in text and "2" in text


def test_build_overview_embed_identifies_the_page_user():
    holders = {10: {"a_5", "voice_3600"}, 20: {"msg_1000"}}
    page0 = _embed_text(_mod().build_overview_embed(holders, [10, 20], 0))
    page1 = _embed_text(_mod().build_overview_embed(holders, [10, 20], 1))
    assert "<@10>" in page0
    assert "<@20>" in page1
    assert "<@20>" not in page0


def test_build_overview_embed_empty_user_ids_does_not_crash():
    embed = _mod().build_overview_embed({}, [], 0)
    assert embed is not None
    assert "Achievement" in _embed_text(embed)


def test_build_overview_embed_total_override_flows_into_text():
    holders = {10: {"a_5"}}
    embed = _mod().build_overview_embed(holders, [10], 0, total=84)
    assert "1/84" in _embed_text(embed)


def test_build_overview_embed_total_none_uses_module_total():
    holders = {10: {"a_5"}}
    embed = _mod().build_overview_embed(holders, [10], 0, total=None)
    assert f"1/{TOTAL_ACHIEVEMENTS}" in _embed_text(embed)


# ── richer overview layout: Platz (rank) + per-category breakdown (UNCHANGED) ─
#
# The pure embed keeps per-user paging plus, from the SAME inputs, a "Platz
# {rank}" (page user's rank by unlock count) and a per-category breakdown line
# (gate/voice/streak/night + a folded 🔒 Secret bucket).

_RANK_HOLDERS = {
    10: {"a_5", "b_5", "voice_3600", "streak_7", "msg_1000"},   # 5
    20: {"a_5", "voice_3600", "streak_7"},                      # 3 (middle)
    30: {"night_10"},                                           # 1
}
_RANK_USER_IDS = [10, 20, 30]


def test_overview_middle_user_shows_platz_two():
    embed = _mod().build_overview_embed(_RANK_HOLDERS, _RANK_USER_IDS, 1)
    assert "Platz 2" in _embed_text(embed)


def test_overview_top_user_shows_platz_one():
    embed = _mod().build_overview_embed(_RANK_HOLDERS, _RANK_USER_IDS, 0)
    assert "Platz 1" in _embed_text(embed)


def test_overview_last_user_shows_platz_three():
    embed = _mod().build_overview_embed(_RANK_HOLDERS, _RANK_USER_IDS, 2)
    assert "Platz 3" in _embed_text(embed)


def test_overview_breakdown_shows_gate_count_for_page_user():
    embed = _mod().build_overview_embed(_RANK_HOLDERS, _RANK_USER_IDS, 1)
    assert "1/67" in _embed_text(embed)


def test_overview_breakdown_shows_night_and_secret_buckets():
    text = _embed_text(_mod().build_overview_embed(_RANK_HOLDERS, _RANK_USER_IDS, 1))
    assert "0/3" in text   # 🌙 night
    assert "0/8" in text   # 🔒 secret (message + reaction, 4 + 4)


def test_overview_breakdown_shows_all_five_category_emojis():
    text = _embed_text(_mod().build_overview_embed(_RANK_HOLDERS, _RANK_USER_IDS, 1))
    for emoji in ("🚀", "🎙️", "🔥", "🌙", "🔒"):
        assert emoji in text


# ── OverviewView: shape / persistence ───────────────────────────────────────

async def test_overview_view_is_a_persistent_view():
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)

    view = _mod().OverviewView(bot, repo, settings)
    assert isinstance(view, discord.ui.View)
    # timeout=None makes the view PERSISTENT so bot.add_view can route clicks
    # after a restart.
    assert view.timeout is None
    await repo.close()


async def test_overview_view_has_prev_and_next_buttons_with_stable_custom_ids():
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)

    view = _mod().OverviewView(bot, repo, settings)
    ids = {getattr(c, "custom_id", None) for c in view.children}
    assert PREV_CUSTOM_ID in ids
    assert NEXT_CUSTOM_ID in ids
    await repo.close()


# ── OverviewView: button navigation ─────────────────────────────────────────

async def test_next_button_advances_page_and_edits_embed():
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)

    await repo.unlock_achievement(10, "a_5")
    await repo.unlock_achievement(10, "voice_3600")   # user 10 -> 2
    await repo.unlock_achievement(20, "msg_1000")      # user 20 -> 1
    bot._overview_state = {"message_id": 1, "page": 0, "user_ids": [10, 20]}

    view = _mod().OverviewView(bot, repo, settings)
    interaction = _btn_interaction()
    await _button(view, NEXT_CUSTOM_ID).callback(interaction)

    # page advanced 0 -> 1 and the shared message was edited in place.
    assert bot._overview_state["page"] == 1
    interaction.response.edit_message.assert_awaited_once()
    edited = interaction.response.edit_message.await_args.kwargs.get("embed")
    assert f"1/{TOTAL_ACHIEVEMENTS}" in _embed_text(edited)  # advanced to user 20
    await repo.close()


async def test_next_button_wraps_from_last_page_to_first():
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)

    await repo.unlock_achievement(10, "a_5")
    await repo.unlock_achievement(10, "voice_3600")   # user 10 -> 2
    await repo.unlock_achievement(20, "msg_1000")      # user 20 -> 1
    bot._overview_state = {"message_id": 1, "page": 1, "user_ids": [10, 20]}

    view = _mod().OverviewView(bot, repo, settings)
    interaction = _btn_interaction()
    await _button(view, NEXT_CUSTOM_ID).callback(interaction)

    # (1 + 1) % 2 == 0 -> wrapped back to user 10.
    assert bot._overview_state["page"] == 0
    edited = interaction.response.edit_message.await_args.kwargs.get("embed")
    assert f"2/{TOTAL_ACHIEVEMENTS}" in _embed_text(edited)
    await repo.close()


async def test_prev_button_wraps_from_first_page_to_last():
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)

    await repo.unlock_achievement(10, "a_5")
    await repo.unlock_achievement(10, "voice_3600")   # user 10 -> 2
    await repo.unlock_achievement(20, "msg_1000")      # user 20 -> 1
    bot._overview_state = {"message_id": 1, "page": 0, "user_ids": [10, 20]}

    view = _mod().OverviewView(bot, repo, settings)
    interaction = _btn_interaction()
    await _button(view, PREV_CUSTOM_ID).callback(interaction)

    # (0 - 1) % 2 == 1 -> wrapped forward to the last page (user 20).
    assert bot._overview_state["page"] == 1
    edited = interaction.response.edit_message.await_args.kwargs.get("embed")
    assert f"1/{TOTAL_ACHIEVEMENTS}" in _embed_text(edited)
    await repo.close()


async def test_button_edit_reuses_the_same_view_instance():
    # edit_message must re-attach `self` so the buttons stay live on the message.
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)

    await repo.unlock_achievement(10, "a_5")
    await repo.unlock_achievement(20, "msg_1000")
    bot._overview_state = {"message_id": 1, "page": 0, "user_ids": [10, 20]}

    view = _mod().OverviewView(bot, repo, settings)
    interaction = _btn_interaction()
    await _button(view, NEXT_CUSTOM_ID).callback(interaction)

    assert interaction.response.edit_message.await_args.kwargs.get("view") is view
    await repo.close()


async def test_button_with_empty_user_ids_is_a_noop():
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)

    # nobody holds any achievements -> live user_ids is empty.
    bot._overview_state = {"message_id": 1, "page": 0, "user_ids": []}

    view = _mod().OverviewView(bot, repo, settings)
    interaction = _btn_interaction()
    await _button(view, NEXT_CUSTOM_ID).callback(interaction)  # must not raise

    interaction.response.edit_message.assert_not_awaited()
    await repo.close()


# ── post_overview: posts WITH the view, NO reactions ────────────────────────

async def test_post_overview_sends_embed_with_overview_view_and_no_reactions():
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _overview_channel()
    bot.get_channel = MagicMock(return_value=channel)

    await repo.unlock_achievement(10, "a_5")
    await repo.unlock_achievement(20, "msg_1000")

    await _mod().post_overview(bot, repo, settings)

    channel.send.assert_awaited_once()
    kwargs = channel.send.await_args.kwargs
    assert kwargs.get("embed") is not None
    # buttons replace reactions: sent WITH an OverviewView, and NO add_reaction.
    assert isinstance(kwargs.get("view"), _mod().OverviewView)
    assert sent[0].add_reaction.await_count == 0
    await repo.close()


async def test_post_overview_records_message_id_in_overview_state():
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _overview_channel()
    bot.get_channel = MagicMock(return_value=channel)

    await repo.unlock_achievement(10, "a_5")

    await _mod().post_overview(bot, repo, settings)

    assert str(sent[0].id) in str(getattr(bot, "_overview_state", None))
    await repo.close()


async def test_post_overview_is_noop_when_channel_unset():
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=0)
    bot = build_bot(settings, repo)
    channel, _ = _overview_channel()
    bot.get_channel = MagicMock(return_value=channel)

    await repo.unlock_achievement(10, "a_5")

    await _mod().post_overview(bot, repo, settings)

    channel.send.assert_not_called()
    await repo.close()


async def test_post_overview_reuses_existing_message_instead_of_spamming():
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _overview_channel()
    bot.get_channel = MagicMock(return_value=channel)

    await repo.unlock_achievement(10, "a_5")

    await _mod().post_overview(bot, repo, settings)      # first: posts fresh
    first_id = bot._overview_state["message_id"]

    await _mod().post_overview(bot, repo, settings)      # second: must reuse

    # only ONE message ever sent; the second call edited it back to page 0. The
    # persistent view stays attached across the edit (no re-send needed).
    channel.send.assert_awaited_once()
    assert len(sent) == 1
    sent[0].edit.assert_awaited_once()
    assert bot._overview_state["message_id"] == first_id
    assert bot._overview_state["page"] == 0
    await repo.close()


async def test_post_overview_posts_fresh_when_tracked_message_gone():
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _overview_channel()
    bot.get_channel = MagicMock(return_value=channel)

    await repo.unlock_achievement(10, "a_5")

    # stale tracker pointing at a message the channel can no longer fetch.
    bot._overview_state = {"message_id": 999999, "page": 3, "user_ids": [10]}

    await _mod().post_overview(bot, repo, settings)

    # fetch of the dead id failed -> a fresh message was posted WITH the view and
    # NO reactions.
    channel.send.assert_awaited_once()
    assert len(sent) == 1
    assert isinstance(channel.send.await_args.kwargs.get("view"), _mod().OverviewView)
    assert sent[0].add_reaction.await_count == 0
    assert bot._overview_state["message_id"] == sent[0].id
    await repo.close()


async def test_post_overview_is_noop_when_no_holders():
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, _ = _overview_channel()
    bot.get_channel = MagicMock(return_value=channel)

    await _mod().post_overview(bot, repo, settings)  # nobody has achievements

    channel.send.assert_not_called()
    await repo.close()


# ── /overview command (UNCHANGED, still drives post_overview) ────────────────

async def test_build_bot_registers_overview_app_command():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    assert bot.get_command("overview") is None
    assert bot.tree.get_command("overview") is not None
    await repo.close()


async def test_overview_command_triggers_post_overview():
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _overview_channel()
    bot.get_channel = MagicMock(return_value=channel)

    await repo.unlock_achievement(10, "a_5")

    cmd = bot.tree.get_command("overview")
    assert cmd is not None
    interaction = MagicMock()
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.user = SimpleNamespace(id=1, display_name="Anyone")

    await cmd.callback(interaction)

    channel.send.assert_awaited_once()
    assert channel.send.await_args.kwargs.get("embed") is not None
    interaction.response.defer.assert_awaited_once()
    assert interaction.response.defer.await_args.kwargs.get("ephemeral") is True
    interaction.followup.send.assert_awaited_once()
    await repo.close()


# ── wiring: on_ready registers the persistent view; reactions no longer wired ─

async def test_on_ready_registers_persistent_overview_view():
    repo = await _flatfile_repo()
    # channels off so on_ready's other best-effort posts are trivial no-ops.
    settings = _settings()
    bot = build_bot(settings, repo)
    bot.add_view = MagicMock()
    bot.tree.sync = AsyncMock()

    await bot.on_ready()

    # on_ready registers several persistent views (overview + command-list);
    # assert the OverviewView is among them.
    registered = [c.args[0] for c in bot.add_view.call_args_list]
    assert any(isinstance(v, _mod().OverviewView) for v in registered)
    await repo.close()


async def test_reacting_on_overview_message_no_longer_edits_it():
    # handle_overview_reaction is no longer wired into on_raw_reaction_add:
    # buttons replace the reaction nav, so a ⬅️ reaction is a plain no-op.
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _overview_channel()
    bot.get_channel = MagicMock(return_value=channel)

    await repo.unlock_achievement(10, "a_5")
    await repo.unlock_achievement(20, "msg_1000")
    await _mod().post_overview(bot, repo, settings)
    msg = sent[0]

    payload = SimpleNamespace(
        channel_id=4242, message_id=msg.id, emoji=_Emoji("⬅️"),
        user_id=7, guild_id=1, member=SimpleNamespace(id=7, bot=False, roles=[]))
    await bot.on_raw_reaction_add(payload)

    msg.edit.assert_not_awaited()
    await repo.close()
