"""Count the alerts that each candidate limit raises over the stored sample week.

ADR-0014 chooses the limits of the threshold alerts from the measured hourly
means, and rejects the lower and the higher candidates with the number of alerts
that they would raise. A figure in a document has to be produced by a script or a
test, so this script reproduces them from the hourly summaries that the loader
wrote, with the same rules that the loader applies, and an integration test pins
them. It reads the database and writes nothing.

Usage, with the Compose stack running and loaded:
    uv run python scripts/alert_candidates.py
"""

from pymongo import MongoClient

from citysense.alert_rules import last_complete_hour, threshold_episodes

URI = "mongodb://127.0.0.1:27017/?directConnection=true"
DATABASE = "citysense"

# The lower, the chosen and the higher candidate of each metric, as proposed.
CANDIDATES = {
    "temp": (25.0, 28.0, 30.0),
    "humidity": (80.0, 85.0, 90.0),
    "co": (0.006, 0.0075, 0.010),
    "lpg": (0.009, 0.010, 0.012),
    "smoke": (0.025, 0.030, 0.035),
}


def horizon_hour(database):
    """Return the start of the last complete hour, as the loader judges it."""
    newest = [
        database["readings"].find_one({"device": device}, {"ts": 1}, sort=[("ts", -1)])["ts"]
        for device in database["readings"].distinct("device")
    ]
    return last_complete_hour(max(newest))


def hourly_rows(database):
    """Return the hourly summaries per device, in time order."""
    rows = {}
    for row in database["summaries_hourly"].find().sort("period_start", 1):
        rows.setdefault(row["device"], []).append(row)
    return rows


def candidate_counts(database):
    """Return `{(metric, limit): {device: (hours above the limit, alerts)}}`.

    An alert is an episode of consecutive hours above the limit, as the rules
    define it, so a run of hours counts once.
    """
    last = horizon_hour(database)
    rows = hourly_rows(database)
    counts = {}
    for metric, limits in CANDIDATES.items():
        for limit in limits:
            counts[(metric, limit)] = {}
            for device, device_rows in sorted(rows.items()):
                episodes = threshold_episodes(device, metric, device_rows, limit, last)
                counts[(metric, limit)][device] = (
                    sum(episode.occurrences for episode in episodes),
                    len(episodes),
                )
    return counts


def lowest_full_hour_coverage(database):
    """Return the lowest coverage per device over the hours that the rules judge.

    The first hour of a device and the hours after the horizon are left out, as
    they are for the silent alerts.
    """
    last = horizon_hour(database)
    lowest = {}
    for device, device_rows in sorted(hourly_rows(database).items()):
        judged = [
            row["coverage"]
            for row in device_rows[1:]
            if row["period_start"] <= last and row["coverage"] is not None
        ]
        lowest[device] = min(judged)
    return lowest


def render(counts, lowest):
    """Format the figures as plain text lines."""
    lines = ["hours above the limit and alerts, per candidate limit and device:"]
    for (metric, limit), per_device in counts.items():
        total_hours = sum(hours for hours, _ in per_device.values())
        total_alerts = sum(alerts for _, alerts in per_device.values())
        detail = ", ".join(
            f"{device[:5]} {hours} h {alerts} a" for device, (hours, alerts) in per_device.items()
        )
        lines.append(f"  {metric} {limit:g}: {total_hours} h, {total_alerts} alerts ({detail})")
    lines.append("lowest coverage of the judged hours per device:")
    lines.extend(f"  {device}: {value:.3f}" for device, value in lowest.items())
    return "\n".join(lines)


def main():
    with MongoClient(URI, serverSelectionTimeoutMS=5000, tz_aware=True) as client:
        database = client[DATABASE]
        print(render(candidate_counts(database), lowest_full_hour_coverage(database)))


if __name__ == "__main__":
    main()
