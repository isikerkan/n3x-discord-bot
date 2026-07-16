# Architecture: Phase 2 — GATE commands migrated to slash-only (`/stat`, `/del`, `/gate verlauf`)

Scope: convert the three prefix gate commands wired by `register_gate_commands`
(`stat`, `del`, and the `gate` group + `verlauf` subcommand) into slash-only app
commands on `bot.tree`. `parse_de_date` (gates.py) and
`render_gate_history_chart` (charts.py) already exist and are unchanged. The
reaction-based removal path (`handle_verlauf_removal`, `bot._verlauf_msgs`) is
unchanged. All edits are confined to `n3x_bot/bot.py`.

## Tests this design satisfies

test_bot_wiring.py
- `test_build_bot_wires_gate_verlauf_group` — `bot.get_command("gate") is None`; `bot.tree.get_command("gate")` is an `app_commands.Group` exposing `verlauf`.
- `test_register_stat_commands_adds_one_command_per_stat_plus_rank` / `_is_idempotent` — the exclusion lists already contain `stat`/`del`/`gate`; these pass once those drop from the prefix registry (no per-stat count change).
- `test_on_command_error_missing_arg_is_generic_for_gate_commands` (name `stat`), `_for_gate_verlauf` (name `verlauf`), `_reports_bad_argument` (name `del`) — satisfied by leaving `_GENERIC_ARG_COMMANDS` UNCHANGED (it is keyed by name string, independent of whether the prefix command exists).
- `test_tree_on_error_surfaces_helper_error_to_interaction` — existing `bot.tree.error` handler unchanged; still applies to the new app commands.
- All other wiring/gate-tracker/on_ready tests — unaffected (no behavioral change to those paths).

test_slash_migration.py (Phase 2 section)
- `test_stat_is_app_command_not_prefix_command` — `bot.get_command("stat") is None`, `bot.tree.get_command("stat") is not None`.
- `test_stat_app_command_sends_costs_embed` — `/stat gate="a"` → `interaction.response.send_message(embed=…)` whose description contains German-formatted costs.
- `test_stat_app_command_reports_no_data_when_empty` — `/stat gate="b"` on empty repo → plain-text `send_message` containing "Noch keine Daten", no embed.
- `test_del_is_app_command_not_prefix_command` — `bot.get_command("del") is None`, `bot.tree.get_command("del") is not None`.
- `test_del_app_command_denies_without_configured_role` — no matching role → ephemeral "Keine Berechtigung", nothing deleted.
- `test_del_app_command_with_role_deletes_and_refreshes_embed` — matching role + valid index → entry removed, gate stats embed refreshed (`channel.send`), one `send_message`.
- `test_del_app_command_reports_index_not_found` — bad index → `send_message` text containing "nicht gefunden".

test_gate_verlauf.py
- `test_gate_group_and_verlauf_subcommand_are_registered_on_tree` — group + subcommand exist on tree, `gate` not a prefix command.
- `test_verlauf_defers_before_rendering_then_posts_via_followup` — `interaction.response.defer` awaited once and strictly before `followup.send`.
- `test_verlauf_valid_gate_posts_png_file_via_followup` — `followup.send(file=discord.File(..., filename endswith ".png"))`.
- `test_verlauf_records_followup_message_for_reaction_removal` — `bot._verlauf_msgs[followup_msg.id] == interaction.user.id`.
- `test_verlauf_invalid_date_refuses_ephemeral_no_file` — bad `von` → ephemeral German "Datum" error, no chart file.
- `test_verlauf_date_range_filters_entries_by_parsed_window` — `repo.list_gate_entries` called with tz-aware `since` at 00:00:00 and `until` at end of `bis` day.
- `test_verlauf_until_covers_whole_bis_day_to_last_microsecond` — `until` local time is 23:59:59.999999.
- `test_verlauf_no_data_in_range_still_posts_empty_chart` — empty range still renders + posts a File.
- `parse_de_date` tests and `render_gate_history_chart` tests — ALREADY satisfied by existing gates.py/charts.py; no change (flagged under Risks re: matplotlib).

test_command_list.py
- Unaffected. `build_command_list` iterates `bot.commands`; with `stat`/`del`/`gate` gone from the prefix registry they no longer appear (which the Phase-2 comments in the command-list tests already assume). Optional cleanup of dead `_COMMAND_DESCRIPTIONS` keys, below.

## Files to create
- None.

