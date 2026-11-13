"""Unit tests of the loader, against an in-memory store that mirrors the real one.

The fake store answers the same questions as `store.py`, so that the order of
batches, the skipping of completed ones, the rebuilding of the rule state, the
retries and the batch log can be tested without a database. The behaviour of the
real store is covered by the integration tests.
"""

import logging
from collections import defaultdict
from datetime import UTC, datetime
from functools import partial

import pytest
from pymongo.errors import (
    AutoReconnect,
    NotPrimaryError,
    OperationFailure,
    ServerSelectionTimeoutError,
    WriteConcernError,
)

from citysense.aggregation import DAILY, HOURLY
from citysense.batches import iter_batches
from citysense.config import Settings
from citysense.loader import EXIT_FAILED, EXIT_OK, RETRY_DELAYS, with_retries
from citysense.loader import run as run_loader
from citysense.store import to_document, to_reading
from citysense.validation import flag_readings

HEADER = '"ts","device","co","humidity","light","lpg","motion","smoke","temp"'
A = "aa:aa"
B = "bb:bb"
DAY0 = 1_594_512_000  # 2020-07-12 00:00:00 UTC in seconds
DAY = 86_400


def csv_row(seconds, device, temp=20.0, humidity=50.0, fraction="000"):
    return (
        f'"{seconds}.{fraction}","{device}","0.005","{humidity}","false","0.008","false",'
        f'"0.02","{temp}"'
    )


def scenario():
    """Three days and two devices, with the cases that need the state of an earlier day.

    Device A has two jump flags at the end of the first day and a third reading
    at the start of the second day, which the streak accepts as the new level.
    Device B repeats one plausible and one flagged row on the second day.
    """
    rows = []
    for day in range(3):
        base = DAY0 + day * DAY
        for step in range(6):
            rows.append(csv_row(base + 1000 + step * 5, B, temp=21.0))
            rows.append(csv_row(base + 1000 + step * 5, A))
    rows += [
        csv_row(DAY0 + DAY - 12, A, temp=20.0),
        csv_row(DAY0 + DAY - 7, A, temp=30.0),
        csv_row(DAY0 + DAY - 2, A, temp=30.0),
        csv_row(DAY0 + DAY + 3, A, temp=30.0),
        csv_row(DAY0 + DAY + 8, A, temp=30.5),
    ]
    duplicate = csv_row(DAY0 + DAY + 500, B, temp=21.0)
    flagged_duplicate = csv_row(DAY0 + DAY + 600, B, temp=30.0)
    rows += [duplicate, duplicate, flagged_duplicate, flagged_duplicate]
    rows.sort(key=lambda text: (float(text.split('"')[1]), text))
    return "\n".join([HEADER, *rows]) + "\n"


class FakeDerived:
    """The derived collections of `Derived`, kept as lists of counts and calls."""

    def __init__(self, store):
        self.store = store
        self.counts = {HOURLY: defaultdict(dict), DAILY: defaultdict(dict)}
        self.merges = []
        self.latest_merges = []
        self.status_during_merge = []
        self.failures = []

    def hour_counts(self, devices, start, end):
        hours = {}
        for document in self.store.readings.values():
            if document["device"] in devices and start <= document["ts"] < end:
                key = (
                    document["device"],
                    document["ts"].replace(minute=0, second=0, microsecond=0),
                )
                row = hours.setdefault(key, {"reading_count": 0, "last_ts": document["ts"]})
                row["reading_count"] += 0 if document["quality"]["flags"] else 1
                row["last_ts"] = max(row["last_ts"], document["ts"])
        return [
            {"_id": {"device": device, "period_start": hour}, **row}
            for (device, hour), row in sorted(hours.items())
        ]

    def earlier_counts(self, collection, device, before):
        periods = self.counts[collection][device]
        return [periods[start] for start in sorted(periods) if start < before]

    def merge_summaries(self, unit, devices, start, end, expected):
        if self.failures:
            raise self.failures.pop(0)
        day = start.date().isoformat()
        self.status_during_merge.append(self.store.runs[day]["status"])
        self.merges.append((unit, day, dict(expected)))
        rows = self.hour_counts(devices, start, end)
        for row in rows:
            device, hour = row["_id"]["device"], row["_id"]["period_start"]
            if unit == "hour":
                self.counts[HOURLY][device][hour] = row["reading_count"]
        if unit == "day":
            for device in devices:
                self.counts[DAILY][device][start] = sum(
                    row["reading_count"] for row in rows if row["_id"]["device"] == device
                )

    def merge_latest(self, devices, start, end):
        self.latest_merges.append((start.date().isoformat(), list(devices)))


