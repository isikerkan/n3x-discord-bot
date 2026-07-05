# Running N3X Bot under CubeCoders AMP

AMP can start/stop/console/auto-restart the bot using its stock **Python App
Runner** generic template (no custom template needed). This is the "AMP Generic"
tier; Docker (see project README) is the alternative.

## One-time setup

1. In AMP, create a new instance and choose the **Python App Runner**
   application (install it from the "Python App Runner" template if not present).
2. Configure the instance (Configuration → the app's settings), setting:
   - **App Download Type:** `Git repo`
   - **App Download Source:** `https://github.com/isikerkan/n3x-discord-bot.git`
   - **Git Repo Branch:** empty (default branch) — or a release branch/tag
   - **Python Version:** `3.12`
   - **Python Packages Install Method:** `Requirements.txt file`
   - **App Run Mode:** `Python module`
   - **App Module Name:** `n3x_bot`
   - **App Subdirectory:** empty (repo root — `n3x_bot/` is importable there)
3. **Update** the instance. AMP git-clones the repo, creates a venv, and
   installs `requirements.txt`.
4. Create the `.env` file in the instance's working directory (the cloned repo
   root) with `DISCORD_TOKEN`, `STORAGE_BACKEND`, `DATABASE_URL` (if sqlite/
   postgres), and the channel/role IDs — same variables as `.env.example`.
   AMP's Python App Runner loads `.env` from the working directory automatically.
5. **Start** the instance. Use the AMP console for logs; Start/Stop/Restart and
   scheduled restarts are managed by AMP.

## Storage under AMP

- `flatfile` / `sqlite`: data persists in the instance's working directory
  (`stats.json` or the sqlite `.db`). Back these up with AMP's file manager.
- `postgres`: point `DATABASE_URL` at an external Postgres (a separate AMP
  instance, a container, or a managed DB). AMP's Python App Runner does not
  provision a database.

## Updating

Re-run **Update** on the instance to pull the latest commit and reinstall
dependencies, then **Restart**.
