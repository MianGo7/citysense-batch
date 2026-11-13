"""Pipelines that derive the summaries and the latest values from the readings.

After the readings of a batch are stored, the hourly and daily summaries of that
day and the latest value of every device are computed by aggregation pipelines
that end in `$merge`, so that the database does the work next to the data and
nothing is read into the loader beyond a few counts (ADR-0013). A summary covers
the valid readings only. A reading with a flag is counted in `flagged_count` and
nowhere else, so a flagged value cannot reach a mean, a minimum or a maximum.

Every pipeline can be run again for the same day. The summaries are replaced by
documents that depend on nothing but the readings of the day and the baseline of
earlier days, so a rerun leaves them unchanged. The latest value is replaced only
by a reading that is not older than the stored one, so that loading an earlier day
again can never move it backwards.

The functions that build pipelines and the rules for the expected count are pure.
Only `Derived` talks to the database.
"""

from datetime import timedelta
from statistics import median

from citysense.batches import FLAG_METRICS, NUMBER_METRICS

HOURLY = "summaries_hourly"
DAILY = "summaries_daily"
LATEST = "latest"

HOURS_PER_DAY = 24
HOUR = timedelta(hours=1)

# A reading is valid exactly when its list of flags is empty (ADR-0010).
VALID = {"$eq": [{"$size": "$quality.flags"}, 0]}

# The summary collections are keyed by device and period, see ADR-0010.
MERGE_REPLACING = {"on": "_id", "whenMatched": "replace", "whenNotMatched": "insert"}


def _window(devices, start, end, **extra):
    # Device first, then the time range, so that the index `device_ts` serves the
    # selection. A match on the field `batch` would read the whole collection.
    return {
        "$match": {
            "device": {"$in": sorted(devices)},
            "ts": {"$gte": start, "$lt": end},
            **extra,
        }
    }


def _period(unit):
    return {"device": "$device", "period_start": {"$dateTrunc": {"date": "$ts", "unit": unit}}}


def hour_counts_pipeline(devices, start, end):
    """Count the valid readings per device and hour, and find the newest reading.

    This is read before the summaries are written, because the expected count of
    a device may have to come from the hours of the day itself.
    """
    return [
        _window(devices, start, end),
        {
            "$group": {
                "_id": _period("hour"),
                "reading_count": {"$sum": {"$cond": [VALID, 1, 0]}},
                "last_ts": {"$max": "$ts"},
            }
        },
        {"$sort": {"_id.device": 1, "_id.period_start": 1}},
    ]


def _expected_expression(expected):
    branches = [
        {"case": {"$eq": ["$device", {"$literal": device}]}, "then": value}
        for device, value in sorted(expected.items())
        if value is not None
    ]
    if not branches:
        return None
    return {"$switch": {"branches": branches, "default": None}}


def summary_pipeline(unit, devices, start, end, expected, into):
    """Return the pipeline of the summaries of one unit, `hour` or `day`.

    `expected` maps a device to its expected reading count for one period, or to
    None if there is no baseline yet. The statistics are taken over valid readings
    only, so an hour whose readings are all flagged still gets a document, with a
    count of zero and null statistics, and an hour without any reading gets none.
    The readings are sorted before they are grouped, so that the floating point
    sum of a mean is formed in the same order on every run.
    """
    accumulators = {
        "reading_count": {"$sum": {"$cond": [VALID, 1, 0]}},
        "flagged_count": {"$sum": {"$cond": [VALID, 0, 1]}},
    }
    metrics = {}
    for name in NUMBER_METRICS:
        value = {"$cond": [VALID, f"${name}", None]}
        accumulators[f"{name}_min"] = {"$min": value}
        accumulators[f"{name}_max"] = {"$max": value}
        accumulators[f"{name}_mean"] = {"$avg": value}
        metrics[name] = {
            "min": f"${name}_min",
            "max": f"${name}_max",
            "mean": f"${name}_mean",
        }
    for name in FLAG_METRICS:
        is_true = {"$and": [VALID, {"$eq": [f"${name}", True]}]}
        accumulators[f"{name}_true"] = {"$sum": {"$cond": [is_true, 1, 0]}}

    return [
        _window(devices, start, end),
        {"$sort": {"device": 1, "ts": -1}},
        {"$group": {"_id": _period(unit), **accumulators}},
        {
            "$project": {
                "device": "$_id.device",
                "period_start": "$_id.period_start",
                "reading_count": 1,
                "flagged_count": 1,
                "metrics": metrics,
                **{f"{name}_true": 1 for name in FLAG_METRICS},
            }
        },
        {"$addFields": {"expected_count": _expected_expression(expected)}},
        {
            "$addFields": {
                "coverage": {
                    "$cond": [
                        {"$gt": ["$expected_count", 0]},
                        {"$divide": ["$reading_count", "$expected_count"]},
                        None,
                    ]
                }
            }
        },
        {"$merge": {"into": into, **MERGE_REPLACING}},
    ]


