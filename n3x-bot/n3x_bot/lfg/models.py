"""Reines LFG-Domänenmodell: Status, Validierung, Parsing der /lfg-Eingaben.

Kein Discord- und kein Repo-Import — alles hier ist ohne Bot und ohne Datenbank
testbar. Die Grenzwerte stehen bewusst als Konstanten oben, weil zwei davon aus
harten Discord-Limits folgen und nicht frei wählbar sind.
"""
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# Ein Select-Menü trägt maximal 25 Optionen — damit ist die Zahl der möglichen
# Startzeiten hart begrenzt, nicht aus Geschmack.
MAX_START_TIMES = 25
# `title` ist String(100) in der Tabelle.
TITLE_MAX = 100
# Obergrenze für max_players: die Teilnehmerliste wird in die Embed-Description
# gerendert (Discord-Limit 4096 Zeichen), darum nicht unbegrenzt.
PLAYERS_CAP = 50
# Spec: Startzeit + 1 Stunde = Cleanup-Zeitpunkt.
CLEANUP_AFTER = timedelta(hours=1)

_PLAYERS_RE = re.compile(r"^\s*(\d{1,3})\s*[-–]\s*(\d{1,3})\s*$")
_TIME_RE = re.compile(r"^\s*(\d{1,2})\s*[:.]\s*(\d{2})\s*$")
# Startzeiten dürfen mit / , ; oder Leerzeichen getrennt werden.
_TIME_SPLIT_RE = re.compile(r"[/,;]+|\s+")


class LfgStatus:
    """Status-Konstanten. Als String gespeichert (String(20) in der Tabelle)."""

    OPEN = "OPEN"
    CONFIRMED = "CONFIRMED"
    FULL = "FULL"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    ALL = (OPEN, CONFIRMED, FULL, CANCELLED, EXPIRED)
    # Zustände, in denen die LFG noch auf einen Termin wartet.
    PENDING = (OPEN,)
    # Zustände mit festem Termin und offener Teilnehmerliste.
    SETTLED = (CONFIRMED, FULL)


class LfgValidationError(ValueError):
    """Eingabefehler mit einer Meldung, die direkt an den User geht."""


@dataclass(frozen=True)
class LfgDraft:
    """Validierte /lfg-Eingaben, noch nicht persistiert."""

    title: str
    min_players: int
    max_players: int
    event_date: date
    start_times: tuple[str, ...]

    @property
    def date_key(self) -> str:
        """Speicherform des Datums (YYYY-MM-DD), wie `streak_stats`."""
        return self.event_date.isoformat()


def parse_title(raw: str) -> str:
    title = (raw or "").strip()
    if not title:
        raise LfgValidationError("❌ Bitte gib einen Titel an.")
    if len(title) > TITLE_MAX:
        raise LfgValidationError(
            f"❌ Der Titel darf maximal {TITLE_MAX} Zeichen lang sein.")
    return title


def parse_players(raw: str) -> tuple[int, int]:
    """`"4-8"` → `(4, 8)`."""
    m = _PLAYERS_RE.match(raw or "")
    if m is None:
        raise LfgValidationError(
            "❌ Spieleranzahl bitte als Bereich angeben, z. B. `4-8`.")
    minimum, maximum = int(m.group(1)), int(m.group(2))
    if minimum < 1:
        raise LfgValidationError("❌ Das Minimum muss mindestens 1 sein.")
    if maximum < minimum:
        raise LfgValidationError(
            "❌ Das Maximum darf nicht kleiner als das Minimum sein.")
    if maximum > PLAYERS_CAP:
        raise LfgValidationError(
            f"❌ Das Maximum darf höchstens {PLAYERS_CAP} sein.")
    return minimum, maximum


def parse_event_date(raw: str, *, today: date) -> date:
    """`"21.09.2026"` → `date(2026, 9, 21)`. Vergangene Tage werden abgelehnt,
    weil sie sofort in den Cleanup laufen würden."""
    try:
        parsed = datetime.strptime((raw or "").strip(), "%d.%m.%Y").date()
    except ValueError as exc:
        raise LfgValidationError(
            "❌ Datum bitte als `TT.MM.JJJJ` angeben, z. B. `21.09.2026`."
        ) from exc
    if parsed < today:
        raise LfgValidationError("❌ Das Datum liegt in der Vergangenheit.")
    return parsed


def parse_start_times(raw: str) -> tuple[str, ...]:
    """`"19:00 / 20:00 / 21:00"` → `("19:00", "20:00", "21:00")`, chronologisch.

    Doppelte Zeiten werden abgelehnt (nicht still zusammengefasst), damit der
    Ersteller seinen Tippfehler sieht.
    """
    parts = [p for p in _TIME_SPLIT_RE.split((raw or "").strip()) if p]
    if not parts:
        raise LfgValidationError(
            "❌ Bitte gib mindestens eine Startzeit an, z. B. `19:00 / 20:00`.")
    times: list[str] = []
    for part in parts:
        m = _TIME_RE.match(part)
        if m is None:
            raise LfgValidationError(
                f"❌ `{part}` ist keine gültige Uhrzeit. Format: `19:00`.")
        hour, minute = int(m.group(1)), int(m.group(2))
        if hour > 23 or minute > 59:
            raise LfgValidationError(f"❌ `{part}` ist keine gültige Uhrzeit.")
        normalised = f"{hour:02d}:{minute:02d}"
        if normalised in times:
            raise LfgValidationError(
                f"❌ Die Startzeit `{normalised}` ist doppelt angegeben.")
        times.append(normalised)
    if len(times) > MAX_START_TIMES:
        raise LfgValidationError(
            f"❌ Maximal {MAX_START_TIMES} Startzeiten "
            "(Discord-Limit für Auswahlmenüs).")
    return tuple(sorted(times))


def build_draft(*, title: str, players: str, event_date: str,
                start_times: str, today: date) -> LfgDraft:
    """Alle vier Eingaben validieren. Wirft `LfgValidationError`."""
    parsed_title = parse_title(title)
    minimum, maximum = parse_players(players)
    parsed_date = parse_event_date(event_date, today=today)
    times = parse_start_times(start_times)
    return LfgDraft(title=parsed_title, min_players=minimum,
                    max_players=maximum, event_date=parsed_date,
                    start_times=times)


def at(event_date: date, start_time: str, tz: ZoneInfo) -> datetime:
    """Zeitzonenbewusster Zeitpunkt aus Datum + `"HH:MM"`."""
    hour, minute = (int(x) for x in start_time.split(":"))
    return datetime(event_date.year, event_date.month, event_date.day,
                    hour, minute, tzinfo=tz)


def cleanup_at(event_date: date, start_time: str, tz: ZoneInfo) -> datetime:
    """Startzeit + 1 Stunde."""
    return at(event_date, start_time, tz) + CLEANUP_AFTER


def pending_cleanup_at(draft_date: date, start_times, tz: ZoneInfo) -> datetime:
    """Cleanup-Zeitpunkt einer noch unbestätigten LFG: letzte mögliche
    Startzeit + 1 Stunde. Danach kann kein Termin mehr zustande kommen."""
    return max(cleanup_at(draft_date, t, tz) for t in start_times)


def dumps_times(times) -> str:
    return json.dumps(list(times))


def loads_times(raw: str) -> list[str]:
    return list(json.loads(raw)) if raw else []
