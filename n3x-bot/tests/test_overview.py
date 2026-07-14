"""RED tests for achievements Pass C, sub-feature 2: the paginated overview embed.

Surfaces:

1. ``build_overview_embed(holders, user_ids, page) -> discord.Embed`` — pure:
   one page per user, showing ``count/TOTAL_ACHIEVEMENTS``, a progress bar and a
   page indicator. Out-of-range pages WRAP (``page % len(user_ids)``).

2. ``async post_overview(bot, repo, settings) -> None`` — builds holders from the
   repo, posts one embed to ``overview_channel_id``, adds ⬅️/➡️ nav reactions and
   records the message id + page in ``bot._overview_state``. No-op when
   ``overview_channel_id == 0`` or there are no holders.

3. ``async handle_overview_reaction(bot, repo, settings, payload) -> None`` —
   when the reaction is on the tracked overview message, in the overview channel,
   with a ⬅️/➡️ emoji, from a non-bot: change page (wrapping), edit the embed, and
   remove the user's reaction. Ignores everything else.

4. The ``!overview`` prefix command (anyone) is registered by ``build_bot`` and
   drives ``post_overview`` end-to-end.

New symbols are resolved lazily inside test bodies so a missing symbol fails the
individual test (correct pre-impl RED) rather than breaking collection.

Assumptions pinned here (flag for the Architect):
  * ``build_overview_embed`` / ``post_overview`` / ``handle_overview_reaction``
    live in ``n3x_bot.achievements``. If homed elsewhere, only ``_mod`` changes.
  * The per-user count is ``len(holders.get(user_id, set()))`` and the "N/59"
    string is rendered somewhere in the embed's flattened text.
  * The nav emojis are exactly ⬅️ and ➡️ and are compared via ``str(emoji)``.
  * ``payload`` mirrors ``discord.RawReactionActionEvent``: ``.channel_id``,
    ``.message_id``, ``.emoji`` (str-able), ``.user_id``, ``.member``.
  * ``handle_overview_reaction`` re-renders by fetching the tracked message via
    ``bot.get_channel(overview_channel_id).fetch_message(id)`` then ``.edit`` /
    ``.remove_reaction`` (v3 fidelity).
"""

import importlib
import os
import tempfile

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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
    unicode, so the handler matches whether it compares str(emoji) or .name."""
    def __init__(self, char):
        self.name = char
        self._char = char

    def __str__(self):
        return self._char


def _overview_channel():
    """A fake overview channel: send() returns a message that also answers
    fetch_message(id) so the reaction handler can re-fetch it to edit."""
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


# ── build_overview_embed (pure) ────────────────────────────────────────────

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
    # human-facing 1-based page "1" and the total user count "2" both appear.
    assert "1" in text and "2" in text


# ── post_overview ──────────────────────────────────────────────────────────

async def test_post_overview_sends_embed_and_two_nav_reactions():
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _overview_channel()
    bot.get_channel = MagicMock(return_value=channel)

    await repo.unlock_achievement(10, "a_5")
    await repo.unlock_achievement(20, "msg_1000")

    await _mod().post_overview(bot, repo, settings)

    channel.send.assert_awaited_once()
    assert channel.send.await_args.kwargs.get("embed") is not None
    assert sent[0].add_reaction.await_count == 2  # ⬅️ and ➡️
    await repo.close()


async def test_post_overview_records_message_id_in_overview_state():
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _overview_channel()
    bot.get_channel = MagicMock(return_value=channel)

    await repo.unlock_achievement(10, "a_5")

    await _mod().post_overview(bot, repo, settings)

    # the posted message id is tracked somewhere in bot._overview_state so the
    # reaction handler can recognise its own message later.
    assert sent[0].id in str(getattr(bot, "_overview_state", None))
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


async def test_post_overview_is_noop_when_no_holders():
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, _ = _overview_channel()
    bot.get_channel = MagicMock(return_value=channel)

    await _mod().post_overview(bot, repo, settings)  # nobody has achievements

    channel.send.assert_not_called()
    await repo.close()


# ── handle_overview_reaction ───────────────────────────────────────────────

async def test_reaction_forward_advances_page_and_edits_embed():
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _overview_channel()
    bot.get_channel = MagicMock(return_value=channel)

    await repo.unlock_achievement(10, "a_5")
    await repo.unlock_achievement(10, "voice_3600")   # user 10 -> 2
    await repo.unlock_achievement(20, "msg_1000")      # user 20 -> 1

    await _mod().post_overview(bot, repo, settings)     # page 0 -> user 10
    msg = sent[0]

    payload = SimpleNamespace(
        channel_id=4242, message_id=msg.id, emoji=_Emoji("➡️"),
        user_id=7, member=SimpleNamespace(id=7, bot=False))
    await _mod().handle_overview_reaction(bot, repo, settings, payload)

    msg.edit.assert_awaited_once()
    edited = msg.edit.await_args.kwargs.get("embed")
    assert f"1/{TOTAL_ACHIEVEMENTS}" in _embed_text(edited)  # advanced to user 20
    msg.remove_reaction.assert_awaited_once()
    await repo.close()


async def test_reaction_in_unrelated_channel_is_ignored():
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _overview_channel()
    bot.get_channel = MagicMock(return_value=channel)

    await repo.unlock_achievement(10, "a_5")
    await _mod().post_overview(bot, repo, settings)
    msg = sent[0]

    payload = SimpleNamespace(
        channel_id=999, message_id=msg.id, emoji=_Emoji("➡️"),
        user_id=7, member=SimpleNamespace(id=7, bot=False))
    await _mod().handle_overview_reaction(bot, repo, settings, payload)

    msg.edit.assert_not_awaited()
    await repo.close()


async def test_reaction_with_unrelated_emoji_is_ignored():
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _overview_channel()
    bot.get_channel = MagicMock(return_value=channel)

    await repo.unlock_achievement(10, "a_5")
    await _mod().post_overview(bot, repo, settings)
    msg = sent[0]

    payload = SimpleNamespace(
        channel_id=4242, message_id=msg.id, emoji=_Emoji("🎉"),
        user_id=7, member=SimpleNamespace(id=7, bot=False))
    await _mod().handle_overview_reaction(bot, repo, settings, payload)

    msg.edit.assert_not_awaited()
    await repo.close()


# ── !overview command ──────────────────────────────────────────────────────

async def test_build_bot_registers_overview_command():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    assert bot.get_command("overview") is not None
    await repo.close()


async def test_overview_command_triggers_post_overview():
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _overview_channel()
    bot.get_channel = MagicMock(return_value=channel)

    await repo.unlock_achievement(10, "a_5")

    cmd = bot.get_command("overview")
    assert cmd is not None
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.author = SimpleNamespace(id=1, display_name="Anyone")

    await cmd.callback(ctx)

    # end-to-end proof the command drove post_overview: an embed was posted.
    channel.send.assert_awaited_once()
    assert channel.send.await_args.kwargs.get("embed") is not None
    await repo.close()
