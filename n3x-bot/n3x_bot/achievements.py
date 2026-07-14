from dataclasses import dataclass

import discord
from discord.ext import commands

from n3x_bot.config import Settings
from n3x_bot.storage.base import StatsRepository


@dataclass(frozen=True)
class Achievement:
    id: str
    category: str
    metric: str
    threshold: int
    title: str
    secret: bool


GATE_NAMES = {"a": "Alpha", "b": "Beta", "c": "Gamma", "d": "Delta"}
MILESTONE_LEVELS = {5: "Bronze", 10: "Silber", 25: "Gold", 50: "Platin",
                    100: "Diamant", 250: "Master", 500: "Grandmaster",
                    1000: "Gott"}


def _build_achievements() -> list[Achievement]:
    out: list[Achievement] = []

    for gtype in ("a", "b", "c", "d"):
        for thr, level in MILESTONE_LEVELS.items():
            out.append(Achievement(
                id=f"{gtype}_{thr}", category="gate", metric=f"gate_{gtype}",
                threshold=thr, title=f"{GATE_NAMES[gtype]} {level} Pilot",
                secret=False))

    gate_specials = [
        ("total_1", "gate_total", 1, "Einsteiger Pilot"),
        ("total_50", "gate_total", 50, "Profi Pilot"),
        ("total_100", "gate_total", 100, "Veteran Pilot"),
        ("millionaire", "gate_cost_total", 1000000, "Millionärs-Club Pilot"),
    ]
    for aid, metric, thr, title in gate_specials:
        out.append(Achievement(id=aid, category="gate", metric=metric,
                               threshold=thr, title=title, secret=False))

    voice = [
        (3600, "Rookie Talker"),
        (36000, "Stammgast"),
        (180000, "Stammspieler"),
        (360000, "Veteran"),
        (1800000, "Elite Player"),
        (3600000, "Night Shadow Legende"),
    ]
    for thr, title in voice:
        out.append(Achievement(id=f"voice_{thr}", category="voice",
                               metric="voice_seconds", threshold=thr,
                               title=title, secret=False))

    message = [
        (1000, "Tastatur-Krieger"),
        (5000, "Chat-Maschine"),
        (10000, "Nachrichten-Veteran"),
        (50000, "Spam-Gott"),
    ]
    for thr, title in message:
        out.append(Achievement(id=f"msg_{thr}", category="message",
                               metric="messages", threshold=thr,
                               title=title, secret=True))

    streak = [
        (7, "Treuer Soldat"),
        (14, "Zuverlässig"),
        (30, "Monats-Krieger"),
        (60, "Unaufhaltsam"),
        (100, "Eiserner Wille"),
        (365, "365-Tage-Legende"),
    ]
    for thr, title in streak:
        out.append(Achievement(id=f"streak_{thr}", category="streak",
                               metric="streak", threshold=thr,
                               title=title, secret=False))

    night = [
        (10, "Nachteule"),
        (50, "Schlaflos"),
        (100, "Vampir Pilot"),
    ]
    for thr, title in night:
        out.append(Achievement(id=f"night_{thr}", category="night",
                               metric="night", threshold=thr,
                               title=title, secret=False))

    reaction = [
        (100, "Emoji-Fan"),
        (500, "Reaktions-Profi"),
        (1000, "Reaktions-Meister"),
        (5000, "Reaktions-Maschine"),
    ]
    for thr, title in reaction:
        out.append(Achievement(id=f"reaction_{thr}", category="reaction",
                               metric="reactions", threshold=thr,
                               title=title, secret=True))

    return out


ACHIEVEMENTS: list[Achievement] = _build_achievements()
TOTAL_ACHIEVEMENTS: int = 59


def newly_unlocked(defs_for_metric: list[Achievement], value: int,
                   already: set[str]) -> set[str]:
    return {a.id for a in defs_for_metric
            if value >= a.threshold and a.id not in already}


async def user_metric_value(repo: StatsRepository, discord_id: int,
                            metric: str) -> int:
    if metric in ("messages", "voice_seconds", "reactions"):
        return await repo.get_activity(discord_id, metric)
    if metric == "streak":
        r = await repo.get_streak(discord_id)
        return r["max_streak"] if r else 0
    if metric == "night":
        r = await repo.get_night(discord_id)
        return r["night_count"] if r else 0
    if metric in ("gate_a", "gate_b", "gate_c", "gate_d"):
        counts = await repo.user_gate_counts(discord_id)
        return counts.get(metric.split("_")[1], 0)
    if metric == "gate_total":
        counts = await repo.user_gate_counts(discord_id)
        return sum(counts.values())
    if metric == "gate_cost_total":
        return await repo.user_gate_cost_total(discord_id)
    return 0


async def check_achievements(repo: StatsRepository, discord_id: int,
                             metric: str) -> list[Achievement]:
    value = await user_metric_value(repo, discord_id, metric)
    defs = [a for a in ACHIEVEMENTS if a.metric == metric]
    already = await repo.get_user_achievements(discord_id)
    new_ids = newly_unlocked(defs, value, already)
    unlocked: list[Achievement] = []
    for a in sorted((d for d in defs if d.id in new_ids),
                    key=lambda d: d.threshold):
        if await repo.unlock_achievement(discord_id, a.id):
            unlocked.append(a)
    return unlocked


def register_achievement_commands(bot, repo: StatsRepository,
                                  settings: Settings) -> None:
    if bot.get_command("erfolge") is not None:
        return

    async def _erfolge(ctx):
        owned = await repo.get_user_achievements(ctx.author.id)
        count = len(owned)
        embed = discord.Embed(
            title=f"🏆 Achievements - {ctx.author.display_name}",
            color=discord.Color.gold())
        embed.description = (
            f"**{count}/{TOTAL_ACHIEVEMENTS}** Achievements freigeschaltet")

        category_labels = {"gate": "🚀 Gates", "voice": "🎙️ Voice",
                           "streak": "🔥 Streak", "night": "🌙 Nachtaktiv"}
        for category, label in category_labels.items():
            defs = sorted((a for a in ACHIEVEMENTS if a.category == category),
                          key=lambda a: a.threshold)
            unlocked = [a for a in defs if a.id in owned]
            nxt = next((a for a in defs if a.id not in owned), None)
            if nxt is None:
                nxt_text = "Alle freigeschaltet"
            else:
                nxt_text = f"Nächstes: {nxt.title} ({nxt.threshold})"
            embed.add_field(
                name=label,
                value=f"{len(unlocked)}/{len(defs)}\n{nxt_text}",
                inline=False)

        secret_total = sum(1 for a in ACHIEVEMENTS if a.secret)
        secret_unlocked = len(owned & {a.id for a in ACHIEVEMENTS if a.secret})
        embed.add_field(name="🔒 Secret",
                        value=f"{secret_unlocked}/{secret_total}", inline=False)

        await ctx.send(embed=embed)

    bot.add_command(commands.Command(_erfolge, name="erfolge"))
