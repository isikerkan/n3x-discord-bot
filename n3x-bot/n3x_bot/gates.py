"""Gate tracker: message parsing + stats embed rendering.

Parsing (`parse_gate_message`) and the totals->content math
(`build_gate_content`) are Discord-free and unit-testable in isolation;
only `build_gate_embed` touches `discord.Embed`.
"""
import re

import discord

from n3x_bot.format import format_number

_PATTERN = re.compile(r"^([abcd])\s+([\d.]+)$", re.IGNORECASE)

GATE_NAMES = {"a": "Alpha Gate", "b": "Beta Gate", "c": "Gamma Gate",
              "d": "Delta Gate"}


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


def build_gate_content(totals: dict, rewards: dict) -> str:
    """Render the totals/reward-diff body text shared by the stats embed.

    `totals` is `StatsRepository.gate_totals()`'s return shape
    (`{gate_type: {"count": int, "avg": int}}`); `rewards` is
    `Settings.gate_rewards_map()`'s return shape (`{gate_type: int}`).
    Missing gate types default to a zeroed row / zero reward.
    """
    content = ""
    for gate_type in ("a", "b", "c"):
        gdata = totals.get(gate_type, {"count": 0, "avg": 0})
        reward = rewards.get(gate_type, 0)
        diff = reward - gdata["avg"] if gdata["count"] > 0 else 0
        diff_color = "🟢" if diff >= 0 else "🔴"
        name = GATE_NAMES.get(gate_type, gate_type.upper())
        content += (
            f"**{name}**\n\n"
            f"Total Gates: {gdata['count']}\n\n"
            f"Average Cost:\n{format_number(gdata['avg'])}\n\n"
            f"Reward:\n{format_number(reward)}\n\n"
            f"Difference: {diff_color}\n{format_number(diff)}\n\n"
            "══════════════════════\n\n"
        )
    return content


def build_gate_embed(totals: dict, rewards: dict, now_str: str,
                     delta: dict | None = None) -> discord.Embed:
    """Build the live gate-stats embed. `now_str` is caller-supplied (no
    `datetime.now()` inside) so the render is deterministic/testable.

    When `delta` (a `delta_stats()` dict) is given, a separate Delta Gate
    field is appended.
    """
    embed = discord.Embed(title="📊 Gate Statistics", color=discord.Color.blue())
    embed.description = build_gate_content(totals, rewards)
    if delta is not None:
        embed.add_field(
            name="💎 Delta Gate",
            value=(f"Runs: {delta['count']}\n"
                   f"Avg. Cost: {format_number(delta['avg'])}\n"
                   f"Reward: {format_number(rewards.get('d', 0))}\n"
                   f"Drop Rate: {delta['laser_rate']:.1f} %"),
            inline=True)
    embed.set_footer(text=f"Last Update: {now_str}")
    return embed
