import asyncio
import logging
from datetime import datetime, time

import discord
from discord.ext import commands, tasks

from n3x_bot.admin import (
    is_admin,
    admin_create_stat, admin_edit_stat, admin_archive_stat,
    admin_delete_stat, admin_list_stats,
    admin_create_message, admin_edit_message, admin_archive_message,
    admin_delete_message, admin_list_messages,
    register_admin_commands,
)
from n3x_bot.activity import (
    register_activity,
    record_message_activity,
    handle_voice_state_update,
    handle_activity_reaction,
    flush_voice_times,
    now_local,
)
from n3x_bot.achievements import (
    register_achievement_commands, check_achievements,
    post_overview, handle_overview_reaction, sync_all_achievements,
)
from n3x_bot.cards import announce_achievements
from n3x_bot.config import Settings
from n3x_bot.format import format_number
from n3x_bot.gates import build_gate_embed, parse_gate_message
from n3x_bot.models import render_output
from n3x_bot.storage.base import StatsRepository

log = logging.getLogger("N3X-Bot")

HOME_KEY = "home"
GATE_STAT_CHUNK_LIMIT = 1900

# Commands whose only required arg is NOT a member/user. A missing-argument
# error on these gets a generic hint; everything else (the targeted stat
# commands, which take a `member`) is told to specify a user. Covers the gate
# commands and the admin CRUD subcommand tokens.
_GENERIC_ARG_COMMANDS = frozenset(
    {"stat", "del", "admin", "msg", "add", "edit", "archive", "rm", "list"})


async def build_output(repo: StatsRepository, stat_key: str,
                       discord_id: int, display_name: str) -> str:
    user_count, total = await repo.record_use(discord_id, display_name, stat_key)
    stat = await repo.get_stat(stat_key)
    message = None
    if stat.message_id is not None:
        message = await repo.get_message(stat.message_id)
    return render_output(stat, message, display_name, total)


async def build_target_output(repo: StatsRepository, stat_key: str,
                              invoker_id: int, invoker_display: str,
                              target_id: int, target_display: str) -> str:
    """Render output for a targeted stat (e.g. `!smart @user`).

    The invoker's own `user_stats` is updated via `record_use` (as today),
    while the target's separate counter is updated via `record_target_use`.
    The rendered count reflects the TARGET's count, not the invoker's.
    """
    await repo.record_use(invoker_id, invoker_display, stat_key)
    count = await repo.record_target_use(target_id, stat_key)
    stat = await repo.get_stat(stat_key)
    message = None
    if stat.message_id is not None:
        message = await repo.get_message(stat.message_id)
    return render_output(stat, message, invoker_display, count, target_display=target_display)


def build_bot(settings: Settings, repo: StatsRepository) -> commands.Bot:
    intents = discord.Intents.default()
    intents.members = True
    intents.guilds = True
    intents.message_content = True

    bot = commands.Bot(command_prefix=settings.command_prefix,
                       intents=intents, case_insensitive=True)
    bot.n3x_settings = settings
    bot.n3x_repo = repo
    bot._rank_last_posts = {}
    bot._target_last_posts = {}
    bot._gate_embed_msg_id = None
    bot._milestone_cards = {}
    # Single-guild bot (N3X): voice_join_times is keyed by member.id alone.
    # voice_lock serialises the flush task against the leave/move handler so
    # they can't double-count or leave a phantom session behind.
    bot.voice_join_times = {}
    bot.voice_lock = asyncio.Lock()
    bot._overview_state = None

    _wire_events(bot, settings, repo)
    register_gate_commands(bot, repo, settings)
    register_admin_commands(bot, repo, settings)
    register_activity(bot, repo, settings)
    register_achievement_commands(bot, repo, settings)
    register_overview_and_sync_commands(bot, repo, settings)
    return bot


