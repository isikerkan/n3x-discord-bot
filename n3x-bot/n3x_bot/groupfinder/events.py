"""Group Finder event logic: creation, voting, time finding, roster.

No Discord imports. Everything takes `now` (aware) as an argument, like the
rest of the bot, so every rule here is deterministic in tests.

Time finding is Variant 1 from the spec: the first proposed time to reach the
minimum becomes the start time immediately; only the members who voted for it
become participants; joining stays open until the maximum is reached or until
5 minutes before the start.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from n3x_bot.groupfinder.parsing import Draft

VOTING = "VOTING"
SCHEDULED = "SCHEDULED"
CLOSED = "CLOSED"
STARTED = "STARTED"
EXPIRED = "EXPIRED"
NO_TIME_FOUND = "NO_TIME_FOUND"
CANCELLED = "CANCELLED"
ACTIVE_STATUSES = (VOTING, SCHEDULED, CLOSED, STARTED)
FIXED_STATUSES = (SCHEDULED, CLOSED, STARTED)

VOTE_CLOSE = timedelta(minutes=5)       # voting/joining closes this long before a start
CLEANUP_AFTER = timedelta(hours=1)      # messages are removed this long after the start


@dataclass(frozen=True)
class VoteResult:
    event: dict | None
    scheduled_at: datetime | None       # set when this vote fixed the time
    error: str | None                   # missing / not_voting


async def create(repo, draft: Draft, *, creator_id: int, origin_zone: str,
                 now: datetime) -> int:
    """Store the event. The creator is not a participant; they take part by
    voting like everyone else."""
    return await repo.gf_create_event(
        creator_id=creator_id, title=draft.title, min_players=draft.min_players,
        max_players=draft.max_players, origin_zone=origin_zone,
        slots=list(draft.slots), status=VOTING, created_at=now,
        # Until a time is found: when the last proposed time + 1 h has passed,
        # nothing can come of it any more.
        cleanup_at=max(draft.slots) + CLEANUP_AFTER)


def open_slots(event: dict, now: datetime) -> list[datetime]:
    """Proposed times that can still be voted on."""
    return [s for s in event["slots"] if s - VOTE_CLOSE > now]


def vote_counts(event: dict, votes: list[dict]) -> dict[datetime, int]:
    counts = {s: 0 for s in event["slots"]}
    for v in votes:
        if v["starts_at"] in counts:
            counts[v["starts_at"]] += 1
    return counts


def voters(votes: list[dict]) -> list[tuple[int, str]]:
    """Distinct voters as (discord_id, zone), in order of their first vote —
    the participant list before a time is found."""
    seen: dict[int, str] = {}
    for v in votes:
        seen.setdefault(v["discord_id"], v["zone"])
    return list(seen.items())


def find_winner(event: dict, votes: list[dict], now: datetime) -> datetime | None:
    """The time that becomes the start time, or None.

    Proposed times are checked in chronological order and the first one that
    has reached the minimum wins. A vote can push several times over the
    minimum at once (multi-select); checking chronologically makes the result
    independent of the order in which concurrent votes arrive.
    """
    counts = vote_counts(event, votes)
    for slot in open_slots(event, now):
        if counts[slot] >= event["min_players"]:
            return slot
    return None


async def vote(repo, event_id: int, discord_id: int, zone: str,
               slots: list[datetime], now: datetime) -> VoteResult:
    """Replace the member's votes, then check whether a time is found.
    Times that are no longer open are ignored."""
    event = await repo.gf_get_event(event_id)
    if event is None:
        return VoteResult(None, None, "missing")
    if event["status"] != VOTING:
        return VoteResult(event, None, "not_voting")
    still_open = set(open_slots(event, now))
    wanted = [s for s in dict.fromkeys(slots) if s in still_open]
    await repo.gf_set_votes(event_id, discord_id, wanted, zone, now)
    scheduled = await try_schedule(repo, event_id, now)
    return VoteResult(await repo.gf_get_event(event_id), scheduled, None)


async def try_schedule(repo, event_id: int, now: datetime) -> datetime | None:
    """Fix the start time if a proposed time has reached the minimum.

    The roster is exactly the members who voted for that time (capped at the
    maximum, first voters first). Everyone else drops out. The write is a
    compare-and-swap on VOTING, so concurrent callers produce one time only.
    Returns the start time if *this* call fixed it.
    """
    event = await repo.gf_get_event(event_id)
    if event is None or event["status"] != VOTING:
        return None
    votes = await repo.gf_get_votes(event_id)
    winner = find_winner(event, votes, now)
    if winner is None:
        return None
    roster = [(v["discord_id"], v["zone"]) for v in votes
              if v["starts_at"] == winner][:event["max_players"]]
    full = len(roster) >= event["max_players"]
    won = await repo.gf_schedule_event(
        event_id, starts_at=winner, status=CLOSED if full else SCHEDULED,
        cleanup_at=winner + CLEANUP_AFTER, closed_at=now if full else None,
        participants=roster, joined_at=now, expect_status=VOTING)
    return winner if won else None


def joining_open(event: dict, now: datetime) -> bool:
    return (event["status"] == SCHEDULED and event["scheduled_at"] is not None
            and event["scheduled_at"] - VOTE_CLOSE > now)


async def join(repo, event_id: int, discord_id: int, zone: str,
               now: datetime) -> str:
    """`added`, `already`, `full`, `closed`, `not_scheduled` or `missing`."""
    event = await repo.gf_get_event(event_id)
    if event is None:
        return "missing"
    if event["status"] == VOTING:
        return "not_scheduled"
    if not joining_open(event, now):
        roster = await repo.gf_get_participants(event_id)
        if any(p["discord_id"] == discord_id for p in roster):
            return "already"
        return "full" if len(roster) >= event["max_players"] else "closed"
    result = await repo.gf_add_participant(
        event_id, discord_id, zone, now, max_players=event["max_players"],
        source="JOIN")
    if result == "added":
        roster = await repo.gf_get_participants(event_id)
        if len(roster) >= event["max_players"]:
            await repo.gf_update_event(event_id, expect_status=SCHEDULED,
                                       status=CLOSED, closed_at=now)
    return result


async def leave(repo, event_id: int, discord_id: int, now: datetime) -> str:
    """`left`, `not_member`, `started` or `missing`.

    While voting, leaving withdraws all of the member's votes. Once a time is
    fixed it removes them from the roster; the start time never changes. If
    the group was closed only because it was full and the start is still more
    than 5 minutes away, joining reopens.
    """
    event = await repo.gf_get_event(event_id)
    if event is None:
        return "missing"
    if event["status"] == VOTING:
        if not any(v["discord_id"] == discord_id
                   for v in await repo.gf_get_votes(event_id)):
            return "not_member"
        await repo.gf_set_votes(event_id, discord_id, [], "", now)
        return "left"
    if event["status"] not in (SCHEDULED, CLOSED):
        return "started"
    if not await repo.gf_remove_participant(event_id, discord_id):
        return "not_member"
    if (event["status"] == CLOSED and event["scheduled_at"] - VOTE_CLOSE > now):
        await repo.gf_update_event(event_id, expect_status=CLOSED,
                                   status=SCHEDULED, closed_at=None)
    return "left"


async def participants(repo, event: dict) -> list[tuple[int, str]]:
    """What the event shows as participants: voters while voting, the roster
    once a time is fixed."""
    if event["status"] == VOTING:
        return voters(await repo.gf_get_votes(event["id"]))
    return [(p["discord_id"], p["zone"])
            for p in await repo.gf_get_participants(event["id"])]


# ── lifecycle (stage 3) ────────────────────────────────────────────────────

CANCELLABLE = (VOTING, SCHEDULED, CLOSED)


def next_status(event: dict, now: datetime) -> str | None:
    """The transition that is due for `event` at `now`, or None.

    VOTING    -> NO_TIME_FOUND  once no proposed time is open any more
    SCHEDULED -> CLOSED         5 minutes before the start
    SCHEDULED/CLOSED -> STARTED at the start
    STARTED   -> EXPIRED        1 hour after the start (cleanup_at)
    """
    status = event["status"]
    if status == VOTING:
        return NO_TIME_FOUND if not open_slots(event, now) else None
    start = event["scheduled_at"]
    if status in (SCHEDULED, CLOSED) and start <= now:
        return STARTED
    if status == SCHEDULED and start - VOTE_CLOSE <= now:
        return CLOSED
    if status == STARTED and event["cleanup_at"] <= now:
        return EXPIRED
    return None


async def advance(repo, event: dict, now: datetime) -> dict:
    """Apply every due transition, one step at a time, so an event that was
    left behind while the bot was down catches up in a single tick. Each step
    is a compare-and-swap on the status it came from."""
    for _ in range(len(ACTIVE_STATUSES) + 1):
        target = next_status(event, now)
        if target is None:
            break
        fields = {"status": target}
        if target == CLOSED:
            fields["closed_at"] = now
        await repo.gf_update_event(event["id"], expect_status=event["status"],
                                   **fields)
        event = await repo.gf_get_event(event["id"])
    return event


def needs_cleanup(event: dict, now: datetime) -> bool:
    """Messages are removed 1 h after the start, 1 h after the last proposed
    time when no time was found, and right away when cancelled."""
    if event["cleaned_at"] is not None:
        return False
    if event["status"] in (EXPIRED, CANCELLED):
        return True
    return event["status"] == NO_TIME_FOUND and event["cleanup_at"] <= now


def can_cancel(event: dict, user_id: int, is_admin: bool) -> bool:
    return is_admin or user_id == event["creator_id"]


async def cancel(repo, event_id: int, user_id: int, is_admin: bool,
                 now: datetime) -> str:
    """`cancelled`, `forbidden`, `not_cancellable` or `missing`."""
    event = await repo.gf_get_event(event_id)
    if event is None:
        return "missing"
    if not can_cancel(event, user_id, is_admin):
        return "forbidden"
    if event["status"] not in CANCELLABLE:
        return "not_cancellable"
    done = await repo.gf_update_event(event_id, expect_status=event["status"],
                                      status=CANCELLED, cancelled_at=now)
    return "cancelled" if done else "not_cancellable"
