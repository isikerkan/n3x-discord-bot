# AMP GUI Content Management — Brainstorm

Goal (catalog §D): manage bot content — stats/commands, achievements, messages,
and later gate rewards / milestones — from the **AMP web GUI**, not only via
Discord `!admin` commands or the DB. This doc grounds the §D candidates against
what actually exists and recommends a phased v1.

## 1. What the content actually is (grounded)

| Content | Home today | Runtime-editable now? |
|---|---|---|
| Stat/command defs (`!tit`, `!smart @m`) | `stats` table (seeded from `LEGACY_STATS`) | **Yes** — `!admin stat …` (live DB write + live command register) |
| Stat output templates (German copy) | `messages` table | **Yes** — `!admin msg …` |
| Achievement definitions (59) | `achievements.py` `_build_achievements()` (frozen dataclasses) | **No** — code edit + redeploy |
| Milestone tiers/thresholds | `MILESTONE_LEVELS` etc. (code) | **No** |
| Narrative copy: Kodex, welcome, card/record/reminder text | `kodex.py`, `welcome.py`, `cards.py`, `bot.py` constants | **No** |
| `gate_rewards`, `voice_achievement_roles`, `allowed_maps`, `reminder_time`, channel/role IDs | `config.py` Settings (delimited strings / scalars, from env) | **Restart only** |

**Takeaway:** stats + message templates are *already* GUI-adjacent (Discord CRUD).
The genuine gaps are (a) the config-string scalars — editable only by hand-editing
env — and (b) achievement defs + narrative copy, which are code-only.

## 2. What AMP actually offers (grounded)

- **GUI config fields** (`n3x-botconfig.json`): **scalar only** — text/password/enum/hidden.
  No list/table/array type. Multi-value content must be a delimited string in one
  `text` field (the bot already does this: `gate_rewards`, `allowed_maps`,
  `voice_achievement_roles`). Fields reach the bot as **env vars → pydantic Settings**;
  `App.SupportsLiveSettingsChanges=False` ⇒ **restart to apply**.
  - **Drift to fix:** ~11 Settings are NOT exposed as GUI fields or injected by the
    kvp (`admin_role_id`, `timezone`, `milestone_channel_id`, `overview_channel_id`,
    `kodex_check_channel_id`, `voice_achievement_roles`, `base_timer_role_id`,
    `timer_overview_channel_id`, `timer_overview_message_id`, `allowed_maps`). Under
    AMP they silently fall back to defaults — several features are unconfigurable from
    the GUI right now.
- **Console (stdin)**: `App.HasWriteableConsole=True` + `App.AdminMethod=STDIO` ⇒ the
  AMP Console input box **is** delivered to the process stdin. **But the bot never
  reads stdin** (`amain()` only runs the gateway loop). A console-command interface is
  possible but requires adding a stdin-reader task.
- **File Manager**: process cwd = `n3x-bot/`. A content file is GUI-editable there, but
  a file committed into the repo tree is **overwritten by the git-pull update**. Only
  paths outside the tracked tree survive — e.g. `data/` (where `data/stats.json`
  already lives). So a managed content file must live at `data/content.yaml`.
- **Launch**: `venv/bin/python3 -u -m n3x_bot` (unbuffered → live Console logs).

## 3. The three §D approaches, matched to content type

No single approach fits all content. Match each:

- **(C) AMP GUI scalar fields** — right for the **config-string scalars** (channel/role
  IDs, `gate_rewards`, `voice_achievement_roles`, `allowed_maps`, `timezone`,
  `reminder_time`). Native, already the pattern, cheap. Can't express open-ended lists
  (achievements). Restart to apply.
- **(A) `data/content.yaml` + File Manager** — right for **achievement definitions +
  narrative copy** (the code-only content). Declarative, versionable, no custom UI.
  Cost: a loader with code-default fallback + reconcile; achievement defs carry **code
  coupling** (card tier colors substring-match the German titles; voice-role map keys
  ARE achievement ids; tests pin `TOTAL_ACHIEVEMENTS`), so moving them to a file means
  decoupling colour into the def and a seed-or-load path.
- **(B) Console stdin CRUD** — right for **stats/messages** as an AMP-native alternative
  to Discord `!admin`, reusing the existing `admin_*` helpers. Live, no file format.
  Cost: add a stdin-reader task + a tiny command parser. Nice-to-have (Discord CRUD
  already covers this), not essential.

## 4. Recommended phased v1

Ordered by value-to-effort. Each phase is independently shippable via the TDD pipeline.

1. **Phase 1 — close the GUI config drift (approach C).** Add the ~11 missing Settings
   to `n3x-botconfig.json` + the kvp `App.EnvironmentVariables` injection. Turns
   `gate_rewards`, `voice_achievement_roles`, `allowed_maps`, `timezone`, and every
   channel/role ID into GUI-editable fields. **Low effort, low risk** (Settings already
   parsed + tested; this is manifest/kvp plumbing), immediate operator value. **Do first.**

2. **Phase 2 — narrative copy in `data/content.yaml` (approach A-lite).** Load Kodex /
   welcome / reminder / record strings from an editable YAML in `data/`, with the current
   Python constants as the fallback default (write the default file on first boot if
   absent; never overwrite an edited one). Medium effort, contained — no code coupling,
   no DB reconcile. Lets non-devs fix German copy in the File Manager.

3. **Phase 3 (defer) — achievement definitions as content (approach A, full).** Biggest:
   decouple tier colour into the def, make `achievements.py` load defs from the file
   (seed-or-load), reconcile with the unlocks table + `sync_achievements`, and relax the
   count-pinned tests. High effort + touches cards/activity/tests. Revisit after Phases 1–2.

4. **Optional — Console stdin bridge (approach B).** Add a stdin-reader that routes
   `stat add …` / `msg edit …` to the existing `admin_*` helpers, so stats/messages are
   also editable from the AMP Console. Independent of the above; schedule only if a
   no-Discord admin path is wanted.

Deliberately NOT recommended: cramming achievement lists into scalar GUI fields (C can't
express them), or a custom AMP module/plugin UI (heavy, out of scope).

## 5. Open decisions for the user
- Start with **Phase 1** (config drift — quick native win) as the v1, or go straight for
  **Phase 2** (editable narrative copy), or scope **Phase 3** (achievements-as-content)
  now despite the coupling cost?
- Source-of-truth for any content file: file-authoritative (re-seed DB from file on load)
  vs DB-authoritative (file is a one-time import)? Recommend file-authoritative for
  narrative copy (no counters in it), DB-authoritative stays for stats (has counters).
