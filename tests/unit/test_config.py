"""Unit tests of the settings that the loader reads from its environment."""

from pathlib import Path

import pytest

from citysense.config import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_DATABASE,
    DEFAULT_SOURCE,
    DEFAULT_THRESHOLDS,
    DEFAULT_URI,
    ConfigError,
    Settings,
    load_thresholds,
)

ROOT = Path(__file__).resolve().parents[2]


def test_an_empty_environment_gives_the_defaults():
    settings = Settings.from_environment({})
    assert settings == Settings(DEFAULT_URI, DEFAULT_DATABASE, DEFAULT_SOURCE, DEFAULT_CHUNK_SIZE)
    assert settings.max_batches is None


def test_the_default_chunk_size_is_5000():
    assert DEFAULT_CHUNK_SIZE == 5000


def test_every_setting_can_be_changed():
    settings = Settings.from_environment(
        {
            "MONGODB_URI": "mongodb://a:27017/?replicaSet=rs0",
            "DATABASE": "other",
            "SOURCE_FILE": "/data/other.csv",
            "CHUNK_SIZE": "100",
            "MAX_BATCHES": "3",
        }
    )
    assert settings == Settings(
        "mongodb://a:27017/?replicaSet=rs0", "other", "/data/other.csv", 100, 3
    )


def test_an_empty_value_means_the_default():
    # Compose passes an unset variable as an empty string.
    settings = Settings.from_environment({"MAX_BATCHES": "", "CHUNK_SIZE": " ", "DATABASE": ""})
    assert settings.max_batches is None
    assert settings.chunk_size == DEFAULT_CHUNK_SIZE
    assert settings.database == DEFAULT_DATABASE


@pytest.mark.parametrize("name", ["CHUNK_SIZE", "MAX_BATCHES"])
@pytest.mark.parametrize("value", ["0", "-5", "1.5", "abc", "1e3", "+4", "٣"])
def test_a_number_that_is_not_a_positive_whole_number_is_rejected(name, value):
    with pytest.raises(ConfigError, match=name):
        Settings.from_environment({name: value})


# The limits of the alerts.


def test_the_limits_file_is_read_from_the_config_directory_unless_the_environment_says_otherwise():
    assert Settings.from_environment({}).thresholds_file == DEFAULT_THRESHOLDS
    assert DEFAULT_THRESHOLDS == "config/alert_thresholds.toml"
    other = Settings.from_environment({"ALERT_THRESHOLDS": "/etc/limits.toml"})
    assert other.thresholds_file == "/etc/limits.toml"


def test_the_committed_limits_are_the_ones_that_were_approved():
    thresholds = load_thresholds(ROOT / DEFAULT_THRESHOLDS)
    assert dict(thresholds.limits) == {
        "temp": 28.0,
        "humidity": 85.0,
        "co": 0.0075,
        "lpg": 0.010,
        "smoke": 0.030,
    }
    assert (thresholds.coverage, thresholds.cap) == (0.5, 10)


def test_every_limit_of_the_committed_file_is_explained_by_a_comment():
    lines = (ROOT / DEFAULT_THRESHOLDS).read_text().splitlines()
    for position, line in enumerate(lines):
        if line.split(" = ")[0] in {"temp", "humidity", "co", "lpg", "smoke", "coverage"}:
            assert lines[position - 1].startswith("#"), line


def test_a_limits_file_that_is_missing_or_unusable_stops_the_loader_before_it_starts(tmp_path):
    with pytest.raises(ConfigError, match="absent.toml"):
        load_thresholds(tmp_path / "absent.toml")
    broken = tmp_path / "broken.toml"
    broken.write_text("[limits\ntemp = 28")
    with pytest.raises(ConfigError, match="broken.toml"):
        load_thresholds(broken)
    wrong = tmp_path / "wrong.toml"
    wrong.write_text(
        '[limits]\ntemp = "hot"\n[silence]\ncoverage = 0.5\n[cap]\nper_source_and_day = 10\n'
    )
    with pytest.raises(ConfigError, match="limits.temp"):
        load_thresholds(wrong)
