"""Stage 1 of the Group Finder: zone administration, the hub, member zones.

A small in-memory fake guild really creates and deletes roles and channels, so
idempotency, deactivation and role reuse are checked against state rather than
against call counts alone.
"""
import itertools
import os
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from n3x_bot.bot import build_bot
from n3x_bot.config import Settings
from n3x_bot.groupfinder import admin as gf_admin
from n3x_bot.groupfinder import hub, provision, zones
from n3x_bot.groupfinder import register_groupfinder, start_groupfinder
from n3x_bot.seed import seed_defaults
from n3x_bot.storage.json_repo import JsonRepository

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
ADMIN_ROLE = 900
GUILD_ID = 1


def _settings(**overrides) -> Settings:
    kwargs = dict(discord_token="tok", target_role_id=1, welcome_channel_id=2,
                  reminder_channel_id=999, julez_id=424242,
                  admin_role_id=ADMIN_ROLE, _env_file=None,
                  _env_prefix="NONEXISTENT_")
    kwargs.update(overrides)
    return Settings(**kwargs)


async def _repo() -> JsonRepository:
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    repo = JsonRepository(path)
    await repo.connect()
    await seed_defaults(repo)
    return repo


def _not_found():
    return discord.NotFound(MagicMock(status=404, reason="Not Found"), "gone")


# ── fake guild ─────────────────────────────────────────────────────────────

_ids = itertools.count(10_000)


class _Snowflake(SimpleNamespace):
    """Hashable by id, like discord.py's Role/Member — they are used as keys
    in permission-overwrite dicts."""

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        return isinstance(other, _Snowflake) and other.id == self.id


class FakeRole(_Snowflake):
    pass


class FakeGuild:
    def __init__(self):
        self.id = GUILD_ID
        self.roles = {}
        self.channels = {}
        self.default_role = self._role("@everyone", GUILD_ID)
        self.me = _Snowflake(id=5555)
        self._role("Admin", ADMIN_ROLE)
        self.create_category = AsyncMock(side_effect=self._create_category)
        self.create_role = AsyncMock(side_effect=self._create_role)
        self.create_text_channel = AsyncMock(side_effect=self._create_text)

    def _role(self, name, rid=None):
        rid = rid if rid is not None else next(_ids)
        role = FakeRole(id=rid, name=name)
        role.delete = AsyncMock(side_effect=lambda **kw: self.roles.pop(rid, None))
        self.roles[rid] = role
        return role

    def _channel(self, name, kind, category=None, overwrites=None, topic=None):
        cid = next(_ids)
        channel = SimpleNamespace(id=cid, name=name, kind=kind, category=category,
                                  overwrites=overwrites or {}, topic=topic)
        channel.delete = AsyncMock(side_effect=lambda **kw: self.channels.pop(cid, None))
        channel.send = AsyncMock(side_effect=lambda **kw: SimpleNamespace(id=next(_ids)))
        channel.fetch_message = AsyncMock(side_effect=_not_found())
        self.channels[cid] = channel
        return channel

    async def _create_category(self, name, reason=None):
        return self._channel(name, "category")

    async def _create_role(self, name, mentionable=False, reason=None):
        return self._role(name)

    async def _create_text(self, name, category=None, overwrites=None,
                           topic=None, reason=None):
        return self._channel(name, "text", category, overwrites, topic)

    def get_role(self, rid):
        return self.roles.get(rid)

    def get_channel(self, cid):
        return self.channels.get(cid)


def _bot_for(guild):
    bot = MagicMock()
    bot.get_channel = guild.get_channel
    return bot


class FakeMember(SimpleNamespace):
    def __init__(self, guild, mid=42, roles=()):
        super().__init__(id=mid, guild=guild, roles=list(roles))
        self.add_roles = AsyncMock(side_effect=lambda *r, **kw: self.roles.extend(r))
        self.remove_roles = AsyncMock(side_effect=lambda *r, **kw: [
            self.roles.remove(x) for x in r if x in self.roles])


