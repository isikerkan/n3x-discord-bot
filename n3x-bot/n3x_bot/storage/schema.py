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
    Column("laser_dropped", Boolean, nullable=True),
    Column("drops", Text, nullable=True),
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

achievements = Table(
    "achievements", metadata,
    Column("discord_id", BigInteger, primary_key=True),
    Column("achievement_id", String(50), primary_key=True),
)

kodex_confirmations = Table(
    "kodex_confirmations", metadata,
    Column("discord_id", BigInteger, primary_key=True),
)

kodex_messages = Table(
    "kodex_messages", metadata,
    Column("message_id", BigInteger, primary_key=True),
    Column("discord_id", BigInteger, nullable=False),
)

base_timers = Table(
    "base_timers", metadata,
    Column("map_name", String(20), primary_key=True),
    Column("end_time", DateTime(timezone=True), nullable=False),
)

channel_messages = Table(
    "channel_messages", metadata,
    Column("key", String(50), primary_key=True),
    Column("message_id", BigInteger, nullable=False),
    Column("channel_id", BigInteger, nullable=False),
)

runtime_config = Table(
    "runtime_config", metadata,
    Column("key", String(50), primary_key=True),
    Column("value", Text, nullable=True),
)

content_texts = Table(
    "content_texts", metadata,
    Column("key", String(50), primary_key=True),
    Column("value", Text, nullable=True),
)

color_config = Table(
    "color_config", metadata,
    Column("key", String(50), primary_key=True),
    Column("value", Text, nullable=True),
)

achievement_defs = Table(
    "achievement_defs", metadata,
    Column("id", String(50), primary_key=True),
    Column("category", String(50), nullable=False),
    Column("metric", String(50), nullable=False),
    Column("threshold", Integer, nullable=False),
    Column("title", Text, nullable=False),
    Column("secret", Boolean, nullable=False),
    Column("color", String(50), nullable=True),
)

# Active voice sessions: `since` is the "uncredited-from" checkpoint. The flush
# loop credits (now - since) and advances `since`, so a crash/restart can
# recover the in-progress interval instead of losing it.
voice_sessions = Table(
    "voice_sessions", metadata,
    Column("discord_id", BigInteger, primary_key=True),
    Column("since", DateTime(timezone=True), nullable=False),
)

# Users opted in to event-reminder pings (via /event reminder or the reaction
# signup message).
event_optin = Table(
    "event_optin", metadata,
    Column("discord_id", BigInteger, primary_key=True),
)

gate_pending = Table(
    "gate_pending", metadata,
    Column("message_id", BigInteger, primary_key=True),
    Column("channel_id", BigInteger, nullable=False),
    Column("gate_type", String(10), nullable=False),
    Column("cost", BigInteger, nullable=False),
    Column("user_id", BigInteger, nullable=False),
    Column("username", String(100), nullable=False),
    Column("options", Text, nullable=True),
)

# ── LFG (Looking For Group) ────────────────────────────────────────────────
# User-created group-finding posts, independent of any activity/quest catalog.
# `start_times` is a JSON list of "HH:MM" strings (same Text-as-JSON convention
# as `gate_pending.options`). `cleanup_at` is the DURABLE expiry deadline the
# cleanup loop reconciles against — keeping it in the row (rather than in an
# in-process timer) is what makes cleanup survive a restart.
lfg_posts = Table(
    "lfg_posts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("creator_id", BigInteger, nullable=False),
    Column("title", String(100), nullable=False),
    Column("event_date", String(10), nullable=False),      # YYYY-MM-DD
    Column("min_players", Integer, nullable=False),
    Column("max_players", Integer, nullable=False),
    Column("start_times", Text, nullable=False),           # JSON ["HH:MM", ...]
    Column("confirmed_time", String(5), nullable=True),    # "HH:MM" once fixed
    Column("status", String(20), nullable=False),
    Column("channel_id", BigInteger, nullable=False),
    Column("message_id", BigInteger, nullable=True),       # set after posting
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("event_at", DateTime(timezone=True), nullable=True),
    Column("cleanup_at", DateTime(timezone=True), nullable=False),
)