class FakeStore:
    """The questions and writes of `Store`, answered from dictionaries."""

    def __init__(self):
        self.derived = FakeDerived(self)
        self.readings = {}
        self.runs = {}
        self.insert_calls = 0
        # Exceptions raised by the next calls of insert_readings, one per call.
        self.insert_failures = []
        # Documents of a failing call that are written before it raises.
        self.written_before_failure = 0
        self.always_fail_with = None

    def batch_run(self, key):
        return dict(self.runs[key]) if key in self.runs else None

    def start_batch_run(self, key, now):
        run = self.runs.setdefault(key, {"_id": key, "attempts": 0})
        run.update(status="running", started_at=now)
        run["attempts"] += 1
        for field in ("finished_at", "counts", "error"):
            run.pop(field, None)

    def complete_batch_run(self, key, counts, now):
        self.runs[key].update(status="completed", finished_at=now, counts=counts)

    def fail_batch_run(self, key, error, now):
        self.runs[key].update(status="failed", finished_at=now, error=error)

    def rule_state(self, device, before):
        documents = sorted(
            (d for d in self.readings.values() if d["device"] == device and d["ts"] < before),
            key=lambda d: d["ts"],
        )
        valid = [i for i, d in enumerate(documents) if not d["quality"]["flags"]]
        if not valid:
            return None, 0
        after = documents[valid[-1] + 1 :]
        streak = sum(any(f.endswith("_jump") for f in d["quality"]["flags"]) for d in after)
        return to_reading(documents[valid[-1]]), streak

    def insert_readings(self, documents):
        self.insert_calls += 1
        failure = self.always_fail_with or (
            self.insert_failures.pop(0) if self.insert_failures else None
        )
        to_write = documents[: self.written_before_failure] if failure else documents
        new = old = 0
        for document in to_write:
            if document["_id"] in self.readings:
                old += 1
            else:
                self.readings[document["_id"]] = document
                new += 1
        if failure:
            raise failure
        return new, old


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.csv"
    path.write_text(scenario())
    return str(path)


@pytest.fixture
def expected(source):
    """The flags of an uninterrupted pass of the rules over the whole source."""
    readings = [reading for batch in iter_batches(source) for reading in batch.readings]
    flags, _ = flag_readings(readings)
    return {reading.id: list(each) for reading, each in zip(readings, flags, strict=True)}


def run(settings, store, **keywords):
    return run_loader(settings, store, store.derived, **keywords)


def settings_for(source, **changes):
    return Settings(source=source, **changes)


def no_wait(sleeps=None):
    return partial(with_retries, sleep=(sleeps if sleeps is not None else []).append)


def stored_flags(store):
    return {key: document["quality"]["flags"] for key, document in store.readings.items()}


# The retry schedule.


def test_the_retry_schedule_is_two_four_eight_sixteen_and_thirty_seconds():
    assert RETRY_DELAYS == (2, 4, 8, 16, 30)


@pytest.mark.parametrize(
    "error",
    [
        AutoReconnect("lost"),
        NotPrimaryError("not primary"),
        ServerSelectionTimeoutError("no server"),
        WriteConcernError("majority not reached"),
    ],
    ids=lambda error: type(error).__name__,
)
def test_each_listed_error_is_retried_until_the_operation_succeeds(error):
    calls, sleeps = [], []

    def operation():
        calls.append(1)
        if len(calls) < 3:
            raise error
        return "done"

    assert with_retries(operation, sleep=sleeps.append) == "done"
    assert sleeps == [2, 4]


def test_six_attempts_are_made_before_the_last_error_is_raised():
    calls, sleeps = [], []

    def operation():
        calls.append(1)
        raise AutoReconnect("still down")

    with pytest.raises(AutoReconnect):
        with_retries(operation, sleep=sleeps.append)
    assert len(calls) == 6
    assert sleeps == [2, 4, 8, 16, 30]
    assert sum(sleeps) == 60


