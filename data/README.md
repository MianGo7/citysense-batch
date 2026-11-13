# Sample data

The sample data is the telemetry of three environmental sensor devices over one
week in July 2020. It is the first part of the continuous feed that the system
is designed for, and the loader replays it as one batch per calendar day.

## Source and licence

Stafford, G. (2020). Environmental sensor telemetry data [Data set]. Kaggle.
https://www.kaggle.com/datasets/garystafford/environmental-sensor-data-132k

The data set is published under the Creative Commons CC0 1.0 Universal public
domain dedication, https://creativecommons.org/publicdomain/zero/1.0/, so it
may be copied and redistributed with the repository. It is cited regardless.

## Files

`raw/iot_telemetry_data.csv.gz` is the file `iot_telemetry_data.csv` of the
Kaggle download, compressed with `gzip -9 -n`. The option `-n` stores neither a
file name nor a timestamp, so the same input always produces the same archive.
The uncompressed CSV is not committed, because the loader and the tests read
the compressed file directly.

| File | Size in bytes | SHA-256 |
| --- | --- | --- |
| `iot_telemetry_data.csv` (original, not committed) | 61,926,558 | `811ffbf210c8c454f328b95f7e8affc0ce031fdee927a8dfb76035b7e3334287` |
| `raw/iot_telemetry_data.csv.gz` | 6,466,866 | `208fea26d9f5552a7b9b91eaf6e19dbd40372f516e421b106f6a45168956f414` |

Both hashes can be checked from a clone:

    shasum -a 256 data/raw/iot_telemetry_data.csv.gz
    gunzip -c data/raw/iot_telemetry_data.csv.gz | shasum -a 256

The unit test `tests/unit/test_sample_data.py` verifies the hash of the
compressed file, so a changed file is noticed before any figure derived from it
is trusted.

## Columns

The descriptions and units are those stated on the Kaggle page of the data set
at its last update on 2020-07-20.

| Column | Description | Unit as stated by the source |
| --- | --- | --- |
| `ts` | Event timestamp | Unix epoch |
| `device` | Unique device name, a MAC address | string |
| `co` | Carbon monoxide | "ppm (%)" |
| `humidity` | Humidity | percent |
| `light` | Light detected | boolean |
| `lpg` | Liquefied petroleum gas | "ppm (%)" |
| `motion` | Motion detected | boolean |
| `smoke` | Smoke | "ppm (%)" |
| `temp` | Temperature | Fahrenheit |

The stated unit of the temperature contradicts the observed values, which range
from 0.0 to 30.6 and are consistent with degrees Celsius, not Fahrenheit.
Furthermore, the source does not explain the unit "ppm (%)" of the three gas
columns, which combines a parts per million scale with a percentage. The system
stores every value as delivered, without conversion or rescaling, so the
interpretation of these units remains a documented open question of the source
and is not decided silently in the code.

## Measured facts

`scripts/profile_data.py` streams the compressed file and prints the facts
below, and `tests/unit/test_sample_data.py` pins them. Both read the compressed
file. The script is run with `uv run python scripts/profile_data.py`.

| Fact | Value |
| --- | --- |
| Rows | 405,184 |
| Devices | 3 |
| Rows of device `00:0f:00:70:91:0a` | 111,815 |
| Rows of device `1c:bf:ce:15:ec:4d` | 105,918 |
| Rows of device `b8:27:eb:bf:9d:51` | 187,451 |
| Exact duplicate rows | 13 |
| Duplicates on device and timestamp | 13 |
| Temperature readings of exactly 0.0 | 7 |
| Humidity readings below 10 percent | 38 |
| `light` values | false 292,657, true 112,527 |
| `motion` values | false 404,702, true 482 |

The observed range of every numeric column, as printed by the script with seven
significant digits:

| Column | Minimum | Maximum |
| --- | --- | --- |
| `ts` | 1594512094.4 | 1595203417.3 |
| `co` | 0.001170509 | 0.01442011 |
| `humidity` | 1.1 | 99.9 |
| `lpg` | 0.002693479 | 0.01656738 |
| `smoke` | 0.006692096 | 0.04659012 |
| `temp` | 0.0 | 30.6 |

Temperature and humidity are held by the source as single precision values, so
a reading of 30.6 appears in the file as 30.600000381469727. Seven significant
digits are therefore the precision the data can claim, and the tests compare
these two ranges with a tolerance far below one decimal.

The 13 exact duplicates are also the 13 duplicates on device and timestamp.
This matters for the identifier of a reading, which is built from device and
timestamp: no two different readings collide on one identifier, so absorbing
the duplicates loses nothing. The 7 temperature readings of 0.0 and the 38
low humidity readings are flagged by the plausibility rules of ADR-0011, which
the unit tests pin.
