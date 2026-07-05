from sqlalchemy import (
    MetaData, Table, Column, Integer, BigInteger, String, Text,
    DateTime, ForeignKey,
)

metadata = MetaData()

users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("discord_id", BigInteger, unique=True, nullable=False),
    Column("display_name", String(100), nullable=False),
    Column("archived_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

messages = Table(
    "messages", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(100), unique=True, nullable=False),
    Column("template", Text, nullable=False),
    Column("archived_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

stats = Table(
    "stats", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("key", String(50), unique=True, nullable=False),
    Column("name", String(100), nullable=False),
    Column("message_id", Integer, ForeignKey("messages.id"), nullable=True),
    Column("archived_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

user_stats = Table(
    "user_stats", metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("stat_id", Integer, ForeignKey("stats.id"), primary_key=True),
    Column("count", Integer, nullable=False, default=0),
)

stat_totals = Table(
    "stat_totals", metadata,
    Column("stat_id", Integer, ForeignKey("stats.id"), primary_key=True),
    Column("count", Integer, nullable=False, default=0),
)

stat_last_post = Table(
    "stat_last_post", metadata,
    Column("stat_id", Integer, ForeignKey("stats.id"), primary_key=True),
    Column("discord_message_id", BigInteger, nullable=False),
    Column("channel_id", BigInteger, nullable=False),
)
