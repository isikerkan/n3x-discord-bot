"""LFG-Geschäftslogik. Kein Discord-Import.

Hier liegt bewusst alles, was die Spec ausdrücklich NICHT in Button-Callbacks
sehen will: Verfügbarkeiten verwalten, Mindestspielerzahl prüfen, Startzeit
bestätigen, Teilnehmer bestimmen, Maximum prüfen, Ablaufzeit bestimmen.

Jede Funktion nimmt `now` (und wo nötig die Zeitzone) als Argument, wie die
übrigen Module des Bots — damit ist alles ohne Warten testbar.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from n3x_bot.lfg import models
from n3x_bot.lfg.models import LfgDraft, LfgStatus
from n3x_bot.storage.base import StatsRepository


async def create(repo: StatsRepository, draft: LfgDraft, *, creator_id: int,
                 channel_id: int, now: datetime, tz: ZoneInfo) -> int:
    """Persistiere eine neue LFG und gib ihre ID zurück.

    Der Ersteller wird NICHT als Teilnehmer eingetragen (Spec). Er kann später
    über seine Verfügbarkeit automatisch Teilnehmer werden.
    """
    return await repo.create_lfg(
        creator_id=creator_id, title=draft.title, event_date=draft.date_key,
        min_players=draft.min_players, max_players=draft.max_players,
        start_times=list(draft.start_times), status=LfgStatus.OPEN,
        channel_id=channel_id, created_at=now,
        cleanup_at=models.pending_cleanup_at(draft.event_date,
                                             draft.start_times, tz))


def _event_date(lfg: dict):
    return datetime.strptime(lfg["event_date"], "%Y-%m-%d").date()


def counts(lfg: dict, availability: dict[str, list[int]]) -> dict[str, int]:
    """`{start_time: anzahl}` für JEDE mögliche Startzeit (auch die mit 0)."""
    return {t: len(availability.get(t, [])) for t in lfg["start_times"]}


def find_confirmable(lfg: dict, availability: dict[str, list[int]]) -> str | None:
    """Die Startzeit, die den Termin bekommt — oder None.

    Deterministisch: die möglichen Startzeiten werden CHRONOLOGISCH geprüft und
    die erste genommen, die das Minimum erfüllt. Damit ist das Ergebnis
    unabhängig davon, in welcher Reihenfolge zwei Interaktionen eintreffen.
    """
    for start_time in sorted(lfg["start_times"]):
        if len(availability.get(start_time, [])) >= lfg["min_players"]:
            return start_time
    return None


def status_for_roster(lfg: dict, participant_count: int) -> str:
    """CONFIRMED oder FULL, je nach Belegung."""
    return (LfgStatus.FULL if participant_count >= lfg["max_players"]
            else LfgStatus.CONFIRMED)


async def set_availability(repo: StatsRepository, lfg_id: int, discord_id: int,
                           start_times, *, now: datetime,
                           tz: ZoneInfo) -> dict:
    """Verfügbarkeit setzen (Replace-Set) und danach sofort prüfen, ob dadurch
    ein Termin zustande kommt.

    Rückgabe: `{"lfg": <aktuelle Zeile>, "confirmed": "HH:MM" | None,
    "error": str | None}`.
    """
    lfg = await repo.get_lfg(lfg_id)
    if lfg is None:
        return {"lfg": None, "confirmed": None, "error": "missing"}
    if lfg["status"] != LfgStatus.OPEN:
        # Termin steht schon — Verfügbarkeiten sind ab hier bedeutungslos.
        return {"lfg": lfg, "confirmed": None, "error": "settled"}
    wanted = [t for t in dict.fromkeys(start_times) if t in lfg["start_times"]]
    await repo.set_lfg_availability(lfg_id, discord_id, wanted)
    confirmed = await try_confirm(repo, lfg_id, now=now, tz=tz)
    return {"lfg": await repo.get_lfg(lfg_id), "confirmed": confirmed,
            "error": None}


async def try_confirm(repo: StatsRepository, lfg_id: int, *, now: datetime,
                      tz: ZoneInfo) -> str | None:
    """Termin festlegen, falls eine Startzeit das Minimum erreicht.

    Die Teilnehmer werden genau aus den Usern gebildet, die für DIESE Startzeit
    verfügbar waren. Der eigentliche Schreibvorgang ist ein Compare-and-Swap im
    Repo: nur der erste Aufrufer gewinnt, eine LFG bekommt also nie zwei
    Termine.
    """
    lfg = await repo.get_lfg(lfg_id)
    if lfg is None or lfg["status"] != LfgStatus.OPEN:
        return None
    availability = await repo.get_lfg_availability(lfg_id)
    start_time = find_confirmable(lfg, availability)
    if start_time is None:
        return None
    participants = sorted(availability.get(start_time, []))
    event_date = _event_date(lfg)
    won = await repo.confirm_lfg(
        lfg_id, start_time=start_time,
        event_at=models.at(event_date, start_time, tz),
        cleanup_at=models.cleanup_at(event_date, start_time, tz),
        status=status_for_roster(lfg, len(participants)),
        participants=participants, joined_at=now,
        expect_status=LfgStatus.OPEN)
    return start_time if won else None


async def join(repo: StatsRepository, lfg_id: int, discord_id: int, *,
               now: datetime) -> str:
    """Nachträglich beitreten. Ergebnis: `added` / `already` / `full` /
    `not_confirmed` / `missing`."""
    lfg = await repo.get_lfg(lfg_id)
    if lfg is None:
        return "missing"
    if lfg["status"] not in LfgStatus.SETTLED:
        return "not_confirmed"
    result = await repo.add_lfg_participant(
        lfg_id, discord_id, now, max_players=lfg["max_players"])
    if result == "added":
        await _sync_roster_status(repo, lfg)
    return result


async def leave(repo: StatsRepository, lfg_id: int, discord_id: int) -> str:
    """Gruppe verlassen. Ergebnis: `removed` / `not_member` / `missing`.

    Der bereits festgelegte Termin bleibt bestehen — es wird ausdrücklich KEIN
    neuer Termin gesucht (Spec). Es entsteht lediglich wieder ein freier Platz.
    """
    lfg = await repo.get_lfg(lfg_id)
    if lfg is None:
        return "missing"
    if not await repo.remove_lfg_participant(lfg_id, discord_id):
        return "not_member"
    await _sync_roster_status(repo, lfg)
    return "removed"


async def _sync_roster_status(repo: StatsRepository, lfg: dict) -> None:
    """CONFIRMED <-> FULL nachziehen. Rührt OPEN/EXPIRED/CANCELLED nicht an."""
    if lfg["status"] not in LfgStatus.SETTLED:
        return
    count = len(await repo.get_lfg_participants(lfg["id"]))
    wanted = status_for_roster(lfg, count)
    if wanted != lfg["status"]:
        await repo.set_lfg_status(lfg["id"], wanted)


async def due_for_cleanup(repo: StatsRepository, now: datetime) -> list[dict]:
    """Fällige LFGs. Die Deadline steht in der Zeile, nicht in einem Timer —
    deshalb überlebt der Cleanup einen Neustart."""
    return await repo.lfg_due_for_cleanup(now)


async def mark_expired(repo: StatsRepository, lfg_id: int) -> None:
    """Status auf EXPIRED. Die Zeile bleibt als Historie erhalten; gelöscht
    wird nur die Discord-Nachricht."""
    await repo.set_lfg_status(lfg_id, LfgStatus.EXPIRED)
