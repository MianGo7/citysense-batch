# Requirements

Source: the phase 2 requirements of task 1 of the DLBDSEDE02 portfolio, and
the commitments of the revised concept. A requirement is only ticked when it
is implemented and checked by a test, a script or a reproducible command.
The full paraphrase of the examination is in `docs/course-context.local.md`,
which is not committed.

## Requirements of the task

| Id | Requirement | Status | Where |
| --- | --- | --- | --- |
| R1 | The database runs in Docker from an open source image on Docker Hub | open | |
| R2 | A script sets up the database inside the container | open | |
| R3 | A Python script connects to the database and loads the sample data | open | |
| R4 | A Dockerfile automates the entire process | open | |
| R5 | The code is in an open GitHub repository | open | |
| R6 | Cloning the repository and running the containers loads the sample data automatically | open | |
| R7 | The repository contains a system description with the expected usages and the place of the system in a larger end user system | open | |

## Commitments of the concept

| Id | Commitment | Status | Where |
| --- | --- | --- | --- |
| C1 | The sample data is loaded in daily batches | open | |
| C2 | A failed batch can be rerun without creating duplicates | open | |
| C3 | The 13 duplicate rows of the source are absorbed | open | |
| C4 | Implausible readings are flagged, kept, and excluded from summaries and alerts | open | |
| C5 | A batch log records every run, and lost connections are retried | open | |
| C6 | Hourly and daily summaries carry reading counts and coverage | open | |
| C7 | The latest valid value per sensor carries its measurement time | open | |
| C8 | Alerts follow the alert model: deduplicated, with hysteresis, capped, with sensor silent and batch failed alerts | open | |
| C9 | A compound index on device and descending timestamp | open | |
| C10 | A TTL index on raw readings, summaries kept | open | |
| C11 | A three node replica set survives the loss of the primary during a load | open | |
| C12 | New metrics from future sensors are accepted without a schema change | open | |