## Files to modify
- `n3x_bot/bot.py` — one module. Changes:
  1. **Add module-level `GATE_CHOICES`** (near the other gate constants, e.g. after `GATE_STAT_CHUNK_LIMIT`/`HOME_KEY` or just above `register_gate_commands`):
     `GATE_CHOICES = [app_commands.Choice(name=GATE_NAMES[g], value=g) for g in GATE_TYPES]`
     — 7 entries, ordered a,b,c,d,e,z,k, readable label = `GATE_NAMES[g]`, value = the letter. Requires `from discord import app_commands` (add to the discord imports at top; `discord.app_commands` is already used at lines 980-981, so importing the alias is consistent).
  2. **Extract a pure embed builder** `build_gate_stat_embeds(gate_type: str, costs: list[int]) -> list[discord.Embed]` from the body of `_handle_gate_stat` (current lines 559-564: the `title`/`lines`/`_chunk_gate_lines` loop). Returns the list of embeds (Discord-only, no I/O).
  3. **Refactor `_handle_gate_stat` (lines 550-564) to delegate** to `build_gate_stat_embeds` for the embed section, KEEPING its ctx-based signature and its invalid-gate + no-data `ctx.send` branches intact. It MUST remain importable/callable with a `ctx` — `tests/test_ezk_gates.py::test_stat_command_accepts_kappa_and_lists_costs` calls `_handle_gate_stat(ctx, repo, settings, "k")` directly.
  4. **Add helper** `async def apply_gate_delete(bot, repo, settings, gate_type: str, index: int) -> bool` — mirrors the delete half of `_handle_gate_del` (lines 573-578 minus the role check and `ctx.send`): `if await repo.delete_gate_entry(gate_type.lower(), index): await update_gate_stats_embed(bot, repo, settings); return True` else `return False`.
  5. **Remove `_handle_gate_del` (lines 567-578)** — no external references (grep-confirmed: only `register_gate_commands`). Its role check moves into the `/del` callback; its delete/refresh moves into `apply_gate_delete`.
  6. **Rewrite `register_gate_commands` (lines 581-634)** so it wires THREE app commands instead of the three prefix commands (full spec below). Signature unchanged; still called once from `build_bot` (line 130).
  7. **Optional cleanup:** delete the now-dead keys `"stat"`, `"del"`, `"gate"`, `"gate verlauf"` from `_COMMAND_DESCRIPTIONS` (lines 367-370). Harmless if left (build_command_list only renders keys for commands present in `bot.commands`), but the handoff asks for it. Do NOT touch `_GENERIC_ARG_COMMANDS` (lines 63-67) — its `stat`/`del`/`gate`/`verlauf` entries are still asserted by on_command_error tests.

### New `register_gate_commands` body (spec — NOT code)

Keep the top-level guard style used by `register_overview_and_sync_commands`
(`if bot.tree.get_command(name) is None:` before each definition).

**`/stat`** — guard `if bot.tree.get_command("stat") is None:`
- decorators (outermost first): `@bot.tree.command(name="stat", description="Zeigt die erfassten Kosten eines Gates.")`, then `@app_commands.describe(gate="Welches Gate?")`, then `@app_commands.choices(gate=GATE_CHOICES)`.
- callback `async def gate_stat(interaction, gate: str):`
  - `gtype = gate.lower()` (defensive; Choice already constrains to a valid letter).
  - `costs = await repo.list_gate_costs(gtype)`.
  - if not costs: `await interaction.response.send_message(f"Noch keine Daten für {gtype.upper()} Gate vorhanden.")` (public, no embed) and return.
  - else: `embeds = build_gate_stat_embeds(gtype, costs)`; `await interaction.response.send_message(embed=embeds[0])`; for each `extra` in `embeds[1:]`: `await interaction.followup.send(embed=extra)` (a single interaction response can carry only one message; overflow chunks go to followups — the tests exercise only the single-chunk path).
  - NOT admin-gated, NOT ephemeral.