def register_overview_and_sync_commands(bot, repo: StatsRepository,
                                        settings: Settings) -> None:
    if bot.get_command("overview") is None:
        async def _overview_cmd(ctx):
            await post_overview(bot, repo, settings)
        bot.add_command(commands.Command(_overview_cmd, name="overview"))

    if bot.get_command("sync_achievements") is None:
        async def _sync_cmd(ctx):
            if not is_admin(ctx.author, settings):
                await ctx.send("❌ Keine Berechtigung.", delete_after=5)
                return
            summary = await sync_all_achievements(repo)
            await ctx.send(
                f"✅ Sync: {summary['users_processed']} Nutzer, "
                f"{summary['achievements_added']} neue Achievements.")
        bot.add_command(commands.Command(_sync_cmd, name="sync_achievements"))


async def _send_or_update(bot, repo, settings, stat_key: str, text: str):
    channel = bot.get_channel(settings.reminder_channel_id)
    if channel is None:
        return
    last = await repo.get_last_post(stat_key)
    if last is not None:
        old_id, _ = last
        try:
            old = await channel.fetch_message(old_id)
            await old.delete()
        except Exception:
            pass
    new_msg = await channel.send(text)
    await repo.set_last_post(stat_key, new_msg.id, channel.id)


async def _send_ephemeral(bot, settings, store: dict, track_key: str, text: str):
    """Send/update an ephemeral output without touching `stat_last_post`.

    Some display keys are not real stat rows (e.g. `rank_<user_id>`, or a
    per-target key like `smart_<member_id>` for a targeted stat command),
    so they cannot go through `_send_or_update` / `repo.set_last_post` —
    `stat_last_post` is FK-bound to `stats` and would raise `KeyError` for
    an unknown key. Instead, track the last posted message id in-memory in
    `store` for the "delete previous, send new" behavior, since these
    displays don't need DB persistence.
    """
    channel = bot.get_channel(settings.reminder_channel_id)
    if channel is None:
        return
    old_id = store.get(track_key)
    if old_id is not None:
        try:
            old = await channel.fetch_message(old_id)
            await old.delete()
        except Exception:
            pass
    new_msg = await channel.send(text)
    store[track_key] = new_msg.id


async def _send_rank(bot, settings, rank_key: str, text: str):
    await _send_ephemeral(bot, settings, bot._rank_last_posts, rank_key, text)


async def register_stat_commands(bot, repo: StatsRepository, settings: Settings):
    for stat in await repo.list_stats():
        if stat.targeted:
            _add_targeted_stat_command(bot, repo, settings, stat.key)
        else:
            _add_stat_command(bot, repo, settings, stat.key)

    async def _rank(ctx):
        data = await repo.get_user_stats(ctx.author.id)
        if not data:
            text = (f"📊 **Command-Ranking von {ctx.author.display_name}**\n\n"
                    "Du hast bisher noch keine Befehle genutzt!")
        else:
            ordered = sorted(data.items(), key=lambda x: x[1], reverse=True)
            emojis = ["🥇", "🥈", "🥉"]
            text = f"📊 **Command-Ranking von {ctx.author.display_name}**\n\n"
            for i, (cmd, count) in enumerate(ordered):
                pref = emojis[i] if i < 3 else f"{i+1}."
                text += f"{pref} !{cmd:<10} {count}\n"
        await _send_rank(bot, settings, f"rank_{ctx.author.id}", text)

    if bot.get_command("rank") is None:
        bot.add_command(commands.Command(_rank, name="rank"))


def _add_stat_command(bot, repo, settings, key: str):
    if bot.get_command(key) is not None:
        return

    @commands.cooldown(1, 20, commands.BucketType.user)
    async def _cmd(ctx, _key=key):
        text = await build_output(repo, _key, ctx.author.id, ctx.author.display_name)
        await _send_or_update(bot, repo, settings, _key, text)

    bot.add_command(commands.Command(_cmd, name=key))


