"""Integration tests of the alerts on data made for the question.

The sample week raises no silent alert, no alert for a failed batch and no alert
that reaches the cap, so those cases are set up here in scratch databases of the
replica set. The summaries are written directly where the question is about the
rules, and derived from readings by the loader's own pipelines where the question
is about flagged readings, so that the alerts run through the code that the loader
uses.
"""

import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pymongo import MongoClient

from citysense.aggregation import HOURLY, Derived
from citysense.alert_rules import ACKNOWLEDGED, RESOLVED, Thresholds
from citysense.alerting import Alerting
from citysense.batches import Reading
from citysense.config import Settings
from citysense.loader import EXIT_FAILED, EXIT_OK, run
from citysense.store import Store, connect, to_document

pytestmark = pytest.mark.integration

URI = "mongodb://127.0.0.1:27017/?directConnection=true"
INGESTED = datetime(2026, 11, 14, tzinfo=UTC)
DAY = datetime(2020, 7, 13, tzinfo=UTC)
X = "device-x"
Y = "device-y"
CHOSEN = (
    ("temp", 28.0),
    ("humidity", 85.0),
    ("co", 0.0075),
    ("lpg", 0.010),
    ("smoke", 0.030),
)
THRESHOLDS = Thresholds.from_mapping(
    {
        "limits": {"temp": 28.0},
        "silence": {"coverage": 0.5},
        "cap": {"per_source_and_day": 10},
    }
)


@pytest.fixture
def database():
    with MongoClient(URI, serverSelectionTimeoutMS=5000, tz_aware=True) as client:
        name = f"citysense_it_{uuid.uuid4().hex[:12]}"
        yield client[name]
        client.drop_database(name)


def hour(number):
    return DAY + timedelta(hours=number)


def reading(database, device, moment, flags=(), **metrics):
    ms = int(moment.timestamp() * 1000)
    values = {"temp": 20.0, "humidity": 50.0, "light": False, "motion": False, **metrics}
    document = to_document(
        Reading(f"{device}|{ms}", device, ms, values), flags, moment.date().isoformat(), INGESTED
    )
    Store(database).insert_readings([document])


def summary(database, device, number, temp=20.0, coverage=1.0):
    """The summary of one hour as the loader writes it, with the fields that the rules read."""
    database[HOURLY].insert_one(
        {
            "_id": {"device": device, "period_start": hour(number)},
            "device": device,
            "period_start": hour(number),
            "reading_count": round(100 * coverage),
            "flagged_count": 0,
            "expected_count": 100.0,
            "coverage": coverage,
            "metrics": {"temp": {"min": temp, "max": temp, "mean": temp}},
        }
    )


def series(database, device, hours, **changes):
    for number in hours:
        summary(database, device, number, **changes)


def hot(database, device, hours):
    for number in hours:
        summary(database, device, number, temp=30.0)


def horizon(database, devices, number):
    """Store the newest readings, which fix the horizon, at the given hour plus ten minutes."""
    for device in devices:
        reading(database, device, hour(number) + timedelta(minutes=10))


def alerts_of(database):
    return {document["_id"]: document for document in database.alerts.find()}


def evaluate(database, day=DAY):
    return Alerting(database, THRESHOLDS).evaluate(day.date())


# Sensor silent.


def test_a_device_that_sends_nothing_for_two_hours_raises_a_silent_alert(database):
    series(database, X, [0, 1, 2, 5, 6, 7])
    series(database, Y, range(0, 8))
    horizon(database, [X, Y], 8)

    counts = evaluate(database)

    ((identifier, alert),) = alerts_of(database).items()
    assert identifier == f"sensor_silent|{X}|-|2020-07-13T03:00:00Z"
    assert (alert["first_seen"], alert["last_seen"], alert["occurrences"]) == (
        hour(3),
        hour(4),
        2,
    )
    assert (alert["value"], alert["threshold"], alert["basis"]) == (0.0, 0.5, None)
    assert alert["severity"] == "warning"
    assert alert["status"] == RESOLVED
    assert "unknown, not as normal" in alert["recommended_action"]
    assert counts == {"alerts_opened": 1, "alerts_resolved": 1, "alerts_suppressed": 0}