def _interaction(guild, *, admin=True, bot=None):
    it = MagicMock()
    it.guild = guild
    it.user = FakeMember(guild, roles=[guild.roles[ADMIN_ROLE]] if admin else [])
    it.client = bot or _bot_for(guild)
    it.response = MagicMock()
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.response.edit_message = AsyncMock()
    it.followup = MagicMock()
    it.followup.send = AsyncMock()
    it.edit_original_response = AsyncMock()
    return it


def _text(interaction) -> str:
    parts = []
    for mock in (interaction.response.send_message, interaction.followup.send,
                 interaction.response.edit_message):
        for call in mock.await_args_list:
            parts += [str(a) for a in call.args]
            if call.kwargs.get("content"):
                parts.append(call.kwargs["content"])
    return " ".join(parts)


# ── zones (pure) ───────────────────────────────────────────────────────────

async def test_popular_zones_fit_one_select_and_are_valid_and_unique():
    assert len(zones.POPULAR_ZONES) <= zones.SELECT_LIMIT
    assert len(set(zones.POPULAR_ZONES)) == len(zones.POPULAR_ZONES)
    assert all(zones.is_valid_zone(z) for z in zones.POPULAR_ZONES)
    assert "Asia/Karachi" in zones.POPULAR_ZONES


@pytest.mark.parametrize("bad", ["UTC+1", "Europe/Atlantis", "US/Eastern",
                                 "Etc/GMT+1", "", "berlin"])
async def test_invalid_or_alias_zones_are_rejected(bad):
    # IANA ids only, and no aliases/technical zones (Spec: never plain offsets)
    assert not zones.is_valid_zone(bad)


async def test_naming():
    assert zones.channel_name("America/Los_Angeles") == "gf-america-los-angeles"
    assert zones.channel_name("Europe/Berlin") == "gf-europe-berlin"
    assert zones.role_name("Europe/Berlin") == "TZ Europe/Berlin"


async def test_search_matches_spaces_as_underscores_and_ranks_city_prefix():
    assert zones.search_zones("new york", zones.all_zones())[0] == "America/New_York"
    assert zones.search_zones("kar", zones.all_zones())[0] == "Asia/Karachi"
    assert len(zones.search_zones("a", zones.all_zones())) == zones.SELECT_LIMIT


# ── provisioning ───────────────────────────────────────────────────────────

async def test_activate_creates_category_role_and_channel():
    repo, guild = await _repo(), FakeGuild()
    assert await provision.activate_zone(guild, repo, _settings(),
                                         "Europe/Berlin", NOW) == "created"
    row = await repo.gf_get_zone("Europe/Berlin")
    assert row["status"] == zones.ACTIVE
    channel = guild.get_channel(row["channel_id"])
    assert channel.name == "gf-europe-berlin"
    assert guild.get_role(row["role_id"]).name == "TZ Europe/Berlin"
    category_id = int(await repo.gf_get_setting(provision.CATEGORY_KEY))
    assert channel.category is guild.get_channel(category_id)
    assert guild.get_channel(category_id).name == "Group Finder"
    await repo.close()


async def test_zone_channel_permissions():
    repo, guild = await _repo(), FakeGuild()
    await provision.activate_zone(guild, repo, _settings(), "Europe/Berlin", NOW)
    row = await repo.gf_get_zone("Europe/Berlin")
    ow = guild.get_channel(row["channel_id"]).overwrites
    role = guild.get_role(row["role_id"])
    assert ow[guild.default_role].view_channel is False          # hidden
    assert ow[role].view_channel is True                         # zone sees it
    assert ow[role].send_messages is False                       # bot-only posts
    assert ow[role].use_application_commands is True             # /lfg works
    assert ow[guild.roles[ADMIN_ROLE]].view_channel is True      # admins see all
    assert ow[guild.me].send_messages is True                    # bot posts
    await repo.close()


