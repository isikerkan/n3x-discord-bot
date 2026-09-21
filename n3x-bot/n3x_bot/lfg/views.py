"""Persistente LFG-Views.

Muster wie `OverviewView`/`CommandListView`: `timeout=None`, feste
`custom_id`s, in `on_ready` per `bot.add_view(...)` wieder angehängt.

Der Unterschied zu diesen beiden: sie tragen je eine Singleton-Nachricht und
halten ihren Zustand am Bot. Es gibt aber beliebig viele LFG-Nachrichten
gleichzeitig, also hängt hier KEIN Zustand am View — jede Interaktion löst ihre
LFG über `repo.get_lfg_by_message(interaction.message.id)` auf. Denselben Weg
gehen schon `kodex_messages` und `gate_pending`.

Zu den Optionen des Select: Discord speichert die gerenderten Optionen an der
Nachricht. Der wieder angehängte View braucht nur passende `custom_id`s zum
Routen; die tatsächliche Auswahl kommt aus der Interaktion und wird gegen die
gespeicherten `start_times` geprüft.
"""
import discord

from zoneinfo import ZoneInfo

from n3x_bot.activity import now_local
from n3x_bot.lfg import embeds as lfg_embeds
from n3x_bot.lfg import service
from n3x_bot.lfg.models import MAX_START_TIMES, LfgStatus

AVAIL_SELECT_ID = "n3x:lfg:avail"
JOIN_BUTTON_ID = "n3x:lfg:join"
LEAVE_BUTTON_ID = "n3x:lfg:leave"

_GONE = "❌ Diese LFG gibt es nicht mehr."


async def render(repo, settings, lfg: dict):
    """`(embed, view)` für den aktuellen Zustand einer LFG."""
    if lfg["status"] in LfgStatus.SETTLED:
        participants = await repo.get_lfg_participants(lfg["id"])
        return (lfg_embeds.build_confirmed_embed(lfg, participants),
                LfgConfirmedView(repo, settings, lfg=lfg,
                                 full=len(participants) >= lfg["max_players"]))
    availability = await repo.get_lfg_availability(lfg["id"])
    return (lfg_embeds.build_open_embed(lfg, service.counts(lfg, availability)),
            LfgOpenView(repo, settings, lfg=lfg))


async def refresh(bot, repo, settings, lfg: dict) -> None:
    """Die LFG-Nachricht an ihren Zustand anpassen. Best-effort."""
    if not lfg.get("message_id"):
        return
    channel = bot.get_channel(lfg["channel_id"])
    if channel is None:
        return
    try:
        message = await channel.fetch_message(lfg["message_id"])
        embed, view = await render(repo, settings, lfg)
        await message.edit(embed=embed, view=view)
    except Exception:
        pass  # Nachricht gelöscht / keine Rechte


class LfgAvailabilitySelect(discord.ui.Select):
    """Mehrfachauswahl der Startzeiten.

    `min_values=0` ist die Variante „Verfügbarkeit entfernen": abschicken ohne
    Auswahl löscht die eigenen Einträge.
    """

    def __init__(self, repo, settings, *, lfg: dict | None = None):
        self.repo = repo
        self.settings = settings
        times = sorted((lfg or {}).get("start_times") or [])
        # Discord verlangt mindestens eine Option. Die registrierte
        # Router-Instanz (ohne lfg) bekommt daher einen Platzhalter; gerendert
        # wird sie nie.
        options = [
            discord.SelectOption(label=f"{t} Uhr", value=t,
                                 emoji=lfg_embeds.clock_for(t))
            for t in times[:MAX_START_TIMES]
        ] or [discord.SelectOption(label="—", value="—")]
        super().__init__(custom_id=AVAIL_SELECT_ID,
                         placeholder="Wann kannst du? (mehrere möglich)",
                         min_values=0, max_values=len(options),
                         options=options)

    async def callback(self, interaction):
        lfg = await self.repo.get_lfg_by_message(interaction.message.id)
        if lfg is None:
            await interaction.response.send_message(_GONE, ephemeral=True)
            return
        if lfg["status"] != LfgStatus.OPEN:
            await interaction.response.send_message(
                "❌ Der Termin steht schon fest — nutze `➕ Beitreten`.",
                ephemeral=True)
            return
        result = await service.set_availability(
            self.repo, lfg["id"], interaction.user.id, self.values,
            now=now_local(self.settings),
            tz=ZoneInfo(self.settings.timezone))
        current = result["lfg"] or lfg
        embed, view = await render(self.repo, self.settings, current)
        await interaction.response.edit_message(embed=embed, view=view)


class LfgOpenView(discord.ui.View):
    """Offene LFG: nur die Zeit-Auswahl."""

    def __init__(self, repo, settings, *, lfg: dict | None = None):
        super().__init__(timeout=None)
        self.repo = repo
        self.settings = settings
        self.add_item(LfgAvailabilitySelect(repo, settings, lfg=lfg))


class LfgConfirmedView(discord.ui.View):
    """Bestätigte LFG: Beitreten / Verlassen.

    Bei `full=True` wird der Beitreten-Button deaktiviert gerendert. Der
    Kapazitäts-Check im Repo bleibt trotzdem bestehen — ein deaktivierter
    Button ist reine Anzeige, verlassen darf man sich darauf nicht.
    """

    def __init__(self, repo, settings, *, lfg: dict | None = None,
                 full: bool = False):
        super().__init__(timeout=None)
        self.repo = repo
        self.settings = settings
        self.join_button.disabled = full

    @discord.ui.button(label="➕ Beitreten", style=discord.ButtonStyle.success,
                       custom_id=JOIN_BUTTON_ID)
    async def join_button(self, interaction, button):
        lfg = await self.repo.get_lfg_by_message(interaction.message.id)
        if lfg is None:
            await interaction.response.send_message(_GONE, ephemeral=True)
            return
        result = await service.join(
            self.repo, lfg["id"], interaction.user.id,
            now=now_local(self.settings))
        if result != "added":
            await interaction.response.send_message(
                {"already": "❌ Du bist schon dabei.",
                 "full": "❌ Die Gruppe ist voll.",
                 "not_confirmed": "❌ Es steht noch kein Termin fest.",
                 "missing": _GONE}.get(result, _GONE), ephemeral=True)
            return
        embed, view = await render(self.repo, self.settings,
                                   await self.repo.get_lfg(lfg["id"]))
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="❌ Verlassen", style=discord.ButtonStyle.secondary,
                       custom_id=LEAVE_BUTTON_ID)
    async def leave_button(self, interaction, button):
        lfg = await self.repo.get_lfg_by_message(interaction.message.id)
        if lfg is None:
            await interaction.response.send_message(_GONE, ephemeral=True)
            return
        result = await service.leave(self.repo, lfg["id"], interaction.user.id)
        if result != "removed":
            await interaction.response.send_message(
                {"not_member": "❌ Du bist nicht dabei.",
                 "missing": _GONE}.get(result, _GONE), ephemeral=True)
            return
        embed, view = await render(self.repo, self.settings,
                                   await self.repo.get_lfg(lfg["id"]))
        await interaction.response.edit_message(embed=embed, view=view)
