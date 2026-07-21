"""`/gatelog` — admin command listing every user's gate entries for a gate
(or all gates), sorted. The admin analogue of the personal `/statme <gate>`."""
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from n3x_bot.admin import app_is_admin
from n3x_bot.config import Settings
from n3x_bot.gates import GATE_NAMES, _FIELD_NAMES, _DROP_LABELS
from n3x_bot.mystats import resolve_gate, _GATE_ORDER
from n3x_bot.storage.base import StatsRepository

_CHUNK = 20            # entries per embed (keeps each well under the desc limit)
_MAX_EMBEDS = 8        # cap the number of followups (160 newest entries)


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _sort_entries(entries: list, sort: str) -> list:
    if sort == "cost":
        return sorted(entries, key=lambda e: e["cost"], reverse=True)
    if sort == "user":
        return sorted(entries, key=lambda e: (e["username"].lower(), -e["cost"]))
    return sorted(entries, key=lambda e: e["created_at"])   # "date"


def build_gatelog_embeds(entries: list, gate_key: str | None, sort: str,
                         tz) -> list[discord.Embed]:
    scope = GATE_NAMES.get(gate_key, gate_key) if gate_key else "Alle Gates"
    title = f"📋 Gate-Log — {scope} (sortiert: {sort})"
    if not entries:
        return [discord.Embed(title=title, description="_Keine Einträge._",
                              color=discord.Color.orange())]
    ordered = _sort_entries(entries, sort)
    total = len(ordered)
    lines = []
    for i, e in enumerate(ordered, 1):
        when = e["created_at"].astimezone(tz).strftime("%d.%m %H:%M")
        dropped = [_DROP_LABELS[k] for k, v in e["drops"].items()
                   if v and k in _DROP_LABELS]
        drop_str = "  " + ",".join(dropped) if dropped else ""
        gate_tag = f"[{e['gate_type'].upper()}] " if gate_key is None else ""
        lines.append(f"{i:>3}. {gate_tag}{_fmt(e['cost']):>11}  "
                     f"{e['username'][:16]:<16} {when}{drop_str}")

    embeds = []
    for start in range(0, min(len(lines), _CHUNK * _MAX_EMBEDS), _CHUNK):
        chunk = lines[start:start + _CHUNK]
        e = discord.Embed(color=discord.Color.orange())
        if start == 0:
            e.title = title
            total_cost = sum(x["cost"] for x in ordered)
            e.description = (f"**{total}** Einträge · Kosten gesamt "
                             f"**{_fmt(total_cost)}**\n```\n"
                             + "\n".join(chunk) + "\n```")
        else:
            e.description = "```\n" + "\n".join(chunk) + "\n```"
        embeds.append(e)
    if len(lines) > _CHUNK * _MAX_EMBEDS:
        embeds[-1].set_footer(
            text=f"… {total - _CHUNK * _MAX_EMBEDS} weitere nicht gezeigt.")
    return embeds


def compute_user_gate_averages(entries: list) -> dict:
    """``{gate: [(username, avg_cost, count)]}`` sorted by avg cost desc — each
    user's average input per gate."""
    agg: dict = {}
    for e in entries:
        u = agg.setdefault(e["gate_type"], {}).setdefault(
            e["user_id"], {"name": e["username"], "sum": 0, "count": 0})
        u["sum"] += e["cost"]
        u["count"] += 1
        u["name"] = e["username"]        # keep the latest known name
    out: dict = {}
    for gate, users in agg.items():
        rows = [(v["name"], round(v["sum"] / v["count"]), v["count"])
                for v in users.values()]
        rows.sort(key=lambda r: r[1], reverse=True)
        out[gate] = rows
    return out


def _avg_table(rows: list) -> str:
    """Monospace 'user  Ø Kosten  (runs)' table for one gate."""
    wname = max([4] + [len(r[0][:16]) for r in rows])
    lines = [f"{'User':<{wname}}  {'Ø Kosten':>11}  Runs"]
    lines += [f"{r[0][:16]:<{wname}}  {_fmt(r[1]):>11}  {r[2]:>4}" for r in rows]
    return "```\n" + "\n".join(lines) + "\n```"


