import glob
import json
import logging
import os
import sqlite3

from n3x_bot.storage.base import StatsRepository
from n3x_bot.achievements import ACHIEVEMENTS, sync_all_achievements
from n3x_bot.seed import migrate_legacy_json

log = logging.getLogger(__name__)


def _connect_ro(db_path: str) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as e:
        log.warning("could not open v3 db %s read-only: %s", db_path, e)
        return None


def _rows(con: sqlite3.Connection, sql: str) -> list[tuple]:
    try:
        return con.execute(sql).fetchall()
    except sqlite3.Error:
        return []


def _looks_like_legacy_flatfile(path: str) -> bool:
    # Replicated from __main__._is_legacy_flatfile to avoid a circular import
    # (__main__ imports run_migration_folder from this module).
    if not os.path.exists(path):
        return False
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(data, dict):
        return False
    has_legacy_counts = any(k.endswith("_count") for k in data)
    return has_legacy_counts and "seq" not in data


async def _ensure_stat(repo: StatsRepository, key: str, targeted: bool) -> None:
    if await repo.get_stat(key) is not None:
        return
    if targeted:
        template = f"{{target}} hat {key} zum {{count}} mal ausgelöst."
    else:
        template = f"{key} wurde bereits {{count}} mal ausgelöst."
    msg = await repo.create_message(f"{key}_msg", template)
    await repo.create_stat(key, key.capitalize(), message_id=msg.id, targeted=targeted)


