"""Batch orchestration, the batch log and retries.

The loader walks through the daily batches of the source in chronological order.
A completed batch is skipped. Any other batch is processed from its first row,
whatever an earlier attempt left behind, because a reading that is stored already
is not written again (ADR-0004 and ADR-0012). A resumable checkpoint inside a
batch was rejected: it would add a second kind of state to keep consistent and
would gain nothing, since repeating a chunk costs only a comparison of identifiers.

The flags of a reading depend on the readings before it, so the rule state of
every device is rebuilt from the readings that were stored before the start of the
batch, and from nothing else. A rerun therefore computes the same flags as an
uninterrupted run, however far the interrupted attempt got.
"""

import logging
import time
from collections import Counter
from datetime import UTC, datetime
from functools import partial

from pymongo.errors import (
    AutoReconnect,
    NotPrimaryError,
    PyMongoError,
    ServerSelectionTimeoutError,
    WriteConcernError,
)

from citysense.batches import SourceError, iter_batches
from citysense.store import day_start, to_document
from citysense.validation import DeviceState, flag_readings

LOG = logging.getLogger("citysense.loader")

# Waiting times in seconds between the attempts at one operation, which makes six
# attempts and about one minute in total. The measured gap between the loss of a
# primary and the next one is about 11 seconds, so the schedule covers it several
# times over. Shorter waits would exhaust the attempts inside one election, and
# a fixed wait would hammer a recovering node, hence the exponential growth with
# a cap (ADR-0012).
RETRY_DELAYS = (2, 4, 8, 16, 30)

# The errors of a cluster that is changing its primary or is out of reach for a
# while. Anything else, such as a document that the validator rejects, would fail
# again in the same way, so it is not retried.
RETRYABLE = (AutoReconnect, NotPrimaryError, ServerSelectionTimeoutError, WriteConcernError)

# The error text kept in the batch log is bounded, because it is read by people.
MAX_ERROR_LENGTH = 1000

EXIT_OK = 0
EXIT_FAILED = 1


def with_retries(operation, delays=RETRY_DELAYS, sleep=time.sleep):
    """Call `operation` and repeat it after a retryable error, once per delay.

    The last error is raised when the delays are used up. The operation has to
    be safe to repeat, which holds for every write of the loader.
    """
    for delay in (*delays, None):
        try:
            return operation()
        except RETRYABLE as error:
            if delay is None:
                raise
            LOG.warning("retrying in %s seconds after %s: %s", delay, type(error).__name__, error)
            sleep(delay)
    raise AssertionError("unreachable, the last delay is None")  # pragma: no cover


def rebuild_states(store, devices, before, retry):
    """Return the rule state of each device as it was at the instant `before`."""
    states = {}
    for device in sorted(devices):
        reference, streak = retry(partial(store.rule_state, device, before))
        states[device] = DeviceState(reference=reference, jump_streak=streak)
    return states


def load_batch(store, batch, chunk_size, retry=with_retries, clock=time.monotonic):
    """Load one batch and return its counts.

    The counts are those of this attempt. After an interrupted attempt, the
    readings that it wrote count as already present, so `inserted` is then lower
    than `read` minus the repeated rows. The number of stored readings of the day
    is the sum of the two, whatever the history of the batch.
    """
    key = batch.key
    readings = batch.readings
    retry(partial(store.start_batch_run, key, datetime.now(UTC)))

    devices = {reading.device for reading in readings}
    states = rebuild_states(store, devices, day_start(batch.day), retry)
    flags, _ = flag_readings(readings, states)

    ingested_at = datetime.now(UTC)
    inserted = already_present = 0
    for first in range(0, len(readings), chunk_size):
        last = first + chunk_size
        documents = [
            to_document(reading, reading_flags, key, ingested_at)
            for reading, reading_flags in zip(readings[first:last], flags[first:last], strict=True)
        ]
        new, old = retry(partial(store.insert_readings, documents))
        inserted += new
        already_present += old

    # Distinct identifiers, because a repeated row receives the flags of the
    # first one and is stored once, so it must not be counted twice.
    flagged = len({r.id for r, reading_flags in zip(readings, flags, strict=True) if reading_flags})
    counts = {
        "read": len(readings),
        "inserted": inserted,
        "already_present": already_present,
        "flagged": flagged,
    }
    retry(partial(store.complete_batch_run, key, counts, datetime.now(UTC)))
    return counts


def _error_text(error):
    return f"{type(error).__name__}: {error}"[:MAX_ERROR_LENGTH]


def _mark_failed(store, key, error, retry):
    # A failure to write the failure is only logged, because the exit code
    # already tells the caller that the run failed.
    try:
        retry(partial(store.fail_batch_run, key, _error_text(error), datetime.now(UTC)))
    except PyMongoError as second:
        LOG.error("could not record the failure of batch %s: %s", key, second)


def _log_totals(outcome, totals, loaded, skipped, began, clock):
    LOG.info(
        "%s: %d batches loaded, %d skipped, read %d, inserted %d, already present %d, "
        "flagged %d, duration %.1f s",
        outcome,
        loaded,
        skipped,
        totals["read"],
        totals["inserted"],
        totals["already_present"],
        totals["flagged"],
        clock() - began,
    )


def run(settings, store, retry=with_retries, clock=time.monotonic):
    """Process the batches of the source and return the exit code of the process.

    The code is 0 when every batch is completed or was skipped as completed, and 1
    when a batch failed, which also ends the run, because later batches must not
    be loaded before an earlier one, or the rule state would be wrong.
    """
    began = clock()
    totals = Counter()
    loaded = skipped = 0
    try:
        for batch in iter_batches(settings.source):
            batch_began = clock()
            try:
                existing = retry(partial(store.batch_run, batch.key))
                if existing is not None and existing["status"] == "completed":
                    skipped += 1
                    LOG.info("batch %s skipped, already completed", batch.key)
                    continue
                counts = load_batch(store, batch, settings.chunk_size, retry, clock)
            except PyMongoError as error:
                LOG.error("batch %s failed: %s", batch.key, _error_text(error))
                _mark_failed(store, batch.key, error, retry)
                _log_totals("failed", totals, loaded, skipped, began, clock)
                return EXIT_FAILED

            loaded += 1
            totals.update(counts)
            LOG.info(
                "batch %s completed: read %d, inserted %d, already present %d, flagged %d, "
                "duration %.1f s",
                batch.key,
                counts["read"],
                counts["inserted"],
                counts["already_present"],
                counts["flagged"],
                clock() - batch_began,
            )
            if settings.max_batches is not None and loaded >= settings.max_batches:
                LOG.info("stopped after %d batches, as MAX_BATCHES asks", loaded)
                break
    except (SourceError, OSError, EOFError) as error:
        # The source is read before anything of the batch is written, so no
        # batch log entry exists for the day that could not be read.
        LOG.error("the source cannot be read: %s", _error_text(error))
        _log_totals("failed", totals, loaded, skipped, began, clock)
        return EXIT_FAILED

    _log_totals("finished", totals, loaded, skipped, began, clock)
    return EXIT_OK
