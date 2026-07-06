# Running N3X Bot under CubeCoders AMP

This directory contains a **custom AMP application template** for the N3X bot
(`n3x_bot`). It lets an admin configure the bot entirely in the AMP web GUI —
Discord token, channels, roles, storage backend, `DATABASE_URL`, and gate
settings. AMP maps each GUI field to a process **environment variable**; the
bot's `pydantic-settings` `Settings` reads them directly, so **no `.env` file is
needed**.

## Template files

| File | Purpose |
|------|---------|
| `n3x-bot.kvp` | Root config: run command (`-m n3x_bot`), venv paths, and the `EnvironmentVariables` map (GUI field → env var). |
| `n3x-botconfig.json` | GUI field definitions (Discord / Storage / Gates groups + Python Version). |
| `n3x-botupdates.json` | AMP-native fetch/update stages (Git repo / GitHub release / Download URL / PyPI, gated on **App Download Type**), plus venv creation and `requirements.txt` install. |
| `n3x-botports.json` | Placeholder port (AMP requires an AdminPortRef; the bot uses no inbound port). |

The template uses **AMP's native repo Fetch/Download system**. The **App
Download Type** defaults to **Git repo** with **App Download Source** pre-filled
to `https://github.com/isikerkan/n3x-discord-bot.git`, so an admin just hits
**Update** to clone/pull the bot — no baked-in clone command. The bot runs as
`python -u -m n3x_bot` from the repo root (`./n3x-bot/`).

## Installing via a Configuration Repository (recommended, native)

AMP can fetch this template natively from a Git repo added as a **Configuration
Repository** — then **N3X Bot** appears in the app list with no manual file
copying. This mirrors how the stock templates ship from `CubeCoders/AMPTemplates`.

**Requirement:** AMP scans the **root** of a configuration repository. The
template files must therefore live at a git repo's top level (lower-cased), not
in a subdirectory. This `deploy/amp/` directory is that template set; publish its
contents to the **root** of a dedicated git repo (e.g. `n3x-amp-templates`).

Config-repo layout (all at repo root):

```
AMPMetadata.json        # marks the repo as an AMP configuration repository
n3x-bot.kvp             # main template (references the JSON files by name)
n3x-botconfig.json      # GUI field definitions
n3x-botupdates.json     # native fetch/update + venv + requirements stages
n3x-botports.json       # placeholder port
```

Add it to AMP (controller host):

```bash
# List current repositories
ampinstmgr ListRepositories
# Add your config repo (use the raw git URL)
ampinstmgr AddRepository https://github.com/<you>/n3x-amp-templates.git
# Refresh the local template cache
ampinstmgr RefreshRepositories
```

(Or via the AMP web UI: **Configuration → Configuration Repositories → Add**,
paste the git URL, then **Refresh**.)

Then in AMP: **Create Instance → select the N3X Bot application**, open
**Configuration**, and set the fields below. **App Download Type** is already
**Git repo** with the repo URL pre-filled — leave it unless you want an
alternative source (see *App download options*).

