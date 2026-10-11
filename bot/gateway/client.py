import asyncio
from contextlib import suppress
import logging

import discord
from discord import app_commands

from .config import Config, GUILD_ID, MATCH_CHANNEL_ID
from .match_results import MatchResultRelay
from .voice_channels import VoiceChannelManager, build_voice_commands


LOGGER = logging.getLogger(__name__)
RULES_URL = "https://junerhobart.github.io/trailmakers-hovercraft-league/"


class LeagueBot(discord.Client):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        super().__init__(
            intents=intents,
            application_id=config.client_id,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.config = config
        self.tree = app_commands.CommandTree(self)
        self.voice_channels = VoiceChannelManager(self)
        self.match_results = MatchResultRelay(
            self,
            MATCH_CHANNEL_ID,
            config.match_result_file,
            config.match_webhook_url,
        )
        self.match_result_task: asyncio.Task | None = None

    async def setup_hook(self) -> None:
        guild = discord.Object(id=GUILD_ID)
        self.tree.add_command(build_voice_commands(self.voice_channels), guild=guild)

        @self.tree.command(name="rules", description="Open the league rules.", guild=guild)
        async def rules(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(RULES_URL, ephemeral=True)

        await self.tree.sync(guild=guild)
        self.match_result_task = asyncio.create_task(
            self.match_results.run(),
            name="match-result-relay",
        )

    async def on_ready(self) -> None:
        LOGGER.info("Ready as %s", self.user)
        guild = self.get_guild(GUILD_ID)
        if guild is None:
            LOGGER.error("League guild %s was not found.", GUILD_ID)
            return
        await self.voice_channels.clean_empty_channels(guild)

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        try:
            await self.voice_channels.handle_voice_change(member, before, after)
        except discord.DiscordException:
            LOGGER.exception("Voice-channel update failed for %s.", member)

    async def close(self) -> None:
        self.match_results.stopping = True
        if self.match_result_task:
            self.match_result_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.match_result_task
        await super().close()
