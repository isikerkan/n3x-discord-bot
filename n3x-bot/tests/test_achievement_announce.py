"""RED tests for achievements Pass B — the announce/post layer and its wiring.

Two surfaces:

1. ``announce_achievements(bot, settings, member, newly)`` — an async helper
   that renders a Pillow card per newly-unlocked achievement and posts it as a
   ``discord.File`` to ``settings.milestone_channel_id``, deleting the prior
   card of the SAME category first. Discord I/O is faked (channel.send /
   message.delete / member.display_avatar.read); the render/announce logic
   itself is NOT mocked. Imported lazily inside each test so a missing symbol
   surfaces as a runtime error, not a collection-time import error.

2. Wiring: an achievement unlocked through the existing on_message /
   record_message_activity flow results in exactly one card posted to the
   milestone channel; an already-unlocked achievement posts nothing. Driven
   against a real seeded JsonRepository + build_bot, in the offline style of
   tests/test_bot_wiring.py.

Assumptions pinned here (flag for the Architect):
  * ``announce_achievements`` is importable from ``n3x_bot.cards`` with the
    signature ``async (bot, settings, member, newly: list[Achievement]) -> None``.
  * The member avatar is fetched via ``member.display_avatar.read()``
    (discord.py built-in, an AsyncMock on the fakes) — NO manual aiohttp
    (this is the v3 B11 bug being avoided).
  * Prior-card tracking (in-memory-on-bot vs repo) is NOT pinned; only the
    OBSERVABLE behaviour is: the prior same-category card's ``.delete()`` is
    awaited before the new card is sent. The fake channel supports both
    "kept the sent message object" and "re-fetched by id" implementations.
  * The card is posted with a ``file=`` kwarg that is a ``discord.File``.
  * Wiring point: ``record_message_activity`` returns the newly-unlocked list
    and ``on_message`` passes ``message.author`` + that list to
    ``announce_achievements`` (where the discord member object is available).
  * ``member.bot`` guard on announce is assumed (defensive); flag if dropped.
"""

import importlib
import os
import tempfile
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
from PIL import Image

from n3x_bot.achievements import ACHIEVEMENTS
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


def _announce():
    """Lazily resolve the (not-yet-existing) announce helper."""
    return importlib.import_module("n3x_bot.cards").announce_achievements


def _ach(achievement_id: str):
    return next(a for a in ACHIEVEMENTS if a.id == achievement_id)


