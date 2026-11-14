"""Everything that talks to MongoDB.

The functions here know the shape of the stored documents and nothing about the
order in which batches are processed, which is the business of the loader. Every
write waits for a majority of the replica set, so that an acknowledged write
survives the loss of the primary (ADR-0009 and ADR-0012).
"""

from datetime import UTC, datetime, time

from pymongo import MongoClient, UpdateOne
from pymongo.errors import BulkWriteError, WriteConcernError

from citysense.batches import FLAG_METRICS, NUMBER_METRICS, Reading
from citysense.identity import milliseconds

# Long enough to cover the measured gap of about 11 seconds between the loss of
# a primary and the election of a new one, with room for a slow start. The
# driver waits this long for a suitable server before it raises an error, and
# the loader then retries the operation (ADR-0012).
SERVER_SELECTION_TIMEOUT_MS = 30_000

METRIC_NAMES = (*NUMBER_METRICS, *FLAG_METRICS)

# A flag of the jump rule ends in this text, see `validation.py`.
JUMP_FLAG_PATTERN = "_jump$"


def connect(uri):
    """Return a client that waits for a majority and reads dates with a time zone.

    Retryable writes stay on, so that the driver repeats a single write once
    after a failover by itself. The loader adds its own retries for the longer
    outages that this does not cover.
    """
    return MongoClient(
        uri,
        w="majority",
        retryWrites=True,
        tz_aware=True,
        serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS,
        appname="citysense-loader",
    )


def day_start(day):
    """Return the first instant of a UTC day."""
    return datetime.combine(day, time.min, tzinfo=UTC)


def to_document(reading, flags, batch, ingested_at):
    """Return the document of a reading as it is stored in `readings`."""
    return {
        "_id": reading.id,
        "device": reading.device,
        "ts": reading.ts,
        "ingested_at": ingested_at,
        "batch": batch,
        "quality": {"flags": list(flags)},
        **reading.metrics,
    }


def to_reading(document):
    """Return the reading that a stored document was made from."""
    return Reading(
        id=document["_id"],
        device=document["device"],
        ms=milliseconds(document["ts"]),
        metrics={name: document[name] for name in METRIC_NAMES if name in document},
    )


def bulk_write(collection, operations):
    """Run unordered bulk operations and raise a write concern failure as its own class.

    A write that was applied on the primary but not acknowledged by a majority, for
    instance during an election, is reported by the driver inside a bulk error,
    which the loader could not tell apart from a rejected document. It is raised
    as `WriteConcernError`, which the loader retries, whereas a rejected document
    keeps its `BulkWriteError` and is not retried.
    """
    try:
        return collection.bulk_write(operations, ordered=False)
    except BulkWriteError as error:
        details = error.details
        if not details.get("writeErrors") and details.get("writeConcernErrors"):
            first = details["writeConcernErrors"][0]
            raise WriteConcernError(
                first.get("errmsg", "the write concern was not satisfied"),
                first.get("code"),
                first,
            ) from error
        raise


class Store:
    """The collections that the loader reads and writes."""

    def __init__(self, database):
        self.readings = database["readings"]
        self.batch_runs = database["batch_runs"]

    def batch_run(self, key):
        """Return the batch log entry of a day, or None if it was never started."""
        return self.batch_runs.find_one({"_id": key})

    def start_batch_run(self, key, now):
        """Record that an attempt at a batch begins and count it.

        The fields of an earlier failed attempt are removed, so that a running
        batch never shows the counts or the error of a previous try.
        """
        self.batch_runs.update_one(
            {"_id": key},
            {
                "$set": {"status": "running", "started_at": now},
                "$inc": {"attempts": 1},
                "$unset": {"finished_at": "", "counts": "", "error": ""},
            },
            upsert=True,
        )

    def complete_batch_run(self, key, counts, now):
        self.batch_runs.update_one(
            {"_id": key},
            {"$set": {"status": "completed", "finished_at": now, "counts": counts}},
        )

    def fail_batch_run(self, key, error, now):
        self.batch_runs.update_one(
            {"_id": key},
            {"$set": {"status": "failed", "finished_at": now, "error": error}},
        )

    def rule_state(self, device, before):
        """Return what the plausibility rules need to know about a device.

        The result is the last valid reading before the instant `before` and the
        number of readings after it that the jump rule flagged, which is exactly
        the reference and the streak that the rules held at that point. Only
        readings before the start of the batch are used, never the part of the
        batch that an interrupted attempt may have written, so that the flags of
        a rerun do not depend on how far the earlier attempt got.

        The streak is the count of jump flags and not of all flags, because a
        reading flagged by a range rule alone leaves the streak unchanged.
        """
        last = self.readings.find_one(
            {"device": device, "ts": {"$lt": before}, "quality.flags": {"$size": 0}},
            sort=[("ts", -1)],
        )
        if last is None:
            return None, 0
        streak = self.readings.count_documents(
            {
                "device": device,
                "ts": {"$gt": last["ts"], "$lt": before},
                "quality.flags": {"$regex": JUMP_FLAG_PATTERN},
            }
        )
        return to_reading(last), streak

    def insert_readings(self, documents):
        """Insert the readings that are new and return how many were new.

        An update with `$setOnInsert` and an upsert leaves a stored reading as it
        is, so a repeated reading, a repeated chunk and a repeated batch change
        nothing. Plain inserts were rejected, because a duplicate would raise an
        error that has to be told apart from a real failure. The writes are
        unordered, so that one rejected document does not hide the others, and
        the server may reorder them. Nothing in a chunk depends on the order.

        Returns the pair of new readings and readings that were already there.
        """
        operations = [
            UpdateOne(
                {"_id": document["_id"]},
                {"$setOnInsert": {key: value for key, value in document.items() if key != "_id"}},
                upsert=True,
            )
            for document in documents
        ]
        result = bulk_write(self.readings, operations)
        return result.upserted_count, result.matched_count
