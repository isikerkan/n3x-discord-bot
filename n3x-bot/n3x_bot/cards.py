import importlib.resources as ir
from io import BytesIO

import discord
from PIL import Image, ImageDraw, ImageFont

from n3x_bot.achievements import Achievement, GATE_NAMES
from n3x_bot.config import Settings
from n3x_bot.format import format_number

_AVATAR_SIZE = 455
_AVATAR_POS = (-70, None)  # x fixed; y computed from bg height at render time
_FONT_SIZE_SMALL = 46
_FONT_SIZE_HUGE = 143
_FONT_SIZE_MEDIUM = 72

ACTIVITY_CATEGORY_COLORS: dict[str, tuple[int, int, int]] = {
    "voice": (30, 144, 255),
    "streak": (255, 69, 0),
    "night": (148, 0, 211),
    "message": (0, 255, 127),
    "reaction": (255, 165, 0),
}

GATE_TIER_COLORS: list[tuple[str, tuple[int, int, int]]] = [
    ("bronze", (205, 127, 50)),
    ("silber", (192, 192, 192)),
    ("gold", (255, 215, 0)),
    ("platin", (229, 228, 226)),
    ("diamant", (185, 242, 255)),
    ("grandmaster", (148, 0, 211)),
    ("master", (255, 69, 0)),
    ("gott", (255, 0, 0)),
    ("einsteiger", (0, 255, 127)),
    ("profi", (30, 144, 255)),
    ("veteran", (255, 20, 147)),
    ("millionär", (255, 215, 0)),
    ("million", (255, 215, 0)),
]


def _gate_tier_color(title: str) -> tuple[int, int, int]:
    t = title.lower()
    if "grandmaster" in t:
        return (148, 0, 211)
    if "master" in t:
        return (255, 69, 0)
    for key, color in GATE_TIER_COLORS:
        if key in ("grandmaster", "master"):
            continue
        if key in t:
            return color
    return (255, 255, 255)


def tier_color(achievement: Achievement) -> tuple[int, int, int]:
    if achievement.category == "gate":
        return _gate_tier_color(achievement.title)
    return ACTIVITY_CATEGORY_COLORS[achievement.category]


def _milestone_line(achievement: Achievement) -> str:
    metric = achievement.metric
    threshold = achievement.threshold
    if metric in ("gate_a", "gate_b", "gate_c", "gate_d"):
        gtype = metric.split("_")[1]
        return f"{threshold} {GATE_NAMES[gtype]} Gates"
    if metric == "gate_total":
        return "Erster Gate" if threshold == 1 else f"{threshold} Gates Gesamt"
    if metric == "gate_cost_total":
        return f"{format_number(threshold)} Uridium"
    if achievement.category == "voice":
        return f"{threshold // 3600}h Voice"
    if achievement.category == "message":
        return f"{threshold} Nachrichten"
    if achievement.category == "streak":
        return f"{threshold} Tage Streak"
    if achievement.category == "night":
        return f"{threshold} Nächte aktiv"
    if achievement.category == "reaction":
        return f"{threshold} Reaktionen"
    return achievement.title


def card_texts(achievement: Achievement,
               member_display_name: str) -> tuple[str, str, str]:
    return (_milestone_line(achievement), member_display_name, achievement.title)


def render_achievement_card(avatar_bytes: bytes | None, title: str,
                            subtitle: str, footer: str,
                            tier_color: tuple[int, int, int]) -> bytes:
    with ir.files("n3x_bot").joinpath("assets/card_bg.webp").open("rb") as f:
        bg = Image.open(f).convert("RGBA")
    draw = ImageDraw.Draw(bg)

    font_bytes = ir.files("n3x_bot").joinpath(
        "assets/DejaVuSans-Bold.ttf").read_bytes()
    font_s = ImageFont.truetype(BytesIO(font_bytes), _FONT_SIZE_SMALL)
    font_h = ImageFont.truetype(BytesIO(font_bytes), _FONT_SIZE_HUGE)
    font_m = ImageFont.truetype(BytesIO(font_bytes), _FONT_SIZE_MEDIUM)

    avatar = None
    if avatar_bytes is not None:
        try:
            avatar = Image.open(BytesIO(avatar_bytes)).convert("RGBA")
        except Exception:
            avatar = None
    if avatar is None:
        avatar = Image.new("RGBA", (_AVATAR_SIZE, _AVATAR_SIZE),
                           (100, 100, 100, 255))
    avatar = avatar.resize((_AVATAR_SIZE, _AVATAR_SIZE),
                           Image.Resampling.LANCZOS)
    mask = Image.new("L", (_AVATAR_SIZE, _AVATAR_SIZE), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, _AVATAR_SIZE, _AVATAR_SIZE), fill=255)
    avatar.putalpha(mask)
    avatar_x = _AVATAR_POS[0]
    avatar_y = (bg.height // 2) - (_AVATAR_SIZE // 2)
    bg.paste(avatar, (avatar_x, avatar_y), avatar)

    def center_x(text: str, font) -> int:
        bbox = draw.textbbox((0, 0), text, font=font)
        return (bg.width // 2) - ((bbox[2] - bbox[0]) // 2)

    def height(text: str, font) -> int:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[3] - bbox[1]

    y_bottom = 380
    gap1 = 10
    gap2 = 25
    h1 = height(title, font_s)
    h2 = height(subtitle, font_h)
    h3 = height(footer, font_m)
    y_line3 = y_bottom - h3
    y_line2 = y_line3 - gap2 - h2
    y_line1 = y_line2 - gap1 - h1

    draw.text((center_x(title, font_s), y_line1), title,
              font=font_s, fill=(255, 255, 255))
    draw.text((center_x(subtitle, font_h), y_line2), subtitle,
              font=font_h, fill=(255, 255, 255))
    draw.text((center_x(footer, font_m), y_line3), footer,
              font=font_m, fill=tier_color)

    buf = BytesIO()
    bg.save(buf, format="PNG")
    return buf.getvalue()


async def announce_achievements(bot, settings: Settings, member,
                                newly: list[Achievement]) -> None:
    if settings.milestone_channel_id == 0:
        return
    if not newly:
        return
    if getattr(member, "bot", False):
        return
    channel = bot.get_channel(settings.milestone_channel_id)
    if channel is None:
        return

    try:
        avatar_bytes = await member.display_avatar.read()
    except Exception:
        avatar_bytes = None

    store = getattr(bot, "_milestone_cards", None)
    if store is None:
        store = bot._milestone_cards = {}

    for ach in newly:
        title, subtitle, footer = card_texts(ach, member.display_name)
        png = render_achievement_card(avatar_bytes, title, subtitle, footer,
                                      tier_color(ach))

        key = (member.id, ach.category)
        old_id = store.get(key)
        if old_id is not None:
            try:
                old = await channel.fetch_message(old_id)
                await old.delete()
            except Exception:
                pass

        msg = await channel.send(file=discord.File(
            BytesIO(png),
            filename=f"achievement_{member.id}_{ach.category}.png"))
        store[key] = msg.id