def test_an_error_that_would_fail_again_is_not_retried():
    sleeps = []

    def operation():
        raise OperationFailure("document failed validation", 121)

    with pytest.raises(OperationFailure):
        with_retries(operation, sleep=sleeps.append)
    assert sleeps == []


# The loading of batches.


def test_the_first_run_loads_every_batch_with_the_flags_of_an_uninterrupted_pass(source, expected):
    store = FakeStore()
    assert run(settings_for(source), store, retry=no_wait()) == EXIT_OK
    assert stored_flags(store) == expected
    assert [(key, r["status"], r["attempts"]) for key, r in sorted(store.runs.items())] == [
        ("2020-07-12", "completed", 1),
        ("2020-07-13", "completed", 1),
        ("2020-07-14", "completed", 1),
    ]
    assert all(
        document["batch"] == document["ts"].date().isoformat()
        for document in store.readings.values()
    )


def test_the_counts_of_a_batch_are_read_inserted_already_present_and_flagged(source):
    store = FakeStore()
    run(settings_for(source), store, retry=no_wait())
    first, second, third = (store.runs[key]["counts"] for key in sorted(store.runs))
    assert first == {"read": 15, "inserted": 15, "already_present": 0, "flagged": 2}
    # The two repeated rows of device B are read twice and stored once each, and
    # the flagged count is over stored readings, so a repeat is not counted again.
    assert second == {"read": 18, "inserted": 16, "already_present": 2, "flagged": 1}
    assert third == {"read": 12, "inserted": 12, "already_present": 0, "flagged": 0}


def test_a_second_run_skips_the_completed_batches_and_writes_nothing(source):
    store = FakeStore()
    run(settings_for(source), store, retry=no_wait())
    calls = store.insert_calls
    runs = {key: dict(value) for key, value in store.runs.items()}
    assert run(settings_for(source), store, retry=no_wait()) == EXIT_OK
    assert store.insert_calls == calls
    assert store.runs == runs


def test_without_the_batch_log_a_second_run_inserts_nothing(source, expected):
    # The identifiers alone make the load idempotent, whatever the batch log says.
    store = FakeStore()
    run(settings_for(source), store, retry=no_wait())
    store.runs.clear()
    run(settings_for(source), store, retry=no_wait())
    assert stored_flags(store) == expected
    counts = [store.runs[key]["counts"] for key in sorted(store.runs)]
    assert [c["inserted"] for c in counts] == [0, 0, 0]
    assert [c["already_present"] for c in counts] == [15, 18, 12]


def test_max_batches_loads_that_many_batches_and_the_next_run_loads_the_following_ones(
    source, expected
):
    store = FakeStore()
    one = settings_for(source, max_batches=1)
    for loaded in (
        ["2020-07-12"],
        ["2020-07-12", "2020-07-13"],
        ["2020-07-12", "2020-07-13", "2020-07-14"],
    ):
        assert run(one, store, retry=no_wait()) == EXIT_OK
        assert sorted(store.runs) == loaded
    assert run(one, store, retry=no_wait()) == EXIT_OK
    assert sorted(store.runs) == ["2020-07-12", "2020-07-13", "2020-07-14"]
    assert stored_flags(store) == expected


def test_the_rule_state_crosses_the_boundary_between_two_batches(source):
    # Device A is flagged twice at the end of the first day, so the first reading
    # of the second day is accepted as the new level. That is only possible if the
    # state was rebuilt from the stored readings of the first day.
    store = FakeStore()
    run(settings_for(source, max_batches=1), store, retry=no_wait())
    run(settings_for(source, max_batches=1), store, retry=no_wait())
    by_time = sorted(
        (d for d in store.readings.values() if d["device"] == A), key=lambda d: d["ts"]
    )
    last_day_one = [d["quality"]["flags"] for d in by_time if d["batch"] == "2020-07-12"][-2:]
    first_day_two = [d["quality"]["flags"] for d in by_time if d["batch"] == "2020-07-13"][:2]
    assert last_day_one == [["temp_jump"], ["temp_jump"]]
    assert first_day_two == [[], []]


# Interruption and rerun.


