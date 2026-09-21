"""Specs for the LFG feature (freie Gruppensuche).

Covers the pure input parsing (`lfg.models`), the Discord-free business logic
(`lfg.service`), the embeds, the persistent views and the `/lfg` command
wiring, plus the restart-safe cleanup.

The service layer takes `now`/`tz` as arguments (like `timers`/`activity`), so
every confirmation, expiry and cleanup assertion here is deterministic and runs
without sleeping.
"""
import os
import tempfile
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import discord
import pytest

from n3x_bot.bot import build_bot
from n3x_bot.config import Settings
from n3x_bot.lfg import commands as lfg_commands
from n3x_bot.lfg import embeds as lfg_embeds
from n3x_bot.lfg import models, service, views
from n3x_bot.lfg.models import LfgStatus, LfgValidationError
from n3x_bot.seed import seed_defaults
from n3x_bot.storage.json_repo import JsonRepository

TZ = ZoneInfo("Europe/Berlin")
LFG_CHANNEL = 1531288209730830447

BASE_SETTINGS_KWARGS = dict(
    discord_token="tok",
    target_role_id=1,
    welcome_channel_id=2,
    reminder_channel_id=999,
    julez_id=424242,
    _env_file=None,
    _env_prefix="NONEXISTENT_",
)

# Spec-Beispiel
JULES, MAX, TOM, ALEX, SARAH = 1, 2, 3, 4, 5


def _settings(**overrides) -> Settings:
    kwargs = dict(BASE_SETTINGS_KWARGS)
    kwargs.setdefault("lfg_channel_id", LFG_CHANNEL)
    kwargs.update(overrides)
    return Settings(**kwargs)


async def _repo() -> JsonRepository:
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    repo = JsonRepository(path)
    await repo.connect()
    await seed_defaults(repo)
    return repo


def _now() -> datetime:
    return datetime(2026, 9, 21, 12, 0, tzinfo=TZ)


def _draft(**overrides) -> models.LfgDraft:
    kwargs = dict(title="Satura Gruppen Gate", players="4-8",
                  event_date="21.09.2026",
                  start_times="19:00 / 20:00 / 21:00",
                  today=date(2026, 9, 21))
    kwargs.update(overrides)
    return models.build_draft(**kwargs)


async def _create(repo, **overrides):
    draft = overrides.pop("draft", None) or _draft(**overrides)
    return await service.create(repo, draft, creator_id=JULES,
                                channel_id=LFG_CHANNEL, now=_now(), tz=TZ)


# ── models: Erstellung / Validierung ───────────────────────────────────────

async def test_build_draft_parses_all_four_inputs():
    draft = _draft()
    assert draft.title == "Satura Gruppen Gate"
    assert (draft.min_players, draft.max_players) == (4, 8)
    assert draft.event_date == date(2026, 9, 21)
    assert draft.start_times == ("19:00", "20:00", "21:00")
    assert draft.date_key == "2026-09-21"


async def test_start_times_are_sorted_chronologically():
    assert _draft(start_times="21:00 / 19:00 / 20:00").start_times == (
        "19:00", "20:00", "21:00")


async def test_start_times_accept_comma_and_space_separators():
    assert _draft(start_times="19:00, 20:00").start_times == ("19:00", "20:00")


async def test_start_times_are_zero_padded():
    assert _draft(start_times="9:00 / 9:30").start_times == ("09:00", "09:30")


@pytest.mark.parametrize("players", ["8-4", "0-8", "abc", "4", "4-51", ""])
async def test_invalid_player_ranges_are_rejected(players):
    with pytest.raises(LfgValidationError):
        _draft(players=players)


@pytest.mark.parametrize("times", ["", "25:00", "19:70", "abends", "19-00"])
async def test_invalid_times_are_rejected(times):
    with pytest.raises(LfgValidationError):
        _draft(start_times=times)


async def test_duplicate_times_are_rejected():
    # Spec: doppelte Zeiten verhindern — nicht still zusammenfassen.
    with pytest.raises(LfgValidationError) as exc:
        _draft(start_times="20:00 / 20:00")
    assert "doppelt" in str(exc.value)


async def test_more_than_25_start_times_is_rejected():
    # Harte Discord-Grenze für Select-Optionen.
    too_many = " / ".join(f"{h:02d}:{m:02d}"
                          for h in range(13) for m in (0, 30))
    assert len(too_many.split(" / ")) > models.MAX_START_TIMES
    with pytest.raises(LfgValidationError):
        _draft(start_times=too_many)


