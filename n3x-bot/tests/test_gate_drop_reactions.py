"""RED spec for the reaction-icon gate-drop confirmation flow.

FEATURE: d/e/z/k gate inputs no longer store immediately (and Kappa no longer
posts a `KappaConfirmView` button panel). Instead the bot seeds a pending entry
and adds, to the USER's own message, one reaction per drop item for that gate
PLUS a "nothing" ❌ reaction. The message author then clicks ONE icon to record
the drop instantly (no separate confirm click). After a successful store the
bot DELETES the user's message and runs the usual post-processing.

New symbols under test (none exist yet -> RED):

  * ``n3x_bot.gates.resolve_drop_emoji(guild, item) -> discord.Emoji | str``
    Resolve a drop item ("laser"/"lf4"/"havoc"/"hercules"/"lf4u") to a custom
    guild emoji by NAME, falling back to a fixed distinct unicode emoji when the
    guild has no such emoji (or is None). Never raises.

  * ``n3x_bot.bot.handle_gate_drop_confirmation(bot, repo, settings, payload)``
    Replaces ``handle_delta_confirmation``. Instant, per-reaction, author-only.

  * ``bot._pending_gate``  — replaces ``bot._pending_delta`` (this flow now
    spans d/e/z/k, so the delta-specific name is gone). Keyed by message id.

Discord I/O is faked (AsyncMock/MagicMock); the repo is a real, connected
JsonRepository (integration over mocks for anything DB-touching). New symbols
are imported lazily inside the test bodies that need them so this module always
collects (failures are missing-behaviour, not test-file import errors).

Drop-item -> guild-emoji-name map (pinned): laser->"prom", lf4->"lf4",
havoc->"havoc", hercules->"hercu", lf4u->"lf4". Drop items per gate:
d->["laser"], e->["lf4"], z->["havoc"], k->["hercules", "lf4u"].
"""

import asyncio
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from n3x_bot.bot import build_bot, handle_gate_input_message
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

NOTHING = "❌"


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


class _FakeEmoji:
    """A faithful stand-in for a custom ``discord.Emoji``: has ``.id`` and
    ``.name`` and renders as ``<:name:id>`` like the real thing (so matching by
    id OR by str both work across the reaction/payload boundary)."""

    def __init__(self, name: str, emoji_id: int):
        self.name = name
        self.id = emoji_id

    def __str__(self) -> str:
        return f"<:{self.name}:{self.id}>"


def _fake_guild(*emoji_names: str):
    """A guild whose ``.emojis`` holds a custom emoji for each given name."""
    guild = MagicMock()
    guild.emojis = [_FakeEmoji(name, 900000 + i)
                    for i, name in enumerate(emoji_names)]
    return guild


def _fake_gate_message(content: str, *, guild, message_id: int = 7001,
                       author_id: int = 7, author_name: str = "Erkan",
                       channel_id: int = 777):
    message = MagicMock()
    message.id = message_id
    message.content = content
    message.author = SimpleNamespace(id=author_id, name=author_name)
    message.guild = guild
    message.add_reaction = AsyncMock()
    message.delete = AsyncMock()
    channel = MagicMock()
    channel.id = channel_id
    channel.send = AsyncMock()
    message.channel = channel
    return message


def _fake_reaction_payload(*, message_id: int, user_id: int, emoji,
                           channel_id: int = 777):
    return SimpleNamespace(message_id=message_id, user_id=user_id,
                           emoji=emoji, channel_id=channel_id, guild_id=1)


