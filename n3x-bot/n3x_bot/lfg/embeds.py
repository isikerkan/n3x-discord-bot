"""LFG-Embeds. Reine Funktionen: Zeile rein, Embed raus."""
import discord

from n3x_bot.lfg.models import LfgStatus

# Ziffernblatt-Emoji pro Stunde, damit jede Zeile ein Symbol trägt (wie die
# übrigen Embeds des Bots).
_CLOCKS = ("🕛", "🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚")


def clock_for(start_time: str) -> str:
    try:
        return _CLOCKS[int(start_time.split(":")[0]) % 12]
    except (ValueError, IndexError):
        return "🕐"


def format_date(event_date: str) -> str:
    """`"2026-09-21"` → `"21.09.2026"`."""
    parts = event_date.split("-")
    return f"{parts[2]}.{parts[1]}.{parts[0]}" if len(parts) == 3 else event_date


def build_open_embed(lfg: dict, counts: dict[str, int]) -> discord.Embed:
    """Die noch offene LFG: mögliche Startzeiten mit Verfügbarkeits-Zählern."""
    lines = [
        f"📅 **{format_date(lfg['event_date'])}**",
        f"👥 **{lfg['min_players']}–{lfg['max_players']} Spieler**",
        "",
        "**Mögliche Startzeiten:**",
    ]
    for start_time in sorted(lfg["start_times"]):
        count = counts.get(start_time, 0)
        lines.append(f"{clock_for(start_time)} {start_time} — "
                     f"{count}/{lfg['max_players']}")
    lines += ["",
              f"Wähle unten die Zeiten, zu denen du kannst. Sobald eine Zeit "
              f"**{lfg['min_players']}** Spieler erreicht, wird sie "
              f"automatisch zum Termin."]
    embed = discord.Embed(title=f"🔎 LFG – {lfg['title']}",
                          description="\n".join(lines),
                          color=discord.Color.blurple())
    embed.set_footer(text=f"LFG #{lfg['id']} · erstellt von einem Mitglied")
    return embed


def build_confirmed_embed(lfg: dict, participants: list[int]) -> discord.Embed:
    """Die bestätigte LFG: fester Termin plus offene Teilnehmerliste."""
    full = len(participants) >= lfg["max_players"]
    lines = [
        f"📅 **{format_date(lfg['event_date'])}**",
        f"{clock_for(lfg['confirmed_time'] or '')} "
        f"**{lfg['confirmed_time']} Uhr**",
        "",
        f"👥 **Teilnehmer: {len(participants)}/{lfg['max_players']}**",
    ]
    if participants:
        lines += [f"• <@{uid}>" for uid in participants]
    else:
        lines.append("_Noch niemand dabei._")
    lines.append("")
    if full:
        lines.append("🔒 **Die Gruppe ist voll.**")
    else:
        free = lfg["max_players"] - len(participants)
        lines.append(f"**Weitere Spieler können beitreten — noch {free} "
                     f"Plätze frei (max. {lfg['max_players']}).**")
    embed = discord.Embed(
        # Der Titel ist der Event-Titel; den Status tragen nur das Emoji und
        # die Farbe (grün = Termin steht, rot = voll).
        title=f"{'🔒' if full else '🎉'} {lfg['title']}",
        description="\n".join(lines),
        color=discord.Color.red() if full else discord.Color.green())
    embed.set_footer(text=f"LFG #{lfg['id']}")
    return embed


def build_embed(lfg: dict, *, counts=None, participants=None) -> discord.Embed:
    """Der richtige Embed zum Status."""
    if lfg["status"] in LfgStatus.SETTLED:
        return build_confirmed_embed(lfg, participants or [])
    return build_open_embed(lfg, counts or {})


def build_help_embed() -> discord.Embed:
    """Die permanente Anleitung im LFG-Channel (einmalig, selbst-editierend)."""
    return discord.Embed(
        title="🔎 LFG – Looking For Group",
        description=(
            "Mit `/lfg` kannst du eine Gruppe für praktisch jede Aktivität "
            "erstellen — Gates, Quests, Zocken, Filmabend, egal was.\n"
            "\n"
            "**Du bestimmst selbst:**\n"
            "• wonach du suchst\n"
            "• wie viele Spieler benötigt werden\n"
            "• an welchem Datum\n"
            "• zu welchen Startzeiten\n"
            "\n"
            "**Beispiel:**\n"
            "```\n"
            "/lfg\n"
            "Titel:       Satura Gruppen Gate\n"
            "Spieler:     4-8\n"
            "Datum:       21.09.2026\n"
            "Startzeiten: 19:00 / 20:00 / 21:00\n"
            "```\n"
            "**So läuft es ab:**\n"
            "1️⃣ Andere wählen im Menü die Zeiten, zu denen sie können — "
            "mehrere sind erlaubt.\n"
            "2️⃣ Sobald eine Startzeit die **Mindestspielerzahl** erreicht, "
            "wird sie automatisch zum Termin.\n"
            "3️⃣ Wer diese Zeit als verfügbar angegeben hat, ist damit "
            "automatisch **Teilnehmer**.\n"
            "4️⃣ Danach können weitere Spieler bis zum Maximum über "
            "`➕ Beitreten` dazukommen.\n"
            "\n"
            "Solange kein Termin steht, kannst du deine Auswahl jederzeit "
            "ändern. Der Ersteller ist **nicht** automatisch dabei — er tritt "
            "über seine eigene Verfügbarkeit oder den Button bei.\n"
            "\n"
            "🧹 Abgelaufene LFGs verschwinden automatisch **1 Stunde nach der "
            "Startzeit**, damit der Channel sauber bleibt."),
        color=discord.Color.blurple())