async def test_exactly_25_start_times_is_accepted():
    ok = " / ".join(f"{h:02d}:00" for h in range(25 - 1))
    ok += " / 23:30"
    assert len(_draft(start_times=ok).start_times) == models.MAX_START_TIMES


@pytest.mark.parametrize("raw", ["2026-09-21", "21/09/2026", "32.09.2026", ""])
async def test_invalid_dates_are_rejected(raw):
    with pytest.raises(LfgValidationError):
        _draft(event_date=raw)


async def test_past_date_is_rejected():
    with pytest.raises(LfgValidationError):
        _draft(event_date="20.09.2026", today=date(2026, 9, 21))


async def test_empty_title_is_rejected():
    with pytest.raises(LfgValidationError):
        _draft(title="   ")


async def test_overlong_title_is_rejected():
    with pytest.raises(LfgValidationError):
        _draft(title="x" * (models.TITLE_MAX + 1))


async def test_cleanup_is_one_hour_after_the_start_time():
    assert models.cleanup_at(date(2026, 9, 21), "20:00", TZ) == datetime(
        2026, 9, 21, 21, 0, tzinfo=TZ)


async def test_pending_cleanup_uses_the_last_possible_start_time():
    # Unbestätigt: erst wenn die LETZTE mögliche Zeit +1h vorbei ist, kann
    # kein Termin mehr zustande kommen.
    assert models.pending_cleanup_at(
        date(2026, 9, 21), ("19:00", "20:00", "21:00"), TZ) == datetime(
            2026, 9, 21, 22, 0, tzinfo=TZ)


# ── service: Erstellung ────────────────────────────────────────────────────

async def test_create_stores_an_open_lfg():
    repo = await _repo()
    lfg = await repo.get_lfg(await _create(repo))
    assert lfg["status"] == LfgStatus.OPEN
    assert lfg["title"] == "Satura Gruppen Gate"
    assert lfg["min_players"] == 4 and lfg["max_players"] == 8
    assert lfg["start_times"] == ["19:00", "20:00", "21:00"]
    await repo.close()


async def test_creator_is_not_automatically_a_participant():
    repo = await _repo()
    lfg_id = await _create(repo)
    assert await repo.get_lfg_participants(lfg_id) == []
    await repo.close()


async def test_create_sets_the_pending_cleanup_deadline():
    repo = await _repo()
    lfg = await repo.get_lfg(await _create(repo))
    assert lfg["cleanup_at"] == datetime(2026, 9, 21, 22, 0, tzinfo=TZ)
    await repo.close()


# ── service: Verfügbarkeit ─────────────────────────────────────────────────

async def test_user_can_select_one_time():
    repo = await _repo()
    lfg_id = await _create(repo)
    await service.set_availability(repo, lfg_id, MAX, ["20:00"], now=_now(), tz=TZ)
    assert await repo.get_lfg_availability(lfg_id) == {"20:00": [MAX]}
    await repo.close()


async def test_user_can_select_multiple_times():
    repo = await _repo()
    lfg_id = await _create(repo)
    await service.set_availability(repo, lfg_id, JULES, ["19:00", "20:00"],
                                   now=_now(), tz=TZ)
    assert await repo.get_lfg_availability(lfg_id) == {
        "19:00": [JULES], "20:00": [JULES]}
    await repo.close()


async def test_user_can_change_their_selection():
    repo = await _repo()
    lfg_id = await _create(repo)
    await service.set_availability(repo, lfg_id, MAX, ["19:00"], now=_now(), tz=TZ)
    await service.set_availability(repo, lfg_id, MAX, ["21:00"], now=_now(), tz=TZ)
    assert await repo.get_lfg_availability(lfg_id) == {"21:00": [MAX]}
    await repo.close()


async def test_user_can_remove_their_availability():
    repo = await _repo()
    lfg_id = await _create(repo)
    await service.set_availability(repo, lfg_id, MAX, ["19:00"], now=_now(), tz=TZ)
    await service.set_availability(repo, lfg_id, MAX, [], now=_now(), tz=TZ)
    assert await repo.get_lfg_availability(lfg_id) == {}
    await repo.close()


async def test_unknown_start_times_are_ignored():
    repo = await _repo()
    lfg_id = await _create(repo)
    await service.set_availability(repo, lfg_id, MAX, ["20:00", "03:00"],
                                   now=_now(), tz=TZ)
    assert await repo.get_lfg_availability(lfg_id) == {"20:00": [MAX]}
    await repo.close()


