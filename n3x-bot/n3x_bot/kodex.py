from discord.ext import commands

from n3x_bot.admin import is_admin
from n3x_bot.config import Settings
from n3x_bot.storage.base import StatsRepository

KODEX_EMOJI = "✅"

KODEX_TEXT = (
    "📜 **Verhaltenskodex**\n\n"
    "Bei uns steht Spass an erster Stelle. Humor, dumme Sprüche und freundschaftliche Beleidigungen gehören zum Alltag – solange alle darüber lachen können.\n\n"
    "Respektiert persönliche Grenzen. Humor ist für jeden anders. Wenn euch etwas zu weit geht, sprecht es offen an. Niemand kann Gedanken lesen.\n"
    "Etwas Zurückhaltung schadet nie. Nicht jeder Witz kommt bei jedem gleich an.\n"
    "Tabu sind Witze oder Beleidigungen über Familie, Partner oder Kinder, auch wenn sie nur scherzhaft gemeint sind.\n"
    "Alles andere ist grundsätzlich erlaubt, solange es nicht gegen andere Serverregeln verstösst oder jemandem ernsthaft schadet.\n"
    "Bei Problemen oder Spannungen zögert nicht, jemanden aus der Serverleitung hinzuzuziehen. Wir helfen gerne dabei, Missverständnisse zu klären.\n\n"
    "Am Ende gilt:\n\n"
    "Habt Spass, nehmt nicht alles zu ernst – aber respektiert die Grenzen eurer Mitspieler. ❤️\n\n"
    "Bitte bestätige, dass du den Verhaltenskodex gelesen hast, indem du mit der unten angegebenen Reaktion auf diese Nachricht reagierst. Erst danach gilt der Kodex als bestätigt."
)


async def send_kodex_dm(bot, repo: StatsRepository, member) -> None:
    if getattr(member, "bot", False):
        return
    try:
        msg = await member.send(KODEX_TEXT)
        # Persist the mapping BEFORE seeding the reaction: if add_reaction hits a
        # rate limit the member can still confirm manually and be recorded. The
        # save stays after a successful send, so a closed-DM failure records
        # nothing. Best-effort — a failure here must not abort the bulk loop.
        await repo.save_kodex_message(msg.id, member.id)
        await msg.add_reaction(KODEX_EMOJI)
    except Exception:
        return


async def handle_kodex_confirmation(bot, repo: StatsRepository, payload) -> None:
    if str(payload.emoji) != KODEX_EMOJI:
        return
    user_id = await repo.get_kodex_message_user(payload.message_id)
    # Only the tracked member may confirm their own kodex DM. This also excludes
    # the bot's own seed reaction (add_reaction in send_kodex_dm), which Discord
    # dispatches back here — without this guard every member would be
    # auto-confirmed the instant the DM is sent.
    if user_id is None or payload.user_id != user_id:
        return
    await repo.confirm_kodex(user_id)


def build_kodex_report(confirmed: set[int], members: list) -> list[str]:
    chunks: list[str] = []
    buffer = ""
    for m in members:
        line = f"{'✅' if m.id in confirmed else '❌'} {m.mention} — {m.display_name}"
        if buffer and len(buffer) + 1 + len(line) > 1900:
            chunks.append(buffer)
            buffer = line
        else:
            buffer = f"{buffer}\n{line}" if buffer else line
    if buffer:
        chunks.append(buffer)
    return chunks


def register_kodex_commands(bot, repo: StatsRepository, settings: Settings) -> None:
    if bot.get_command("kodex") is not None:
        return

    async def _kodex_cmd(ctx):
        if not is_admin(ctx.author, settings):
            await ctx.send("❌ Keine Berechtigung.", delete_after=5)
            return
        for member in ctx.guild.members:
            await send_kodex_dm(bot, repo, member)
        await ctx.send("✅ Kodex verschickt.", delete_after=5)

    async def _kodex_check_cmd(ctx):
        if not is_admin(ctx.author, settings):
            await ctx.send("❌ Keine Berechtigung.", delete_after=5)
            return
        confirmed = await repo.list_kodex_confirmed()
        members = [m for m in ctx.guild.members if not getattr(m, "bot", False)]
        chunks = build_kodex_report(confirmed, members)
        channel = bot.get_channel(settings.kodex_check_channel_id)
        if channel is not None:
            for chunk in chunks:
                await channel.send(chunk)

    bot.add_command(commands.Command(_kodex_cmd, name="kodex"))
    bot.add_command(commands.Command(_kodex_check_cmd, name="kodex_check"))
