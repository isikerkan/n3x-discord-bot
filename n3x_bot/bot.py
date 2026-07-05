import asyncio
import logging
from datetime import datetime, time

import discord
from discord.ext import commands, tasks

from n3x_bot.config import Settings
from n3x_bot.models import render_output
from n3x_bot.storage.base import StatsRepository

log = logging.getLogger("N3X-Bot")

HOME_KEY = "home"


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

    _wire_events(bot, settings, repo)
    return bot


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

    @bot.event
    async def on_command_error(ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"Warte bitte {error.retry_after:.1f} Sekunden.",
                           delete_after=5)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❌ Bitte gib einen Nutzer an.", delete_after=5)
        elif isinstance(error, commands.MemberNotFound):
            await ctx.send("❌ Nutzer nicht gefunden.", delete_after=5)

    @bot.event
    async def on_message(message):
        if message.author == bot.user:
            return
        if message.content.startswith(settings.command_prefix):
            try:
                await message.delete(delay=5.0)
            except Exception:
                pass
        await bot.process_commands(message)

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