async def migrate_legacy_sqlite(repo: StatsRepository, db_path: str) -> dict:
    summary = {
        "users": 0,
        "user_stats": 0,
        "targets": 0,
        "gate_entries": 0,
        "delta": 0,
        "activity": 0,
        "streaks": 0,
        "nights": 0,
        "kodex": 0,
        "achievements_unlocked": 0,
        "achievements_skipped": 0,
    }

    con = _connect_ro(db_path)
    if con is None:
        log.warning("v3 db %s unreadable; skipping migration", db_path)
        return summary

    try:
        user_stats = _rows(con, "SELECT user_id, command_name, count FROM user_stats")
        target_stats = _rows(con, "SELECT target_id, command_name, count FROM target_stats")
        gate_stats = _rows(con, "SELECT gate_type, cost, user_id, username FROM gate_stats")
        delta_stats = _rows(con, "SELECT cost, laser_dropped, user_id, username FROM delta_stats")
        achievements = _rows(con, "SELECT user_id, achievement_id FROM achievements")
        voice_stats = _rows(con, "SELECT user_id, total_seconds FROM voice_stats")
        message_stats = _rows(con, "SELECT user_id, total_messages FROM message_stats")
        reaction_stats = _rows(con, "SELECT user_id, reaction_count FROM reaction_stats")
        streak_stats = _rows(con, "SELECT user_id, current_streak, last_active_date, max_streak FROM streak_stats")
        night_stats = _rows(con, "SELECT user_id, night_count, last_night_date FROM night_stats")
        kodex_confirmations = _rows(con, "SELECT user_id FROM kodex_confirmations")
        kodex_messages = _rows(con, "SELECT message_id, user_id FROM kodex_messages")
    finally:
        con.close()

    # Display-name map from gate/delta usernames.
    names: dict[int, str] = {}
    for _, _, user_id, username in gate_stats:
        names[int(user_id)] = username
    for _, _, user_id, username in delta_stats:
        names[int(user_id)] = username

    def name_for(uid: int) -> str:
        return names.get(uid) or f"user_{uid}"

    # Collect distinct user ids from every id-bearing migrated table.
    user_ids: set[int] = set()
    for user_id, _, _ in user_stats:
        user_ids.add(int(user_id))
    for target_id, _, _ in target_stats:
        user_ids.add(int(target_id))
    for _, _, user_id, _ in gate_stats:
        user_ids.add(int(user_id))
    for _, _, user_id, _ in delta_stats:
        user_ids.add(int(user_id))
    for user_id, _ in achievements:
        user_ids.add(int(user_id))
    for user_id, _ in voice_stats:
        user_ids.add(int(user_id))
    for user_id, _ in message_stats:
        user_ids.add(int(user_id))
    for user_id, _ in reaction_stats:
        user_ids.add(int(user_id))
    for user_id, *_ in streak_stats:
        user_ids.add(int(user_id))
    for user_id, *_ in night_stats:
        user_ids.add(int(user_id))
    for (user_id,) in kodex_confirmations:
        user_ids.add(int(user_id))
    for _, user_id in kodex_messages:
        user_ids.add(int(user_id))

    for uid in sorted(user_ids):
        await repo.upsert_user(uid, name_for(uid))
    summary["users"] = len(user_ids)

    # user_stats — global_stats is intentionally never read.
    for user_id, cmd, count in user_stats:
        await _ensure_stat(repo, cmd, targeted=False)
        uid = int(user_id)
        for _ in range(count):
            await repo.record_use(uid, name_for(uid), cmd)
        summary["user_stats"] += count

    # target_stats
    for target_id, cmd, count in target_stats:
        await _ensure_stat(repo, cmd, targeted=True)
        tid = int(target_id)
        for _ in range(count):
            await repo.record_target_use(tid, cmd)
        summary["targets"] += count

    # gate_stats — dedup_window_seconds=0 keeps identical rows.
    for gate_type, cost, user_id, username in gate_stats:
        await repo.add_gate_entry(gate_type, cost, int(user_id), username,
                                  dedup_window_seconds=0)
        summary["gate_entries"] += 1

    # delta_stats -> gate "d"
    for cost, laser_dropped, user_id, username in delta_stats:
        await repo.add_gate_entry("d", cost, int(user_id), username,
                                  dedup_window_seconds=0,
                                  drops={"laser": bool(laser_dropped)})
        summary["delta"] += 1

    # achievements — only unlock ids we still define; count the rest as skipped.
    valid = {a.id for a in ACHIEVEMENTS}
    for user_id, ach_id in achievements:
        if ach_id in valid:
            await repo.unlock_achievement(int(user_id), ach_id)
            summary["achievements_unlocked"] += 1
        else:
            summary["achievements_skipped"] += 1

    # activity
    for user_id, total_seconds in voice_stats:
        await repo.add_activity(int(user_id), "voice_seconds", total_seconds)
        summary["activity"] += 1
    for user_id, total_messages in message_stats:
        await repo.add_activity(int(user_id), "messages", total_messages)
        summary["activity"] += 1
    for user_id, reaction_count in reaction_stats:
        await repo.add_activity(int(user_id), "reactions", reaction_count)
        summary["activity"] += 1

    # streak / night
    for user_id, current_streak, last_active_date, max_streak in streak_stats:
        await repo.set_streak(int(user_id), current_streak, last_active_date, max_streak)
        summary["streaks"] += 1
    for user_id, night_count, last_night_date in night_stats:
        await repo.set_night(int(user_id), night_count, last_night_date)
        summary["nights"] += 1

    # kodex
    for (user_id,) in kodex_confirmations:
        await repo.confirm_kodex(int(user_id))
        summary["kodex"] += 1
    for message_id, user_id in kodex_messages:
        await repo.save_kodex_message(int(message_id), int(user_id))
        summary["kodex"] += 1

    # Re-derive achievements under OUR definitions from the imported counts.
    await sync_all_achievements(repo)

    return summary


async def run_migration_folder(repo: StatsRepository, settings) -> dict | None:
    d = settings.migration_dir
    if not os.path.isdir(d):
        return None

    db_path = os.path.join(d, "bot_data.db")
    if os.path.exists(db_path):
        summary = await migrate_legacy_sqlite(repo, db_path)
        os.replace(db_path, db_path + ".imported")
        # sqlite wins: neutralize any legacy json so it can never surprise-import.
        for path in glob.glob(os.path.join(d, "*.json")):
            if _looks_like_legacy_flatfile(path):
                log.warning("v3 sqlite present; renaming legacy flatfile %s aside "
                            "without importing (sqlite wins)", path)
                os.replace(path, path + ".imported")
        return summary

    for path in glob.glob(os.path.join(d, "*.json")):
        if _looks_like_legacy_flatfile(path):
            await migrate_legacy_json(repo, path)
            os.replace(path, path + ".imported")
            return {"source": "flatfile", "path": path}

    return None
