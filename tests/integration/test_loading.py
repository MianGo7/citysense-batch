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

from citysense.batches import Reading
from citysense.config import Settings
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


def load(database_name, **changes):
    """Run the loader in this process against a database of the replica set."""
    settings = Settings(uri=URI, database=database_name, source=SOURCE, **changes)
    client = connect(URI)
    try:
        return run(settings, Store(client[database_name]))
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
