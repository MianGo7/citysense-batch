"""Alert conditions, deduplication, hysteresis and daily caps.

This module is pure and does not import pymongo, so it is tested without a database.

The rules follow ADR-0007 and ADR-0014. A threshold alert is judged on the hourly
mean of the valid readings of one metric, a sensor silent alert on the coverage of
one hour, and a batch failed alert on the loader itself. Every condition is judged
only for hours that are complete, which are the hours that end at or before the
horizon, the newest reading loaded for any device. Without this rule the last hour
of the data would look as if every sensor had gone silent.

An alert is an episode of consecutive hours in which the condition holds. It is
identified by the first hour of the episode, so that the same episode always has
the same identifier, and it is resolved when the next complete hour is clear. The
documents of the alerts are a function of the summaries and of the limits and
contain no wall clock time, except for the alerts about the loader, so that
evaluating a day again leaves them unchanged.
"""

import math
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from types import MappingProxyType

from citysense.batches import NUMBER_METRICS

HOUR = timedelta(hours=1)

THRESHOLD = "threshold"
SENSOR_SILENT = "sensor_silent"
BATCH_FAILED = "batch_failed"

OPEN = "open"
ACKNOWLEDGED = "acknowledged"
RESOLVED = "resolved"

WARNING = "warning"
CRITICAL = "critical"

# The source of an alert about the loader, which has no device.
LOADER = "loader"
HOURLY_SUMMARIES = "summaries_hourly"
BATCH_RUNS = "batch_runs"

SEVERITY = {THRESHOLD: WARNING, SENSOR_SILENT: WARNING, BATCH_FAILED: CRITICAL}

# One fixed sentence per type, so that an alert says what to do and not only what
# was seen. The sentence for silence says that the values are unknown, because a
# silent sensor must not be read as an all clear.
RECOMMENDED_ACTION = {
    THRESHOLD: "Check the location and inform the affected district.",
    SENSOR_SILENT: (
        "Inspect the sensor and its connection, and treat its values as unknown, not as normal."
    ),
    BATCH_FAILED: "Inspect the loader log and rerun the batch.",
}

METRIC_NAMES = {
    "co": "carbon monoxide",
    "humidity": "humidity",
    "lpg": "liquefied petroleum gas",
    "smoke": "smoke",
    "temp": "temperature",
}

# The text of a message is short, because it is read in a list. The complete error
# stays in the batch log.
MAX_MESSAGE_ERROR = 200

_SECTIONS = {"limits", "silence", "cap"}


class ThresholdError(ValueError):
    """The limits of the alerts have a value that cannot be used."""


@dataclass(frozen=True)
class Thresholds:
    """The limits of the alert rules, read from `config/alert_thresholds.toml`.

    `limits` maps a metric to the hourly mean above which a threshold alert is
    raised. A metric without an entry is not judged, which is how an operator
    switches one off. `coverage` is the hourly coverage below which a sensor is
    silent, and `cap` the number of alerts that a source may raise per UTC day.
    """

    limits: MappingProxyType
    coverage: float
    cap: int

    @classmethod
    def from_mapping(cls, data):
        unknown = set(data) - _SECTIONS
        if unknown:
            raise ThresholdError(f"unknown section {sorted(unknown)[0]!r}")
        missing = _SECTIONS - set(data)
        if missing:
            raise ThresholdError(f"missing section {sorted(missing)[0]!r}")

        limits = data["limits"]
        for metric, value in limits.items():
            if metric not in NUMBER_METRICS:
                raise ThresholdError(f"limits.{metric}: not a metric of the readings")
            _require_number(f"limits.{metric}", value)

        silence = _only(data["silence"], "silence", {"coverage"})
        coverage = _require_number("silence.coverage", silence["coverage"])
        if not 0 < coverage <= 1:
            raise ThresholdError(f"silence.coverage must lie above 0 and up to 1, got {coverage}")

        cap = _only(data["cap"], "cap", {"per_source_and_day"})["per_source_and_day"]
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
            raise ThresholdError(f"cap.per_source_and_day must be a positive whole number: {cap!r}")

        return cls(
            limits=MappingProxyType({metric: float(value) for metric, value in limits.items()}),
            coverage=float(coverage),
            cap=cap,
        )


