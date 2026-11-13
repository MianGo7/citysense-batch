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

from citysense.batches import iter_batches

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


# The figures below are quoted in ADR-0011. They are produced by the rules of
# the package on the sample file, so the document states what the loader does.


@pytest.fixture(scope="module")
def flags(profile_module):
    return profile_module.flag_statistics(DATA_FILE)


def test_thirteen_duplicates_on_device_and_truncated_millisecond(facts):
    # The identifier uses the timestamp truncated to milliseconds. If two different
    # readings of one device shared a millisecond, one of them would be lost.
    assert facts["duplicate_device_millisecond"] == 13
    assert facts["duplicate_device_millisecond"] == facts["duplicate_rows"]


def test_the_stored_readings_are_the_rows_without_the_duplicates(flags, facts):
    assert flags["readings"] == facts["rows"] - facts["duplicate_rows"] == 405_171


def test_the_file_is_sliced_into_nine_daily_batches_in_order():
    sizes = {batch.key: len(batch.readings) for batch in iter_batches(DATA_FILE)}
    assert list(sizes) == [f"2020-07-{day}" for day in range(12, 21)]
    assert list(sizes.values()) == [50462, 50121, 50719, 51058, 51040, 50386, 50074, 51196, 128]


def test_580_readings_are_flagged(flags):
    assert flags["flagged"] == 580


def test_the_flagged_readings_by_rule(flags):
    assert flags["flagged_temp_only"] == 261
    assert flags["flagged_humidity_only"] == 230
    assert flags["flagged_both"] == 89
    # No reading violates a range rule, so the jump rule accounts for all of them.
    assert flags["flagged_other"] == 0
    assert flags["flagged"] == 261 + 230 + 89


def test_535_flagged_readings_lie_beyond_the_zeros_and_the_low_humidity(flags):
    # 580 flagged, of which the 7 zeros and the 38 low humidity readings are named
    # in the concept, and the two groups do not overlap.
    assert flags["flagged_further"] == 535
    assert flags["flagged"] - flags["zeros"] - flags["below_limit"] == 535


def test_all_seven_temperature_zeros_are_flagged(flags):
    assert (flags["zeros_flagged"], flags["zeros"]) == (7, 7)


def test_all_38_humidity_readings_below_ten_percent_are_flagged(flags):
    assert (flags["below_limit_flagged"], flags["below_limit"]) == (38, 38)


def test_two_readings_are_accepted_as_a_new_reference_after_jump_flags(flags):
    # The humidity rises of 14 July at 21:00 and 16 July at 20:12, the measured
    # cost of the re-baseline, see ADR-0011.
    assert flags["rebaselines"] == 2


def test_the_jumps_are_flagged_on_two_devices_and_none_on_the_third(flags):
    assert flags["jumps_per_device"] == {
        "00:0f:00:70:91:0a": {"humidity": 223, "temp": 236},
        "1c:bf:ce:15:ec:4d": {"humidity": 96, "temp": 114},
        "b8:27:eb:bf:9d:51": {},
    }


def test_size_classes_of_the_flagged_temperature_jumps(flags):
    assert flags["jump_sizes"]["temp"] == {
        3.1: 3,
        3.2: 39,
        3.3: 3,
        4.8: 1,
        6.3: 4,
        6.4: 50,
        6.5: 6,
        9.6: 1,
        12.7: 15,
        12.8: 137,
        12.9: 17,
        19.1: 2,
        19.2: 17,
        19.3: 1,
        25.5: 4,
        25.6: 47,
        25.7: 3,
    }


def test_size_classes_of_the_flagged_humidity_jumps(flags):
    assert flags["jump_sizes"]["humidity"] == {
        11.6: 1,
        12.0: 2,
        12.3: 1,
        12.4: 1,
        12.6: 3,
        12.7: 27,
        12.8: 89,
        12.9: 19,
        13.0: 4,
        13.1: 3,
        13.2: 1,
        13.4: 1,
        19.1: 5,
        19.2: 12,
        19.3: 3,
        24.2: 1,
        24.8: 1,
        25.1: 1,
        25.4: 1,
        25.5: 5,
        25.6: 10,
        25.7: 3,
        25.8: 1,
        26.1: 1,
        50.9: 1,
        51.0: 4,
        51.1: 10,
        51.2: 77,
        51.3: 19,
        51.4: 4,
        51.5: 4,
        51.7: 1,
        51.8: 1,
        52.0: 1,
        76.8: 1,
    }


def test_the_flagged_jumps_cluster_at_multiples_of_3_2(profile_module, flags):
    # An observation about the data, and no cause is claimed for it. A size spread
    # evenly would lie within the tolerance of a multiple in a quarter of the cases.
    near = profile_module.jumps_near_multiples(flags["jump_sizes"])
    assert near == {
        "temp": {"near": 349, "total": 350},
        "humidity": {"near": 306, "total": 319},
    }


def test_median_interval_per_device(facts):
    # Two flagged readings before the re-baseline span about twice this interval.
    assert facts["median_interval"] == {
        "00:0f:00:70:91:0a": 5.4,
        "1c:bf:ce:15:ec:4d": 5.4,
        "b8:27:eb:bf:9d:51": 3.7,
    }


def test_largest_interval_between_readings_of_one_device(facts):
    # The reference of the jump rule is compared only up to 300 seconds back, and
    # this is far below that, so the sample never reaches the limit.
    assert facts["largest_interval"] == 40.2


def test_the_two_rebaselines_are_the_humidity_rises_of_14_and_16_july(flags):
    assert flags["rebaseline_events"] == [
        ("1c:bf:ce:15:ec:4d", "2020-07-14T21:00:24.418000+00:00"),
        ("00:0f:00:70:91:0a", "2020-07-16T20:12:40.974000+00:00"),
    ]


def test_limits_of_5_and_30_would_let_the_small_spike_classes_through(flags):
    # The rejected alternative of ADR-0011. Among the flagged jumps, these are
    # the ones that a limit of 5 degrees and of 30 points would not flag.
    temperature = flags["jump_sizes"]["temp"]
    humidity = flags["jump_sizes"]["humidity"]
    assert sum(count for size, count in temperature.items() if size <= 5.0) == 46
    assert sum(count for size, count in humidity.items() if size <= 30.0) == 196
