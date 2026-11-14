"""Integration tests of the database setup, run against the Docker Compose stack.

They check what a user of the data would see: the collections and indexes exist,
the validators accept a reading from a future sensor and reject a reading without
a device, the setup can be repeated, the retention period is configurable, and
the TTL (time to live) index removes expired raw readings. The setup is repeated
through `docker compose run`, so the host needs the Docker command line client,
which the stack needs anyway.
"""

import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pymongo import MongoClient
from pymongo.errors import WriteError

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
# Direct connection to mongo1, which is the primary after a clean start.
URI = "mongodb://127.0.0.1:27017/?directConnection=true"
DATABASE = "citysense"
COLLECTIONS = {
    "readings",
    "batch_runs",
    "summaries_hourly",
    "summaries_daily",
    "latest",
    "alerts",
}
DOCUMENT_VALIDATION_FAILURE = 121
SECONDS_PER_DAY = 86400
DEFAULT_RETENTION_DAYS = 365
# The device of every test document, so that cleanup never touches loaded data.
TEST_DEVICE = "integration-test"


@pytest.fixture(scope="module")
def database():
    with MongoClient(URI, serverSelectionTimeoutMS=5000) as client:
        yield client[DATABASE]


def remove_test_documents(database):
    # By identifier prefix and not by device, because a reading that the
    # validator should have rejected may lack the device field altogether.
    database.readings.delete_many({"_id": {"$regex": f"^{TEST_DEVICE}\\|"}})
    database.alerts.delete_many({"source": TEST_DEVICE})
    database.batch_runs.delete_many({"_id": "1999-01-01"})
    database.summaries_hourly.delete_many({"device": TEST_DEVICE})
    database.latest.delete_many({"_id": TEST_DEVICE})


@pytest.fixture
def cleanup(database):
    # Also before the test, so that leftovers of an interrupted run cannot
    # make the next run fail with a duplicate identifier.
    remove_test_documents(database)
    yield
    remove_test_documents(database)


def run_setup(retention_days=None):
    command = ["docker", "compose", "run", "--rm", "-T"]
    if retention_days is not None:
        command += ["-e", f"RETENTION_DAYS={retention_days}"]
    command.append("mongo-setup")
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)


def reading(measured_at_ms=1594512094385, **fields):
    now = datetime.now(UTC)
    document = {
        "_id": f"{TEST_DEVICE}|{measured_at_ms}",
        "device": TEST_DEVICE,
        "ts": datetime.fromtimestamp(measured_at_ms / 1000, UTC),
        "ingested_at": now,
        "batch": "2020-07-12",
        "quality": {"flags": []},
        "temp": 22.7,
        "light": False,
    }
    document.update(fields)
    return document


def indexes(database, collection):
    return {index["name"]: index for index in database[collection].list_indexes()}


def test_every_collection_exists(database):
    assert set(database.list_collection_names()) >= COLLECTIONS


def test_every_collection_has_a_strict_validator(database):
    for info in database.list_collections():
        if info["name"] not in COLLECTIONS:
            continue
        options = info["options"]
        assert "$jsonSchema" in options["validator"], info["name"]
        assert options["validationLevel"] == "strict", info["name"]
        assert options["validationAction"] == "error", info["name"]


def test_readings_index_serves_ranges_per_device(database):
    index = indexes(database, "readings")["device_ts"]
    assert list(index["key"].items()) == [("device", 1), ("ts", -1)]


def test_alerts_index_serves_open_alerts_per_sensor(database):
    index = indexes(database, "alerts")["status_type_device"]
    assert list(index["key"].items()) == [("status", 1), ("type", 1), ("device", 1)]


def test_alerts_have_an_index_on_source_and_first_seen(database):
    index = indexes(database, "alerts")["source_first_seen"]
    assert list(index["key"].items()) == [("source", 1), ("first_seen", 1)]


def test_the_alerts_of_a_source_from_a_day_on_are_read_through_that_index(database):
    plan = database.command(
        "explain",
        {
            "find": "alerts",
            "filter": {
                "source": TEST_DEVICE,
                "first_seen": {"$gte": datetime(2020, 7, 12, tzinfo=UTC)},
            },
        },
        verbosity="queryPlanner",
    )
    assert "source_first_seen" in str(plan["queryPlanner"]["winningPlan"])


