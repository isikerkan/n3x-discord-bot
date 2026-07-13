from sqlalchemy import (
    MetaData, Table, Column, Integer, BigInteger, String, Text,
    DateTime, ForeignKey, Boolean, text,
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
    Column("targeted", Boolean, nullable=False, server_default=text("false")),
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

target_stats = Table(
    "target_stats", metadata,
    Column("target_discord_id", BigInteger, primary_key=True),
    Column("stat_id", Integer, ForeignKey("stats.id"), primary_key=True),
    Column("count", Integer, nullable=False, default=0),
)

gate_entries = Table(
    "gate_entries", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("gate_type", String(1), nullable=False),
    Column("cost", Integer, nullable=False),
    Column("user_id", BigInteger, nullable=False),
    Column("username", String(100), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

activity_counters = Table(
    "activity_counters", metadata,
    Column("discord_id", BigInteger, primary_key=True),
    Column("metric", String(20), primary_key=True),
    Column("count", BigInteger, nullable=False, default=0),
)

streak_stats = Table(
    "streak_stats", metadata,
    Column("discord_id", BigInteger, primary_key=True),
    Column("current_streak", Integer, nullable=False),
    Column("last_active_date", String(10), nullable=False),
    Column("max_streak", Integer, nullable=False),
)

night_stats = Table(
    "night_stats", metadata,
    Column("discord_id", BigInteger, primary_key=True),
    Column("night_count", Integer, nullable=False),
    Column("last_night_date", String(10), nullable=False),
)