async def test_activate_twice_creates_nothing_new():
    repo, guild = await _repo(), FakeGuild()
    await provision.activate_zone(guild, repo, _settings(), "Europe/Berlin", NOW)
    before = (len(guild.roles), len(guild.channels))
    assert await provision.activate_zone(guild, repo, _settings(),
                                         "Europe/Berlin", NOW) == "exists"
    assert (len(guild.roles), len(guild.channels)) == before
    await repo.close()


async def test_zones_share_one_category():
    repo, guild = await _repo(), FakeGuild()
    await provision.activate_zone(guild, repo, _settings(), "Europe/Berlin", NOW)
    await provision.activate_zone(guild, repo, _settings(), "Asia/Tokyo", NOW)
    assert guild.create_category.await_count == 1
    await repo.close()


async def test_activate_rejects_unknown_zone():
    repo, guild = await _repo(), FakeGuild()
    with pytest.raises(ValueError):
        await provision.activate_zone(guild, repo, _settings(), "Mars/Olympus", NOW)
    guild.create_text_channel.assert_not_awaited()
    await repo.close()


async def test_manual_channel_deletion_deactivates_and_does_not_recreate():
    repo, guild = await _repo(), FakeGuild()
    await provision.activate_zone(guild, repo, _settings(), "Europe/Berlin", NOW)
    row = await repo.gf_get_zone("Europe/Berlin")
    await guild.get_channel(row["channel_id"]).delete()          # by hand

    assert await provision.deactivate_for_deleted_channel(
        repo, row["channel_id"], NOW) == "Europe/Berlin"
    after = await repo.gf_get_zone("Europe/Berlin")
    assert after["status"] == zones.DEACTIVATED
    assert after["channel_id"] is None
    assert after["role_id"] == row["role_id"]                    # kept for reuse
    assert guild.create_text_channel.await_count == 1            # not recreated
    await repo.close()


async def test_reactivation_reuses_the_role():
    repo, guild = await _repo(), FakeGuild()
    await provision.activate_zone(guild, repo, _settings(), "Europe/Berlin", NOW)
    row = await repo.gf_get_zone("Europe/Berlin")
    await guild.get_channel(row["channel_id"]).delete()
    await provision.deactivate_for_deleted_channel(repo, row["channel_id"], NOW)

    assert await provision.activate_zone(guild, repo, _settings(),
                                         "Europe/Berlin", NOW) == "reactivated"
    again = await repo.gf_get_zone("Europe/Berlin")
    assert again["role_id"] == row["role_id"]
    assert guild.create_role.await_count == 1                    # no duplicate role
    assert again["status"] == zones.ACTIVE
    await repo.close()


async def test_delete_zone_removes_channel_role_and_members():
    repo, guild = await _repo(), FakeGuild()
    await provision.activate_zone(guild, repo, _settings(), "Europe/Berlin", NOW)
    row = await repo.gf_get_zone("Europe/Berlin")
    await repo.gf_set_member_zone(42, "Europe/Berlin", NOW)

    assert await provision.delete_zone(guild, repo, "Europe/Berlin", NOW) is True
    assert guild.get_channel(row["channel_id"]) is None
    assert guild.get_role(row["role_id"]) is None
    after = await repo.gf_get_zone("Europe/Berlin")
    assert after["status"] == zones.DEACTIVATED
    assert after["channel_id"] is None and after["role_id"] is None
    assert await repo.gf_get_member_zone(42) is None
    # the delete event that Discord then fires is a no-op
    assert await provision.deactivate_for_deleted_channel(
        repo, row["channel_id"], NOW) is None
    await repo.close()


async def test_delete_unknown_zone_returns_false():
    repo, guild = await _repo(), FakeGuild()
    assert await provision.delete_zone(guild, repo, "Asia/Tokyo", NOW) is False
    await repo.close()


