from dataclasses import dataclass

import discord

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
    color: str | None = None


GATE_NAMES = {"a": "Alpha", "b": "Beta", "c": "Gamma", "d": "Delta",
              "e": "Epsilon", "z": "Zeta", "k": "Kappa"}
MILESTONE_LEVELS = {5: "Bronze", 10: "Silber", 25: "Gold", 50: "Platin",
                    100: "Diamant", 250: "Master", 500: "Grandmaster",
                    1000: "Gott"}


def _build_achievements() -> list[Achievement]:
    out: list[Achievement] = []

    # The "d"/Delta gate tiers are live: reaction-confirmed delta entries feed
    # the gate_d metric, so all 8 d_* achievements can unlock (!erfolge 59/59).
    for gtype in ("a", "b", "c", "d", "e", "z", "k"):
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
# Derived from the definitions so it stays correct when achievements are
# added/removed (currently 83).
TOTAL_ACHIEVEMENTS: int = len(ACHIEVEMENTS)


def _bar(filled_num: int, total: int, segments: int = 10) -> str:
    filled = round(filled_num / total * segments) if total else 0
    filled = max(0, min(segments, filled))
    return "█" * filled + "░" * (segments - filled)


_METRIC_UNITS = {
    "gate_a": "Läufe", "gate_b": "Läufe", "gate_c": "Läufe", "gate_d": "Läufe",
    "gate_e": "Läufe", "gate_z": "Läufe", "gate_k": "Läufe", "gate_total": "Läufe",
    "streak": "Tage", "night": "Nächte",
}


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
    if metric in ("gate_a", "gate_b", "gate_c", "gate_d",
                  "gate_e", "gate_z", "gate_k"):
        counts = await repo.user_gate_counts(discord_id)
        return counts.get(metric.split("_")[1], 0)
    if metric == "gate_total":
        counts = await repo.user_gate_counts(discord_id)
        return sum(counts.values())
    if metric == "gate_cost_total":
        return await repo.user_gate_cost_total(discord_id)
    return 0


async def check_achievements(repo: StatsRepository, discord_id: int,
                             metric: str,
                             defs: list[Achievement] | None = None
                             ) -> list[Achievement]:
    source = defs if defs is not None else ACHIEVEMENTS
    value = await user_metric_value(repo, discord_id, metric)
    metric_defs = [a for a in source if a.metric == metric]
    # Short-circuit: below the lowest threshold nothing can unlock, so skip the
    # get_user_achievements read entirely (the common case for low-value users).
    if not metric_defs or value < min(a.threshold for a in metric_defs):
        return []
    already = await repo.get_user_achievements(discord_id)
    new_ids = newly_unlocked(metric_defs, value, already)
    unlocked: list[Achievement] = []
    for a in sorted((d for d in metric_defs if d.id in new_ids),
                    key=lambda d: d.threshold):
        if await repo.unlock_achievement(discord_id, a.id):
            unlocked.append(a)
    return unlocked


# ── overview embed ─────────────────────────────────────────────────────────

def build_overview_embed(holders: dict[int, set[str]], user_ids: list[int],
                         page: int, total: int | None = None,
                         defs: list[Achievement] | None = None) -> discord.Embed:
    if not user_ids:
        return discord.Embed(
            title="🏆 Achievement-Übersicht",
            description="Noch keine Achievements freigeschaltet",
            color=discord.Color.gold())
    denom = total if total is not None else TOTAL_ACHIEVEMENTS
    idx = page % len(user_ids)
    uid = user_ids[idx]
    owned = holders.get(uid, set())
    count = len(owned)
    bar = _bar(count, denom)
    ranking = sorted(user_ids,
                     key=lambda u: len(holders.get(u, set())), reverse=True)
    rank = ranking.index(uid) + 1
    embed = discord.Embed(
        title="🏆 Achievement-Übersicht",
        color=discord.Color.gold())
    # Render the page's user as a Discord mention so each page identifies WHO it
    # shows — the client resolves <@id> to the name, no async member lookup here.
    embed.description = (
        f"<@{uid}>\n"
        f"**{count}/{denom}** Achievements freigeschaltet\n"
        f"{bar}\n"
        f"{_overview_breakdown(owned, defs)}\n"
        f"Seite {idx + 1}/{len(user_ids)} · Platz {rank}")
    return embed


def _overview_breakdown(owned: set[str], defs: list[Achievement] | None = None) -> str:
    source = defs if defs is not None else ACHIEVEMENTS

    def owned_count(*categories: str) -> int:
        return sum(1 for a in source
                   if a.category in categories and a.id in owned)

    def total_count(*categories: str) -> int:
        return sum(1 for a in source if a.category in categories)

    return (f"🚀 {owned_count('gate')}/{total_count('gate')}  "
            f"🎙️ {owned_count('voice')}/{total_count('voice')}  "
            f"🔥 {owned_count('streak')}/{total_count('streak')}  "
            f"🌙 {owned_count('night')}/{total_count('night')}  "
            f"🔒 {owned_count('message', 'reaction')}/"
            f"{total_count('message', 'reaction')}")


async def post_overview(bot, repo: StatsRepository, settings: Settings) -> None:
    if bot.runtime_config.overview_channel_id == 0:
        return
    holders = await repo.list_achievement_holders()
    if not holders:
        return
    user_ids = sorted(holders.keys())
    page = 0
    channel = bot.get_channel(bot.runtime_config.overview_channel_id)
    if channel is None:
        return
    embed = build_overview_embed(holders, user_ids, page,
                                 total=bot.achievement_defs.total,
                                 defs=bot.achievement_defs.all())
    # Re-use the tracked overview message if one still exists: EDIT it back to
    # page 0 instead of spamming a fresh message (which would orphan the nav
    # reactions on the old, now-dead message). Only post anew when nothing is
    # tracked or the old message is gone (fetch_message raises).
    state = getattr(bot, "_overview_state", None)
    if state:
        try:
            msg = await channel.fetch_message(state["message_id"])
            await msg.edit(embed=embed)
            bot._overview_state = {"message_id": msg.id, "page": page,
                                   "user_ids": user_ids}
            return
        except Exception:
            pass
    msg = await channel.send(embed=embed)
    await msg.add_reaction("⬅️")
    await msg.add_reaction("➡️")
    bot._overview_state = {"message_id": msg.id, "page": page, "user_ids": user_ids}


