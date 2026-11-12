# Technical concept

This file summarises the technical concept the implementation follows. It is
a working document for the repository and not the submitted concept text.

## Purpose

The data system is the storage layer of a municipal environmental monitoring
project. It accepts sensor readings in batches, keeps them reliably, and
prepares exactly the data that two planned front ends and the operators of
the system need. The sample data, roughly 405,000 readings from three devices
over one week in July 2020, is treated as the first part of a continuous feed
rather than as a closed data set, and it is replayed as one batch per day.

## Users and what they read

| User | Question | Collection |
| --- | --- | --- |
| Urban planner | How do conditions develop per sensor over the day, and how do locations differ? Over months: what are the long term trends? | `summaries_hourly`, `summaries_daily` |
| Citizen, through the warning application | Is the air in the district acceptable right now, and how current is that statement? | `latest`, `alerts` |
| Data engineer | Did every batch arrive and load, how many readings were rejected, is the storage growing as expected, does a new sensor type fit? | `batch_runs`, `alerts`, `readings` |

A summary always carries the number of readings it is based on, because an
hourly mean computed from a handful of readings must not look as trustworthy
as one computed from several hundred. The latest value always carries its
measurement time, because a sensor that stopped reporting must appear as
missing data and never as an all clear.

## What can go wrong, and how the system responds

| Situation | Consequence for the users if ignored | Response |
| --- | --- | --- |
| Duplicate rows in a batch | Inflated counts and distorted means | Deterministic identifier from device and timestamp, so a duplicate overwrites nothing and adds nothing |
| Implausible readings | False warnings for citizens, distorted trends for planners | Plausibility rules flag the reading; flagged readings stay stored but are excluded from summaries and alerts |
| Loader crash or lost database connection mid batch | Gaps that look like clean air | Batch log with a status per batch; a rerun skips completed batches and repeats the interrupted one idempotently; retries with increasing delay |
| Loss of a database node | Downtime, lost writes | Three node replica set, writes acknowledged by the majority, loader survives an election |
| A sensor goes silent | The warning application shows outdated values as current | Hourly coverage per sensor and an alert of the type "sensor silent" |
| Growing raw data, full disk | The whole system stops | TTL index removes raw readings after a retention period; summaries are kept; the original batch files remain the archive |
| Alerts repeating on every batch | Users stop reading alerts | One open alert per condition, updated rather than repeated, resolved only when the condition has cleared, capped per day |

## Collections

- `readings`: one document per reading, identifier from device and timestamp,
  the measured values, a quality section with any flags, the batch it came
  from and the ingestion time. New metrics from future sensors are simply
  further fields.
- `batch_runs`: one document per batch with status, attempts, counts and
  timing.
- `summaries_hourly`, `summaries_daily`: per device and period the minimum,
  maximum and mean of each metric over valid readings, the number of
  readings, the number of flagged readings and the coverage.
- `latest`: one document per device with the latest valid reading and its
  time.
- `alerts`: one document per alert, following the alert model in the decision
  log.

## Indexes

- `readings`: device ascending with timestamp descending, serving time range
  queries per sensor and the latest value per sensor alike.
- `readings`: TTL on the ingestion time. The measurement time cannot carry the
  TTL, because readings from 2020 would expire immediately.
- `alerts`: status, type and device, serving the open alerts per sensor.

## Limits that are stated openly

Alerts can only be as current as the batch interval, so the warning
application shows the time of the last batch with every statement. Sharding
is not part of the prototype; the replica set can be converted into a shard
of a sharded cluster with device and timestamp as the shard key when the
number of sensors requires it.
