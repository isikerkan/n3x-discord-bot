"""Gate tracker: message parsing + stats embed rendering.

Parsing (`parse_gate_message`) and the totals->content math
(`build_gate_content`) are Discord-free and unit-testable in isolation;
only `build_gate_embed` touches `discord.Embed`.
"""
import re

import discord

from n3x_bot.format import format_number

_PATTERN = re.compile(r"^([abcdezk])\s+([\d.]+)$", re.IGNORECASE)

GATE_NAMES = {"a": "Alpha Gate", "b": "Beta Gate", "c": "Gamma Gate",
              "d": "Delta Gate", "e": "Epsilon Gate", "z": "Zeta Gate",
              "k": "Kappa Gate"}

_DROP_LABELS = {"laser": "Laser Drop Rate", "lf4": "LF4 Drop Rate",
                "havoc": "Havoc Drop Rate", "hercules": "Hercules Drop Rate",
                "lf4u": "LF4-U Drop Rate"}


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


def _drop_rate_lines(stats: dict) -> str:
    return "".join(f"\n{_DROP_LABELS.get(item, item)}: {rate:.1f} %"
                   for item, rate in stats["rates"].items())


def build_gate_embed(totals: dict, rewards: dict, now_str: str,
                     delta: dict | None = None, epsilon: dict | None = None,
                     zeta: dict | None = None,
                     kappa: dict | None = None) -> discord.Embed:
    """Build the live gate-stats embed. `now_str` is caller-supplied (no
    `datetime.now()` inside) so the render is deterministic/testable.

    When `delta` (a `delta_stats()` dict) is given, a separate Delta Gate
    field is appended (it keeps its Reward line). Each of `epsilon`/`zeta`/
    `kappa` (a `gate_drop_stats()` dict) that is given adds a rewardless field
    listing its per-item drop rates.
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
    for name, stats in (("🔷 Epsilon Gate", epsilon), ("🔷 Zeta Gate", zeta),
                        ("🔷 Kappa Gate", kappa)):
        if stats is not None:
            embed.add_field(
                name=name,
                value=(f"Runs: {stats['count']}\n"
                       f"Avg. Cost: {format_number(stats['avg'])}"
                       f"{_drop_rate_lines(stats)}"),
                inline=True)
    embed.set_footer(text=f"Last Update: {now_str}")
    return embed


class KappaConfirmView(discord.ui.View):
    """Button panel confirming a Kappa gate: Hercules toggle, LF4-U toggle,
    Submit. Author-only; on Submit stores gate "k" with both drop bools.
    """

    def __init__(self, repo, bot, settings, *, cost: int, user_id: int,
                 username: str):
        super().__init__(timeout=None)
        self.repo = repo
        self.bot = bot
        self.settings = settings
        self.cost = cost
        self.user_id = user_id
        self.username = username
        self.hercules_dropped = False
        self.lf4u_dropped = False
        self._submitted = False

        hercules_btn = discord.ui.Button(label="Hercules",
                                         style=discord.ButtonStyle.secondary)
        hercules_btn.callback = self.on_toggle_hercules
        lf4u_btn = discord.ui.Button(label="LF4-U",
                                     style=discord.ButtonStyle.secondary)
        lf4u_btn.callback = self.on_toggle_lf4u
        submit_btn = discord.ui.Button(label="Bestätigen",
                                       style=discord.ButtonStyle.success)
        submit_btn.callback = self.on_submit
        self._hercules_btn = hercules_btn
        self._lf4u_btn = lf4u_btn
        self.add_item(hercules_btn)
        self.add_item(lf4u_btn)
        self.add_item(submit_btn)

    def _refresh_styles(self) -> None:
        self._hercules_btn.style = (discord.ButtonStyle.success
                                    if self.hercules_dropped
                                    else discord.ButtonStyle.secondary)
        self._lf4u_btn.style = (discord.ButtonStyle.success
                                if self.lf4u_dropped
                                else discord.ButtonStyle.secondary)

    async def on_toggle_hercules(self, interaction) -> None:
        if interaction.user.id != self.user_id:
            return
        self.hercules_dropped = not self.hercules_dropped
        self._refresh_styles()
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            pass

    async def on_toggle_lf4u(self, interaction) -> None:
        if interaction.user.id != self.user_id:
            return
        self.lf4u_dropped = not self.lf4u_dropped
        self._refresh_styles()
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            pass

    async def on_submit(self, interaction) -> None:
        if interaction.user.id != self.user_id:
            return
        # Consume the panel ATOMICALLY before any await: with timeout=None the
        # buttons stay live indefinitely, so a second "Bestätigen" click (past
        # add_gate_entry's dedup window) would otherwise store a second "k" row
        # for the same run. Setting the flag before the store is the analog of
        # the reaction path's atomic _pending_delta.pop() single-store guard.
        if self._submitted:
            return
        self._submitted = True
        before = await self.repo.gate_record("k")
        inserted = await self.repo.add_gate_entry(
            "k", self.cost, self.user_id, self.username,
            drops={"hercules": self.hercules_dropped,
                   "lf4u": self.lf4u_dropped})
        if inserted:
            try:
                from n3x_bot.bot import (update_gate_stats_embed,
                                         _announce_records)
                from n3x_bot.achievements import check_achievements
                from n3x_bot.cards import announce_achievements
                after = await self.repo.gate_record("k")
                await _announce_records(self.bot, self.settings, "k",
                                        changed_records(before, after), after)
                await update_gate_stats_embed(self.bot, self.repo, self.settings)
                newly = (await check_achievements(self.repo, self.user_id, "gate_k")
                         + await check_achievements(self.repo, self.user_id, "gate_total")
                         + await check_achievements(self.repo, self.user_id, "gate_cost_total"))
                if newly:
                    await announce_achievements(self.bot, self.settings,
                                                interaction.user, newly)
            except Exception:
                pass
        # Retire the panel so it can't be re-submitted: stop the view and
        # disable every button (best-effort message edit to push the state).
        self.stop()
        for child in self.children:
            child.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            pass