def test_a_device_that_stops_while_another_continues_is_silent_and_the_alert_stays_open(database):
    series(database, X, range(0, 4))
    series(database, Y, range(0, 12))
    horizon(database, [Y], 12)
    reading(database, X, hour(3) + timedelta(minutes=50))

    evaluate(database)

    ((identifier, alert),) = alerts_of(database).items()
    assert identifier == f"sensor_silent|{X}|-|2020-07-13T04:00:00Z"
    assert (alert["status"], alert["last_seen"]) == ("open", hour(11))
    assert alert["occurrences"] == 8


def test_an_hour_that_ends_after_the_horizon_is_still_open_and_not_silent(database):
    series(database, X, range(0, 6))
    series(database, Y, range(0, 6))
    # The newest reading of any device is in the hour 6, so that hour is not complete.
    horizon(database, [X, Y], 6)
    evaluate(database)
    assert alerts_of(database) == {}

    # The same data judged one hour later: the hour 6 is complete, and device x has
    # no summary for it, whereas device y has one.
    summary(database, Y, 6)
    horizon(database, [Y], 7)
    evaluate(database)
    assert list(alerts_of(database)) == [f"sensor_silent|{X}|-|2020-07-13T06:00:00Z"]


def test_a_device_that_sends_nothing_for_a_whole_batch_is_silent_from_its_earlier_baseline(
    database,
):
    # Device x has no summary at all in the second batch, so its baseline can only
    # come from the summaries before the hours that are judged.
    series(database, X, range(0, 23))
    series(database, Y, range(0, 24))
    horizon(database, [X], 22)
    horizon(database, [Y], 23)
    evaluate(database)
    assert alerts_of(database) == {}

    series(database, Y, range(24, 31))
    horizon(database, [Y], 30)
    evaluate(database, DAY + timedelta(days=1))

    ((identifier, alert),) = alerts_of(database).items()
    assert identifier == f"sensor_silent|{X}|-|2020-07-13T23:00:00Z"
    assert (alert["status"], alert["occurrences"], alert["last_seen"]) == ("open", 7, hour(29))


def test_the_first_hour_of_a_device_is_not_judged(database):
    summary(database, X, 0, coverage=0.1)
    series(database, X, range(1, 4))
    horizon(database, [X], 4)
    evaluate(database)
    assert alerts_of(database) == {}


# Threshold alerts and their lifetime.


def test_a_condition_that_lasts_for_hours_raises_one_alert_and_updates_it(database):
    series(database, X, range(0, 4))
    hot(database, X, [4, 5, 6])
    series(database, X, [7, 8])
    horizon(database, [X], 9)

    counts = evaluate(database)

    ((identifier, alert),) = alerts_of(database).items()
    assert identifier == f"threshold|{X}|temp|2020-07-13T04:00:00Z"
    assert alert["occurrences"] == 3
    assert alert["status"] == RESOLVED
    assert alert["resolved_at"] == hour(8)
    assert counts["alerts_opened"] == 1


def test_an_alert_that_spans_two_batches_keeps_its_identifier_and_is_updated(database):
    series(database, X, range(0, 20))
    hot(database, X, range(20, 24))
    horizon(database, [X], 23)
    evaluate(database)

    ((identifier, first),) = alerts_of(database).items()
    # The hour 23 is not complete, so the first batch sees three hours and no end.
    assert (first["occurrences"], first["status"]) == (3, "open")

    hot(database, X, [24, 25])
    series(database, X, [26, 27])
    horizon(database, [X], 27)
    counts = evaluate(database, DAY + timedelta(days=1))

    ((same, second),) = alerts_of(database).items()
    assert same == identifier
    assert (second["occurrences"], second["status"]) == (6, RESOLVED)
    assert second["first_seen"] == hour(20)
    assert counts == {"alerts_opened": 0, "alerts_resolved": 1, "alerts_suppressed": 0}


