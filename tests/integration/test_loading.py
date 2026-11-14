"""Integration tests of the loader, run against the Docker Compose stack.

Two groups of tests exist. The first checks what `docker compose up --build`
left in the database, which is what a user of the data sees. The second runs the
loader in this process against scratch databases on the same replica set, so
that a load can be interrupted, repeated and compared without touching the data
of the stack. The scratch databases have no validators, because the collection
setup belongs to the stack, whose own load already proves that the validators
accept what the loader writes.
"""

import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pymongo import MongoClient

from citysense.aggregation import Derived
from citysense.alerting import Alerting
from citysense.batches import Reading
from citysense.config import DEFAULT_THRESHOLDS, Settings, load_thresholds
from citysense.loader import EXIT_OK, run
from citysense.store import Store, connect, to_document

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
SOURCE = str(ROOT / "data" / "raw" / "iot_telemetry_data.csv.gz")
# Direct connection to mongo1, which is the primary after a clean start.
URI = "mongodb://127.0.0.1:27017/?directConnection=true"
DATABASE = "citysense"
# The device of the tests of the database setup, which they remove again.
TEST_DEVICE = "integration-test"

STORED_READINGS = 405_171
# Measured by the first load of the stack, which is also the figure of the
# profile script: read, inserted, already present and flagged, per day.
BATCH_COUNTS = {
    "2020-07-12": (50462, 50461, 1, 89),
    "2020-07-13": (50121, 50120, 1, 65),
    "2020-07-14": (50719, 50718, 1, 67),
    "2020-07-15": (51058, 51055, 3, 70),
    "2020-07-16": (51040, 51040, 0, 79),
    "2020-07-17": (50386, 50384, 2, 68),
    "2020-07-18": (50074, 50071, 3, 57),
    "2020-07-19": (51196, 51194, 2, 85),
    "2020-07-20": (128, 128, 0, 0),
}
FLAGGED_READINGS = 580


def compose(*arguments, environment=()):
    command = ["docker", "compose", "run", "--rm", "-T"]
    for setting in environment:
        command += ["-e", setting]
    return subprocess.run(
        [*command, *arguments], cwd=ROOT, capture_output=True, text=True, timeout=300
    )


def counts_of(database):
    runs = {document["_id"]: document for document in database.batch_runs.find()}
    return {
        key: (
            run_["counts"]["read"],
            run_["counts"]["inserted"],
            run_["counts"]["already_present"],
            run_["counts"]["flagged"],
        )
        for key, run_ in runs.items()
    }


def flags_of(database):
    return {
        document["_id"]: document["quality"]["flags"]
        for document in database.readings.find({}, {"quality.flags": 1})
    }


DERIVED = ("summaries_hourly", "summaries_daily", "latest")
HOURLY_SUMMARIES = 579
DAILY_SUMMARIES = 27
DEVICES = ("00:0f:00:70:91:0a", "1c:bf:ce:15:ec:4d", "b8:27:eb:bf:9d:51")
VALID_READINGS = STORED_READINGS - FLAGGED_READINGS


def snapshot(database):
    """The derived collections as dictionaries, to compare them for equality."""
    return {name: {repr(d["_id"]): d for d in database[name].find()} for name in DERIVED}


def valid_and_flagged_per_period(database, unit):
    """The counts that the summaries must reproduce, taken from the readings themselves."""
    pipeline = [
        {
            "$group": {
                "_id": {
                    "device": "$device",
                    "start": {"$dateTrunc": {"date": "$ts", "unit": unit}},
                },
                "valid": {"$sum": {"$cond": [{"$eq": [{"$size": "$quality.flags"}, 0]}, 1, 0]}},
                "flagged": {"$sum": {"$cond": [{"$gt": [{"$size": "$quality.flags"}, 0]}, 1, 0]}},
            }
        }
    ]
    return {
        (row["_id"]["device"], row["_id"]["start"]): (row["valid"], row["flagged"])
        for row in database.readings.aggregate(pipeline, allowDiskUse=True)
    }


