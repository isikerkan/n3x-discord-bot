from datetime import date, datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from n3x_bot.achievements import ACHIEVEMENTS, Achievement, check_achievements
from n3x_bot.cards import announce_achievements
from n3x_bot.config import Settings
from n3x_bot.storage.base import StatsRepository


# ── pure logic ─────────────────────────────────────────────────────────────

def elapsed_seconds(join_dt: datetime, leave_dt: datetime) -> int:
    # Truncates sub-second remainder (int()); the lost <1s per session is a
    # deliberate, negligible rounding — voice time is a coarse counter.
    return int((leave_dt - join_dt).total_seconds())


def next_streak(prev: dict | None, today: date) -> dict:
    t = today.isoformat()
    if prev is None:
        return {"current_streak": 1, "last_active_date": t, "max_streak": 1}
    if prev["last_active_date"] == t:
        return prev
    last = date.fromisoformat(prev["last_active_date"])
    delta = (today - last).days
    if delta == 1:
        cur = prev["current_streak"] + 1
        mx = max(prev["max_streak"], cur)
        return {"current_streak": cur, "last_active_date": t, "max_streak": mx}
    return {"current_streak": 1, "last_active_date": t, "max_streak": prev["max_streak"]}


def is_night(dt: datetime) -> bool:
    return 0 <= dt.hour < 5


def next_night(prev: dict | None, today: date) -> dict | None:
    t = today.isoformat()
    if prev is None:
        return {"night_count": 1, "last_night_date": t}
    if prev["last_night_date"] == t:
        return prev
    return {"night_count": prev["night_count"] + 1, "last_night_date": t}


def now_local(settings: Settings) -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


def today_local(settings: Settings) -> date:
    return now_local(settings).date()


# ── voice-tier roles ───────────────────────────────────────────────────────

def voice_role_transition(newly_ids: list[str], role_map: dict[str, int],
                          defs: list[Achievement] | None = None
                          ) -> tuple[int | None, list[int]]:
    source = defs if defs is not None else ACHIEVEMENTS
    mapped = [aid for aid in newly_ids if aid in role_map]
    if not mapped:
        return (None, [])
    highest_id = max(
        mapped, key=lambda aid: next(a for a in source if a.id == aid).threshold)
    grant = role_map[highest_id]
    others = [rid for rid in role_map.values() if rid != grant]
    return (grant, others)


async def apply_voice_roles(bot, settings: Settings, member,
                            newly: list[Achievement]) -> None:
    role_map = settings.voice_role_map()
    if not role_map:
        return
    grant_id, other_ids = voice_role_transition(
        [a.id for a in newly], role_map, defs=bot.achievement_defs.all())
    if grant_id is None:
        return
    # Best-effort by contract: if remove_roles raises after add_roles succeeded
    # the member may briefly hold two tiers — acceptable, the next transition
    # reconciles it. We never re-raise (role automation must not break voice).
    try:
        grant_role = member.guild.get_role(grant_id)
        if grant_role is not None:
            await member.add_roles(grant_role)
        held = {r.id for r in member.roles}
        to_remove = [role for rid in other_ids
                     if (role := member.guild.get_role(rid)) is not None and rid in held]
        if to_remove:
            await member.remove_roles(*to_remove)
    except Exception:
        pass


# ── event helpers ──────────────────────────────────────────────────────────

async def record_message_activity(bot, repo: StatsRepository, settings: Settings,
                                   member_id: int, now: datetime) -> list[Achievement]:
    # Intentionally fires on EVERY non-bot message (including command and
    # gate-input posts): the message counter + streak reflect all participation,
    # matching v3 intent. Not scoped to "chat-only" channels by design.
    await repo.add_activity(member_id, "messages", 1)
    today = now.date()
    prev = await repo.get_streak(member_id)
    new = next_streak(prev, today)
    streak_changed = new != prev
    if streak_changed:
        await repo.set_streak(member_id, new["current_streak"],
                              new["last_active_date"], new["max_streak"])
    night_changed = False
    if is_night(now):
        prev_n = await repo.get_night(member_id)
        new_n = next_night(prev_n, today)
        if new_n is not None and new_n != prev_n:
            night_changed = True
            await repo.set_night(member_id, new_n["night_count"],
                                 new_n["last_night_date"])
    # The message counter changes every message, so its check always runs.
    # streak/night change at most once per day: only (re)check when the
    # underlying value actually moved — a same-day repeat message can never
    # unlock a new streak/night tier, so skipping it is behaviour-preserving.
    newly = await check_achievements(repo, member_id, "messages",
                                     defs=bot.achievement_defs.all())
    if streak_changed:
        newly += await check_achievements(repo, member_id, "streak",
                                          defs=bot.achievement_defs.all())
    if night_changed:
        newly += await check_achievements(repo, member_id, "night",
                                          defs=bot.achievement_defs.all())
    return newly


