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


class OverviewView(discord.ui.View):
    """Persistent (``timeout=None``) nav for the achievement overview.

    Anyone may page; each ◀/▶ click recomputes holders live, advances
    ``bot._overview_state["page"]`` by ±1 (mod holder count), rebuilds the page
    embed and edits the message in place. ``bot.add_view(OverviewView(...))`` in
    ``on_ready`` re-attaches the callbacks after a restart.
    """

    def __init__(self, bot, repo: StatsRepository, settings: Settings):
        super().__init__(timeout=None)
        self.bot = bot
        self.repo = repo
        self.settings = settings

    async def _page(self, interaction, delta: int) -> None:
        holders = await self.repo.list_achievement_holders()
        user_ids = sorted(holders)
        if not user_ids:
            return
        state = getattr(self.bot, "_overview_state", None) or {}
        page = state.get("page", 0)
        new_page = (page + delta) % len(user_ids)
        embed = build_overview_embed(holders, user_ids, new_page,
                                     total=self.bot.achievement_defs.total,
                                     defs=self.bot.achievement_defs.all())
        await interaction.response.edit_message(embed=embed, view=self)
        self.bot._overview_state = {"message_id": state.get("message_id"),
                                    "page": new_page, "user_ids": user_ids}

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary,
                       custom_id="n3x:overview:prev")
    async def prev(self, interaction, button):
        await self._page(interaction, -1)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary,
                       custom_id="n3x:overview:next")
    async def next(self, interaction, button):
        await self._page(interaction, 1)


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
    # page 0 instead of spamming a fresh message. The persistent view stays
    # attached across the edit (no re-send). Only post anew when nothing is
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
    msg = await channel.send(embed=embed,
                             view=OverviewView(bot, repo, settings))
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


def _fmt(n: int) -> str:
    """German thousands separator (1.234.567)."""
    return f"{n:,}".replace(",", ".")


def _achievement_label(a: Achievement) -> str:
    """Human milestone label for a single achievement in a ✅/⬛ checklist."""
    if a.metric == "voice_seconds":
        return f"{a.threshold // 3600}h"
    if a.metric == "gate_cost_total":
        return f"{_fmt(a.threshold)} Uridium"
    if a.metric == "gate_total":
        return f"{a.threshold} Gate" if a.threshold == 1 else f"{a.threshold} Gates"
    if a.metric.startswith("gate_"):
        return f"{a.threshold} Läufe"
    if a.category == "streak":
        return f"{a.threshold} Tage"
    if a.category == "night":
        return f"{a.threshold} Nächte"
    return str(a.threshold)


def _checklist(defs: list[Achievement], owned: set[str]) -> str:
    """✅/⬛ line per achievement (sorted by threshold): icon, label — title."""
    lines = []
    for a in sorted(defs, key=lambda d: d.threshold):
        icon = "✅" if a.id in owned else "⬛"
        lines.append(f"{icon} {_achievement_label(a)} — {a.title}")
    return "\n".join(lines)


async def _tracking_value(repo: StatsRepository, uid: int) -> str:
    """Live raw-stat block (v3-style): the numbers the milestones track."""
    voice_h = (await repo.get_activity(uid, "voice_seconds")) / 3600
    messages = await repo.get_activity(uid, "messages")
    reactions = await repo.get_activity(uid, "reactions")
    streak = await repo.get_streak(uid) or {}
    night = await repo.get_night(uid) or {}
    return (f"🎙️ Voice: {voice_h:.1f}h\n"
            f"💬 Nachrichten: {_fmt(messages)}\n"
            f"🔥 Streak: {streak.get('current_streak', 0)} Tage "
            f"(Max: {streak.get('max_streak', 0)})\n"
            f"🌙 Nächte: {night.get('night_count', 0)}\n"
            f"👍 Reaktionen: {_fmt(reactions)}")


async def build_erfolge_detail_embeds(repo: StatsRepository, owned: set[str],
                                      uid: int,
                                      defs: list[Achievement] | None = None
                                      ) -> list[discord.Embed]:
    """v3-style DETAILED checklists: every non-secret achievement with ✅/⬛.

    Two embeds — 🛡️ Gate Achievements (one field per gate type + specials) and
    ⭐ Weitere Achievements (Voice/Streak/Nacht + a count-only 🔒 Secret row).
    Secret achievements are NEVER listed by title, only summarised as a count.
    """
    source = defs if defs is not None else ACHIEVEMENTS

    gate_embed = discord.Embed(title="🛡️ Gate Achievements",
                               color=discord.Color.blue())
    for gtype, gname in GATE_NAMES.items():
        tier = [a for a in source if a.metric == f"gate_{gtype}"]
        if tier:
            gate_embed.add_field(name=f"{gname} Gate",
                                 value=_checklist(tier, owned), inline=True)
    specials = [a for a in source
                if a.metric in ("gate_total", "gate_cost_total")]
    if specials:
        gate_embed.add_field(name="⭐ Spezial",
                             value=_checklist(specials, owned), inline=False)

    weitere = discord.Embed(title="⭐ Weitere Achievements",
                            color=discord.Color.purple())
    for category, label in (("voice", "🎙️ Voice-Zeit"),
                            ("streak", "🔥 Login-Streak"),
                            ("night", "🌙 Nachtaktivität")):
        cat_defs = [a for a in source if a.category == category and not a.secret]
        if cat_defs:
            weitere.add_field(name=label, value=_checklist(cat_defs, owned),
                              inline=True)
    secret_total = sum(1 for a in source if a.secret)
    secret_unlocked = len(owned & {a.id for a in source if a.secret})
    weitere.add_field(name="🔒 Secret",
                      value=f"{secret_unlocked}/{secret_total} freigeschaltet  ???",
                      inline=False)
    return [gate_embed, weitere]


async def _build_erfolge_embed(repo: StatsRepository, owned: set[str], uid: int,
                               display_name: str,
                               defs: list[Achievement] | None = None,
                               total: int | None = None,
                               avatar_url: str | None = None) -> discord.Embed:
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
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)
    embed.add_field(name="📊 Tracking",
                    value=await _tracking_value(repo, uid), inline=False)

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
        lines = [f"{len(unlocked)}/{len(cat_defs)}  {bar}", nxt_text]
        if unlocked:
            cap = 8
            titles = [a.title for a in unlocked[:cap]]
            titles_text = ", ".join(titles)
            if len(unlocked) > cap:
                titles_text += ", …"
            lines.append(titles_text)
        embed.add_field(name=label, value="\n".join(lines), inline=False)

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
        defs = bot.achievement_defs.all()
        avatar = getattr(getattr(interaction.user, "display_avatar", None),
                         "url", None)
        summary = await _build_erfolge_embed(
            repo, owned, interaction.user.id, interaction.user.display_name,
            defs=defs, total=bot.achievement_defs.total, avatar_url=avatar)
        details = await build_erfolge_detail_embeds(
            repo, owned, interaction.user.id, defs=defs)
        embeds = [summary, *details]
        try:
            for e in embeds:
                await interaction.user.send(embed=e)
            await interaction.response.send_message(
                "📬 Ich hab dir deine Erfolge per DM geschickt.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(embeds=embeds, ephemeral=True)
