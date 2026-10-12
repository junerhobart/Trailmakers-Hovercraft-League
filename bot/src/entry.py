import json
from urllib.parse import urlparse

from workers import Response, WorkerEntrypoint

from discord_interactions import verify_signature
from match_api import (
    MatchConflict,
    MatchError,
    leaderboard,
    publish_leaderboard,
    record_match,
    verify_api_signature,
)

MAX_BODY_BYTES = 1_000_000


def json_response(payload, status=200, headers=None):
    response_headers = {
        "cache-control": "no-store",
        "content-type": "application/json; charset=utf-8",
    }
    if headers:
        response_headers.update(headers)
    return Response(
        json.dumps(payload, separators=(",", ":")),
        status=status,
        headers=response_headers,
    )


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        path = urlparse(request.url).path

        if path == "/" and request.method == "GET":
            return json_response(
                {
                    "service": "trailmakers-hovercraft-league-bot",
                    "status": "ok",
                }
            )

        if path == "/leaderboard" and request.method == "GET":
            if not getattr(self.env, "DB", None):
                return json_response(
                    {"error": "Database is not configured."}, status=503
                )
            return json_response({"players": await leaderboard(self.env.DB)})

        if path in {"/leaderboard", "/matches"}:
            if request.method != "POST":
                return json_response(
                    {"error": "Method not allowed."},
                    status=405,
                    headers={
                        "allow": "GET, POST" if path == "/leaderboard" else "POST"
                    },
                )
            body, error = await read_body(request)
            if error:
                return error
            token = str(getattr(self.env, "DISCORD_TOKEN", ""))
            if not token:
                return json_response({"error": "Service is not configured."}, status=503)
            if not verify_api_signature(
                token,
                request.headers.get("x-hsl-timestamp"),
                request.headers.get("x-hsl-signature"),
                body,
            ):
                return json_response({"error": "Invalid request signature."}, status=401)

            try:
                if path == "/leaderboard":
                    message_id = await publish_leaderboard(self.env)
                    result = {"leaderboardMessageId": message_id}
                else:
                    try:
                        payload = json.loads(body)
                    except json.JSONDecodeError:
                        return json_response({"error": "Invalid JSON."}, status=400)
                    result = await record_match(self.env, payload)
            except MatchConflict as error:
                return json_response({"error": str(error)}, status=409)
            except MatchError as error:
                return json_response({"error": str(error)}, status=400)
            except RuntimeError:
                return json_response(
                    {"error": "Could not update the leaderboard."}, status=502
                )
            return json_response(result, status=200)

        if path != "/interactions":
            return json_response({"error": "Not found."}, status=404)
        if request.method != "POST":
            return json_response(
                {"error": "Method not allowed."},
                status=405,
                headers={"allow": "POST"},
            )

        public_key = getattr(self.env, "DISCORD_PUBLIC_KEY", None)
        if not public_key:
            return json_response({"error": "Service is not configured."}, status=503)

        body, error = await read_body(request)
        if error:
            return error

        signature = request.headers.get("x-signature-ed25519")
        timestamp = request.headers.get("x-signature-timestamp")
        valid = await verify_signature(
            str(public_key),
            str(signature) if signature else None,
            str(timestamp) if timestamp else None,
            body,
        )
        if not valid:
            return json_response({"error": "Invalid request signature."}, status=401)

        try:
            interaction = json.loads(body)
        except json.JSONDecodeError:
            return json_response({"error": "Invalid JSON."}, status=400)
        if not isinstance(interaction, dict):
            return json_response({"error": "Invalid interaction."}, status=400)

        interaction_type = interaction.get("type")
        if interaction_type == 1:
            return json_response({"type": 1})
        if interaction_type == 2:
            return json_response(
                {
                    "type": 4,
                    "data": {
                        "content": "Bot template is connected. Commands come next.",
                        "flags": 64,
                        "allowed_mentions": {"parse": []},
                    },
                }
            )

        return json_response({"error": "Unsupported interaction type."}, status=400)


async def read_body(request):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_BODY_BYTES:
                return None, json_response(
                    {"error": "Request body is too large."}, status=413
                )
        except ValueError:
            return None, json_response({"error": "Invalid content length."}, status=400)

    body = await request.text()
    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        return None, json_response({"error": "Request body is too large."}, status=413)
    return body, None