def _only(table, name, keys):
    if not isinstance(table, dict):
        raise ThresholdError(f"{name} must be a table")
    for key in table:
        if key not in keys:
            raise ThresholdError(f"{name}.{key}: unknown entry")
    for key in keys:
        if key not in table:
            raise ThresholdError(f"{name}.{key}: missing")
    return table


def _require_number(name, value):
    # A bool is an int in Python, and a limit of true would pass silently.
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ThresholdError(f"{name} must be a finite number, got {value!r}")
    return value


# Time and identifiers.


def floor_hour(moment):
    return moment.replace(minute=0, second=0, microsecond=0)


def last_complete_hour(horizon):
    """Return the start of the newest hour that ends at or before the horizon."""
    return floor_hour(horizon) - HOUR


def iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def alert_id(kind, source, metric, hour):
    """Return the identifier `<type>|<source>|<metric or ->|<ISO start hour or ->`."""
    return "|".join((kind, source, metric or "-", iso(hour) if hour is not None else "-"))


def number(value):
    """Return a number as short text that keeps a decimal point, for the messages."""
    text = f"{value:.4g}"
    return text if "." in text or "e" in text else f"{text}.0"


# Episodes.


@dataclass(frozen=True)
class Episode:
    """Consecutive hours in which one condition held, as found in the summaries.

    `value` is the most extreme hour, the highest mean or the lowest coverage, and
    `basis` is the start of that hour if a summary of it exists. An hour in which
    the device sent nothing has no summary, so its basis is None.
    """

    kind: str
    source: str
    metric: str | None
    first: object
    last: object
    occurrences: int
    value: float
    limit: float
    basis: object


def _episodes(kind, source, metric, breaches, limit, worse):
    """Group the breaches, sorted by hour, into episodes separated by a clear hour."""
    runs, current = [], []
    for breach in breaches:
        if current and breach[0] - current[-1][0] > HOUR:
            runs.append(current)
            current = []
        current.append(breach)
    if current:
        runs.append(current)

    episodes = []
    for run in runs:
        extreme = run[0]
        for breach in run[1:]:
            if worse(breach[1], extreme[1]):
                extreme = breach
        hour, value, summarised = extreme
        episodes.append(
            Episode(
                kind=kind,
                source=source,
                metric=metric,
                first=run[0][0],
                last=run[-1][0],
                occurrences=len(run),
                value=value,
                limit=limit,
                basis=hour if summarised else None,
            )
        )
    return episodes


def _mean(row, metric):
    return row.get("metrics", {}).get(metric, {}).get("mean")


def threshold_episodes(device, metric, rows, limit, last_hour):
    """Return the episodes in which the hourly mean of a metric is above its limit.

    `rows` are the hourly summaries of the device in time order. An hour whose
    mean is null, because no valid reading carried the metric, does not breach.
    """
    breaches = [
        (row["period_start"], _mean(row, metric), True)
        for row in rows
        if row["period_start"] <= last_hour
        and _mean(row, metric) is not None
        and _mean(row, metric) > limit
    ]
    return _episodes(THRESHOLD, device, metric, breaches, limit, lambda new, old: new > old)


def _has_baseline(row):
    return (row.get("expected_count") or 0) > 0