async def test_reconcile_deactivates_channels_deleted_while_offline():
    repo, guild = await _repo(), FakeGuild()
    await provision.activate_zone(guild, repo, _settings(), "Europe/Berlin", NOW)
    await provision.activate_zone(guild, repo, _settings(), "Asia/Tokyo", NOW)
    tokyo = await repo.gf_get_zone("Asia/Tokyo")
    guild.channels.pop(tokyo["channel_id"])                      # gone, no event

    assert await provision.reconcile_zones(_bot_for(guild), repo, NOW) == ["Asia/Tokyo"]
    assert (await repo.gf_get_zone("Asia/Tokyo"))["status"] == zones.DEACTIVATED
    assert (await repo.gf_get_zone("Europe/Berlin"))["status"] == zones.ACTIVE
    await repo.close()


async def test_hub_channel_is_visible_to_everyone_but_read_only():
    repo, guild = await _repo(), FakeGuild()
    hub_channel = await provision.ensure_hub_channel(guild, repo, _settings())
    assert hub_channel.name == "group-finder"
    ow = hub_channel.overwrites
    assert ow[guild.default_role].view_channel is True
    assert ow[guild.default_role].send_messages is False
    assert await provision.ensure_hub_channel(guild, repo, _settings()) is hub_channel
    await repo.close()


async def test_deleted_category_and_hub_are_forgotten():
    repo, guild = await _repo(), FakeGuild()
    hub_channel = await provision.ensure_hub_channel(guild, repo, _settings())
    category_id = int(await repo.gf_get_setting(provision.CATEGORY_KEY))
    await provision.deactivate_for_deleted_channel(repo, hub_channel.id, NOW)
    await provision.deactivate_for_deleted_channel(repo, category_id, NOW)
    assert await repo.gf_get_setting(provision.HUB_CHANNEL_KEY) is None
    assert await repo.gf_get_setting(provision.CATEGORY_KEY) is None
    await repo.close()


# ── hub ────────────────────────────────────────────────────────────────────

async def test_hub_embed_is_english_and_explains_the_flow():
    embed = hub.build_hub_embed(["Europe/Berlin"])
    for fragment in ("timezone", "/lfg", "15 minutes", "/timezone"):
        assert fragment in embed.description
    assert "No timezones" in hub.build_hub_embed([]).description


async def test_hub_view_chunks_zones_into_selects_of_25():
    repo = await _repo()
    view = hub.HubView(repo, _settings(), [f"Z/{i}" for i in range(30)])
    assert [len(c.options) for c in view.children] == [25, 5]
    capped = hub.HubView(repo, _settings(), [f"Z/{i}" for i in range(200)])
    assert len(capped.children) == hub.MAX_SELECTS
    await repo.close()


async def test_hub_router_view_is_persistent_with_fixed_ids():
    repo = await _repo()
    router = hub.HubView(repo, _settings())
    assert router.timeout is None
    assert [c.custom_id for c in router.children] == [
        f"n3x:gf:zone:{i}" for i in range(hub.MAX_SELECTS)]
    await repo.close()


async def _hub_setup():
    repo, guild = await _repo(), FakeGuild()
    await provision.ensure_hub_channel(guild, repo, _settings())
    await provision.activate_zone(guild, repo, _settings(), "Europe/Berlin", NOW)
    hub_channel = guild.get_channel(
        int(await repo.gf_get_setting(provision.HUB_CHANNEL_KEY)))
    return repo, guild, hub_channel


async def test_update_hub_posts_and_tracks_the_message():
    repo, guild, hub_channel = await _hub_setup()
    await hub.update_hub(_bot_for(guild), repo, _settings())
    hub_channel.send.assert_awaited_once()
    view = hub_channel.send.call_args.kwargs["view"]
    assert [o.value for o in view.children[0].options] == ["Europe/Berlin"]
    assert (await repo.get_channel_message(hub.HUB_MESSAGE_KEY))[1] == hub_channel.id
    await repo.close()


async def test_update_hub_edits_existing_message():
    repo, guild, hub_channel = await _hub_setup()
    message = SimpleNamespace(id=777, edit=AsyncMock())
    await repo.set_channel_message(hub.HUB_MESSAGE_KEY, 777, hub_channel.id)
    hub_channel.fetch_message = AsyncMock(return_value=message)
    await hub.update_hub(_bot_for(guild), repo, _settings())
    message.edit.assert_awaited_once()
    hub_channel.send.assert_not_awaited()
    await repo.close()


