"""`/lfg`, die permanente Anleitung und der Cleanup-Loop.

Registrierung wie jedes andere Feature-Modul: `register_lfg_commands(bot, repo,
settings)` aus `build_bot`, plus drei Aufrufe in `on_ready`.
"""
import logging
from zoneinfo import ZoneInfo

from discord import app_commands
from discord.ext import tasks

from n3x_bot.activity import now_local, today_local
from n3x_bot.config import Settings
from n3x_bot.lfg import embeds as lfg_embeds
from n3x_bot.lfg import models, service, views
from n3x_bot.lfg.models import LfgValidationError
from n3x_bot.storage.base import StatsRepository

log = logging.getLogger("N3X-Bot")

# Die Anleitung liegt wie jede Dauer-Nachricht des Bots in `channel_messages`,
# damit sie über Neustarts hinweg in place editiert und nicht neu gepostet wird.
LFG_HELP_KEY = "lfg_help"


def _lfg_channel_id(bot) -> int:
    return bot.runtime_config.lfg_channel_id


async def update_lfg_help(bot, repo: StatsRepository,
                          settings: Settings) -> None:
    """Die permanente Anleitung anlegen bzw. in place aktualisieren.

    Spiegelt `update_gate_input_help`: die Message-ID steht unter
    `LFG_HELP_KEY` in `channel_messages`, also entsteht bei jedem Neustart
    KEINE zweite Anleitung. Best-effort; wirft nie.
    """
    channel_id = _lfg_channel_id(bot)
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if channel is None:
        return
    embed = lfg_embeds.build_help_embed()
    stored = await repo.get_channel_message(LFG_HELP_KEY)
    if stored is not None:
        try:
            message = await channel.fetch_message(stored[0])
            await message.edit(content=None, embed=embed)
            return
        except Exception:
            pass  # gelöscht → unten neu posten
    try:
        message = await channel.send(embed=embed)
        await repo.set_channel_message(LFG_HELP_KEY, message.id, channel.id)
    except Exception:
        log.exception("lfg help post failed")


async def restore_lfg_views(bot, repo: StatsRepository,
                            settings: Settings) -> None:
    """Nach einem Neustart die Views wieder anhängen.

    Zwei Router-Instanzen genügen: Discord ordnet Interaktionen über die
    `custom_id` zu, nicht über die Nachricht. Zusätzlich werden die noch
    aktiven LFG-Nachrichten einmal neu gerendert, damit Zähler und
    Button-Zustand zum DB-Stand passen (eine Bestätigung, die kurz vor dem
    Neustart fiel, wird so sichtbar).
    """
    bot.add_view(views.LfgOpenView(repo, settings))
    bot.add_view(views.LfgConfirmedView(repo, settings))
    for lfg in await repo.all_active_lfgs():
        await views.refresh(bot, repo, settings, lfg)


async def run_lfg_cleanup(bot, repo: StatsRepository, settings: Settings,
                          now) -> int:
    """Fällige LFGs abräumen: Status EXPIRED, Nachricht löschen.

    Die Deadline steht in `lfg_posts.cleanup_at`, nicht in einem Timer — der
    Cleanup holt also nach einem Neustart alles nach, was zwischenzeitlich
    fällig wurde. Der Datensatz bleibt als Historie erhalten; entfernt wird
    ausschließlich die Discord-Nachricht.
    """
    cleaned = 0
    for lfg in await service.due_for_cleanup(repo, now):
        if lfg.get("message_id"):
            channel = bot.get_channel(lfg["channel_id"])
            if channel is not None:
                try:
                    message = await channel.fetch_message(lfg["message_id"])
                    await message.delete()
                except Exception:
                    pass  # schon weg / keine Rechte
        await service.mark_expired(repo, lfg["id"])
        cleaned += 1
    return cleaned


def start_lfg_cleanup_loop(bot, repo: StatsRepository,
                           settings: Settings) -> tasks.Loop:
    """Minütlicher Cleanup, abgesichert gegen Doppelstart (wie B4 bei den
    Base-Timern). Eine Minute Granularität reicht für „Startzeit + 1 h"."""
    existing = getattr(bot, "_lfg_cleanup_loop", None)
    if isinstance(existing, tasks.Loop) and existing.is_running():
        return existing

    @tasks.loop(minutes=1)
    async def _lfg_cleanup_loop():
        try:
            await run_lfg_cleanup(bot, repo, settings, now_local(settings))
        except Exception:
            log.exception("lfg cleanup failed")

    bot._lfg_cleanup_loop = _lfg_cleanup_loop
    if not _lfg_cleanup_loop.is_running():
        _lfg_cleanup_loop.start()
    return _lfg_cleanup_loop


def register_lfg_commands(bot, repo: StatsRepository,
                          settings: Settings) -> None:
    if bot.tree.get_command("lfg") is not None:
        return

    @bot.tree.command(name="lfg", description="Erstellt eine LFG-Gruppensuche.")
    @app_commands.describe(titel="Wonach suchst du? z. B. Satura Gruppen Gate",
                           spieler="Spieleranzahl als Bereich, z. B. 4-8",
                           datum="Datum, z. B. 21.09.2026",
                           startzeiten="Startzeiten, z. B. 19:00 / 20:00 / 21:00")
    async def lfg_cmd(interaction, titel: str, spieler: str, datum: str,
                      startzeiten: str):
        channel_id = _lfg_channel_id(bot)
        if not channel_id:
            await interaction.response.send_message(
                "❌ Es ist kein LFG-Channel konfiguriert.", ephemeral=True)
            return
        # Die LFG-Verwaltung bleibt auf den einen Channel beschränkt; überall
        # sonst gibt es den ephemeren Hinweis (wie die übrigen Refusals).
        if interaction.channel_id != channel_id:
            await interaction.response.send_message(
                f"❌ `/lfg` funktioniert nur in <#{channel_id}>.",
                ephemeral=True)
            return
        try:
            draft = models.build_draft(title=titel, players=spieler,
                                       event_date=datum,
                                       start_times=startzeiten,
                                       today=today_local(settings))
        except LfgValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        lfg_id = await service.create(
            repo, draft, creator_id=interaction.user.id,
            channel_id=channel_id, now=now_local(settings),
            tz=ZoneInfo(settings.timezone))
        lfg = await repo.get_lfg(lfg_id)
        channel = bot.get_channel(channel_id)
        if channel is None:
            await interaction.followup.send(
                "❌ Der LFG-Channel ist nicht erreichbar.", ephemeral=True)
            return
        embed, view = await views.render(repo, settings, lfg)
        message = await channel.send(embed=embed, view=view)
        await repo.set_lfg_message(lfg_id, message.id, channel.id)
        # Kein Erfolgs-Text: die LFG-Nachricht IST die Rückmeldung (wie bei
        # /base). Den deferred Platzhalter wieder entfernen.
        try:
            await interaction.delete_original_response()
        except Exception:
            pass
