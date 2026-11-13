"""Reading the source file as a stream and slicing it into daily batches.

A batch is one calendar day in Coordinated Universal Time (UTC), and batches are
yielded in chronological order. The file is read once and never held completely
in memory, only the batch that is being collected. Nothing is dropped
silently: a row that cannot be parsed, or a source that goes back in time,
raises an error, so that the batch fails loudly and is retried instead of
loading a wrong picture.

This module is pure and does not import pymongo, so it is tested without a database.
"""

import csv
import gzip
from dataclasses import dataclass
from datetime import date

from citysense.identity import epoch_ms, identifier, instant

NUMBER_METRICS = ("co", "humidity", "lpg", "smoke", "temp")
FLAG_METRICS = ("light", "motion")
COLUMNS = ("ts", "device", *NUMBER_METRICS, *FLAG_METRICS)

_BOOLEANS = {"true": True, "false": False}


class SourceError(ValueError):
    """The source file cannot be turned into batches, with the line that shows it."""


@dataclass(frozen=True)
class Reading:
    """One reading of one device, as it will be stored."""

    id: str
    device: str
    ms: int
    metrics: dict

    @property
    def ts(self):
        return instant(self.ms)

    @property
    def day(self):
        return self.ts.date()


@dataclass(frozen=True)
class Batch:
    """The readings of one UTC day, in the order of the source."""

    day: date
    readings: list

    @property
    def key(self):
        """The day as `YYYY-MM-DD`, which is the identifier of the batch run."""
        return self.day.isoformat()


def _parse_row(row):
    if None in row:
        raise ValueError("the row has more fields than the header")
    missing = [column for column in COLUMNS if row[column] is None]
    if missing:
        raise ValueError(f"the row has no value for {', '.join(missing)}")
    metrics = {}
    for name in NUMBER_METRICS:
        try:
            metrics[name] = float(row[name])
        except ValueError as error:
            raise ValueError(f"{name} is not a number: {row[name]!r}") from error
    for name in FLAG_METRICS:
        try:
            metrics[name] = _BOOLEANS[row[name].strip().lower()]
        except KeyError as error:
            raise ValueError(f"{name} is not true or false: {row[name]!r}") from error
    device = row["device"]
    ms = epoch_ms(row["ts"])
    return Reading(id=identifier(device, ms), device=device, ms=ms, metrics=metrics)


def parse_readings(lines):
    """Yield the readings of CSV lines, in order, and check their order per device.

    Time must not go backwards for one device. Equal timestamps are allowed,
    because the source contains rows that are exact duplicates of each other, and
    those have to reach the identifier logic that absorbs them. A strict check
    would fail the batch on them.
    """
    reader = csv.DictReader(lines)
    header = reader.fieldnames or []
    unknown = [name for name in header if name not in COLUMNS]
    absent = [name for name in COLUMNS if name not in header]
    if unknown or absent:
        # A new column is a new sensor metric. It has to be added on purpose,
        # because silently ignoring it would drop measured data.
        raise SourceError(f"unexpected header, unknown columns {unknown}, missing columns {absent}")
    last_ms = {}
    for row in reader:
        try:
            reading = _parse_row(row)
        except ValueError as error:
            raise SourceError(f"line {reader.line_num}: {error}") from error
        if reading.ms < last_ms.get(reading.device, reading.ms):
            raise SourceError(
                f"line {reader.line_num}: device {reading.device} goes back in time "
                f"to {reading.ts.isoformat()}"
            )
        last_ms[reading.device] = reading.ms
        yield reading


def slice_days(readings):
    """Group readings into one batch per UTC day, in chronological order.

    The day is taken from the truncated millisecond, like the identifier, so
    that a reading just before midnight is never moved to the next day by
    rounding. A reading from an earlier day than the batch in progress cannot be
    placed, because that batch has been handed on already, so it is an error.
    """
    current = None
    collected = []
    for reading in readings:
        day = reading.day
        if current is None:
            current = day
        if day != current:
            if day < current:
                raise SourceError(
                    f"reading {reading.id} belongs to {day.isoformat()}, "
                    f"but the batch of {current.isoformat()} is already complete"
                )
            yield Batch(current, collected)
            current, collected = day, []
        collected.append(reading)
    if collected:
        yield Batch(current, collected)


def _open_text(path):
    if str(path).endswith(".gz"):
        return gzip.open(path, mode="rt", encoding="utf-8", newline="")
    return open(path, encoding="utf-8", newline="")


def iter_batches(path):
    """Yield the daily batches of a CSV file, compressed or not."""
    with _open_text(path) as handle:
        yield from slice_days(parse_readings(handle))
