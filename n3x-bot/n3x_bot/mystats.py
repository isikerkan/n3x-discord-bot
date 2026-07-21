"""`/meinestats` + `/statme` — the caller's own stats.

``/meinestats`` (and ``/statme`` with no gate) show the overview table: gate
runs from stat-input + command/counter stats. ``/statme <gate>`` lists the
caller's own INPUT HISTORY for that gate (every entry: cost, drops, date) —
the "what did I actually input" view v3 had. Read-only, public embeds.
"""
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from n3x_bot.config import Settings
from n3x_bot.gates import (
    GATE_NAMES, _DROP_LABELS, _FIELD_NAMES, GATE_DROP_REACTION_ITEMS)
from n3x_bot.storage.base import StatsRepository

# Fixed gate order for the table.
_GATE_ORDER = ["a", "b", "c", "d", "e", "z", "k"]

# Accept a gate by letter ("d") or name ("delta", "Delta Gate").
_GATE_ALIASES: dict[str, str] = {}
for _k, _name in GATE_NAMES.items():
    _GATE_ALIASES[_k] = _k
    _GATE_ALIASES[_name.lower()] = _k               # "delta gate"
    _GATE_ALIASES[_name.split()[0].lower()] = _k    # "delta"


def resolve_gate(raw: str) -> str | None:
    return _GATE_ALIASES.get((raw or "").strip().lower())


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _table(rows: list[tuple[str, str]], head_left: str, head_right: str) -> str:
    """Monospace two-column table (right-aligned values) in a code block."""
    width = max([len(head_left)] + [len(left) for left, _ in rows], default=len(head_left))
    rwidth = max([len(head_right)] + [len(r) for _, r in rows], default=len(head_right))
    lines = [f"{head_left:<{width}}  {head_right:>{rwidth}}"]
    lines += [f"{left:<{width}}  {right:>{rwidth}}" for left, right in rows]
    return "```\n" + "\n".join(lines) + "\n```"


def _drop_rate_lines(gate: str, rates: dict) -> str:
    return "".join(f"\n{_DROP_LABELS[i]}: {rates.get(i, 0.0):.1f} %"
                   for i in GATE_DROP_REACTION_ITEMS.get(gate, []))


async def personal_gate_stats(repo: StatsRepository, discord_id: int) -> dict:
    """Per-gate stats from the caller's OWN entries: ``{gate: {count, avg,
    rates:{item:pct}}}`` — the personal analogue of the public gate overview."""
    counts = await repo.user_gate_counts(discord_id)
    out: dict = {}
    for gate in counts:
        entries = await repo.list_user_gate_entries(discord_id, gate)
        n = len(entries)
        avg = round(sum(e["cost"] for e in entries) / n) if n else 0
        rates = {item: (sum(1 for e in entries if e["drops"].get(item)) / n * 100
                        if n else 0.0)
                 for item in GATE_DROP_REACTION_ITEMS.get(gate, [])}
        out[gate] = {"count": n, "avg": avg, "rates": rates}
    return out


def build_mystats_embed(display_name: str, user_stats: dict, gate_stats: dict,
                        gate_cost: int, stat_names: dict) -> discord.Embed:
    embed = discord.Embed(title=f"📊 Stats von {display_name}",
                          color=discord.Color.blurple())

    # Personal gate overview: one inline field per gate (Läufe, Ø Kosten, drop
    # rates) — mirrors the public gate embed but for this user's entries only.
    any_gate = False
    for gate in _GATE_ORDER:
        s = gate_stats.get(gate)
        if not s or not s["count"]:
            continue
        any_gate = True
        embed.add_field(
            name=_FIELD_NAMES.get(gate, GATE_NAMES.get(gate, gate)),
            value=(f"Läufe: {s['count']}\n"
                   f"Ø Kosten: {_fmt(s['avg'])}"
                   f"{_drop_rate_lines(gate, s['rates'])}"),
            inline=True)
    if not any_gate:
        embed.add_field(name="🚀 Gates (Stat-Input)",
                        value="_Noch keine Gates erfasst._", inline=False)
    else:
        total_runs = sum(s["count"] for s in gate_stats.values())
        embed.add_field(name="💰 Gesamt",
                        value=f"Läufe: {total_runs}\nKosten: {_fmt(gate_cost)}",
                        inline=True)

    # Counter / command stats, highest first.
    counter_rows = [(stat_names.get(k, k), _fmt(c))
                    for k, c in sorted(user_stats.items(),
                                       key=lambda kv: kv[1], reverse=True)]
    if counter_rows:
        cval = _table(counter_rows, "Befehl", "Anzahl")
    else:
        cval = "_Noch keine Befehle genutzt._"
    embed.add_field(name="🎮 Zähler", value=cval, inline=False)
    return embed