async def test_availability_is_refused_once_a_date_is_fixed():
    repo = await _repo()
    lfg_id = await _create(repo, players="1-8")
    await service.set_availability(repo, lfg_id, MAX, ["20:00"], now=_now(), tz=TZ)
    result = await service.set_availability(repo, lfg_id, TOM, ["19:00"],
                                            now=_now(), tz=TZ)
    assert result["error"] == "settled"
    await repo.close()


# ── service: Terminfindung ─────────────────────────────────────────────────

async def test_below_minimum_stays_open():
    repo = await _repo()
    lfg_id = await _create(repo)
    for uid in (JULES, MAX, TOM):
        await service.set_availability(repo, lfg_id, uid, ["20:00"],
                                       now=_now(), tz=TZ)
    lfg = await repo.get_lfg(lfg_id)
    assert lfg["status"] == LfgStatus.OPEN
    assert lfg["confirmed_time"] is None
    await repo.close()


async def test_reaching_the_minimum_confirms_that_time():
    repo = await _repo()
    lfg_id = await _create(repo)
    for uid in (JULES, MAX, TOM, ALEX):
        await service.set_availability(repo, lfg_id, uid, ["20:00"],
                                       now=_now(), tz=TZ)
    lfg = await repo.get_lfg(lfg_id)
    assert lfg["status"] == LfgStatus.CONFIRMED
    assert lfg["confirmed_time"] == "20:00"
    await repo.close()


async def test_confirmation_sets_event_at_and_cleanup_one_hour_later():
    repo = await _repo()
    lfg_id = await _create(repo)
    for uid in (JULES, MAX, TOM, ALEX):
        await service.set_availability(repo, lfg_id, uid, ["20:00"],
                                       now=_now(), tz=TZ)
    lfg = await repo.get_lfg(lfg_id)
    assert lfg["event_at"] == datetime(2026, 9, 21, 20, 0, tzinfo=TZ)
    assert lfg["cleanup_at"] == datetime(2026, 9, 21, 21, 0, tzinfo=TZ)
    await repo.close()


async def test_the_spec_example_confirms_2000_with_four_participants():
    # Minimum 4. Jules 19+20, Max 20, Tom 20, Alex 20, Sarah 21.
    # -> 20:00 wird bestätigt; Teilnehmer Jules/Max/Tom/Alex; Sarah NICHT.
    repo = await _repo()
    lfg_id = await _create(repo)
    await service.set_availability(repo, lfg_id, JULES, ["19:00", "20:00"],
                                   now=_now(), tz=TZ)
    await service.set_availability(repo, lfg_id, MAX, ["20:00"], now=_now(), tz=TZ)
    await service.set_availability(repo, lfg_id, TOM, ["20:00"], now=_now(), tz=TZ)
    await service.set_availability(repo, lfg_id, SARAH, ["21:00"], now=_now(), tz=TZ)
    # noch offen: 20:00 steht bei 3
    assert (await repo.get_lfg(lfg_id))["status"] == LfgStatus.OPEN
    await service.set_availability(repo, lfg_id, ALEX, ["20:00"], now=_now(), tz=TZ)

    lfg = await repo.get_lfg(lfg_id)
    assert lfg["status"] == LfgStatus.CONFIRMED
    assert lfg["confirmed_time"] == "20:00"
    assert await repo.get_lfg_participants(lfg_id) == [JULES, MAX, TOM, ALEX]
    assert SARAH not in await repo.get_lfg_participants(lfg_id)
    await repo.close()


async def test_only_users_available_for_the_confirmed_time_become_participants():
    repo = await _repo()
    lfg_id = await _create(repo, players="2-8")
    await service.set_availability(repo, lfg_id, SARAH, ["21:00"], now=_now(), tz=TZ)
    await service.set_availability(repo, lfg_id, MAX, ["20:00"], now=_now(), tz=TZ)
    await service.set_availability(repo, lfg_id, TOM, ["20:00"], now=_now(), tz=TZ)
    assert await repo.get_lfg_participants(lfg_id) == [MAX, TOM]
    await repo.close()


async def test_earliest_qualifying_time_wins_deterministically():
    # Zwei Zeiten erfüllen das Minimum gleichzeitig -> chronologisch die erste.
    repo = await _repo()
    lfg_id = await _create(repo, players="2-8")
    lfg = await repo.get_lfg(lfg_id)
    availability = {"19:00": [1, 2], "20:00": [3, 4], "21:00": [5, 6]}
    assert service.find_confirmable(lfg, availability) == "19:00"
    await repo.close()


