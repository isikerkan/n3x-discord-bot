# Architecture: Phase 3 — `/config` and `/content` slash-only admin groups

Migrates the `!config` and `!content` prefix command groups to slash-only
`app_commands.Group`s on `bot.tree`, replacing the interactive
`ChannelConfigView`/`RoleConfigView` pickers with native channel/role options.
Mirrors the Phase-2 `/gate` group (bot.py:625-669) and `/admin` group
(admin.py:242-353). The `content.py` resolver (`ContentTexts`,
`CONTENT_DEFAULTS`, `CONTENT_KEYS`) already exists and fully satisfies
test_content_texts.py sections 1-2 — no change there. Read-site routing
(section 5 of test_content_texts.py) is already wired in bot.py
(lines 870-931). This phase touches ONLY the two command-registration modules
plus a dead-entry cleanup in bot.py.

## Verified facts (do not re-litigate)
- discord.py 2.7.1 (`requirements.txt`). Confirmed by executing against
  `.venv`: `discord.abc.GuildChannel` → `AppCommandOptionType.channel`,
  `discord.Role` → `AppCommandOptionType.role`, hyphenated subcommand names
  (`gate-delete-delay`) are accepted, and `@app_commands.choices(...)` on a
  `str`-annotated param yields a param whose `.choices` carry the given values.
- Direct-callback tests bypass Discord transforms, so passing
  `SimpleNamespace(id=999)` as `channel=`/`role=` works; the body only reads
  `.id`. The native annotation matters only for Discord-side registration.
- `OVERRIDABLE_KEYS` = 19 keys, `CHANNEL_PURPOSES` = 10, `ROLE_PURPOSES` = 3,
  `MESSAGE_PURPOSES` = 1, `CONTENT_KEYS` = 6 — all ≤ 25, the Discord choice cap.
- `app_is_admin(interaction, settings)` (admin.py:26) reads
  `interaction.user.roles`; the fake interaction's `user` is an admin/non-admin
  `SimpleNamespace` with `.roles`, `settings.admin_role_id == 42`.
- No external references to `ChannelConfigView`/`RoleConfigView` or the prefix
  `config`/`content` commands exist outside the two modules and the
  `_COMMAND_DESCRIPTIONS` config entries in bot.py (grep-confirmed).

## Tests this design satisfies

### tests/test_config_commands.py (24)
- test_config_is_app_group_not_prefix_command — `config` absent from prefix, present as `app_commands.Group` on tree
- test_config_group_exposes_expected_subcommands — names ⊇ {channel, role, message, gate-rewards, allowed-maps, voice-roles, reminder-time, gate-delete-delay, show, reset}
- test_config_view_classes_are_removed — module has no `ChannelConfigView`/`RoleConfigView`
- test_register_config_commands_is_idempotent — re-register after build_bot must not raise
- test_config_channel_writes_id_and_refreshes — `welcome_channel_id="999"` + live resolver reflects it
- test_config_channel_confirms_ephemerally — last send `ephemeral=True`
- test_all_channel_purposes_map_to_expected_keys — 8 purposes → correct keys via `str(channel.id)`
- test_config_channel_non_admin_refused_no_write — "Berechtigung", ephemeral, no write, resolver unchanged
- test_config_role_writes_id_and_refreshes — `target_role_id="777"` + resolver + ephemeral
- test_all_role_purposes_map_to_expected_keys — 3 purposes → correct keys
- test_config_role_non_admin_refused_no_write
- test_config_message_writes_id_and_refreshes — `timer_overview_message_id="555"` + resolver + ephemeral
- test_config_message_non_numeric_id_rejected_no_write — `"55x"` → replied, no write, ephemeral
- test_config_message_non_admin_refused
- test_config_gate_rewards_sets_value_and_refreshes — verbatim write + resolver + ephemeral
- test_config_allowed_maps_sets_value_and_refreshes
- test_config_voice_roles_sets_value_and_refreshes
- test_config_reminder_time_sets_value_and_refreshes
- test_config_gate_delete_delay_sets_value_verbatim_and_refreshes — `"2m"` written verbatim
- test_config_gate_delete_delay_rejects_invalid_duration_no_write — `"banana"` → "Ungültige Dauer", no write, ephemeral
- test_config_content_setter_non_admin_refused_no_write — gate-rewards as non-admin
- test_config_show_includes_overridden_key_and_db_value — shows override value
- test_config_show_lists_non_overridden_key_at_env_default — shows `.env` base
- test_config_show_is_ephemeral
- test_config_show_never_leaks_token_or_db_url — iterate OVERRIDABLE_KEYS only
- test_config_show_non_admin_refused
- test_config_reset_reverts_override_to_env_default — delete override + resolver + ephemeral
- test_config_reset_key_choices_are_exactly_overridable_keys — `key` choices == `set(OVERRIDABLE_KEYS)`
- test_config_reset_non_admin_refused
- test_register_config_commands_entrypoint_exists — callable `register_config_commands`
- test_build_bot_registers_config_group_on_tree — build_bot alone wires the group