def counts_in(database, collection):
    return {
        (d["device"], d["period_start"]): (d["reading_count"], d["flagged_count"])
        for d in database[collection].find()
    }


def load(database_name, **changes):
    """Run the loader in this process against a database of the replica set."""
    settings = Settings(uri=URI, database=database_name, source=SOURCE, **changes)
    client = connect(URI)
    database = client[database_name]
    alerting = Alerting(database, load_thresholds(ROOT / DEFAULT_THRESHOLDS))
    try:
        return run(settings, Store(database), Derived(database), alerting)
    finally:
        client.close()


@pytest.fixture(scope="module")
def client():
    with MongoClient(URI, serverSelectionTimeoutMS=5000, tz_aware=True) as connection:
        yield connection


@pytest.fixture
def scratch(client):
    names = []

    def create():
        name = f"citysense_it_{uuid.uuid4().hex[:12]}"
        names.append(name)
        return name

    yield create
    for name in names:
        client.drop_database(name)


@pytest.fixture(scope="module")
def reference(client):
    """A scratch database that holds an uninterrupted load of the whole sample."""
    name = f"citysense_it_{uuid.uuid4().hex[:12]}"
    assert load(name) == EXIT_OK
    yield client[name]
    client.drop_database(name)


# What the stack left behind.


def test_the_stack_stored_the_sample_without_its_duplicates(client):
    database = client[DATABASE]
    stored = database.readings.count_documents({"device": {"$ne": TEST_DEVICE}})
    assert stored == STORED_READINGS


def test_every_batch_of_the_stack_is_completed_with_its_counts(client):
    database = client[DATABASE]
    assert counts_of(database) == BATCH_COUNTS
    assert {run_["status"] for run_ in database.batch_runs.find()} == {"completed"}
    assert sum(read for read, _, _, _ in BATCH_COUNTS.values()) == 405_184
    assert sum(inserted for _, inserted, _, _ in BATCH_COUNTS.values()) == STORED_READINGS
    assert sum(present for _, _, present, _ in BATCH_COUNTS.values()) == 13


def test_the_flagged_readings_are_stored_and_counted(client):
    database = client[DATABASE]
    stored = database.readings.count_documents({"quality.flags.0": {"$exists": True}})
    assert stored == FLAGGED_READINGS
    assert sum(flagged for _, _, _, flagged in BATCH_COUNTS.values()) == FLAGGED_READINGS


def test_every_stored_reading_carries_the_day_of_its_batch(client):
    database = client[DATABASE]
    mismatches = database.readings.count_documents(
        {
            "device": {"$ne": TEST_DEVICE},
            "$expr": {"$ne": ["$batch", {"$dateToString": {"date": "$ts", "format": "%Y-%m-%d"}}]},
        }
    )
    assert mismatches == 0