def interrupted_store(source, expected, keep):
    """A store in which the second batch stopped after writing some of its readings."""
    store = FakeStore()
    run(settings_for(source, max_batches=1), store, retry=no_wait())
    readings = [
        r for batch in iter_batches(source) if batch.key == "2020-07-13" for r in batch.readings
    ]
    written = [readings[i] for i in keep]
    for reading in written:
        store.readings[reading.id] = to_document(
            reading, expected[reading.id], "2020-07-13", datetime.now(UTC)
        )
    store.runs["2020-07-13"] = {
        "_id": "2020-07-13",
        "status": "running",
        "attempts": 1,
        "started_at": datetime.now(UTC),
    }
    return store, len({reading.id for reading in written})


@pytest.mark.parametrize(
    "keep",
    [[], [0, 1, 2], [0, 1, 2, 3, 4, 5], [10, 11, 12], [0, 5, 6, 12, 13, 14, 15]],
    ids=["nothing", "prefix", "longer prefix", "late rows only", "gaps"],
)
def test_a_running_batch_is_processed_again_and_ends_with_the_flags_of_an_uninterrupted_run(
    source, expected, keep
):
    # An unordered bulk write can leave gaps, so the rows that exist are not
    # necessarily a prefix. The state must come from before the batch only.
    store, written = interrupted_store(source, expected, keep)
    assert run(settings_for(source), store, retry=no_wait()) == EXIT_OK
    assert stored_flags(store) == expected
    second = store.runs["2020-07-13"]
    assert (second["status"], second["attempts"]) == ("completed", 2)
    # The day has 16 distinct identifiers. Those not written yet are inserted, and
    # every other row, including the repeated one, is already present.
    assert second["counts"]["inserted"] == 16 - written
    assert second["counts"]["read"] == 18
    assert (
        second["counts"]["inserted"] + second["counts"]["already_present"]
        == second["counts"]["read"]
    )


# Failures.


def test_a_lost_connection_is_retried_and_the_batch_still_completes(source, expected):
    store = FakeStore()
    store.insert_failures = [AutoReconnect("lost"), NotPrimaryError("election")]
    store.written_before_failure = 3
    sleeps = []
    assert run(settings_for(source), store, retry=no_wait(sleeps)) == EXIT_OK
    assert sleeps == [2, 4]
    assert stored_flags(store) == expected
    first = store.runs["2020-07-12"]["counts"]
    # The writes of the failed calls count as already present on the next call.
    assert first["inserted"] + first["already_present"] == first["read"]


def test_a_batch_that_keeps_failing_is_marked_failed_and_ends_the_run(source):
    store = FakeStore()
    store.always_fail_with = ServerSelectionTimeoutError("no primary")
    sleeps = []
    assert run(settings_for(source), store, retry=no_wait(sleeps)) == EXIT_FAILED
    assert sleeps == [2, 4, 8, 16, 30]
    assert list(store.runs) == ["2020-07-12"]
    failed = store.runs["2020-07-12"]
    assert failed["status"] == "failed"
    assert "ServerSelectionTimeoutError: no primary" in failed["error"]
    assert "finished_at" in failed


def test_a_later_batch_is_not_loaded_after_a_failure(source, expected):
    store = FakeStore()
    run(settings_for(source, max_batches=1), store, retry=no_wait())
    store.always_fail_with = AutoReconnect("down")
    assert run(settings_for(source), store, retry=no_wait()) == EXIT_FAILED
    assert sorted(store.runs) == ["2020-07-12", "2020-07-13"]
    assert store.runs["2020-07-13"]["status"] == "failed"
    store.always_fail_with = None
    assert run(settings_for(source), store, retry=no_wait()) == EXIT_OK
    assert store.runs["2020-07-13"]["attempts"] == 2
    assert "error" not in store.runs["2020-07-13"]
    assert stored_flags(store) == expected


def test_an_error_that_is_not_retryable_fails_the_batch_at_once(source):
    store = FakeStore()
    store.always_fail_with = OperationFailure("document failed validation", 121)
    sleeps = []
    assert run(settings_for(source), store, retry=no_wait(sleeps)) == EXIT_FAILED
    assert sleeps == []
    assert store.runs["2020-07-12"]["status"] == "failed"