async def test_deleted_hub_message_is_reposted():
    repo, guild, hub_channel = await _hub_setup()
    await repo.set_channel_message(hub.HUB_MESSAGE_KEY, 777, hub_channel.id)
    hub_channel.fetch_message = AsyncMock(side_effect=_not_found())
    await hub.update_hub(_bot_for(guild), repo, _settings())
    hub_channel.send.assert_awaited_once()
    assert (await repo.get_channel_message(hub.HUB_MESSAGE_KEY))[0] != 777
    await repo.close()


async def test_hub_never_reposts_on_other_errors():
    repo, guild, hub_channel = await _hub_setup()
    await repo.set_channel_message(hub.HUB_MESSAGE_KEY, 777, hub_channel.id)
    hub_channel.fetch_message = AsyncMock(side_effect=RuntimeError("503"))
    await hub.update_hub(_bot_for(guild), repo, _settings())
    hub_channel.send.assert_not_awaited()
    await repo.close()


async def test_update_hub_without_hub_channel_is_a_noop():
    repo, guild = await _repo(), FakeGuild()
    await hub.update_hub(_bot_for(guild), repo, _settings())   # must not raise
    await repo.close()


async def test_hub_without_zones_has_no_select():
    repo, guild = await _repo(), FakeGuild()
    hub_channel = await provision.ensure_hub_channel(guild, repo, _settings())
    await hub.update_hub(_bot_for(guild), repo, _settings())
    assert hub_channel.send.call_args.kwargs["view"] is None
    await repo.close()


# ── member zone ────────────────────────────────────────────────────────────

async def test_assign_gives_role_and_stores_zone():
    repo, guild = await _repo(), FakeGuild()
    await provision.activate_zone(guild, repo, _settings(), "Europe/Berlin", NOW)
    role = guild.get_role((await repo.gf_get_zone("Europe/Berlin"))["role_id"])
    member = FakeMember(guild)
    result, _ = await hub.assign_member_zone(repo, member, "Europe/Berlin", NOW)
    assert result == "set"
    assert role in member.roles
    assert await repo.gf_get_member_zone(member.id) == "Europe/Berlin"
    await repo.close()


async def test_switching_zone_replaces_the_role_and_keeps_others():
    repo, guild = await _repo(), FakeGuild()
    for z in ("Europe/Berlin", "Asia/Karachi"):
        await provision.activate_zone(guild, repo, _settings(), z, NOW)
    berlin = guild.get_role((await repo.gf_get_zone("Europe/Berlin"))["role_id"])
    karachi = guild.get_role((await repo.gf_get_zone("Asia/Karachi"))["role_id"])
    unrelated = guild._role("Clan")
    member = FakeMember(guild, roles=[unrelated])
    await hub.assign_member_zone(repo, member, "Europe/Berlin", NOW)
    await hub.assign_member_zone(repo, member, "Asia/Karachi", NOW)
    assert karachi in member.roles and berlin not in member.roles
    assert unrelated in member.roles                              # untouched
    assert await repo.gf_get_member_zone(member.id) == "Asia/Karachi"
    await repo.close()


async def test_picking_the_same_zone_again_is_unchanged():
    repo, guild = await _repo(), FakeGuild()
    await provision.activate_zone(guild, repo, _settings(), "Europe/Berlin", NOW)
    member = FakeMember(guild)
    await hub.assign_member_zone(repo, member, "Europe/Berlin", NOW)
    result, _ = await hub.assign_member_zone(repo, member, "Europe/Berlin", NOW)
    assert result == "unchanged"
    await repo.close()


async def test_inactive_zone_cannot_be_picked():
    repo, guild = await _repo(), FakeGuild()
    member = FakeMember(guild)
    result, _ = await hub.assign_member_zone(repo, member, "Asia/Tokyo", NOW)
    assert result == "inactive"
    member.add_roles.assert_not_awaited()
    await repo.close()