def test_the_loader_container_of_the_stack_exited_with_code_0():
    result = subprocess.run(
        ["docker", "compose", "ps", "--all", "--format", "json", "loader"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    containers = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert [(c["State"], c["ExitCode"]) for c in containers] == [("exited", 0)]


def test_a_second_run_of_the_container_inserts_nothing(client):
    database = client[DATABASE]
    before = (database.readings.estimated_document_count(), counts_of(database))
    result = compose("loader")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 batches loaded, 9 skipped, read 0, inserted 0" in result.stdout
    assert (database.readings.estimated_document_count(), counts_of(database)) == before


def test_the_loader_runs_as_a_user_without_privileges():
    identity = subprocess.run(
        ["docker", "compose", "run", "--rm", "-T", "--entrypoint", "id", "loader", "-u"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert identity.stdout.strip().splitlines()[-1] == "10001"


@pytest.mark.parametrize("setting", ["MAX_BATCHES=0", "CHUNK_SIZE=abc"])
def test_an_invalid_setting_stops_the_container_with_code_2(setting):
    result = compose("loader", environment=[setting])
    assert result.returncode == 2
    assert setting.split("=")[0] in result.stdout


# The load in this process, against scratch databases.


def test_an_uninterrupted_load_has_the_measured_counts(reference):
    assert counts_of(reference) == BATCH_COUNTS
    assert reference.readings.count_documents({}) == STORED_READINGS
    assert reference.readings.count_documents({"quality.flags.0": {"$exists": True}}) == (
        FLAGGED_READINGS
    )


def test_the_load_of_the_stack_and_the_load_in_this_process_agree(client, reference):
    stack = {
        key: value
        for key, value in flags_of(client[DATABASE]).items()
        if not key.startswith(f"{TEST_DEVICE}|")
    }
    assert stack == flags_of(reference)


def test_a_second_load_without_the_batch_log_inserts_nothing(client, reference, scratch):
    # The batch log lets a completed batch be skipped, but the identifiers alone
    # keep the load idempotent, so the log is removed to show that.
    name = scratch()
    assert load(name) == EXIT_OK
    client[name].batch_runs.delete_many({})
    assert load(name) == EXIT_OK
    counts = counts_of(client[name])
    assert {inserted for _, inserted, _, _ in counts.values()} == {0}
    assert {key: read for key, (read, _, _, _) in counts.items()} == {
        key: read for key, (read, _, _, _) in BATCH_COUNTS.items()
    }
    assert client[name].readings.count_documents({}) == STORED_READINGS
    assert flags_of(client[name]) == flags_of(reference)


def test_loading_three_days_and_then_the_rest_equals_one_load(client, reference, scratch):
    # The daily arrival of data: three batches now, the following ones later.
    name = scratch()
    assert load(name, max_batches=3) == EXIT_OK
    assert sorted(counts_of(client[name])) == ["2020-07-12", "2020-07-13", "2020-07-14"]
    assert load(name) == EXIT_OK
    assert counts_of(client[name]) == BATCH_COUNTS
    assert flags_of(client[name]) == flags_of(reference)


@pytest.mark.parametrize(
    ("day", "rows"),
    [
        pytest.param("2020-07-15", slice(0, 5000), id="first chunk written"),
        pytest.param("2020-07-15", slice(10000, 15000), id="a later chunk written alone"),
        pytest.param("2020-07-16", slice(None, None, 3), id="every third row written"),
    ],
)
def test_an_interrupted_batch_is_repeated_and_ends_with_the_flags_of_an_uninterrupted_load(
    client, reference, scratch, day, rows
):
    # The interrupted attempt is simulated by what it leaves behind: a batch log
    # entry that is still running and some of the readings of that day, as an
    # unordered bulk write can leave them, with gaps. The real kill of a container
    # is shown by the failure demonstrations.
    name = scratch()
    days = sorted(BATCH_COUNTS)
    before = days[: days.index(day)]
    assert load(name, max_batches=len(before)) == EXIT_OK

    database = client[name]
    left_behind = list(reference.readings.find({"batch": day}).sort([("ts", 1), ("_id", 1)]))[rows]
    marker = datetime(2000, 1, 1, tzinfo=UTC)
    for document in left_behind:
        document["ingested_at"] = marker
    database.readings.insert_many(left_behind, ordered=False)
    database.batch_runs.insert_one(
        {"_id": day, "status": "running", "attempts": 1, "started_at": marker}
    )

    assert load(name) == EXIT_OK

    assert flags_of(database) == flags_of(reference)
    interrupted = database.batch_runs.find_one({"_id": day})
    assert (interrupted["status"], interrupted["attempts"]) == ("completed", 2)
    read, inserted, present, flagged = counts_of(database)[day]
    assert (read, flagged) == (BATCH_COUNTS[day][0], BATCH_COUNTS[day][3])
    assert inserted + present == read
    assert present >= len(left_behind)
    # The readings of the first attempt are not rewritten.
    assert database.readings.count_documents({"ingested_at": marker}) == len(left_behind)
    assert database.readings.count_documents({}) == STORED_READINGS


def test_the_rule_state_is_the_last_valid_reading_before_the_batch_and_its_jump_flags(
    client, scratch
):
    # The sample has no run of flags that crosses midnight, so the question that
    # the store has to answer is posed here with readings made for it. Flags of a
    # range rule leave the streak alone, flags after the start of the batch are
    # not part of the state, and a device without a valid reading has no state.
    name = scratch()
    store = Store(client[name])
    day = datetime(2020, 7, 13, tzinfo=UTC)

    def stored(device, minute, flags):
        moment = datetime(2020, 7, 12, 23, minute, tzinfo=UTC)
        reading = Reading(
            id=f"{device}|{int(moment.timestamp() * 1000)}",
            device=device,
            ms=int(moment.timestamp() * 1000),
            metrics={"temp": 20.0 + minute, "light": False},
        )
        return to_document(reading, flags, "2020-07-12", moment)

    client[name].readings.insert_many(
        [
            stored("a", 10, ["temp_jump"]),
            stored("a", 20, []),
            stored("a", 30, ["temp_jump"]),
            stored("a", 40, ["humidity_range"]),
            stored("a", 50, ["humidity_jump", "humidity_range"]),
            to_document(
                Reading("a|1594598400000", "a", 1594598400000, {"temp": 99.0}),
                [],
                "2020-07-13",
                day,
            ),
            stored("b", 10, ["temp_range"]),
        ]
    )

    reference, streak = store.rule_state("a", day)
    assert (reference.id, reference.metrics["temp"], streak) == (
        stored("a", 20, [])["_id"],
        40.0,
        2,
    )
    assert store.rule_state("b", day) == (None, 0)
    assert store.rule_state("unknown", day) == (None, 0)


# The derived collections of the stack and of an uninterrupted load.


@pytest.fixture(params=["stack", "scratch"])
def loaded(request, client, reference):
    return client[DATABASE] if request.param == "stack" else reference


def test_the_derived_collections_hold_one_document_per_device_and_period(loaded):
    assert loaded.summaries_hourly.count_documents({}) == HOURLY_SUMMARIES
    assert loaded.summaries_daily.count_documents({}) == DAILY_SUMMARIES
    assert sorted(document["_id"] for document in loaded.latest.find()) == sorted(DEVICES)


def test_the_hourly_reading_counts_add_up_to_the_valid_readings(loaded):
    hourly = list(loaded.summaries_hourly.find())
    assert sum(document["reading_count"] for document in hourly) == VALID_READINGS
    assert sum(document["flagged_count"] for document in hourly) == FLAGGED_READINGS
    assert loaded.readings.count_documents({"quality.flags": {"$size": 0}}) == VALID_READINGS


def test_every_hourly_and_daily_count_equals_the_count_of_its_readings(loaded):
    assert counts_in(loaded, "summaries_hourly") == valid_and_flagged_per_period(loaded, "hour")
    assert counts_in(loaded, "summaries_daily") == valid_and_flagged_per_period(loaded, "day")


def test_the_daily_counts_add_up_to_the_hourly_counts(loaded):
    for collection in ("summaries_hourly", "summaries_daily"):
        assert sum(d["reading_count"] for d in loaded[collection].find()) == VALID_READINGS


def test_latest_is_the_newest_valid_reading_of_each_device(loaded):
    for device in DEVICES:
        newest = loaded.readings.find_one(
            {"device": device, "quality.flags": {"$size": 0}}, sort=[("ts", -1)]
        )
        document = loaded.latest.find_one({"_id": device})
        assert document["ts"] == newest["ts"]
        assert document["reading_id"] == newest["_id"]
        assert document["temp"] == newest["temp"]
        assert document["batch"] == "2020-07-20"


def test_the_coverage_of_a_full_hour_stays_near_one_for_every_device(loaded):
    # The measured range of the coverage over the 191 full hours of each device,
    # which is the evidence for the choice of the baseline in ADR-0013.
    ranges = {}
    for device in DEVICES:
        hours = list(loaded.summaries_hourly.find({"device": device}).sort("period_start", 1))
        coverage = [document["coverage"] for document in hours[1:-1]]
        assert len(coverage) == 191
        ranges[device] = (min(coverage), max(coverage))
    assert ranges["00:0f:00:70:91:0a"] == pytest.approx((0.893, 1.104), abs=0.0005)
    assert ranges["1c:bf:ce:15:ec:4d"] == pytest.approx((0.835, 1.213), abs=0.0005)
    assert ranges["b8:27:eb:bf:9d:51"] == pytest.approx((0.996, 1.001), abs=0.0005)


def test_the_last_hour_of_the_sample_is_partial_and_its_coverage_is_low(loaded):
    # The data ends at 00:03:37 on 20 July, so the last hour holds a few minutes.
    # The alert rules of B7 leave such an hour out, because it ends after the
    # newest reading, and the summary reports the plain figure.
    last = {
        device: loaded.summaries_hourly.find_one({"device": device}, sort=[("period_start", -1)])
        for device in DEVICES
    }
    assert {device: document["reading_count"] for device, document in last.items()} == {
        "00:0f:00:70:91:0a": 36,
        "1c:bf:ce:15:ec:4d": 32,
        "b8:27:eb:bf:9d:51": 60,
    }
    assert all(document["coverage"] < 0.07 for document in last.values())
    assert {d["period_start"] for d in last.values()} == {datetime(2020, 7, 20, tzinfo=UTC)}
    # The partial day of 20 July is treated in the same way, so its daily coverage
    # is the plain figure as well.
    days = list(loaded.summaries_daily.find({"period_start": datetime(2020, 7, 20, tzinfo=UTC)}))
    assert sorted(day["reading_count"] for day in days) == [32, 36, 60]
    assert all(day["coverage"] < 0.01 for day in days)


def test_the_first_day_takes_its_baseline_from_its_own_full_hours(loaded):
    first = loaded.summaries_hourly.find_one({"device": DEVICES[2]}, sort=[("period_start", 1)])
    second = loaded.summaries_hourly.find({"device": DEVICES[2]}).sort("period_start", 1)[1]
    assert first["expected_count"] == second["expected_count"] == 976.0
    day = loaded.summaries_daily.find_one({"device": DEVICES[2]}, sort=[("period_start", 1)])
    assert day["expected_count"] == 976.0 * 24


def test_rerunning_every_batch_leaves_the_derived_collections_unchanged(client, scratch):
    name = scratch()
    assert load(name) == EXIT_OK
    before = snapshot(client[name])
    client[name].batch_runs.delete_many({})
    assert load(name) == EXIT_OK
    assert snapshot(client[name]) == before


def test_rerunning_an_early_day_changes_neither_the_summaries_nor_latest(client, scratch):
    # The sentinel of the guard on `latest`: the early day contains valid readings
    # that are older than the stored ones, and it must not move the latest value back.
    name = scratch()
    assert load(name) == EXIT_OK
    database = client[name]
    before = snapshot(database)
    newest = {document["_id"]: document["ts"] for document in database.latest.find()}
    assert newest == {
        "00:0f:00:70:91:0a": datetime(2020, 7, 20, 0, 3, 36, 979000, tzinfo=UTC),
        "1c:bf:ce:15:ec:4d": datetime(2020, 7, 20, 0, 3, 36, 167000, tzinfo=UTC),
        "b8:27:eb:bf:9d:51": datetime(2020, 7, 20, 0, 3, 37, 264000, tzinfo=UTC),
    }

    database.batch_runs.delete_one({"_id": "2020-07-13"})
    assert load(name) == EXIT_OK

    repeated = database.batch_runs.find_one({"_id": "2020-07-13"})
    assert (repeated["status"], repeated["counts"]["inserted"]) == ("completed", 0)
    assert {document["_id"]: document["ts"] for document in database.latest.find()} == newest
    assert snapshot(database) == before


# The alerts of the sample week, see ADR-0014.

# The measured number of threshold alerts per metric and device, which the limits
# of `config/alert_thresholds.toml` produce over the sample week. The profile
# script `scripts/alert_candidates.py` reproduces the same figures for the
# candidate limits, which is how the limits were chosen.
THRESHOLD_ALERTS = {
    ("temp", DEVICES[1]): 5,
    ("humidity", DEVICES[1]): 1,
    ("co", DEVICES[0]): 2,
    ("co", DEVICES[2]): 1,
    ("lpg", DEVICES[0]): 2,
    ("lpg", DEVICES[2]): 1,
    ("smoke", DEVICES[0]): 2,
}
FIRST_HOURS = [
    "threshold|00:0f:00:70:91:0a|co|2020-07-12T22:00:00Z",
    "threshold|00:0f:00:70:91:0a|co|2020-07-15T23:00:00Z",
    "threshold|00:0f:00:70:91:0a|lpg|2020-07-12T22:00:00Z",
    "threshold|00:0f:00:70:91:0a|lpg|2020-07-15T23:00:00Z",
    "threshold|00:0f:00:70:91:0a|smoke|2020-07-12T22:00:00Z",
    "threshold|00:0f:00:70:91:0a|smoke|2020-07-15T23:00:00Z",
    "threshold|1c:bf:ce:15:ec:4d|humidity|2020-07-16T21:00:00Z",
    "threshold|1c:bf:ce:15:ec:4d|temp|2020-07-12T18:00:00Z",
    "threshold|1c:bf:ce:15:ec:4d|temp|2020-07-15T18:00:00Z",
    "threshold|1c:bf:ce:15:ec:4d|temp|2020-07-17T20:00:00Z",
    "threshold|1c:bf:ce:15:ec:4d|temp|2020-07-18T16:00:00Z",
    "threshold|1c:bf:ce:15:ec:4d|temp|2020-07-19T15:00:00Z",
    "threshold|b8:27:eb:bf:9d:51|co|2020-07-16T22:00:00Z",
    "threshold|b8:27:eb:bf:9d:51|lpg|2020-07-16T22:00:00Z",
]
LIMITS = {"temp": 28.0, "humidity": 85.0, "co": 0.0075, "lpg": 0.010, "smoke": 0.030}


def alert_snapshot(database):
    return {document["_id"]: document for document in database.alerts.find()}


def test_the_sample_week_raises_fourteen_threshold_alerts_per_metric_and_device(loaded):
    raised = {}
    for alert in loaded.alerts.find({"type": "threshold"}):
        key = (alert["metric"], alert["source"])
        raised[key] = raised.get(key, 0) + 1
    assert raised == THRESHOLD_ALERTS
    assert sum(raised.values()) == 14
    assert sorted(alert_snapshot(loaded)) == FIRST_HOURS


def test_the_sample_week_raises_no_silent_alert_and_no_alert_about_the_loader(loaded):
    assert loaded.alerts.count_documents({"type": "sensor_silent"}) == 0
    assert loaded.alerts.count_documents({"type": "batch_failed"}) == 0
    assert loaded.alerts.count_documents({}) == 14


def test_no_alert_of_the_sample_week_was_suppressed_by_the_cap(loaded):
    counts = [document["counts"] for document in loaded.batch_runs.find()]
    assert sum(each["alerts_suppressed"] for each in counts) == 0
    assert sum(each["alerts_opened"] for each in counts) == 14


def test_every_alert_of_the_sample_week_has_ended_before_the_horizon(loaded):
    # The last hour of the data, 20 July 00:00, holds only a few minutes of readings
    # and lies after the horizon, so it is not judged. The last complete hour is
    # 19 July 23:00, which is clear for every device, so every alert is resolved.
    assert {alert["status"] for alert in loaded.alerts.find()} == {"resolved"}
    last = max(alert["last_seen"] for alert in loaded.alerts.find())
    assert last <= datetime(2020, 7, 19, 22, tzinfo=UTC)
    assert max(alert["resolved_at"] for alert in loaded.alerts.find()) <= datetime(
        2020, 7, 20, tzinfo=UTC
    )


def test_every_alert_names_a_summary_that_exists_and_a_value_above_its_limit(loaded):
    for alert in loaded.alerts.find():
        assert alert["value"] > alert["threshold"] == LIMITS[alert["metric"]]
        reference = alert["basis"]
        assert reference["collection"] == "summaries_hourly"
        summary = loaded.summaries_hourly.find_one({"_id": reference["_id"]})
        assert summary["metrics"][alert["metric"]]["mean"] == alert["value"]
        assert summary["reading_count"] > 0
        assert alert["first_seen"] <= reference["_id"]["period_start"] <= alert["last_seen"]


def test_an_alert_carries_the_content_that_the_alert_model_asks_for(loaded):
    alert = loaded.alerts.find_one({"_id": FIRST_HOURS[7]})
    assert alert["type"] == "threshold"
    assert alert["severity"] == "warning"
    assert alert["source"] == alert["device"] == DEVICES[1]
    assert alert["message"] == f"Hourly mean temperature 28.46 above limit 28.0 at {DEVICES[1]}"
    assert alert["recommended_action"] == "Check the location and inform the affected district."
    assert alert["first_seen"] == datetime(2020, 7, 12, 18, tzinfo=UTC)
    assert alert["last_seen"] == datetime(2020, 7, 12, 21, tzinfo=UTC)
    assert alert["occurrences"] == 4
    assert alert["resolved_at"] == datetime(2020, 7, 12, 23, tzinfo=UTC)


def test_the_last_hour_would_have_made_every_sensor_silent_without_the_horizon(loaded):
    # The sentinel of the horizon rule: the partial hour exists and is far below
    # the limit of the silence, yet it raised no alert.
    partial = datetime(2020, 7, 20, tzinfo=UTC)
    coverages = [
        document["coverage"] for document in loaded.summaries_hourly.find({"period_start": partial})
    ]
    assert len(coverages) == 3
    assert all(coverage < 0.5 for coverage in coverages)
    assert loaded.alerts.count_documents({"type": "sensor_silent"}) == 0


def test_rerunning_every_batch_leaves_the_alerts_unchanged(client, scratch):
    name = scratch()
    assert load(name) == EXIT_OK
    before = alert_snapshot(client[name])
    client[name].batch_runs.delete_many({})
    assert load(name) == EXIT_OK
    assert alert_snapshot(client[name]) == before
    counts = [document["counts"] for document in client[name].batch_runs.find()]
    assert sum(each["alerts_opened"] for each in counts) == 0
    assert sum(each["alerts_resolved"] for each in counts) == 0


def test_rerunning_any_single_day_leaves_the_alerts_unchanged(client, scratch):
    # Each day is repeated in turn on the finished load, so that the alerts of the
    # later days exist already. An alert that spans midnight must keep its
    # identifier, and the alerts of the days after it must not be touched.
    name = scratch()
    assert load(name) == EXIT_OK
    database = client[name]
    before = alert_snapshot(database)
    assert len(before) == 14

    for day in sorted(BATCH_COUNTS):
        database.batch_runs.delete_one({"_id": day})
        assert load(name) == EXIT_OK
        assert alert_snapshot(database) == before, day
        repeated = database.batch_runs.find_one({"_id": day})["counts"]
        assert (repeated["alerts_opened"], repeated["alerts_resolved"]) == (0, 0), day
        assert repeated["alerts_suppressed"] == 0, day
