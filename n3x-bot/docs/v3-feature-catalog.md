# v3 Feature Catalog & Port Roadmap

Reverse-engineered from `manus versions/v3/bot.py` (2035 lines, discord.py 2.x, SQLite). Source is a prototype; we port its **features** into the `n3x_bot` package (config-driven, pluggable storage, modular per-feature `.py`). Do NOT copy v3 code verbatim — it's single-file, hardcoded, slash-only, and carries the bugs listed below.

**Key structural facts about v3:**
- Every command is a **Discord slash command** (`bot.tree.command`, prefix `/`). No `!` text commands.
- Branding is inconsistent (`[N3X]` vs "R3X" in logs/help).
- German UX copy throughout.

---

## A. Feature Catalog

Legend — **NEW** (not in n3x_bot), **EXISTING** (already ported), **CHANGED** (exists but v3 differs). Port effort S/M/L.

| # | System | What | Commands / triggers | Data | NEW/EXISTING | Effort |
|---|--------|------|---------------------|------|--------------|--------|
| 1 | Counters & targeted stats | Joke counters (self + targeted) + `/rank` | `/cry /oma /tit`, `/smart /crash /rauchen /schlaganfall @m`, `/beleidigung /afk @m`, `/rank`; 20s cooldown | global_stats, user_stats, target_stats | EXISTING (CHANGED: slash; some target cmds skip user_stats) | S |
| 2 | Gate tracker + **Delta** + records | A/B/C instant, **D reaction-confirmed (laser bool)**; live embed (avg/reward/profit); min/max records | `on_message` parse, `on_raw_reaction_add`, `/stat a\|b\|c\|d\|me`, `/del` (admin) | gate_stats, delta_stats, gate_records, last_messages | Core EXISTING/CHANGED; **Delta + records + paginated /stat = NEW** | M |
| 3 | **Achievements** (59, image cards) | Tiered gate + activity milestones; Pillow card to milestone channel; `/erfolge` DM; paginated overview; voice-tier roles | `/erfolge`, `/sync_achievements` (admin, destructive), voice loop, all trackers | achievements | **NEW** | L |
| 4 | **Activity tracking** | voice time, message count, daily streak (cur+max), night (00–05), reactions; **events stubbed** | `on_voice_state_update`, `voice_live_check_task` (1min), `on_message`, `on_raw_reaction_add` | voice_stats, message_stats, streak_stats, night_stats, reaction_stats, (event_stats unused) | **NEW** | M |
| 5 | **Kodex / rules acceptance** | DM code-of-conduct to new members, ✅-react to confirm; admin audit | `/kodex`, `/kodex_check` (admin), `on_member_join`, DM `on_raw_reaction_add` | kodex_confirmations, kodex_messages | **NEW** | S/M |
| 6 | **Welcome cards** (Pillow) | Graphical welcome image on join (bg + name); text fallback | `on_member_join`, `/sync_welcome` (admin) | none; `welcome_bg.jpg` | **NEW** | S/M |
| 7 | Prefix / nickname enforcement | `[N3X]` nick prefix for role holders; strip otherwise | `on_member_update`, `on_member_join`, `full_scan` on ready | — | NEW/CHANGED vs our join/leave model | S |
| 8 | **Base timers** (game maps) | Role-gated countdown timers per map; 30s overview refresh | `/base <map> <zeit>`, `/basestop <map>`, `timer_overview_task` (30s) | in-memory only (lost on restart) | **NEW** | S/M |
| 9 | Channel-message maintenance | Self-editing persistent embeds (gate stats, input help, command list, overview) | on_ready + updates | last_messages | Partly EXISTING | S |
| 10 | Admin / help | `/help`, `/save`, admin syncs; per-guild slash sync on ready | slash | — | CHANGED (we have `!admin` CRUD) | S |

**Config constants v3 hardcodes (become `.env`/Settings when porting):** TARGET_ROLE_ID, PREFIX_STR, GATE_INPUT_CHANNEL_ID, GATE_STATS_CHANNEL_ID, MILESTONE_CHANNEL_ID, OVERVIEW_CHANNEL_ID, COMMAND_LIST_CHANNEL_ID, KODEX_CHECK_CHANNEL_ID, TIMER_OVERVIEW_CHANNEL_ID + FIXED_MESSAGE_ID, REQUIRED_ROLE_ID (base), admin role id, VOICE_ACHIEVEMENT_ROLES, GATE_REWARDS{a,b,c,d}, ALLOWED_MAPS, and the milestone threshold tables (MILESTONE_LEVELS, VOICE/MESSAGE/STREAK/NIGHT/REACTION_MILESTONES). Assets: `welcome_bg.jpg`, `back.webp`, DejaVuSans-Bold, timezone (Europe/Berlin).

---

## B. Port roadmap (priority-ordered TODO)

Each becomes its own modular `n3x_bot/<feature>.py` + repo methods + tests (TDD pipeline). Config → Settings; no hardcoded IDs.

1. **[ ] Activity tracking** (voice/message/streak/night/reaction) — foundation for achievements; highest value. **Fix timezone (B6) + async DB (B3) at design time.** (M)
2. **[ ] Achievements + image cards** — headline feature; depends on #1. Thresholds as config; card rendering isolated + testable (render to bytes, not Discord). (L)
3. **[ ] Delta gate + records/leaderboards** — extends our existing gate tracker; self-contained. (M)
4. **[ ] Kodex / rules acceptance** — small, high governance value, independent. (S/M)
5. **[ ] Welcome cards** — cosmetic, independent. (S/M)
6. **[ ] Base timers** — needs DB persistence to be worth it (B12). (S/M)
7. **[ ] Prefix/nickname enforcement** — reconcile with our join/leave model. (S)

