from dataclasses import dataclass
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

GUILD_ID = 1558516613861609746
MATCH_CHANNEL_ID = 1558843046332993587
JOIN_TO_CREATE_CHANNEL_ID = 1558843199391277108
TEMP_VOICE_CATEGORY_ID = 1558516799891308614
DEFAULT_VOICE_MEMBERS = 5
MAX_VOICE_MEMBERS = 99
DEFAULT_RATINGS_URL = (
    "https://trailmakers-hovercraft-league-bot.june-hobart.workers.dev"
)


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name:
            os.environ.setdefault(name, value)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required.")
    return value


@dataclass(frozen=True, slots=True)
class Config:
    token: str
    client_id: int
    match_result_file: Path
    match_webhook_url: str | None
    ratings_url: str

    @classmethod
    def from_environment(cls) -> "Config":
        _load_env_file(PROJECT_ROOT / ".env")

        client_id = _required("DISCORD_CLIENT_ID")
        if not client_id.isdigit():
            raise RuntimeError("DISCORD_CLIENT_ID must be a Discord application ID.")

        result_file = os.environ.get("HSL_MATCH_RESULT_FILE", "").strip()
        if result_file:
            match_result_file = Path(result_file).expanduser().resolve()
        else:
            match_result_file = PROJECT_ROOT / "bot" / "runtime" / "match-result.json"

        return cls(
            token=_required("DISCORD_TOKEN"),
            client_id=int(client_id),
            match_result_file=match_result_file,
            match_webhook_url=os.environ.get("MATCH_RESULTS_DISCORD_WEBHOOK_URL"),
            ratings_url=os.environ.get(
                "HSL_RATINGS_URL", DEFAULT_RATINGS_URL
            ).rstrip("/"),
        )