# Who is available for which start time. Replace-set per (lfg, user): a user
# changing their selection clears their rows and re-inserts.
lfg_availability = Table(
    "lfg_availability", metadata,
    Column("lfg_id", Integer, ForeignKey("lfg_posts.id"), primary_key=True),
    Column("discord_id", BigInteger, primary_key=True),
    Column("start_time", String(5), primary_key=True),
)

# The actual roster. Deliberately NOT derived from `lfg_availability`: after a
# time is confirmed, people join and leave who never declared availability, so
# the two sets diverge.
lfg_participants = Table(
    "lfg_participants", metadata,
    Column("lfg_id", Integer, ForeignKey("lfg_posts.id"), primary_key=True),
    Column("discord_id", BigInteger, primary_key=True),
    Column("joined_at", DateTime(timezone=True), nullable=False),
)

# ── Group Finder (global, timezone-channel based) ──────────────────────────
# Successor of the single-channel LFG above; the lfg_* tables stay as history.
# Key/value settings: the Group Finder category and the hub channel.
gf_settings = Table(
    "gf_settings", metadata,
    Column("key", String(50), primary_key=True),
    Column("value", Text, nullable=True),
)

# Admin-curated zones. A zone is ACTIVE while its channel exists; a manually
# deleted channel marks it DEACTIVATED (channel_id cleared, role_id kept so a
# later re-activation reuses the role instead of creating a duplicate).
gf_zones = Table(
    "gf_zones", metadata,
    Column("zone", String(64), primary_key=True),          # IANA id
    Column("role_id", BigInteger, nullable=True),
    Column("channel_id", BigInteger, nullable=True),
    Column("status", String(20), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("deactivated_at", DateTime(timezone=True), nullable=True),
)

# One zone per member.
gf_members = Table(
    "gf_members", metadata,
    Column("discord_id", BigInteger, primary_key=True),
    Column("zone", String(64), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

# One row per global event. All times are UTC; zone channels are only views.
gf_events = Table(
    "gf_events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("creator_id", BigInteger, nullable=False),
    Column("title", String(100), nullable=False),
    Column("min_players", Integer, nullable=False),
    Column("max_players", Integer, nullable=False),
    Column("origin_zone", String(64), nullable=False),     # where it was created
    Column("status", String(20), nullable=False),
    Column("scheduled_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("closed_at", DateTime(timezone=True), nullable=True),
    Column("time_found_notified_at", DateTime(timezone=True), nullable=True),
    Column("cleanup_at", DateTime(timezone=True), nullable=False),
    Column("cancelled_at", DateTime(timezone=True), nullable=True),
    # set once the event's messages are removed from every zone channel
    Column("cleaned_at", DateTime(timezone=True), nullable=True),
    Column("legacy_lfg_id", Integer, nullable=True),        # migrated lfg_posts.id
)

# The proposed start times.
gf_slots = Table(
    "gf_slots", metadata,
    Column("event_id", Integer, ForeignKey("gf_events.id"), primary_key=True),
    Column("starts_at", DateTime(timezone=True), primary_key=True),
)

# Availability. Kept separate from the final time and never overwritten by it.
# `zone` is the zone of the channel the vote came from — it decides where the
# member is pinged.
gf_votes = Table(
    "gf_votes", metadata,
    Column("event_id", Integer, ForeignKey("gf_events.id"), primary_key=True),
    Column("discord_id", BigInteger, primary_key=True),
    Column("starts_at", DateTime(timezone=True), primary_key=True),
    Column("zone", String(64), nullable=False),
    Column("voted_at", DateTime(timezone=True), nullable=False),
)

# The roster once a time is found (before that, participants are the voters).
gf_participants = Table(
    "gf_participants", metadata,
    Column("event_id", Integer, ForeignKey("gf_events.id"), primary_key=True),
    Column("discord_id", BigInteger, primary_key=True),
    Column("zone", String(64), nullable=False),
    Column("joined_at", DateTime(timezone=True), nullable=False),
    Column("source", String(10), nullable=False),           # VOTE / JOIN
)

# One message per event per zone channel.
gf_messages = Table(
    "gf_messages", metadata,
    Column("event_id", Integer, ForeignKey("gf_events.id"), primary_key=True),
    Column("zone", String(64), primary_key=True),
    Column("channel_id", BigInteger, nullable=False),
    Column("message_id", BigInteger, nullable=False),
    Column("posted_at", DateTime(timezone=True), nullable=False),
)
