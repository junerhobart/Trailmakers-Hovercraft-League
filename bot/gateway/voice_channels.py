import asyncio
import logging

import discord
from discord import app_commands

from .config import (
    DEFAULT_VOICE_MEMBERS,
    JOIN_TO_CREATE_CHANNEL_ID,
    MAX_VOICE_MEMBERS,
    TEMP_VOICE_CATEGORY_ID,
)


LOGGER = logging.getLogger(__name__)


async def _reply(interaction: discord.Interaction, message: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


class VoiceChannelManager:
    def __init__(self, client: discord.Client) -> None:
        self.client = client
        self.member_locks: dict[int, asyncio.Lock] = {}

    async def handle_voice_change(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return

        if after.channel and after.channel.id == JOIN_TO_CREATE_CHANNEL_ID:
            lock = self.member_locks.setdefault(member.id, asyncio.Lock())
            try:
                async with lock:
                    await self._move_to_temporary_channel(member)
            finally:
                self.member_locks.pop(member.id, None)

        if before.channel and before.channel != after.channel:
            await asyncio.sleep(0.25)
            await self._delete_if_empty(before.channel)

    async def clean_empty_channels(self, guild: discord.Guild) -> None:
        category = guild.get_channel(TEMP_VOICE_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            LOGGER.error("Temporary voice category %s was not found.", TEMP_VOICE_CATEGORY_ID)
            return
        for channel in tuple(category.voice_channels):
            await self._delete_if_empty(channel)

    async def _move_to_temporary_channel(self, member: discord.Member) -> None:
        existing = self._owned_channel(member, current_only=False)
        if existing:
            await member.move_to(existing, reason="Returning owner to temporary channel")
            return

        category = member.guild.get_channel(TEMP_VOICE_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            LOGGER.error("Temporary voice category %s was not found.", TEMP_VOICE_CATEGORY_ID)
            return

        bot_member = member.guild.me
        if bot_member is None:
            LOGGER.error("The bot member is not available in guild %s.", member.guild.id)
            return

        overwrites = dict(category.overwrites)
        owner_permissions = overwrites.get(member, discord.PermissionOverwrite())
        owner_permissions.view_channel = True
        owner_permissions.connect = True
        owner_permissions.manage_channels = True
        overwrites[member] = owner_permissions

        bot_marker = overwrites.get(bot_member, discord.PermissionOverwrite())
        bot_marker.manage_channels = True
        overwrites[bot_member] = bot_marker

        channel = await category.create_voice_channel(
            name=f"{member.display_name}'s room"[:100],
            user_limit=DEFAULT_VOICE_MEMBERS,
            overwrites=overwrites,
            reason=f"Join-to-create request from {member}",
        )
        try:
            await member.move_to(channel, reason="Join-to-create")
        except discord.DiscordException:
            if not channel.members:
                await channel.delete(reason="Could not complete join-to-create")
            raise

    async def _delete_if_empty(self, channel: discord.abc.GuildChannel) -> None:
        if not isinstance(channel, discord.VoiceChannel):
            return
        if channel.category_id != TEMP_VOICE_CATEGORY_ID or channel.members:
            return
        if not self._is_temporary_channel(channel):
            return
        await channel.delete(reason="Temporary voice channel is empty")

    def _is_temporary_channel(self, channel: discord.VoiceChannel) -> bool:
        bot_user = self.client.user
        if bot_user is None:
            return False
        return any(
            target.id == bot_user.id and overwrite.manage_channels is True
            for target, overwrite in channel.overwrites.items()
        )

    def _channel_owner_id(self, channel: discord.VoiceChannel) -> int | None:
        bot_user_id = self.client.user.id if self.client.user else None
        for target, overwrite in channel.overwrites.items():
            if (
                not isinstance(target, discord.Role)
                and target.id != bot_user_id
                and overwrite.manage_channels is True
            ):
                return target.id
        return None

    def _owned_channel(
        self,
        member: discord.Member,
        *,
        current_only: bool,
    ) -> discord.VoiceChannel | None:
        if current_only:
            voice = member.voice
            channels = [voice.channel] if voice and voice.channel else []
        else:
            category = member.guild.get_channel(TEMP_VOICE_CATEGORY_ID)
            channels = (
                category.voice_channels
                if isinstance(category, discord.CategoryChannel)
                else []
            )

        for channel in channels:
            if not isinstance(channel, discord.VoiceChannel):
                continue
            if self._channel_owner_id(channel) == member.id:
                return channel
        return None

    async def require_owned_channel(
        self, interaction: discord.Interaction
    ) -> discord.VoiceChannel | None:
        if not isinstance(interaction.user, discord.Member):
            await _reply(interaction, "Use this command in the server.")
            return None
        channel = self._owned_channel(interaction.user, current_only=True)
        if channel is None:
            await _reply(interaction, "Join your temporary voice channel first.")
        return channel


def build_voice_commands(manager: VoiceChannelManager) -> app_commands.Group:
    group = app_commands.Group(name="vc", description="Manage your temporary voice channel.")

    @group.command(name="rename", description="Rename your voice channel.")
    async def rename(interaction: discord.Interaction, name: str) -> None:
        channel = await manager.require_owned_channel(interaction)
        if channel is None:
            return
        name = name.strip()
        if not name or len(name) > 100:
            await _reply(interaction, "Use a name between 1 and 100 characters.")
            return
        await channel.edit(name=name, reason=f"Requested by {interaction.user}")
        await _reply(interaction, f"Renamed to {name}.")

    @group.command(name="limit", description="Set the voice-channel size (1-99).")
    async def limit(
        interaction: discord.Interaction,
        people: app_commands.Range[int, 1, MAX_VOICE_MEMBERS],
    ) -> None:
        channel = await manager.require_owned_channel(interaction)
        if channel is None:
            return
        await channel.edit(user_limit=people, reason=f"Requested by {interaction.user}")
        await _reply(interaction, f"Limit set to {people}.")

    @group.command(name="lock", description="Stop new people from joining.")
    async def lock(interaction: discord.Interaction) -> None:
        channel = await manager.require_owned_channel(interaction)
        if channel is None or interaction.guild is None:
            return
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.connect = False
        await channel.set_permissions(
            interaction.guild.default_role,
            overwrite=overwrite,
            reason=f"Locked by {interaction.user}",
        )
        await _reply(interaction, "Locked.")

    @group.command(name="unlock", description="Allow people to join again.")
    async def unlock(interaction: discord.Interaction) -> None:
        channel = await manager.require_owned_channel(interaction)
        if channel is None or interaction.guild is None:
            return
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.connect = None
        await channel.set_permissions(
            interaction.guild.default_role,
            overwrite=None if overwrite.is_empty() else overwrite,
            reason=f"Unlocked by {interaction.user}",
        )
        await _reply(interaction, "Unlocked.")

    @group.command(name="kick", description="Remove someone from your voice channel.")
    async def kick(
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        channel = await manager.require_owned_channel(interaction)
        if channel is None:
            return
        if member == interaction.user:
            await _reply(interaction, "Use /vc close to close your channel.")
            return
        if member.voice is None or member.voice.channel != channel:
            await _reply(interaction, "That person is not in your voice channel.")
            return
        await member.move_to(None, reason=f"Removed by {interaction.user}")
        await _reply(interaction, f"Removed {member.display_name}.")

    @group.command(name="close", description="Close your voice channel.")
    async def close(interaction: discord.Interaction) -> None:
        channel = await manager.require_owned_channel(interaction)
        if channel is None:
            return
        await _reply(interaction, "Closed.")
        await channel.delete(reason=f"Closed by {interaction.user}")

    return group