def _add_targeted_stat_command(bot, repo, settings, key: str):
    """Register a targeted stat command.

    Ordinary targeted stats (e.g. `smart`, `crash`) take an explicit
    `member: discord.Member` argument. `HOME_KEY` is special-cased: it has
    a fixed target (`settings.julez_id`) and takes no argument. If
    `settings.julez_id` is unset (0/falsy), `home` is skipped entirely
    rather than registering a broken command with no valid target.
    """
    if bot.get_command(key) is not None:
        return

    if key == HOME_KEY:
        if not settings.julez_id:
            return

        @commands.cooldown(1, 20, commands.BucketType.user)
        async def _home_cmd(ctx, _key=key):
            text = await build_target_output(
                repo, _key, ctx.author.id, ctx.author.display_name,
                settings.julez_id, f"<@{settings.julez_id}>")
            await _send_or_update(bot, repo, settings, _key, text)

        bot.add_command(commands.Command(_home_cmd, name=key))
        return

    @commands.cooldown(1, 20, commands.BucketType.user)
    async def _tcmd(ctx, member: discord.Member, _key=key):
        text = await build_target_output(
            repo, _key, ctx.author.id, ctx.author.display_name,
            member.id, member.mention)
        # Per-target output isn't a single row `stat_last_post` can track
        # (many possible targets per stat), so use in-memory tracking keyed
        # by "<stat_key>_<member_id>", same pattern as `_send_rank`.
        await _send_ephemeral(bot, settings, bot._target_last_posts,
                              f"{_key}_{member.id}", text)

    bot.add_command(commands.Command(_tcmd, name=key))


# ── gate tracker ─────────────────────────────────────────────────────────────

async def update_gate_stats_embed(bot, repo: StatsRepository, settings: Settings):
    """Refresh (or first-post) the live gate-stats embed.

    The last-posted message id is tracked in-memory on `bot._gate_embed_msg_id`
    (like `_rank_last_posts`/`_target_last_posts`) rather than persisted via
    `repo.set_last_post`, since that's FK-bound to a real `stats` row and
    "gate" isn't one — it would KeyError. Ephemeral tracking means the embed
    re-posts once after a bot restart, which is an acceptable trade-off.
    """
    if not settings.gate_stats_channel_id:
        return
    channel = bot.get_channel(settings.gate_stats_channel_id)
    if channel is None:
        return
    totals = await repo.gate_totals()
    rewards = settings.gate_rewards_map()
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    embed = build_gate_embed(totals, rewards, now_str)
    if bot._gate_embed_msg_id is not None:
        try:
            msg = await channel.fetch_message(bot._gate_embed_msg_id)
            await msg.edit(embed=embed)
            return
        except Exception:
            pass
    new_msg = await channel.send(embed=embed)
    bot._gate_embed_msg_id = new_msg.id


def _chunk_gate_lines(lines: list[str], limit: int = GATE_STAT_CHUNK_LIMIT) -> list[str]:
    chunks = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 2 > limit:
            chunks.append(current)
            current = ""
        current += line + "\n"
    chunks.append(current)
    return chunks


async def _handle_gate_stat(ctx, repo: StatsRepository, settings: Settings, gate_type: str):
    gtype = gate_type.lower()
    if gtype not in settings.gate_rewards_map():
        await ctx.send("Ungültiger Gate-Typ. Bitte nutze a, b oder c.", delete_after=5)
        return
    costs = await repo.list_gate_costs(gtype)
    if not costs:
        await ctx.send(f"Noch keine Daten für {gate_type.upper()} Gate vorhanden.", delete_after=5)
        return
    title = f"📊 {gate_type.upper()} Gate"
    lines = [f"{i}. {format_number(cost)}" for i, cost in enumerate(costs, 1)]
    for i, chunk in enumerate(_chunk_gate_lines(lines)):
        embed = discord.Embed(title=title if i == 0 else f"{title} (Fortsetzung)",
                              description=chunk, color=discord.Color.green())
        await ctx.send(embed=embed)


async def _handle_gate_del(ctx, bot, repo: StatsRepository, settings: Settings,
                           gate_type: str, index: int):
    has_role = any(r.id == settings.gate_delete_role_id for r in ctx.author.roles)
    if not has_role:
        await ctx.send("❌ Keine Berechtigung.", delete_after=5)
        return
    gtype = gate_type.lower()
    if await repo.delete_gate_entry(gtype, index):
        await ctx.send(f"✅ Eintrag {index} für {gate_type.upper()} gelöscht.", delete_after=5)
        await update_gate_stats_embed(bot, repo, settings)
    else:
        await ctx.send(f"❌ Eintrag {index} nicht gefunden.", delete_after=5)


