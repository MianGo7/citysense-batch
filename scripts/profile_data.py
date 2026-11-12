"""Profile the compressed sample data and print the facts the concept quotes.

The concept quotes figures about the source file, for instance the number of
duplicate rows. A figure in a document has to be produced by a script or a test,
so this script reproduces them and the unit test pins them. The standard library
is enough: the file is streamed once, and pandas would enlarge the environment
for no capability that is needed here (ADR-0006).

Usage:
    uv run python scripts/profile_data.py [path/to/file.csv.gz]
"""

import csv
import gzip
import sys
from collections import Counter
from pathlib import Path

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
    duplicate_rows = 0
    duplicate_keys = 0
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
        "temperature_zeros": temperature_zeros,
        "humidity_below_limit": low_humidity,
        "minimum": minimum,
        "maximum": maximum,
        "flags": {column: dict(counts) for column, counts in flags.items()},
    }


def render(facts):
    """Format the facts as plain text lines for the terminal and for the README."""
    lines = [
        f"rows: {facts['rows']}",
        f"devices: {len(facts['devices'])}",
    ]
    for device, count in sorted(facts["devices"].items()):
        lines.append(f"  {device}: {count} rows")
    lines.extend(
        [
            f"duplicate rows: {facts['duplicate_rows']}",
            f"duplicates on device and timestamp: {facts['duplicate_device_timestamp']}",
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


def main(argv):
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_PATH
    print(render(profile(path)))


if __name__ == "__main__":
    main(sys.argv)
