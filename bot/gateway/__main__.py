import logging

from .client import LeagueBot
from .config import Config


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = Config.from_environment()
    LeagueBot(config).run(config.token, log_handler=None)


if __name__ == "__main__":
    main()
