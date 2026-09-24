"""Pure `/lfg` input parsing. No Discord, no repo.

Times are entered in the zone of the channel where `/lfg` is used and turned
into UTC instants here. Local times that do not exist or exist twice on a DST
transition day are rejected rather than silently shifted.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
MAX_SLOTS = 25          # one select menu
TITLE_MAX = 100         # gf_events.title
PLAYERS_CAP = 50        # the roster is rendered into the embed (4096 chars)
# Voting on a time closes 5 minutes before it, so a nearer time could never be
# voted on.
MIN_LEAD = timedelta(minutes=5)

_PLAYERS_RE = re.compile(r"^\s*(\d{1,3})\s*[-–]\s*(\d{1,3})\s*$")
_TIME_RE = re.compile(r"^(\d{1,2})[:.](\d{2})$")
_SPLIT_RE = re.compile(r"[/,;]+|\s+")


class GfValidationError(ValueError):
    """An input error whose message goes straight to the user."""


@dataclass(frozen=True)
class Draft:
    title: str
    min_players: int
    max_players: int
    slots: tuple[datetime, ...]      # UTC, ascending


def parse_title(raw: str) -> str:
    title = (raw or "").strip()
    if not title:
        raise GfValidationError("❌ Please give your group search a title.")
    if len(title) > TITLE_MAX:
        raise GfValidationError(f"❌ The title can be at most {TITLE_MAX} characters.")
    return title


def parse_players(raw: str) -> tuple[int, int]:
    m = _PLAYERS_RE.match(raw or "")
    if m is None:
        raise GfValidationError("❌ Players must be a range like `4-8`.")
    low, high = int(m.group(1)), int(m.group(2))
    if low < 1:
        raise GfValidationError("❌ The minimum must be at least 1.")
    if high < low:
        raise GfValidationError("❌ The maximum cannot be lower than the minimum.")
    if high > PLAYERS_CAP:
        raise GfValidationError(f"❌ The maximum can be at most {PLAYERS_CAP}.")
    return low, high


def parse_date(raw: str, *, today: date) -> date:
    """`26.09.2026` or `2026-09-26` (both unambiguous; `09/26` is not)."""
    text = (raw or "").strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt).date()
            break
        except ValueError:
            continue
    else:
        raise GfValidationError(
            "❌ Date must be `DD.MM.YYYY` (e.g. `26.09.2026`) or `YYYY-MM-DD`.")
    if parsed < today:
        raise GfValidationError("❌ That date is in the past.")
    return parsed


def parse_times(raw: str) -> list[tuple[int, int]]:
    """`19:00 / 20:00 / 21:00` -> [(19, 0), (20, 0), (21, 0)], ascending.
    Duplicates are rejected, not merged, so a typo is visible."""
    parts = [p for p in _SPLIT_RE.split((raw or "").strip()) if p]
    if not parts:
        raise GfValidationError("❌ Give at least one start time, e.g. `19:00 / 20:00`.")
    seen: list[tuple[int, int]] = []
    for part in parts:
        m = _TIME_RE.match(part)
        if m is None:
            raise GfValidationError(f"❌ `{part}` is not a time. Use `HH:MM`.")
        hm = (int(m.group(1)), int(m.group(2)))
        if hm[0] > 23 or hm[1] > 59:
            raise GfValidationError(f"❌ `{part}` is not a valid time.")
        if hm in seen:
            raise GfValidationError(f"❌ `{hm[0]:02d}:{hm[1]:02d}` is listed twice.")
        seen.append(hm)
    if len(seen) > MAX_SLOTS:
        raise GfValidationError(f"❌ At most {MAX_SLOTS} start times.")
    return sorted(seen)


def to_utc(day: date, hour: int, minute: int, tz: ZoneInfo) -> datetime:
    """Local wall time in `tz` -> UTC. Rejects wall times that do not exist
    (clocks jump forward) or exist twice (clocks fall back)."""
    naive = datetime(day.year, day.month, day.day, hour, minute)
    first = naive.replace(tzinfo=tz, fold=0).astimezone(UTC)
    second = naive.replace(tzinfo=tz, fold=1).astimezone(UTC)
    if first != second:
        label = f"{hour:02d}:{minute:02d}"
        exists = first.astimezone(tz).replace(tzinfo=None) == naive
        if exists:
            raise GfValidationError(
                f"❌ {label} happens twice that night (clocks go back). "
                "Please pick another time.")
        raise GfValidationError(
            f"❌ {label} does not exist that night (clocks go forward). "
            "Please pick another time.")
    return first


def build_draft(*, title: str, players: str, event_date: str, times: str,
                zone: str, now: datetime) -> Draft:
    """Validate everything and convert the times to UTC. `now` is aware."""
    tz = ZoneInfo(zone)
    parsed_title = parse_title(title)
    low, high = parse_players(players)
    day = parse_date(event_date, today=now.astimezone(tz).date())
    slots = []
    for hour, minute in parse_times(times):
        slot = to_utc(day, hour, minute, tz)
        if slot - MIN_LEAD <= now:
            raise GfValidationError(
                f"❌ {hour:02d}:{minute:02d} is too soon — start times must be "
                "more than 5 minutes away.")
        slots.append(slot)
    return Draft(title=parsed_title, min_players=low, max_players=high,
                 slots=tuple(sorted(slots)))