### tests/test_content_texts.py sections 3-4 (the command-migration subset; sections 1-2 & 5 already pass)
- test_build_bot_registers_content_group_on_tree — `content` off prefix, on tree as Group
- test_content_group_exposes_expected_subcommands — {list, show, set, reset}
- test_register_content_commands_entrypoint_exists
- test_register_content_commands_is_idempotent
- test_content_key_choices_are_exactly_content_keys — show/set/reset `key` choices == `set(CONTENT_KEYS)`
- test_content_set_stores_value_and_refreshes_live_resolver — write + resolver + ephemeral
- test_content_set_welcome_dm_wrong_placeholder_rejected_no_write — `{name}` → "Platzhalter", no write, ephemeral
- test_content_set_welcome_dm_valid_placeholder_stored — `{mention}` stored
- test_content_set_record_template_extra_literal_text_stored — all placeholders + extra text OK
- test_content_set_record_template_bad_placeholder_rejected_no_write — `{bogus}` → "Platzhalter", no write
- test_content_set_non_template_key_not_validated — `kodex_text` arbitrary text stored
- test_content_set_non_admin_refused_no_write
- test_content_reset_reverts_to_default — delete + resolver + ephemeral
- test_content_reset_non_admin_refused
- test_content_show_reports_effective_value_for_key — effective value in text, ephemeral
- test_content_show_non_admin_refused
- test_content_list_includes_all_keys_and_overridden_marker — all 6 keys in text, ephemeral
- test_content_list_non_admin_refused

### tests/test_bot_wiring.py
- test_on_command_error_missing_arg_is_generic_for_config_subcommands REMOVED (config has no prefix subcommands now) — no code required; verify no new prefix `config` command reintroduces the branch. The existing `on_command_error` handler is unchanged.

## Files to create
- None. (`n3x_bot/content.py` already provides the resolver and defaults.)

## Files to modify

### `n3x_bot/config_commands.py` — full rewrite of the command-wiring body
Keep at module top:
- The three purpose maps `CHANNEL_PURPOSES`, `ROLE_PURPOSES`, `MESSAGE_PURPOSES`
  (unchanged — the Choice lists are built from their keys).
- `register_config_commands(bot, repo, settings)` name/signature (called from
  build_bot:133).

Delete:
- `class ChannelConfigView`, `class RoleConfigView` (lines 40-91).
- The entire prefix-based body of `register_config_commands` (the
  `@bot.group`/`@config.command` block, incl. the nested `_set_content`).

Change imports:
- Remove `from n3x_bot.admin import is_admin`; add
  `from n3x_bot.admin import app_is_admin`.
- Add `from discord import app_commands`.
- Keep `import discord` (needed for `discord.abc.GuildChannel` / `discord.Role`
  annotations), keep `parse_duration`, `OVERRIDABLE_KEYS`, `Settings`,
  `StatsRepository`.

New body of `register_config_commands(bot, repo, settings)`:
```
if bot.tree.get_command("config") is not None:
    return
config_group = app_commands.Group(name="config", description="Laufzeit-Konfiguration (Admin).")
# ... define nested helpers + @config_group.command(...) callbacks ...
bot.tree.add_command(config_group)
```