async def handle_voice_state_update(bot, repo: StatsRepository, settings: Settings,
                                    member, before, after, now: datetime) -> None:
    if getattr(member, "bot", False):
        return
    b = before.channel
    a = after.channel
    # `bot.voice_lock` serialises every read/mutation of `voice_join_times`
    # against `flush_voice_times`; without it a leave landing mid-flush-await
    # would double-count the interval and resurrect the popped key as a
    # phantom session. See flush_voice_times for the full rationale.
    credited = False
    async with bot.voice_lock:
        times = bot.voice_join_times
        # NOTE: keyed by member.id alone (single-guild bot — N3X). If the bot
        # ever joined multiple guilds, a per-guild key would be required.
        if b is None and a is not None:
            times[member.id] = now
        elif b is not None and a is None:
            join = times.pop(member.id, None)
            if join is not None:
                secs = elapsed_seconds(join, now)
                if secs > 0:
                    await repo.add_activity(member.id, "voice_seconds", secs)
                    credited = True
        elif b is not None and a is not None and b.id != a.id:
            join = times.pop(member.id, None)
            if join is not None:
                secs = elapsed_seconds(join, now)
                if secs > 0:
                    await repo.add_activity(member.id, "voice_seconds", secs)
                    credited = True
            times[member.id] = now
    if credited:
        newly = await check_achievements(repo, member.id, "voice_seconds",
                                         defs=bot.achievement_defs.all())
        if newly:
            try:
                await announce_achievements(bot, settings, member, newly)
            except Exception:
                pass
            await apply_voice_roles(bot, bot.runtime_config, member, newly)


async def flush_voice_times(bot, repo: StatsRepository, now: datetime) -> None:
    """Credit elapsed voice time for every tracked member, then reset their
    stored join to ``now`` so the next interval starts fresh.

    Guarded by ``bot.voice_lock`` so it cannot interleave with
    ``handle_voice_state_update``. Without the lock, a member leaving during
    the ``add_activity`` await would (1) be credited twice — once here and
    once by the leave handler — and (2) worse, this loop's reset would
    re-insert the just-popped key as a phantom session that accrues time
    forever. The pop-recompute + re-check-before-reset below is a second line
    of defence: a key that vanished mid-iteration is never resurrected.
    """
    credited: list[int] = []
    async with bot.voice_lock:
        for member_id in list(bot.voice_join_times.keys()):
            join = bot.voice_join_times.get(member_id)
            if join is None:
                continue
            secs = elapsed_seconds(join, now)
            if secs > 0:
                await repo.add_activity(member_id, "voice_seconds", secs)
                credited.append(member_id)
            if member_id in bot.voice_join_times:
                bot.voice_join_times[member_id] = now
    # Unlock voice achievements OUTSIDE voice_lock (mirrors the leave path in
    # handle_voice_state_update, keeping the lock cheap). Without this a
    # continuously-connected member crosses a voice threshold via this flush
    # loop but the achievement stays locked until they leave/move.
    settings = bot.n3x_settings
    for member_id in credited:
        newly = await check_achievements(repo, member_id, "voice_seconds",
                                         defs=bot.achievement_defs.all())
        if newly:
            member = None
            for g in bot.guilds:
                member = g.get_member(member_id)
                if member is not None:
                    break
            if member is not None:
                try:
                    await announce_achievements(bot, settings, member, newly)
                except Exception:
                    pass
                await apply_voice_roles(bot, bot.runtime_config, member, newly)


async def handle_activity_reaction(bot, repo: StatsRepository, settings: Settings,
                                   payload) -> None:
    member = getattr(payload, "member", None)
    if member is None or getattr(member, "bot", False):
        return
    if payload.channel_id in (bot.runtime_config.gate_input_channel_id,
                              bot.runtime_config.gate_stats_channel_id,
                              bot.runtime_config.overview_channel_id):
        return
    # Reactions on bot-UI messages (reminder/welcome) still count — a design
    # choice: any reaction is treated as engagement, not filtered by target.
    await repo.add_activity(payload.user_id, "reactions", 1)
    newly = await check_achievements(repo, payload.user_id, "reactions",
                                     defs=bot.achievement_defs.all())
    if newly:
        try:
            await announce_achievements(bot, settings, member, newly)
        except Exception:
            pass


