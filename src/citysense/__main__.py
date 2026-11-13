"""Entry point of the loader container, started with `python -m citysense`."""

import logging
import sys

from citysense.config import ConfigError, Settings
from citysense.loader import run
from citysense.store import Store, connect

EXIT_CONFIGURATION = 2


def main():
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        settings = Settings.from_environment()
    except ConfigError as error:
        logging.getLogger("citysense").error("invalid setting: %s", error)
        return EXIT_CONFIGURATION

    client = connect(settings.uri)
    try:
        return run(settings, Store(client[settings.database]))
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