def test_evaluating_a_day_again_changes_nothing_even_after_later_days_exist(database):
    series(database, X, range(0, 20))
    hot(database, X, range(20, 26))
    series(database, X, range(26, 30))
    horizon(database, [X], 29)
    evaluate(database, DAY + timedelta(days=1))
    before = alerts_of(database)

    for day in (DAY, DAY + timedelta(days=1)):
        counts = evaluate(database, day)
        assert alerts_of(database) == before
        assert counts == {"alerts_opened": 0, "alerts_resolved": 0, "alerts_suppressed": 0}


def test_a_value_that_returns_after_one_clear_hour_is_a_new_alert(database):
    series(database, X, range(0, 3))
    hot(database, X, [3])
    series(database, X, [4])
    hot(database, X, [5])
    series(database, X, [6, 7])
    horizon(database, [X], 8)
    evaluate(database)
    assert sorted(alerts_of(database)) == [
        f"threshold|{X}|temp|2020-07-13T03:00:00Z",
        f"threshold|{X}|temp|2020-07-13T05:00:00Z",
    ]


def test_an_alert_that_an_engineer_acknowledged_stays_acknowledged_until_it_clears(database):
    series(database, X, range(0, 4))
    hot(database, X, [4, 5])
    horizon(database, [X], 6)
    evaluate(database)
    identifier = f"threshold|{X}|temp|2020-07-13T04:00:00Z"
    acknowledged_at = datetime(2020, 7, 13, 6, 30, tzinfo=UTC)
    database.alerts.update_one(
        {"_id": identifier}, {"$set": {"status": ACKNOWLEDGED, "acknowledged_at": acknowledged_at}}
    )

    hot(database, X, [6])
    horizon(database, [X], 7)
    evaluate(database)
    alert = alerts_of(database)[identifier]
    assert (alert["status"], alert["acknowledged_at"], alert["occurrences"]) == (
        ACKNOWLEDGED,
        acknowledged_at,
        3,
    )

    series(database, X, [7, 8])
    horizon(database, [X], 9)
    evaluate(database)
    alert = alerts_of(database)[identifier]
    assert (alert["status"], alert["acknowledged_at"]) == (RESOLVED, acknowledged_at)


def test_a_resolved_alert_is_not_rewritten_by_a_later_change_of_the_limit(database):
    series(database, X, range(0, 3))
    hot(database, X, [3, 4])
    series(database, X, [5, 6])
    horizon(database, [X], 7)
    evaluate(database)
    before = alerts_of(database)

    stricter = Thresholds.from_mapping(
        {"limits": {"temp": 29.0}, "silence": {"coverage": 0.5}, "cap": {"per_source_and_day": 10}}
    )
    Alerting(database, stricter).evaluate(DAY.date())
    assert alerts_of(database) == before


def test_an_open_alert_whose_limit_is_raised_above_its_value_is_resolved(database):
    series(database, X, range(0, 3))
    hot(database, X, [3, 4])
    horizon(database, [X], 5)
    evaluate(database)
    (identifier,) = alerts_of(database)
    assert alerts_of(database)[identifier]["status"] == "open"

    stricter = Thresholds.from_mapping(
        {"limits": {"temp": 35.0}, "silence": {"coverage": 0.5}, "cap": {"per_source_and_day": 10}}
    )
    counts = Alerting(database, stricter).evaluate(DAY.date())
    assert alerts_of(database)[identifier]["status"] == RESOLVED
    assert counts["alerts_resolved"] == 1


# The cap.


def test_the_cap_limits_the_new_alerts_of_a_source_and_a_day_and_counts_the_rest(database):
    # Twelve episodes of one hour each, separated by one clear hour, on one day.
    for number in range(0, 24):
        summary(database, X, number, temp=30.0 if number % 2 == 0 else 20.0)
    horizon(database, [X], 24)

    counts = evaluate(database)

    assert len(alerts_of(database)) == 10
    assert counts == {"alerts_opened": 10, "alerts_resolved": 10, "alerts_suppressed": 2}
    firsts = sorted(alert["first_seen"] for alert in alerts_of(database).values())
    assert firsts == [hour(number) for number in range(0, 20, 2)]


def test_an_evaluation_that_repeats_a_capped_day_raises_nothing_new(database):
    for number in range(0, 24):
        summary(database, X, number, temp=30.0 if number % 2 == 0 else 20.0)
    horizon(database, [X], 24)
    evaluate(database)
    before = alerts_of(database)

    counts = evaluate(database)

    assert alerts_of(database) == before
    assert (counts["alerts_opened"], counts["alerts_suppressed"]) == (0, 2)