async def _seed_input(content, *, guild, message_id=7001, author_id=7,
                      author_name="Erkan", channel_id=777):
    """Run the input handler for a d/e/z/k message and return the harness plus
    the list of reactions that were seeded onto the user's message.

    ``bot.get_channel`` is wired to a channel whose ``fetch_message`` returns the
    same message object, so a later confirmation can fetch-and-delete it.
    """
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=channel_id)
    bot = build_bot(settings, repo)
    message = _fake_gate_message(content, guild=guild, message_id=message_id,
                                 author_id=author_id, author_name=author_name,
                                 channel_id=channel_id)
    channel = MagicMock()
    channel.id = channel_id
    channel.fetch_message = AsyncMock(return_value=message)
    bot.get_channel = MagicMock(return_value=channel)
    await handle_gate_input_message(bot, repo, settings, message)
    added = [c.args[0] for c in message.add_reaction.await_args_list]
    return bot, repo, settings, message, added


async def _dispatch(bot, repo, settings, *, message_id, user_id, emoji,
                    channel_id=777):
    from n3x_bot.bot import handle_gate_drop_confirmation
    payload = _fake_reaction_payload(message_id=message_id, user_id=user_id,
                                     emoji=emoji, channel_id=channel_id)
    await handle_gate_drop_confirmation(bot, repo, settings, payload)


async def _delta_rows(repo):
    return [r for r in (await repo.export_all())["gate_entries"]
            if r["gate_type"] == "d"]


# ── resolver: custom-by-name hit vs unicode fallback vs never-raises ──────────

def test_resolve_drop_emoji_returns_custom_when_guild_has_named_emoji():
    from n3x_bot.gates import resolve_drop_emoji
    guild = _fake_guild("prom")
    resolved = resolve_drop_emoji(guild, "laser")
    assert resolved is guild.emojis[0]  # the custom emoji named "prom"


def test_resolve_drop_emoji_falls_back_to_unicode_when_emoji_absent():
    from n3x_bot.gates import resolve_drop_emoji
    guild = _fake_guild()  # no custom emojis at all
    resolved = resolve_drop_emoji(guild, "laser")
    assert isinstance(resolved, str)  # a plain unicode fallback


def test_resolve_drop_emoji_never_raises_on_none_guild():
    from n3x_bot.gates import resolve_drop_emoji
    resolved = resolve_drop_emoji(None, "laser")
    assert isinstance(resolved, str)


def test_resolve_drop_emoji_per_item_name_lookup_is_independent():
    # Guild has only "hercu" -> hercules resolves custom, lf4u (name "lf4") falls
    # back. Pins that the lookup keys off the per-item emoji NAME.
    from n3x_bot.gates import resolve_drop_emoji
    guild = _fake_guild("hercu")
    assert resolve_drop_emoji(guild, "hercules") is guild.emojis[0]
    assert isinstance(resolve_drop_emoji(guild, "lf4u"), str)


def test_kappa_unicode_fallbacks_are_distinct_from_each_other_and_cross():
    from n3x_bot.gates import resolve_drop_emoji
    guild = _fake_guild()  # forces fallbacks
    hercu = resolve_drop_emoji(guild, "hercules")
    lf4u = resolve_drop_emoji(guild, "lf4u")
    assert hercu != lf4u
    assert hercu != NOTHING
    assert lf4u != NOTHING


# ── input: d/e/z/k seed a pending entry and add drop-icon + ❌ reactions ──────

async def test_delta_input_adds_custom_emoji_when_guild_has_it():
    guild = _fake_guild("prom")
    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=7101)

    assert await repo.list_gate_costs("d") == []  # nothing stored yet
    assert any(getattr(r, "name", None) == "prom" for r in added)  # custom emoji
    assert added[-1] == NOTHING  # ❌ last

    await repo.close()


async def test_delta_input_adds_unicode_fallback_when_guild_lacks_emoji():
    guild = _fake_guild()  # no "prom"
    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=7102)

    assert len(added) == 2  # one drop icon + ❌
    assert isinstance(added[0], str)
    assert added[0] != NOTHING
    assert added[-1] == NOTHING
    # never raises even without the custom emoji present
    message.add_reaction.assert_awaited()

    await repo.close()