async def test_a_second_confirm_attempt_cannot_change_the_date():
    repo = await _repo()
    lfg_id = await _create(repo, players="2-8")
    await service.set_availability(repo, lfg_id, MAX, ["20:00"], now=_now(), tz=TZ)
    await service.set_availability(repo, lfg_id, TOM, ["20:00"], now=_now(), tz=TZ)
    assert (await repo.get_lfg(lfg_id))["confirmed_time"] == "20:00"
    # ein erneuter Versuch (z. B. aus einer parallelen Interaktion) ist ein No-op
    assert await service.try_confirm(repo, lfg_id, now=_now(), tz=TZ) is None
    assert (await repo.get_lfg(lfg_id))["confirmed_time"] == "20:00"
    await repo.close()


async def test_confirmation_can_land_straight_in_full():
    repo = await _repo()
    lfg_id = await _create(repo, players="2-2")
    await service.set_availability(repo, lfg_id, MAX, ["20:00"], now=_now(), tz=TZ)
    await service.set_availability(repo, lfg_id, TOM, ["20:00"], now=_now(), tz=TZ)
    assert (await repo.get_lfg(lfg_id))["status"] == LfgStatus.FULL
    await repo.close()


async def test_creator_becomes_participant_through_own_availability():
    repo = await _repo()
    lfg_id = await _create(repo)  # creator == JULES
    for uid in (JULES, MAX, TOM, ALEX):
        await service.set_availability(repo, lfg_id, uid, ["20:00"],
                                       now=_now(), tz=TZ)
    assert JULES in await repo.get_lfg_participants(lfg_id)
    await repo.close()


async def test_creator_stays_out_when_giving_no_availability():
    repo = await _repo()
    lfg_id = await _create(repo)  # creator == JULES
    for uid in (MAX, TOM, ALEX, SARAH):
        await service.set_availability(repo, lfg_id, uid, ["20:00"],
                                       now=_now(), tz=TZ)
    participants = await repo.get_lfg_participants(lfg_id)
    assert participants == [MAX, TOM, ALEX, SARAH]
    assert JULES not in participants
    await repo.close()


# ── service: nachträglich beitreten / verlassen ────────────────────────────

async def _confirmed(repo, *, players="4-8"):
    lfg_id = await _create(repo, players=players)
    for uid in (JULES, MAX, TOM, ALEX):
        await service.set_availability(repo, lfg_id, uid, ["20:00"],
                                       now=_now(), tz=TZ)
    return lfg_id


async def test_further_players_can_join_after_confirmation():
    repo = await _repo()
    lfg_id = await _confirmed(repo)
    assert await service.join(repo, lfg_id, SARAH, now=_now()) == "added"
    assert len(await repo.get_lfg_participants(lfg_id)) == 5
    await repo.close()


async def test_join_cannot_exceed_the_maximum():
    repo = await _repo()
    lfg_id = await _confirmed(repo, players="4-5")
    await service.join(repo, lfg_id, SARAH, now=_now())          # 5/5
    assert await service.join(repo, lfg_id, 6, now=_now()) == "full"
    assert len(await repo.get_lfg_participants(lfg_id)) == 5
    await repo.close()


async def test_reaching_the_maximum_sets_full():
    repo = await _repo()
    lfg_id = await _confirmed(repo, players="4-5")
    await service.join(repo, lfg_id, SARAH, now=_now())
    assert (await repo.get_lfg(lfg_id))["status"] == LfgStatus.FULL
    await repo.close()


async def test_joining_twice_is_refused():
    repo = await _repo()
    lfg_id = await _confirmed(repo)
    assert await service.join(repo, lfg_id, JULES, now=_now()) == "already"
    await repo.close()


async def test_join_before_confirmation_is_refused():
    repo = await _repo()
    lfg_id = await _create(repo)
    assert await service.join(repo, lfg_id, SARAH, now=_now()) == "not_confirmed"
    await repo.close()


async def test_participant_can_leave():
    repo = await _repo()
    lfg_id = await _confirmed(repo)
    assert await service.leave(repo, lfg_id, JULES) == "removed"
    assert await repo.get_lfg_participants(lfg_id) == [MAX, TOM, ALEX]
    await repo.close()


