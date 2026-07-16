"""Gate tracker: message parsing + stats embed rendering.

Parsing (`parse_gate_message`) is Discord-free and unit-testable in
isolation; only `build_gate_embed` touches `discord.Embed`.
"""
import re
from datetime import date, datetime

import discord

from n3x_bot.format import format_number

_PATTERN = re.compile(r"^([abcdezk])\s+([\d.]+)$", re.IGNORECASE)

GATE_NAMES = {"a": "Alpha Gate", "b": "Beta Gate", "c": "Gamma Gate",
              "d": "Delta Gate", "e": "Epsilon Gate", "z": "Zeta Gate",
              "k": "Kappa Gate"}

_FIELD_NAMES = {"a": "🅰 Alpha Gate", "b": "🅱 Beta Gate", "c": "🇨 Gamma Gate",
                "d": "💎 Delta Gate", "e": "🟦 Epsilon Gate",
                "z": "🟪 Zeta Gate", "k": "🟩 Kappa Gate"}

_DROP_LABELS = {"laser": "Laser", "lf4": "LF4", "havoc": "Havoc",
                "hercules": "Hercules", "lf4u": "LF4-U"}

_GATE_DROP_ITEMS = {"e": ["lf4"], "z": ["havoc"], "k": ["hercules", "lf4u"]}

GATE_DROP_REACTION_ITEMS = {"d": ["laser"], "e": ["lf4"], "z": ["havoc"],
                            "k": ["hercules", "lf4u"]}

DROP_EMOJI_NAMES = {"laser": "prom", "lf4": "lf4", "havoc": "havoc",
                    "hercules": "hercu", "lf4u": "lf4"}

DROP_EMOJI_FALLBACKS = {"laser": "🔫", "lf4": "🟦", "havoc": "🟪",
                        "hercules": "🟩", "lf4u": "🔷"}

DROP_NOTHING_EMOJI = "❌"

_ZWSP = "​"


def resolve_drop_emoji(guild, item) -> "discord.Emoji | str":
    """Resolve a drop item to a custom guild emoji by name, else a unicode
    fallback. Never raises; `guild=None` (or no match) yields the fallback.
    """
    name = DROP_EMOJI_NAMES[item]
    if guild is not None:
        for emoji in guild.emojis:
            if emoji.name == name:
                return emoji
    return DROP_EMOJI_FALLBACKS[item]


def parse_gate_message(content: str) -> tuple[str, int] | None:
    """Parse a gate-input message like `a 46892` or `A 1.234.567`.

    Dots (German thousands separators) are stripped before parsing the
    number. Returns `(gate_type_lower, cost)` or None if the message
    doesn't match the expected shape or the number can't be parsed.
    """
    match = _PATTERN.match(content.strip())
    if match is None:
        return None
    gate_type = match.group(1).lower()
    cost_str = match.group(2).replace(".", "")
    try:
        cost = int(cost_str)
    except ValueError:
        return None
    return gate_type, cost


def parse_de_date(s: str) -> date | None:
    """Parse a German `TT.MM.JJJJ` or ISO `JJJJ-MM-TT` date string.

    Returns the first format that parses as a `date`, else None (junk and
    empty strings yield None, never raises).
    """
    s = s.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def changed_records(before: dict | None, after: dict) -> set[str]:
    """Which of {"min", "max"} newly changed between two gate_record dicts.

    `before` is the gate_record BEFORE an add (or None for the first entry);
    `after` is the gate_record AFTER. The first entry sets both.
    """
    if before is None:
        return {"min", "max"}
    out = set()
    if after["min_cost"] < before["min_cost"]:
        out.add("min")
    if after["max_cost"] > before["max_cost"]:
        out.add("max")
    return out


def _drop_rate_lines(gate_type: str, stats: dict) -> str:
    return "".join(f"\n{_DROP_LABELS[item]}: {stats['rates'].get(item, 0.0):.1f} %"
                   for item in _GATE_DROP_ITEMS[gate_type])


def _belohnung_line(reward: int) -> str:
    return f"Belohnung: {format_number(reward)}"


def _reward_lines(reward: int, avg: int, count: int) -> str:
    diff = reward - avg if count > 0 else 0
    diff_color = "🟢" if diff >= 0 else "🔴"
    return (f"Belohnung: {format_number(reward)}\n"
            f"Gewinn: {diff_color} {format_number(diff)}")


def build_gate_embed(totals: dict, rewards: dict, now_str: str,
                     delta: dict | None = None, epsilon: dict | None = None,
                     zeta: dict | None = None,
                     kappa: dict | None = None) -> discord.Embed:
    """Build the live gate-stats embed. `now_str` is caller-supplied (no
    `datetime.now()` inside) so the render is deterministic/testable.

    Every gate is a uniform German inline field; there is no description blob.
    a/b/c carry Läufe/Ø Kosten/Belohnung/Gewinn rows; Delta keeps its Belohnung
    line plus a Laser drop rate; Epsilon/Zeta/Kappa are rewardless and list
    their per-item drop rates. A zero-width spacer field pads the grid so Zeta
    and Kappa begin a fresh row. When `delta`/`epsilon`/`zeta`/`kappa` are all
    None (legacy call), only the a/b/c fields are emitted (no spacer).
    """
    embed = discord.Embed(title="📊 Gate Statistik", color=discord.Color.blue())
    for gate_type in ("a", "b", "c"):
        gdata = totals.get(gate_type, {"count": 0, "avg": 0})
        embed.add_field(
            name=_FIELD_NAMES[gate_type],
            value=(f"Läufe: {gdata['count']}\n"
                   f"Ø Kosten: {format_number(gdata['avg'])}\n"
                   f"{_reward_lines(rewards.get(gate_type, 0), gdata['avg'], gdata['count'])}"),
            inline=True)
    if delta is not None:
        embed.add_field(
            name=_FIELD_NAMES["d"],
            value=(f"Läufe: {delta['count']}\n"
                   f"Ø Kosten: {format_number(delta['avg'])}\n"
                   f"{_belohnung_line(rewards.get('d', 0))}\n"
                   f"Laser: {delta['laser_rate']:.1f} %"),
            inline=True)
    if epsilon is not None:
        embed.add_field(
            name=_FIELD_NAMES["e"],
            value=(f"Läufe: {epsilon['count']}\n"
                   f"Ø Kosten: {format_number(epsilon['avg'])}\n"
                   f"{_belohnung_line(rewards.get('e', 0))}"
                   f"{_drop_rate_lines('e', epsilon)}"),
            inline=True)
    if zeta is not None or kappa is not None:
        embed.add_field(name=_ZWSP, value=_ZWSP, inline=True)
    for gate_type, stats in (("z", zeta), ("k", kappa)):
        if stats is not None:
            embed.add_field(
                name=_FIELD_NAMES[gate_type],
                value=(f"Läufe: {stats['count']}\n"
                       f"Ø Kosten: {format_number(stats['avg'])}\n"
                       f"{_belohnung_line(rewards.get(gate_type, 0))}"
                       f"{_drop_rate_lines(gate_type, stats)}"),
                inline=True)
    embed.set_footer(text=f"Letztes Update: {now_str}")
    return embed