def build_gate_history_embed(display_name: str, gate_key: str, entries: list,
                             tz, limit: int = 25) -> discord.Embed:
    """A numbered list of the user's own entries for one gate (newest last)."""
    name = GATE_NAMES.get(gate_key, gate_key)
    embed = discord.Embed(title=f"📜 {name} — Verlauf von {display_name}",
                          color=discord.Color.green())
    if not entries:
        embed.description = "_Noch keine Einträge erfasst._"
        return embed
    shown = entries[-limit:]
    start = len(entries) - len(shown) + 1
    lines = []
    for i, e in enumerate(shown, start):
        when = e["created_at"].astimezone(tz).strftime("%d.%m %H:%M")
        dropped = [_DROP_LABELS[k] for k, v in e["drops"].items()
                   if v and k in _DROP_LABELS]
        drop_str = "  ·  " + ", ".join(dropped) if dropped else ""
        lines.append(f"{i:>3}. {_fmt(e['cost']):>10}   {when}{drop_str}")
    total = sum(e["cost"] for e in entries)
    head = (f"**{len(entries)}** Einträge · Kosten gesamt **{_fmt(total)}**")
    if len(entries) > limit:
        head += f"  (letzte {limit} gezeigt)"
    embed.description = head + "\n```\n" + "\n".join(lines) + "\n```"
    return embed


def register_mystats_command(bot, repo: StatsRepository, settings: Settings) -> None:
    if bot.tree.get_command("meinestats") is not None:
        return

    async def _overview_embed(interaction) -> discord.Embed:
        uid = interaction.user.id
        user_stats = await repo.get_user_stats(uid)
        gate_stats = await personal_gate_stats(repo, uid)
        gate_cost = await repo.user_gate_cost_total(uid)
        stat_names = {s.key: s.name
                      for s in await repo.list_stats(include_archived=True)}
        embed = build_mystats_embed(interaction.user.display_name, user_stats,
                                    gate_stats, gate_cost, stat_names)
        embed.set_footer(text="Verlauf: /statme <gate> (z. B. /statme delta)")
        return embed

    if bot.tree.get_command("meinestats") is None:
        @bot.tree.command(name="meinestats",
                          description="Zeigt deine eigenen Stats (Gates & Zähler).")
        async def meinestats(interaction):
            await interaction.response.send_message(
                embed=await _overview_embed(interaction))

    if bot.tree.get_command("statme") is not None:
        return
    tz = ZoneInfo(settings.timezone)

    @bot.tree.command(
        name="statme",
        description="Deine Stats; mit Gate den Eingabe-Verlauf (z. B. /statme delta).")
    @app_commands.describe(gate="Gate für deinen Eingabe-Verlauf (a-k oder Name)")
    async def statme(interaction, gate: str | None = None):
        if gate is None:
            await interaction.response.send_message(
                embed=await _overview_embed(interaction))
            return
        key = resolve_gate(gate)
        if key is None:
            await interaction.response.send_message(
                f"❌ Unbekanntes Gate `{gate}`. Nutze a-k oder z. B. `delta`.",
                ephemeral=True)
            return
        entries = await repo.list_user_gate_entries(interaction.user.id, key)
        embed = build_gate_history_embed(
            interaction.user.display_name, key, entries, tz)
        await interaction.response.send_message(embed=embed)

    @statme.autocomplete("gate")
    async def _gate_ac(interaction, current: str):
        cur = (current or "").lower()
        return [app_commands.Choice(name=n, value=k)
                for k, n in GATE_NAMES.items()
                if cur in k or cur in n.lower()][:25]