async def test_zone_with_deleted_role_reports_misconfiguration():
    repo, guild = await _repo(), FakeGuild()
    await provision.activate_zone(guild, repo, _settings(), "Europe/Berlin", NOW)
    guild.roles.pop((await repo.gf_get_zone("Europe/Berlin"))["role_id"])
    result, _ = await hub.assign_member_zone(repo, FakeMember(guild),
                                             "Europe/Berlin", NOW)
    assert result == "missing_role"
    await repo.close()


# ── commands ───────────────────────────────────────────────────────────────

def _sub(bot, name):
    return bot.tree.get_command("groupfinder").get_command(name)


async def test_commands_are_registered():
    repo = await _repo()
    bot = build_bot(_settings(), repo)
    group = bot.tree.get_command("groupfinder")
    assert {c.name for c in group.commands} == {
        "setup", "timezone-add", "timezone-delete"}
    assert bot.tree.get_command("timezone") is not None
    await repo.close()


@pytest.mark.parametrize("name,kwargs", [
    ("setup", {}), ("timezone-add", {"zone": "Asia/Tokyo"}),
    ("timezone-delete", {"zone": "Asia/Tokyo"})])
async def test_admin_commands_refuse_non_admins(name, kwargs):
    repo, guild = await _repo(), FakeGuild()
    bot = build_bot(_settings(), repo)
    interaction = _interaction(guild, admin=False)
    await _sub(bot, name).callback(interaction, **kwargs)
    assert "Only admins" in _text(interaction)
    guild.create_text_channel.assert_not_awaited()
    await repo.close()


async def test_setup_offers_only_popular_zones_not_yet_active():
    repo, guild = await _repo(), FakeGuild()
    await provision.activate_zone(guild, repo, _settings(), "Europe/Berlin", NOW)
    embed, view = await gf_admin._setup_payload(repo, _settings())
    offered = [o.value for o in view.children[0].options]
    assert "Europe/Berlin" not in offered
    assert len(offered) == len(zones.POPULAR_ZONES) - 1
    assert "Europe/Berlin" in embed.description                  # shown as active
    await repo.close()


async def test_setup_without_anything_left_to_add_has_no_select():
    repo = await _repo()
    for z in zones.POPULAR_ZONES:
        await repo.gf_save_zone(z, role_id=1, channel_id=2, status=zones.ACTIVE,
                                now=NOW)
    _, view = await gf_admin._setup_payload(repo, _settings())
    assert view is None
    await repo.close()


async def test_setup_select_adds_zones_and_hub():
    repo, guild = await _repo(), FakeGuild()
    bot = _bot_for(guild)
    select = gf_admin.SetupSelect(repo, _settings(), ["Europe/Berlin", "Asia/Tokyo"])
    select._values = ["Europe/Berlin", "Asia/Tokyo"]
    interaction = _interaction(guild, bot=bot)
    await select.callback(interaction)
    assert {z["zone"] for z in await provision.active_zones(repo)} == {
        "Europe/Berlin", "Asia/Tokyo"}
    assert await repo.gf_get_setting(provision.HUB_CHANNEL_KEY) is not None
    await repo.close()


async def test_timezone_add_rejects_invalid_zone():
    repo, guild = await _repo(), FakeGuild()
    bot = build_bot(_settings(), repo)
    interaction = _interaction(guild)
    await _sub(bot, "timezone-add").callback(interaction, zone="UTC+1")
    assert "not a valid timezone" in _text(interaction)
    guild.create_text_channel.assert_not_awaited()
    await repo.close()


async def test_timezone_add_creates_the_zone():
    repo, guild = await _repo(), FakeGuild()
    bot = build_bot(_settings(), repo)
    bot.get_channel = guild.get_channel
    interaction = _interaction(guild, bot=bot)
    await _sub(bot, "timezone-add").callback(interaction, zone="America/Toronto")
    assert (await repo.gf_get_zone("America/Toronto"))["status"] == zones.ACTIVE
    assert "added" in _text(interaction)
    await repo.close()