def register_gate_commands(bot, repo: StatsRepository, settings: Settings):
    if bot.get_command("stat") is None:
        async def _stat_cmd(ctx, gate_type: str):
            await _handle_gate_stat(ctx, repo, settings, gate_type)
        bot.add_command(commands.Command(_stat_cmd, name="stat"))

    if bot.get_command("del") is None:
        async def _del_cmd(ctx, gate_type: str, index: int):
            await _handle_gate_del(ctx, bot, repo, settings, gate_type, index)
        bot.add_command(commands.Command(_del_cmd, name="del"))


async def handle_gate_input_message(bot, repo: StatsRepository, settings: Settings, message):
    """Parse a message in the configured gate-input channel and react.

    ✅ on a freshly-recorded entry, ⏳ if it was rejected as a duplicate
    within the dedup window, ❌ if the message doesn't parse as a gate entry
    at all (and isn't a bot command, which is left alone).
    """
    parsed = parse_gate_message(message.content)
    if parsed is None:
        if not message.content.startswith(settings.command_prefix):
            try:
                await message.add_reaction("❌")
            except Exception:
                pass
        return
    gate_type, cost = parsed
    inserted = await repo.add_gate_entry(gate_type, cost, message.author.id, message.author.name)
    try:
        await message.add_reaction("✅" if inserted else "⏳")
    except Exception:
        pass
    if inserted:
        await update_gate_stats_embed(bot, repo, settings)
        newly = (await check_achievements(repo, message.author.id, f"gate_{gate_type}")
                 + await check_achievements(repo, message.author.id, "gate_total")
                 + await check_achievements(repo, message.author.id, "gate_cost_total"))
        if newly:
            try:
                await announce_achievements(bot, settings, message.author, newly)
            except Exception:
                pass


