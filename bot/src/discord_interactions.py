import time

from js import TextEncoder, Uint8Array, crypto
from pyodide.ffi import to_js

MAX_TIMESTAMP_AGE_SECONDS = 300


def _uint8_from_hex(value):
    return Uint8Array.new(to_js(list(bytes.fromhex(value))))


async def verify_signature(public_key, signature, timestamp, body):
    if not public_key or not signature or not timestamp:
        return False
    if len(public_key) != 64 or len(signature) != 128:
        return False

    try:
        request_time = int(timestamp)
        if abs(time.time() - request_time) > MAX_TIMESTAMP_AGE_SECONDS:
            return False

        key = await crypto.subtle.importKey(
            "raw",
            _uint8_from_hex(public_key),
            "Ed25519",
            False,
            to_js(["verify"]),
        )
        message = TextEncoder.new().encode(timestamp + body)
        return bool(
            await crypto.subtle.verify(
                "Ed25519",
                key,
                _uint8_from_hex(signature),
                message,
            )
        )
    except (TypeError, ValueError):
        return False