async def test_leaving_keeps_the_fixed_date():
    # Spec: nach der Terminfindung wird NIE ein neuer Termin gesucht.
    repo = await _repo()
    lfg_id = await _confirmed(repo)
    await service.leave(repo, lfg_id, JULES)
    await service.leave(repo, lfg_id, MAX)
    lfg = await repo.get_lfg(lfg_id)
    assert lfg["confirmed_time"] == "20:00"
    assert lfg["status"] == LfgStatus.CONFIRMED
    assert lfg["event_at"] == datetime(2026, 9, 21, 20, 0, tzinfo=TZ)
    await repo.close()


async def test_leaving_a_full_group_reopens_a_seat():
    repo = await _repo()
    lfg_id = await _confirmed(repo, players="4-4")
    assert (await repo.get_lfg(lfg_id))["status"] == LfgStatus.FULL
    await service.leave(repo, lfg_id, JULES)
    assert (await repo.get_lfg(lfg_id))["status"] == LfgStatus.CONFIRMED
    assert await service.join(repo, lfg_id, SARAH, now=_now()) == "added"
    await repo.close()


async def test_leaving_when_not_a_member_is_refused():
    repo = await _repo()
    lfg_id = await _confirmed(repo)
    assert await service.leave(repo, lfg_id, 999) == "not_member"
    await repo.close()


# ── embeds ─────────────────────────────────────────────────────────────────

async def test_open_embed_lists_every_time_with_counts():
    repo = await _repo()
    lfg_id = await _create(repo)
    await service.set_availability(repo, lfg_id, JULES, ["19:00", "20:00"],
                                   now=_now(), tz=TZ)
    lfg = await repo.get_lfg(lfg_id)
    availability = await repo.get_lfg_availability(lfg_id)
    embed = lfg_embeds.build_open_embed(lfg, service.counts(lfg, availability))
    assert "🔎 LFG – Satura Gruppen Gate" == embed.title
    assert "21.09.2026" in embed.description
    assert "4–8 Spieler" in embed.description
    assert "19:00 — 1/8" in embed.description
    assert "20:00 — 1/8" in embed.description
    assert "21:00 — 0/8" in embed.description
    await repo.close()


async def test_confirmed_embed_shows_date_time_and_roster():
    repo = await _repo()
    lfg_id = await _confirmed(repo)
    lfg = await repo.get_lfg(lfg_id)
    embed = lfg_embeds.build_confirmed_embed(
        lfg, await repo.get_lfg_participants(lfg_id))
    assert embed.title == "🎉 TERMIN GEFUNDEN"
    assert "20:00 Uhr" in embed.description
    assert "Teilnehmer: 4/8" in embed.description
    assert f"<@{JULES}>" in embed.description
    await repo.close()


async def test_full_embed_says_the_group_is_full():
    repo = await _repo()
    lfg_id = await _confirmed(repo, players="4-4")
    lfg = await repo.get_lfg(lfg_id)
    embed = lfg_embeds.build_confirmed_embed(
        lfg, await repo.get_lfg_participants(lfg_id))
    assert embed.title == "🔒 GRUPPE VOLL"
    assert "voll" in embed.description
    await repo.close()


async def test_help_embed_explains_the_flow():
    embed = lfg_embeds.build_help_embed()
    assert "LFG" in embed.title
    for fragment in ("/lfg", "Startzeiten", "Mindestspielerzahl", "Beitreten"):
        assert fragment in embed.description


# ── views: persistent, per-message resolution, Discord limits ──────────────

async def test_open_view_has_a_select_with_one_option_per_time():
    repo = await _repo()
    lfg = await repo.get_lfg(await _create(repo))
    view = views.LfgOpenView(repo, _settings(), lfg=lfg)
    select = view.children[0]
    assert isinstance(select, discord.ui.Select)
    assert [o.value for o in select.options] == ["19:00", "20:00", "21:00"]
    assert select.custom_id == views.AVAIL_SELECT_ID
    await repo.close()


async def test_select_allows_clearing_and_multi_select():
    repo = await _repo()
    lfg = await repo.get_lfg(await _create(repo))
    select = views.LfgOpenView(repo, _settings(), lfg=lfg).children[0]
    assert select.min_values == 0        # Verfügbarkeit entfernen
    assert select.max_values == 3        # alle Zeiten gleichzeitig
    await repo.close()


async def test_select_never_exceeds_the_discord_option_limit():
    repo = await _repo()
    many = " / ".join(f"{h:02d}:00" for h in range(24))
    lfg = await repo.get_lfg(await _create(repo, start_times=many))
    select = views.LfgOpenView(repo, _settings(), lfg=lfg).children[0]
    assert len(select.options) <= models.MAX_START_TIMES
    await repo.close()


