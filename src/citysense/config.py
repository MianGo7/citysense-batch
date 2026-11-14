"""Settings read from environment variables, each with a default.

The defaults suit a run on the host against the port that Compose publishes. The
Compose file overrides the connection string, because inside the network the
nodes are reached through the replica set and not through the loopback address.
Retry delays and the server selection timeout are constants of the loader and
not settings, because their values follow from the measured duration of an
election (ADR-0012) and a deployment has no reason to change them.
"""

import os
import tomllib
from dataclasses import dataclass

from citysense.alert_rules import ThresholdError, Thresholds

DEFAULT_URI = "mongodb://127.0.0.1:27017/?directConnection=true"
DEFAULT_DATABASE = "citysense"
DEFAULT_SOURCE = "data/raw/iot_telemetry_data.csv.gz"
DEFAULT_CHUNK_SIZE = 5000
DEFAULT_THRESHOLDS = "config/alert_thresholds.toml"


class ConfigError(ValueError):
    """A setting has a value that cannot be used."""


@dataclass(frozen=True)
class Settings:
    """Everything the loader needs to know from its environment.

    `max_batches` limits how many batches one run loads. A batch that is already
    completed and therefore skipped does not count, so that a run with the limit
    3 followed by a second run with the same limit loads the following three
    days. None means every batch. The limit also simulates the daily arrival of
    data, which the integration tests use instead of a hook in the loader.
    """

    uri: str = DEFAULT_URI
    database: str = DEFAULT_DATABASE
    source: str = DEFAULT_SOURCE
    chunk_size: int = DEFAULT_CHUNK_SIZE
    max_batches: int | None = None
    thresholds_file: str = DEFAULT_THRESHOLDS

    @classmethod
    def from_environment(cls, environ=None):
        environ = os.environ if environ is None else environ
        return cls(
            uri=_text(environ, "MONGODB_URI", DEFAULT_URI),
            database=_text(environ, "DATABASE", DEFAULT_DATABASE),
            source=_text(environ, "SOURCE_FILE", DEFAULT_SOURCE),
            chunk_size=_positive(environ, "CHUNK_SIZE", DEFAULT_CHUNK_SIZE),
            max_batches=_positive(environ, "MAX_BATCHES", None),
            thresholds_file=_text(environ, "ALERT_THRESHOLDS", DEFAULT_THRESHOLDS),
        )


def _text(environ, name, default):
    value = environ.get(name, "").strip()
    return value or default


def _positive(environ, name, default):
    raw = environ.get(name, "").strip()
    if not raw:
        return default
    # The pattern is checked first, because int() alone would accept a sign.
    if not (raw.isascii() and raw.isdecimal()) or int(raw) < 1:
        raise ConfigError(f"{name} must be a positive whole number, got {raw!r}")
    return int(raw)


def load_thresholds(path):
    """Read the limits of the alerts from a TOML file and check them.

    The standard library reads the format, so no dependency is added. A file that
    is missing, is not valid TOML or holds a value that cannot be used stops the
    loader before it touches the database, because an alert rule that is silently
    off is worse than a loader that does not start.
    """
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
        return Thresholds.from_mapping(data)
    except (OSError, tomllib.TOMLDecodeError, ThresholdError) as error:
        raise ConfigError(f"the alert limits in {path} cannot be used: {error}") from error
