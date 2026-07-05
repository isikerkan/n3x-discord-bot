import asyncio
import logging
from datetime import datetime, time

import discord
from discord.ext import commands, tasks

from n3x_bot.config import Settings
from n3x_bot.models import render_output
from n3x_bot.storage.base import StatsRepository

log = logging.getLogger("N3X-Bot")


async def build_output(repo: StatsRepository, stat_key: str,
                       discord_id: int, display_name: str) -> str:
    user_count, total = await repo.record_use(discord_id, display_name, stat_key)
    stat = await repo.get_stat(stat_key)
    message = None
    if stat.message_id is not None:
        message = await repo.get_message(stat.message_id)
    return render_output(stat, message, display_name, total)


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


async def _send_rank(bot, settings, rank_key: str, text: str):
    """Send/update ephemeral rank output without touching stat_last_post.

    Rank output is not a real stat (there is no `rank_<id>` row in the
    `stats` table), so it cannot go through `_send_or_update` /
    `repo.set_last_post` — the `stat_last_post` table is FK-bound to
    `stats` and would raise `KeyError`. Instead, track the last posted
    message id in-memory on the bot for the "delete previous, send new"
    behavior, since this display doesn't need DB persistence.
    """
    channel = bot.get_channel(settings.reminder_channel_id)
    if channel is None:
        return
    old_id = bot._rank_last_posts.get(rank_key)
    if old_id is not None:
        try:
            old = await channel.fetch_message(old_id)
            await old.delete()
        except Exception:
            pass
    new_msg = await channel.send(text)
    bot._rank_last_posts[rank_key] = new_msg.id


async def register_stat_commands(bot, repo: StatsRepository, settings: Settings):
    for stat in await repo.list_stats():
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
