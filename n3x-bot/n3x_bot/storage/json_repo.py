import copy
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

from n3x_bot.models import User, Stat, Message
from n3x_bot.storage.base import GATE_TYPES, StatsRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(v):
    return datetime.fromisoformat(v) if v else None


def _as_aware_utc(dt):
    """Coerce a naive datetime to tz-aware UTC (aware dt is returned as-is)."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _drops_of(row) -> dict:
    d = row.get("drops")
    if d:
        return d
    lz = row.get("laser_dropped")
    return {"laser": lz} if lz is not None else {}


class JsonRepository(StatsRepository):
    def __init__(self, path: str):
        self.path = path
        self._db = None

    # ── lifecycle / persistence ────────────────────────────────────────────
    def _empty(self) -> dict:
        return {
            "seq": {"user": 0, "message": 0, "stat": 0, "gate": 0},
            "users": [], "messages": [], "stats": [],
            "user_stats": {}, "stat_totals": {}, "stat_last_post": {},
            "target_stats": {}, "gate_entries": [],
            "activity_counters": {}, "streak_stats": {}, "night_stats": {},
            "achievements": {},
            "kodex_confirmations": [], "kodex_messages": {},
            "base_timers": {}, "channel_messages": {},
            "gate_pending": {},
            "voice_sessions": {},
            "runtime_config": {},
            "content_texts": {},
            "color_config": {},
            "achievement_defs": {},
        }

    async def connect(self) -> None:
        if os.path.exists(self.path):
            with open(self.path, "r") as f:
                self._db = json.load(f)
            for k, v in self._empty().items():
                self._db.setdefault(k, v)
        else:
            self._db = self._empty()
            self._flush()

    async def close(self) -> None:
        self._flush()

    def _flush(self) -> None:
        directory = os.path.dirname(self.path) or "."
        fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._db, f)
            os.replace(tmp_path, self.path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    def _next(self, kind: str) -> int:
        self._db["seq"][kind] += 1
        return self._db["seq"][kind]

    # ── row -> model helpers ───────────────────────────────────────────────
    def _stat(self, r) -> Stat:
        return Stat(id=r["id"], key=r["key"], name=r["name"],
                    message_id=r["message_id"],
                    targeted=r.get("targeted", False),
                    archived_at=_parse_dt(r["archived_at"]),
                    created_at=_parse_dt(r["created_at"]))

    def _msg(self, r) -> Message:
        return Message(id=r["id"], name=r["name"], template=r["template"],
                       archived_at=_parse_dt(r["archived_at"]),
                       created_at=_parse_dt(r["created_at"]))

    def _user(self, r) -> User:
        return User(id=r["id"], discord_id=r["discord_id"],
                    display_name=r["display_name"],
                    archived_at=_parse_dt(r["archived_at"]),
                    created_at=_parse_dt(r["created_at"]))

    def _find(self, coll: str, **kw):
        for r in self._db[coll]:
            if all(r[k] == v for k, v in kw.items()):
                return r
        return None

    # ── messages ───────────────────────────────────────────────────────────
    async def create_message(self, name, template) -> Message:
        row = {"id": self._next("message"), "name": name, "template": template,
               "archived_at": None, "created_at": _now()}
        self._db["messages"].append(row)
        self._flush()
        return self._msg(row)

    async def get_message(self, message_id):
        r = self._find("messages", id=message_id)
        return self._msg(r) if r else None

    async def list_messages(self, include_archived=False):
        return [self._msg(r) for r in self._db["messages"]
                if include_archived or r["archived_at"] is None]

    async def update_message(self, message_id, name=None, template=None):
        r = self._find("messages", id=message_id)
        if r is None:
            raise KeyError(message_id)
        if name is not None:
            r["name"] = name
        if template is not None:
            r["template"] = template
        self._flush()
        return self._msg(r)

    async def archive_message(self, message_id):
        r = self._find("messages", id=message_id)
        if r is None:
            raise KeyError(message_id)
        r["archived_at"] = _now()
        self._flush()

    async def delete_message(self, message_id):
        self._db["messages"] = [r for r in self._db["messages"] if r["id"] != message_id]
        self._flush()

    # ── stats ──────────────────────────────────────────────────────────────
    async def create_stat(self, key, name, message_id=None, targeted=False) -> Stat:
        row = {"id": self._next("stat"), "key": key, "name": name,
               "message_id": message_id, "targeted": targeted,
               "archived_at": None, "created_at": _now()}
        self._db["stats"].append(row)
        self._flush()
        return self._stat(row)

    async def get_stat(self, key):
        r = self._find("stats", key=key)
        return self._stat(r) if r else None

    async def list_stats(self, include_archived=False):
        return [self._stat(r) for r in self._db["stats"]
                if include_archived or r["archived_at"] is None]

    async def update_stat(self, key, name=None):
        r = self._find("stats", key=key)
        if r is None:
            raise KeyError(key)
        if name is not None:
            r["name"] = name
        self._flush()
        return self._stat(r)

    async def set_stat_message(self, key, message_id):
        r = self._find("stats", key=key)
        if r is None:
            raise KeyError(key)
        r["message_id"] = message_id
        self._flush()
        return self._stat(r)

    async def archive_stat(self, key):
        r = self._find("stats", key=key)
        if r is None:
            raise KeyError(key)
        r["archived_at"] = _now()
        self._flush()

    async def unarchive_stat(self, key):
        r = self._find("stats", key=key)
        if r is None:
            raise KeyError(key)
        r["archived_at"] = None
        self._flush()

    async def delete_stat(self, key):
        self._db["stats"] = [r for r in self._db["stats"] if r["key"] != key]
        self._flush()

    # ── users ──────────────────────────────────────────────────────────────
    async def upsert_user(self, discord_id, display_name) -> User:
        r = self._find("users", discord_id=discord_id)
        if r is None:
            r = {"id": self._next("user"), "discord_id": discord_id,
                 "display_name": display_name, "archived_at": None,
                 "created_at": _now()}
            self._db["users"].append(r)
        else:
            r["display_name"] = display_name
            r["archived_at"] = None
        self._flush()
        return self._user(r)

    async def get_user(self, discord_id):
        r = self._find("users", discord_id=discord_id)
        return self._user(r) if r else None

    async def list_users(self, include_archived=False):
        return [self._user(r) for r in self._db["users"]
                if include_archived or r["archived_at"] is None]

    async def archive_user(self, discord_id):
        r = self._find("users", discord_id=discord_id)
        if r is None:
            raise KeyError(discord_id)
        r["archived_at"] = _now()
        self._flush()

    async def delete_user(self, discord_id):
        self._db["users"] = [r for r in self._db["users"]
                             if r["discord_id"] != discord_id]
        self._flush()

    # ── tracking ───────────────────────────────────────────────────────────
    async def record_use(self, discord_id, display_name, stat_key):
        stat = self._find("stats", key=stat_key)
        if stat is None:
            raise KeyError(stat_key)
        user = await self.upsert_user(discord_id, display_name)
        uid, sid = str(user.id), str(stat["id"])
        us = self._db["user_stats"].setdefault(uid, {})
        us[sid] = us.get(sid, 0) + 1
        self._db["stat_totals"][sid] = self._db["stat_totals"].get(sid, 0) + 1
        self._flush()
        return us[sid], self._db["stat_totals"][sid]

    async def get_user_stats(self, discord_id):
        user = self._find("users", discord_id=discord_id)
        if user is None:
            return {}
        us = self._db["user_stats"].get(str(user["id"]), {})
        id_to_key = {str(s["id"]): s["key"] for s in self._db["stats"]}
        return {id_to_key[sid]: c for sid, c in us.items() if sid in id_to_key}

    async def get_total(self, stat_key):
        stat = self._find("stats", key=stat_key)
        if stat is None:
            return 0
        return self._db["stat_totals"].get(str(stat["id"]), 0)

    async def get_last_post(self, stat_key):
        stat = self._find("stats", key=stat_key)
        if stat is None:
            return None
        v = self._db["stat_last_post"].get(str(stat["id"]))
        return (v[0], v[1]) if v else None

    async def set_last_post(self, stat_key, discord_message_id, channel_id):
        stat = self._find("stats", key=stat_key)
        if stat is None:
            raise KeyError(stat_key)
        self._db["stat_last_post"][str(stat["id"])] = [discord_message_id, channel_id]
        self._flush()

    # ── channel messages ───────────────────────────────────────────────────
    async def set_channel_message(self, key, message_id, channel_id):
        self._db["channel_messages"][key] = [message_id, channel_id]
        self._flush()

    async def get_channel_message(self, key):
        v = self._db["channel_messages"].get(key)
        return (v[0], v[1]) if v else None

    # ── gate pending ───────────────────────────────────────────────────────
    async def set_gate_pending(self, message_id, *, channel_id, gate_type, cost,
                               user_id, username, options):
        self._db["gate_pending"][str(message_id)] = {
            "channel_id": channel_id, "gate_type": gate_type, "cost": cost,
            "user_id": user_id, "username": username, "options": dict(options)}
        self._flush()

    def _gate_pending_row(self, message_id, row) -> dict:
        return {"message_id": int(message_id),
                "channel_id": int(row["channel_id"]),
                "gate_type": row["gate_type"], "cost": int(row["cost"]),
                "user_id": int(row["user_id"]), "username": row["username"],
                "options": dict(row["options"])}

    async def get_gate_pending(self, message_id):
        row = self._db["gate_pending"].get(str(message_id))
        return None if row is None else self._gate_pending_row(message_id, row)

    async def delete_gate_pending(self, message_id):
        existed = str(message_id) in self._db["gate_pending"]
        self._db["gate_pending"].pop(str(message_id), None)
        self._flush()
        return existed

    async def all_gate_pending(self):
        return [self._gate_pending_row(k, v)
                for k, v in self._db["gate_pending"].items()]

    # ── runtime config ─────────────────────────────────────────────────────
    async def set_runtime_config(self, key, value):
        self._db["runtime_config"][key] = value
        self._flush()

    async def get_runtime_config(self, key):
        return self._db["runtime_config"].get(key)

    async def delete_runtime_config(self, key):
        existed = key in self._db["runtime_config"]
        self._db["runtime_config"].pop(key, None)
        self._flush()
        return existed

    async def all_runtime_config(self):
        return dict(self._db["runtime_config"])

    # ── content texts ──────────────────────────────────────────────────────
    async def set_content_text(self, key, value):
        self._db["content_texts"][key] = value
        self._flush()

    async def get_content_text(self, key):
        return self._db["content_texts"].get(key)

    async def delete_content_text(self, key):
        existed = key in self._db["content_texts"]
        self._db["content_texts"].pop(key, None)
        self._flush()
        return existed

    async def all_content_texts(self):
        return dict(self._db["content_texts"])

    # ── color config ───────────────────────────────────────────────────────
    async def set_color_config(self, key, value):
        self._db["color_config"][key] = value
        self._flush()

    async def get_color_config(self, key):
        return self._db["color_config"].get(key)

    async def delete_color_config(self, key):
        existed = key in self._db["color_config"]
        self._db["color_config"].pop(key, None)
        self._flush()
        return existed

    async def all_color_config(self):
        return dict(self._db["color_config"])

    # ── achievement definitions ────────────────────────────────────────────
    async def set_achievement_def(self, id, *, category, metric, threshold,
                                  title, secret, color=None):
        self._db["achievement_defs"][id] = {
            "category": category, "metric": metric, "threshold": threshold,
            "title": title, "secret": secret, "color": color}
        self._flush()

    async def get_achievement_def(self, id):
        row = self._db["achievement_defs"].get(id)
        return None if row is None else {"id": id, **row}

    async def delete_achievement_def(self, id):
        existed = id in self._db["achievement_defs"]
        self._db["achievement_defs"].pop(id, None)
        self._flush()
        return existed

    async def all_achievement_defs(self):
        return [{"id": k, **self._db["achievement_defs"][k]}
                for k in sorted(self._db["achievement_defs"])]

    async def replace_achievement_defs(self, defs):
        self._db["achievement_defs"] = {
            d["id"]: {"category": d["category"], "metric": d["metric"],
                      "threshold": d["threshold"], "title": d["title"],
                      "secret": d["secret"], "color": d.get("color")}
            for d in defs}
        self._flush()

    # ── target tracking ────────────────────────────────────────────────────
    async def record_target_use(self, target_discord_id, stat_key):
        stat = self._find("stats", key=stat_key)
        if stat is None:
            raise KeyError(stat_key)
        sid, tid = str(stat["id"]), str(target_discord_id)
        ts = self._db["target_stats"].setdefault(sid, {})
        ts[tid] = ts.get(tid, 0) + 1
        self._flush()
        return ts[tid]

    async def get_target_total(self, target_discord_id, stat_key):
        stat = self._find("stats", key=stat_key)
        if stat is None:
            return 0
        return self._db["target_stats"].get(str(stat["id"]), {}).get(str(target_discord_id), 0)

    # ── gate tracker ───────────────────────────────────────────────────────
    async def add_gate_entry(self, gate_type, cost, user_id, username,
                             dedup_window_seconds=30, laser_dropped=None,
                             drops=None):
        if drops is None and laser_dropped is not None:
            drops = {"laser": bool(laser_dropped)}
        laser_col = drops.get("laser") if drops else None
        threshold = datetime.now(timezone.utc) - timedelta(seconds=dedup_window_seconds)
        for r in self._db["gate_entries"]:
            if (r["gate_type"] == gate_type and r["cost"] == cost
                    and r["user_id"] == user_id):
                created = _parse_dt(r["created_at"])
                if created is not None and created > threshold:
                    return False
        row = {"id": self._next("gate"), "gate_type": gate_type, "cost": cost,
               "user_id": user_id, "username": username,
               "laser_dropped": laser_col, "drops": drops, "created_at": _now()}
        self._db["gate_entries"].append(row)
        self._flush()
        return True

    async def gate_drop_stats(self, gate_type):
        rows = [r for r in self._db["gate_entries"]
                if r["gate_type"] == gate_type]
        count = len(rows)
        avg = round(sum(r["cost"] for r in rows) / count) if count else 0
        observed: set[str] = set()
        trues: dict[str, int] = {}
        for r in rows:
            for item, val in _drops_of(r).items():
                observed.add(item)
                if val:
                    trues[item] = trues.get(item, 0) + 1
        rates = ({item: 100 * trues.get(item, 0) / count for item in observed}
                 if count else {})
        return {"count": count, "avg": avg, "rates": rates}

    async def delta_stats(self):
        s = await self.gate_drop_stats("d")
        return {"count": s["count"], "avg": s["avg"],
                "laser_rate": s["rates"].get("laser", 0.0)}

    async def gate_record(self, gate_type):
        rows = [r for r in self._db["gate_entries"]
                if r["gate_type"] == gate_type]
        if not rows:
            return None
        min_row = min(rows, key=lambda r: r["cost"])
        max_row = max(rows, key=lambda r: r["cost"])
        return {"min_cost": min_row["cost"], "min_user": min_row["user_id"],
                "max_cost": max_row["cost"], "max_user": max_row["user_id"]}

    async def list_gate_costs(self, gate_type):
        return [r["cost"] for r in self._db["gate_entries"]
                if r["gate_type"] == gate_type]

    async def list_gate_entries(self, gate_type, since=None, until=None):
        rows = [r for r in self._db["gate_entries"]
                if r["gate_type"] == gate_type]
        rows.sort(key=lambda r: (_parse_dt(r["created_at"]), r["id"]))
        since = _as_aware_utc(since)
        until = _as_aware_utc(until)
        out = []
        for r in rows:
            created = _parse_dt(r["created_at"])
            if since is not None and created < since:
                continue
            if until is not None and created > until:
                continue
            out.append({"cost": r["cost"], "created_at": created,
                        "drops": _drops_of(r)})
        return out

    async def delete_gate_entry(self, gate_type, index):
        matches = [r for r in self._db["gate_entries"] if r["gate_type"] == gate_type]
        if index < 1 or index > len(matches):
            return False
        target = matches[index - 1]
        self._db["gate_entries"] = [r for r in self._db["gate_entries"] if r is not target]
        self._flush()
        return True

    async def gate_totals(self):
        out = {}
        for gtype in GATE_TYPES:
            costs = [r["cost"] for r in self._db["gate_entries"]
                     if r["gate_type"] == gtype]
            count = len(costs)
            out[gtype] = {"count": count,
                          "avg": round(sum(costs) / count) if count else 0}
        return out

    async def user_gate_counts(self, discord_id):
        out: dict[str, int] = {}
        for r in self._db["gate_entries"]:
            if r["user_id"] == discord_id:
                out[r["gate_type"]] = out.get(r["gate_type"], 0) + 1
        return out

    async def user_gate_cost_total(self, discord_id):
        return sum(r["cost"] for r in self._db["gate_entries"]
                   if r["user_id"] == discord_id)

    # ── activity ───────────────────────────────────────────────────────────
    async def add_activity(self, discord_id, metric, amount):
        d = self._db["activity_counters"].setdefault(str(discord_id), {})
        d[metric] = d.get(metric, 0) + amount
        self._flush()
        return d[metric]

    async def set_activity(self, discord_id, metric, value):
        d = self._db["activity_counters"].setdefault(str(discord_id), {})
        d[metric] = value
        self._flush()
        return value

    async def get_activity(self, discord_id, metric):
        return self._db["activity_counters"].get(str(discord_id), {}).get(metric, 0)

    async def voice_session_set(self, discord_id, since):
        self._db["voice_sessions"][str(discord_id)] = since.isoformat()
        self._flush()

    async def voice_session_end(self, discord_id):
        raw = self._db["voice_sessions"].pop(str(discord_id), None)
        self._flush()
        return _parse_dt(raw) if raw else None

    async def voice_sessions_all(self):
        return {int(k): _parse_dt(v)
                for k, v in self._db["voice_sessions"].items()}

    async def get_streak(self, discord_id):
        r = self._db["streak_stats"].get(str(discord_id))
        if r is None:
            return None
        return {"current_streak": r["current_streak"],
                "last_active_date": r["last_active_date"],
                "max_streak": r["max_streak"]}

    async def set_streak(self, discord_id, current_streak, last_active_date, max_streak):
        self._db["streak_stats"][str(discord_id)] = {
            "current_streak": current_streak,
            "last_active_date": last_active_date,
            "max_streak": max_streak}
        self._flush()

    async def get_night(self, discord_id):
        r = self._db["night_stats"].get(str(discord_id))
        if r is None:
            return None
        return {"night_count": r["night_count"],
                "last_night_date": r["last_night_date"]}

    async def set_night(self, discord_id, night_count, last_night_date):
        self._db["night_stats"][str(discord_id)] = {
            "night_count": night_count,
            "last_night_date": last_night_date}
        self._flush()

    # ── achievements ───────────────────────────────────────────────────────
    async def unlock_achievement(self, discord_id, achievement_id):
        lst = self._db["achievements"].setdefault(str(discord_id), [])
        if achievement_id in lst:
            return False
        lst.append(achievement_id)
        self._flush()
        return True

    async def has_achievement(self, discord_id, achievement_id):
        return achievement_id in self._db["achievements"].get(str(discord_id), [])

    async def get_user_achievements(self, discord_id):
        return set(self._db["achievements"].get(str(discord_id), []))

    async def list_achievement_holders(self):
        return {int(did): set(ids)
                for did, ids in self._db["achievements"].items() if ids}

    # ── kodex ──────────────────────────────────────────────────────────────
    async def confirm_kodex(self, discord_id):
        lst = self._db["kodex_confirmations"]
        if discord_id not in lst:
            lst.append(discord_id)
            self._flush()

    async def has_confirmed_kodex(self, discord_id):
        return discord_id in self._db["kodex_confirmations"]

    async def list_kodex_confirmed(self):
        return set(self._db["kodex_confirmations"])

    async def save_kodex_message(self, message_id, discord_id):
        self._db["kodex_messages"][str(message_id)] = discord_id
        self._flush()

    async def get_kodex_message_user(self, message_id):
        return self._db["kodex_messages"].get(str(message_id))

    # ── base timers ────────────────────────────────────────────────────────
    async def set_base_timer(self, map_name, end_time):
        self._db["base_timers"][map_name] = end_time.isoformat()
        self._flush()

    async def remove_base_timer(self, map_name):
        existed = map_name in self._db["base_timers"]
        self._db["base_timers"].pop(map_name, None)
        self._flush()
        return existed

    async def list_base_timers(self):
        return {m: _parse_dt(v) for m, v in self._db["base_timers"].items()}

    async def purge_expired_base_timers(self, now):
        removed = [m for m, v in self._db["base_timers"].items()
                   if _parse_dt(v) <= now]
        for m in removed:
            del self._db["base_timers"][m]
        self._flush()
        return removed

    # ── bulk export / import ───────────────────────────────────────────────
    @staticmethod
    def _max_id(rows) -> int:
        return max((r["id"] for r in rows), default=0)

    async def export_all(self) -> dict:
        users = copy.deepcopy(sorted(self._db["users"], key=lambda r: r["id"]))
        messages = copy.deepcopy(sorted(self._db["messages"], key=lambda r: r["id"]))
        stats = copy.deepcopy(sorted(self._db["stats"], key=lambda r: r["id"]))
        # backfill flags absent from pre-feature (legacy) stat rows so the
        # snapshot is always fully shaped for any importing backend.
        for s in stats:
            s.setdefault("targeted", False)
        gate_entries = copy.deepcopy(
            sorted(self._db["gate_entries"], key=lambda r: r["id"]))
        # backfill the laser flag absent from pre-feature (legacy) gate rows so
        # the snapshot is always fully shaped for any importing backend.
        for g in gate_entries:
            g.setdefault("laser_dropped", None)
            g.setdefault("drops", None)
        return {
            "users": users,
            "messages": messages,
            "stats": stats,
            "user_stats": copy.deepcopy(self._db["user_stats"]),
            "stat_totals": copy.deepcopy(self._db["stat_totals"]),
            "stat_last_post": copy.deepcopy(self._db["stat_last_post"]),
            "target_stats": copy.deepcopy(self._db["target_stats"]),
            "gate_entries": gate_entries,
            "activity_counters": copy.deepcopy(self._db["activity_counters"]),
            "streak_stats": copy.deepcopy(self._db["streak_stats"]),
            "night_stats": copy.deepcopy(self._db["night_stats"]),
            "achievements": {did: sorted(ids)
                             for did, ids in self._db["achievements"].items() if ids},
            "kodex_confirmations": sorted(self._db["kodex_confirmations"]),
            "kodex_messages": copy.deepcopy(self._db["kodex_messages"]),
            "base_timers": copy.deepcopy(self._db["base_timers"]),
            "channel_messages": copy.deepcopy(self._db["channel_messages"]),
            "gate_pending": copy.deepcopy(self._db["gate_pending"]),
            "runtime_config": copy.deepcopy(self._db["runtime_config"]),
            "content_texts": copy.deepcopy(self._db["content_texts"]),
            "color_config": copy.deepcopy(self._db["color_config"]),
            "achievement_defs": copy.deepcopy(self._db["achievement_defs"]),
            "seq": {
                "user": self._max_id(users),
                "message": self._max_id(messages),
                "stat": self._max_id(stats),
                "gate": self._max_id(gate_entries),
            },
        }

    async def import_all(self, snapshot: dict) -> None:
        self._db["users"] = copy.deepcopy(snapshot["users"])
        self._db["messages"] = copy.deepcopy(snapshot["messages"])
        self._db["stats"] = copy.deepcopy(snapshot["stats"])
        self._db["gate_entries"] = copy.deepcopy(snapshot["gate_entries"])
        self._db["user_stats"] = copy.deepcopy(snapshot["user_stats"])
        self._db["stat_totals"] = copy.deepcopy(snapshot["stat_totals"])
        self._db["stat_last_post"] = copy.deepcopy(snapshot["stat_last_post"])
        self._db["target_stats"] = copy.deepcopy(snapshot["target_stats"])
        self._db["activity_counters"] = copy.deepcopy(snapshot.get("activity_counters", {}))
        self._db["streak_stats"] = copy.deepcopy(snapshot.get("streak_stats", {}))
        self._db["night_stats"] = copy.deepcopy(snapshot.get("night_stats", {}))
        self._db["achievements"] = copy.deepcopy(snapshot.get("achievements", {}))
        self._db["kodex_confirmations"] = copy.deepcopy(
            snapshot.get("kodex_confirmations", []))
        self._db["kodex_messages"] = copy.deepcopy(
            snapshot.get("kodex_messages", {}))
        self._db["base_timers"] = copy.deepcopy(snapshot.get("base_timers", {}))
        self._db["channel_messages"] = copy.deepcopy(
            snapshot.get("channel_messages", {}))
        self._db["gate_pending"] = copy.deepcopy(
            snapshot.get("gate_pending", {}))
        self._db["runtime_config"] = copy.deepcopy(
            snapshot.get("runtime_config", {}))
        self._db["content_texts"] = copy.deepcopy(
            snapshot.get("content_texts", {}))
        self._db["color_config"] = copy.deepcopy(
            snapshot.get("color_config", {}))
        self._db["achievement_defs"] = copy.deepcopy(
            snapshot.get("achievement_defs", {}))
        self._db["seq"] = dict(snapshot["seq"])
        self._flush()

    async def clear(self) -> None:
        self._db = self._empty()
        self._flush()
