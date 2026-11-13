"""Profile the compressed sample data and print the facts the concept quotes.

The concept quotes figures about the source file, for instance the number of
duplicate rows. A figure in a document has to be produced by a script or a test,
so this script reproduces them and the unit test pins them. The standard library
is enough: the file is streamed once, and pandas would enlarge the environment
for no capability that is needed here (ADR-0006). The figures about the flagged
readings are produced by the rules of the package itself, so that the documents
quote what the loader will do.

Usage:
    uv run python scripts/profile_data.py [path/to/file.csv.gz]
"""

import csv
import gzip
import statistics
import sys
from collections import Counter
from pathlib import Path

from citysense.batches import iter_batches
from citysense.identity import epoch_ms
from citysense.validation import DeviceState, check_reading

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "raw" / "iot_telemetry_data.csv.gz"

# The columns that are parsed as numbers. The flags light and motion are
# delivered as the strings "true" and "false" and are counted, not ranged.
NUMERIC_COLUMNS = ("ts", "co", "humidity", "lpg", "smoke", "temp")
FLAG_COLUMNS = ("light", "motion")

HUMIDITY_LIMIT = 10.0

# Temperature and humidity are stored by the source as single precision values,
# for instance 30.600000381469727 for 30.6, so seven significant digits are what
# the data can claim. The timestamp is shown in seconds with one decimal.
SIGNIFICANT_DIGITS = 7