def _format_voice(vsecs: int) -> str:
    return f"{vsecs // 3600}h {vsecs % 3600 // 60}m"


async def _build_activity_embed(repo: StatsRepository, target) -> discord.Embed:
    msgs = await repo.get_activity(target.id, "messages")
    reacts = await repo.get_activity(target.id, "reactions")
    vsecs = await repo.get_activity(target.id, "voice_seconds")
    streak = await repo.get_streak(target.id) or {"current_streak": 0, "max_streak": 0}
    night = await repo.get_night(target.id) or {"night_count": 0}

    embed = discord.Embed(
        title=f"📊 Aktivität von {target.display_name}",
        color=discord.Color.blurple())
    embed.add_field(name="💬 Nachrichten", value=str(msgs))
    embed.add_field(name="👍 Reaktionen", value=str(reacts))
    embed.add_field(name="🎙️ Voice", value=_format_voice(vsecs))
    embed.add_field(name="🔥 Streak",
                    value=f"{streak['current_streak']} (Max {streak['max_streak']})")
    embed.add_field(name="🌙 Nachtaktiv", value=str(night["night_count"]))
    return embed


def register_activity(bot, repo: StatsRepository, settings: Settings) -> None:
    if bot.tree.get_command("activity") is not None:
        return

    @bot.tree.command(name="activity",
                      description="Zeigt die Aktivitätsstatistik.")
    @app_commands.describe(
        member="Nutzer, dessen Aktivität angezeigt werden soll.")
    async def activity(interaction, member: discord.Member | None = None):
        target = member or interaction.user
        embed = await _build_activity_embed(repo, target)
        await interaction.response.send_message(embed=embed)


async def announce_voice_change(bot, member, before, after) -> None:
    """Post a German message when a member joins/leaves/moves a voice channel.

    Best-effort side-channel; posts to `voice_log_channel_id` when configured.
    Bots and pure mute/deafen state changes (same channel) are ignored.
    """
    if getattr(member, "bot", False):
        return
    b = before.channel
    a = after.channel
    if b is a or (b is not None and a is not None and b.id == a.id):
        return  # no channel change (mute/deafen/self-video toggle)
    channel_id = bot.runtime_config.voice_log_channel_id
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if channel is None:
        return
    name = member.display_name
    if b is None and a is not None:
        text = f"🔊 **{name}** ist **{a.name}** beigetreten."
    elif b is not None and a is None:
        text = f"👋 **{name}** hat **{b.name}** verlassen."
    else:
        mover = await _find_voice_mover(getattr(member, "guild", None), member, a)
        if mover is not None:
            text = (f"🔀 **{name}** wurde von **{mover.display_name}** "
                    f"nach **{a.name}** verschoben.")
        else:
            text = f"🔀 **{name}**: **{b.name}** → **{a.name}**"
    try:
        # The voice log prints plain names, never mentions. `member.display_name`
        # is user-controlled free text — a name like "@everyone"/"<@&role>" would
        # otherwise make the bot ping with its own permissions. Suppress all
        # mentions in the announcement.
        await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


async def _find_voice_mover(guild, member, after_channel):
    """The member who force-moved `member` into `after_channel`, or None.

    `on_voice_state_update` doesn't say who moved someone, so we consult the
    guild audit log for a recent `member_move` action targeting the destination
    channel. `member_move` entries are bulk (no per-member target), so we match
    on destination channel + recency and require the actor ≠ the moved member
    (a self-move produces no audit entry). Needs View-Audit-Log; best-effort.
    """
    if guild is None:
        return None
    try:
        async for entry in guild.audit_logs(
                limit=5, action=discord.AuditLogAction.member_move):
            try:
                age = (discord.utils.utcnow() - entry.created_at).total_seconds()
            except Exception:
                age = None
            if age is not None and age > 15:
                continue
            extra_channel = getattr(entry.extra, "channel", None)
            actor = getattr(entry, "user", None)
            if (extra_channel is not None
                    and getattr(extra_channel, "id", None) == after_channel.id
                    and actor is not None
                    and getattr(actor, "id", None) != member.id):
                return actor
    except Exception:
        return None
    return None
