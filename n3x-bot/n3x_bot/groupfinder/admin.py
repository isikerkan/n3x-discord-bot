"""Admin commands: `/groupfinder setup`, `timezone-add`, `timezone-delete`.

Setup is additive: it lists what is already active and offers the popular zones
that are not. Nothing is ever removed by leaving it unselected; removal is the
explicit `timezone-delete` (or deleting a zone channel by hand, which the
channel-delete listener turns into a deactivation).
"""
import logging

import discord
from discord import app_commands

from n3x_bot.activity import now_local
from n3x_bot.admin import app_is_admin
from datetime import datetime, timezone

from n3x_bot.groupfinder import hub, provision, sync, zones

log = logging.getLogger("N3X-Bot")

_NO_PERMISSION = "❌ Only admins can manage the Group Finder."


def build_setup_embed(active: list[dict]) -> discord.Embed:
    if active:
        lines = [f"• **{z['zone']}** — <#{z['channel_id']}>" for z in active]
        body = "**Active timezones:**\n" + "\n".join(lines)
    else:
        body = "**No timezones are active yet.**"
    body += ("\n\nPick timezones below to **add** them — each gets its own "
             "role and channel. Zones that are not in the list can be added "
             "with `/groupfinder timezone-add`.")
    return discord.Embed(title="🛠️ Group Finder setup", description=body,
                         color=discord.Color.blurple())


async def _setup_payload(repo, settings):
    active = await provision.active_zones(repo)
    active_ids = {z["zone"] for z in active}
    offer = [z for z in zones.POPULAR_ZONES if z not in active_ids]
    view = SetupView(repo, settings, offer) if offer else None
    return build_setup_embed(active), view


async def _activate_many(interaction, repo, settings, zone_ids) -> list[str]:
    now = now_local(settings)
    results = []
    for zone in zone_ids:
        try:
            outcome = await provision.activate_zone(
                interaction.guild, repo, settings, zone, now)
        except Exception:
            log.exception("group finder: activating %s failed", zone)
            outcome = "failed"
        results.append(f"{zone}: {outcome}")
    return results


class SetupSelect(discord.ui.Select):
    def __init__(self, repo, settings, offer):
        self.repo = repo
        self.settings = settings
        super().__init__(
            placeholder="Add timezones…", min_values=1,
            max_values=len(offer),
            options=[discord.SelectOption(label=z, value=z) for z in offer])

    async def callback(self, interaction):
        if not app_is_admin(interaction, self.settings):
            await interaction.response.send_message(_NO_PERMISSION, ephemeral=True)
            return
        # Creating roles and channels takes a few API calls per zone.
        await interaction.response.defer(ephemeral=True)
        await provision.ensure_hub_channel(interaction.guild, self.repo,
                                           self.settings)
        results = await _activate_many(interaction, self.repo, self.settings,
                                       self.values)
        await hub.update_hub(interaction.client, self.repo, self.settings)
        await sync.sync_all(interaction.client, self.repo, self.settings,
                            datetime.now(timezone.utc))
        embed, view = await _setup_payload(self.repo, self.settings)
        try:
            await interaction.edit_original_response(embed=embed, view=view)
        except Exception:
            pass
        await interaction.followup.send("✅ " + " · ".join(results),
                                        ephemeral=True)


class SetupView(discord.ui.View):
    """Ephemeral, only for the admin who ran setup — not persistent."""

    def __init__(self, repo, settings, offer):
        super().__init__(timeout=600)
        self.add_item(SetupSelect(repo, settings, offer[:zones.SELECT_LIMIT]))


class ConfirmDeleteView(discord.ui.View):
    def __init__(self, repo, settings, zone: str):
        super().__init__(timeout=120)
        self.repo = repo
        self.settings = settings
        self.zone = zone

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if not app_is_admin(interaction, self.settings):
            await interaction.response.send_message(_NO_PERMISSION, ephemeral=True)
            return
        deleted = await provision.delete_zone(
            interaction.guild, self.repo, self.zone, now_local(self.settings))
        await hub.update_hub(interaction.client, self.repo, self.settings)
        await interaction.response.edit_message(
            content=(f"🗑️ **{self.zone}** deleted — channel and role removed."
                     if deleted else f"❌ Unknown timezone {self.zone}."),
            view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="Cancelled.", view=None)


def register_groupfinder_admin(bot, repo, settings) -> None:
    if bot.tree.get_command("groupfinder") is not None:
        return
    group = app_commands.Group(name="groupfinder",
                               description="Group Finder administration.")

    async def _any_zone(interaction, current: str):
        return [app_commands.Choice(name=z, value=z)
                for z in zones.search_zones(current, zones.all_zones())]

    async def _tracked_zone(interaction, current: str):
        pool = [z["zone"] for z in await repo.gf_all_zones()]
        return [app_commands.Choice(name=z, value=z)
                for z in zones.search_zones(current, pool)]

    @group.command(name="setup", description="Set up the Group Finder timezones.")
    async def setup(interaction):
        if not app_is_admin(interaction, settings):
            await interaction.response.send_message(_NO_PERMISSION, ephemeral=True)
            return
        embed, view = await _setup_payload(repo, settings)
        await interaction.response.send_message(embed=embed, view=view,
                                                ephemeral=True)

    @group.command(name="timezone-add", description="Add any IANA timezone.")
    @app_commands.describe(zone="IANA timezone, e.g. America/Toronto")
    @app_commands.autocomplete(zone=_any_zone)
    async def timezone_add(interaction, zone: str):
        if not app_is_admin(interaction, settings):
            await interaction.response.send_message(_NO_PERMISSION, ephemeral=True)
            return
        if not zones.is_valid_zone(zone):
            await interaction.response.send_message(
                f"❌ `{zone}` is not a valid timezone.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await provision.ensure_hub_channel(interaction.guild, repo, settings)
        outcome = await provision.activate_zone(interaction.guild, repo, settings,
                                                zone, now_local(settings))
        await hub.update_hub(bot, repo, settings)
        await sync.sync_all(bot, repo, settings, datetime.now(timezone.utc))
        await interaction.followup.send(
            {"exists": f"ℹ️ **{zone}** is already active.",
             "created": f"✅ **{zone}** added.",
             "reactivated": f"✅ **{zone}** re-activated."}[outcome],
            ephemeral=True)

    @group.command(name="timezone-delete",
                   description="Delete a timezone's channel and role.")
    @app_commands.describe(zone="Timezone to delete")
    @app_commands.autocomplete(zone=_tracked_zone)
    async def timezone_delete(interaction, zone: str):
        if not app_is_admin(interaction, settings):
            await interaction.response.send_message(_NO_PERMISSION, ephemeral=True)
            return
        row = await repo.gf_get_zone(zone)
        if row is None:
            await interaction.response.send_message(
                f"❌ Unknown timezone `{zone}`.", ephemeral=True)
            return
        where = f"<#{row['channel_id']}>" if row["channel_id"] else "its channel"
        await interaction.response.send_message(
            f"⚠️ Delete **{zone}**? This removes {where} with its history and "
            f"the role `{zones.role_name(zone)}`.",
            view=ConfirmDeleteView(repo, settings, zone), ephemeral=True)

    bot.tree.add_command(group)