def profile(path=DEFAULT_PATH):
    """Stream the file once and return the measured facts as a dictionary."""
    rows = 0
    devices = Counter()
    seen_rows = set()
    seen_keys = set()
    seen_milliseconds = set()
    duplicate_rows = 0
    duplicate_keys = 0
    duplicate_milliseconds = 0
    last_ts = {}
    intervals = {}
    temperature_zeros = 0
    low_humidity = 0
    minimum = {}
    maximum = {}
    flags = {column: Counter() for column in FLAG_COLUMNS}

    with gzip.open(path, mode="rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows += 1
            devices[row["device"]] += 1

            # Exact duplicates are compared on the raw strings so that no
            # rounding of floating point values can hide or invent one.
            whole_row = tuple(row.values())
            if whole_row in seen_rows:
                duplicate_rows += 1
            seen_rows.add(whole_row)

            key = (row["device"], row["ts"])
            if key in seen_keys:
                duplicate_keys += 1
            seen_keys.add(key)

            # The identifier uses the timestamp truncated to milliseconds, so the
            # duplicates must also be counted after that truncation.
            millisecond_key = (row["device"], epoch_ms(row["ts"]))
            if millisecond_key in seen_milliseconds:
                duplicate_milliseconds += 1
            seen_milliseconds.add(millisecond_key)

            # The interval to the previous row of the same device, without the
            # zero of a duplicate row, which is no interval between readings.
            timestamp = float(row["ts"])
            previous = last_ts.get(row["device"])
            if previous is not None and timestamp > previous:
                intervals.setdefault(row["device"], []).append(timestamp - previous)
            last_ts[row["device"]] = timestamp

            for column in NUMERIC_COLUMNS:
                value = float(row[column])
                if column not in minimum or value < minimum[column]:
                    minimum[column] = value
                if column not in maximum or value > maximum[column]:
                    maximum[column] = value
            for column in FLAG_COLUMNS:
                flags[column][row[column]] += 1

            if float(row["temp"]) == 0.0:
                temperature_zeros += 1
            if float(row["humidity"]) < HUMIDITY_LIMIT:
                low_humidity += 1

    return {
        "rows": rows,
        "devices": dict(devices),
        "duplicate_rows": duplicate_rows,
        "duplicate_device_timestamp": duplicate_keys,
        "duplicate_device_millisecond": duplicate_milliseconds,
        "median_interval": {
            device: round(statistics.median(values), 1) for device, values in intervals.items()
        },
        "largest_interval": round(max(max(values) for values in intervals.values()), 1),
        "temperature_zeros": temperature_zeros,
        "humidity_below_limit": low_humidity,
        "minimum": minimum,
        "maximum": maximum,
        "flags": {column: dict(counts) for column, counts in flags.items()},
    }


def flag_statistics(path=DEFAULT_PATH):
    """Run the plausibility rules over the file and count what they flag.

    The counts are over the stored readings, so a row that repeats the identifier
    of an earlier one is not counted twice. The size of a flagged jump is its
    distance from the reference, rounded to one decimal, which is the class that
    it is counted in.
    """
    states = {}
    seen = set()
    stored = 0
    flagged = Counter()
    zeros = zeros_flagged = low = low_flagged = rebaselines = further = 0
    rebaseline_events = []
    sizes = {"temp": Counter(), "humidity": Counter()}
    jumps_per_device = {}

    for batch in iter_batches(path):
        for reading in batch.readings:
            state = states.get(reading.device, DeviceState())
            result = check_reading(reading, state)
            states[reading.device] = result.state
            if reading.id in seen:
                continue
            seen.add(reading.id)
            stored += 1

            devices = jumps_per_device.setdefault(reading.device, Counter())
            for metric, counter in sizes.items():
                if f"{metric}_jump" in result.flags:
                    distance = abs(reading.metrics[metric] - state.reference.metrics[metric])
                    counter[round(distance, 1)] += 1
                    devices[metric] += 1
            if result.flags:
                flagged[result.flags] += 1
            if result.rebaselined:
                rebaselines += 1
                rebaseline_events.append((reading.device, reading.ts.isoformat()))

            # Flagged readings that are neither a zero temperature nor a humidity
            # below the limit, the ones that the concept does not name.
            if (
                result.flags
                and reading.metrics["temp"] != 0.0
                and reading.metrics["humidity"] >= HUMIDITY_LIMIT
            ):
                further += 1
            if reading.metrics["temp"] == 0.0:
                zeros += 1
                zeros_flagged += bool(result.flags)
            if reading.metrics["humidity"] < HUMIDITY_LIMIT:
                low += 1
                low_flagged += bool(result.flags)

    temp_only = flagged[("temp_jump",)]
    humidity_only = flagged[("humidity_jump",)]
    both = flagged[("humidity_jump", "temp_jump")]
    total = sum(flagged.values())
    return {
        "readings": stored,
        "flagged": total,
        "flagged_temp_only": temp_only,
        "flagged_humidity_only": humidity_only,
        "flagged_both": both,
        "flagged_other": total - temp_only - humidity_only - both,
        "flagged_further": further,
        "zeros": zeros,
        "zeros_flagged": zeros_flagged,
        "below_limit": low,
        "below_limit_flagged": low_flagged,
        "rebaselines": rebaselines,
        "rebaseline_events": rebaseline_events,
        "jump_sizes": {metric: dict(counter) for metric, counter in sizes.items()},
        "jumps_per_device": {device: dict(counts) for device, counts in jumps_per_device.items()},
    }


# The sizes of the flagged jumps are not spread evenly. They lie close to
# multiples of 3.2, for instance 3.2, 6.4, 12.8, 19.2, 25.6 and 51.2. This is an
# observation about the data, and no cause is claimed for it.
SIZE_STEP = 3.2
SIZE_TOLERANCE = 0.4


def jumps_near_multiples(jump_sizes, step=SIZE_STEP, tolerance=SIZE_TOLERANCE):
    """Count per metric the flagged jumps within the tolerance of a multiple of the step."""
    result = {}
    for metric, classes in jump_sizes.items():
        near = total = 0
        for size, count in classes.items():
            total += count
            nearest = round(size / step) * step
            if nearest > 0 and abs(size - nearest) <= tolerance:
                near += count
        result[metric] = {"near": near, "total": total}
    return result


def render(facts):
    """Format the facts as plain text lines for the terminal and for the README."""
    lines = [
        f"rows: {facts['rows']}",
        f"devices: {len(facts['devices'])}",
    ]
    for device, count in sorted(facts["devices"].items()):
        median = facts["median_interval"][device]
        lines.append(f"  {device}: {count} rows, median interval {median} s")
    lines.append(f"largest interval between readings of one device: {facts['largest_interval']} s")
    lines.extend(
        [
            f"duplicate rows: {facts['duplicate_rows']}",
            f"duplicates on device and timestamp: {facts['duplicate_device_timestamp']}",
            "duplicates on device and truncated millisecond: "
            f"{facts['duplicate_device_millisecond']}",
            f"temperature readings of exactly 0.0: {facts['temperature_zeros']}",
            f"humidity readings below {HUMIDITY_LIMIT:g}: {facts['humidity_below_limit']}",
            "observed range per numeric column:",
        ]
    )
    for column in NUMERIC_COLUMNS:
        low, high = facts["minimum"][column], facts["maximum"][column]
        spec = ".1f" if column == "ts" else f".{SIGNIFICANT_DIGITS}g"
        lines.append(f"  {column}: {low:{spec}} to {high:{spec}}")
    for column in FLAG_COLUMNS:
        counts = ", ".join(
            f"{value}={count}" for value, count in sorted(facts["flags"][column].items())
        )
        lines.append(f"{column} values: {counts}")
    return "\n".join(lines)


def render_flags(stats):
    """Format the statistics of the plausibility rules as plain text lines."""
    lines = [
        f"stored readings: {stats['readings']}",
        f"flagged readings: {stats['flagged']}",
        f"  temp_jump only: {stats['flagged_temp_only']}",
        f"  humidity_jump only: {stats['flagged_humidity_only']}",
        f"  both: {stats['flagged_both']}",
        f"  other combinations: {stats['flagged_other']}",
        f"flagged readings beyond the zeros and the low humidity: {stats['flagged_further']}",
        "temperature readings of exactly 0.0 flagged: "
        f"{stats['zeros_flagged']} of {stats['zeros']}",
        f"humidity readings below {HUMIDITY_LIMIT:g} flagged: "
        f"{stats['below_limit_flagged']} of {stats['below_limit']}",
        f"readings accepted as new reference after consecutive jump flags: {stats['rebaselines']}",
        *(f"  {device} at {moment}" for device, moment in stats["rebaseline_events"]),
        "flagged jumps per device:",
    ]
    for device, counts in sorted(stats["jumps_per_device"].items()):
        lines.append(f"  {device}: {dict(sorted(counts.items()))}")
    for metric, classes in sorted(stats["jump_sizes"].items()):
        listed = ", ".join(f"{size}: {count}" for size, count in sorted(classes.items()))
        lines.append(f"size classes of {metric} jumps, rounded to one decimal: {listed}")
    for metric, counts in sorted(jumps_near_multiples(stats["jump_sizes"]).items()):
        lines.append(
            f"{metric} jumps within {SIZE_TOLERANCE:g} of a multiple of {SIZE_STEP:g}: "
            f"{counts['near']} of {counts['total']}"
        )
    return "\n".join(lines)


def main(argv):
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_PATH
    print(render(profile(path)))
    print()
    print(render_flags(flag_statistics(path)))


if __name__ == "__main__":
    main(sys.argv)
