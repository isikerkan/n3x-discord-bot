from datetime import datetime, timezone

from sqlalchemy import select, insert, update, delete
from sqlalchemy.ext.asyncio import create_async_engine

from n3x_bot.models import User, Stat, Message
from n3x_bot.storage.base import StatsRepository
from n3x_bot.storage import schema as sc


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
                    archived_at=r.archived_at, created_at=r.created_at)

    @staticmethod
    def _msg(r) -> Message:
        return Message(id=r.id, name=r.name, template=r.template,
                       archived_at=r.archived_at, created_at=r.created_at)

    @staticmethod
    def _user(r) -> User:
        return User(id=r.id, discord_id=r.discord_id, display_name=r.display_name,
                    archived_at=r.archived_at, created_at=r.created_at)

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
    async def create_stat(self, key, name, message_id=None) -> Stat:
        async with self.engine.begin() as conn:
            res = await conn.execute(
                insert(sc.stats).values(key=key, name=name, message_id=message_id,
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
                                   .values(display_name=display_name))
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
            exists = (await conn.execute(select(sc.stat_last_post)
                      .where(sc.stat_last_post.c.stat_id == stat.id))).one_or_none()
            if exists is None:
                await conn.execute(insert(sc.stat_last_post).values(
                    stat_id=stat.id, discord_message_id=discord_message_id,
                    channel_id=channel_id))
            else:
                await conn.execute(update(sc.stat_last_post)
                                   .where(sc.stat_last_post.c.stat_id == stat.id)
                                   .values(discord_message_id=discord_message_id,
                                           channel_id=channel_id))
