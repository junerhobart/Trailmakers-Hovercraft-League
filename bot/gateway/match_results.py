import asyncio
from dataclasses import dataclass
import json
import logging
from pathlib import Path

import discord


LOGGER = logging.getLogger(__name__)
MAX_PLAYERS_PER_TEAM = 16
MAX_PLAYER_NAME_LENGTH = 32


@dataclass(frozen=True, slots=True)
class MatchResult:
    winner: str
    red_players: tuple[str, ...]
    blue_players: tuple[str, ...]


def parse_match_result(raw: str) -> MatchResult:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("Match result is not valid JSON.") from error

    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("Match result must use version 1.")
    if payload.get("official") is not True:
        raise ValueError("Only official matches can be submitted.")

    winner = payload.get("winner")
    if winner not in {"red", "blue"}:
        raise ValueError("Match winner must be red or blue.")

    def players(name: str) -> tuple[str, ...]:
        values = payload.get(name)
        if not isinstance(values, list) or not values:
            raise ValueError(f"{name} must contain at least one player.")
        if len(values) > MAX_PLAYERS_PER_TEAM:
            raise ValueError(f"{name} contains too many players.")

        clean = []
        for value in values:
            if not isinstance(value, str):
                raise ValueError(f"{name} contains an invalid player name.")
            value = value.strip()
            if (
                not value
                or len(value) > MAX_PLAYER_NAME_LENGTH
                or not value.isprintable()
            ):
                raise ValueError(f"{name} contains an invalid player name.")
            clean.append(value)
        return tuple(clean)

    return MatchResult(
        winner=winner,
        red_players=players("redPlayers"),
        blue_players=players("bluePlayers"),
    )


def format_match_result(result: MatchResult) -> str:
    winner = result.winner.capitalize()
    red = ", ".join(discord.utils.escape_markdown(name) for name in result.red_players)
    blue = ", ".join(
        discord.utils.escape_markdown(name) for name in result.blue_players
    )
    return f"{winner} won\nRed: {red}\nBlue: {blue}"


class MatchResultRelay:
    def __init__(
        self,
        client: discord.Client,
        channel_id: int,
        result_file: Path,
        webhook_url: str | None,
    ) -> None:
        self.client = client
        self.channel_id = channel_id
        self.result_file = result_file
        self.webhook_url = webhook_url
        self.stopping = False
        self.rejected_result: str | None = None

    async def run(self) -> None:
        self.result_file.parent.mkdir(parents=True, exist_ok=True)
        LOGGER.info("Watching match results at %s", self.result_file)
        while not self.stopping:
            try:
                await self._send_pending_result()
            except Exception:
                LOGGER.exception("Could not process the pending match result.")
            await asyncio.sleep(1)

    async def _send_pending_result(self) -> None:
        if not self.result_file.is_file():
            return

        raw = await asyncio.to_thread(self.result_file.read_text, encoding="utf-8")
        if raw == self.rejected_result:
            return
        try:
            result = parse_match_result(raw)
        except ValueError as error:
            self.rejected_result = raw
            LOGGER.error("Rejected match result: %s", error)
            return
        self.rejected_result = None
        content = format_match_result(result)

        delivered = await self._send_with_bot(content)
        if not delivered:
            delivered = await self._send_with_webhook(content)
        if not delivered:
            return

        current = await asyncio.to_thread(self.result_file.read_text, encoding="utf-8")
        if current == raw:
            sent_file = self.result_file.with_suffix(self.result_file.suffix + ".sent")
            await asyncio.to_thread(self.result_file.replace, sent_file)

    async def _send_with_bot(self, content: str) -> bool:
        try:
            channel = self.client.get_channel(self.channel_id)
            if channel is None:
                channel = await self.client.fetch_channel(self.channel_id)
            if not isinstance(channel, discord.abc.Messageable):
                raise TypeError("The match destination cannot receive messages.")
            await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
            return True
        except (discord.DiscordException, TypeError):
            LOGGER.exception("Bot delivery failed for a match result.")
            return False

    async def _send_with_webhook(self, content: str) -> bool:
        if not self.webhook_url:
            LOGGER.error("No match-result webhook backup is configured.")
            return False
        try:
            webhook = discord.Webhook.from_url(self.webhook_url, client=self.client)
            await webhook.send(
                content,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        except (discord.DiscordException, ValueError):
            LOGGER.exception("Webhook backup failed for a match result.")
            return False