def silent_episodes(device, rows, limit, grid_start, last_hour, baseline):
    """Return the episodes in which the coverage of the device is below the limit.

    Every hour from `grid_start` to `last_hour` is judged, so that an hour without
    a summary, in which the device sent no reading at all, counts as a coverage of
    zero. That hour has no summary because nothing was aggregated. The first hour
    of a device is left out by the caller, because it is partial by construction.
    An hour can only be judged once the device has a baseline, which `baseline`
    tells for the hours before the rows, and a row with a null coverage is unknown
    and does not breach.
    """
    by_hour = {row["period_start"]: row for row in rows}
    known = baseline or any(_has_baseline(row) for row in rows if row["period_start"] < grid_start)
    breaches = []
    hour = grid_start
    while hour <= last_hour:
        row = by_hour.get(hour)
        if row is None:
            value, summarised = (0.0, False) if known else (None, False)
        else:
            known = known or _has_baseline(row)
            value, summarised = row.get("coverage"), True
        if value is not None and value < limit:
            breaches.append((hour, value, summarised))
        hour += HOUR
    return _episodes(SENSOR_SILENT, device, None, breaches, limit, lambda new, old: new < old)


def device_episodes(device, rows, thresholds, first_hour, origin, last_hour, baseline):
    """Return every episode of one device that the summaries show up to `last_hour`.

    `origin` is where the rows start. The silence judgement starts one hour after
    the first summary of the device.
    """
    episodes = []
    for metric in sorted(thresholds.limits):
        episodes += threshold_episodes(device, metric, rows, thresholds.limits[metric], last_hour)
    grid_start = max(origin, first_hour + HOUR)
    episodes += silent_episodes(device, rows, thresholds.coverage, grid_start, last_hour, baseline)
    return episodes


# Alert documents.


def _message(episode):
    if episode.kind == THRESHOLD:
        name = METRIC_NAMES.get(episode.metric, episode.metric)
        return (
            f"Hourly mean {name} {number(episode.value)} above limit "
            f"{number(episode.limit)} at {episode.source}"
        )
    return (
        f"Hourly coverage {number(episode.value)} below limit "
        f"{number(episode.limit)} at {episode.source}"
    )


def to_alert(episode, last_hour):
    """Return the document of an episode, resolved if the next hour is complete and clear.

    The episode ends with its last breaching hour, and the hour after it is clear
    by construction, so the alert is resolved as soon as that hour is complete.
    The time of resolution is the end of that clear hour, which depends on the data
    and not on the clock.
    """
    resolved = episode.last + HOUR <= last_hour
    document = {
        "_id": alert_id(episode.kind, episode.source, episode.metric, episode.first),
        "type": episode.kind,
        "severity": SEVERITY[episode.kind],
        "status": RESOLVED if resolved else OPEN,
        "source": episode.source,
        "device": episode.source,
        "value": episode.value,
        "threshold": episode.limit,
        "basis": (
            None
            if episode.basis is None
            else {
                "collection": HOURLY_SUMMARIES,
                "_id": {"device": episode.source, "period_start": episode.basis},
            }
        ),
        "message": _message(episode),
        "recommended_action": RECOMMENDED_ACTION[episode.kind],
        "first_seen": episode.first,
        "last_seen": episode.last,
        "occurrences": episode.occurrences,
    }
    if episode.metric is not None:
        document["metric"] = episode.metric
    if resolved:
        document["resolved_at"] = episode.last + 2 * HOUR
    return document


# Reconciling the episodes with the stored alerts.


@dataclass
class Plan:
    """What an evaluation changes: new alerts, replaced alerts and the counts."""

    inserts: list
    replacements: list
    opened: int = 0
    resolved: int = 0
    suppressed: int = 0


def _sort_key(document):
    return (document["first_seen"], document["type"], document.get("metric", ""))


def _carried(existing, candidate):
    """Return the candidate with what the stored alert holds that the data cannot know."""
    merged = dict(candidate)
    if existing["status"] == ACKNOWLEDGED and candidate["status"] == OPEN:
        merged["status"] = ACKNOWLEDGED
    if "acknowledged_at" in existing:
        merged["acknowledged_at"] = existing["acknowledged_at"]
    return merged