def _png_bytes(size=(64, 64), color=(12, 34, 56)) -> bytes:
    buf = BytesIO()
    Image.new("RGBA", size, (*color, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _member(mid=7, name="Erkan", is_bot=False):
    m = MagicMock()
    m.id = mid
    m.bot = is_bot
    m.display_name = name
    m.display_avatar = MagicMock()
    m.display_avatar.read = AsyncMock(return_value=_png_bytes())
    return m


def _milestone_channel():
    """Fake channel whose sends return message objects that carry both ``.id``
    and their own ``.delete`` AsyncMock, and whose ``fetch_message`` resolves
    those same messages by id — so the delete-prior assertion holds whether the
    impl keeps the message object or re-fetches it by id."""
    sent: list = []
    counter = {"n": 100}

    def _send(*args, **kwargs):
        counter["n"] += 1
        msg = MagicMock()
        msg.id = counter["n"]
        msg.delete = AsyncMock()
        msg.file = kwargs.get("file")
        sent.append(msg)
        return msg

    def _fetch(message_id):
        for msg in sent:
            if msg.id == message_id:
                return msg
        raise RuntimeError(f"unknown message {message_id}")

    channel = MagicMock()
    channel.send = AsyncMock(side_effect=_send)
    channel.fetch_message = AsyncMock(side_effect=_fetch)
    return channel, sent


# ── announce_achievements: happy path ──────────────────────────────────────

async def test_announce_posts_one_card_per_new_achievement():
    announce = _announce()
    repo = await _flatfile_repo()
    settings = _settings(milestone_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _milestone_channel()
    bot.get_channel = MagicMock(return_value=channel)

    newly = [_ach("a_5"), _ach("voice_3600")]
    await announce(bot, settings, _member(), newly)

    assert channel.send.await_count == 2
    await repo.close()


async def test_announce_posts_a_discord_file():
    announce = _announce()
    repo = await _flatfile_repo()
    settings = _settings(milestone_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _milestone_channel()
    bot.get_channel = MagicMock(return_value=channel)

    await announce(bot, settings, _member(), [_ach("a_5")])

    channel.send.assert_awaited_once()
    assert isinstance(sent[0].file, discord.File)
    await repo.close()


async def test_announce_reads_avatar_via_display_avatar_read():
    # Pins the v3-B11 fix: avatar comes from the built-in display_avatar.read(),
    # never a hand-rolled aiohttp GET.
    announce = _announce()
    repo = await _flatfile_repo()
    settings = _settings(milestone_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, _ = _milestone_channel()
    bot.get_channel = MagicMock(return_value=channel)

    member = _member()
    await announce(bot, settings, member, [_ach("a_5")])

    member.display_avatar.read.assert_awaited()
    await repo.close()


# ── announce_achievements: prior same-category card is deleted first ────────

async def test_announce_deletes_prior_same_category_card_before_sending():
    announce = _announce()
    repo = await _flatfile_repo()
    settings = _settings(milestone_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _milestone_channel()
    bot.get_channel = MagicMock(return_value=channel)

    member = _member()
    await announce(bot, settings, member, [_ach("a_5")])      # gate card #1
    first_card = sent[0]
    await announce(bot, settings, member, [_ach("a_10")])     # gate card #2

    # The first gate card must have been deleted; a second card was sent.
    first_card.delete.assert_awaited_once()
    assert channel.send.await_count == 2
    await repo.close()


# ── announce_achievements: distinct progression lines never collide ─────────

async def test_announce_different_gate_metrics_do_not_delete_each_other():
    # gate_a and gate_b share category "gate" but are distinct progression
    # lines; unlocking gate_b must NOT delete the still-valid gate_a card.
    announce = _announce()
    repo = await _flatfile_repo()
    settings = _settings(milestone_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _milestone_channel()
    bot.get_channel = MagicMock(return_value=channel)

    member = _member()
    await announce(bot, settings, member, [_ach("a_5")])      # gate_a card
    gate_a_card = sent[0]
    await announce(bot, settings, member, [_ach("b_5")])      # gate_b card

    assert channel.send.await_count == 2
    gate_a_card.delete.assert_not_awaited()
    await repo.close()


async def test_announce_batch_two_gate_metrics_posts_two_and_deletes_neither():
    # One gate submission can fire both a gate_<type> tier and a gate_total
    # tier in a single ``newly`` list; both cards must be posted and neither
    # deleted (the pre-fix bug posted card #1 then deleted it on iteration #2).
    announce = _announce()
    repo = await _flatfile_repo()
    settings = _settings(milestone_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _milestone_channel()
    bot.get_channel = MagicMock(return_value=channel)

    member = _member()
    await announce(bot, settings, member, [_ach("a_5"), _ach("total_50")])

    assert channel.send.await_count == 2
    for msg in sent:
        msg.delete.assert_not_awaited()
    await repo.close()


async def test_announce_batch_same_metric_two_tiers_keeps_only_highest():
    # Two tiers of the SAME metric in one batch (rare): collapse to the highest
    # tier — a single card posted, nothing post-then-deleted within the batch.
    announce = _announce()
    repo = await _flatfile_repo()
    settings = _settings(milestone_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _milestone_channel()
    bot.get_channel = MagicMock(return_value=channel)

    member = _member()
    await announce(bot, settings, member, [_ach("a_5"), _ach("a_10")])

    assert channel.send.await_count == 1
    sent[0].delete.assert_not_awaited()
    await repo.close()


# ── announce_achievements: best-effort prior-card delete never blocks send ──

async def test_announce_still_sends_when_prior_card_fetch_raises_notfound():
    announce = _announce()
    repo = await _flatfile_repo()
    settings = _settings(milestone_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _milestone_channel()
    bot.get_channel = MagicMock(return_value=channel)

    member = _member()
    await announce(bot, settings, member, [_ach("a_5")])          # store a card
    # The prior card was deleted out-of-band; re-fetching it 404s.
    resp = SimpleNamespace(status=404, reason="Not Found")
    channel.fetch_message = AsyncMock(side_effect=discord.NotFound(resp, "gone"))
    await announce(bot, settings, member, [_ach("a_10")])         # same metric

    assert channel.send.await_count == 2
    await repo.close()


async def test_announce_still_sends_when_prior_card_delete_raises_notfound():
    announce = _announce()
    repo = await _flatfile_repo()
    settings = _settings(milestone_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _milestone_channel()
    bot.get_channel = MagicMock(return_value=channel)

    member = _member()
    await announce(bot, settings, member, [_ach("a_5")])
    resp = SimpleNamespace(status=404, reason="Not Found")
    sent[0].delete = AsyncMock(side_effect=discord.NotFound(resp, "gone"))
    await announce(bot, settings, member, [_ach("a_10")])

    assert channel.send.await_count == 2
    await repo.close()


async def test_announce_still_sends_when_avatar_read_raises():
    announce = _announce()
    repo = await _flatfile_repo()
    settings = _settings(milestone_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, _ = _milestone_channel()
    bot.get_channel = MagicMock(return_value=channel)

    member = _member()
    member.display_avatar.read = AsyncMock(side_effect=RuntimeError("boom"))
    await announce(bot, settings, member, [_ach("a_5")])

    channel.send.assert_awaited_once()
    await repo.close()


# ── announce_achievements: no-op guards ────────────────────────────────────

async def test_announce_is_noop_when_milestone_channel_unset():
    announce = _announce()
    repo = await _flatfile_repo()
    settings = _settings(milestone_channel_id=0)
    bot = build_bot(settings, repo)
    channel, _ = _milestone_channel()
    bot.get_channel = MagicMock(return_value=channel)

    member = _member()
    await announce(bot, settings, member, [_ach("a_5")])

    channel.send.assert_not_called()
    member.display_avatar.read.assert_not_awaited()
    await repo.close()


async def test_announce_is_noop_for_empty_newly_list():
    announce = _announce()
    repo = await _flatfile_repo()
    settings = _settings(milestone_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, _ = _milestone_channel()
    bot.get_channel = MagicMock(return_value=channel)

    member = _member()
    await announce(bot, settings, member, [])

    channel.send.assert_not_called()
    member.display_avatar.read.assert_not_awaited()
    await repo.close()


async def test_announce_is_noop_when_channel_missing():
    announce = _announce()
    repo = await _flatfile_repo()
    settings = _settings(milestone_channel_id=4242)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)

    # Must not raise even though the configured channel can't be resolved.
    await announce(bot, settings, _member(), [_ach("a_5")])
    await repo.close()


async def test_announce_skips_bot_member():
    announce = _announce()
    repo = await _flatfile_repo()
    settings = _settings(milestone_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, _ = _milestone_channel()
    bot.get_channel = MagicMock(return_value=channel)

    await announce(bot, settings, _member(is_bot=True), [_ach("a_5")])

    channel.send.assert_not_called()
    await repo.close()


# ── wiring: unlocking through on_message posts a card ──────────────────────

def _plain_message(author, content="hallo", channel_id=555):
    msg = MagicMock()
    msg.author = author
    msg.content = content
    msg.channel = SimpleNamespace(id=channel_id)
    msg.delete = AsyncMock()
    return msg


async def test_message_event_posts_card_when_achievement_unlocks():
    repo = await _flatfile_repo()
    settings = _settings(milestone_channel_id=4242)
    bot = build_bot(settings, repo)
    bot.process_commands = AsyncMock()
    channel, sent = _milestone_channel()
    bot.get_channel = MagicMock(return_value=channel)

    # One message short of the msg_1000 threshold; the next message crosses it.
    await repo.add_activity(7, "messages", 999)

    member = _member(mid=7)
    await bot.on_message(_plain_message(member))

    channel.send.assert_awaited_once()
    assert isinstance(sent[0].file, discord.File)
    await repo.close()


async def test_message_event_posts_exactly_one_card_across_repeat_messages():
    # Pins BOTH "exactly one card per newly-unlocked achievement" and
    # "already-unlocked -> no card". Framed as a single RED test (a standalone
    # "no card" assertion would pass vacuously before the wiring exists).
    repo = await _flatfile_repo()
    settings = _settings(milestone_channel_id=4242)
    bot = build_bot(settings, repo)
    bot.process_commands = AsyncMock()
    channel, _ = _milestone_channel()
    bot.get_channel = MagicMock(return_value=channel)

    await repo.add_activity(7, "messages", 999)
    member = _member(mid=7)

    await bot.on_message(_plain_message(member))   # crosses 1000 -> unlock + 1 card
    await bot.on_message(_plain_message(member))   # 1001 -> nothing new, no card

    assert channel.send.await_count == 1
    await repo.close()
