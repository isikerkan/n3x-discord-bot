"""`/backfill_history` — reconstruct message + reaction counters from Discord
history, then recompute achievements.

The live trackers only count events they saw while connected, so pre-tracking
history is uncounted (nobody reaches the msg_* / reaction_* secret tiers). This
walks every readable text channel's full history, tallies per-user **messages
sent** and **reactions given** (matching the live semantics — one per
message/emoji/user), writes the ABSOLUTE totals via ``set_activity`` (idempotent
across re-runs), and recomputes achievements so the secret tiers unlock.

Heavy + rate-limited (fetches reactors on every reacted message); admin-only,
run on demand.
"""
import logging

from n3x_bot.achievements import sync_all_achievements
from n3x_bot.admin import app_is_admin
from n3x_bot.config import Settings
from n3x_bot.storage.base import StatsRepository

log = logging.getLogger(__name__)


async def scan_history(guild) -> tuple[dict[int, int], dict[int, int]]:
    """Tally (messages_sent, reactions_given) per non-bot user across every
    readable text channel. Channels that raise (no access) are skipped."""
    messages: dict[int, int] = {}
    reactions: dict[int, int] = {}
    for channel in getattr(guild, "text_channels", []):
        try:
            async for msg in channel.history(limit=None):
                author = msg.author
                if not getattr(author, "bot", False):
                    messages[author.id] = messages.get(author.id, 0) + 1
                for reaction in getattr(msg, "reactions", []) or []:
                    async for user in reaction.users():
                        if getattr(user, "bot", False):
                            continue
                        reactions[user.id] = reactions.get(user.id, 0) + 1
        except Exception:
            log.warning("history scan skipped a channel (no access?)", exc_info=True)
            continue
    return messages, reactions


async def apply_history_counts(repo: StatsRepository, messages: dict[int, int],
                               reactions: dict[int, int]) -> None:
    """Write the scanned ABSOLUTE totals into activity_counters."""
    for uid, count in messages.items():
        await repo.set_activity(uid, "messages", count)
    for uid, count in reactions.items():
        await repo.set_activity(uid, "reactions", count)


async def run_history_backfill(bot, repo: StatsRepository, guild) -> dict:
    """Full backfill: scan → set counters → recompute achievements. Returns a
    summary dict."""
    messages, reactions = await scan_history(guild)
    await apply_history_counts(repo, messages, reactions)
    recompute = await sync_all_achievements(repo, defs=bot.achievement_defs.all())
    return {
        "users_messages": len(messages),
        "users_reactions": len(reactions),
        "total_messages": sum(messages.values()),
        "total_reactions": sum(reactions.values()),
        "achievements_added": recompute["achievements_added"],
    }


def register_history_backfill_command(bot, repo: StatsRepository,
                                      settings: Settings) -> None:
    if bot.tree.get_command("backfill_history") is not None:
        return

    @bot.tree.command(
        name="backfill_history",
        description="Zählt Nachrichten & Reaktionen aus dem Verlauf nach (Admin).")
    async def backfill_history(interaction):
        if not app_is_admin(interaction, settings):
            await interaction.response.send_message(
                "❌ Keine Berechtigung.", ephemeral=True)
            return
        # Long-running + rate-limited; ack immediately, work in the handler.
        await interaction.response.defer(ephemeral=True)
        try:
            summary = await run_history_backfill(bot, repo, interaction.guild)
        except Exception:
            log.exception("history backfill failed")
            await _safe_followup(interaction, "❌ Backfill fehlgeschlagen (siehe Logs).")
            return
        await _safe_followup(
            interaction,
            f"✅ Verlauf nachgezählt.\n"
            f"💬 Nachrichten: {summary['total_messages']} "
            f"({summary['users_messages']} User)\n"
            f"👍 Reaktionen: {summary['total_reactions']} "
            f"({summary['users_reactions']} User)\n"
            f"🏆 Neue Achievements: {summary['achievements_added']}")


async def _safe_followup(interaction, text: str) -> None:
    # A scan can outlive the 15-min interaction token; never let the report
    # failing hide that the DB work already completed.
    try:
        await interaction.followup.send(text, ephemeral=True)
    except Exception:
        log.info("backfill report: %s", text)
