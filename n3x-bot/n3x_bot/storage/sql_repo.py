import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, select, insert, update, delete, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import create_async_engine

from n3x_bot.models import User, Stat, Message
from n3x_bot.storage.base import GATE_TYPES, StatsRepository
from n3x_bot.storage import schema as sc


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v) if v else None


def _as_aware_utc(dt: datetime | None) -> datetime | None:
    """Normalize a datetime read back from the DB to tz-aware UTC.

    SQLite's `DateTime(timezone=True)` column round-trips as a naive
    datetime (SQLite has no native timezone type) even though it was
    written as aware UTC, while Postgres/asyncpg returns it aware already.
    Comparing an aware threshold against a naive value raises TypeError, so
    normalize here to keep dedup logic identical across both backends.
    """
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _pk_where(table, pk_values: dict):
    return and_(*(table.c[col] == val for col, val in pk_values.items()))


class SqlRepository(StatsRepository):
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.engine = None

    async def connect(self) -> None:
        self.engine = create_async_engine(self.database_url)
        async with self.engine.begin() as conn:
            await conn.run_sync(sc.metadata.create_all)

    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()

    # ── row -> model helpers ───────────────────────────────────────────────
    @staticmethod
    def _stat(r) -> Stat:
        return Stat(id=r.id, key=r.key, name=r.name, message_id=r.message_id,
                    targeted=r.targeted,
                    archived_at=r.archived_at, created_at=r.created_at)

    @staticmethod
    def _msg(r) -> Message:
        return Message(id=r.id, name=r.name, template=r.template,
                       archived_at=r.archived_at, created_at=r.created_at)

    @staticmethod
    def _user(r) -> User:
        return User(id=r.id, discord_id=r.discord_id, display_name=r.display_name,
                    archived_at=r.archived_at, created_at=r.created_at)

    # ── upsert helpers ─────────────────────────────────────────────────────
    async def _upsert(self, conn, table, pk_values: dict, values: dict) -> None:
        where = _pk_where(table, pk_values)
        exists = (await conn.execute(select(table).where(where))).one_or_none()
        if exists is None:
            await conn.execute(insert(table).values(**pk_values, **values))
        else:
            await conn.execute(update(table).where(where).values(**values))

    async def _insert_ignore(self, conn, table, values: dict) -> bool:
        ins = (pg_insert if self.engine.dialect.name == "postgresql"
               else sqlite_insert)
        result = await conn.execute(
            ins(table).values(**values).on_conflict_do_nothing())
        return result.rowcount == 1

    # ── messages ───────────────────────────────────────────────────────────
    async def create_message(self, name, template) -> Message:
        async with self.engine.begin() as conn:
            res = await conn.execute(
                insert(sc.messages).values(name=name, template=template,
                                           created_at=_now())
                .returning(sc.messages))
            return self._msg(res.one())

    async def get_message(self, message_id):
        async with self.engine.connect() as conn:
            r = (await conn.execute(
                select(sc.messages).where(sc.messages.c.id == message_id))).one_or_none()
            return self._msg(r) if r else None

    async def list_messages(self, include_archived=False):
        q = select(sc.messages)
        if not include_archived:
            q = q.where(sc.messages.c.archived_at.is_(None))
        async with self.engine.connect() as conn:
            return [self._msg(r) for r in await conn.execute(q)]

    async def update_message(self, message_id, name=None, template=None):
        vals = {}
        if name is not None:
            vals["name"] = name
        if template is not None:
            vals["template"] = template
        async with self.engine.begin() as conn:
            if vals:
                await conn.execute(update(sc.messages)
                                   .where(sc.messages.c.id == message_id).values(**vals))
            r = (await conn.execute(
                select(sc.messages).where(sc.messages.c.id == message_id))).one_or_none()
            if r is None:
                raise KeyError(message_id)
            return self._msg(r)

    async def archive_message(self, message_id):
        async with self.engine.begin() as conn:
            await conn.execute(update(sc.messages)
                               .where(sc.messages.c.id == message_id)
                               .values(archived_at=_now()))

    async def delete_message(self, message_id):
        async with self.engine.begin() as conn:
            await conn.execute(delete(sc.messages).where(sc.messages.c.id == message_id))

    # ── stats ──────────────────────────────────────────────────────────────
    async def create_stat(self, key, name, message_id=None, targeted=False) -> Stat:
        async with self.engine.begin() as conn:
            res = await conn.execute(
                insert(sc.stats).values(key=key, name=name, message_id=message_id,
                                        targeted=targeted,
                                        created_at=_now()).returning(sc.stats))
            return self._stat(res.one())

    async def get_stat(self, key):
        async with self.engine.connect() as conn:
            r = (await conn.execute(
                select(sc.stats).where(sc.stats.c.key == key))).one_or_none()
            return self._stat(r) if r else None

    async def list_stats(self, include_archived=False):
        q = select(sc.stats)
        if not include_archived:
            q = q.where(sc.stats.c.archived_at.is_(None))
        async with self.engine.connect() as conn:
            return [self._stat(r) for r in await conn.execute(q)]

    async def update_stat(self, key, name=None):
        async with self.engine.begin() as conn:
            if name is not None:
                await conn.execute(update(sc.stats)
                                   .where(sc.stats.c.key == key).values(name=name))
            r = (await conn.execute(
                select(sc.stats).where(sc.stats.c.key == key))).one_or_none()
            if r is None:
                raise KeyError(key)
            return self._stat(r)

    async def set_stat_message(self, key, message_id):
        async with self.engine.begin() as conn:
            await conn.execute(update(sc.stats)
                               .where(sc.stats.c.key == key)
                               .values(message_id=message_id))
            r = (await conn.execute(
                select(sc.stats).where(sc.stats.c.key == key))).one_or_none()
            if r is None:
                raise KeyError(key)
            return self._stat(r)

    async def archive_stat(self, key):
        async with self.engine.begin() as conn:
            await conn.execute(update(sc.stats)
                               .where(sc.stats.c.key == key).values(archived_at=_now()))

    async def unarchive_stat(self, key):
        async with self.engine.begin() as conn:
            await conn.execute(update(sc.stats)
                               .where(sc.stats.c.key == key).values(archived_at=None))

    async def delete_stat(self, key):
        async with self.engine.begin() as conn:
            await conn.execute(delete(sc.stats).where(sc.stats.c.key == key))

    # ── users ──────────────────────────────────────────────────────────────
    async def upsert_user(self, discord_id, display_name) -> User:
        async with self.engine.begin() as conn:
            r = (await conn.execute(select(sc.users)
                 .where(sc.users.c.discord_id == discord_id))).one_or_none()
            if r is None:
                res = await conn.execute(
                    insert(sc.users).values(discord_id=discord_id,
                                            display_name=display_name,
                                            created_at=_now()).returning(sc.users))
                return self._user(res.one())
            await conn.execute(update(sc.users)
                               .where(sc.users.c.discord_id == discord_id)
                               .values(display_name=display_name, archived_at=None))
            r = (await conn.execute(select(sc.users)
                 .where(sc.users.c.discord_id == discord_id))).one()
            return self._user(r)

    async def get_user(self, discord_id):
        async with self.engine.connect() as conn:
            r = (await conn.execute(select(sc.users)
                 .where(sc.users.c.discord_id == discord_id))).one_or_none()
            return self._user(r) if r else None

    async def list_users(self, include_archived=False):
        q = select(sc.users)
        if not include_archived:
            q = q.where(sc.users.c.archived_at.is_(None))
        async with self.engine.connect() as conn:
            return [self._user(r) for r in await conn.execute(q)]

    async def archive_user(self, discord_id):
        async with self.engine.begin() as conn:
            await conn.execute(update(sc.users)
                               .where(sc.users.c.discord_id == discord_id)
                               .values(archived_at=_now()))

    async def delete_user(self, discord_id):
        async with self.engine.begin() as conn:
            await conn.execute(delete(sc.users)
                               .where(sc.users.c.discord_id == discord_id))

    # ── tracking ───────────────────────────────────────────────────────────
    async def record_use(self, discord_id, display_name, stat_key):
        async with self.engine.begin() as conn:
            stat = (await conn.execute(select(sc.stats)
                    .where(sc.stats.c.key == stat_key))).one_or_none()
            if stat is None:
                raise KeyError(stat_key)
            user = (await conn.execute(select(sc.users)
                    .where(sc.users.c.discord_id == discord_id))).one_or_none()
            if user is None:
                res = await conn.execute(
                    insert(sc.users).values(discord_id=discord_id,
                                            display_name=display_name,
                                            created_at=_now()).returning(sc.users))
                user = res.one()
            else:
                await conn.execute(update(sc.users)
                                   .where(sc.users.c.id == user.id)
                                   .values(display_name=display_name, archived_at=None))
            # user_stats upsert
            us = (await conn.execute(select(sc.user_stats).where(
                (sc.user_stats.c.user_id == user.id) &
                (sc.user_stats.c.stat_id == stat.id)))).one_or_none()
            if us is None:
                user_count = 1
                await conn.execute(insert(sc.user_stats).values(
                    user_id=user.id, stat_id=stat.id, count=1))
            else:
                user_count = us.count + 1
                await conn.execute(update(sc.user_stats).where(
                    (sc.user_stats.c.user_id == user.id) &
                    (sc.user_stats.c.stat_id == stat.id)).values(count=user_count))
            # stat_totals upsert
            tot = (await conn.execute(select(sc.stat_totals)
                   .where(sc.stat_totals.c.stat_id == stat.id))).one_or_none()
            if tot is None:
                total = 1
                await conn.execute(insert(sc.stat_totals)
                                   .values(stat_id=stat.id, count=1))
            else:
                total = tot.count + 1
                await conn.execute(update(sc.stat_totals)
                                   .where(sc.stat_totals.c.stat_id == stat.id)
                                   .values(count=total))
            return user_count, total

    async def get_user_stats(self, discord_id):
        async with self.engine.connect() as conn:
            rows = await conn.execute(
                select(sc.stats.c.key, sc.user_stats.c.count)
                .select_from(sc.user_stats
                             .join(sc.users, sc.users.c.id == sc.user_stats.c.user_id)
                             .join(sc.stats, sc.stats.c.id == sc.user_stats.c.stat_id))
                .where(sc.users.c.discord_id == discord_id))
            return {key: count for key, count in rows}

    async def get_total(self, stat_key):
        async with self.engine.connect() as conn:
            r = (await conn.execute(
                select(sc.stat_totals.c.count)
                .select_from(sc.stat_totals
                             .join(sc.stats, sc.stats.c.id == sc.stat_totals.c.stat_id))
                .where(sc.stats.c.key == stat_key))).one_or_none()
            return r.count if r else 0

    async def get_last_post(self, stat_key):
        async with self.engine.connect() as conn:
            r = (await conn.execute(
                select(sc.stat_last_post.c.discord_message_id,
                       sc.stat_last_post.c.channel_id)
                .select_from(sc.stat_last_post
                             .join(sc.stats, sc.stats.c.id == sc.stat_last_post.c.stat_id))
                .where(sc.stats.c.key == stat_key))).one_or_none()
            return (r.discord_message_id, r.channel_id) if r else None

    async def set_last_post(self, stat_key, discord_message_id, channel_id):
        async with self.engine.begin() as conn:
            stat = (await conn.execute(select(sc.stats)
                    .where(sc.stats.c.key == stat_key))).one_or_none()
            if stat is None:
                raise KeyError(stat_key)
            await self._upsert(conn, sc.stat_last_post, {"stat_id": stat.id},
                               {"discord_message_id": discord_message_id,
                                "channel_id": channel_id})

    # ── channel messages ──────────────────────────────────────────────────
    async def set_channel_message(self, key, message_id, channel_id):
        async with self.engine.begin() as conn:
            await self._upsert(conn, sc.channel_messages, {"key": key},
                               {"message_id": message_id, "channel_id": channel_id})

    async def get_channel_message(self, key):
        async with self.engine.connect() as conn:
            r = (await conn.execute(
                select(sc.channel_messages.c.message_id,
                       sc.channel_messages.c.channel_id)
                .where(sc.channel_messages.c.key == key))).one_or_none()
            return (int(r.message_id), int(r.channel_id)) if r else None

    # ── gate pending ──────────────────────────────────────────────────────
    @staticmethod
    def _gate_pending_row(r) -> dict:
        return {"message_id": int(r.message_id), "channel_id": int(r.channel_id),
                "gate_type": r.gate_type, "cost": int(r.cost),
                "user_id": int(r.user_id), "username": r.username,
                "options": json.loads(r.options) if r.options else {}}

    async def set_gate_pending(self, message_id, *, channel_id, gate_type, cost,
                               user_id, username, options):
        async with self.engine.begin() as conn:
            await self._upsert(conn, sc.gate_pending, {"message_id": message_id},
                               {"channel_id": channel_id, "gate_type": gate_type,
                                "cost": cost, "user_id": user_id,
                                "username": username,
                                "options": json.dumps(options)})

    async def get_gate_pending(self, message_id):
        async with self.engine.connect() as conn:
            r = (await conn.execute(select(sc.gate_pending)
                 .where(sc.gate_pending.c.message_id == message_id))).one_or_none()
            return self._gate_pending_row(r) if r else None

    async def delete_gate_pending(self, message_id):
        async with self.engine.begin() as conn:
            exists = (await conn.execute(select(sc.gate_pending.c.message_id)
                      .where(sc.gate_pending.c.message_id == message_id))).one_or_none()
            await conn.execute(delete(sc.gate_pending)
                               .where(sc.gate_pending.c.message_id == message_id))
            return exists is not None

    async def all_gate_pending(self):
        async with self.engine.connect() as conn:
            rows = await conn.execute(select(sc.gate_pending)
                                      .order_by(sc.gate_pending.c.message_id.asc()))
            return [self._gate_pending_row(r) for r in rows]

    # ── runtime config ────────────────────────────────────────────────────
    async def set_runtime_config(self, key, value):
        async with self.engine.begin() as conn:
            await self._upsert(conn, sc.runtime_config, {"key": key},
                               {"value": value})

    async def get_runtime_config(self, key):
        async with self.engine.connect() as conn:
            r = (await conn.execute(select(sc.runtime_config.c.value)
                 .where(sc.runtime_config.c.key == key))).one_or_none()
            return r.value if r else None

    async def delete_runtime_config(self, key):
        async with self.engine.begin() as conn:
            exists = (await conn.execute(select(sc.runtime_config.c.key)
                      .where(sc.runtime_config.c.key == key))).one_or_none()
            await conn.execute(delete(sc.runtime_config)
                               .where(sc.runtime_config.c.key == key))
            return exists is not None

    async def all_runtime_config(self):
        async with self.engine.connect() as conn:
            return {r.key: r.value
                    for r in await conn.execute(select(sc.runtime_config))}

    # ── content texts ─────────────────────────────────────────────────────
    async def set_content_text(self, key, value):
        async with self.engine.begin() as conn:
            await self._upsert(conn, sc.content_texts, {"key": key},
                               {"value": value})

    async def get_content_text(self, key):
        async with self.engine.connect() as conn:
            r = (await conn.execute(select(sc.content_texts.c.value)
                 .where(sc.content_texts.c.key == key))).one_or_none()
            return r.value if r else None

    async def delete_content_text(self, key):
        async with self.engine.begin() as conn:
            exists = (await conn.execute(select(sc.content_texts.c.key)
                      .where(sc.content_texts.c.key == key))).one_or_none()
            await conn.execute(delete(sc.content_texts)
                               .where(sc.content_texts.c.key == key))
            return exists is not None

    async def all_content_texts(self):
        async with self.engine.connect() as conn:
            return {r.key: r.value
                    for r in await conn.execute(select(sc.content_texts))}

    # ── color config ──────────────────────────────────────────────────────
    async def set_color_config(self, key, value):
        async with self.engine.begin() as conn:
            await self._upsert(conn, sc.color_config, {"key": key},
                               {"value": value})

    async def get_color_config(self, key):
        async with self.engine.connect() as conn:
            r = (await conn.execute(select(sc.color_config.c.value)
                 .where(sc.color_config.c.key == key))).one_or_none()
            return r.value if r else None

    async def delete_color_config(self, key):
        async with self.engine.begin() as conn:
            exists = (await conn.execute(select(sc.color_config.c.key)
                      .where(sc.color_config.c.key == key))).one_or_none()
            await conn.execute(delete(sc.color_config)
                               .where(sc.color_config.c.key == key))
            return exists is not None

    async def all_color_config(self):
        async with self.engine.connect() as conn:
            return {r.key: r.value
                    for r in await conn.execute(select(sc.color_config))}

    # ── achievement definitions ────────────────────────────────────────────
    async def set_achievement_def(self, id, *, category, metric, threshold,
                                  title, secret, color=None):
        async with self.engine.begin() as conn:
            await self._upsert(conn, sc.achievement_defs, {"id": id},
                               {"category": category, "metric": metric,
                                "threshold": threshold, "title": title,
                                "secret": secret, "color": color})

    async def get_achievement_def(self, id):
        async with self.engine.connect() as conn:
            r = (await conn.execute(select(sc.achievement_defs)
                 .where(sc.achievement_defs.c.id == id))).one_or_none()
            if r is None:
                return None
            return {"id": r.id, "category": r.category, "metric": r.metric,
                    "threshold": int(r.threshold), "title": r.title,
                    "secret": bool(r.secret), "color": r.color}

    async def delete_achievement_def(self, id):
        async with self.engine.begin() as conn:
            exists = (await conn.execute(select(sc.achievement_defs.c.id)
                      .where(sc.achievement_defs.c.id == id))).one_or_none()
            await conn.execute(delete(sc.achievement_defs)
                               .where(sc.achievement_defs.c.id == id))
            return exists is not None

    async def all_achievement_defs(self):
        async with self.engine.connect() as conn:
            rows = await conn.execute(select(sc.achievement_defs)
                                      .order_by(sc.achievement_defs.c.id.asc()))
            return [{"id": r.id, "category": r.category, "metric": r.metric,
                     "threshold": int(r.threshold), "title": r.title,
                     "secret": bool(r.secret), "color": r.color}
                    for r in rows]

    async def replace_achievement_defs(self, defs):
        async with self.engine.begin() as conn:
            await conn.execute(delete(sc.achievement_defs))
            for d in defs:
                await conn.execute(insert(sc.achievement_defs).values(
                    id=d["id"], category=d["category"], metric=d["metric"],
                    threshold=d["threshold"], title=d["title"],
                    secret=d["secret"], color=d.get("color")))

    # ── target tracking ───────────────────────────────────────────────────
    async def record_target_use(self, target_discord_id, stat_key):
        async with self.engine.begin() as conn:
            stat = (await conn.execute(select(sc.stats)
                    .where(sc.stats.c.key == stat_key))).one_or_none()
            if stat is None:
                raise KeyError(stat_key)
            row = (await conn.execute(select(sc.target_stats).where(
                (sc.target_stats.c.target_discord_id == target_discord_id) &
                (sc.target_stats.c.stat_id == stat.id)))).one_or_none()
            if row is None:
                new_count = 1
                await conn.execute(insert(sc.target_stats).values(
                    target_discord_id=target_discord_id, stat_id=stat.id, count=1))
            else:
                new_count = row.count + 1
                await conn.execute(update(sc.target_stats).where(
                    (sc.target_stats.c.target_discord_id == target_discord_id) &
                    (sc.target_stats.c.stat_id == stat.id)).values(count=new_count))
            return new_count

    async def get_target_total(self, target_discord_id, stat_key):
        async with self.engine.connect() as conn:
            r = (await conn.execute(
                select(sc.target_stats.c.count)
                .select_from(sc.target_stats
                             .join(sc.stats, sc.stats.c.id == sc.target_stats.c.stat_id))
                .where((sc.stats.c.key == stat_key) &
                       (sc.target_stats.c.target_discord_id == target_discord_id)))
                ).one_or_none()
            return r.count if r else 0

    # ── gate tracker ───────────────────────────────────────────────────────
    async def add_gate_entry(self, gate_type, cost, user_id, username,
                             dedup_window_seconds=30, laser_dropped=None,
                             drops=None):
        if drops is None and laser_dropped is not None:
            drops = {"laser": bool(laser_dropped)}
        laser_col = drops.get("laser") if drops else None
        threshold = _now() - timedelta(seconds=dedup_window_seconds)
        async with self.engine.begin() as conn:
            candidates = await conn.execute(select(sc.gate_entries.c.created_at).where(
                (sc.gate_entries.c.user_id == user_id) &
                (sc.gate_entries.c.gate_type == gate_type) &
                (sc.gate_entries.c.cost == cost)))
            for row in candidates:
                created = _as_aware_utc(row.created_at)
                if created is not None and created > threshold:
                    return False
            await conn.execute(insert(sc.gate_entries).values(
                gate_type=gate_type, cost=cost, user_id=user_id,
                username=username, laser_dropped=laser_col,
                drops=json.dumps(drops) if drops else None,
                created_at=_now()))
            return True

    async def gate_drop_stats(self, gate_type):
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(sc.gate_entries.c.cost, sc.gate_entries.c.drops,
                       sc.gate_entries.c.laser_dropped)
                .where(sc.gate_entries.c.gate_type == gate_type))).all()
        count = len(rows)
        avg = round(sum(r.cost for r in rows) / count) if count else 0
        observed: set[str] = set()
        trues: dict[str, int] = {}
        for r in rows:
            if r.drops:
                drop_map = json.loads(r.drops)
            elif r.laser_dropped is not None:
                drop_map = {"laser": bool(r.laser_dropped)}
            else:
                drop_map = {}
            for item, val in drop_map.items():
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
        async with self.engine.connect() as conn:
            min_row = (await conn.execute(
                select(sc.gate_entries.c.cost, sc.gate_entries.c.user_id)
                .where(sc.gate_entries.c.gate_type == gate_type)
                .order_by(sc.gate_entries.c.cost.asc(), sc.gate_entries.c.id.asc())
                .limit(1))).one_or_none()
            if min_row is None:
                return None
            max_row = (await conn.execute(
                select(sc.gate_entries.c.cost, sc.gate_entries.c.user_id)
                .where(sc.gate_entries.c.gate_type == gate_type)
                .order_by(sc.gate_entries.c.cost.desc(), sc.gate_entries.c.id.asc())
                .limit(1))).one_or_none()
            if max_row is None:
                return None
            return {"min_cost": int(min_row.cost), "min_user": int(min_row.user_id),
                    "max_cost": int(max_row.cost), "max_user": int(max_row.user_id)}

    async def list_gate_costs(self, gate_type):
        async with self.engine.connect() as conn:
            rows = await conn.execute(
                select(sc.gate_entries.c.cost)
                .where(sc.gate_entries.c.gate_type == gate_type)
                .order_by(sc.gate_entries.c.id.asc()))
            return [r.cost for r in rows]

    async def list_gate_entries(self, gate_type, since=None, until=None):
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(sc.gate_entries.c.cost, sc.gate_entries.c.drops,
                       sc.gate_entries.c.laser_dropped,
                       sc.gate_entries.c.created_at)
                .where(sc.gate_entries.c.gate_type == gate_type)
                .order_by(sc.gate_entries.c.created_at.asc(),
                          sc.gate_entries.c.id.asc()))).all()
        since = _as_aware_utc(since)
        until = _as_aware_utc(until)
        out = []
        for r in rows:
            created = _as_aware_utc(r.created_at)
            if since is not None and created < since:
                continue
            if until is not None and created > until:
                continue
            if r.drops:
                drop_map = json.loads(r.drops)
            elif r.laser_dropped is not None:
                drop_map = {"laser": bool(r.laser_dropped)}
            else:
                drop_map = {}
            out.append({"cost": r.cost, "created_at": created,
                        "drops": drop_map})
        return out

    async def delete_gate_entry(self, gate_type, index):
        async with self.engine.begin() as conn:
            rows = (await conn.execute(
                select(sc.gate_entries.c.id)
                .where(sc.gate_entries.c.gate_type == gate_type)
                .order_by(sc.gate_entries.c.id.asc()))).all()
            if index < 1 or index > len(rows):
                return False
            target_id = rows[index - 1].id
            await conn.execute(delete(sc.gate_entries)
                               .where(sc.gate_entries.c.id == target_id))
            return True

    async def gate_totals(self):
        out = {}
        async with self.engine.connect() as conn:
            for gtype in GATE_TYPES:
                r = (await conn.execute(
                    select(func.count(sc.gate_entries.c.id),
                          func.avg(sc.gate_entries.c.cost))
                    .where(sc.gate_entries.c.gate_type == gtype))).one()
                count, avg = r[0], r[1]
                out[gtype] = {"count": count or 0,
                             "avg": round(avg) if avg else 0}
        return out

    async def list_gate_entries_full(self, gate_type=None):
        """All gate entries (optionally one gate), oldest first, WITH the user.
        ``[{gate_type, cost, user_id, username, drops, created_at}]`` — for the
        admin gate-log."""
        async with self.engine.connect() as conn:
            q = select(sc.gate_entries.c.gate_type, sc.gate_entries.c.cost,
                       sc.gate_entries.c.user_id, sc.gate_entries.c.username,
                       sc.gate_entries.c.drops, sc.gate_entries.c.laser_dropped,
                       sc.gate_entries.c.created_at)
            if gate_type is not None:
                q = q.where(sc.gate_entries.c.gate_type == gate_type)
            rows = (await conn.execute(
                q.order_by(sc.gate_entries.c.created_at.asc(),
                           sc.gate_entries.c.id.asc()))).all()
        out = []
        for r in rows:
            if r.drops:
                drop_map = json.loads(r.drops)
            elif r.laser_dropped is not None:
                drop_map = {"laser": bool(r.laser_dropped)}
            else:
                drop_map = {}
            out.append({"gate_type": r.gate_type, "cost": r.cost,
                        "user_id": r.user_id, "username": r.username,
                        "drops": drop_map,
                        "created_at": _as_aware_utc(r.created_at)})
        return out

    async def list_user_gate_entries(self, discord_id, gate_type):
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(sc.gate_entries.c.cost, sc.gate_entries.c.drops,
                       sc.gate_entries.c.laser_dropped,
                       sc.gate_entries.c.created_at)
                .where((sc.gate_entries.c.gate_type == gate_type) &
                       (sc.gate_entries.c.user_id == discord_id))
                .order_by(sc.gate_entries.c.created_at.asc(),
                          sc.gate_entries.c.id.asc()))).all()
        out = []
        for r in rows:
            if r.drops:
                drop_map = json.loads(r.drops)
            elif r.laser_dropped is not None:
                drop_map = {"laser": bool(r.laser_dropped)}
            else:
                drop_map = {}
            out.append({"cost": r.cost, "created_at": _as_aware_utc(r.created_at),
                        "drops": drop_map})
        return out

    async def user_gate_counts(self, discord_id):
        async with self.engine.connect() as conn:
            rows = await conn.execute(
                select(sc.gate_entries.c.gate_type, func.count())
                .where(sc.gate_entries.c.user_id == discord_id)
                .group_by(sc.gate_entries.c.gate_type))
            return {gate_type: count for gate_type, count in rows}

    async def user_gate_cost_total(self, discord_id):
        async with self.engine.connect() as conn:
            r = (await conn.execute(
                select(func.coalesce(func.sum(sc.gate_entries.c.cost), 0))
                .where(sc.gate_entries.c.user_id == discord_id))).scalar()
            return int(r or 0)

    # ── activity ───────────────────────────────────────────────────────────
    async def add_activity(self, discord_id, metric, amount):
        async with self.engine.begin() as conn:
            row = (await conn.execute(select(sc.activity_counters.c.count).where(
                (sc.activity_counters.c.discord_id == discord_id) &
                (sc.activity_counters.c.metric == metric)))).one_or_none()
            if row is None:
                new_total = amount
                await conn.execute(insert(sc.activity_counters).values(
                    discord_id=discord_id, metric=metric, count=amount))
            else:
                new_total = row.count + amount
                await conn.execute(update(sc.activity_counters).where(
                    (sc.activity_counters.c.discord_id == discord_id) &
                    (sc.activity_counters.c.metric == metric)).values(count=new_total))
            return new_total

    async def set_activity(self, discord_id, metric, value):
        async with self.engine.begin() as conn:
            row = (await conn.execute(select(sc.activity_counters.c.count).where(
                (sc.activity_counters.c.discord_id == discord_id) &
                (sc.activity_counters.c.metric == metric)))).one_or_none()
            if row is None:
                await conn.execute(insert(sc.activity_counters).values(
                    discord_id=discord_id, metric=metric, count=value))
            else:
                await conn.execute(update(sc.activity_counters).where(
                    (sc.activity_counters.c.discord_id == discord_id) &
                    (sc.activity_counters.c.metric == metric)).values(count=value))
            return value

    async def get_activity(self, discord_id, metric):
        async with self.engine.connect() as conn:
            r = (await conn.execute(select(sc.activity_counters.c.count).where(
                (sc.activity_counters.c.discord_id == discord_id) &
                (sc.activity_counters.c.metric == metric)))).one_or_none()
            return r.count if r else 0

    async def voice_session_set(self, discord_id, since):
        async with self.engine.begin() as conn:
            exists = (await conn.execute(select(sc.voice_sessions.c.discord_id)
                      .where(sc.voice_sessions.c.discord_id == discord_id))).one_or_none()
            if exists is None:
                await conn.execute(insert(sc.voice_sessions).values(
                    discord_id=discord_id, since=since))
            else:
                await conn.execute(update(sc.voice_sessions)
                                   .where(sc.voice_sessions.c.discord_id == discord_id)
                                   .values(since=since))

    async def voice_session_end(self, discord_id):
        async with self.engine.begin() as conn:
            r = (await conn.execute(select(sc.voice_sessions.c.since)
                 .where(sc.voice_sessions.c.discord_id == discord_id))).one_or_none()
            await conn.execute(delete(sc.voice_sessions)
                               .where(sc.voice_sessions.c.discord_id == discord_id))
            return r.since if r else None

    async def voice_sessions_all(self):
        async with self.engine.connect() as conn:
            rows = await conn.execute(select(sc.voice_sessions.c.discord_id,
                                             sc.voice_sessions.c.since))
            return {did: since for did, since in rows}

    async def get_streak(self, discord_id):
        async with self.engine.connect() as conn:
            r = (await conn.execute(select(sc.streak_stats)
                 .where(sc.streak_stats.c.discord_id == discord_id))).one_or_none()
            if r is None:
                return None
            return {"current_streak": r.current_streak,
                    "last_active_date": r.last_active_date,
                    "max_streak": r.max_streak}

    async def set_streak(self, discord_id, current_streak, last_active_date, max_streak):
        vals = {"current_streak": current_streak,
                "last_active_date": last_active_date,
                "max_streak": max_streak}
        async with self.engine.begin() as conn:
            await self._upsert(conn, sc.streak_stats, {"discord_id": discord_id}, vals)

    async def get_night(self, discord_id):
        async with self.engine.connect() as conn:
            r = (await conn.execute(select(sc.night_stats)
                 .where(sc.night_stats.c.discord_id == discord_id))).one_or_none()
            if r is None:
                return None
            return {"night_count": r.night_count,
                    "last_night_date": r.last_night_date}

    async def set_night(self, discord_id, night_count, last_night_date):
        vals = {"night_count": night_count, "last_night_date": last_night_date}
        async with self.engine.begin() as conn:
            await self._upsert(conn, sc.night_stats, {"discord_id": discord_id}, vals)

    # ── achievements ───────────────────────────────────────────────────────
    async def unlock_achievement(self, discord_id, achievement_id):
        async with self.engine.begin() as conn:
            return await self._insert_ignore(
                conn, sc.achievements,
                {"discord_id": discord_id, "achievement_id": achievement_id})

    async def has_achievement(self, discord_id, achievement_id):
        async with self.engine.connect() as conn:
            r = (await conn.execute(select(sc.achievements).where(
                (sc.achievements.c.discord_id == discord_id) &
                (sc.achievements.c.achievement_id == achievement_id)))).one_or_none()
            return r is not None

    async def get_user_achievements(self, discord_id):
        async with self.engine.connect() as conn:
            rows = await conn.execute(
                select(sc.achievements.c.achievement_id)
                .where(sc.achievements.c.discord_id == discord_id))
            return {r.achievement_id for r in rows}

    async def list_achievement_holders(self):
        out: dict[int, set[str]] = {}
        async with self.engine.connect() as conn:
            for r in await conn.execute(select(sc.achievements)):
                out.setdefault(r.discord_id, set()).add(r.achievement_id)
        return out

    # ── kodex ──────────────────────────────────────────────────────────────
    async def confirm_kodex(self, discord_id):
        async with self.engine.begin() as conn:
            await self._insert_ignore(conn, sc.kodex_confirmations,
                                      {"discord_id": discord_id})

    async def has_confirmed_kodex(self, discord_id):
        async with self.engine.connect() as conn:
            r = (await conn.execute(select(sc.kodex_confirmations).where(
                sc.kodex_confirmations.c.discord_id == discord_id))).one_or_none()
            return r is not None

    async def list_kodex_confirmed(self):
        async with self.engine.connect() as conn:
            rows = await conn.execute(select(sc.kodex_confirmations.c.discord_id))
            return {r.discord_id for r in rows}

    async def save_kodex_message(self, message_id, discord_id):
        async with self.engine.begin() as conn:
            await self._upsert(conn, sc.kodex_messages, {"message_id": message_id},
                               {"discord_id": discord_id})

    async def get_kodex_message_user(self, message_id):
        async with self.engine.connect() as conn:
            r = (await conn.execute(select(sc.kodex_messages.c.discord_id).where(
                sc.kodex_messages.c.message_id == message_id))).one_or_none()
            return int(r.discord_id) if r else None

    # ── base timers ────────────────────────────────────────────────────────
    async def set_base_timer(self, map_name, end_time):
        stored = _as_aware_utc(end_time).astimezone(timezone.utc)
        async with self.engine.begin() as conn:
            await self._upsert(conn, sc.base_timers, {"map_name": map_name},
                               {"end_time": stored})

    async def remove_base_timer(self, map_name):
        async with self.engine.begin() as conn:
            exists = (await conn.execute(select(sc.base_timers.c.map_name)
                      .where(sc.base_timers.c.map_name == map_name))).one_or_none()
            if exists is None:
                return False
            await conn.execute(delete(sc.base_timers)
                               .where(sc.base_timers.c.map_name == map_name))
            return True

    async def list_base_timers(self):
        async with self.engine.connect() as conn:
            rows = await conn.execute(select(sc.base_timers))
            return {r.map_name: _as_aware_utc(r.end_time) for r in rows}

    async def purge_expired_base_timers(self, now):
        threshold = _as_aware_utc(now)
        async with self.engine.begin() as conn:
            rows = (await conn.execute(select(sc.base_timers.c.map_name,
                                              sc.base_timers.c.end_time))).all()
            removed = [r.map_name for r in rows
                       if _as_aware_utc(r.end_time) <= threshold]
            if removed:
                await conn.execute(delete(sc.base_timers)
                                   .where(sc.base_timers.c.map_name.in_(removed)))
            return removed

    # ── bulk export / import ───────────────────────────────────────────────
    @staticmethod
    def _dt(dt: datetime | None) -> str | None:
        aware = _as_aware_utc(dt)
        return aware.isoformat() if aware is not None else None

    async def export_all(self) -> dict:
        async with self.engine.connect() as conn:
            users = [
                {"id": r.id, "discord_id": r.discord_id,
                 "display_name": r.display_name,
                 "archived_at": self._dt(r.archived_at),
                 "created_at": self._dt(r.created_at)}
                for r in await conn.execute(
                    select(sc.users).order_by(sc.users.c.id.asc()))
            ]
            messages = [
                {"id": r.id, "name": r.name, "template": r.template,
                 "archived_at": self._dt(r.archived_at),
                 "created_at": self._dt(r.created_at)}
                for r in await conn.execute(
                    select(sc.messages).order_by(sc.messages.c.id.asc()))
            ]
            stats = [
                {"id": r.id, "key": r.key, "name": r.name,
                 "message_id": r.message_id, "targeted": bool(r.targeted),
                 "archived_at": self._dt(r.archived_at),
                 "created_at": self._dt(r.created_at)}
                for r in await conn.execute(
                    select(sc.stats).order_by(sc.stats.c.id.asc()))
            ]
            user_stats: dict = {}
            for r in await conn.execute(select(sc.user_stats)):
                user_stats.setdefault(str(r.user_id), {})[str(r.stat_id)] = r.count
            stat_totals = {
                str(r.stat_id): r.count
                for r in await conn.execute(select(sc.stat_totals))
            }
            stat_last_post = {
                str(r.stat_id): [r.discord_message_id, r.channel_id]
                for r in await conn.execute(select(sc.stat_last_post))
            }
            target_stats: dict = {}
            for r in await conn.execute(select(sc.target_stats)):
                target_stats.setdefault(str(r.stat_id), {})[
                    str(r.target_discord_id)] = r.count
            gate_entries = [
                {"id": r.id, "gate_type": r.gate_type, "cost": r.cost,
                 "user_id": r.user_id, "username": r.username,
                 "laser_dropped": (None if r.laser_dropped is None
                                   else bool(r.laser_dropped)),
                 "drops": (json.loads(r.drops) if r.drops else None),
                 "created_at": self._dt(r.created_at)}
                for r in await conn.execute(
                    select(sc.gate_entries).order_by(sc.gate_entries.c.id.asc()))
            ]
            activity_counters: dict = {}
            for r in await conn.execute(select(sc.activity_counters)):
                activity_counters.setdefault(str(r.discord_id), {})[r.metric] = r.count
            streak_stats = {
                str(r.discord_id): {"current_streak": r.current_streak,
                                    "last_active_date": r.last_active_date,
                                    "max_streak": r.max_streak}
                for r in await conn.execute(select(sc.streak_stats))
            }
            night_stats = {
                str(r.discord_id): {"night_count": r.night_count,
                                    "last_night_date": r.last_night_date}
                for r in await conn.execute(select(sc.night_stats))
            }
            achievements: dict[str, list[str]] = {}
            for r in await conn.execute(select(sc.achievements)):
                achievements.setdefault(str(r.discord_id), []).append(r.achievement_id)
            achievements = {did: sorted(ids) for did, ids in achievements.items()}
            kodex_confirmations = [
                r.discord_id for r in await conn.execute(
                    select(sc.kodex_confirmations)
                    .order_by(sc.kodex_confirmations.c.discord_id.asc()))
            ]
            kodex_messages = {
                str(r.message_id): r.discord_id
                for r in await conn.execute(select(sc.kodex_messages))
            }
            base_timers = {
                r.map_name: self._dt(r.end_time)
                for r in await conn.execute(select(sc.base_timers))
            }
            channel_messages = {
                r.key: [int(r.message_id), int(r.channel_id)]
                for r in await conn.execute(select(sc.channel_messages))
            }
            gate_pending = {
                str(r.message_id): {
                    "channel_id": int(r.channel_id), "gate_type": r.gate_type,
                    "cost": int(r.cost), "user_id": int(r.user_id),
                    "username": r.username,
                    "options": json.loads(r.options) if r.options else {}}
                for r in await conn.execute(select(sc.gate_pending))
            }
            runtime_config = {
                r.key: r.value
                for r in await conn.execute(select(sc.runtime_config))
            }
            content_texts = {
                r.key: r.value
                for r in await conn.execute(select(sc.content_texts))
            }
            color_config = {
                r.key: r.value
                for r in await conn.execute(select(sc.color_config))
            }
            achievement_defs = {
                r.id: {"category": r.category, "metric": r.metric,
                       "threshold": int(r.threshold), "title": r.title,
                       "secret": bool(r.secret), "color": r.color}
                for r in await conn.execute(select(sc.achievement_defs))
            }
            seq = {}
            for key, table in (("user", sc.users), ("message", sc.messages),
                               ("stat", sc.stats), ("gate", sc.gate_entries)):
                m = (await conn.execute(select(func.max(table.c.id)))).scalar()
                seq[key] = m or 0
        return {
            "users": users, "messages": messages, "stats": stats,
            "user_stats": user_stats, "stat_totals": stat_totals,
            "stat_last_post": stat_last_post, "target_stats": target_stats,
            "gate_entries": gate_entries,
            "activity_counters": activity_counters, "streak_stats": streak_stats,
            "night_stats": night_stats, "achievements": achievements,
            "kodex_confirmations": kodex_confirmations,
            "kodex_messages": kodex_messages, "base_timers": base_timers,
            "channel_messages": channel_messages,
            "gate_pending": gate_pending,
            "runtime_config": runtime_config,
            "content_texts": content_texts,
            "color_config": color_config,
            "achievement_defs": achievement_defs,
            "seq": seq,
        }

    async def import_all(self, snapshot: dict) -> None:
        async with self.engine.begin() as conn:
            for r in snapshot["messages"]:
                await conn.execute(insert(sc.messages).values(
                    id=r["id"], name=r["name"], template=r["template"],
                    archived_at=_parse_dt(r["archived_at"]),
                    created_at=_parse_dt(r["created_at"])))
            for r in snapshot["users"]:
                await conn.execute(insert(sc.users).values(
                    id=r["id"], discord_id=r["discord_id"],
                    display_name=r["display_name"],
                    archived_at=_parse_dt(r["archived_at"]),
                    created_at=_parse_dt(r["created_at"])))
            for r in snapshot["stats"]:
                await conn.execute(insert(sc.stats).values(
                    id=r["id"], key=r["key"], name=r["name"],
                    message_id=r["message_id"], targeted=r.get("targeted", False),
                    archived_at=_parse_dt(r["archived_at"]),
                    created_at=_parse_dt(r["created_at"])))
            for uid, inner in snapshot["user_stats"].items():
                for sid, count in inner.items():
                    await conn.execute(insert(sc.user_stats).values(
                        user_id=int(uid), stat_id=int(sid), count=count))
            for sid, count in snapshot["stat_totals"].items():
                await conn.execute(insert(sc.stat_totals).values(
                    stat_id=int(sid), count=count))
            for sid, v in snapshot["stat_last_post"].items():
                await conn.execute(insert(sc.stat_last_post).values(
                    stat_id=int(sid), discord_message_id=v[0], channel_id=v[1]))
            for sid, inner in snapshot["target_stats"].items():
                for tid, count in inner.items():
                    await conn.execute(insert(sc.target_stats).values(
                        target_discord_id=int(tid), stat_id=int(sid), count=count))
            for r in snapshot["gate_entries"]:
                await conn.execute(insert(sc.gate_entries).values(
                    id=r["id"], gate_type=r["gate_type"], cost=r["cost"],
                    user_id=r["user_id"], username=r["username"],
                    laser_dropped=r.get("laser_dropped"),
                    drops=json.dumps(r["drops"]) if r.get("drops") else None,
                    created_at=_parse_dt(r["created_at"])))
            for did, metrics in snapshot.get("activity_counters", {}).items():
                for metric, count in metrics.items():
                    await conn.execute(insert(sc.activity_counters).values(
                        discord_id=int(did), metric=metric, count=count))
            for did, v in snapshot.get("streak_stats", {}).items():
                await conn.execute(insert(sc.streak_stats).values(
                    discord_id=int(did), current_streak=v["current_streak"],
                    last_active_date=v["last_active_date"],
                    max_streak=v["max_streak"]))
            for did, v in snapshot.get("night_stats", {}).items():
                await conn.execute(insert(sc.night_stats).values(
                    discord_id=int(did), night_count=v["night_count"],
                    last_night_date=v["last_night_date"]))
            for did, ids in snapshot.get("achievements", {}).items():
                for aid in ids:
                    await conn.execute(insert(sc.achievements).values(
                        discord_id=int(did), achievement_id=aid))
            for did in snapshot.get("kodex_confirmations", []):
                await conn.execute(insert(sc.kodex_confirmations).values(
                    discord_id=did))
            for mid, did in snapshot.get("kodex_messages", {}).items():
                await conn.execute(insert(sc.kodex_messages).values(
                    message_id=int(mid), discord_id=did))
            for map_name, iso in snapshot.get("base_timers", {}).items():
                await conn.execute(insert(sc.base_timers).values(
                    map_name=map_name,
                    end_time=_as_aware_utc(_parse_dt(iso)).astimezone(timezone.utc)))
            for key, v in snapshot.get("channel_messages", {}).items():
                await conn.execute(insert(sc.channel_messages).values(
                    key=key, message_id=v[0], channel_id=v[1]))
            for mid, v in snapshot.get("gate_pending", {}).items():
                await conn.execute(insert(sc.gate_pending).values(
                    message_id=int(mid), channel_id=v["channel_id"],
                    gate_type=v["gate_type"], cost=v["cost"],
                    user_id=v["user_id"], username=v["username"],
                    options=json.dumps(v["options"])))
            for key, value in snapshot.get("runtime_config", {}).items():
                await conn.execute(insert(sc.runtime_config).values(
                    key=key, value=value))
            for key, value in snapshot.get("content_texts", {}).items():
                await conn.execute(insert(sc.content_texts).values(
                    key=key, value=value))
            for key, value in snapshot.get("color_config", {}).items():
                await conn.execute(insert(sc.color_config).values(
                    key=key, value=value))
            for aid, v in snapshot.get("achievement_defs", {}).items():
                await conn.execute(insert(sc.achievement_defs).values(
                    id=aid, category=v["category"], metric=v["metric"],
                    threshold=v["threshold"], title=v["title"],
                    secret=v["secret"], color=v.get("color")))
            if self.engine.dialect.name == "postgresql":
                for tbl, key in (("users", "user"), ("messages", "message"),
                                 ("stats", "stat"), ("gate_entries", "gate")):
                    if snapshot["seq"].get(key, 0) > 0:
                        await conn.execute(
                            text("SELECT setval(pg_get_serial_sequence(:t, 'id'), :v)"),
                            {"t": tbl, "v": snapshot["seq"][key]})

    async def clear(self) -> None:
        async with self.engine.begin() as conn:
            for table in (sc.achievements,
                          sc.gate_entries, sc.user_stats, sc.stat_totals,
                          sc.stat_last_post, sc.target_stats, sc.stats,
                          sc.users, sc.messages,
                          sc.activity_counters, sc.streak_stats, sc.night_stats,
                          sc.kodex_confirmations, sc.kodex_messages,
                          sc.base_timers, sc.channel_messages,
                          sc.gate_pending,
                          sc.runtime_config, sc.content_texts,
                          sc.color_config,
                          sc.achievement_defs):
                await conn.execute(delete(table))