def test_readings_expire_on_the_ingestion_time_after_the_default_retention(database):
    index = indexes(database, "readings")["ingested_at_ttl"]
    assert list(index["key"].items()) == [("ingested_at", 1)]
    assert index["expireAfterSeconds"] == DEFAULT_RETENTION_DAYS * SECONDS_PER_DAY


@pytest.mark.parametrize("collection", ["summaries_hourly", "summaries_daily"])
def test_summaries_have_an_index_on_device_and_period(database, collection):
    index = indexes(database, collection)["device_period"]
    assert list(index["key"].items()) == [("device", 1), ("period_start", 1)]


def stages(plan):
    """Every stage of an explained plan, from the winning one down to the scans."""
    yield plan
    if "inputStage" in plan:
        yield from stages(plan["inputStage"])
    for child in plan.get("inputStages", []):
        yield from stages(child)


@pytest.mark.parametrize("collection", ["summaries_hourly", "summaries_daily"])
def test_the_period_query_of_a_planner_uses_the_index_and_reads_no_other_device(
    database, collection
):
    # The query of the urban planner: one device over a stretch of time. The explain
    # runs on the loaded data of the stack, and the plan has to bound both fields.
    explained = (
        database[collection]
        .find(
            {
                "device": "b8:27:eb:bf:9d:51",
                "period_start": {"$gte": datetime(2020, 7, 14, tzinfo=UTC)},
            }
        )
        .explain()
    )
    scans = [s for s in stages(explained["queryPlanner"]["winningPlan"]) if s["stage"] == "IXSCAN"]
    assert [scan["indexName"] for scan in scans] == ["device_period"]
    bounds = scans[0]["indexBounds"]
    assert bounds["device"] == ['["b8:27:eb:bf:9d:51", "b8:27:eb:bf:9d:51"]']
    assert bounds["period_start"][0].startswith("[new Date(1594684800000)")


@pytest.mark.parametrize("collection", sorted(COLLECTIONS - {"readings"}))
def test_only_raw_readings_expire(database, collection):
    # Summaries are kept permanently, so no other collection carries a TTL index.
    assert all(
        "expireAfterSeconds" not in index for index in indexes(database, collection).values()
    )


def test_a_reading_with_an_unknown_extra_metric_is_accepted(database, cleanup):
    document = reading(co2=415.0, noise_db=48, sensor_model="future-1")
    assert database.readings.insert_one(document).inserted_id == document["_id"]


def test_a_reading_without_a_device_is_rejected(database, cleanup):
    document = reading()
    del document["device"]
    with pytest.raises(WriteError) as failure:
        database.readings.insert_one(document)
    assert failure.value.code == DOCUMENT_VALIDATION_FAILURE


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"_id": "no-separator"}, id="identifier without a timestamp"),
        pytest.param({"ts": 1594512094.385}, id="timestamp as a number"),
        pytest.param({"temp": "22.7"}, id="known metric of the wrong type"),
        pytest.param({"quality": {}}, id="quality without flags"),
        pytest.param({"quality": {"flags": [1]}}, id="flag that is not a string"),
        pytest.param({"batch": "12 July"}, id="batch that is not a day"),
    ],
)
def test_malformed_readings_are_rejected(database, cleanup, change):
    with pytest.raises(WriteError) as failure:
        database.readings.insert_one(reading(**change))
    assert failure.value.code == DOCUMENT_VALIDATION_FAILURE


def test_a_reading_without_quality_is_rejected(database, cleanup):
    # A missing field would mean never checked, which is not the same as an
    # empty list of flags, so the validator does not allow it.
    document = reading()
    del document["quality"]
    with pytest.raises(WriteError) as failure:
        database.readings.insert_one(document)
    assert failure.value.code == DOCUMENT_VALIDATION_FAILURE


def test_a_flagged_reading_is_accepted_and_found_by_the_valid_selection(database, cleanup):
    database.readings.insert_one(reading(1594512094385, quality={"flags": []}))
    database.readings.insert_one(reading(1594512099385, quality={"flags": ["temp_zero"]}))
    valid = database.readings.find({"device": TEST_DEVICE, "quality.flags": {"$size": 0}})
    assert [document["_id"] for document in valid] == [f"{TEST_DEVICE}|1594512094385"]