async def test_views_are_persistent():
    repo = await _repo()
    assert views.LfgOpenView(repo, _settings()).timeout is None
    assert views.LfgConfirmedView(repo, _settings()).timeout is None
    await repo.close()


async def test_confirmed_view_has_join_and_leave_with_fixed_custom_ids():
    repo = await _repo()
    view = views.LfgConfirmedView(repo, _settings())
    ids = {c.custom_id for c in view.children}
    assert ids == {views.JOIN_BUTTON_ID, views.LEAVE_BUTTON_ID}
    await repo.close()


async def test_join_button_is_disabled_when_full():
    repo = await _repo()
    view = views.LfgConfirmedView(repo, _settings(), full=True)
    join = next(c for c in view.children if c.custom_id == views.JOIN_BUTTON_ID)
    assert join.disabled is True
    await repo.close()


async def test_join_button_is_enabled_when_seats_remain():
    repo = await _repo()
    view = views.LfgConfirmedView(repo, _settings(), full=False)
    join = next(c for c in view.children if c.custom_id == views.JOIN_BUTTON_ID)
    assert join.disabled is False
    await repo.close()


async def test_render_switches_view_type_on_confirmation():
    repo = await _repo()
    lfg_id = await _create(repo)
    _, view = await views.render(repo, _settings(), await repo.get_lfg(lfg_id))
    assert isinstance(view, views.LfgOpenView)
    for uid in (JULES, MAX, TOM, ALEX):
        await service.set_availability(repo, lfg_id, uid, ["20:00"],
                                       now=_now(), tz=TZ)
    embed, view = await views.render(repo, _settings(),
                                     await repo.get_lfg(lfg_id))
    assert isinstance(view, views.LfgConfirmedView)
    assert embed.title == "🎉 TERMIN GEFUNDEN"
    await repo.close()


# ── cleanup: restart-safe, Startzeit + 1 h ─────────────────────────────────

def _bot_with_channel(message=None):
    """Fake bot whose LFG channel yields `message` on fetch_message."""
    msg = message or SimpleNamespace(delete=AsyncMock(), edit=AsyncMock())
    channel = MagicMock()
    channel.id = LFG_CHANNEL          # echte int, landet in der DB
    channel.fetch_message = AsyncMock(return_value=msg)
    channel.send = AsyncMock(return_value=SimpleNamespace(id=777001))
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)
    bot._msg = msg
    bot._channel = channel
    return bot


async def test_confirmed_lfg_is_not_cleaned_before_start_plus_one_hour():
    repo = await _repo()
    lfg_id = await _confirmed(repo)
    await repo.set_lfg_message(lfg_id, 777001, LFG_CHANNEL)
    bot = _bot_with_channel()
    at_2059 = datetime(2026, 9, 21, 20, 59, tzinfo=TZ)

    assert await lfg_commands.run_lfg_cleanup(bot, repo, _settings(), at_2059) == 0
    bot._msg.delete.assert_not_awaited()
    assert (await repo.get_lfg(lfg_id))["status"] == LfgStatus.CONFIRMED
    await repo.close()


async def test_confirmed_lfg_is_cleaned_exactly_one_hour_after_the_start():
    repo = await _repo()
    lfg_id = await _confirmed(repo)
    await repo.set_lfg_message(lfg_id, 777001, LFG_CHANNEL)
    bot = _bot_with_channel()
    at_2100 = datetime(2026, 9, 21, 21, 0, tzinfo=TZ)

    assert await lfg_commands.run_lfg_cleanup(bot, repo, _settings(), at_2100) == 1
    bot._msg.delete.assert_awaited_once()
    assert (await repo.get_lfg(lfg_id))["status"] == LfgStatus.EXPIRED
    await repo.close()


async def test_unconfirmed_lfg_expires_after_its_last_possible_time():
    repo = await _repo()
    lfg_id = await _create(repo)  # nie bestätigt, Zeiten bis 21:00
    await repo.set_lfg_message(lfg_id, 777001, LFG_CHANNEL)
    bot = _bot_with_channel()

    # 21:59 -> letzte Zeit +1h noch nicht erreicht
    assert await lfg_commands.run_lfg_cleanup(
        bot, repo, _settings(), datetime(2026, 9, 21, 21, 59, tzinfo=TZ)) == 0
    # 22:00 -> abgelaufen
    assert await lfg_commands.run_lfg_cleanup(
        bot, repo, _settings(), datetime(2026, 9, 21, 22, 0, tzinfo=TZ)) == 1
    assert (await repo.get_lfg(lfg_id))["status"] == LfgStatus.EXPIRED
    bot._msg.delete.assert_awaited_once()
    await repo.close()


