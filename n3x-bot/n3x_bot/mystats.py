"""`/meinestats` — a personal table of the caller's own stats: gate runs
(from stat-input) and command/counter stats. Read-only, public embed."""
import discord

from n3x_bot.config import Settings
from n3x_bot.gates import GATE_NAMES
from n3x_bot.storage.base import StatsRepository

# Fixed gate order for the table.
_GATE_ORDER = ["a", "b", "c", "d", "e", "z", "k"]


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _table(rows: list[tuple[str, str]], head_left: str, head_right: str) -> str:
    """Monospace two-column table (right-aligned values) in a code block."""
    width = max([len(head_left)] + [len(left) for left, _ in rows], default=len(head_left))
    rwidth = max([len(head_right)] + [len(r) for _, r in rows], default=len(head_right))
    lines = [f"{head_left:<{width}}  {head_right:>{rwidth}}"]
    lines += [f"{left:<{width}}  {right:>{rwidth}}" for left, right in rows]
    return "```\n" + "\n".join(lines) + "\n```"


def build_mystats_embed(display_name: str, user_stats: dict, gate_counts: dict,
                        gate_cost: int, stat_names: dict) -> discord.Embed:
    embed = discord.Embed(title=f"📊 Stats von {display_name}",
                          color=discord.Color.blurple())

    # Gates (stat-input): one row per gate type + a total.
    gate_rows = [(GATE_NAMES.get(g, g), _fmt(gate_counts.get(g, 0)))
                 for g in _GATE_ORDER if gate_counts.get(g)]
    total_runs = sum(gate_counts.values())
    if gate_rows:
        gate_rows.append(("Gesamt", _fmt(total_runs)))
        value = _table(gate_rows, "Gate", "Runs")
        value += f"\n💰 Kosten gesamt: **{_fmt(gate_cost)}**"
    else:
        value = "_Noch keine Gates erfasst._"
    embed.add_field(name="🚀 Gates (Stat-Input)", value=value, inline=False)

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


def register_mystats_command(bot, repo: StatsRepository, settings: Settings) -> None:
    if bot.tree.get_command("meinestats") is not None:
        return

    @bot.tree.command(name="meinestats",
                      description="Zeigt deine eigenen Stats (Gates & Zähler).")
    async def meinestats(interaction):
        uid = interaction.user.id
        user_stats = await repo.get_user_stats(uid)
        gate_counts = await repo.user_gate_counts(uid)
        gate_cost = await repo.user_gate_cost_total(uid)
        stat_names = {s.key: s.name
                      for s in await repo.list_stats(include_archived=True)}
        embed = build_mystats_embed(interaction.user.display_name, user_stats,
                                    gate_counts, gate_cost, stat_names)
        await interaction.response.send_message(embed=embed)