def test_the_derived_collections_accept_their_keys(database, cleanup):
    period_start = datetime(2020, 7, 12, tzinfo=UTC)
    database.summaries_hourly.insert_one(
        {
            "_id": {"device": TEST_DEVICE, "period_start": period_start},
            "device": TEST_DEVICE,
            "period_start": period_start,
            "reading_count": 900,
            "flagged_count": 0,
            "coverage": 1.0,
        }
    )
    database.latest.insert_one({"_id": TEST_DEVICE, "device": TEST_DEVICE, "ts": period_start})
    database.batch_runs.insert_one(
        {"_id": "1999-01-01", "status": "running", "attempts": 1, "started_at": period_start}
    )
    database.alerts.insert_one(alert())


def alert(**changes):
    """A complete alert of the model of ADR-0014, as the rules write it."""
    moment = datetime(2020, 7, 12, 10, tzinfo=UTC)
    document = {
        "_id": f"threshold|{TEST_DEVICE}|temp|2020-07-12T10:00:00Z",
        "type": "threshold",
        "severity": "warning",
        "status": "open",
        "source": TEST_DEVICE,
        "device": TEST_DEVICE,
        "metric": "temp",
        "value": 28.4,
        "threshold": 28.0,
        "basis": {
            "collection": "summaries_hourly",
            "_id": {"device": TEST_DEVICE, "period_start": moment},
        },
        "message": f"Hourly mean temperature 28.4 above limit 28.0 at {TEST_DEVICE}",
        "recommended_action": "Check the location and inform the affected district.",
        "first_seen": moment,
        "last_seen": moment,
        "occurrences": 1,
    }
    document.update(changes)
    return document


def summary(**changes):
    period_start = datetime(2020, 7, 12, tzinfo=UTC)
    document = {
        "_id": {"device": TEST_DEVICE, "period_start": period_start},
        "device": TEST_DEVICE,
        "period_start": period_start,
        "reading_count": 0,
        "flagged_count": 2,
        "coverage": None,
        "expected_count": None,
        "metrics": {"temp": {"min": None, "max": None, "mean": None}},
        "light_true": 0,
        "motion_true": 0,
    }
    document.update(changes)
    return document


def test_a_summary_without_a_baseline_has_a_null_coverage_and_null_statistics(database, cleanup):
    # An hour in which every reading is flagged, of a device that has no baseline yet.
    document = summary()
    assert database.summaries_hourly.insert_one(document).inserted_id == document["_id"]


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"coverage": "0.5"}, id="coverage as text"),
        pytest.param({"expected_count": "576"}, id="expected count as text"),
        pytest.param({"metrics": [1, 2]}, id="statistics that are not an object"),
        pytest.param({"light_true": 1.5}, id="count of true readings that is not whole"),
        pytest.param({"reading_count": None}, id="reading count that is null"),
    ],
)
def test_a_malformed_summary_is_rejected(database, cleanup, change):
    with pytest.raises(WriteError) as failure:
        database.summaries_hourly.insert_one(summary(**change))
    assert failure.value.code == DOCUMENT_VALIDATION_FAILURE