async def test_expired_record_is_kept_as_history():
    repo = await _repo()
    lfg_id = await _confirmed(repo)
    await repo.set_lfg_message(lfg_id, 777001, LFG_CHANNEL)
    await lfg_commands.run_lfg_cleanup(
        _bot_with_channel(), repo, _settings(),
        datetime(2026, 9, 21, 21, 0, tzinfo=TZ))
    assert await repo.get_lfg(lfg_id) is not None   # Zeile bleibt
    await repo.close()


async def test_cleanup_runs_only_once_per_lfg():
    repo = await _repo()
    lfg_id = await _confirmed(repo)
    await repo.set_lfg_message(lfg_id, 777001, LFG_CHANNEL)
    bot = _bot_with_channel()
    late = datetime(2026, 9, 22, 12, 0, tzinfo=TZ)
    assert await lfg_commands.run_lfg_cleanup(bot, repo, _settings(), late) == 1
    assert await lfg_commands.run_lfg_cleanup(bot, repo, _settings(), late) == 0
    await repo.close()


async def test_cleanup_catches_up_after_a_restart():
    # Der Deadline steht in der DB, nicht in einem Task: ein FRISCHER Bot
    # (= Neustart) holt alles nach, was währenddessen fällig wurde.
    repo = await _repo()
    lfg_id = await _confirmed(repo)
    await repo.set_lfg_message(lfg_id, 777001, LFG_CHANNEL)

    restarted = _bot_with_channel()
    much_later = datetime(2026, 9, 23, 9, 0, tzinfo=TZ)
    assert await lfg_commands.run_lfg_cleanup(
        restarted, repo, _settings(), much_later) == 1
    restarted._msg.delete.assert_awaited_once()
    assert (await repo.get_lfg(lfg_id))["status"] == LfgStatus.EXPIRED
    await repo.close()


async def test_cleanup_survives_an_already_deleted_message():
    repo = await _repo()
    lfg_id = await _confirmed(repo)
    await repo.set_lfg_message(lfg_id, 777001, LFG_CHANNEL)
    bot = _bot_with_channel()
    bot._channel.fetch_message = AsyncMock(side_effect=RuntimeError("gone"))
    # darf nicht werfen, und der Status muss trotzdem gesetzt werden
    assert await lfg_commands.run_lfg_cleanup(
        bot, repo, _settings(), datetime(2026, 9, 22, tzinfo=TZ)) == 1
    assert (await repo.get_lfg(lfg_id))["status"] == LfgStatus.EXPIRED
    await repo.close()


async def test_cleanup_loop_is_guarded_against_double_start():
    repo = await _repo()
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=None)
    loop = lfg_commands.start_lfg_cleanup_loop(bot, repo, _settings())
    second = lfg_commands.start_lfg_cleanup_loop(bot, repo, _settings())
    try:
        assert loop.is_running() is True
        assert second is loop
    finally:
        loop.cancel()
    await repo.close()


# ── permanente Anleitung ───────────────────────────────────────────────────

async def test_help_is_posted_once_and_tracked():
    repo = await _repo()
    bot = _bot_with_channel()
    bot.runtime_config = SimpleNamespace(lfg_channel_id=LFG_CHANNEL)
    await lfg_commands.update_lfg_help(bot, repo, _settings())
    bot._channel.send.assert_awaited_once()
    stored = await repo.get_channel_message(lfg_commands.LFG_HELP_KEY)
    assert stored is not None and stored[0] == 777001
    await repo.close()


async def test_help_is_edited_not_reposted_on_restart():
    repo = await _repo()
    bot = _bot_with_channel()
    bot.runtime_config = SimpleNamespace(lfg_channel_id=LFG_CHANNEL)
    await lfg_commands.update_lfg_help(bot, repo, _settings())
    bot._channel.send.reset_mock()
    await lfg_commands.update_lfg_help(bot, repo, _settings())
    bot._channel.send.assert_not_awaited()      # keine zweite Anleitung
    bot._msg.edit.assert_awaited()               # sondern in place editiert
    await repo.close()


