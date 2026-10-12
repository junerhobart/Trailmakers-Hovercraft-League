import json
import time

from workers import fetch


DISCORD_API_URL = "https://discord.com/api/v10"
LEADERBOARD_CHANNEL_ID = "1558843063101825178"
LEADERBOARD_MESSAGE_KEY = "leaderboard_message_id"
MAX_LEADERBOARD_PLAYERS = 20


def _escape_markdown(value: str) -> str:
    for character in ("\\", "*", "_", "~", "`", "|"):
        value = value.replace(character, f"\\{character}")
    return value


def format_leaderboard(players: list[dict]) -> str:
    lines = ["**Hovercraft Soccer Leaderboard**"]
    if not players:
        lines.append("No rated matches yet.")
    else:
        for position, player in enumerate(players[:MAX_LEADERBOARD_PLAYERS], start=1):
            rating = round(float(player["rating"]))
            wins = int(player["wins"])
            losses = int(player["losses"])
            name = _escape_markdown(str(player["display_name"]))
            lines.append(
                f"`{position:>2}. {rating:>4}  {wins}-{losses}` {name}"
            )
    lines.append(f"Updated <t:{int(time.time())}:R>")
    return "\n".join(lines)


async def _discord_request(token: str, path: str, method: str, body=None):
    response = await fetch(
        f"{DISCORD_API_URL}{path}",
        method=method,
        body=json.dumps(body) if body is not None else None,
        headers={
            "authorization": f"Bot {token}",
            "content-type": "application/json; charset=utf-8",
        },
    )
    text = await response.text()
    if response.status < 200 or response.status >= 300:
        raise RuntimeError(f"Discord returned HTTP {response.status}.")
    return json.loads(text) if text else None


async def replace_leaderboard_message(database, token: str, players: list[dict]):
    current = await database.prepare(
        "SELECT value FROM settings WHERE key = ?"
    ).bind(LEADERBOARD_MESSAGE_KEY).run()
    rows = _rows(current)
    old_message_id = str(rows[0]["value"]) if rows else None

    message = await _discord_request(
        token,
        f"/channels/{LEADERBOARD_CHANNEL_ID}/messages",
        "POST",
        {
            "content": format_leaderboard(players),
            "allowed_mentions": {"parse": []},
        },
    )
    new_message_id = str(message["id"])
    await database.prepare(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
    ).bind(LEADERBOARD_MESSAGE_KEY, new_message_id).run()

    if old_message_id and old_message_id != new_message_id:
        try:
            await _discord_request(
                token,
                f"/channels/{LEADERBOARD_CHANNEL_ID}/messages/{old_message_id}",
                "DELETE",
            )
        except RuntimeError:
            pass
    return new_message_id


def _rows(result) -> list[dict]:
    rows = result.results
    if hasattr(rows, "to_py"):
        rows = rows.to_py()
    converted = []
    for row in rows:
        if hasattr(row, "to_py"):
            row = row.to_py()
        converted.append(dict(row))
    return converted