async def test_delta_input_registers_pending_with_gate_type_and_options():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=7103, author_id=7,
        author_name="Erkan")

    pending = bot._pending_gate[7103]
    assert pending["cost"] == 250000
    assert pending["user_id"] == 7
    assert pending["username"] == "Erkan"
    assert pending["gate_type"] == "d"
    # loose structural pin: an options mapping exists with ❌ -> None (nothing)
    # and the drop icon -> its item name.
    opts = next((v for v in pending.values() if isinstance(v, dict)), None)
    assert opts is not None
    assert NOTHING in opts and opts[NOTHING] is None
    assert opts[added[0]] == "laser"

    await repo.close()


async def test_epsilon_input_seeds_pending_and_single_drop_plus_cross():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "e 46.892", guild=guild, message_id=7104)

    assert await repo.list_gate_costs("e") == []
    assert bot._pending_gate[7104]["gate_type"] == "e"
    assert len(added) == 2  # lf4 icon + ❌
    assert added[-1] == NOTHING

    await repo.close()


async def test_zeta_input_seeds_pending_and_single_drop_plus_cross():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "z 1.234", guild=guild, message_id=7105)

    assert bot._pending_gate[7105]["gate_type"] == "z"
    assert len(added) == 2
    assert added[-1] == NOTHING

    await repo.close()


async def test_kappa_input_seeds_two_drops_plus_cross_and_sends_no_message():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "k 500", guild=guild, message_id=7106)

    assert await repo.list_gate_costs("k") == []
    assert bot._pending_gate[7106]["gate_type"] == "k"
    assert len(added) == 3  # hercules icon + lf4u icon + ❌
    assert added[-1] == NOTHING
    # Kappa no longer posts a separate KappaConfirmView panel.
    message.channel.send.assert_not_awaited()

    await repo.close()


# ── confirmation: clicking a drop icon stores that ONE item dropped=True ──────

async def test_delta_drop_click_stores_laser_dropped_true():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=7201)

    await _dispatch(bot, repo, settings, message_id=7201, user_id=7,
                    emoji=added[0])  # the laser drop icon

    assert await repo.list_gate_costs("d") == [250000]
    assert (await _delta_rows(repo))[0]["laser_dropped"] is True
    assert 7201 not in bot._pending_gate

    await repo.close()


async def test_delta_nothing_click_stores_laser_dropped_false():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=7202)

    await _dispatch(bot, repo, settings, message_id=7202, user_id=7,
                    emoji=NOTHING)

    assert await repo.list_gate_costs("d") == [250000]
    assert (await _delta_rows(repo))[0]["laser_dropped"] is False

    await repo.close()


async def test_epsilon_drop_click_stores_lf4_true():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "e 46.892", guild=guild, message_id=7203)

    await _dispatch(bot, repo, settings, message_id=7203, user_id=7,
                    emoji=added[0])

    assert await repo.list_gate_costs("e") == [46892]
    assert (await repo.gate_drop_stats("e"))["rates"]["lf4"] == 100.0

    await repo.close()


async def test_zeta_nothing_click_stores_havoc_false():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "z 1.234", guild=guild, message_id=7204)

    await _dispatch(bot, repo, settings, message_id=7204, user_id=7,
                    emoji=NOTHING)

    assert await repo.list_gate_costs("z") == [1234]
    assert (await repo.gate_drop_stats("z"))["rates"]["havoc"] == 0.0

    await repo.close()


async def test_kappa_hercules_click_stores_hercules_true_lf4u_false():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "k 500", guild=guild, message_id=7205)

    await _dispatch(bot, repo, settings, message_id=7205, user_id=7,
                    emoji=added[0])  # hercules icon

    assert await repo.list_gate_costs("k") == [500]
    rates = (await repo.gate_drop_stats("k"))["rates"]
    assert rates["hercules"] == 100.0
    assert rates["lf4u"] == 0.0

    await repo.close()