def test_an_unreadable_source_fails_the_run_and_writes_nothing(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text(
        HEADER + '\n"1594512000.000","a","oops","50","false","0.008","false","0.02","20"\n'
    )
    store = FakeStore()
    assert run(settings_for(str(path)), store, retry=no_wait()) == EXIT_FAILED
    assert (store.readings, store.runs) == ({}, {})


def test_a_missing_source_file_fails_the_run(tmp_path):
    store = FakeStore()
    assert run(settings_for(str(tmp_path / "absent.csv")), store, retry=no_wait()) == EXIT_FAILED


# Chunks and the log.


def test_a_batch_is_written_in_chunks_of_the_configured_size(source):
    store = FakeStore()
    run(settings_for(source, chunk_size=5), store, retry=no_wait())
    # 15, 18 and 12 rows give 3, 4 and 3 chunks.
    assert store.insert_calls == 10


def test_every_batch_and_the_run_leave_one_line_each(source, caplog):
    caplog.set_level(logging.INFO, logger="citysense.loader")
    run(settings_for(source), FakeStore(), retry=no_wait())
    lines = [record.getMessage() for record in caplog.records]
    assert len(lines) == 4
    assert lines[0].startswith(
        "batch 2020-07-12 completed: read 15, inserted 15, already present 0"
    )
    assert "flagged 2" in lines[0]
    assert "duration" in lines[0]
    assert lines[3].startswith("finished: 3 batches loaded, 0 skipped, read 45, inserted 43")
    assert "already present 2, flagged 3, duration" in lines[3]


def test_a_skipped_batch_is_logged_as_skipped(source, caplog):
    store = FakeStore()
    run(settings_for(source), store, retry=no_wait())
    caplog.set_level(logging.INFO, logger="citysense.loader")
    run(settings_for(source), store, retry=no_wait())
    lines = [record.getMessage() for record in caplog.records]
    assert lines[0] == "batch 2020-07-12 skipped, already completed"
    assert lines[3].startswith("finished: 0 batches loaded, 3 skipped, read 0")


# The derived collections.


def test_the_summaries_and_the_latest_values_are_derived_inside_each_batch(source):
    store = FakeStore()
    run(settings_for(source), store, retry=no_wait())
    derived = store.derived
    assert [(unit, day) for unit, day, _ in derived.merges] == [
        (unit, day) for day in sorted(store.runs) for unit in ("hour", "day")
    ]
    assert [day for day, _ in derived.latest_merges] == sorted(store.runs)
    assert all(devices == [A, B] for _, devices in derived.latest_merges)
    # The batch is still running while its summaries are written, so a failure
    # there leaves it to be repeated and not completed without them.
    assert set(derived.status_during_merge) == {"running"}


def test_a_skipped_batch_derives_nothing(source):
    store = FakeStore()
    run(settings_for(source), store, retry=no_wait())
    merges = len(store.derived.merges)
    run(settings_for(source), store, retry=no_wait())
    assert len(store.derived.merges) == merges


def test_the_expected_counts_come_from_the_earlier_days_only(source):
    store = FakeStore()
    run(settings_for(source), store, retry=no_wait())
    by_day = {(unit, day): expected for unit, day, expected in store.derived.merges}
    # The first day has no hour that is complete and not the first of its device.
    assert by_day[("hour", "2020-07-12")] == {A: None, B: None}
    assert by_day[("day", "2020-07-12")] == {A: None, B: None}
    # On the second day A has two earlier hours and drops the first, and B has one,
    # which is its first hour and cannot give a baseline, nor can its own hour.
    assert by_day[("hour", "2020-07-13")] == {A: 1.0, B: None}
    assert by_day[("day", "2020-07-13")] == {A: 24.0, B: None}


def test_a_batch_whose_derivation_fails_is_not_completed_and_is_derived_again(source):
    store = FakeStore()
    store.derived.failures = [OperationFailure("pipeline failed", 8)]
    assert run(settings_for(source), store, retry=no_wait()) == EXIT_FAILED
    failed = store.runs["2020-07-12"]
    assert failed["status"] == "failed"
    assert "pipeline failed" in failed["error"]
    assert run(settings_for(source), store, retry=no_wait()) == EXIT_OK
    assert store.runs["2020-07-12"]["attempts"] == 2
    assert [day for day, _ in store.derived.latest_merges][0] == "2020-07-12"
