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


def build_reminder_mentions(opted_in_ids: list) -> str:
    """A ping line for the opted-in users, or '' when nobody is signed up."""
    if not opted_in_ids:
        return ""
    return "\n\n" + EVENT_EMOJI + " " + " ".join(f"<@{uid}>" for uid in opted_in_ids)


def _event_channel(bot):
    return bot.get_channel(bot.runtime_config.event_reminder_channel_id)


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