Nested helpers (mirror the old nested `_set_content` convention so `bot`/`repo`/
`settings` are captured by closure):
- `async def _require_admin(interaction) -> bool:` — returns True if
  `app_is_admin(interaction, settings)`; else
  `await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)`
  and returns False. Every callback starts with `if not await _require_admin(interaction): return`.
  Rationale: single ephemeral-refusal shape, no write, no refresh — exactly what
  every `*_non_admin_refused*` test asserts.
- `async def _write(interaction, key, value):` — `repo.set_runtime_config(key, value)`;
  `await bot.runtime_config.refresh(repo)`;
  `await interaction.response.send_message(f"✅ \`{key}\` gesetzt.", ephemeral=True)`.
  Shared by the four verbatim setters, gate-delete-delay, channel/role/message.

Subcommand callbacks (all admin-gated first; all replies ephemeral):

- `channel(interaction, purpose: str, channel: discord.abc.GuildChannel)`
  - decorators: `@config_group.command(name="channel", description=…)`,
    `@app_commands.describe(purpose="Zweck des Kanals", channel="Kanal")`,
    `@app_commands.choices(purpose=[app_commands.Choice(name=k, value=k) for k in CHANNEL_PURPOSES])`
  - body: admin gate; `await _write(interaction, CHANNEL_PURPOSES[purpose], str(channel.id))`.

- `role(interaction, purpose: str, role: discord.Role)`
  - `@app_commands.choices(purpose=[Choice(name=k, value=k) for k in ROLE_PURPOSES])`
  - body: admin gate; `_write(interaction, ROLE_PURPOSES[purpose], str(role.id))`.

- `message(interaction, purpose: str, message_id: str)`
  - `@app_commands.choices(purpose=[Choice(name=k, value=k) for k in MESSAGE_PURPOSES])`,
    `@app_commands.describe(purpose="Zweck", message_id="Nachrichten-ID")`
  - body: admin gate; if not `message_id.isdigit()`:
    `await interaction.response.send_message(f"❌ Ungültige ID \`{message_id}\`.", ephemeral=True); return`;
    else `_write(interaction, MESSAGE_PURPOSES[purpose], message_id)`.

- `gate_rewards(interaction, value: str)` → name `"gate-rewards"`; admin gate; `_write(interaction, "gate_rewards", value)`.
- `allowed_maps(interaction, value: str)` → name `"allowed-maps"`; `_write(interaction, "allowed_maps", value)`.
- `voice_roles(interaction, value: str)` → name `"voice-roles"`; `_write(interaction, "voice_achievement_roles", value)`.
- `reminder_time(interaction, value: str)` → name `"reminder-time"`; `_write(interaction, "reminder_time", value)`.
  (All four use `@app_commands.describe(value=…)`.)

- `gate_delete_delay(interaction, value: str)` → name `"gate-delete-delay"`;
  admin gate; `try: parse_duration(value) except ValueError:`
  `await interaction.response.send_message("❌ Ungültige Dauer. Beispiele: 30s, 1m, 5m, 2h, 90", ephemeral=True); return`;
  else `_write(interaction, "gate_message_delete_delay", value)`.

- `show(interaction)` → admin gate; `overrides = await repo.all_runtime_config()`;
  build one text block iterating `sorted(OVERRIDABLE_KEYS)`:
  `f"\`{key}\` = \`{overrides[key]}\` (Override)"` when overridden else
  `f"\`{key}\` = \`{getattr(settings, key)}\`"`; send once via
  `interaction.response.send_message(text, ephemeral=True)`. Only OVERRIDABLE_KEYS
  are iterated, so `discord_token`/`database_url` can never leak.
  Note: 19 short lines stay well under Discord's 2000-char limit → a single
  message is safe; no chunking needed at this key count.

- `reset(interaction, key: str)`
  - `@app_commands.choices(key=[Choice(name=k, value=k) for k in sorted(OVERRIDABLE_KEYS)])`,
    `@app_commands.describe(key="Zurückzusetzender Schlüssel")`
  - body: admin gate; `await repo.delete_runtime_config(key)`;
    `await bot.runtime_config.refresh(repo)`;
    `await interaction.response.send_message(f"✅ Override \`{key}\` entfernt.", ephemeral=True)`.

