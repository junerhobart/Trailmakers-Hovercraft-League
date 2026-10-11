import json
from urllib.parse import urlparse

from workers import Response, WorkerEntrypoint

from discord_interactions import verify_signature

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

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_BODY_BYTES:
                    return json_response({"error": "Request body is too large."}, status=413)
            except ValueError:
                return json_response({"error": "Invalid content length."}, status=400)

        body = await request.text()
        if len(body.encode("utf-8")) > MAX_BODY_BYTES:
            return json_response({"error": "Request body is too large."}, status=413)

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
