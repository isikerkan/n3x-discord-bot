"""RED-phase specs for editable narrative copy (de-hardcode Phase 1).

Hardcoded German player-facing strings move to a ``content_texts`` DB table
with CODE defaults, editable live via ``!content`` admin commands. This mirrors
the ``runtime_config`` resolver pattern (DB value else default) + the
``!config`` command style — but here the fallback is a code constant, not
``Settings``.

Surfaces (to be implemented downstream):

  * ``n3x_bot/content.py``:
        CONTENT_DEFAULTS: dict[str, str]   # kodex_text, reminder_aceball,
            reminder_invasion, record_lucky, record_unlucky, welcome_dm
        CONTENT_KEYS = frozenset(CONTENT_DEFAULTS)
        class ContentTexts:
            __init__(overrides: dict[str, str] | None = None)
            get(key: str) -> str              # DB override else CONTENT_DEFAULTS[key]
            async refresh(repo) -> None       # cache = all_content_texts() ∩ CONTENT_KEYS
            @classmethod async load(repo) -> ContentTexts

  * ``n3x_bot/content_commands.py``:
        register_content_commands(bot, repo, settings) -> None   # idempotent
            !content list                     -> keys + overridden?
            !content show  <key>              -> effective value
            !content set   <key> <value...>   -> set_content_text + refresh
            !content reset <key>              -> delete_content_text + refresh

  * Read-site routing (defaults == the OLD constants, so behaviour is preserved):
        kodex.send_kodex_dm      -> bot.content_texts.get("kodex_text")
        welcome.send_welcome_card-> get("welcome_dm").format(mention=…)
        bot._announce_records    -> get("record_lucky"/"record_unlucky").format(…)
        bot event_reminder_task  -> get("reminder_aceball"/"reminder_invasion")
        build_bot attaches       -> bot.content_texts = ContentTexts()
        on_ready refreshes it.

PINNED ASSUMPTIONS (see the handoff report):
  * ``ContentTexts.get`` on a key NOT in CONTENT_DEFAULTS raises KeyError
    (plain-dict semantics — content keys are a closed, code-defined set).
  * ``!content`` lives in a NEW module ``n3x_bot/content_commands.py`` (a
    ``commands.Group`` named ``content``), NOT folded into config_commands.
  * ``content set`` consumes the rest of the line as the value (keyword-only
    ``*, value`` param) so multi-word German copy survives.
  * Record templates use ``{user}`` / ``{name}`` / ``{cost}``; welcome uses
    ``{mention}``.
  * ``CONTENT_DEFAULTS["kodex_text"]`` IS ``kodex.KODEX_TEXT`` (the constant
    stays the source of the default).

Imports of the not-yet-existing modules are LAZY (inside test bodies) so
collection succeeds and each test REDs cleanly on ModuleNotFoundError /
AttributeError / AssertionError rather than a collection-time ImportError.
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


from n3x_bot.bot import build_bot
from n3x_bot.config import Settings
from n3x_bot.seed import seed_defaults
from n3x_bot.storage.json_repo import JsonRepository

ADMIN_ROLE = 42

# The exact CURRENT hardcoded reminder strings (bot.py:798/800). Defaults must
# equal these verbatim so the reminder behaviour is preserved with no override.
ACEBALL_STRING = "*EVENT REMINDER*: ACE-BALL beginnt in 30 Minuten! @everyone"
INVASION_STRING = "*EVENT REMINDER*: Invasion beginnt in 30 Minuten! @everyone"
WELCOME_DM_STRING = "Willkommen {mention}!"  # welcome.py:90 template

BASE_SETTINGS_KWARGS = dict(
    discord_token="tok",
    target_role_id=1,
    welcome_channel_id=2,
    reminder_channel_id=999,
    julez_id=424242,
    admin_role_id=ADMIN_ROLE,
    _env_file=None,
    _env_prefix="NONEXISTENT_",
)


def _settings(**overrides) -> Settings:
    kwargs = dict(BASE_SETTINGS_KWARGS)
    kwargs.update(overrides)
    return Settings(**kwargs)


async def _flatfile_repo() -> JsonRepository:
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    repo = JsonRepository(path)
    await repo.connect()
    await seed_defaults(repo)
    repo._test_path = path
    return repo


async def _cleanup(repo: JsonRepository) -> None:
    path = getattr(repo, "_test_path", None)
    await repo.close()
    if path and os.path.exists(path):
        os.remove(path)


def _member(*, member_id=5, role_ids=(ADMIN_ROLE,)):
    return SimpleNamespace(id=member_id,
                           roles=[SimpleNamespace(id=r) for r in role_ids],
                           bot=False)


def _admin():
    return _member(role_ids=(ADMIN_ROLE,))


def _non_admin():
    return _member(role_ids=(999,))


def _fake_interaction(user=None):
    it = MagicMock()
    it.user = user or _admin()
    it.response = MagicMock()
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.followup = MagicMock()
    it.followup.send = AsyncMock()
    return it


def _sent_text(interaction) -> str:
    """All text the callback sent the caller, via response.send_message and any
    followup.send, joined — content arg, `content=` kwarg, or embed text."""
    parts = []
    for mock in (interaction.response.send_message, interaction.followup.send):
        for call in mock.await_args_list:
            if call.args and isinstance(call.args[0], str):
                parts.append(call.args[0])
            content = call.kwargs.get("content")
            if isinstance(content, str):
                parts.append(content)
            embed = call.kwargs.get("embed")
            if embed is not None:
                parts.append(str(getattr(embed, "description", "") or ""))
                for field in getattr(embed, "fields", []):
                    parts.append(f"{field.name} {field.value}")
    return "\n".join(parts)


def _last_send(interaction):
    calls = (list(interaction.response.send_message.await_args_list)
             + list(interaction.followup.send.await_args_list))
    assert calls, "the callback must reply to the caller"
    return calls[-1]


def _content_group(bot):
    from discord import app_commands
    assert bot.get_command("content") is None, \
        "`content` must be REMOVED from the prefix registry (slash-only)"
    group = bot.tree.get_command("content")
    assert isinstance(group, app_commands.Group), \
        "`content` must be an app_commands.Group on bot.tree"
    return group


def _content_sub(bot, name):
    sub = _content_group(bot).get_command(name)
    assert sub is not None, f"/content {name} subcommand must be registered"
    return sub


# ══════════════════════════════════════════════════════════════════════════
# 1. CONTENT_DEFAULTS / CONTENT_KEYS — the code defaults
# ══════════════════════════════════════════════════════════════════════════

async def test_content_defaults_has_all_expected_keys():
    from n3x_bot.content import CONTENT_DEFAULTS
    expected = {"kodex_text", "reminder_aceball", "reminder_invasion",
                "record_lucky", "record_unlucky", "welcome_dm"}
    assert expected <= set(CONTENT_DEFAULTS)


async def test_content_keys_is_frozenset_of_defaults():
    from n3x_bot.content import CONTENT_DEFAULTS, CONTENT_KEYS
    assert isinstance(CONTENT_KEYS, frozenset)
    assert CONTENT_KEYS == frozenset(CONTENT_DEFAULTS)


async def test_kodex_text_default_is_the_kodex_constant():
    from n3x_bot import kodex
    from n3x_bot.content import CONTENT_DEFAULTS
    assert CONTENT_DEFAULTS["kodex_text"] == kodex.KODEX_TEXT
    assert "Verhaltenskodex" in CONTENT_DEFAULTS["kodex_text"]


async def test_reminder_defaults_equal_current_hardcoded_strings():
    from n3x_bot.content import CONTENT_DEFAULTS
    assert CONTENT_DEFAULTS["reminder_aceball"] == ACEBALL_STRING
    assert CONTENT_DEFAULTS["reminder_invasion"] == INVASION_STRING


async def test_welcome_dm_default_carries_mention_placeholder():
    from n3x_bot.content import CONTENT_DEFAULTS
    assert CONTENT_DEFAULTS["welcome_dm"] == WELCOME_DM_STRING
    assert "{mention}" in CONTENT_DEFAULTS["welcome_dm"]


async def test_record_templates_carry_user_name_cost_placeholders():
    from n3x_bot.content import CONTENT_DEFAULTS
    for key in ("record_lucky", "record_unlucky"):
        tpl = CONTENT_DEFAULTS[key]
        assert "{user}" in tpl, key
        assert "{name}" in tpl, key
        assert "{cost}" in tpl, key


async def test_record_lucky_default_formats_without_keyerror():
    # The template must be `.format(user=…, name=…, cost=…)`-able with exactly
    # those three named fields and no stray positional/unknown placeholders.
    from n3x_bot.content import CONTENT_DEFAULTS
    out = CONTENT_DEFAULTS["record_lucky"].format(user=3, name="Delta", cost="50")
    assert "3" in out and "Delta" in out and "50" in out
    assert "Glückspilz" in out


async def test_record_unlucky_default_formats_without_keyerror():
    from n3x_bot.content import CONTENT_DEFAULTS
    out = CONTENT_DEFAULTS["record_unlucky"].format(user=4, name="Delta", cost="600")
    assert "4" in out and "Delta" in out and "600" in out
    assert "Pechvogel" in out


# ══════════════════════════════════════════════════════════════════════════
# 2. ContentTexts resolver
# ══════════════════════════════════════════════════════════════════════════

async def test_get_no_override_returns_default():
    from n3x_bot.content import CONTENT_DEFAULTS, ContentTexts
    ct = ContentTexts()
    for key in CONTENT_DEFAULTS:
        assert ct.get(key) == CONTENT_DEFAULTS[key], key


async def test_get_override_wins_over_default():
    from n3x_bot.content import ContentTexts
    ct = ContentTexts({"kodex_text": "Mein neuer Kodex"})
    assert ct.get("kodex_text") == "Mein neuer Kodex"


async def test_get_unset_key_still_default_when_another_is_overridden():
    from n3x_bot.content import CONTENT_DEFAULTS, ContentTexts
    ct = ContentTexts({"kodex_text": "Mein neuer Kodex"})
    assert ct.get("welcome_dm") == CONTENT_DEFAULTS["welcome_dm"]


async def test_get_unknown_key_raises_keyerror():
    # Content keys are a closed, code-defined set; an unknown key is a bug, not
    # a silent empty string (pinned semantics).
    from n3x_bot.content import ContentTexts
    ct = ContentTexts()
    try:
        ct.get("not_a_content_key")
        raised = False
    except KeyError:
        raised = True
    assert raised, "get() of an unknown content key must raise KeyError"


async def test_init_ignores_override_for_non_content_key():
    # An override stored under a key that is NOT a content key must be dropped
    # by the resolver (it can never be resolved via get()).
    from n3x_bot.content import ContentTexts
    ct = ContentTexts({"bogus_key": "x", "kodex_text": "override"})
    assert ct.get("kodex_text") == "override"
    # the stray key is not resolvable
    try:
        ct.get("bogus_key")
        raised = False
    except KeyError:
        raised = True
    assert raised


async def test_refresh_loads_db_value_and_filters_to_content_keys():
    from n3x_bot.content import CONTENT_DEFAULTS, ContentTexts
    repo = await _flatfile_repo()
    ct = ContentTexts()
    assert ct.get("kodex_text") == CONTENT_DEFAULTS["kodex_text"]  # default first

    await repo.set_content_text("kodex_text", "DB Kodex")
    await repo.set_content_text("not_a_content_key", "junk")  # must be filtered
    await ct.refresh(repo)

    assert ct.get("kodex_text") == "DB Kodex"
    try:
        ct.get("not_a_content_key")
        leaked = True
    except KeyError:
        leaked = False
    assert not leaked, "refresh must not admit keys outside CONTENT_KEYS"

    await _cleanup(repo)


async def test_load_classmethod_builds_resolver_with_db_overrides():
    from n3x_bot.content import ContentTexts
    repo = await _flatfile_repo()
    await repo.set_content_text("welcome_dm", "Hallo {mention} :)")

    ct = await ContentTexts.load(repo)

    assert ct.get("welcome_dm") == "Hallo {mention} :)"

    await _cleanup(repo)


async def test_load_with_no_overrides_is_behaviour_preserving():
    from n3x_bot.content import CONTENT_DEFAULTS, ContentTexts
    repo = await _flatfile_repo()

    ct = await ContentTexts.load(repo)

    for key in CONTENT_DEFAULTS:
        assert ct.get(key) == CONTENT_DEFAULTS[key], key

    await _cleanup(repo)


# ══════════════════════════════════════════════════════════════════════════
# 3. build_bot wiring: bot.content_texts + the `content` command group
# ══════════════════════════════════════════════════════════════════════════

async def test_build_bot_attaches_content_texts():
    from n3x_bot.content import ContentTexts
    repo = await _flatfile_repo()
    settings = _settings()

    bot = build_bot(settings, repo)

    assert isinstance(bot.content_texts, ContentTexts)

    await _cleanup(repo)


async def test_build_bot_content_texts_behaviour_preserving_without_overrides():
    from n3x_bot.content import CONTENT_DEFAULTS
    repo = await _flatfile_repo()
    settings = _settings()

    bot = build_bot(settings, repo)

    assert bot.content_texts.get("kodex_text") == CONTENT_DEFAULTS["kodex_text"]
    assert bot.content_texts.get("reminder_aceball") == ACEBALL_STRING

    await _cleanup(repo)


async def test_build_bot_registers_content_group_on_tree():
    from discord import app_commands
    repo = await _flatfile_repo()
    settings = _settings()

    bot = build_bot(settings, repo)

    assert bot.get_command("content") is None       # dropped from prefix registry
    assert isinstance(bot.tree.get_command("content"), app_commands.Group)

    await _cleanup(repo)


async def test_content_group_exposes_expected_subcommands():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    names = {c.name for c in _content_group(bot).commands}
    assert {"list", "show", "set", "reset"} <= names

    await _cleanup(repo)


async def test_register_content_commands_entrypoint_exists():
    import n3x_bot.content_commands as ccmod
    assert callable(getattr(ccmod, "register_content_commands", None))


async def test_register_content_commands_is_idempotent():
    from n3x_bot.content_commands import register_content_commands
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    from discord import app_commands
    register_content_commands(bot, repo, settings)  # re-register must not raise

    assert bot.get_command("content") is None
    assert isinstance(bot.tree.get_command("content"), app_commands.Group)

    await _cleanup(repo)


async def test_on_ready_refreshes_content_texts():
    # on_ready must pull DB overrides into the live resolver (like it does for
    # runtime_config), so an edit made while offline goes live on reconnect.
    repo = await _flatfile_repo()
    settings = _settings(gate_stats_channel_id=0)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)
    bot.tree.sync = AsyncMock()
    await repo.set_content_text("kodex_text", "Refreshed Kodex")

    await bot.on_ready()

    assert bot.content_texts.get("kodex_text") == "Refreshed Kodex"

    await _cleanup(repo)


# ══════════════════════════════════════════════════════════════════════════
# 4. /content admin commands (slash-only)
#
# `content` is an app_commands.Group on bot.tree (removed from the prefix
# registry), admin-gated via app_is_admin. Subcommands are invoked directly:
#     bot.tree.get_command("content").get_command("set").callback(
#         interaction, key="kodex_text", value="…")
# `key` is a Choice[str] over CONTENT_KEYS, so an unknown key can never reach a
# callback body — the old "unknown key" branches are gone. Refusals/errors are
# ephemeral and perform NO write.
# ══════════════════════════════════════════════════════════════════════════

async def test_content_key_choices_are_exactly_content_keys():
    # show/set/reset enumerate `key` over CONTENT_KEYS, so invalid keys are
    # unreachable at the callback boundary.
    from n3x_bot.content import CONTENT_KEYS
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)

    for name in ("show", "set", "reset"):
        sub = _content_sub(bot, name)
        param = next(p for p in sub.parameters if p.name == "key")
        assert {c.value for c in param.choices} == set(CONTENT_KEYS), name

    await _cleanup(repo)


async def test_content_set_stores_value_and_refreshes_live_resolver():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    interaction = _fake_interaction()

    await _content_sub(bot, "set").callback(interaction, key="kodex_text",
                                            value="Ganz neuer Kodex Text")

    assert await repo.get_content_text("kodex_text") == "Ganz neuer Kodex Text"
    # refresh() ran -> the live resolver reflects the override immediately.
    assert bot.content_texts.get("kodex_text") == "Ganz neuer Kodex Text"
    assert _last_send(interaction).kwargs.get("ephemeral") is True

    await _cleanup(repo)


async def test_content_set_welcome_dm_wrong_placeholder_rejected_no_write():
    # welcome_dm is `.format(mention=…)`-ed at its read-site; an override using a
    # placeholder the read-site does not supply (`{name}`) would raise KeyError
    # there (silently swallowed). Reject on write instead — no store, no refresh.
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    interaction = _fake_interaction()

    await _content_sub(bot, "set").callback(interaction, key="welcome_dm",
                                            value="Hi {name}!")

    assert "Platzhalter" in _sent_text(interaction)
    assert _last_send(interaction).kwargs.get("ephemeral") is True
    assert await repo.get_content_text("welcome_dm") is None
    # live resolver still shows the default, untouched
    from n3x_bot.content import CONTENT_DEFAULTS
    assert bot.content_texts.get("welcome_dm") == CONTENT_DEFAULTS["welcome_dm"]

    await _cleanup(repo)


async def test_content_set_welcome_dm_valid_placeholder_stored():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    interaction = _fake_interaction()

    await _content_sub(bot, "set").callback(interaction, key="welcome_dm",
                                            value="Servus {mention}!")

    assert await repo.get_content_text("welcome_dm") == "Servus {mention}!"
    assert bot.content_texts.get("welcome_dm") == "Servus {mention}!"

    await _cleanup(repo)


async def test_content_set_record_template_extra_literal_text_stored():
    # All required placeholders present plus extra literal text is valid.
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    interaction = _fake_interaction()

    await _content_sub(bot, "set").callback(
        interaction, key="record_lucky", value="{user} {name} {cost} extra")

    assert await repo.get_content_text("record_lucky") == "{user} {name} {cost} extra"
    assert bot.content_texts.get("record_lucky") == "{user} {name} {cost} extra"

    await _cleanup(repo)


async def test_content_set_record_template_bad_placeholder_rejected_no_write():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    interaction = _fake_interaction()

    await _content_sub(bot, "set").callback(
        interaction, key="record_unlucky", value="Pech {user} {bogus} {cost}")

    assert "Platzhalter" in _sent_text(interaction)
    assert await repo.get_content_text("record_unlucky") is None

    await _cleanup(repo)


async def test_content_set_non_template_key_not_validated():
    # kodex_text has no `.format` read-site, so arbitrary text (no validation).
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    interaction = _fake_interaction()

    await _content_sub(bot, "set").callback(
        interaction, key="kodex_text", value="Ganz normaler Kodex ohne Platzhalter")

    assert await repo.get_content_text("kodex_text") == (
        "Ganz normaler Kodex ohne Platzhalter")

    await _cleanup(repo)


async def test_content_set_non_admin_refused_no_write():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    interaction = _fake_interaction(user=_non_admin())

    await _content_sub(bot, "set").callback(interaction, key="kodex_text",
                                            value="hax")

    assert "Berechtigung" in _sent_text(interaction)
    assert _last_send(interaction).kwargs.get("ephemeral") is True
    assert await repo.get_content_text("kodex_text") is None

    await _cleanup(repo)


async def test_content_reset_reverts_to_default():
    from n3x_bot.content import CONTENT_DEFAULTS
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    await repo.set_content_text("kodex_text", "Override Kodex")
    await bot.content_texts.refresh(repo)
    assert bot.content_texts.get("kodex_text") == "Override Kodex"  # override active

    interaction = _fake_interaction()
    await _content_sub(bot, "reset").callback(interaction, key="kodex_text")

    assert await repo.get_content_text("kodex_text") is None
    assert bot.content_texts.get("kodex_text") == CONTENT_DEFAULTS["kodex_text"]
    assert _last_send(interaction).kwargs.get("ephemeral") is True

    await _cleanup(repo)


async def test_content_reset_non_admin_refused():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    await repo.set_content_text("kodex_text", "Override Kodex")
    await bot.content_texts.refresh(repo)
    interaction = _fake_interaction(user=_non_admin())

    await _content_sub(bot, "reset").callback(interaction, key="kodex_text")

    assert "Berechtigung" in _sent_text(interaction)
    # a non-admin reset must not delete the override
    assert await repo.get_content_text("kodex_text") == "Override Kodex"

    await _cleanup(repo)


async def test_content_show_reports_effective_value_for_key():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    await repo.set_content_text("welcome_dm", "Servus {mention}")
    await bot.content_texts.refresh(repo)
    interaction = _fake_interaction()

    await _content_sub(bot, "show").callback(interaction, key="welcome_dm")

    text = _sent_text(interaction)
    assert "Servus {mention}" in text
    assert _last_send(interaction).kwargs.get("ephemeral") is True

    await _cleanup(repo)


async def test_content_show_non_admin_refused():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    interaction = _fake_interaction(user=_non_admin())

    await _content_sub(bot, "show").callback(interaction, key="kodex_text")

    assert "Berechtigung" in _sent_text(interaction)

    await _cleanup(repo)


async def test_content_list_includes_all_keys_and_overridden_marker():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    await repo.set_content_text("kodex_text", "Override")
    await bot.content_texts.refresh(repo)
    interaction = _fake_interaction()

    await _content_sub(bot, "list").callback(interaction)

    text = _sent_text(interaction)
    for key in ("kodex_text", "reminder_aceball", "reminder_invasion",
                "record_lucky", "record_unlucky", "welcome_dm"):
        assert key in text, key
    assert _last_send(interaction).kwargs.get("ephemeral") is True

    await _cleanup(repo)


async def test_content_list_non_admin_refused():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    interaction = _fake_interaction(user=_non_admin())

    await _content_sub(bot, "list").callback(interaction)

    assert "Berechtigung" in _sent_text(interaction)

    await _cleanup(repo)


# ══════════════════════════════════════════════════════════════════════════
# 5. Read-site routing (integration): an override actually changes output
# ══════════════════════════════════════════════════════════════════════════

def _dm_member(*, member_id=111, msg_id=9001):
    msg = SimpleNamespace(id=msg_id, add_reaction=AsyncMock())
    member = SimpleNamespace(id=member_id, bot=False,
                             display_name=f"U{member_id}",
                             mention=f"<@{member_id}>",
                             send=AsyncMock(return_value=msg))
    member._sent_msg = msg
    return member


async def test_kodex_dm_uses_content_text_override():
    # Routing proof: with a `kodex_text` override refreshed into the live
    # resolver, send_kodex_dm DMs the OVERRIDE, not the KODEX_TEXT default.
    from n3x_bot import kodex
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    await repo.set_content_text("kodex_text", "ÜBERSCHRIEBENER KODEX")
    await bot.content_texts.refresh(repo)
    member = _dm_member(member_id=111, msg_id=9001)

    await kodex.send_kodex_dm(bot, repo, member)

    member.send.assert_awaited_once_with("ÜBERSCHRIEBENER KODEX")

    await _cleanup(repo)


async def test_kodex_dm_no_override_sends_default_constant():
    # Behaviour-preserving counterpart: no override -> the KODEX_TEXT default.
    from n3x_bot import kodex
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    member = _dm_member(member_id=222, msg_id=9002)

    await kodex.send_kodex_dm(bot, repo, member)

    member.send.assert_awaited_once_with(kodex.KODEX_TEXT)

    await _cleanup(repo)


def _welcome_channel():
    sent: list = []

    def _send(*args, **kwargs):
        msg = MagicMock()
        msg.content = args[0] if args else kwargs.get("content")
        msg.file = kwargs.get("file")
        sent.append(msg)
        return msg

    channel = MagicMock()
    channel.send = AsyncMock(side_effect=_send)
    return channel, sent


async def test_welcome_card_uses_content_text_override():
    from n3x_bot import welcome
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    channel, sent = _welcome_channel()
    bot.get_channel = MagicMock(return_value=channel)
    await repo.set_content_text("welcome_dm", "Servus {mention} 👋")
    await bot.content_texts.refresh(repo)

    await welcome.send_welcome_card(
        bot, settings, SimpleNamespace(id=555, display_name="Newbie", bot=False,
                                       mention="<@555>"))

    assert sent[0].content == "Servus <@555> 👋"

    await _cleanup(repo)


async def test_welcome_card_no_override_uses_default_template():
    from n3x_bot import welcome
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    channel, sent = _welcome_channel()
    bot.get_channel = MagicMock(return_value=channel)

    await welcome.send_welcome_card(
        bot, settings, SimpleNamespace(id=777, display_name="Newbie", bot=False,
                                       mention="<@777>"))

    assert sent[0].content == "Willkommen <@777>!"

    await _cleanup(repo)


async def test_announce_records_uses_content_text_override():
    # _announce_records renders record_lucky via the resolver; an override
    # changes the announced copy while {user}/{name}/{cost} still substitute.
    from n3x_bot.bot import _announce_records
    repo = await _flatfile_repo()
    settings = _settings(milestone_channel_id=888)
    bot = build_bot(settings, repo)
    channel = MagicMock()
    channel.send = AsyncMock()
    bot.get_channel = MagicMock(return_value=channel)
    await repo.set_content_text("record_lucky",
                                "REKORD {user} {name} {cost}")
    await bot.content_texts.refresh(repo)
    record = {"min_cost": 50, "min_user": 3, "max_cost": 500, "max_user": 2}

    await _announce_records(bot, settings, "d", {"min"}, record)

    channel.send.assert_awaited_once()
    msg = channel.send.await_args.args[0]
    assert msg.startswith("REKORD 3 ")
    assert "3" in msg  # {user}
    assert "50" in msg  # {cost}

    await _cleanup(repo)
