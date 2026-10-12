import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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
        ratings_url: str,
        ratings_secret: str,
    ) -> None:
        self.client = client
        self.channel_id = channel_id
        self.result_file = result_file
        self.webhook_url = webhook_url
        self.ratings_url = ratings_url
        self.ratings_secret = ratings_secret
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

        file_stat = await asyncio.to_thread(self.result_file.stat)
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

        delivered = await self._submit_rating(result, raw, file_stat.st_mtime_ns)
        if not delivered:
            return

        delivered = await self._send_with_bot(content)
        if not delivered:
            delivered = await self._send_with_webhook(content)
        if not delivered:
            return

        current = await asyncio.to_thread(self.result_file.read_text, encoding="utf-8")
        if current == raw:
            sent_file = self.result_file.with_suffix(self.result_file.suffix + ".sent")
            await asyncio.to_thread(self.result_file.replace, sent_file)

    async def _submit_rating(
        self,
        result: MatchResult,
        raw: str,
        modified_at_ns: int,
    ) -> bool:
        match_id = hashlib.sha256(
            f"{modified_at_ns}\n{raw}".encode("utf-8")
        ).hexdigest()[:32]
        payload = {
            "version": 1,
            "matchId": match_id,
            "official": True,
            "winner": result.winner,
            "redPlayers": list(result.red_players),
            "bluePlayers": list(result.blue_players),
            "playedAt": datetime.fromtimestamp(
                modified_at_ns / 1_000_000_000,
                timezone.utc,
            ).isoformat(),
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        timestamp = str(int(time.time()))
        signature = hmac.new(
            self.ratings_secret.encode("utf-8"),
            timestamp.encode("utf-8") + b"\n" + body,
            hashlib.sha256,
        ).hexdigest()
        request = Request(
            f"{self.ratings_url}/matches",
            data=body,
            method="POST",
            headers={
                "content-type": "application/json; charset=utf-8",
                "x-hsl-timestamp": timestamp,
                "x-hsl-signature": signature,
            },
        )

        def submit() -> None:
            with urlopen(request, timeout=15) as response:
                if response.status < 200 or response.status >= 300:
                    raise RuntimeError(
                        f"Ratings service returned HTTP {response.status}."
                    )
                response.read()

        try:
            await asyncio.to_thread(submit)
            return True
        except (HTTPError, URLError, TimeoutError, RuntimeError):
            LOGGER.exception("Ratings service rejected the match result.")
            return False

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