async def test_kappa_lf4u_click_stores_lf4u_true_hercules_false():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "k 500", guild=guild, message_id=7206)

    await _dispatch(bot, repo, settings, message_id=7206, user_id=7,
                    emoji=added[1])  # lf4u icon

    rates = (await repo.gate_drop_stats("k"))["rates"]
    assert rates["hercules"] == 0.0
    assert rates["lf4u"] == 100.0

    await repo.close()


async def test_kappa_nothing_click_stores_both_false():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "k 500", guild=guild, message_id=7207)

    await _dispatch(bot, repo, settings, message_id=7207, user_id=7,
                    emoji=NOTHING)

    rates = (await repo.gate_drop_stats("k"))["rates"]
    assert rates["hercules"] == 0.0
    assert rates["lf4u"] == 0.0

    await repo.close()


# ── confirmation: message deletion after a successful store ──────────────────

async def test_message_deleted_after_successful_store():
    # Deletion is now SCHEDULED with the configured delay (default .env "1m" ->
    # 60s) rather than immediate — the drop message lingers briefly like a/b/c.
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=7301)

    await _dispatch(bot, repo, settings, message_id=7301, user_id=7,
                    emoji=added[0])

    message.delete.assert_awaited_once_with(delay=60)

    await repo.close()


async def test_message_delete_delay_reflects_configured_seconds(monkeypatch):
    # The delay is driven by bot.runtime_config.gate_delete_delay_seconds, not a
    # hardcoded constant: a resolver reporting 120 yields delete(delay=120).
    from n3x_bot import bot as botmod
    monkeypatch.setattr(botmod, "update_gate_stats_embed", AsyncMock())
    monkeypatch.setattr(botmod, "update_gate_chart", AsyncMock())
    monkeypatch.setattr(botmod, "_announce_records", AsyncMock())
    monkeypatch.setattr(botmod, "check_achievements", AsyncMock(return_value=[]))
    monkeypatch.setattr(botmod, "announce_achievements", AsyncMock())

    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=7303)
    bot.runtime_config = SimpleNamespace(gate_delete_delay_seconds=120)

    await _dispatch(bot, repo, settings, message_id=7303, user_id=7,
                    emoji=added[0])

    message.delete.assert_awaited_once_with(delay=120)

    await repo.close()


async def test_store_survives_delete_failure():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=7302)
    message.delete = AsyncMock(side_effect=RuntimeError("boom"))

    # must not raise despite the delete blowing up
    await _dispatch(bot, repo, settings, message_id=7302, user_id=7,
                    emoji=added[0])

    assert await repo.list_gate_costs("d") == [250000]  # store still happened
    message.delete.assert_awaited_once_with(delay=60)

    await repo.close()


# ── confirmation guards: ignored reactions store nothing and keep pending ─────

async def test_non_author_reaction_is_ignored_then_author_can_store():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "e 46.892", guild=guild, message_id=7401, author_id=7)

    # a different user clicking the drop icon is ignored
    await _dispatch(bot, repo, settings, message_id=7401, user_id=8,
                    emoji=added[0])
    assert await repo.list_gate_costs("e") == []
    assert 7401 in bot._pending_gate            # pending preserved
    message.delete.assert_not_awaited()          # no deletion on ignore

    # the author can still confirm afterwards
    await _dispatch(bot, repo, settings, message_id=7401, user_id=7,
                    emoji=added[0])
    assert await repo.list_gate_costs("e") == [46892]

    await repo.close()


async def test_unknown_message_reaction_is_ignored():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=7402)

    await _dispatch(bot, repo, settings, message_id=999999, user_id=7,
                    emoji=added[0])

    assert await repo.list_gate_costs("d") == []
    assert 7402 in bot._pending_gate  # the real pending is untouched

    await repo.close()


async def test_non_option_emoji_is_ignored_and_pending_preserved():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=7403)

    await _dispatch(bot, repo, settings, message_id=7403, user_id=7,
                    emoji="🎉")  # neither a drop icon nor ❌

    assert await repo.list_gate_costs("d") == []
    assert 7403 in bot._pending_gate
    message.delete.assert_not_awaited()

    await repo.close()