def _wire_events(bot, settings: Settings, repo: StatsRepository):
    reminder_h, reminder_m = settings.reminder_hm()

    async def enforce_prefix(member: discord.Member):
        if member.bot or member == member.guild.owner:
            return
        if not member.guild.me.guild_permissions.manage_nicknames:
            return
        if member.guild.me.top_role <= member.top_role:
            return
        has_role = any(r.id == settings.target_role_id for r in member.roles)
        current = member.display_name
        if has_role and not current.startswith(settings.prefix_str):
            base = current.replace("R3X", "").replace(settings.prefix_str, "").strip()
            try:
                await member.edit(nick=f"{settings.prefix_str}{base}"[:32],
                                  reason="N3X Prefix Enforcement")
            except Exception:
                pass
        elif not has_role and current.startswith(settings.prefix_str):
            try:
                await member.edit(nick=current[len(settings.prefix_str):],
                                  reason="N3X Prefix Removal")
            except Exception:
                pass

    @tasks.loop(time=time(hour=reminder_h, minute=reminder_m))
    async def event_reminder_task():
        weekday = datetime.now().weekday()
        channel = bot.get_channel(settings.reminder_channel_id)
        if channel is None:
            return
        if weekday == 2:
            await channel.send("*EVENT REMINDER*: ACE-BALL beginnt in 30 Minuten! @everyone")
        elif weekday == 4:
            await channel.send("*EVENT REMINDER*: Invasion beginnt in 30 Minuten! @everyone")

    @tasks.loop(minutes=5)
    async def voice_flush_task():
        await flush_voice_times(bot, repo, now_local(settings))

    @bot.event
    async def on_ready():
        log.info("Bot eingeloggt als %s", bot.user)
        await register_stat_commands(bot, repo, settings)
        for guild in bot.guilds:
            try:
                members = [m async for m in guild.fetch_members(limit=None)]
            except Exception:
                members = guild.members
            for m in members:
                if not m.bot:
                    await repo.upsert_user(m.id, m.display_name)
                await enforce_prefix(m)
        if not event_reminder_task.is_running():
            event_reminder_task.start()
        for guild in bot.guilds:
            for channel in guild.voice_channels:
                for m in channel.members:
                    if not m.bot:
                        # Idempotent across reconnects: setdefault so a repeat
                        # on_ready doesn't overwrite an existing join time and
                        # drop un-flushed voice seconds.
                        bot.voice_join_times.setdefault(m.id, now_local(settings))
        if not voice_flush_task.is_running():
            voice_flush_task.start()
        if settings.gate_stats_channel_id:
            await update_gate_stats_embed(bot, repo, settings)
        # Publish the /admin ... slash group (and any other app commands) to
        # Discord. Global sync — the tree is only populated locally otherwise,
        # so the slash commands would never appear at runtime.
        await bot.tree.sync()

    @bot.event
    async def on_command_error(ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"Warte bitte {error.retry_after:.1f} Sekunden.",
                           delete_after=5)
        elif isinstance(error, commands.MissingRequiredArgument):
            if ctx.command and ctx.command.name in _GENERIC_ARG_COMMANDS:
                await ctx.send("❌ Fehlendes Argument.", delete_after=5)
            else:
                await ctx.send("❌ Bitte gib einen Nutzer an.", delete_after=5)
        elif isinstance(error, commands.MemberNotFound):
            await ctx.send("❌ Nutzer nicht gefunden.", delete_after=5)
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❌ Ungültiges Argument.", delete_after=5)
        elif (isinstance(error, commands.CommandInvokeError)
              and isinstance(error.original, (ValueError, KeyError))):
            # Admin CRUD helpers raise ValueError/KeyError (duplicate key,
            # message-not-found, unknown key, ...). Surface the reason to the
            # admin instead of failing silently.
            await ctx.send(f"❌ {error.original}", delete_after=5)

    @bot.tree.error
    async def on_app_command_error(interaction, error):
        original = getattr(error, "original", error)
        if isinstance(original, (ValueError, KeyError)):
            msg = f"❌ {original}"
        else:
            msg = "❌ Ein Fehler ist aufgetreten."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    @bot.event
    async def on_message(message):
        if message.author == bot.user:
            return
        author = message.author
        if not getattr(author, "bot", False) and getattr(author, "id", None) is not None:
            newly = await record_message_activity(repo, settings, author.id,
                                                  now_local(settings))
            if newly:
                try:
                    await announce_achievements(bot, settings, author, newly)
                except Exception:
                    pass
        if settings.gate_input_channel_id and message.channel.id == settings.gate_input_channel_id:
            await handle_gate_input_message(bot, repo, settings, message)
        if message.content.startswith(settings.command_prefix):
            try:
                await message.delete(delay=5.0)
            except Exception:
                pass
        await bot.process_commands(message)

    @bot.event
    async def on_voice_state_update(member, before, after):
        await handle_voice_state_update(bot, repo, settings, member, before, after,
                                        now_local(settings))

    @bot.event
    async def on_raw_reaction_add(payload):
        # Both handlers are independent best-effort side-channels; wrap each so a
        # failure in one (e.g. the activity counter) can't skip the other (the
        # overview nav), and vice versa.
        try:
            await handle_activity_reaction(bot, repo, settings, payload)
        except Exception:
            pass
        try:
            await handle_overview_reaction(bot, repo, settings, payload)
        except Exception:
            pass

    @bot.event
    async def on_member_update(before, after):
        if before.roles != after.roles or before.display_name != after.display_name:
            await enforce_prefix(after)

    @bot.event
    async def on_member_join(member):
        if not member.bot:
            await repo.upsert_user(member.id, member.display_name)
        channel = bot.get_channel(settings.welcome_channel_id)
        if channel:
            try:
                await channel.send(
                    f"Willkommen {member.mention} bei N3X - Night Shadow!")
            except Exception:
                pass
        await asyncio.sleep(5)
        await enforce_prefix(member)

    @bot.event
    async def on_member_remove(member):
        if await repo.get_user(member.id) is not None:
            await repo.archive_user(member.id)