async def test_timezone_delete_asks_for_confirmation_first():
    repo, guild = await _repo(), FakeGuild()
    bot = build_bot(_settings(), repo)
    await provision.activate_zone(guild, repo, _settings(), "Asia/Tokyo", NOW)
    interaction = _interaction(guild)
    await _sub(bot, "timezone-delete").callback(interaction, zone="Asia/Tokyo")
    view = interaction.response.send_message.call_args.kwargs["view"]
    assert isinstance(view, gf_admin.ConfirmDeleteView)
    assert (await repo.gf_get_zone("Asia/Tokyo"))["status"] == zones.ACTIVE
    await repo.close()


async def test_confirm_delete_removes_the_zone():
    repo, guild = await _repo(), FakeGuild()
    await provision.activate_zone(guild, repo, _settings(), "Asia/Tokyo", NOW)
    view = gf_admin.ConfirmDeleteView(repo, _settings(), "Asia/Tokyo")
    interaction = _interaction(guild)
    await view.confirm.callback(interaction)
    assert (await repo.gf_get_zone("Asia/Tokyo"))["status"] == zones.DEACTIVATED
    await repo.close()


async def test_timezone_delete_unknown_zone():
    repo, guild = await _repo(), FakeGuild()
    bot = build_bot(_settings(), repo)
    interaction = _interaction(guild)
    await _sub(bot, "timezone-delete").callback(interaction, zone="Asia/Tokyo")
    assert "Unknown timezone" in _text(interaction)
    await repo.close()


async def test_timezone_command_only_accepts_active_zones():
    repo, guild = await _repo(), FakeGuild()
    bot = build_bot(_settings(), repo)
    interaction = _interaction(guild, admin=False)
    await bot.tree.get_command("timezone").callback(interaction, zone="Asia/Tokyo")
    assert "not available" in _text(interaction)
    await repo.close()


# ── wiring ─────────────────────────────────────────────────────────────────

async def test_channel_delete_listener_is_added_not_overridden():
    repo = await _repo()
    bot = build_bot(_settings(), repo)
    listeners = bot.extra_events.get("on_guild_channel_delete", [])
    assert len(listeners) == 1                    # via add_listener
    await repo.close()


async def test_channel_delete_listener_deactivates_the_zone():
    repo, guild = await _repo(), FakeGuild()
    bot = build_bot(_settings(), repo)
    bot.get_channel = guild.get_channel
    await provision.activate_zone(guild, repo, _settings(), "Asia/Tokyo", NOW)
    row = await repo.gf_get_zone("Asia/Tokyo")
    listener = bot.extra_events["on_guild_channel_delete"][0]
    await listener(SimpleNamespace(id=row["channel_id"]))
    assert (await repo.gf_get_zone("Asia/Tokyo"))["status"] == zones.DEACTIVATED
    await repo.close()


async def test_start_groupfinder_registers_router_and_reconciles():
    repo, guild = await _repo(), FakeGuild()
    await provision.activate_zone(guild, repo, _settings(), "Asia/Tokyo", NOW)
    row = await repo.gf_get_zone("Asia/Tokyo")
    guild.channels.pop(row["channel_id"])
    bot = _bot_for(guild)
    bot.add_view = MagicMock()
    await start_groupfinder(bot, repo, _settings())
    bot._gf_lifecycle_loop.cancel()       # started by start_groupfinder
    from n3x_bot.groupfinder import views
    registered = {type(c.args[0]) for c in bot.add_view.call_args_list}
    assert registered == {hub.HubView, views.VotingView, views.FixedView}
    assert (await repo.gf_get_zone("Asia/Tokyo"))["status"] == zones.DEACTIVATED
    await repo.close()


async def test_register_is_idempotent():
    repo = await _repo()
    bot = build_bot(_settings(), repo)
    register_groupfinder(bot, repo, _settings())   # second call from tests
    assert bot.tree.get_command("groupfinder") is not None
    await repo.close()