Defer/skip: event tracking (never implemented in v3), legacy `stats.json`/`send_or_update_msg`.

---

## C. Bug / pitfall catalog (fixes to apply when porting — do NOT inherit these)

v3 bugs found during reverse-engineering. Most are "avoid when porting"; a few also apply to our current bot (noted).

| ID | Sev | What | Fix |
|----|-----|------|-----|
| B1 | Critical | Hardcoded Discord token in source | Load from env/Settings (we already do); never inherit. Rotate the exposed token. |
| B2 | Critical (perf) | `fetch_user` REST call on EVERY message/reaction before checking if a milestone is due | Compute due-milestones first; only fetch when non-empty; prefer cache `get_user`. |
| B3 | Important | Blocking sync `sqlite3` in async hot paths (per-message); no WAL/timeout → "db locked" | Our repo is already async (SQLAlchemy/aiosqlite) — keep tracker writes on the async repo; batch where possible. |
| B4 | Important | `timer_overview_task.start()` not guarded → crashes on gateway reconnect | Guard `if not task.is_running()` or start in `setup_hook`. |
| B5 | Important | `full_scan()` nick-edits ALL members on every `on_ready`/reconnect (rate-limit) | Run once (flag/`setup_hook`); only edit members needing a change. |
| B6 | Important | Naive `datetime.now()` for night window + streaks (no pytz despite dep) | Use tz-aware time (`ZoneInfo("Europe/Berlin")`, configurable) for all date/hour logic. |
| B7 | Important | Gate min/max records go permanently stale after `/del` (monotonic, never recomputed) | On delete, fully recompute min/max for that gate from remaining rows. |
| B8 | Minor | `/beleidigung`/`/afk` skip `user_stats` → never in `/rank` (inconsistent) | Decide semantics; write caller usage if it should count. |
| B9 | Minor | Overview page-turn reactions (⬅️/➡️) inflate reaction counts/achievements | Skip reaction tracking in bot-managed UI channels (`return` early). |
| B10 | Minor | `guild.fetch_members(...).flatten()` removed in discord.py 2.x (swallowed by bare except) | `[m async for m in g.fetch_members(limit=None)]` (we already fixed this in n3x_bot). |
| B11 | Minor | New `aiohttp.ClientSession` per achievement card render | Reuse one session (create in `setup_hook`, close on shutdown). |
| B12 | Minor | In-memory voice sessions + base timers lost on restart | Persist session start-times / timer end-times to DB; reconcile on ready. |
| B13 | Minor | Dozens of bare `except:` hide real errors | Narrow to expected exceptions + log. |
| B14 | Minor | Delta dedup (user+cost, 30s) silently drops legit repeat runs | Surface rejection to user; document window. |
| B15 | Minor | Cooldown handler drops non-cooldown errors; assumes fresh interaction | Handle other errors + `followup`/`is_done()`. |
| B16 | Minor | Dead code/stubs (event tracking, `send_or_update_msg`, legacy stats.json) | Implement or drop when porting. |
| B17 | Minor | `/sync_achievements` wipes table before re-sync (data loss if sync fails) | Rebuild into temp, swap only on success. |
| B18 | Minor | Nickname strip leaves stray space; `[:32]` truncation mangles | Single normalize helper (`"[N3X] "` + `.strip()`). |

Note: v3 SQL is parameterized — **no SQL-injection found**. Only critical security issue is the hardcoded token (B1).

---

## D. TODO — AMP GUI content management

**Goal:** manage bot content (stats/commands, achievements, messages — later maybe gate rewards, milestones) from the **AMP web GUI**, not only via Discord `!admin` commands or the DB. Needs its own brainstorm to pick an approach; candidates (AMP-native):

- **Config-file + AMP File Manager (declarative, recommended to evaluate first).** Bot loads content from an editable file in the working dir (e.g. `content.yaml`/`.json`: list of stats, achievement definitions/thresholds, message templates). Admin edits it in AMP's built-in **File Manager**; bot reloads on change (watch file, `/admin reload`, or on restart). Simple, no custom UI, versionable. Downside: file is source-of-truth, must reconcile with the DB-backed runtime (seed/sync on load).
- **Console command interface (interactive).** Bot reads **stdin**; admin types management commands in AMP's **Console** tab (our kvp already sets `App.HasWriteableConsole=True`), e.g. `stat add tit Tit`, `achievement add …`. Reuses the existing `admin_*` helpers. AMP-native, live, no file format. Downside: console is ephemeral/one-way-ish, no listing UI.
- **AMP config manifest fields (limited).** `n3x-botconfig.json` GUI fields are for static instance config (env → Settings), NOT dynamic CRUD tables — can't list/add achievements. Usable only for a small fixed set (already used for channel/role IDs). Not sufficient for open-ended content.
- **Custom AMP module/plugin UI** — heavy; out of scope unless the above don't suffice.

**Suggested scope for v1:** a `content.yaml` (stats + messages + achievement definitions) loaded on startup + an `/admin reload` (Discord) and/or console `reload`, editable via AMP File Manager. Decide file-vs-DB source-of-truth in the brainstorm. Depends on the achievements port (#2) for the achievement side.
