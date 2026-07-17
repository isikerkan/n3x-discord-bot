"""Nick/prefix enforcement, extracted from ``bot.py`` for unit testing.

This is the prefix-enforcement logic lifted out of the inline ``enforce_prefix``
closure in ``_wire_events``. ``desired_nick`` is a pure decision helper (no
Discord, no member object) that returns the target nickname or ``None`` when no
change is needed; ``enforce_nick`` is the thin Discord-side wrapper that runs the
guards, delegates the decision, and edits the member only when required.
"""

from n3x_bot.config import Settings


def strip_prefix(display_name: str, prefix_str: str) -> str:
    if display_name.startswith(prefix_str):
        return display_name[len(prefix_str):].lstrip()
    return display_name


def desired_nick(display_name: str, has_role: bool, prefix_str: str) -> str | None:
    if has_role:
        if display_name.startswith(prefix_str):
            return None
        base = display_name.replace("R3X", "").replace(prefix_str, "").strip()
        return prefix_str + base[:32 - len(prefix_str)]
    if display_name.startswith(prefix_str):
        return display_name[len(prefix_str):].strip()
    return None


async def enforce_nick(member, settings: Settings) -> bool:
    if member.bot or member == member.guild.owner:
        return False
    if not member.guild.me.guild_permissions.manage_nicknames:
        return False
    if member.guild.me.top_role <= member.top_role:
        return False
    has_role = any(r.id in settings.target_role_ids for r in member.roles)
    target = desired_nick(member.display_name, has_role, settings.prefix_str)
    if target is None:
        return False
    reason = "N3X Prefix Enforcement" if has_role else "N3X Prefix Removal"
    try:
        await member.edit(nick=target, reason=reason)
        return True
    except Exception:
        return False
