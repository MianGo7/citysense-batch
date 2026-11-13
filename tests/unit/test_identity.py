"""Unit tests of the deterministic identifier."""

import math
from datetime import UTC, datetime

import pytest

from citysense.identity import epoch_ms, identifier, instant, reading_id

DEVICE = "b8:27:eb:bf:9d:51"


def test_identifier_of_the_first_row_of_the_sample():
    # The example of ADR-0010, from the first row of the sample file.
    assert reading_id(DEVICE, "1.5945120943859746E9") == f"{DEVICE}|1594512094385"


def test_milliseconds_are_truncated_and_not_rounded():
    assert epoch_ms("1594512094.3859746") == 1594512094385
    assert epoch_ms("1594512094.3999999") == 1594512094399
    assert epoch_ms("1594512094.9999999") == 1594512094999


def test_a_timestamp_just_before_midnight_stays_on_its_day():
    # Rounding would move this reading into the next day.
    ms = epoch_ms("1594598399.9999")
    assert instant(ms).date().isoformat() == "2020-07-12"


def test_exponent_notation_and_plain_notation_agree():
    assert epoch_ms("1.5945120943859746E9") == epoch_ms("1594512094.3859746")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1594512094.0009999999", 1594512094000),
        ("1594512094.9999999999", 1594512094999),
        ("1594512094.0999999999", 1594512094099),
    ],
)
def test_conversion_is_exact_where_a_float_would_not_be(raw, expected):
    # A float rounds these texts up to the next millisecond before the
    # truncation, so the same reading would receive another identifier and ts.
    assert math.floor(float(raw) * 1000) == expected + 1
    assert epoch_ms(raw) == expected


def test_surrounding_whitespace_is_ignored():
    assert epoch_ms(" 1594512094.5 ") == 1594512094500


@pytest.mark.parametrize("raw", ["", "abc", "nan", "inf", "-1", "1.5.2"])
def test_a_timestamp_that_is_not_usable_is_rejected(raw):
    with pytest.raises(ValueError):
        epoch_ms(raw)


@pytest.mark.parametrize("device", ["", "a|b"])
def test_a_device_that_would_make_the_identifier_ambiguous_is_rejected(device):
    with pytest.raises(ValueError):
        identifier(device, 1594512094385)


def test_identical_input_gives_the_identical_identifier():
    assert reading_id(DEVICE, "1.5945120943859746E9") == reading_id(DEVICE, "1.5945120943859746E9")


def test_different_devices_at_one_instant_have_different_identifiers():
    assert identifier("a", 1) != identifier("b", 1)


def test_instant_is_an_aware_utc_datetime_and_exact():
    assert instant(1594512094385) == datetime(2020, 7, 12, 0, 1, 34, 385000, tzinfo=UTC)