async def handle_overview_reaction(bot, repo: StatsRepository, settings: Settings,
                                   payload) -> None:
    state = getattr(bot, "_overview_state", None)
    if not state:
        return
    if payload.channel_id != bot.runtime_config.overview_channel_id:
        return
    if payload.message_id != state["message_id"]:
        return
    if str(payload.emoji) not in ("⬅️", "➡️"):
        return
    member = getattr(payload, "member", None)
    if member is None or getattr(member, "bot", False):
        return
    delta = 1 if str(payload.emoji) == "➡️" else -1
    user_ids = state["user_ids"]
    new_page = (state["page"] + delta) % len(user_ids)
    holders = await repo.list_achievement_holders()
    embed = build_overview_embed(holders, user_ids, new_page,
                                 total=bot.achievement_defs.total,
                                 defs=bot.achievement_defs.all())
    channel = bot.get_channel(bot.runtime_config.overview_channel_id)
    if channel is None:
        return
    msg = await channel.fetch_message(state["message_id"])
    await msg.edit(embed=embed)
    state["page"] = new_page
    try:
        await msg.remove_reaction(payload.emoji, member)
    except Exception:
        pass


# ── additive sync ──────────────────────────────────────────────────────────

async def recompute_user_achievements(repo: StatsRepository, discord_id: int,
                                      defs: list[Achievement] | None = None
                                      ) -> list[Achievement]:
    source = defs if defs is not None else ACHIEVEMENTS
    metrics = sorted({a.metric for a in source})
    newly: list[Achievement] = []
    for metric in metrics:
        newly += await check_achievements(repo, discord_id, metric, defs=source)
    return newly


async def sync_all_achievements(repo: StatsRepository,
                                defs: list[Achievement] | None = None) -> dict:
    snap = await repo.export_all()
    user_ids: set[int] = set()
    for key in ("achievements", "activity_counters", "streak_stats", "night_stats"):
        user_ids.update(int(k) for k in snap.get(key, {}))
    for row in snap.get("gate_entries", []):
        user_ids.add(int(row["user_id"]))
    users_processed = 0
    achievements_added = 0
    for uid in sorted(user_ids):
        newly = await recompute_user_achievements(repo, uid, defs=defs)
        users_processed += 1
        achievements_added += len(newly)
    return {"users_processed": users_processed,
            "achievements_added": achievements_added}


def _milestone_progress(metric: str, live: int, threshold: int) -> str:
    if metric == "voice_seconds":
        return f"{live // 3600}h/{threshold // 3600}h"
    unit = _METRIC_UNITS.get(metric)
    if unit:
        return f"{live}/{threshold} {unit}"
    return f"{live}/{threshold}"


async def _build_erfolge_embed(repo: StatsRepository, owned: set[str], uid: int,
                               display_name: str,
                               defs: list[Achievement] | None = None,
                               total: int | None = None) -> discord.Embed:
    source = defs if defs is not None else ACHIEVEMENTS
    denom = total if total is not None else TOTAL_ACHIEVEMENTS
    count = len(owned)
    percent = round(count / denom * 100)
    embed = discord.Embed(
        title=f"🏆 Achievements - {display_name}",
        color=discord.Color.gold())
    embed.description = (
        f"**{count}/{denom}** Achievements freigeschaltet\n"
        f"{_bar(count, denom)} {percent} %")

    category_labels = {"gate": "🚀 Gates", "voice": "🎙️ Voice",
                       "streak": "🔥 Streak", "night": "🌙 Nachtaktiv"}
    for category, label in category_labels.items():
        cat_defs = sorted((a for a in source if a.category == category),
                          key=lambda a: a.threshold)
        unlocked = [a for a in cat_defs if a.id in owned]
        bar = _bar(len(unlocked), len(cat_defs))
        nxt = next((a for a in cat_defs if a.id not in owned), None)
        if nxt is None:
            nxt_text = "Alle freigeschaltet"
        else:
            live = await user_metric_value(repo, uid, nxt.metric)
            nxt_text = (f"Nächstes: {nxt.title} — "
                        f"{_milestone_progress(nxt.metric, live, nxt.threshold)}")
        embed.add_field(
            name=label,
            value=f"{len(unlocked)}/{len(cat_defs)}  {bar}\n{nxt_text}",
            inline=False)

    secret_total = sum(1 for a in source if a.secret)
    secret_unlocked = len(owned & {a.id for a in source if a.secret})
    embed.add_field(name="🔒 Secret",
                    value=f"{secret_unlocked}/{secret_total}  ???", inline=False)

    return embed


def register_achievement_commands(bot, repo: StatsRepository,
                                  settings: Settings) -> None:
    if bot.tree.get_command("erfolge") is not None:
        return

    @bot.tree.command(name="erfolge",
                      description="Zeigt deine freigeschalteten Achievements.")
    async def erfolge(interaction):
        owned = await repo.get_user_achievements(interaction.user.id)
        embed = await _build_erfolge_embed(
            repo, owned, interaction.user.id, interaction.user.display_name,
            defs=bot.achievement_defs.all(), total=bot.achievement_defs.total)
        await interaction.response.send_message(embed=embed)
