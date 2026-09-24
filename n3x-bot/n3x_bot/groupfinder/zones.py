"""Pure zone helpers: the IANA catalogue, validation, naming and search.

No Discord and no repo imports. Everything the admin and member flows need to
decide *which* zone and *what it is called* lives here and is testable without
a bot.
"""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

ACTIVE = "ACTIVE"
DEACTIVATED = "DEACTIVATED"

CATEGORY_NAME = "Group Finder"
HUB_CHANNEL_NAME = "group-finder"

# Offered in the setup guide. A select menu holds at most 25 options, so this
# list may never grow past 25; anything else is added via `timezone-add`.
# Asia/Karachi is in because the server has its own Pakistan category.
POPULAR_ZONES: tuple[str, ...] = (
    "Europe/Berlin", "Europe/Zurich", "Europe/Vienna", "Europe/London",
    "Europe/Paris", "Europe/Madrid", "Europe/Rome", "Europe/Amsterdam",
    "Europe/Warsaw", "Europe/Athens", "Europe/Istanbul", "Europe/Moscow",
    "Africa/Cairo", "Asia/Dubai", "Asia/Karachi", "Asia/Kolkata",
    "Asia/Singapore", "Asia/Shanghai", "Asia/Tokyo", "Australia/Sydney",
    "America/New_York", "America/Chicago", "America/Denver",
    "America/Los_Angeles", "America/Sao_Paulo",
)
SELECT_LIMIT = 25

# Aliases and technical zones that would only clutter the autocomplete
# ("Etc/GMT+1" has an inverted sign, "US/Eastern" duplicates America/New_York).
_EXCLUDED_PREFIXES = ("Etc/", "SystemV/", "US/", "Canada/", "Mexico/",
                      "Brazil/", "Chile/", "posix/", "right/")


@lru_cache(maxsize=1)
def all_zones() -> tuple[str, ...]:
    """Every selectable IANA zone, sorted. Region/City form only."""
    return tuple(sorted(
        z for z in available_timezones()
        if "/" in z and not z.startswith(_EXCLUDED_PREFIXES)))


def is_valid_zone(zone: str) -> bool:
    return zone in all_zones()


def search_zones(query: str, pool, limit: int = SELECT_LIMIT) -> list[str]:
    """Case-insensitive match for autocomplete. Spaces match underscores, so
    `new york` finds `America/New_York`. Prefix matches of the city come first."""
    needle = (query or "").strip().lower().replace(" ", "_")
    if not needle:
        return list(pool)[:limit]
    hits = [z for z in pool if needle in z.lower()]
    hits.sort(key=lambda z: (not z.lower().split("/")[-1].startswith(needle), z))
    return hits[:limit]


# ── one channel per clock ──────────────────────────────────────────────────
# Zones whose clocks always show the same time share one channel (Berlin,
# Zurich, Vienna, Paris, ...). "Same hour right now" is not enough: Berlin and
# Lagos are both UTC+1 in winter but an hour apart in summer, so grouping them
# would show wrong local times half the year. The clock is sampled every
# 6 hours over two years, which catches every DST rule difference.
_SAMPLE_START = datetime(datetime.now(timezone.utc).year, 1, 1, tzinfo=timezone.utc)
_SAMPLES = tuple(_SAMPLE_START + timedelta(hours=6 * i) for i in range(4 * 730))


@lru_cache(maxsize=None)
def clock_signature(zone: str) -> tuple:
    tz = ZoneInfo(zone)
    return tuple(t.astimezone(tz).utcoffset() for t in _SAMPLES)


def same_clock(a: str, b: str) -> bool:
    return a == b or clock_signature(a) == clock_signature(b)


def find_same_clock(zone: str, candidates) -> str | None:
    """The first candidate (in the given order) whose clock matches `zone`."""
    for candidate in candidates:
        if same_clock(zone, candidate):
            return candidate
    return None


def offset_label(zone: str, now: datetime) -> str:
    """`UTC+02:00` — the offset *now* (it changes with DST)."""
    offset = now.astimezone(ZoneInfo(zone)).utcoffset()
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    return f"UTC{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


# ── names follow the current offset ────────────────────────────────────────
# A channel is named after its clock's offset *right now* and is renamed at
# every DST switch (gf-utc+2 -> gf-utc+1 on the last Sunday of October).
# Members never move; only the name changes. When two groups share an offset
# at the same time (Berlin and Lagos are both UTC+1 in winter), the group
# created first keeps the plain name and the others get their city appended.

def _offset_minutes(zone: str, now: datetime) -> int:
    return int(now.astimezone(ZoneInfo(zone)).utcoffset().total_seconds() // 60)


def offset_text(zone: str, now: datetime) -> str:
    """`UTC+2`, `UTC-5`, `UTC+5:30`, `UTC+0` — for roles and labels."""
    minutes = _offset_minutes(zone, now)
    sign = "+" if minutes >= 0 else "-"
    hours, rest = divmod(abs(minutes), 60)
    return f"UTC{sign}{hours}" + (f":{rest:02d}" if rest else "")


def _channel_offset(zone: str, now: datetime, words: bool) -> str:
    minutes = _offset_minutes(zone, now)
    hours, rest = divmod(abs(minutes), 60)
    tail = f"{hours}" + (f"h{rest:02d}" if rest else "")
    if words:
        return f"utc-{'plus' if minutes >= 0 else 'minus'}-{tail}"
    return f"utc{'+' if minutes >= 0 else '-'}{tail}"


def city(zone: str) -> str:
    """`America/Sao_Paulo` -> `Sao Paulo`."""
    return zone.rsplit("/", 1)[-1].replace("_", " ")


def zone_names(rows: list[dict], now: datetime, *, words: bool = False) -> dict:
    """`{zone: (channel_name, role_name)}` for every row given (the active
    zones). `rows` need `zone` and `created_at`. `words` selects the
    `gf-utc-plus-2` spelling for servers that do not keep `+` in names."""
    by_offset: dict[str, list[dict]] = {}
    for row in rows:
        by_offset.setdefault(offset_text(row["zone"], now), []).append(row)
    names = {}
    for label, group in by_offset.items():
        group = sorted(group, key=lambda r: (r["created_at"], r["zone"]))
        for i, row in enumerate(group):
            channel = "gf-" + _channel_offset(row["zone"], now, words)
            role = label
            if i:                                  # shares the offset right now
                slug = city(row["zone"]).lower().replace(" ", "-")
                channel += f"-{slug}"
                role += f" · {city(row['zone'])}"
            names[row["zone"]] = (channel, role)
    return names