The bot's own code is fetched at runtime into the instance's `./n3x-bot/`
directory (`App.RootDir`/`App.BaseDirectory`, exactly like the stock Python App
Runner's `./python-app-runner/`), so it is browsable in AMP's file manager.

### Alternative: drop-in template (no repository)

Instead of a configuration repository, copy the five files above directly into
your AMP controller's shared template directory; AMP discovers the app via
`n3x-bot.kvp`. Then create the instance and configure as below.

## GUI fields → environment variables

Every field maps to an uppercased env var matching a `Settings` field in
`n3x_bot/config.py`.

| GUI field | Env var | Required | Default | Group |
|-----------|---------|:--------:|---------|-------|
| Discord Bot Token | `DISCORD_TOKEN` | yes | — | Discord |
| Target Role ID | `TARGET_ROLE_ID` | yes | — | Discord |
| Welcome Channel ID | `WELCOME_CHANNEL_ID` | yes | — | Discord |
| Reminder Channel ID | `REMINDER_CHANNEL_ID` | yes | — | Discord |
| Julez User ID | `JULEZ_ID` | no | `0` | Discord |
| Log Prefix | `PREFIX_STR` | no | `[N3X]` | Discord |
| Command Prefix | `COMMAND_PREFIX` | no | `!` | Discord |
| Reminder Time | `REMINDER_TIME` | no | `19:30` | Discord |
| Storage Backend | `STORAGE_BACKEND` | no | `flatfile` | Storage |
| Database URL | `DATABASE_URL` | no* | (empty) | Storage |
| Data File | `DATA_FILE` | no | `data/stats.json` | Storage |
| Gate Input Channel ID | `GATE_INPUT_CHANNEL_ID` | no | `0` | Gates |
| Gate Stats Channel ID | `GATE_STATS_CHANNEL_ID` | no | `0` | Gates |
| Gate Delete Role ID | `GATE_DELETE_ROLE_ID` | no | `0` | Gates |
| Gate Rewards | `GATE_REWARDS` | no | `a:46892,b:93820,c:139522` | Gates |
| Python Version | (build only) | no | System default | Runtime |

\* `DATABASE_URL` is **required by the bot** when Storage Backend is `sqlite` or
`postgres` (enforced by the `Settings` validator). Leave it empty only for
`flatfile`.

4. **Update** the instance. AMP fetches the app per **App Download Type**
   (default: git-clone/pull the repo), creates a venv, installs pip/setuptools/
   wheel, then installs `requirements.txt`.
5. **Start** the instance. Use the AMP console for logs; Start/Stop/Restart and
   scheduled restarts are managed by AMP.

## App download options

The **Download** settings group controls how AMP fetches the bot on **Update**:

| App Download Type | App Download Source | Notes |
|-------------------|---------------------|-------|
| **Git repo** (default) | `https://github.com/isikerkan/n3x-discord-bot.git` | Clones on first Update, `git pull` thereafter. Set **Git Repo Branch** to pin a branch (empty = default). |
| **GitHub release** | `User/Repo` | Downloads a release zip. Set **GitHub Release Filename** (the asset zip) and optionally **GitHub Release Version** (empty = latest). |
| **Download URL** | URL to a `.zip` | Fetches and extracts the archive into the app directory. |
| **PyPI package** | package name (and version) | `pip install`s the package into the venv. Add extra pip flags via **PyPI Package Installation Arguments**. |

**Private repo auth:** for a private Git repo, set **Git Repo Username** and
**Git Repo Password/Token** (a GitHub personal access token works as the
password). Leave both empty for public repos. The default N3X repo is public, so
no auth is needed.

**Python Packages Install Method** (Runtime group) defaults to
**Requirements.txt file**, which installs the repo's `requirements.txt`. Set it
to **None** to skip dependency install (pip/setuptools/wheel are always
installed).

## Storage backends

- **flatfile** (default): stats persist as JSON at `DATA_FILE` (`data/stats.json`)
  in the instance's working directory. Back it up with AMP's file manager.
- **sqlite**: set `DATABASE_URL` to a sqlite URL, e.g.
  `sqlite+aiosqlite:///data/stats.db`. The file lives under the working
  directory.
- **postgres**: set `DATABASE_URL` to an **external** Postgres, e.g.
  `postgresql+asyncpg://user:pass@host:5432/db`. **AMP does not provision a
  database** — host Postgres separately (a managed DB, a container, or another
  AMP instance) and point the URL at it.

> Keep `DATA_FILE`/sqlite paths under a `data/` subdirectory so they survive an
> **Update** (which pulls fresh repo contents). This mirrors the Docker setup.

## Updating

Re-run **Update** on the instance to pull the latest commit and reinstall
dependencies, then **Restart**.

---

## Alternative: stock Python App Runner template

If you prefer not to install a custom template, AMP's stock **Python App
Runner** generic template can run the bot too, but the bot's variables must be
supplied via a `.env` file in the working directory (the custom template above
removes that step). Configure the stock instance with:

- **App Download Type:** `Git repo`
- **App Download Source:** `https://github.com/isikerkan/n3x-discord-bot.git`
- **Git Repo Branch:** empty (default branch)
- **Python Version:** `3.12`
- **Python Packages Install Method:** `Requirements.txt file`
- **App Run Mode:** `Python module`, **App Module Name:** `n3x_bot`
- **App Subdirectory:** empty (repo root)

Then create a `.env` file in the working directory with the same variables as
`.env.example`, **Update**, and **Start**.
