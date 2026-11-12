"""Pin the facts about the sample data that the concept quotes.

The figures are measured on the compressed file in `data/raw/` by the profiling
script, which is loaded by path because `scripts/` is not an importable package.
A failure here means either that the data file changed or that a document quotes
a figure the data does not support.
"""

import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DATA_FILE = ROOT / "data" / "raw" / "iot_telemetry_data.csv.gz"
SCRIPT = ROOT / "scripts" / "profile_data.py"

# Recorded in data/README.md, so that a changed file is noticed before any
# figure derived from it is trusted.
DATA_FILE_SHA256 = "208fea26d9f5552a7b9b91eaf6e19dbd40372f516e421b106f6a45168956f414"

# The source holds temperature and humidity as single precision values, so 99.9 is
# delivered as 99.9000015258789. The ranges are claimed to one decimal, and the
# tolerance is far below that step while still above the single precision error.
RANGE_TOLERANCE = 1e-4


@pytest.fixture(scope="module")
def profile_module():
    spec = importlib.util.spec_from_file_location("profile_data", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def facts(profile_module):
    return profile_module.profile(DATA_FILE)


def test_data_file_is_the_recorded_one():
    assert hashlib.sha256(DATA_FILE.read_bytes()).hexdigest() == DATA_FILE_SHA256


def test_row_count(facts):
    assert facts["rows"] == 405_184


def test_three_devices(facts):
    assert len(facts["devices"]) == 3


def test_thirteen_duplicate_rows(facts):
    assert facts["duplicate_rows"] == 13


def test_duplicates_on_device_and_timestamp_are_the_same_thirteen(facts):
    # The identifier is built from device and timestamp, so the rows it absorbs
    # must be exactly the exact duplicates. Otherwise two different readings
    # would collide on one identifier and one of them would be lost.
    assert facts["duplicate_device_timestamp"] == facts["duplicate_rows"] == 13


def test_seven_temperature_readings_of_exactly_zero(facts):
    assert facts["temperature_zeros"] == 7


def test_thirty_eight_humidity_readings_below_ten_percent(facts):
    assert facts["humidity_below_limit"] == 38


def test_temperature_range(facts):
    # The source states Fahrenheit, but the range is consistent with Celsius.
    assert facts["minimum"]["temp"] == 0.0
    assert facts["maximum"]["temp"] == pytest.approx(30.6, abs=RANGE_TOLERANCE)


def test_humidity_range(facts):
    assert facts["minimum"]["humidity"] == pytest.approx(1.1, abs=RANGE_TOLERANCE)
    assert facts["maximum"]["humidity"] == pytest.approx(99.9, abs=RANGE_TOLERANCE)
