"""RED spec for the self-editing German gate-input Anleitung (help) message.

FEATURE: a bot-maintained help message in the gate-input channel that explains
how to enter gate costs. It must be *self-editing* — persisted via the
``channel_messages`` store under a NEW key ``"gate_input_help"`` and edited in
place across restarts (never re-posted), exactly like the gate-stats embed
(``update_gate_stats_embed`` / ``GATE_STATS_KEY``). It also has to cover the
new Epsilon/Zeta/Kappa gates on top of the old v3 hints.

New symbols under test (none exist yet -> RED):
  * ``n3x_bot.bot.build_gate_input_help``  — PURE, deterministic; returns the
    Anleitung. Pinned as a ``discord.Embed`` in the report, but the content
    assertions render both ``str`` and ``Embed`` (via ``_help_text``) so the
    coder keeps freedom on the container; only the substrings are load-bearing.
  * ``n3x_bot.bot.update_gate_input_help`` — async; mirrors
    ``update_gate_stats_embed`` against ``bot.runtime_config.gate_input_channel_id``.
  * ``n3x_bot.bot.GATE_INPUT_HELP_KEY`` == ``"gate_input_help"``.
  * ``on_ready`` best-effort wiring: posts/edits the help when the input
    channel is configured.

Imports of n3x_bot live INSIDE test bodies so a not-yet-implemented symbol
fails as a runtime missing-symbol (ImportError/AttributeError) at call time,
never as a collection-time ImportError.
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# Pinned in the report. The test file hard-codes the expected value so the
# assertions double as the contract for the key string.
GATE_INPUT_HELP_KEY = "gate_input_help"

BASE_SETTINGS_KWARGS = dict(
    discord_token="tok",
    target_role_id=1,
    welcome_channel_id=2,
    reminder_channel_id=999,
    julez_id=424242,
    _env_file=None,
    _env_prefix="NONEXISTENT_",
)


def _settings(**overrides):
    from n3x_bot.config import Settings
    kwargs = dict(BASE_SETTINGS_KWARGS)
    kwargs.update(overrides)
    return Settings(**kwargs)


async def _flatfile_repo():
    from n3x_bot.storage.json_repo import JsonRepository
    from n3x_bot.seed import seed_defaults
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    repo = JsonRepository(path)
    await repo.connect()
    await seed_defaults(repo)
    return repo


def _fake_channel(send_return_id: int = 42, channel_id: int = 777):
    """A channel whose .send returns a msg with .id and whose .fetch_message
    returns a msg with an awaitable .edit."""
    channel = MagicMock()
    channel.id = channel_id
    channel.send = AsyncMock(return_value=SimpleNamespace(id=send_return_id))
    fetched = MagicMock()
    fetched.edit = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=fetched)
    return channel, fetched


def _help_text(result) -> str:
    """Flatten the Anleitung to a single searchable string.

    Works whether ``build_gate_input_help`` returns a ``str`` or a
    ``discord.Embed`` (title + description + every field name/value), so the
    content assertions pin only the substrings, not the container.
    """
    if isinstance(result, str):
        return result
    parts = [result.title or "", result.description or ""]
    for field in getattr(result, "fields", []):
        parts.append(field.name or "")
        parts.append(field.value or "")
    return "\n".join(parts)


# ── build_gate_input_help: pure content spec ────────────────────────────────

def test_help_has_anleitung_title():
    from n3x_bot.bot import build_gate_input_help
    text = _help_text(build_gate_input_help())
    assert "📝 Anleitung: Gate-Kosten eintragen" in text


def test_help_names_the_instant_abc_gates():
    from n3x_bot.bot import build_gate_input_help
    text = _help_text(build_gate_input_help())
    assert "Alpha" in text


def test_help_documents_delta_laser_gate():
    from n3x_bot.bot import build_gate_input_help
    text = _help_text(build_gate_input_help())
    assert "Delta" in text
    assert "Laser" in text


def test_help_documents_new_epsilon_gate():
    from n3x_bot.bot import build_gate_input_help
    text = _help_text(build_gate_input_help())
    assert "Epsilon" in text
    assert "LF4" in text


def test_help_documents_new_zeta_gate():
    from n3x_bot.bot import build_gate_input_help
    text = _help_text(build_gate_input_help())
    assert "Zeta" in text
    assert "Havoc" in text


def test_help_documents_new_kappa_gate():
    from n3x_bot.bot import build_gate_input_help
    text = _help_text(build_gate_input_help())
    assert "Kappa" in text
    assert "Hercules" in text
    assert "LF4-U" in text


def test_help_drops_no_longer_confirmed_by_check_cross_reactions():
    from n3x_bot.bot import build_gate_input_help
    text = _help_text(build_gate_input_help())
    # The old ✅ (Drop) / ❎ (kein Drop) confirmation reactions are gone: d/e/z/k
    # now confirm by clicking the drop ICON reaction (or ❌ for no drop). The ❎
    # "no drop" reaction in particular must no longer appear.
    assert "❎" not in text


def test_help_no_longer_documents_kappa_buttons():
    from n3x_bot.bot import build_gate_input_help
    text = _help_text(build_gate_input_help())
    # Kappa no longer uses a button panel (KappaConfirmView is removed); it now
    # confirms via drop-icon reactions like the other drop gates.
    assert "Button" not in text


def test_help_keeps_v3_instant_hint():
    from n3x_bot.bot import build_gate_input_help
    text = _help_text(build_gate_input_help())
    # a/b/c are recorded "sofort" (instantly) — a retained v3 hint.
    assert "sofort" in text


def test_help_keeps_v3_duplicate_or_autodelete_hint():
    from n3x_bot.bot import build_gate_input_help
    text = _help_text(build_gate_input_help())
    # v3 retained the duplicate (⏳) note and/or the auto-delete (30s) note.
    assert "Duplikat" in text or "30" in text


def test_help_is_deterministic():
    from n3x_bot.bot import build_gate_input_help
    assert _help_text(build_gate_input_help()) == _help_text(build_gate_input_help())


# ── GATE_INPUT_HELP_KEY contract ────────────────────────────────────────────

def test_gate_input_help_key_value():
    from n3x_bot.bot import GATE_INPUT_HELP_KEY as key
    assert key == GATE_INPUT_HELP_KEY


# ── update_gate_input_help: first post sends AND persists the id ────────────

async def test_first_post_persists_help_message_in_repo():
    from n3x_bot.bot import build_bot, update_gate_input_help
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    channel, _ = _fake_channel(send_return_id=42, channel_id=777)
    bot.get_channel = MagicMock(return_value=channel)

    await update_gate_input_help(bot, repo, settings)

    channel.send.assert_awaited_once()
    assert await repo.get_channel_message(GATE_INPUT_HELP_KEY) == (42, 777)

    await repo.close()


# ── second call in the same run edits the persisted message ─────────────────

async def test_second_call_edits_persisted_help_without_new_send():
    from n3x_bot.bot import build_bot, update_gate_input_help
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    channel, fetched = _fake_channel(send_return_id=42, channel_id=777)
    bot.get_channel = MagicMock(return_value=channel)

    await update_gate_input_help(bot, repo, settings)
    await update_gate_input_help(bot, repo, settings)

    channel.fetch_message.assert_awaited_once_with(42)
    fetched.edit.assert_awaited_once()
    channel.send.assert_awaited_once()  # only the first call posted
    assert await repo.get_channel_message(GATE_INPUT_HELP_KEY) == (42, 777)

    await repo.close()


# ── THE REGRESSION: restart (fresh bot, same repo) edits, not re-posts ──────

async def test_restart_edits_persisted_help_instead_of_reposting():
    from n3x_bot.bot import build_bot, update_gate_input_help
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)

    # A prior run already posted the help and persisted its id.
    await repo.set_channel_message(GATE_INPUT_HELP_KEY, 42, 777)

    # Restart: a brand-new bot with no in-memory message-id cache.
    bot = build_bot(settings, repo)
    channel, fetched = _fake_channel(send_return_id=999, channel_id=777)
    bot.get_channel = MagicMock(return_value=channel)

    await update_gate_input_help(bot, repo, settings)

    channel.fetch_message.assert_awaited_once_with(42)
    fetched.edit.assert_awaited_once()
    channel.send.assert_not_called()  # MUST NOT re-post after restart

    await repo.close()


# ── fetch-fail (stored message deleted): re-post + re-persist the new id ────

async def test_restart_reposts_and_repersists_when_stored_help_gone():
    from n3x_bot.bot import build_bot, update_gate_input_help
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    await repo.set_channel_message(GATE_INPUT_HELP_KEY, 42, 777)

    bot = build_bot(settings, repo)
    channel, _ = _fake_channel(send_return_id=99, channel_id=777)
    channel.fetch_message = AsyncMock(side_effect=RuntimeError("gone"))
    bot.get_channel = MagicMock(return_value=channel)

    await update_gate_input_help(bot, repo, settings)  # must not raise

    channel.send.assert_awaited_once()
    assert await repo.get_channel_message(GATE_INPUT_HELP_KEY) == (99, 777)

    await repo.close()


# ── noop paths persist nothing ──────────────────────────────────────────────

async def test_noop_when_input_channel_unset_persists_nothing():
    from n3x_bot.bot import build_bot, update_gate_input_help
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=0)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock()

    await update_gate_input_help(bot, repo, settings)

    bot.get_channel.assert_not_called()
    assert await repo.get_channel_message(GATE_INPUT_HELP_KEY) is None

    await repo.close()


async def test_noop_when_input_channel_missing_persists_nothing():
    from n3x_bot.bot import build_bot, update_gate_input_help
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)

    await update_gate_input_help(bot, repo, settings)  # must not raise

    assert await repo.get_channel_message(GATE_INPUT_HELP_KEY) is None

    await repo.close()


# ── on_ready wiring: best-effort posts the help when configured ─────────────

async def test_on_ready_posts_gate_input_help_when_channel_configured():
    from n3x_bot.bot import build_bot
    repo = await _flatfile_repo()
    # gate_stats off, gate_input on -> only the help path posts/persists.
    settings = _settings(gate_stats_channel_id=0, gate_input_channel_id=777)
    bot = build_bot(settings, repo)
    channel, _ = _fake_channel(send_return_id=7, channel_id=777)
    bot.get_channel = MagicMock(return_value=channel)
    bot.tree.sync = AsyncMock()

    await bot.on_ready()

    assert await repo.get_channel_message(GATE_INPUT_HELP_KEY) == (7, 777)

    await repo.close()
