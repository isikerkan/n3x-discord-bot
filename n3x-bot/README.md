# N3X Discord Bot

Discord bot with pluggable CRUD storage (flatfile / SQLite / Postgres) and
data-driven commands.

## Setup

1. `cp .env.example .env` and fill in `DISCORD_TOKEN` (rotate the old one first),
   channel/role IDs, and choose `STORAGE_BACKEND`.
2. For sqlite/postgres, set `DATABASE_URL` (see `.env.example`).

## Run (local)

    uv sync
    uv run python -m n3x_bot

## Run (Docker)

    docker compose up --build          # flatfile or sqlite (set in .env)
    # Postgres: set STORAGE_BACKEND=postgres and
    # DATABASE_URL=postgresql+asyncpg://n3x:n3x@postgres:5432/n3x in .env

Under Docker, flatfile data only persists if `DATA_FILE` points inside
`data/` (the mounted `bot-data` volume); `docker-compose.yml` sets
`DATA_FILE=data/stats.json` by default for this reason.

## Run (CubeCoders AMP)

This repository is an **AMP configuration repository**: the AMP template files
live at the repo root and this `n3x-bot/` folder is the app's `App.RootDir`. See
the [root `README.md`](../README.md) for how to add it to AMP and configure the
bot in the GUI.

## Test

    uv run pytest --cov=n3x_bot

Postgres contract tests run only when TEST_POSTGRES_URL is set.

## Storage backends

Selected via `STORAGE_BACKEND` in `.env`: `flatfile` | `sqlite` | `postgres`.
All three satisfy the same repository contract. Adding a counter = insert a
`stats` row (a linked `messages` template is optional); the command is
registered automatically on next start.
