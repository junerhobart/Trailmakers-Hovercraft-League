import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

from discord_api import replace_leaderboard_message
from ratings import Rating, rating_periods, team_rating, update_rating


MAX_PLAYERS_PER_TEAM = 16
MAX_PLAYER_NAME_LENGTH = 32
AUTH_WINDOW_SECONDS = 300


class MatchError(ValueError):
    pass


class MatchConflict(MatchError):
    pass


def verify_api_signature(secret: str, timestamp: str | None, signature: str | None, body: str) -> bool:
    if not secret or not timestamp or not signature:
        return False
    try:
        request_time = int(timestamp)
    except ValueError:
        return False
    if abs(int(time.time()) - request_time) > AUTH_WINDOW_SECONDS:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}\n{body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def validate_match(payload) -> dict:
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise MatchError("Match result must use version 1.")
    if payload.get("official") is not True:
        raise MatchError("Only official matches are rated.")

    match_id = payload.get("matchId")
    if (
        not isinstance(match_id, str)
        or len(match_id) != 32
        or any(character not in "0123456789abcdef" for character in match_id)
    ):
        raise MatchError("matchId must be 32 lowercase hexadecimal characters.")

    winner = payload.get("winner")
    if winner not in {"red", "blue"}:
        raise MatchError("Match winner must be red or blue.")

    def players(field: str) -> list[dict]:
        values = payload.get(field)
        if not isinstance(values, list) or not values:
            raise MatchError(f"{field} must contain at least one player.")
        if len(values) > MAX_PLAYERS_PER_TEAM:
            raise MatchError(f"{field} contains too many players.")
        result = []
        for value in values:
            if not isinstance(value, str):
                raise MatchError(f"{field} contains an invalid player name.")
            display_name = value.strip()
            if (
                not display_name
                or len(display_name) > MAX_PLAYER_NAME_LENGTH
                or not display_name.isprintable()
            ):
                raise MatchError(f"{field} contains an invalid player name.")
            result.append(
                {"key": display_name.casefold(), "display_name": display_name}
            )
        return result

    red_players = players("redPlayers")
    blue_players = players("bluePlayers")
    if len(red_players) != len(blue_players):
        raise MatchError("Rated matches require equal team sizes.")

    all_keys = [player["key"] for player in red_players + blue_players]
    if len(set(all_keys)) != len(all_keys):
        raise MatchError("A player can appear only once in a match.")

    played_at = payload.get("playedAt")
    if not isinstance(played_at, str):
        raise MatchError("playedAt is required.")
    try:
        parsed_time = datetime.fromisoformat(played_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise MatchError("playedAt must be an ISO-8601 timestamp.") from error
    if parsed_time.tzinfo is None:
        raise MatchError("playedAt must include a timezone.")

    return {
        "match_id": match_id,
        "winner": winner,
        "played_at": parsed_time.astimezone(timezone.utc).isoformat(),
        "red_players": red_players,
        "blue_players": blue_players,
    }


async def leaderboard(database) -> list[dict]:
    result = await database.prepare(
        "SELECT display_name, rating, wins, losses, games_played "
        "FROM players WHERE games_played > 0 "
        "ORDER BY rating DESC, wins DESC, display_name COLLATE NOCASE ASC "
        "LIMIT 25"
    ).run()
    return _rows(result)


async def publish_leaderboard(environment) -> str:
    token = str(getattr(environment, "DISCORD_TOKEN", ""))
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not configured.")
    players = await leaderboard(environment.DB)
    return await replace_leaderboard_message(environment.DB, token, players)


async def record_match(environment, payload) -> dict:
    match = validate_match(payload)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    existing = await environment.DB.prepare(
        "SELECT payload_hash FROM matches WHERE id = ?"
    ).bind(match["match_id"]).run()
    existing_rows = _rows(existing)
    if existing_rows:
        if existing_rows[0]["payload_hash"] != payload_hash:
            raise MatchConflict("matchId already belongs to a different result.")
        message_id = await publish_leaderboard(environment)
        return {
            "matchId": match["match_id"],
            "duplicate": True,
            "leaderboardMessageId": message_id,
        }

    red_players = match["red_players"]
    blue_players = match["blue_players"]
    all_players = red_players + blue_players
    placeholders = ", ".join("(?, ?)" for _ in all_players)
    values = []
    for player in all_players:
        values.extend((player["key"], player["display_name"]))
    await environment.DB.prepare(
        "INSERT INTO players (player_key, display_name) VALUES "
        + placeholders
        + " ON CONFLICT(player_key) DO UPDATE SET display_name = excluded.display_name"
    ).bind(*values).run()

    key_placeholders = ", ".join("?" for _ in all_players)
    current = await environment.DB.prepare(
        "SELECT id, player_key, display_name, rating, rating_deviation, "
        "volatility, wins, losses, games_played, last_played_at "
        "FROM players WHERE player_key IN (" + key_placeholders + ")"
    ).bind(*(player["key"] for player in all_players)).run()
    player_rows = {row["player_key"]: row for row in _rows(current)}
    if len(player_rows) != len(all_players):
        raise RuntimeError("Could not load every match player.")

    red_ratings = [_rating(player_rows[player["key"]]) for player in red_players]
    blue_ratings = [_rating(player_rows[player["key"]]) for player in blue_players]
    red_team = team_rating(red_ratings)
    blue_team = team_rating(blue_ratings)
    now = datetime.now(timezone.utc)

    updates = []
    history = []
    for team, players, own_team, opponent_team in (
        ("red", red_players, red_team, blue_team),
        ("blue", blue_players, blue_team, red_team),
    ):
        won = team == match["winner"]
        for player in players:
            row = player_rows[player["key"]]
            before = _rating(row)
            after = update_rating(
                before,
                own_team,
                opponent_team,
                1.0 if won else 0.0,
                rating_periods(row.get("last_played_at"), now),
            )
            updates.append(
                (
                    player["key"],
                    player["display_name"],
                    after.value,
                    after.deviation,
                    after.volatility,
                    int(row["wins"]) + (1 if won else 0),
                    int(row["losses"]) + (0 if won else 1),
                    int(row["games_played"]) + 1,
                    match["played_at"],
                )
            )
            history.append(
                (
                    match["match_id"],
                    int(row["id"]),
                    team,
                    1 if won else 0,
                    before.value,
                    after.value,
                    before.deviation,
                    after.deviation,
                )
            )

    statements = [
        environment.DB.prepare(
            "INSERT INTO matches "
            "(id, payload_hash, winner, red_players_json, blue_players_json, played_at) "
            "VALUES (?, ?, ?, ?, ?, ?)"
        ).bind(
            match["match_id"],
            payload_hash,
            match["winner"],
            json.dumps([player["display_name"] for player in red_players]),
            json.dumps([player["display_name"] for player in blue_players]),
            match["played_at"],
        )
    ]
    statements.extend(_player_update_statements(environment.DB, updates))
    statements.extend(_history_statements(environment.DB, history))
    await environment.DB.batch(statements)

    message_id = await publish_leaderboard(environment)
    return {
        "matchId": match["match_id"],
        "duplicate": False,
        "leaderboardMessageId": message_id,
    }


def _rating(row: dict) -> Rating:
    return Rating(
        value=float(row["rating"]),
        deviation=float(row["rating_deviation"]),
        volatility=float(row["volatility"]),
    )


def _player_update_statements(database, updates: list[tuple]):
    statements = []
    for chunk in _chunks(updates, 10):
        placeholders = ", ".join("(?, ?, ?, ?, ?, ?, ?, ?, ?)" for _ in chunk)
        values = [value for row in chunk for value in row]
        statements.append(
            database.prepare(
                "INSERT INTO players "
                "(player_key, display_name, rating, rating_deviation, volatility, "
                "wins, losses, games_played, last_played_at) VALUES "
                + placeholders
                + " ON CONFLICT(player_key) DO UPDATE SET "
                "display_name = excluded.display_name, rating = excluded.rating, "
                "rating_deviation = excluded.rating_deviation, "
                "volatility = excluded.volatility, wins = excluded.wins, "
                "losses = excluded.losses, games_played = excluded.games_played, "
                "last_played_at = excluded.last_played_at"
            ).bind(*values)
        )
    return statements


def _history_statements(database, history: list[tuple]):
    statements = []
    for chunk in _chunks(history, 12):
        placeholders = ", ".join("(?, ?, ?, ?, ?, ?, ?, ?)" for _ in chunk)
        values = [value for row in chunk for value in row]
        statements.append(
            database.prepare(
                "INSERT INTO match_players "
                "(match_id, player_id, team, won, rating_before, rating_after, "
                "deviation_before, deviation_after) VALUES " + placeholders
            ).bind(*values)
        )
    return statements


def _chunks(values: list, size: int):
    for index in range(0, len(values), size):
        yield values[index : index + size]


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