def test_the_cap_of_one_source_does_not_use_up_that_of_another(database):
    for device in (X, Y):
        for number in range(0, 24):
            summary(database, device, number, temp=30.0 if number % 2 == 0 else 20.0)
    horizon(database, [X, Y], 24)
    counts = evaluate(database)
    assert len(alerts_of(database)) == 20
    assert counts["alerts_suppressed"] == 4


# Flagged readings.


def test_a_flagged_reading_with_an_extreme_value_does_not_raise_an_alert(database):
    for number in range(0, 6):
        for minute in (5, 15, 25):
            reading(database, X, hour(number) + timedelta(minutes=minute))
        # The flagged value would be an alert, if it reached the mean.
        reading(database, X, hour(number) + timedelta(minutes=35), ["temp_jump"], temp=99.0)
    reading(database, X, hour(6) + timedelta(minutes=5))
    Derived(database).merge_summaries("hour", [X], DAY, DAY + timedelta(days=1), {X: 3.0})

    evaluate(database)

    summaries = list(database[HOURLY].find({"device": X}).sort("period_start", 1))
    assert all(document["flagged_count"] == 1 for document in summaries[:6])
    assert alerts_of(database) == {}


def test_an_hour_in_which_every_reading_is_flagged_counts_as_silent(database):
    for number in range(0, 6):
        reading(database, X, hour(number) + timedelta(minutes=5))
        reading(database, X, hour(number) + timedelta(minutes=15))
    reading(database, X, hour(3) + timedelta(minutes=30), ["temp_jump"], temp=99.0)
    for minute in (5, 15):
        reading(database, X, hour(6) + timedelta(minutes=minute), ["temp_jump"], temp=99.0)
    reading(database, X, hour(7) + timedelta(minutes=5))
    Derived(database).merge_summaries("hour", [X], DAY, DAY + timedelta(days=1), {X: 2.0})

    evaluate(database)

    assert list(alerts_of(database)) == [f"sensor_silent|{X}|-|2020-07-13T06:00:00Z"]


# The loader.

HEADER = '"ts","device","co","humidity","light","lpg","motion","smoke","temp"'
DAY0 = 1_594_598_400  # 2020-07-13 00:00:00 UTC in seconds


def source_with_two_days(path):
    rows = [
        f'"{DAY0 + 100}.000","{X}","0.005","50.0","false","0.008","false","0.02","20.0"',
        f'"{DAY0 + 200}.000","{X}","0.005","50.0","false","0.008","false","0.02","20.0"',
        f'"{DAY0 + 86_400 + 100}.000","{X}","0.005","50.0","false","0.008","false","0.02","20.0"',
    ]
    path.write_text("\n".join([HEADER, *rows]) + "\n")
    return str(path)


def run_loader(database, source):
    settings = Settings(uri=URI, database=database.name, source=source)
    client = connect(URI)
    try:
        target = client[database.name]
        return run(settings, Store(target), Derived(target), Alerting(target, THRESHOLDS))
    finally:
        client.close()


def test_a_source_that_cannot_be_read_raises_one_alert_that_the_next_good_run_resolves(
    database, tmp_path
):
    broken = tmp_path / "broken.csv"
    broken.write_text("not,the,header\n1,2,3\n")
    assert run_loader(database, str(broken)) == EXIT_FAILED
    assert run_loader(database, str(broken)) == EXIT_FAILED

    ((identifier, alert),) = alerts_of(database).items()
    assert identifier == "batch_failed|loader|-|-"
    assert (alert["type"], alert["severity"], alert["source"]) == (
        "batch_failed",
        "critical",
        "loader",
    )
    assert (alert["status"], alert["occurrences"], alert["basis"]) == ("open", 2, None)
    assert alert["recommended_action"] == "Inspect the loader log and rerun the batch."
    assert database.batch_runs.count_documents({}) == 0

    assert run_loader(database, source_with_two_days(tmp_path / "good.csv")) == EXIT_OK
    assert alerts_of(database)[identifier]["status"] == RESOLVED