### `n3x_bot/content_commands.py` — full rewrite of the command-wiring body
Keep at module top:
- `REQUIRED_PLACEHOLDERS` (unchanged).
- `register_content_commands(bot, repo, settings)` name/signature (build_bot:134).

Change imports:
- Remove `from n3x_bot.admin import is_admin`; add
  `from n3x_bot.admin import app_is_admin`.
- Add `from discord import app_commands`.
- Keep `from n3x_bot.content import CONTENT_KEYS`, `Settings`, `StatsRepository`.

New body:
```
if bot.tree.get_command("content") is not None:
    return
content_group = app_commands.Group(name="content", description="Narrative Texte (Admin).")
# nested _require_admin (same shape as config_commands)
# @content_group.command callbacks:
bot.tree.add_command(content_group)
```

Subcommands (`key` choices built from `sorted(CONTENT_KEYS)` as
`Choice(name=k, value=k)`; test asserts `{c.value for c in choices} == set(CONTENT_KEYS)`):

- `list(interaction)` → name `"list"`; admin gate;
  `overrides = await repo.all_content_texts()`; build text over
  `sorted(CONTENT_KEYS)`: `f"\`{key}\`"` + `" (Override)"` when
  `key in overrides`; `interaction.response.send_message(text, ephemeral=True)`.

- `show(interaction, key: str)` → `@app_commands.choices(key=…)`; admin gate;
  `await interaction.response.send_message(f"\`\`\`\n{bot.content_texts.get(key)}\n\`\`\`", ephemeral=True)`.

- `set(interaction, key: str, value: str)` → `@app_commands.choices(key=…)`,
  `@app_commands.describe(key="Schlüssel", value="Neuer Text")`; admin gate;
  placeholder validation (verbatim from the old prefix `set_cmd`):
  ```
  required = REQUIRED_PLACEHOLDERS.get(key)
  if required is not None:
      try:
          value.format(**{p: "" for p in required})
      except (KeyError, IndexError, ValueError):
          allowed = ", ".join(f"{{{p}}}" for p in sorted(required))
          await interaction.response.send_message(
              f"❌ Ungültige Platzhalter. Erlaubt für `{key}`: {allowed}",
              ephemeral=True)
          return
  await repo.set_content_text(key, value)
  await bot.content_texts.refresh(repo)
  await interaction.response.send_message(f"✅ `{key}` gesetzt.", ephemeral=True)
  ```
  The error message contains "Platzhalter" as asserted; validation happens
  before any write.

- `reset(interaction, key: str)` → `@app_commands.choices(key=…)`; admin gate;
  `await repo.delete_content_text(key)`;
  `await bot.content_texts.refresh(repo)`;
  `await interaction.response.send_message(f"✅ Override \`{key}\` entfernt.", ephemeral=True)`.

Note: the Python function for `set` cannot literally be named `set` if it
shadows the builtin in an awkward way — it is fine as a nested def, but name it
`set_cmd`/`list_cmd` (as the old code did) while passing `name="set"`/`"list"`
to the decorator. The decorator's `name=` is what the test looks up.

### `n3x_bot/bot.py` — dead-entry cleanup only (no behavioural change)
- Remove the now-dead `config`-keyed entries from `_COMMAND_DESCRIPTIONS`
  (lines 372-377: `"config"`, `"config channel"`, `"config role"`,
  `"config message"`, `"config show"`, `"config reset"`). `build_command_list`
  enumerates `bot.commands` (prefix registry); `config` is no longer a prefix
  command, so these keys can never match a `qualified_name` again. There are no
  `content`-keyed entries to remove. Do NOT otherwise touch
  `build_command_list` this phase.
- No change to `build_bot`, `register_config_commands`/`register_content_commands`
  call sites (133-134), `on_ready`, `sync_commands_to_guilds`, `on_command_error`,
  or `bot.tree.on_error` — the groups get published through the existing
  guild-scoped sync path unchanged.

## Data flow

`/config channel purpose:gate_stats channel:#foo` (admin):
1. Discord resolves the native channel option to a `TextChannel`; in tests the
   callback is invoked directly with `channel=SimpleNamespace(id=321)`.