@pytest.mark.parametrize(
    ("collection", "document"),
    [
        pytest.param(
            "alerts",
            {key: value for key, value in alert().items() if key != "source"},
            id="alert without a source",
        ),
        pytest.param("alerts", alert(type="loud"), id="alert of an unknown type"),
        pytest.param("alerts", alert(status="closed"), id="alert of an unknown status"),
        pytest.param("alerts", alert(severity="fatal"), id="alert of an unknown severity"),
        pytest.param(
            "alerts",
            {key: value for key, value in alert().items() if key != "message"},
            id="alert without a message",
        ),
        pytest.param(
            "alerts",
            {key: value for key, value in alert().items() if key != "recommended_action"},
            id="alert without a recommended action",
        ),
        pytest.param("alerts", alert(occurrences=1.5), id="alert with a fractional count"),
        pytest.param("alerts", alert(first_seen="2020-07-12"), id="alert with a time as text"),
        pytest.param("alerts", alert(value="28.4"), id="alert with a value as text"),
        pytest.param(
            "batch_runs",
            {
                "_id": "1999-01-01",
                "status": "paused",
                "attempts": 1,
                "started_at": datetime(2020, 7, 12, tzinfo=UTC),
            },
            id="batch run of an unknown status",
        ),
        pytest.param(
            "summaries_hourly",
            {
                "_id": "string-instead-of-object",
                "device": TEST_DEVICE,
                "period_start": datetime(2020, 7, 12, tzinfo=UTC),
                "reading_count": 1,
                "flagged_count": 0,
                "coverage": 1.0,
            },
            id="summary with a plain identifier",
        ),
    ],
)
def test_malformed_derived_documents_are_rejected(database, cleanup, collection, document):
    with pytest.raises(WriteError) as failure:
        database[collection].insert_one(document)
    assert failure.value.code == DOCUMENT_VALIDATION_FAILURE


def test_an_alert_without_a_metric_or_a_basis_is_accepted(database, cleanup):
    # A sensor silent alert has no metric, and the hour in which a device sent
    # nothing has no summary to refer to, so its basis is null.
    silent = alert(
        _id=f"sensor_silent|{TEST_DEVICE}|-|2020-07-12T10:00:00Z",
        type="sensor_silent",
        basis=None,
        value=0.0,
        threshold=0.5,
    )
    del silent["metric"]
    assert database.alerts.insert_one(silent).inserted_id == silent["_id"]


def test_the_alert_of_a_failed_batch_has_no_device_value_or_metric(database, cleanup):
    failed = alert(
        _id="batch_failed|loader|-|2020-07-12T00:00:00Z",
        type="batch_failed",
        severity="critical",
        source=TEST_DEVICE,
        basis={"collection": "batch_runs", "_id": "2020-07-12"},
    )
    for name in ("device", "metric", "value", "threshold"):
        del failed[name]
    assert database.alerts.insert_one(failed).inserted_id == failed["_id"]


def test_the_setup_can_be_run_again(database):
    before = {name: sorted(indexes(database, name)) for name in COLLECTIONS}
    result = run_setup()
    assert result.returncode == 0, result.stderr
    assert "nothing to do" in result.stdout
    assert {name: sorted(indexes(database, name)) for name in COLLECTIONS} == before


def test_the_retention_is_configurable_and_restored(database):
    try:
        result = run_setup(retention_days=30)
        assert result.returncode == 0, result.stderr
        assert indexes(database, "readings")["ingested_at_ttl"]["expireAfterSeconds"] == (
            30 * SECONDS_PER_DAY
        )
    finally:
        restored = run_setup()
    assert restored.returncode == 0, restored.stderr
    assert indexes(database, "readings")["ingested_at_ttl"]["expireAfterSeconds"] == (
        DEFAULT_RETENTION_DAYS * SECONDS_PER_DAY
    )


@pytest.mark.parametrize("value", ["abc", "0", "-5", "1.5", ""])
def test_an_invalid_retention_fails_the_setup(database, value):
    result = run_setup(retention_days=value)
    assert result.returncode != 0
    assert "RETENTION_DAYS" in result.stdout
    assert indexes(database, "readings")["ingested_at_ttl"]["expireAfterSeconds"] == (
        DEFAULT_RETENTION_DAYS * SECONDS_PER_DAY
    )


def test_the_ttl_monitor_removes_an_expired_reading_and_keeps_a_current_one(database, cleanup):
    # The TTL monitor of MongoDB wakes up every 60 seconds, so the wait is
    # bounded by 150 seconds, which covers two cycles. The ingestion time of the
    # expired reading is 400 days in the past, older than the retention of 365.
    now = datetime.now(UTC)
    expired = reading(1594512094385, ingested_at=now - timedelta(days=400))
    current = reading(1594512099385, ingested_at=now)
    database.readings.insert_many([expired, current])

    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        if database.readings.count_documents({"_id": expired["_id"]}) == 0:
            break
        time.sleep(5)

    assert database.readings.count_documents({"_id": expired["_id"]}) == 0
    assert database.readings.count_documents({"_id": current["_id"]}) == 1
