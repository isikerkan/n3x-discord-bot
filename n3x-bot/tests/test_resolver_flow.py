"""RED-phase specs for de-hardcode Phase 2b, Part B: flip the live achievement
call sites from the hard-coded module ``ACHIEVEMENTS`` list to the DB-backed
resolver ``bot.achievement_defs``.

Each pin proves the resolver actually FLOWS THROUGH a live path by seeding the
``achievement_defs`` table with a def that CANNOT exist under the module
defaults — either a lower threshold than any code default (so a low-value user
unlocks it) or an id absent from ``ACHIEVEMENTS`` entirely. If the call site
still reads the module list the unlock never happens and the test REDs.

Because the resolver is total-replacement, seeding a single custom def makes the
resolver hold EXACTLY that def, which keeps these end-to-end proofs sharp.

New behaviour is exercised through directly-callable handlers that already carry
``bot`` in scope (so the coder can thread ``bot.achievement_defs.all()`` /
``.total`` without a signature change to the pure helpers):

  * gate success     -> ``handle_gate_input_message``
  * activity/reaction-> ``handle_activity_reaction``
  * additive sync    -> ``sync_all_achievements(repo, defs=...)`` + ``/sync_achievements``
  * ``/erfolge``     -> resolver total in the embed
  * ``/overview``    -> resolver total in the embed
  * voice-tier roles -> ``apply_voice_roles`` reads resolver defs
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from n3x_bot.achievements import ACHIEVEMENTS, Achievement
from n3x_bot.bot import build_bot
from n3x_bot.config import Settings
from n3x_bot.seed import seed_defaults
from n3x_bot.storage.json_repo import JsonRepository

BASE_SETTINGS_KWARGS = dict(
    discord_token="tok",
    target_role_id=1,
    welcome_channel_id=2,
    reminder_channel_id=999,
    julez_id=424242,
    admin_role_id=42,
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


async def _seed_code_defaults(repo) -> None:
    for a in ACHIEVEMENTS:
        await repo.set_achievement_def(
            a.id, category=a.category, metric=a.metric, threshold=a.threshold,
            title=a.title, secret=a.secret, color=getattr(a, "color", None))


def _member(*, member_id=5, role_ids=()):
    return SimpleNamespace(id=member_id,
                           roles=[SimpleNamespace(id=r) for r in role_ids],
                           bot=False)


def _embed_text(embed) -> str:
    parts = [str(getattr(embed, "title", "") or ""),
             str(getattr(embed, "description", "") or "")]
    for f in getattr(embed, "fields", None) or []:
        parts.append(str(getattr(f, "name", "") or ""))
        parts.append(str(getattr(f, "value", "") or ""))
    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════════
# 1. gate success path -> handle_gate_input_message reads the resolver
# ══════════════════════════════════════════════════════════════════════════

async def test_gate_success_path_unlocks_resolver_only_def():
    from n3x_bot.bot import handle_gate_input_message
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    bot.get_channel = MagicMock(return_value=None)

    # a_1 (gate_a threshold 1) does NOT exist in the module defaults (a_5 is the
    # lowest gate_a tier), so a single "a" gate can only unlock it via the
    # resolver. Total-replacement: the resolver now holds exactly this def.
    await repo.set_achievement_def("a_1", category="gate", metric="gate_a",
                                   threshold=1, title="Alpha Rookie",
                                   secret=False)
    await bot.achievement_defs.refresh(repo)

    message = MagicMock()
    message.content = "a 46892"
    message.author = SimpleNamespace(id=7, name="Erkan")
    message.add_reaction = AsyncMock()

    await handle_gate_input_message(bot, repo, settings, message)

    assert await repo.has_achievement(7, "a_1") is True

    await _cleanup(repo)


# ══════════════════════════════════════════════════════════════════════════
# 2. activity/reaction path -> handle_activity_reaction reads the resolver
# ══════════════════════════════════════════════════════════════════════════

async def test_reaction_activity_path_unlocks_resolver_only_def():
    from n3x_bot.bot import handle_activity_reaction
    repo = await _flatfile_repo()
    settings = _settings(gate_input_channel_id=777, gate_stats_channel_id=888)
    bot = build_bot(settings, repo)

    # reaction_1 (reactions threshold 1) is below the module minimum
    # (reaction_100), so a single reaction can only unlock it via the resolver.
    await repo.set_achievement_def("reaction_1", category="reaction",
                                   metric="reactions", threshold=1,
                                   title="Erste Reaktion", secret=True)
    await bot.achievement_defs.refresh(repo)

    payload = SimpleNamespace(user_id=7, channel_id=555,
                              member=_member(member_id=7))
    await handle_activity_reaction(bot, repo, settings, payload)

    assert await repo.has_achievement(7, "reaction_1") is True

    await _cleanup(repo)


# ══════════════════════════════════════════════════════════════════════════
# 3. additive sync -> defs param + /sync_achievements uses the resolver
# ══════════════════════════════════════════════════════════════════════════

async def test_sync_all_achievements_accepts_and_honours_defs():
    from n3x_bot.achievements import sync_all_achievements
    repo = await _flatfile_repo()
    await repo.add_activity(7, "voice_seconds", 5000)

    custom = [Achievement(id="voice_1", category="voice", metric="voice_seconds",
                          threshold=1, title="Solo", secret=False)]
    summary = await sync_all_achievements(repo, defs=custom)

    assert await repo.has_achievement(7, "voice_1") is True
    assert summary["achievements_added"] >= 1

    await _cleanup(repo)


async def test_sync_achievements_command_uses_resolver_defs():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    await repo.add_activity(7, "voice_seconds", 5000)

    # resolver holds exactly one custom sub-threshold def; the /sync_achievements
    # admin command must feed those resolver defs to the additive sync.
    await repo.set_achievement_def("voice_1", category="voice",
                                   metric="voice_seconds", threshold=1,
                                   title="Solo", secret=False)
    await bot.achievement_defs.refresh(repo)

    cmd = bot.tree.get_command("sync_achievements")
    interaction = MagicMock()
    interaction.user = _member(member_id=5, role_ids=(42,))  # admin
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    await cmd.callback(interaction)

    assert await repo.has_achievement(7, "voice_1") is True

    await _cleanup(repo)


# ══════════════════════════════════════════════════════════════════════════
# 4. /erfolge -> resolver total in the embed (84 with an extra def)
# ══════════════════════════════════════════════════════════════════════════

async def test_erfolge_embed_uses_resolver_total():
    repo = await _flatfile_repo()
    settings = _settings()
    bot = build_bot(settings, repo)
    await _seed_code_defaults(repo)
    await repo.set_achievement_def("voice_9999999", category="voice",
                                   metric="voice_seconds", threshold=9999999,
                                   title="Test Legende", secret=False)
    await bot.achievement_defs.refresh(repo)
    assert bot.achievement_defs.total == 84

    await repo.unlock_achievement(7, "voice_3600")

    cmd = bot.tree.get_command("erfolge")
    interaction = MagicMock()
    interaction.user = SimpleNamespace(id=7, display_name="Erkan", mention="<@7>",
                                       send=AsyncMock())
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    await cmd.callback(interaction)

    # /erfolge now DMs the embed; the interaction reply is just the ephemeral ack.
    embed = interaction.user.send.await_args.kwargs.get("embed")
    assert embed is not None
    assert "/84" in _embed_text(embed)

    await _cleanup(repo)


# ══════════════════════════════════════════════════════════════════════════
# 5. /overview -> resolver total in the embed (84 with an extra def)
# ══════════════════════════════════════════════════════════════════════════

def _overview_channel():
    counter = {"n": 500}
    sent = []

    def _send(*args, **kwargs):
        counter["n"] += 1
        msg = MagicMock()
        msg.id = counter["n"]
        msg.embed = kwargs.get("embed")
        msg.add_reaction = AsyncMock()
        msg.edit = AsyncMock()
        msg.remove_reaction = AsyncMock()
        sent.append(msg)
        return msg

    channel = MagicMock()
    channel.send = AsyncMock(side_effect=_send)
    channel.fetch_message = AsyncMock(side_effect=RuntimeError("no message"))
    return channel, sent


async def test_overview_embed_uses_resolver_total():
    repo = await _flatfile_repo()
    settings = _settings(overview_channel_id=4242)
    bot = build_bot(settings, repo)
    channel, sent = _overview_channel()
    bot.get_channel = MagicMock(return_value=channel)
    await bot.runtime_config.refresh(repo)

    await _seed_code_defaults(repo)
    await repo.set_achievement_def("voice_9999999", category="voice",
                                   metric="voice_seconds", threshold=9999999,
                                   title="Test Legende", secret=False)
    await bot.achievement_defs.refresh(repo)
    assert bot.achievement_defs.total == 84

    await repo.unlock_achievement(10, "a_5")

    cmd = bot.tree.get_command("overview")
    interaction = MagicMock()
    interaction.user = SimpleNamespace(id=1, display_name="Anyone")
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    await cmd.callback(interaction)

    channel.send.assert_awaited_once()
    embed = channel.send.await_args.kwargs.get("embed")
    assert embed is not None
    assert "/84" in _embed_text(embed)

    await _cleanup(repo)


# ══════════════════════════════════════════════════════════════════════════
# 6. voice-tier roles -> apply_voice_roles resolves thresholds via the resolver
# ══════════════════════════════════════════════════════════════════════════

async def test_apply_voice_roles_resolves_custom_def_via_resolver():
    from n3x_bot.activity import apply_voice_roles
    repo = await _flatfile_repo()
    # role map keyed by a custom id that is NOT in the module ACHIEVEMENTS: the
    # threshold lookup inside voice_role_transition would raise StopIteration if
    # it walked the module list, so a clean grant proves it walked the resolver.
    settings = _settings(voice_achievement_roles="voice_custom:555")
    bot = build_bot(settings, repo)
    await bot.runtime_config.refresh(repo)

    await repo.set_achievement_def("voice_custom", category="voice",
                                   metric="voice_seconds", threshold=7,
                                   title="Custom Tier", secret=False)
    await bot.achievement_defs.refresh(repo)

    grant_role = SimpleNamespace(id=555)
    member = SimpleNamespace(
        id=7,
        roles=[],
        guild=SimpleNamespace(get_role=lambda rid: grant_role if rid == 555 else None),
        add_roles=AsyncMock(),
        remove_roles=AsyncMock(),
    )
    newly = [Achievement(id="voice_custom", category="voice",
                         metric="voice_seconds", threshold=7,
                         title="Custom Tier", secret=False)]

    # must not raise (module-list lookup would StopIteration) and must grant 555.
    await apply_voice_roles(bot, bot.runtime_config, member, newly)

    member.add_roles.assert_awaited_once_with(grant_role)

    await _cleanup(repo)
