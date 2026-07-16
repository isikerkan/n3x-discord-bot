"""Admin-gated `!content` prefix commands that edit `content_texts` DB
overrides and refresh the live resolver so narrative copy changes apply without
a restart.

Structural mirror of `config_commands.register_config_commands`: module-level
funcs, no cog, `is_admin` gate, `delete_after=5`, refresh-after-write.
"""
from n3x_bot.admin import is_admin
from n3x_bot.config import Settings
from n3x_bot.content import CONTENT_KEYS
from n3x_bot.storage.base import StatsRepository

# Template keys are `.format(...)`-ed at their read-sites (welcome.py,
# bot._announce_records) with exactly these named placeholders. An override with
# a wrong/missing/positional/malformed placeholder would raise at the read-site,
# where it is silently swallowed — so validate on write. Keys absent here carry
# no placeholders and are never `.format`-ed, so they need no validation.
REQUIRED_PLACEHOLDERS: dict[str, frozenset[str]] = {
    "welcome_dm": frozenset({"mention"}),
    "record_lucky": frozenset({"user", "name", "cost"}),
    "record_unlucky": frozenset({"user", "name", "cost"}),
}


def register_content_commands(bot, repo: StatsRepository, settings: Settings) -> None:
    if bot.get_command("content") is not None:
        return

    @bot.group(name="content", invoke_without_command=True)
    async def content(ctx):
        await ctx.send("Nutze `!content list|show|set|reset ...`.", delete_after=5)

    @content.command(name="list")
    async def list_cmd(ctx):
        if not is_admin(ctx.author, settings):
            await ctx.send("❌ Keine Berechtigung.", delete_after=5)
            return
        overrides = await repo.all_content_texts()
        chunk = ""
        for key in sorted(CONTENT_KEYS):
            line = f"`{key}`"
            if key in overrides:
                line = f"{line} (Override)"
            if len(chunk) + len(line) + 1 > 1900:
                await ctx.send(chunk)
                chunk = ""
            chunk = f"{chunk}\n{line}" if chunk else line
        if chunk:
            await ctx.send(chunk)

    @content.command(name="show")
    async def show_cmd(ctx, key):
        if not is_admin(ctx.author, settings):
            await ctx.send("❌ Keine Berechtigung.", delete_after=5)
            return
        if key not in CONTENT_KEYS:
            await ctx.send(f"❌ Unbekannter Schlüssel `{key}`.", delete_after=5)
            return
        await ctx.send(f"```\n{bot.content_texts.get(key)}\n```")

    @content.command(name="set")
    async def set_cmd(ctx, key, *, value):
        if not is_admin(ctx.author, settings):
            await ctx.send("❌ Keine Berechtigung.", delete_after=5)
            return
        if key not in CONTENT_KEYS:
            await ctx.send(f"❌ Unbekannter Schlüssel `{key}`.", delete_after=5)
            return
        required = REQUIRED_PLACEHOLDERS.get(key)
        if required is not None:
            try:
                value.format(**{p: "" for p in required})
            except (KeyError, IndexError, ValueError):
                allowed = ", ".join(f"{{{p}}}" for p in sorted(required))
                await ctx.send(
                    f"❌ Ungültige Platzhalter. Erlaubt für `{key}`: {allowed}",
                    delete_after=5)
                return
        await repo.set_content_text(key, value)
        await bot.content_texts.refresh(repo)
        await ctx.send(f"✅ `{key}` gesetzt.", delete_after=5)

    @content.command(name="reset")
    async def reset_cmd(ctx, key):
        if not is_admin(ctx.author, settings):
            await ctx.send("❌ Keine Berechtigung.", delete_after=5)
            return
        if key not in CONTENT_KEYS:
            await ctx.send(f"❌ Unbekannter Schlüssel `{key}`.", delete_after=5)
            return
        await repo.delete_content_text(key)
        await bot.content_texts.refresh(repo)
        await ctx.send(f"✅ Override `{key}` entfernt.", delete_after=5)
