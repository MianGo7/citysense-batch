"""Integration tests of the derived collections on readings made for the question.

The sample data has no hour in which every reading is flagged and no device that
is silent for an hour, so the cases that the summaries have to handle are set up
here, in scratch databases of the replica set, and the pipelines run against them
through the same code that the loader uses.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pymongo import MongoClient

from citysense.aggregation import DAILY, HOURLY, LATEST, Derived
from citysense.batches import Reading
from citysense.store import Store, to_document

pytestmark = pytest.mark.integration

URI = "mongodb://127.0.0.1:27017/?directConnection=true"
DAY = datetime(2020, 7, 13, tzinfo=UTC)
NEXT_DAY = DAY + timedelta(days=1)
INGESTED = datetime(2026, 11, 13, tzinfo=UTC)


@pytest.fixture
def database():
    with MongoClient(URI, serverSelectionTimeoutMS=5000, tz_aware=True) as client:
        name = f"citysense_it_{uuid.uuid4().hex[:12]}"
        yield client[name]
        client.drop_database(name)


def store_reading(database, device, moment, flags=(), **metrics):
    ms = int(moment.timestamp() * 1000)
    values = {"temp": 20.0, "humidity": 50.0, "light": False, "motion": False, **metrics}
    reading = Reading(f"{device}|{ms}", device, ms, values)
    document = to_document(reading, flags, moment.date().isoformat(), INGESTED)
    Store(database).insert_readings([document])


def hour(number):
    return DAY + timedelta(hours=number)


def populate(database):
    # Device x, hour 10: three valid readings and one flagged reading whose
    # value must not reach any statistic.
    for minute, temp, humidity, light in [
        (1, 20.0, 50.0, True),
        (2, 22.0, 60.0, False),
        (3, 24.0, 70.0, True),
    ]:
        store_reading(
            database,
            "x",
            hour(10) + timedelta(minutes=minute),
            temp=temp,
            humidity=humidity,
            light=light,
        )
    store_reading(database, "x", hour(10) + timedelta(minutes=4), ["temp_jump"], temp=99.0)
    # Device x, hour 11: every reading is flagged.
    store_reading(database, "x", hour(11) + timedelta(minutes=1), ["temp_jump"], temp=99.0)
    store_reading(
        database, "x", hour(11) + timedelta(minutes=2), ["humidity_range"], humidity=101.0
    )
    # Device x has no reading in hour 12. Device y has one in hour 10 and no baseline.
    store_reading(database, "y", hour(10) + timedelta(minutes=5), motion=True)


def summaries(database, collection):
    return {
        (document["device"], document["period_start"]): document
        for document in database[collection].find()
    }


def derive_hours(database, expected=None):
    expected = {"x": 6.0, "y": None} if expected is None else expected
    Derived(database).merge_summaries("hour", ["x", "y"], DAY, NEXT_DAY, expected)


def test_an_hour_summarises_the_valid_readings_and_counts_the_flagged_ones(database):
    populate(database)
    derive_hours(database)
    document = summaries(database, HOURLY)[("x", hour(10))]
    assert document["_id"] == {"device": "x", "period_start": hour(10)}
    assert (document["reading_count"], document["flagged_count"]) == (3, 1)
    assert document["metrics"]["temp"] == {"min": 20.0, "max": 24.0, "mean": 22.0}
    assert document["metrics"]["humidity"] == {"min": 50.0, "max": 70.0, "mean": 60.0}
    assert (document["light_true"], document["motion_true"]) == (2, 0)


def test_a_metric_that_no_valid_reading_carries_has_null_statistics(database):
    populate(database)
    derive_hours(database)
    document = summaries(database, HOURLY)[("x", hour(10))]
    assert document["metrics"]["co"] == {"min": None, "max": None, "mean": None}


def test_an_hour_in_which_every_reading_is_flagged_still_gets_a_document(database):
    populate(database)
    derive_hours(database)
    document = summaries(database, HOURLY)[("x", hour(11))]
    assert (document["reading_count"], document["flagged_count"]) == (0, 2)
    assert document["metrics"]["temp"] == {"min": None, "max": None, "mean": None}
    assert document["coverage"] == 0.0


def test_an_hour_without_any_reading_has_no_document(database):
    populate(database)
    derive_hours(database)
    assert sorted(summaries(database, HOURLY)) == [
        ("x", hour(10)),
        ("x", hour(11)),
        ("y", hour(10)),
    ]


def test_the_coverage_is_the_valid_count_over_the_expected_count(database):
    populate(database)
    derive_hours(database)
    document = summaries(database, HOURLY)[("x", hour(10))]
    assert (document["expected_count"], document["coverage"]) == (6.0, 0.5)


def test_a_device_without_a_baseline_has_a_null_expected_count_and_coverage(database):
    populate(database)
    derive_hours(database)
    document = summaries(database, HOURLY)[("y", hour(10))]
    assert (document["expected_count"], document["coverage"]) == (None, None)
    assert document["motion_true"] == 1


def test_a_day_is_summarised_from_its_readings_and_not_from_the_hourly_means(database):
    populate(database)
    # One more valid reading in another hour changes a mean of means but not the mean of readings.
    store_reading(database, "x", hour(15), temp=30.0)
    Derived(database).merge_summaries("day", ["x", "y"], DAY, NEXT_DAY, {"x": 8.0, "y": None})
    document = summaries(database, DAILY)[("x", DAY)]
    assert (document["reading_count"], document["flagged_count"]) == (4, 3)
    assert document["metrics"]["temp"]["mean"] == pytest.approx((20 + 22 + 24 + 30) / 4)
    assert document["coverage"] == 0.5


def test_running_the_pipeline_again_leaves_the_summaries_unchanged(database):
    populate(database)
    derive_hours(database)
    before = summaries(database, HOURLY)
    derive_hours(database)
    assert summaries(database, HOURLY) == before


def test_a_summary_is_replaced_when_the_readings_of_its_hour_change(database):
    populate(database)
    derive_hours(database)
    store_reading(database, "x", hour(10) + timedelta(minutes=30), temp=26.0)
    derive_hours(database)
    document = summaries(database, HOURLY)[("x", hour(10))]
    assert document["reading_count"] == 4
    assert document["metrics"]["temp"]["max"] == 26.0


# The latest value.


def test_latest_holds_the_newest_valid_reading_of_each_device(database):
    populate(database)
    Derived(database).merge_latest(["x", "y"], DAY, NEXT_DAY)
    latest = {document["_id"]: document for document in database[LATEST].find()}
    assert sorted(latest) == ["x", "y"]
    # The flagged readings of hour 11 are newer, but they are not valid.
    assert latest["x"]["ts"] == hour(10) + timedelta(minutes=3)
    assert latest["x"]["temp"] == 24.0
    assert (
        latest["x"]["reading_id"]
        == f"x|{int((hour(10) + timedelta(minutes=3)).timestamp() * 1000)}"
    )
    assert latest["x"]["batch"] == "2020-07-13"
    assert latest["y"]["motion"] is True


def test_a_newer_reading_replaces_the_latest_value(database):
    populate(database)
    derive = Derived(database)
    derive.merge_latest(["x", "y"], DAY, NEXT_DAY)
    store_reading(database, "x", NEXT_DAY + timedelta(minutes=1), temp=18.0)
    derive.merge_latest(["x"], NEXT_DAY, NEXT_DAY + timedelta(days=1))
    assert database[LATEST].find_one({"_id": "x"})["temp"] == 18.0


def test_loading_an_earlier_day_again_never_moves_the_latest_value_backwards(database):
    populate(database)
    derive = Derived(database)
    store_reading(database, "x", NEXT_DAY + timedelta(minutes=1), temp=18.0)
    derive.merge_latest(["x"], NEXT_DAY, NEXT_DAY + timedelta(days=1))
    derive.merge_latest(["x", "y"], DAY, NEXT_DAY)
    assert database[LATEST].find_one({"_id": "x"})["temp"] == 18.0
    # A device that the newer day does not know is still taken from the earlier one.
    assert database[LATEST].find_one({"_id": "y"})["motion"] is True


def test_a_device_whose_readings_are_all_flagged_keeps_its_earlier_latest_value(database):
    populate(database)
    derive = Derived(database)
    derive.merge_latest(["x"], DAY, NEXT_DAY)
    store_reading(database, "x", NEXT_DAY + timedelta(minutes=1), ["temp_jump"], temp=99.0)
    derive.merge_latest(["x"], NEXT_DAY, NEXT_DAY + timedelta(days=1))
    assert database[LATEST].find_one({"_id": "x"})["temp"] == 24.0


# The baseline is read from the earlier summaries.


def test_the_earlier_counts_are_the_counts_strictly_before_the_instant_in_time_order(database):
    for number, count in [(3, 30), (1, 10), (2, 20), (4, 40)]:
        database[HOURLY].insert_one(
            {
                "_id": {"device": "x", "period_start": hour(number)},
                "device": "x",
                "period_start": hour(number),
                "reading_count": count,
            }
        )
    derived = Derived(database)
    assert derived.earlier_counts(HOURLY, "x", hour(3)) == [10, 20]
    assert derived.earlier_counts(HOURLY, "x", hour(1)) == []
    assert derived.earlier_counts(HOURLY, "y", hour(4)) == []


def test_the_hourly_counts_of_a_day_are_valid_readings_and_the_newest_reading_of_each_hour(
    database,
):
    populate(database)
    rows = Derived(database).hour_counts(["x", "y"], DAY, NEXT_DAY)
    assert [(r["_id"]["device"], r["_id"]["period_start"], r["reading_count"]) for r in rows] == [
        ("x", hour(10), 3),
        ("x", hour(11), 0),
        ("y", hour(10), 1),
    ]
    assert rows[0]["last_ts"] == hour(10) + timedelta(minutes=4)