# ── confirmation: atomic single-store on a double dispatch ────────────────────

class _SuspendingRepo:
    """add_gate_entry suspends between the dedup-check and the insert (mimicking
    SQL I/O) so two concurrent confirmations for the SAME pending message can
    genuinely interleave. The pending must be claimed exactly once."""

    def __init__(self):
        self.rows: list = []

    async def gate_record(self, gate_type):
        rows = [r for r in self.rows if r["gate_type"] == gate_type]
        if not rows:
            return None
        mn = min(rows, key=lambda r: r["cost"])
        mx = max(rows, key=lambda r: r["cost"])
        return {"min_cost": mn["cost"], "min_user": mn["user_id"],
                "max_cost": mx["cost"], "max_user": mx["user_id"]}

    async def add_gate_entry(self, gate_type, cost, user_id, username,
                             dedup_window_seconds=30, laser_dropped=None,
                             drops=None):
        for r in self.rows:
            if (r["gate_type"] == gate_type and r["cost"] == cost
                    and r["user_id"] == user_id):
                return False
        await asyncio.sleep(0.005)  # real suspension point (SQL I/O)
        self.rows.append({"gate_type": gate_type, "cost": cost,
                          "user_id": user_id, "username": username,
                          "laser_dropped": laser_dropped, "drops": drops})
        return True


async def test_double_dispatch_stores_exactly_one(monkeypatch):
    from n3x_bot import bot as botmod
    monkeypatch.setattr(botmod, "update_gate_stats_embed", AsyncMock())
    monkeypatch.setattr(botmod, "update_gate_chart", AsyncMock())
    monkeypatch.setattr(botmod, "_announce_records", AsyncMock())
    monkeypatch.setattr(botmod, "check_achievements", AsyncMock(return_value=[]))
    monkeypatch.setattr(botmod, "announce_achievements", AsyncMock())

    guild = _fake_guild()
    # seed the pending via the real input path, then swap in the suspending repo
    bot, _seed_repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=7501)
    await _seed_repo.close()
    repo = _SuspendingRepo()

    from n3x_bot.bot import handle_gate_drop_confirmation
    p_drop = _fake_reaction_payload(message_id=7501, user_id=7, emoji=added[0])
    p_nothing = _fake_reaction_payload(message_id=7501, user_id=7, emoji=NOTHING)

    await asyncio.gather(
        handle_gate_drop_confirmation(bot, repo, settings, p_drop),
        handle_gate_drop_confirmation(bot, repo, settings, p_nothing),
    )

    assert len(repo.rows) == 1               # exactly one delta stored
    assert 7501 not in bot._pending_gate     # pending claimed exactly once


# ── confirmation: custom-emoji click round-trips by id, not str form ─────────

async def test_custom_emoji_click_roundtrips_by_id_across_animated_str_mismatch():
    """A CUSTOM drop emoji must still match even when the seeded str form and the
    payload str form differ. An animated guild emoji renders ``<a:prom:123>``,
    but the reaction payload's ``PartialEmoji`` may render ``<:prom:123>`` (the
    gateway can omit ``animated``). Keying off the shared ``.id`` keeps them
    matched, so the drop is stored and the pending is claimed."""

    class _AnimatedEmoji:
        def __init__(self, name, emoji_id):
            self.name = name
            self.id = emoji_id

        def __str__(self):
            return f"<a:{self.name}:{self.id}>"

    class _PartialLikeEmoji:
        def __init__(self, name, emoji_id):
            self.name = name
            self.id = emoji_id

        def __str__(self):
            return f"<:{self.name}:{self.id}>"

    guild = MagicMock()
    guild.emojis = [_AnimatedEmoji("prom", 123)]  # DROP_EMOJI_NAMES["laser"]

    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=7701)

    assert added[0] is guild.emojis[0]            # seeded the animated custom emoji
    payload_emoji = _PartialLikeEmoji("prom", 123)
    assert str(payload_emoji) != str(added[0])    # str forms genuinely differ

    await _dispatch(bot, repo, settings, message_id=7701, user_id=7,
                    emoji=payload_emoji)

    assert await repo.list_gate_costs("d") == [250000]      # still matched + stored
    assert (await _delta_rows(repo))[0]["laser_dropped"] is True
    assert 7701 not in bot._pending_gate

    await repo.close()


