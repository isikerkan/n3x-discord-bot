"""Event-reminder opt-in: users sign up to be pinged for events.

Two ways in, one opt-in list (persisted in `event_optin`):
  * ``/event reminder`` — toggle your own opt-in.
  * ``/event signup`` (admin) — post a 🔔 reaction message; reacting opts in,
    un-reacting opts out.
The daily event-reminder post then @mentions everyone opted in.
"""
import discord
from discord import app_commands

from n3x_bot.admin import app_is_admin
from n3x_bot.config import Settings
from n3x_bot.storage.base import StatsRepository

EVENT_SIGNUP_KEY = "event_signup"
EVENT_EMOJI = "🔔"
_SIGNUP_TEXT = (
    "🔔 **Event-Erinnerungen**\n\n"
    f"Reagiere mit {EVENT_EMOJI}, um für Event-Erinnerungen (z. B. Aceball & "
    "Invasion) gepingt zu werden. Reaktion entfernen = abmelden.\n"
    "Alternativ: `/event reminder` zum An-/Abmelden.")


def build_reminder_mentions(opted_in_ids: list, event_role_id: int = 0) -> str:
    """A ping line for the reminder.

    When an event role is configured, mention the role (covers everyone with
    it, including manually-added members). Otherwise mention the opted-in users
    individually. Empty when there is nobody/nothing to ping.
    """
    if event_role_id:
        return f"\n\n{EVENT_EMOJI} <@&{event_role_id}>"
    if not opted_in_ids:
        return ""
    return "\n\n" + EVENT_EMOJI + " " + " ".join(f"<@{uid}>" for uid in opted_in_ids)


def _event_channel(bot):
    return bot.get_channel(bot.runtime_config.event_reminder_channel_id)


async def _apply_event_role(bot, member, opted_in: bool) -> None:
    """Best-effort: grant/remove the configured event role for `member`."""
    role_id = bot.runtime_config.event_role_id
    if not role_id or member is None:
        return
    role = member.guild.get_role(role_id)
    if role is None:
        return
    try:
        if opted_in:
            await member.add_roles(role, reason="Event-Erinnerung opt-in")
        else:
            await member.remove_roles(role, reason="Event-Erinnerung opt-out")
    except Exception:
        pass


async def handle_event_signup_reaction(bot, repo: StatsRepository, payload,
                                       added: bool) -> None:
    """Opt in/out when a user (un)reacts 🔔 on the persisted signup message."""
    if str(payload.emoji) != EVENT_EMOJI:
        return
    if bot.user is not None and payload.user_id == bot.user.id:
        return  # the bot's own seed reaction
    stored = await repo.get_channel_message(EVENT_SIGNUP_KEY)
    if stored is None or payload.message_id != stored[0]:
        return
    await repo.event_optin_set(payload.user_id, added)
    # Sync the event role only when one is configured. `payload.member` is
    # present on adds; on removes we resolve the member from the guild.
    if bot.runtime_config.event_role_id:
        member = getattr(payload, "member", None)
        if member is None:
            guild = bot.get_guild(getattr(payload, "guild_id", 0))
            member = guild.get_member(payload.user_id) if guild is not None else None
        await _apply_event_role(bot, member, added)


def register_event_commands(bot, repo: StatsRepository, settings: Settings) -> None:
    if bot.tree.get_command("event") is not None:
        return
    group = app_commands.Group(name="event", description="Event-Erinnerungen.")

    @group.command(name="reminder",
                   description="Meldet dich für Event-Erinnerungen an oder ab.")
    async def reminder(interaction):
        uid = interaction.user.id
        now_in = await repo.event_optin_is(uid)
        await repo.event_optin_set(uid, not now_in)
        await _apply_event_role(bot, interaction.user, not now_in)
        if now_in:
            await interaction.response.send_message(
                "🔕 Du bekommst **keine** Event-Erinnerungen mehr.", ephemeral=True)
        else:
            await interaction.response.send_message(
                "🔔 Du bekommst jetzt **Event-Erinnerungen**.", ephemeral=True)

    @group.command(name="signup",
                   description="Postet die Event-Anmeldung mit Reaktion (Admin).")
    async def signup(interaction):
        if not app_is_admin(interaction, settings):
            await interaction.response.send_message(
                "❌ Keine Berechtigung.", ephemeral=True)
            return
        channel = _event_channel(bot)
        if channel is None:
            await interaction.response.send_message(
                "❌ Kein Event-Channel konfiguriert.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        msg = await channel.send(
            _SIGNUP_TEXT, allowed_mentions=discord.AllowedMentions.none())
        await repo.set_channel_message(EVENT_SIGNUP_KEY, msg.id, channel.id)
        try:
            await msg.add_reaction(EVENT_EMOJI)
        except Exception:
            pass
        await interaction.followup.send(
            f"✅ Anmelde-Nachricht in {channel.mention} gepostet.", ephemeral=True)

    bot.tree.add_command(group)
