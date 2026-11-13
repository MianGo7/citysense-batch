"""Unit tests of the settings that the loader reads from its environment."""

import pytest

from citysense.config import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_DATABASE,
    DEFAULT_SOURCE,
    DEFAULT_URI,
    ConfigError,
    Settings,
)


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
