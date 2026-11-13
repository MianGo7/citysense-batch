"""Unit tests of the shape of a stored reading, without a database."""

from datetime import UTC, datetime

from citysense.batches import Reading
from citysense.store import day_start, to_document, to_reading

MS = 1_594_512_094_385
METRICS = {
    "co": 0.005,
    "humidity": 51.0,
    "lpg": 0.008,
    "smoke": 0.02,
    "temp": 22.7,
    "light": False,
    "motion": True,
}
READING = Reading(f"d1|{MS}", "d1", MS, METRICS)
INGESTED = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def test_a_document_carries_the_fields_the_validator_requires_and_the_metrics():
    document = to_document(READING, ("temp_jump",), "2020-07-12", INGESTED)
    assert document == {
        "_id": f"d1|{MS}",
        "device": "d1",
        "ts": datetime(2020, 7, 12, 0, 1, 34, 385000, tzinfo=UTC),
        "ingested_at": INGESTED,
        "batch": "2020-07-12",
        "quality": {"flags": ["temp_jump"]},
        **METRICS,
    }


def test_a_plausible_reading_has_an_empty_list_of_flags_and_not_a_missing_field():
    assert to_document(READING, (), "2020-07-12", INGESTED)["quality"] == {"flags": []}


def test_a_document_becomes_the_same_reading_again():
    document = to_document(READING, (), "2020-07-12", INGESTED)
    assert to_reading(document) == READING


def test_a_stored_metric_that_the_source_does_not_know_is_not_part_of_the_reading():
    document = to_document(READING, (), "2020-07-12", INGESTED) | {"co2": 415.0}
    assert to_reading(document) == READING


def test_a_day_starts_at_midnight_utc():
    assert day_start(READING.day) == datetime(2020, 7, 12, tzinfo=UTC)