def build_average_embeds(entries: list, gate_key: str | None) -> list[discord.Embed]:
    averages = compute_user_gate_averages(entries)
    if gate_key is not None:
        rows = averages.get(gate_key, [])
        title = f"📊 Ø Kosten pro User — {GATE_NAMES.get(gate_key, gate_key)}"
        if not rows:
            return [discord.Embed(title=title, description="_Keine Einträge._",
                                  color=discord.Color.teal())]
        embeds = []
        for start in range(0, min(len(rows), _CHUNK * _MAX_EMBEDS), _CHUNK):
            chunk = rows[start:start + _CHUNK]
            e = discord.Embed(color=discord.Color.teal())
            if start == 0:
                e.title = title
                e.description = f"**{len(rows)}** User\n" + _avg_table(chunk)
            else:
                e.description = _avg_table(chunk)
            embeds.append(e)
        return embeds
    # all gates: one field per gate (top 10 users by avg), compact
    embed = discord.Embed(title="📊 Ø Kosten pro User — Alle Gates",
                          color=discord.Color.teal())
    any_gate = False
    for gate in _GATE_ORDER:
        rows = averages.get(gate)
        if not rows:
            continue
        any_gate = True
        top = rows[:10]
        body = "\n".join(f"{r[0][:14]:<14} {_fmt(r[1]):>11} ({r[2]})" for r in top)
        if len(rows) > 10:
            body += f"\n… +{len(rows) - 10}"
        embed.add_field(name=_FIELD_NAMES.get(gate, GATE_NAMES.get(gate, gate)),
                        value="```\n" + body + "\n```", inline=True)
    if not any_gate:
        embed.description = "_Keine Einträge._"
    return [embed]


def register_gatelog_command(bot, repo: StatsRepository, settings: Settings) -> None:
    # `/gate log` — a subcommand of the existing `/gate` group (created by
    # register_gate_commands, which runs first).
    gate_group = bot.tree.get_command("gate")
    if not isinstance(gate_group, app_commands.Group):
        return
    if gate_group.get_command("log") is not None:
        return
    tz = ZoneInfo(settings.timezone)

    @gate_group.command(
        name="log",
        description="Listet alle Gate-Einträge der User (Admin).")
    @app_commands.describe(
        gate="Gate (a-k oder Name); leer/all = alle Gates",
        sort="Sortierung: date, cost oder user")
    @app_commands.choices(sort=[
        app_commands.Choice(name="Datum", value="date"),
        app_commands.Choice(name="Kosten (höchste zuerst)", value="cost"),
        app_commands.Choice(name="User", value="user"),
    ])
    async def gatelog(interaction, gate: str | None = None,
                      sort: app_commands.Choice[str] | None = None):
        if not app_is_admin(interaction, settings):
            await interaction.response.send_message(
                "❌ Keine Berechtigung.", ephemeral=True)
            return
        sort_val = sort.value if sort is not None else "date"
        gate_key = None
        if gate is not None and gate.strip().lower() not in ("", "all", "alle"):
            gate_key = resolve_gate(gate)
            if gate_key is None:
                await interaction.response.send_message(
                    f"❌ Unbekanntes Gate `{gate}`.", ephemeral=True)
                return
        await interaction.response.defer(ephemeral=True)
        entries = await repo.list_gate_entries_full(gate_key)
        for embed in build_gatelog_embeds(entries, gate_key, sort_val, tz):
            await interaction.followup.send(embed=embed, ephemeral=True)

    @gatelog.autocomplete("gate")
    async def _gate_ac(interaction, current: str):
        return _gate_choices(current)

    @gate_group.command(
        name="durchschnitt",
        description="Ø Kosten pro User pro Gate — öffentlich (Admin).")
    @app_commands.describe(gate="Gate (a-k oder Name); leer/all = alle Gates")
    async def gate_avg(interaction, gate: str | None = None):
        if not app_is_admin(interaction, settings):
            await interaction.response.send_message(
                "❌ Keine Berechtigung.", ephemeral=True)
            return
        gate_key = None
        if gate is not None and gate.strip().lower() not in ("", "all", "alle"):
            gate_key = resolve_gate(gate)
            if gate_key is None:
                await interaction.response.send_message(
                    f"❌ Unbekanntes Gate `{gate}`.", ephemeral=True)
                return
        # Public output (everyone sees the averages), per the request.
        await interaction.response.defer()
        entries = await repo.list_gate_entries_full(gate_key)
        for embed in build_average_embeds(entries, gate_key):
            await interaction.followup.send(embed=embed)

    @gate_avg.autocomplete("gate")
    async def _avg_gate_ac(interaction, current: str):
        return _gate_choices(current)


def _gate_choices(current: str):
    cur = (current or "").lower()
    choices = [app_commands.Choice(name="Alle Gates", value="all")]
    choices += [app_commands.Choice(name=n, value=k)
                for k, n in GATE_NAMES.items()
                if cur in k or cur in n.lower()]
    return choices[:25]