# ── confirmation: dedup-rejected store gives ⏳ feedback and keeps the message ─

async def test_dedup_rejected_confirmation_adds_hourglass_and_keeps_message():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=7801, author_id=7,
        author_name="Erkan")
    # Pre-insert the identical delta so the confirmation hits a dedup rejection.
    await repo.add_gate_entry("d", 250000, 7, "Erkan", laser_dropped=True)

    await _dispatch(bot, repo, settings, message_id=7801, user_id=7,
                    emoji=added[0])

    assert await repo.list_gate_costs("d") == [250000]   # no second row
    message.delete.assert_not_awaited()                  # message kept
    reacts = [c.args[0] for c in message.add_reaction.await_args_list]
    assert "⏳" in reacts                                  # duplicate feedback
    assert 7801 not in bot._pending_gate                 # pending still claimed

    await repo.close()


# ── wiring: on_raw_reaction_add routes to the renamed handler ────────────────

async def test_on_raw_reaction_add_dispatches_to_drop_confirmation(monkeypatch):
    from n3x_bot import bot as botmod
    handler = AsyncMock()
    # raising=True (default): fails loudly until the renamed handler exists.
    monkeypatch.setattr(botmod, "handle_gate_drop_confirmation", handler)

    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    payload = _fake_reaction_payload(message_id=1, user_id=7, emoji=NOTHING)

    await bot.on_raw_reaction_add(payload)

    handler.assert_awaited_once()

    await repo.close()


# ── confirmation: post-processing (embed/chart/achievements) is invoked ───────

async def test_store_invokes_post_processing(monkeypatch):
    from n3x_bot import bot as botmod
    embed = AsyncMock()
    chart = AsyncMock()
    announce_rec = AsyncMock()
    checked = []

    async def _tracking_check(_repo, user_id, category, defs=None):
        checked.append(category)
        return []

    monkeypatch.setattr(botmod, "update_gate_stats_embed", embed)
    monkeypatch.setattr(botmod, "update_gate_chart", chart)
    monkeypatch.setattr(botmod, "_announce_records", announce_rec)
    monkeypatch.setattr(botmod, "check_achievements", _tracking_check)

    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=7601)

    await _dispatch(bot, repo, settings, message_id=7601, user_id=7,
                    emoji=added[0])

    embed.assert_awaited()
    chart.assert_awaited()
    announce_rec.assert_awaited()
    assert set(checked) == {"gate_d", "gate_total", "gate_cost_total"}

    await repo.close()


# ── confirmation: German in-channel confirmation on a successful drop store ───
#
# After a successful d/e/z/k store the bot posts a German confirmation into the
# channel, addressed to the user (``<@user_id>`` — payload.member may be None),
# naming the gate and the drop outcome (the chosen item label, or "kein Drop"
# for ❌), and schedules it to delete with the SAME configured delay. The
# architect may send via ``bot.get_channel(payload.channel_id)`` or via the
# fetched message's channel, so the capture below wires both.


def _capture_confirm(bot, message, *, channel_id=777):
    """Give both candidate channels an awaitable ``.send`` that returns the same
    confirmation message (with an awaitable ``.delete``). Returns the confirm
    message plus the ``bot.get_channel`` channel for inspection."""
    confirm = MagicMock()
    confirm.delete = AsyncMock()
    getchan = bot.get_channel(channel_id)
    getchan.send = AsyncMock(return_value=confirm)
    message.channel.send = AsyncMock(return_value=confirm)
    return confirm, getchan


def _confirm_text(getchan, message):
    for ch in (getchan, message.channel):
        if ch.send.await_count:
            return ch.send.await_args.args[0]
    return None


