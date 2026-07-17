import asyncio
import importlib.resources as ir
from io import BytesIO

import discord
from PIL import Image, ImageDraw, ImageFont

from n3x_bot.admin import app_is_admin
from n3x_bot.cards import _font_bytes
from n3x_bot.config import Settings
from n3x_bot.nicknames import strip_prefix

_FONT_SIZE_LINE1 = 42
_FONT_SIZE_LINE2 = 72
_FONT_SIZE_LINE3 = 36
_COLOR_WHITE = (255, 255, 255)
_COLOR_GOLD = (255, 215, 0)
_COLOR_GREY = (200, 200, 200)
_GAP = 12

_WELCOME_BG: Image.Image | None = None


def _welcome_bg() -> Image.Image:
    global _WELCOME_BG
    if _WELCOME_BG is None:
        with ir.files("n3x_bot").joinpath("assets/welcome_bg.jpg").open("rb") as f:
            img = Image.open(f).convert("RGBA")
            img.load()
            _WELCOME_BG = img
    return _WELCOME_BG.copy()


def render_welcome_card(display_name: str) -> bytes:
    bg = _welcome_bg()
    draw = ImageDraw.Draw(bg)

    fb = _font_bytes()
    font1 = ImageFont.truetype(BytesIO(fb), _FONT_SIZE_LINE1)
    font2 = ImageFont.truetype(BytesIO(fb), _FONT_SIZE_LINE2)
    font3 = ImageFont.truetype(BytesIO(fb), _FONT_SIZE_LINE3)

    line1 = "Willkommen"
    line2 = display_name
    line3 = "bei"

    def center_x(text: str, font) -> int:
        bbox = draw.textbbox((0, 0), text, font=font)
        return (bg.width // 2) - ((bbox[2] - bbox[0]) // 2)

    def height(text: str, font) -> int:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[3] - bbox[1]

    h1 = height(line1, font1)
    h2 = height(line2, font2)
    h3 = height(line3, font3)

    total_height = h1 + _GAP + h2 + _GAP + h3
    y_start = (bg.height // 2) // 2 - total_height // 2
    y1 = y_start
    y2 = y1 + h1 + _GAP
    y3 = y2 + h2 + _GAP

    draw.text((center_x(line1, font1), y1), line1, font=font1, fill=_COLOR_WHITE)
    draw.text((center_x(line2, font2), y2), line2, font=font2, fill=_COLOR_GOLD)
    draw.text((center_x(line3, font3), y3), line3, font=font3, fill=_COLOR_GREY)

    buf = BytesIO()
    bg.save(buf, format="PNG")
    return buf.getvalue()


async def send_welcome_card(bot, settings: Settings, member) -> bool:
    if getattr(member, "bot", False):
        return False
    channel = bot.get_channel(bot.runtime_config.welcome_channel_id)
    if channel is None:
        return False
    try:
        name = strip_prefix(member.display_name, settings.prefix_str)
        png = render_welcome_card(name)
        await channel.send(
            bot.content_texts.get("welcome_dm").format(mention=member.mention),
            file=discord.File(BytesIO(png), filename=f"welcome_{member.id}.png"))
        return True
    except Exception:
        return False


def register_welcome_commands(bot, settings: Settings) -> None:
    if bot.tree.get_command("sync_welcome") is not None:
        return

    @bot.tree.command(name="sync_welcome",
                      description="Postet eine Willkommenskarte pro Mitglied (Admin).")
    async def sync_welcome_cmd(interaction):
        if not app_is_admin(interaction, settings):
            await interaction.response.send_message(
                "❌ Keine Berechtigung.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        count = 0
        for member in interaction.guild.members:
            if getattr(member, "bot", False):
                continue
            if await send_welcome_card(bot, settings, member):
                count += 1
                await asyncio.sleep(1)
        await interaction.followup.send(
            f"✅ {count} Willkommenskarten verschickt.", ephemeral=True)