**`/del`** — guard `if bot.tree.get_command("del") is None:`
- decorators: `@bot.tree.command(name="del", description="Löscht einen Gate-Eintrag (nur mit Berechtigung).")`, then `@app_commands.describe(gate="Welches Gate?", index="Nummer des zu löschenden Eintrags")`, then `@app_commands.choices(gate=GATE_CHOICES)`.
- callback function named `gate_del` (NOT `del`, a keyword; the command NAME is set to "del" by the decorator): `async def gate_del(interaction, gate: str, index: int):`
  - role check: `roles = getattr(interaction.user, "roles", [])`; `has_role = any(r.id == bot.runtime_config.gate_delete_role_id for r in roles)`. Mirrors the prefix check (`bot.runtime_config.gate_delete_role_id`).
  - if not has_role: `await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)` and return (nothing deleted).
  - `gtype = gate.lower()`.
  - if `await apply_gate_delete(bot, repo, settings, gtype, index)`: `await interaction.response.send_message(f"✅ Eintrag {index} für {gtype.upper()} gelöscht.", ephemeral=True)`.
  - else: `await interaction.response.send_message(f"❌ Eintrag {index} nicht gefunden.", ephemeral=True)`.
  - (Success confirmation is ephemeral — the tests only require one `send_message` + embed refresh; ephemeral keeps the channel clean, matching the prefix command's `delete_after=5` intent.)

**`gate` group + `verlauf`** — guard `if bot.tree.get_command("gate") is None:`
- `gate_group = app_commands.Group(name="gate", description="Gate-Auswertungen.")`.
- `@gate_group.command(name="verlauf", description="Zeigt den Preisverlauf eines Gates als Diagramm.")`, then `@app_commands.describe(gate="Welches Gate?", von="Startdatum (TT.MM.JJJJ)", bis="Enddatum (TT.MM.JJJJ)")`, then `@app_commands.choices(gate=GATE_CHOICES)`.
- callback `async def gate_verlauf(interaction, gate: str, von: str | None = None, bis: str | None = None):`
  - `gtype = gate.lower()`.
  - Parse dates BEFORE deferring (so the error can use `response.send_message` ephemeral): reuse the exact loop from the prefix impl (lines 606-619). For each of `von`/`bis` that is not None, `parsed = parse_de_date(raw)`; if `parsed is None`: `await interaction.response.send_message("❌ Ungültiges Datum. Nutze TT.MM.JJJJ oder JJJJ-MM-TT.", ephemeral=True)` and return. Collect into `von_d` / `bis_d`.
  - Compute tz-aware bounds (identical to lines 620-624): `tz = ZoneInfo(settings.timezone)`; `since = datetime.combine(von_d, time(0,0,0), tzinfo=tz) if von_d else None`; `until = datetime.combine(bis_d, time(23,59,59,999999), tzinfo=tz) if bis_d else None`.
  - `await interaction.response.defer()` — public, no ephemeral (chart is public). MUST be before render/followup.
  - `entries = await repo.list_gate_entries(gtype, since, until)` — POSITIONAL args (the date-filter test reads `call.args[1]/[2]` as the fallback), so `since`/`until` line up with `list_gate_entries(gate, since, until)`.
  - `png = render_gate_history_chart(gtype, entries, now_local(settings), von_d, bis_d)` — module-global name so `patch("n3x_bot.bot.render_gate_history_chart", …)` intercepts it (already imported at line 37).
  - `msg = await interaction.followup.send(file=discord.File(BytesIO(png), filename=f"verlauf_{gtype}.png"))` — capture the returned Message.
  - `try: await msg.add_reaction("❌") except Exception: pass`.
  - `bot._verlauf_msgs[msg.id] = interaction.user.id`.
- After defining the subcommand: `bot.tree.add_command(gate_group)` (mirrors `bot.tree.add_command(admin_g)` in admin.py:353).

## Data flow

Representative call — `/gate verlauf gate="a" von="01.07.2026" bis="15.07.2026"`:
1. Framework resolves the `gate` Choice → passes raw value `"a"` to `gate_verlauf(interaction, gate="a", von=..., bis=...)`.
2. `parse_de_date("01.07.2026")` → `date(2026,7,1)`; `parse_de_date("15.07.2026")` → `date(2026,7,15)`. (Bad input → ephemeral "Datum" error, return before any defer.)
3. Build `since = 2026-07-01 00:00:00+TZ`, `until = 2026-07-15 23:59:59.999999+TZ`.
4. `interaction.response.defer()` acks the interaction ("thinking…") so the slow render doesn't hit the 3s reply deadline.
5. `repo.list_gate_entries("a", since, until)` returns the in-window entries (possibly empty).
6. `render_gate_history_chart("a", entries, now_local(settings), von_d, bis_d)` → PNG bytes (empty list still yields a valid "keine Daten" PNG).
7. `interaction.followup.send(file=discord.File(BytesIO(png), "verlauf_a.png"))` posts the chart and returns the Message.
8. `msg.add_reaction("❌")` + `bot._verlauf_msgs[msg.id] = interaction.user.id` register the original invoker for reaction-removal.
9. Later, when that user reacts ❌, the UNCHANGED `handle_verlauf_removal(bot, payload)` (bot.py:797) looks up `bot._verlauf_msgs[payload.message_id]`, matches `payload.user_id`, deletes the message, and pops the key.

`/stat gate="a"`: framework passes `"a"` → `repo.list_gate_costs("a")` → non-empty → `build_gate_stat_embeds` → first embed via `response.send_message`, overflow via `followup.send`. Empty → plain "Noch keine Daten…" text.

`/del gate="a" index=1`: role check against `bot.runtime_config.gate_delete_role_id` → if allowed, `apply_gate_delete` → `repo.delete_gate_entry("a",1)` → on success `update_gate_stats_embed` refreshes the live gate-stats embed and an ephemeral confirmation is sent; on miss an ephemeral "nicht gefunden".

## Dependencies

New packages: NONE. (matplotlib/PIL are needed by the pre-existing `render_gate_history_chart` and the chart tests — see Risks — but not by this migration.)

Internal modules this code depends on (all already imported in bot.py):
- `discord`, `discord.ext.commands`, and `from discord import app_commands` (NEW import alias; the codebase already references `discord.app_commands` and `admin.py` imports `app_commands`).
- `n3x_bot.gates.GATE_NAMES` (labels), `parse_de_date` — already imported (lines 38-42).
- `n3x_bot.charts.render_gate_history_chart` — already imported (line 37).
- `n3x_bot.storage.base.GATE_TYPES` — already imported (line 49); source of the 7 letters and their order.
- `now_local` (line 24), `format_number` (line 36), `update_gate_stats_embed`, `_chunk_gate_lines`, `BytesIO`, `ZoneInfo`, `time`, `datetime` — all already present.

## Build sequence (for the Coder)

1. Add `from discord import app_commands` to the imports at the top of `n3x_bot/bot.py` (alongside `import discord` / `from discord.ext import commands`).
2. Add the module-level `GATE_CHOICES` constant (after the existing gate constants, before `register_gate_commands`).
3. Extract `build_gate_stat_embeds(gate_type, costs) -> list[discord.Embed]` from the tail of `_handle_gate_stat`; refactor `_handle_gate_stat` to call it, preserving its invalid-gate and no-data `ctx.send` branches. Run `tests/test_ezk_gates.py` — must stay green.
4. Add `async def apply_gate_delete(bot, repo, settings, gate_type, index) -> bool`; delete the old `_handle_gate_del`.
5. Replace the body of `register_gate_commands` with the three app-command definitions (`/stat`, `/del`, `gate` group + `verlauf`) per the spec, each behind its `bot.tree.get_command(...) is None` guard; `bot.tree.add_command(gate_group)` at the end.
6. (Optional) Remove the dead `stat`/`del`/`gate`/`gate verlauf` keys from `_COMMAND_DESCRIPTIONS`.
7. Run the four target test files:
   - `tests/test_slash_migration.py` (Phase 2 section)
   - `tests/test_gate_verlauf.py` (the `/gate verlauf` wiring section)
   - `tests/test_bot_wiring.py`
   - `tests/test_command_list.py`
   - plus `tests/test_ezk_gates.py` as a regression guard.

## Risks and open questions

- **matplotlib/PIL dependency (pre-existing, flag to user):** the chart-render and PNG-validity tests in `test_gate_verlauf.py` (and PIL in the test) require `matplotlib` + `Pillow`. The TDD notes say matplotlib is NOT installed in the env, so those tests RED on `ModuleNotFoundError` independent of this migration. `render_gate_history_chart` and `charts.py` already exist and are unchanged by this work. Recommendation: ensure `matplotlib` and `Pillow` are installed / declared as project deps before expecting the chart tests to pass — but that is orthogonal to the slash-command wiring and should be confirmed with the user rather than silently added here.
- **Multi-chunk `/stat` overflow:** an interaction's initial `response.send_message` can send only one message, so embeds beyond the first are sent via `followup.send`. The tests only cover the single-embed path; the followup overflow path is a design choice (no test pins it). Called out so it isn't mistaken for untested speculative code — it is the minimal correct handling of the existing chunking logic under the interaction API.
- **`/del` success confirmation is ephemeral:** the tests assert only that a `send_message` occurs and the embed refreshes; ephemeral vs public is unpinned for the success case. Chosen ephemeral to match the prefix command's `delete_after=5` "transient confirmation" intent and keep the channel clean. Denial and the date error ARE pinned ephemeral by the tests.
- **`_GENERIC_ARG_COMMANDS` intentionally retains `stat`/`del`/`gate`/`verlauf`:** even though these are no longer prefix commands, three on_command_error tests still assert that a `MissingRequiredArgument` on those NAMES yields the generic hint. Leaving the set unchanged is deliberate, not an oversight.
- **`interaction.user.roles` on DM/global users:** guarded with `getattr(interaction.user, "roles", [])` so a role-less user is denied rather than raising (consistent with `app_is_admin`'s defensive handling in Phase 1).