async def test_help_is_skipped_without_a_configured_channel():
    repo = await _repo()
    bot = _bot_with_channel()
    bot.runtime_config = SimpleNamespace(lfg_channel_id=0)
    await lfg_commands.update_lfg_help(bot, repo, _settings(lfg_channel_id=0))
    bot._channel.send.assert_not_awaited()
    await repo.close()


# ── command wiring ─────────────────────────────────────────────────────────

def _interaction(user_id=JULES, channel_id=LFG_CHANNEL):
    it = MagicMock()
    it.user = SimpleNamespace(id=user_id, roles=[])
    it.channel_id = channel_id
    it.response = MagicMock()
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.followup = MagicMock()
    it.followup.send = AsyncMock()
    it.delete_original_response = AsyncMock()
    return it


def _sent_text(interaction) -> str:
    parts = []
    for mock in (interaction.response.send_message, interaction.followup.send):
        for call in mock.await_args_list:
            if call.args:
                parts.append(str(call.args[0]))
    return " ".join(parts)


async def test_build_bot_registers_lfg_as_slash_only():
    repo = await _repo()
    bot = build_bot(_settings(), repo)
    assert bot.get_command("lfg") is None
    assert bot.tree.get_command("lfg") is not None
    await repo.close()


async def test_register_lfg_commands_is_idempotent():
    repo = await _repo()
    bot = build_bot(_settings(), repo)
    lfg_commands.register_lfg_commands(bot, repo, _settings())
    assert bot.tree.get_command("lfg") is not None
    await repo.close()


async def test_lfg_outside_the_lfg_channel_is_refused():
    repo = await _repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=MagicMock())
    interaction = _interaction(channel_id=123456789)

    await bot.tree.get_command("lfg").callback(
        interaction, titel="Satura Gruppen Gate", spieler="4-8",
        datum="21.09.2026", startzeiten="19:00 / 20:00")

    assert await repo.all_active_lfgs() == []       # nichts angelegt
    assert str(LFG_CHANNEL) in _sent_text(interaction)  # Hinweis auf Channel
    await repo.close()


async def test_lfg_in_the_right_channel_creates_and_posts():
    repo = await _repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    channel = MagicMock()
    channel.id = LFG_CHANNEL
    channel.send = AsyncMock(return_value=SimpleNamespace(id=888001))
    bot.get_channel = MagicMock(return_value=channel)
    interaction = _interaction()

    await bot.tree.get_command("lfg").callback(
        interaction, titel="Satura Gruppen Gate", spieler="4-8",
        datum="21.09.2026", startzeiten="19:00 / 20:00 / 21:00")

    active = await repo.all_active_lfgs()
    assert len(active) == 1
    assert active[0]["title"] == "Satura Gruppen Gate"
    assert active[0]["message_id"] == 888001      # Nachricht gebunden
    channel.send.assert_awaited_once()
    interaction.delete_original_response.assert_awaited()
    await repo.close()


async def test_lfg_rejects_invalid_input_without_creating_anything():
    repo = await _repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=MagicMock())
    interaction = _interaction()

    await bot.tree.get_command("lfg").callback(
        interaction, titel="Satura", spieler="8-4",
        datum="21.09.2026", startzeiten="19:00")

    assert await repo.all_active_lfgs() == []
    assert "Maximum" in _sent_text(interaction)
    await repo.close()


# ── restore after restart ──────────────────────────────────────────────────

async def test_restore_registers_both_views_and_refreshes_messages():
    repo = await _repo()
    lfg_id = await _confirmed(repo)
    await repo.set_lfg_message(lfg_id, 777001, LFG_CHANNEL)
    bot = _bot_with_channel()
    bot.add_view = MagicMock()

    await lfg_commands.restore_lfg_views(bot, repo, _settings())

    registered = [type(c.args[0]) for c in bot.add_view.call_args_list]
    assert views.LfgOpenView in registered
    assert views.LfgConfirmedView in registered
    bot._msg.edit.assert_awaited()     # Zustand neu gerendert
    await repo.close()


async def test_restore_skips_expired_lfgs():
    repo = await _repo()
    lfg_id = await _confirmed(repo)
    await repo.set_lfg_message(lfg_id, 777001, LFG_CHANNEL)
    await repo.set_lfg_status(lfg_id, LfgStatus.EXPIRED)
    bot = _bot_with_channel()
    bot.add_view = MagicMock()

    await lfg_commands.restore_lfg_views(bot, repo, _settings())

    bot._msg.edit.assert_not_awaited()
    await repo.close()