2. `_require_admin(interaction)` → `app_is_admin(interaction, settings)` reads
   `interaction.user.roles`; admin → continue.
3. `_write(interaction, CHANNEL_PURPOSES["gate_stats"], str(channel.id))`:
   `repo.set_runtime_config("gate_stats_channel_id", "321")`.
4. `await bot.runtime_config.refresh(repo)` reloads overrides ∩ OVERRIDABLE_KEYS
   → `bot.runtime_config.gate_stats_channel_id == 321` immediately.
5. `interaction.response.send_message("✅ `gate_stats_channel_id` gesetzt.", ephemeral=True)`.

`/content set key:welcome_dm value:"Hi {name}!"` (admin, invalid):
1. admin gate passes.
2. `REQUIRED_PLACEHOLDERS["welcome_dm"] == {"mention"}`;
   `"Hi {name}!".format(mention="")` raises `KeyError` → ephemeral "Ungültige
   Platzhalter…", `return` before any `set_content_text` → repo untouched,
   resolver still returns the default.

Non-admin on any subcommand:
1. `_require_admin` sends ephemeral "❌ Keine Berechtigung." and returns False.
2. Callback returns immediately — no `set_*`/`delete_*`, no `refresh`. Repo and
   live resolver unchanged.

## Dependencies
- New packages: none.
- Internal modules: `discord.app_commands` (Group/command/describe/choices/Choice),
  `n3x_bot.admin.app_is_admin`, `n3x_bot.config.parse_duration`/`Settings`,
  `n3x_bot.runtime_config.OVERRIDABLE_KEYS`, `n3x_bot.content.CONTENT_KEYS`,
  `bot.runtime_config.refresh` / `bot.content_texts.refresh` / `.get`,
  repo methods `set_runtime_config`/`delete_runtime_config`/`all_runtime_config`,
  `set_content_text`/`delete_content_text`/`all_content_texts`.

## Build sequence (for the Coder)
1. `n3x_bot/config_commands.py`: swap imports (drop `is_admin`, add `app_is_admin`
   + `app_commands`), delete both View classes, replace the
   `register_config_commands` body with the tree-group version (idempotency
   guard → group → nested `_require_admin`/`_write` → 10 subcommands →
   `bot.tree.add_command`). Run test_config_commands.py to green (24).
2. `n3x_bot/content_commands.py`: same import swap, replace
   `register_content_commands` body with the tree-group version (guard → group →
   `_require_admin` → list/show/set/reset → add). Run test_content_texts.py to
   green (all sections; 1-2 & 5 already passed).
3. `n3x_bot/bot.py`: delete the six dead `config*` keys from
   `_COMMAND_DESCRIPTIONS`. Run test_bot_wiring.py + full suite to confirm no
   regression (esp. the wired-count exclusion lists already name config/content).
4. Full test run.

## Risks and open questions
- Choice `name` readability: tests assert only `.value`, so `name=key` is used
  verbatim. If human-friendly labels are wanted in the Discord UI later, that's a
  cosmetic follow-up, not a test requirement — flagging, not designing around it.
- `CHANNEL_PURPOSES` (10 keys) is a superset of the test's `CHANNEL_MAP` (8);
  `gate_chart`/`command_list` have channel purposes but aren't exercised. No test
  pins the channel Choice set to exactly `CHANNEL_PURPOSES`, so keeping all 10 is
  safe and preserves existing operator capability. (Contrast: `reset` and
  `content` key Choices ARE pinned exactly, and are built from
  OVERRIDABLE_KEYS/CONTENT_KEYS respectively.)
- `show` single-message assumption: valid at 19 keys (< 2000 chars). If
  OVERRIDABLE_KEYS grows past a single Discord message the coder must add
  followup chunking; not needed now. Flagging the boundary.
- The `content` group is NOT admin-gated at the Discord permission layer
  (default_permissions) — gating is purely the in-callback `app_is_admin` check,
  matching the tests and the Phase-2 `/admin` group precedent. A non-admin can
  still invoke and receive the ephemeral refusal. This is the established
  pattern; no change proposed.