def latest_pipeline(devices, start, end):
    """Return the pipeline of the latest valid reading of each device.

    The stored document is replaced only if the new reading is not older, which
    is the guard that keeps `latest` from moving backwards when an earlier day is
    loaded again. Equal times replace, which changes nothing.
    """
    carried = ("batch", *NUMBER_METRICS, *FLAG_METRICS)
    return [
        _window(devices, start, end, **{"quality.flags": {"$size": 0}}),
        {"$sort": {"device": 1, "ts": -1}},
        {"$group": {"_id": "$device", "reading": {"$first": "$$ROOT"}}},
        {
            "$project": {
                "device": "$reading.device",
                "ts": "$reading.ts",
                "reading_id": "$reading._id",
                **{name: f"$reading.{name}" for name in carried},
            }
        },
        {
            "$merge": {
                "into": LATEST,
                "on": "_id",
                "whenMatched": [
                    {"$replaceWith": {"$cond": [{"$gte": ["$$new.ts", "$ts"]}, "$$new", "$$ROOT"]}}
                ],
                "whenNotMatched": "insert",
            }
        },
    ]


def expected_count(earlier, own):
    """Return the expected reading count of one hour of a device, or None.

    The expected count is the median reading count of the full hours of the device
    before the batch day. `earlier` holds the counts of all its earlier hourly
    summaries in time order. The first of them is dropped, because a device starts
    to report at an arbitrary moment and its first hour is therefore partial.
    A device without earlier hours takes the baseline from the hours of the day
    itself, again without its first hour, and only from hours that end at or before
    the newest reading of the batch, because the later ones are cut short by the end
    of the data. `own` holds a count and a flag for each hour of the day in time
    order, the flag telling whether the hour is complete.

    The median was chosen over the mean, because a few hours of an outage move a
    mean but not a median. The rule depends on earlier days only, so a rerun of a
    day gives the same expected count however many days follow it.
    """
    usable = earlier[1:]
    if not usable:
        skipped = 0 if earlier else 1
        usable = [count for count, complete in own[skipped:] if complete]
    return float(median(usable)) if usable else None


def expected_daily_count(earlier, hourly):
    """Return the expected reading count of one day of a device, or None.

    This is the median reading count of the earlier days without the first one,
    for the same reason as for the hours. A device with fewer days than that takes
    24 times its expected hourly count, so that the first days have a baseline.
    """
    usable = earlier[1:]
    if usable:
        return float(median(usable))
    return None if hourly is None else float(HOURS_PER_DAY * hourly)


def expected_counts(devices, rows, earlier_hours, earlier_days):
    """Return the expected hourly and daily counts of every device of a batch.

    `rows` are the hourly counts of the day as returned by the database,
    `earlier_hours` and `earlier_days` map a device to its earlier counts.
    """
    horizon = max(row["last_ts"] for row in rows)
    hourly, daily = {}, {}
    for device in devices:
        own = [
            (row["reading_count"], row["_id"]["period_start"] + HOUR <= horizon)
            for row in rows
            if row["_id"]["device"] == device
        ]
        hourly[device] = expected_count(earlier_hours[device], own)
        daily[device] = expected_daily_count(earlier_days[device], hourly[device])
    return hourly, daily


class Derived:
    """The reads and writes of the derived collections."""

    def __init__(self, database):
        self.database = database

    def hour_counts(self, devices, start, end):
        pipeline = hour_counts_pipeline(devices, start, end)
        return list(self.database["readings"].aggregate(pipeline))

    def earlier_counts(self, collection, device, before):
        """Return the reading counts of the earlier summaries of a device, in time order."""
        documents = (
            self.database[collection]
            .find({"device": device, "period_start": {"$lt": before}}, {"reading_count": 1})
            .sort("period_start", 1)
        )
        return [document["reading_count"] for document in documents]

    def merge_summaries(self, unit, devices, start, end, expected):
        into = HOURLY if unit == "hour" else DAILY
        pipeline = summary_pipeline(unit, devices, start, end, expected, into)
        list(self.database["readings"].aggregate(pipeline))

    def merge_latest(self, devices, start, end):
        list(self.database["readings"].aggregate(latest_pipeline(devices, start, end)))
