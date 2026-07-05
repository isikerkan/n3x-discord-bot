import json
import os
import tempfile
from datetime import datetime, timezone

from n3x_bot.models import User, Stat, Message
from n3x_bot.storage.base import StatsRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(v):
    return datetime.fromisoformat(v) if v else None


class JsonRepository(StatsRepository):
    def __init__(self, path: str):
        self.path = path
        self._db = None

    # ── lifecycle / persistence ────────────────────────────────────────────
    def _empty(self) -> dict:
        return {
            "seq": {"user": 0, "message": 0, "stat": 0},
            "users": [], "messages": [], "stats": [],
            "user_stats": {}, "stat_totals": {}, "stat_last_post": {},
            "target_stats": {},
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