def reconcile(candidates, stored, cap, last_hour):
    """Compare the alerts that the summaries show with the stored ones.

    Deduplication is by identifier: an episode that is stored is updated, never
    raised again. A stored alert that is resolved is final, so a later change of a
    limit does not rewrite the history. A stored alert that is still unresolved
    but has no episode any more, because its limit was raised or its basis was
    superseded, is resolved. A new alert is raised only while its source has fewer
    than `cap` alerts that began on the same UTC day, otherwise it is counted as
    suppressed. The candidates are taken in time order, so that the alerts that
    are kept when the cap is reached are the earliest ones, whatever the order in
    which the caller found them.
    """
    plan = Plan(inserts=[], replacements=[])
    by_id = {document["_id"]: document for document in stored}
    per_day = Counter((document["source"], document["first_seen"].date()) for document in stored)

    for candidate in sorted(candidates, key=_sort_key):
        existing = by_id.get(candidate["_id"])
        if existing is None:
            day = (candidate["source"], candidate["first_seen"].date())
            if per_day[day] >= cap:
                plan.suppressed += 1
                continue
            per_day[day] += 1
            plan.inserts.append(candidate)
            plan.opened += 1
            plan.resolved += candidate["status"] == RESOLVED
        elif existing["status"] != RESOLVED:
            merged = _carried(existing, candidate)
            if merged != existing:
                plan.replacements.append((existing, merged))
                plan.resolved += merged["status"] == RESOLVED

    found = {candidate["_id"] for candidate in candidates}
    for existing in stored:
        unresolved = existing["status"] != RESOLVED
        if unresolved and existing["type"] != BATCH_FAILED and existing["_id"] not in found:
            ended = {**existing, "status": RESOLVED, "resolved_at": last_hour + HOUR}
            plan.replacements.append((existing, ended))
            plan.resolved += 1
    return plan


# Alerts about the loader.


def _short(error):
    return str(error)[:MAX_MESSAGE_ERROR]


def batch_failed_alert(existing, day, error, now):
    """Return the alert of a failed batch, or of an unreadable source if `day` is None.

    A second failure of the same batch updates the alert and counts an occurrence,
    and reopens it if it was resolved, because the condition has returned. An
    acknowledged alert stays acknowledged. The times are the wall clock, because
    unlike a threshold alert this one has no hour of data to be derived from.
    """
    if day is None:
        what = f"The source file cannot be read: {_short(error)}"
        hour, basis = None, None
    else:
        what = f"Batch {day.isoformat()} failed: {_short(error)}"
        hour = _midnight(day)
        basis = {"collection": BATCH_RUNS, "_id": day.isoformat()}
    identifier = alert_id(BATCH_FAILED, LOADER, None, hour)
    if existing is None:
        return {
            "_id": identifier,
            "type": BATCH_FAILED,
            "severity": CRITICAL,
            "status": OPEN,
            "source": LOADER,
            "basis": basis,
            "message": what,
            "recommended_action": RECOMMENDED_ACTION[BATCH_FAILED],
            "first_seen": now,
            "last_seen": now,
            "occurrences": 1,
        }
    updated = {key: value for key, value in existing.items() if key != "resolved_at"}
    updated.update(
        status=ACKNOWLEDGED if existing["status"] == ACKNOWLEDGED else OPEN,
        message=what,
        last_seen=now,
        occurrences=existing["occurrences"] + 1,
    )
    return updated


def batch_failed_id(day):
    """Return the identifier that `batch_failed_alert` gives to a day, or to the source."""
    return alert_id(BATCH_FAILED, LOADER, None, None if day is None else _midnight(day))


def resolved_alert(existing, now):
    """Return the alert as resolved, or None if it is resolved already."""
    if existing is None or existing["status"] == RESOLVED:
        return None
    return {**existing, "status": RESOLVED, "resolved_at": now}


def _midnight(day):
    return datetime.combine(day, time.min, tzinfo=UTC)
