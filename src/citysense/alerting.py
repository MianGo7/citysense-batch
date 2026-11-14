"""The reads and writes of the alerts, around the pure rules of `alert_rules.py`.

An evaluation reads the hourly summaries from the point where an alert may still
change, hands them to the rules, and writes the difference to the stored alerts.
Nothing is remembered between two evaluations except the alerts themselves, so a
day can be evaluated again at any time and leaves the alerts unchanged (ADR-0014).
"""

import logging
from datetime import UTC, datetime, time, timedelta

from pymongo import ReplaceOne, UpdateOne

from citysense.aggregation import HOURLY
from citysense.alert_rules import (
    HOUR,
    LOADER,
    RESOLVED,
    batch_failed_alert,
    batch_failed_id,
    device_episodes,
    floor_hour,
    last_complete_hour,
    reconcile,
    resolved_alert,
    to_alert,
)
from citysense.store import bulk_write

LOG = logging.getLogger("citysense.alerting")

PROJECTION = {"period_start": 1, "reading_count": 1, "coverage": 1, "expected_count": 1}


def _floor_day(moment):
    return datetime.combine(moment.date(), time.min, tzinfo=UTC)


class Alerting:
    """Evaluates the alerts of the data and records the failures of the loader."""

    def __init__(self, database, thresholds):
        self.thresholds = thresholds
        self.readings = database["readings"]
        self.hourly = database[HOURLY]
        self.alerts = database["alerts"]

    # Reads.

    def _newest(self, devices, before=None):
        newest = None
        for device in devices:
            query = {"device": device}
            if before is not None:
                query["ts"] = {"$lt": before}
            document = self.readings.find_one(query, {"ts": 1}, sort=[("ts", -1)])
            if document is not None and (newest is None or document["ts"] > newest):
                newest = document["ts"]
        return newest

    def _first_hour(self, device):
        document = self.hourly.find_one(
            {"device": device}, {"period_start": 1}, sort=[("period_start", 1)]
        )
        return None if document is None else document["period_start"]

    def _origin(self, devices, begin):
        """Return the hour from which the summaries are read.

        This is the first pending hour, and earlier if an alert that is still
        unresolved, or that ended at the hour before it, began earlier. The rules
        then see all of its hours, so that the alert keeps the identifier of its
        first hour and is not mistaken for a new one that begins at `begin`.
        """
        touching = self.alerts.find(
            {
                "source": {"$in": devices},
                "$or": [{"status": {"$ne": RESOLVED}}, {"last_seen": {"$gte": begin - HOUR}}],
            },
            {"first_seen": 1},
        )
        return min([begin, *(document["first_seen"] for document in touching)])

    # Evaluation.

    def evaluate(self, day):
        """Evaluate the complete hours up to the horizon and return the counts.

        The horizon is the newest reading of any device, and an hour is judged only
        if it ends at or before it. The first pending hour is the one that holds
        the newest reading before `day`, which was still open when the previous
        batch was evaluated. For the first batch every hour is pending. After a day
        without any data, the hours of that day are pending as well, and every
        device is silent in them.
        """
        start = datetime.combine(day, time.min, tzinfo=UTC)
        devices = sorted(self.readings.distinct("device"))
        horizon = self._newest(devices)
        firsts = {device: self._first_hour(device) for device in devices}
        firsts = {device: first for device, first in firsts.items() if first is not None}
        if horizon is None or not firsts:
            return {"alerts_opened": 0, "alerts_resolved": 0, "alerts_suppressed": 0}
        last_hour = last_complete_hour(horizon)

        prior = self._newest(devices, before=start)
        begin = floor_hour(prior) if prior is not None else min(firsts.values())
        origin = self._origin(devices, begin)

        candidates = []
        for device, first in firsts.items():
            rows_from = max(origin, first)
            rows = list(
                self.hourly.find(
                    {"device": device, "period_start": {"$gte": rows_from}},
                    {
                        **PROJECTION,
                        **{f"metrics.{metric}.mean": 1 for metric in self.thresholds.limits},
                    },
                ).sort("period_start", 1)
            )
            baseline = (
                self.hourly.count_documents(
                    {
                        "device": device,
                        "period_start": {"$lt": rows_from},
                        "expected_count": {"$gt": 0},
                    },
                    limit=1,
                )
                > 0
            )
            episodes = device_episodes(
                device, rows, self.thresholds, first, rows_from, last_hour, baseline
            )
            candidates += [to_alert(episode, last_hour) for episode in episodes]

        stored = list(
            self.alerts.find(
                {"source": {"$in": sorted(firsts)}, "first_seen": {"$gte": _floor_day(origin)}}
            )
        )
        plan = reconcile(candidates, stored, self.thresholds.cap, last_hour)
        operations = [
            UpdateOne({"_id": document["_id"]}, {"$setOnInsert": _body(document)}, upsert=True)
            for document in plan.inserts
        ] + [
            # The stored status is part of the filter, so that an alert that an
            # engineer acknowledged between the read and the write is not
            # overwritten. A replacement that finds nothing is made by the next
            # evaluation, which reads the alert again.
            ReplaceOne({"_id": old["_id"], "status": old["status"]}, new)
            for old, new in plan.replacements
        ]
        if operations:
            bulk_write(self.alerts, operations)
        return {
            "alerts_opened": plan.opened,
            "alerts_resolved": plan.resolved,
            "alerts_suppressed": plan.suppressed,
        }

    # The alerts about the loader.

    def _raise(self, identifier, build):
        existing = self.alerts.find_one({"_id": identifier})
        document = build(existing)
        if existing is None:
            now = document["first_seen"]
            # The cap counts the alerts of the loader that began on the same day.
            began = self.alerts.count_documents(
                {
                    "source": LOADER,
                    "first_seen": {
                        "$gte": _floor_day(now),
                        "$lt": _floor_day(now) + timedelta(days=1),
                    },
                }
            )
            if began >= self.thresholds.cap:
                LOG.warning("alert %s suppressed, the cap of %d is reached", identifier, began)
                return False
            self.alerts.update_one(
                {"_id": identifier}, {"$setOnInsert": _body(document)}, upsert=True
            )
        else:
            self.alerts.replace_one({"_id": identifier}, document)
        return True

    def batch_failed(self, day, error, now):
        """Raise or update the alert of a batch that failed after its retries."""
        return self._raise(
            batch_failed_id(day), lambda existing: batch_failed_alert(existing, day, error, now)
        )

    def source_failed(self, error, now):
        """Raise or update the alert of a source that cannot be read."""
        return self._raise(
            batch_failed_id(None), lambda existing: batch_failed_alert(existing, None, error, now)
        )

    def _resolve(self, identifier, now):
        existing = self.alerts.find_one({"_id": identifier})
        document = resolved_alert(existing, now)
        if document is not None:
            self.alerts.replace_one({"_id": identifier}, document)

    def batch_completed(self, day, now):
        """Resolve the alert of a batch that has completed since."""
        self._resolve(batch_failed_id(day), now)

    def source_read(self, now):
        """Resolve the alert of a source that can be read again."""
        self._resolve(batch_failed_id(None), now)


def _body(document):
    return {key: value for key, value in document.items() if key != "_id"}