async def test_delta_drop_click_sends_confirmation_with_item_label():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=7901, author_id=7)
    confirm, getchan = _capture_confirm(bot, message)

    await _dispatch(bot, repo, settings, message_id=7901, user_id=7,
                    emoji=added[0])  # the laser drop icon

    text = _confirm_text(getchan, message)
    assert text is not None                 # a confirmation was sent
    assert "<@7>" in text                    # addressed to the user
    assert "registriert" in text             # German confirmation wording
    assert "Delta Gate" in text              # GATE_NAMES["d"]
    assert "Laser" in text                   # chosen drop item label
    confirm.delete.assert_awaited_once_with(delay=60)  # same configured delay

    await repo.close()


async def test_delta_nothing_click_confirmation_says_kein_drop():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=7902, author_id=7)
    confirm, getchan = _capture_confirm(bot, message)

    await _dispatch(bot, repo, settings, message_id=7902, user_id=7,
                    emoji=NOTHING)

    text = _confirm_text(getchan, message)
    assert text is not None
    assert "<@7>" in text
    assert "registriert" in text
    assert "kein Drop" in text               # ❌ outcome

    await repo.close()


async def test_kappa_hercules_click_confirmation_says_hercules():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "k 500", guild=guild, message_id=7903, author_id=7)
    confirm, getchan = _capture_confirm(bot, message)

    await _dispatch(bot, repo, settings, message_id=7903, user_id=7,
                    emoji=added[0])  # hercules icon

    text = _confirm_text(getchan, message)
    assert text is not None
    assert "Kappa Gate" in text              # GATE_NAMES["k"]
    assert "Hercules" in text                # kappa hercu label

    await repo.close()


async def test_drop_confirmation_delete_delay_reflects_configured_seconds(
        monkeypatch):
    from n3x_bot import bot as botmod
    monkeypatch.setattr(botmod, "update_gate_stats_embed", AsyncMock())
    monkeypatch.setattr(botmod, "update_gate_chart", AsyncMock())
    monkeypatch.setattr(botmod, "_announce_records", AsyncMock())
    monkeypatch.setattr(botmod, "check_achievements", AsyncMock(return_value=[]))
    monkeypatch.setattr(botmod, "announce_achievements", AsyncMock())

    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=7904, author_id=7)
    bot.runtime_config = SimpleNamespace(gate_delete_delay_seconds=120)
    confirm, getchan = _capture_confirm(bot, message)

    await _dispatch(bot, repo, settings, message_id=7904, user_id=7,
                    emoji=added[0])

    confirm.delete.assert_awaited_once_with(delay=120)

    await repo.close()


async def test_drop_confirmation_send_failure_swallowed_store_intact():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=7905, author_id=7)
    getchan = bot.get_channel(777)
    getchan.send = AsyncMock(side_effect=RuntimeError("no perms"))
    message.channel.send = AsyncMock(side_effect=RuntimeError("no perms"))

    # must not raise despite the confirmation send blowing up
    await _dispatch(bot, repo, settings, message_id=7905, user_id=7,
                    emoji=added[0])

    assert await repo.list_gate_costs("d") == [250000]  # store still happened

    await repo.close()


async def test_dedup_rejected_drop_sends_no_confirmation():
    guild = _fake_guild()
    bot, repo, settings, message, added = await _seed_input(
        "d 250.000", guild=guild, message_id=7906, author_id=7,
        author_name="Erkan")
    # Pre-insert the identical delta so the confirmation hits a dedup rejection.
    await repo.add_gate_entry("d", 250000, 7, "Erkan", laser_dropped=True)
    confirm, getchan = _capture_confirm(bot, message)

    await _dispatch(bot, repo, settings, message_id=7906, user_id=7,
                    emoji=added[0])

    # ⏳ path: no German confirmation sent to the channel
    assert _confirm_text(getchan, message) is None

    await repo.close()
