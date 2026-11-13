# Requirements

Source: the phase 2 requirements of task 1 of the DLBDSEDE02 portfolio, and
the commitments of the revised concept. A requirement is only ticked when it
is implemented and checked by a test, a script or a reproducible command.
The full paraphrase of the examination is in `docs/course-context.local.md`,
which is not committed.

## Requirements of the task

| Id | Requirement | Status | Where |
| --- | --- | --- | --- |
| R1 | The database runs in Docker from an open source image on Docker Hub | done | `docker-compose.yml` (official `mongo:8.0.38`, ADR-0009), `db/init-replica-set.js`, `tests/integration/test_stack.py` |
| R2 | A script sets up the database inside the container | done | `db/setup-database.js`, service `mongo-setup` in `docker-compose.yml`, `tests/integration/test_database.py` (ADR-0010) |
| R3 | A Python script connects to the database and loads the sample data | done | `src/citysense/loader.py`, `store.py`, `config.py`, `__main__.py` (ADR-0012), `tests/unit/test_loader.py`, `tests/integration/test_loading.py` |
| R4 | A Dockerfile automates the entire process | done | `Dockerfile` (two stages, pinned bases, non-root user, `uv sync --locked`), service `loader` in `docker-compose.yml` (ADR-0012) |
| R5 | The code is in an open GitHub repository | open | |
| R6 | Cloning the repository and running the containers loads the sample data automatically | done | `docker compose down -v && docker compose up --build` loads 405,171 readings and the loader container exits with code 0, checked by `test_the_stack_stored_the_sample_without_its_duplicates` and `test_the_loader_container_of_the_stack_exited_with_code_0` in `tests/integration/test_loading.py`; the image was also built from a copy of the tracked files alone, which holds none of the tooling files |
| R7 | The repository contains a system description with the expected usages and the place of the system in a larger end user system | open | |

## Commitments of the concept

| Id | Commitment | Status | Where |
| --- | --- | --- | --- |
| C1 | The sample data is loaded in daily batches | done | nine daily batches, pinned per day in `tests/unit/test_sample_data.py` (`batches.py`, ADR-0011) and in `tests/integration/test_loading.py` (`loader.py`, ADR-0012) |
| C2 | A failed batch can be rerun without creating duplicates | done | `test_an_interrupted_batch_is_repeated_and_ends_with_the_flags_of_an_uninterrupted_load`, `test_a_second_load_without_the_batch_log_inserts_nothing` and `test_a_later_batch_is_not_loaded_after_a_failure`; the demonstration with a killed container is open (B8) |
| C3 | The 13 duplicate rows of the source are absorbed | done | the identifier (`identity.py`) gives the 13 duplicates the same identifier, pinned in `tests/unit/test_sample_data.py`; the loader reads 405,184 rows, inserts 405,171 and finds 13 already present, pinned per day in `tests/integration/test_loading.py` |
| C4 | Implausible readings are flagged, kept, and excluded from summaries and alerts | open | The rules flag 580 readings, including all 7 zeros and 38 low humidity readings (`validation.py`, ADR-0011, `tests/unit/test_sample_data.py`); the flags are stored with every reading, 580 of them (`test_the_flagged_readings_are_stored_and_counted`); the summaries exclude them, as their counts add up to the 404,591 valid readings and the statistics cover valid readings only (`tests/integration/test_derived.py`); the exclusion from alerts is open (B7) |
| C5 | A batch log records every run, and lost connections are retried | open | The batch log with status, attempts and counts is done and checked (`tests/integration/test_loading.py`); the retry schedule is covered by unit tests with injected errors (`tests/unit/test_loader.py`), and the check against a real loss of the primary is open (B8) |
| C6 | Hourly and daily summaries carry reading counts and coverage | done | `src/citysense/aggregation.py` (ADR-0013); `tests/integration/test_loading.py` (counts add up to the 404,591 valid readings, per hour and per day; coverage range per device; partial last hour), `tests/integration/test_derived.py`, `tests/unit/test_aggregation.py`, `tests/unit/test_sample_data.py` (the rejected definition of coverage) |
| C7 | The latest valid value per sensor carries its measurement time | done | collection `latest`, one document per device with `ts` and `reading_id` (ADR-0013); `test_latest_is_the_newest_valid_reading_of_each_device`, and the guard against moving backwards in `test_rerunning_an_early_day_changes_neither_the_summaries_nor_latest` and `test_loading_an_earlier_day_again_never_moves_the_latest_value_backwards` |
| C8 | Alerts follow the alert model: deduplicated, with hysteresis, capped, with sensor silent and batch failed alerts | open | |
| C9 | A compound index on device and descending timestamp | done | index `device_ts` in `db/setup-database.js`, checked by `test_readings_index_serves_ranges_per_device` |
| C10 | A TTL index on raw readings, summaries kept | done | index `ingested_at_ttl`; `test_the_ttl_monitor_removes_an_expired_reading_and_keeps_a_current_one` and `test_only_raw_readings_expire` |
| C11 | A three node replica set survives the loss of the primary during a load | open | The replica set elects a new primary after the loss of mongo1 (B2, journal 2026-11-12); survival of a load is open (B5, B8) |
| C12 | New metrics from future sensors are accepted without a schema change | done | validators allow additional fields, `test_a_reading_with_an_unknown_extra_metric_is_accepted` |
| C13 | The figures about the sample data quoted in the concept are reproduced by a script and pinned by a test | done | `scripts/profile_data.py`, `tests/unit/test_sample_data.py`, `data/README.md` |
