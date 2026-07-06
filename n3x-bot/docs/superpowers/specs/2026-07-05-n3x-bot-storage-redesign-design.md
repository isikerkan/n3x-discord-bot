# N3X Discord Bot — Storage Redesign & Dynamic Stats

Date: 2026-07-05
Status: Approved (v1)

## Goal

Refactor the single-file `bot.py` into a maintainable, testable bot with:

1. Credentials + configuration externalized to `.env` (no hardcoded secrets).
2. Pluggable storage backend selectable via `.env` (`flatfile` | `sqlite` | `postgres`).
3. Data-driven, CRUD-based domain model (users, stats, messages) instead of hardcoded counters.
4. Dynamic Discord commands generated from `stats` rows — new counter = new row, no code change.
5. Docker + docker-compose deployment; compose reads `STORAGE_BACKEND` from `.env`.
6. Built via TDD, 80%+ coverage.

Out of scope for v1: Discord admin CRUD commands (`!stat add`, etc.) — deferred to a separate brainstorm (v2).

## Configuration

Single config file: **`.env`** (values), loaded via `pydantic-settings`. `docker-compose.yml` is the higher deployment tier (which services run, wiring). Backend is chosen in `.env`; compose reads it.

`Settings` fields:

- `discord_token` (secret, required)
- `storage_backend`: `flatfile | sqlite | postgres` (default `flatfile`)
- `database_url`: optional; **required** when backend is `sqlite` or `postgres` (startup validator fails fast otherwise)
- `data_file`: path for flatfile backend (default `stats.json`)
- `target_role_id`, `welcome_channel_id`, `reminder_channel_id` (ints)
- `prefix_str` (default `[N3X]`)
- `command_prefix` (default `!`)
- `reminder_time` (HH:MM, default `19:30`)

`.env.example` is committed; `.env` is gitignored.

## Domain Model (CRUD elements + tracking relations)

### Element tables (full CRUD: create / get / update / archive / delete / list)

- `users(id PK, discord_id UNIQUE, display_name, archived_at NULL, created_at)`
- `messages(id PK, name UNIQUE, template, archived_at NULL, created_at)`
  - `template` supports placeholders: `{user}`, `{count}`, `{stat}`
- `stats(id PK, key UNIQUE, name, message_id FK->messages NULL, archived_at NULL, created_at)`
  - `key` = command trigger (e.g. `tit`)
  - optional linked message template

Archive = soft-hide via `archived_at` timestamp; delete = hard remove.

### Tracking / relation tables

- `user_stats(user_id FK->users, stat_id FK->stats, count, PK(user_id, stat_id))` — per-user usage
- `stat_totals(stat_id FK->stats PK, count)` — global count per stat
- `stat_last_post(stat_id FK->stats PK, discord_message_id, channel_id)` — last bot post for delete/repost

## Architecture — Repository Pattern

Storage sits behind one async interface. A factory picks the implementation from `Settings.storage_backend`. Commands depend only on the interface.

### Modules

- `config.py` — `Settings` (pydantic-settings) + validation.
- `storage/base.py` — `StatsRepository` abstract interface:
  - element CRUD: `create_stat/get_stat/update_stat/archive_stat/delete_stat/list_stats` (and same shape for `user`, `message`)
  - tracking: `record_use(discord_id, stat_key) -> (user_count, total_count)`, `get_user_stats(discord_id)`, `get/set_last_post(stat_id, msg_id, channel_id)`
  - `load()` / lifecycle: `connect()` / `close()`
- `storage/json_repo.py` — flatfile JSON implementation (mirrors relational model as nested JSON). Includes one-time migration of the legacy `stats.json` counter format into the new element model.
- `storage/sql_repo.py` — **single** SQLAlchemy 2.0 async implementation serving both `sqlite` (via `aiosqlite`) and `postgres` (via `asyncpg`); dialect derived from `DATABASE_URL`. Tables auto-created on startup via metadata `create_all` (no Alembic in v1 — YAGNI).
- `storage/factory.py` — `create_repository(settings) -> StatsRepository`.
- `bot.py` — thin: builds `Settings`, builds repo, registers dynamic commands from `list_stats()`, wires events. No hardcoded credentials.

### Legacy seed

The existing hardcoded counters (`tit`, `wahab`, `cry`, `afk`, `oma`, `jules`, `smart`, `crash`) and their German message templates are seeded as `stats` + `messages` rows on first run (idempotent), preserving current behavior. Current `stats.json` counts migrated into `stat_totals` / `user_stats`.

## Command Flow (dynamic)

On startup: `list_stats()` → register one command per non-archived `stat.key`.

Per invocation `!<key>`:

1. upsert `user` by `discord_id`
2. `record_use` → `+1` on `user_stats` and `stat_totals`
3. if `stat.message_id` set → render linked `messages.template` with `{user}/{count}/{stat}`
4. else → default render: `"<stat.name> — <user> — <count>"`
5. delete previous bot post (`stat_last_post`) in reminder channel, send new, update `stat_last_post`

Retained features: cooldowns, prefix enforcement, welcome message, event reminder task, `!rank`, auto-delete of `!` messages.

## Testing (TDD)

- `pytest`, `pytest-asyncio`, `pytest-cov`.
- **Shared repository contract test suite**, parametrized over all three backends, asserting identical behavior:
  - flatfile → temp file
  - sqlite → temp DB file
  - postgres → disposable container; **skipped** (not failed) when Docker/DB unavailable
- Config validation tests (missing token; sqlite/postgres without `database_url`).
- Template rendering tests (linked message vs default).
- Dynamic command registration test.
- Target 80%+ coverage.

## Docker

- `Dockerfile` — `python:3.12-slim`, deps via `uv`, non-root user, runs `python -m n3x_bot` (or `bot.py`).
- `docker-compose.yml`:
  - `bot` service: `env_file: .env`, volume for flatfile/sqlite persistence.
  - `postgres` service: standard image, named volume, healthcheck; `bot` `depends_on` it.
  - `bot` reads `STORAGE_BACKEND` from `.env` to decide which backend to use.
- `.dockerignore`, `.env.example`, `.gitignore` (excludes `.env`, `stats.json`, `*.db`, `__pycache__`, `.venv`).

## Dependencies

discord.py, pydantic-settings, SQLAlchemy[asyncio], asyncpg, aiosqlite, pytest, pytest-asyncio, pytest-cov. Managed with `uv`.

## Security Notes

- The token currently hardcoded in `bot.py:18` is compromised and MUST be rotated in the Discord Developer Portal before any deployment.
- `.env` never committed; `.env.example` documents required vars with placeholder values.
- `bot.py` in its current (token-bearing) form is never committed to git history.