def test_a_batch_that_fails_raises_an_alert_and_the_completed_rerun_resolves_it(database, tmp_path):
    source = source_with_two_days(tmp_path / "source.csv")
    # A validator that rejects every reading makes the write fail in a way that is
    # not retried, which is the failure of a batch without waiting for an election.
    database.create_collection(
        "readings", validator={"$jsonSchema": {"bsonType": "object", "required": ["absent"]}}
    )
    assert run_loader(database, source) == EXIT_FAILED

    identifier = "batch_failed|loader|-|2020-07-13T00:00:00Z"
    ((found, alert),) = alerts_of(database).items()
    assert found == identifier
    assert alert["basis"] == {"collection": "batch_runs", "_id": "2020-07-13"}
    assert alert["message"].startswith("Batch 2020-07-13 failed:")
    assert (alert["status"], alert["occurrences"]) == ("open", 1)
    assert database.batch_runs.find_one({"_id": "2020-07-13"})["status"] == "failed"

    database.command("collMod", "readings", validator={})
    assert run_loader(database, source) == EXIT_OK

    assert alerts_of(database)[identifier]["status"] == RESOLVED
    assert database.batch_runs.find_one({"_id": "2020-07-13"})["status"] == "completed"


# The figures of the candidate limits that ADR-0014 and the limits file quote.

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "alert_candidates.py"
# The hours above each candidate limit and the alerts that they form, over all devices.
CANDIDATE_TOTALS = {
    ("temp", 25.0): (125, 8),
    ("temp", 28.0): (37, 5),
    ("temp", 30.0): (4, 2),
    ("humidity", 80.0): (8, 1),
    ("humidity", 85.0): (4, 1),
    ("humidity", 90.0): (4, 1),
    ("co", 0.006): (54, 5),
    ("co", 0.0075): (7, 3),
    ("co", 0.010): (2, 1),
    ("lpg", 0.009): (28, 8),
    ("lpg", 0.010): (8, 3),
    ("lpg", 0.012): (2, 1),
    ("smoke", 0.025): (16, 4),
    ("smoke", 0.030): (4, 2),
    ("smoke", 0.035): (2, 1),
}


@pytest.fixture(scope="module")
def candidates():
    spec = importlib.util.spec_from_file_location("alert_candidates", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with MongoClient(URI, serverSelectionTimeoutMS=5000, tz_aware=True) as client:
        yield (
            module.candidate_counts(client["citysense"]),
            module.lowest_full_hour_coverage(client["citysense"]),
        )


def test_the_candidate_limits_raise_the_numbers_of_alerts_that_the_decision_quotes(candidates):
    counts, _ = candidates
    totals = {
        key: (
            sum(hours for hours, _ in per_device.values()),
            sum(alerts for _, alerts in per_device.values()),
        )
        for key, per_device in counts.items()
    }
    assert totals == CANDIDATE_TOTALS


def test_the_chosen_limits_add_up_to_the_fourteen_alerts_of_the_sample_week(candidates):
    counts, _ = candidates
    chosen = [counts[(metric, limit)] for metric, limit in CHOSEN]
    assert sum(alerts for per_device in chosen for _, alerts in per_device.values()) == 14


def test_the_rejected_lower_limits_would_fire_on_normal_hours(candidates):
    counts, _ = candidates
    # 0.006 for co fires on 47 hours of b8:27:eb:bf:9d:51, whose co is usually near
    # 0.0055, and 0.009 for lpg raises 6 of its 8 alerts for the same device.
    assert counts[("co", 0.006)]["b8:27:eb:bf:9d:51"] == (47, 3)
    assert counts[("lpg", 0.009)]["b8:27:eb:bf:9d:51"][1] == 6


def test_the_lowest_coverage_of_a_healthy_full_hour_is_well_above_the_silence_limit(candidates):
    _, lowest = candidates
    assert {device: round(value, 3) for device, value in lowest.items()} == {
        "00:0f:00:70:91:0a": 0.893,
        "1c:bf:ce:15:ec:4d": 0.835,
        "b8:27:eb:bf:9d:51": 0.996,
    }
