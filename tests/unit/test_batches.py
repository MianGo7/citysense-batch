"""Unit tests of reading the source and slicing it into daily batches."""

import gzip
from datetime import date

import pytest

from citysense.batches import SourceError, iter_batches, parse_readings, slice_days

HEADER = '"ts","device","co","humidity","light","lpg","motion","smoke","temp"'
A = "b8:27:eb:bf:9d:51"
B = "00:0f:00:70:91:0a"


def row(
    ts,
    device=A,
    co="0.005",
    humidity="51.0",
    light="false",
    lpg="0.008",
    motion="false",
    smoke="0.02",
    temp="22.7",
):
    return f'"{ts}","{device}","{co}","{humidity}","{light}","{lpg}","{motion}","{smoke}","{temp}"'


def lines(*rows):
    return [HEADER, *rows]


def test_a_row_becomes_a_reading_with_typed_metrics():
    (reading,) = parse_readings(lines(row("1.5945120943859746E9", light="true", temp="19.7")))
    assert reading.id == f"{A}|1594512094385"
    assert reading.device == A
    assert reading.ms == 1594512094385
    assert reading.ts.isoformat() == "2020-07-12T00:01:34.385000+00:00"
    assert reading.metrics == {
        "co": 0.005,
        "humidity": 51.0,
        "lpg": 0.008,
        "smoke": 0.02,
        "temp": 19.7,
        "light": True,
        "motion": False,
    }


def test_empty_input_gives_no_batches():
    assert list(slice_days(parse_readings(lines()))) == []


def test_readings_are_grouped_into_utc_days_in_order():
    source = lines(
        row("1594598399.5"),  # 2020-07-12 23:59:59.5
        row("1594598400.0"),  # 2020-07-13 00:00:00.0
        row("1594598401.0"),
        row("1594684800.0"),  # 2020-07-14 00:00:00.0
    )
    batches = list(slice_days(parse_readings(source)))
    assert [batch.day for batch in batches] == [
        date(2020, 7, 12),
        date(2020, 7, 13),
        date(2020, 7, 14),
    ]
    assert [len(batch.readings) for batch in batches] == [1, 2, 1]
    assert [batch.key for batch in batches] == ["2020-07-12", "2020-07-13", "2020-07-14"]


def test_a_day_without_readings_gives_no_batch():
    source = lines(row("1594598399.0"), row("1594771200.0"))  # 12 July, then 15 July
    assert [batch.key for batch in slice_days(parse_readings(source))] == [
        "2020-07-12",
        "2020-07-15",
    ]


def test_the_batches_are_generated_one_at_a_time():
    consumed = []

    def stream():
        for text in lines(row("1594598399.0"), row("1594684800.0"), row("1594771200.0")):
            consumed.append(text)
            yield text

    batches = slice_days(parse_readings(stream()))
    next(batches)
    assert len(consumed) == 3  # the header, the first row, and the row that ends the batch


def test_equal_timestamps_are_allowed_so_that_duplicates_reach_the_identifier():
    source = lines(row("1594512094.5"), row("1594512094.5"))
    readings = list(parse_readings(source))
    assert readings[0].id == readings[1].id


def test_time_going_backwards_for_one_device_fails_the_batch():
    source = lines(row("1594512100.0"), row("1594512099.0"))
    with pytest.raises(SourceError, match="line 3.*goes back in time"):
        list(parse_readings(source))


def test_order_is_checked_per_device_and_not_across_devices():
    source = lines(row("1594512100.0", A), row("1594512099.0", B), row("1594512101.0", A))
    assert len(list(parse_readings(source))) == 3


def test_a_reading_from_an_earlier_day_than_the_batch_in_progress_fails():
    # Each device is in order, but the second device is back on the day before.
    source = lines(row("1594684800.0", A), row("1594598399.0", B))
    with pytest.raises(SourceError, match="already complete"):
        list(slice_days(parse_readings(source)))


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (row("1594512094.5", temp="warm"), "line 2.*temp is not a number"),
        (row("1594512094.5", light="maybe"), "line 2.*light is not true or false"),
        (row("not-a-time"), "line 2.*not a timestamp"),
        (row("1594512094.5", device=""), "line 2.*not a usable device"),
        ('"1594512094.5","a"', "line 2.*no value for"),
        (row("1594512094.5") + ',"extra"', "line 2.*more fields"),
    ],
)
def test_a_row_that_cannot_be_parsed_fails_the_batch_with_its_line(bad, message):
    with pytest.raises(SourceError, match=message):
        list(parse_readings(lines(bad)))


def test_the_line_number_counts_from_the_header():
    source = lines(row("1594512094.5"), row("1594512095.5"), row("1594512096.5", temp="x"))
    with pytest.raises(SourceError, match="line 4"):
        list(parse_readings(source))


def test_a_header_with_an_unknown_column_fails_instead_of_dropping_it():
    header = HEADER + ',"co2"'
    with pytest.raises(SourceError, match="unknown columns.*co2"):
        list(parse_readings([header, row("1594512094.5") + ',"415"']))


def test_a_header_with_a_missing_column_fails():
    with pytest.raises(SourceError, match="missing columns.*temp"):
        list(parse_readings(['"ts","device","co","humidity","light","lpg","motion","smoke"']))


def test_a_gzip_file_and_a_plain_file_give_the_same_batches(tmp_path):
    text = "\n".join(lines(row("1594598399.0"), row("1594684800.0"))) + "\n"
    plain = tmp_path / "source.csv"
    plain.write_text(text)
    packed = tmp_path / "source.csv.gz"
    with gzip.open(packed, "wt") as handle:
        handle.write(text)
    assert [b.key for b in iter_batches(plain)] == [b.key for b in iter_batches(packed)]
    assert [b.key for b in iter_batches(packed)] == ["2020-07-12", "2020-07-14"]
