# Backlog

Work items in approximate priority order. One item is taken at a time and
committed before the next begins.

Three conditions apply to every item. The unit tests and ruff pass before a
commit is proposed, `requirements.md` is updated whenever a requirement
moves, and any decision not already recorded in `decision-log.md` receives an
entry there. The status of an item is open, in progress or done.

---

## B0, repository skeleton and toolchain. Done, 2026-11-12.

A Python 3.13 project managed with uv (originally planned with 3.12, changed
in ADR-0008): `pyproject.toml`, `uv.lock`, the
package `src/citysense/` with empty modules one per planned component,
`tests/unit/` and `tests/integration/` with the `integration` marker
registered, pytest and ruff as development dependencies, pymongo as the only
runtime dependency, and a `.dockerignore`.

Done when `uv run pytest tests/unit` runs green on a placeholder test and
`uv run ruff check .` reports nothing.

Documents to update: `decision-log.md` (Python version, uv, ruff, build
backend), `dev-journal.md`.

---

## B1, sample data in the repository. Done, 2026-11-12.

The CSV from the Kaggle data set is committed gzip compressed under
`data/raw/`, with a `data/README.md` naming the source, the CC0 licence and
the meaning and unit of every column as far as the source documents it. A
profiling script, `scripts/profile_data.py`, reproduces the facts the concept
quotes, and a unit test pins them.

Done when the test asserts 405,184 rows, three devices, 13 duplicate rows,
seven temperature readings of exactly 0.0, and 38 humidity readings below 10
percent, reading the compressed file.

Documents to update: `requirements.md`, `dev-journal.md`.

---

## B2, MongoDB replica set in Docker Compose. Done, 2026-11-13.

Three `mongod` services from the official image with an exact version tag, a
one shot `mongo-setup` service that initiates the replica set `rs0` and does
nothing when it is already initiated, health checks, named volumes, and a
small WiredTiger cache so the stack runs comfortably on an ordinary laptop.
Only one port is exposed to the host, for inspection with a database tool.

Ask before: the image tag, the cache size, any exposed port.

Done when `docker compose up` brings up one primary and two secondaries,
`docker compose down && docker compose up` brings them back without errors,
and stopping the primary leads to a new primary within seconds.

Documents to update: `decision-log.md`, `requirements.md` (R1),
`dev-journal.md`.

---

## B3, database setup script. Done, 2026-11-13.

An idempotent setup script run by `mongo-setup` after initiation. It creates
the collections from `docs/concept.md` with `$jsonSchema` validators that
require the core fields and types but allow additional fields for future
sensors, and the indexes listed in the concept. The retention period of the
TTL index is configurable and defaults to 365 days on the ingestion time.

Done when running the script twice raises no error, an integration test
finds every collection and index, and a document with an unknown extra metric
is accepted while a document without a device is rejected.

Documents to update: `requirements.md` (R2), `dev-journal.md`.

---

## B4, pure rules: identity, slicing, validation. Open.

`identity.py` builds the deterministic identifier from device and timestamp.
`batches.py` reads the compressed CSV as a stream and yields one batch per
calendar day in Coordinated Universal Time (UTC), in chronological order.
`validation.py` holds plausibility rules, a range rule per metric and a rule
for physically implausible jumps between consecutive readings of one device,
and returns flags rather than dropping readings.

Ask before: the concrete ranges and the jump limits. Propose them with the
number of readings each rule flags on the sample data.

Done when unit tests cover each rule, the seven readings of exactly 0.0
degrees and the 38 humidity readings below 10 percent are flagged, and every
further flagged reading is counted and explained in the ADR.

Documents to update: `decision-log.md`, `requirements.md`, `dev-journal.md`.

---

## B5, the loader and its container. Open.

`loader.py` processes the daily batches in order. Each batch is recorded in
`batch_runs` as running, written in chunks as bulk upserts that only insert
when the identifier is new, with write concern majority, and then marked
completed with its counts. Completed batches are skipped on a rerun. Lost
connections and primary elections are retried with exponential backoff, and a
batch that keeps failing is marked failed and ends the run with a non zero
exit code. The `Dockerfile` builds the loader image with uv and runs it as a
non root user, and the `loader` service in Compose waits for `mongo-setup`.

Done when a fresh clone followed by `docker compose up --build` loads 405,171
readings, which is 405,184 minus the 13 duplicates, a second run inserts
nothing, and the loader container exits with code 0.

Documents to update: `requirements.md` (R3, R4, R6), `decision-log.md`,
`dev-journal.md`.

---

## B6, derived data. Open.

After each completed batch, aggregation pipelines with `$merge` recompute the
hourly and daily summaries of the affected day and update `latest`. Summaries
cover valid readings only, and carry the reading count, the flagged count and
the coverage, which is the reading count relative to the count expected from
the device's usual interval.

Done when an integration test shows that the hourly reading counts add up to
the number of valid readings, rerunning a batch leaves the summaries
unchanged, and `latest` holds one document per device.

Documents to update: `requirements.md`, `dev-journal.md`.

---

## B7, alerts. Open.

The alert model and its rules follow ADR-0007. Threshold alerts are evaluated
on hourly summaries of valid readings, never on single raw values. Sensor
silent alerts are raised from coverage, and a failed batch raises an alert of
its own. One open alert exists per type, device and metric; while the
condition persists it is updated, not repeated; it is resolved only after the
condition has cleared for a full hour; alerts per device and day are capped.

Ask before: the thresholds. Propose them with the number of alerts each
choice produces over the sample week.

Done when unit tests cover deduplication, hysteresis and the cap, and an
integration test pins the number of alerts the sample week produces.

Documents to update: `decision-log.md`, `requirements.md`, `dev-journal.md`.

---

## B8, demonstrations and verification queries. Open.

`scripts/demo_crash_rerun.sh` stops the loader in the middle of a batch,
restarts it and shows that the final counts are unchanged.
`scripts/demo_failover.sh` stops the primary during a load and shows that the
loader continues and the counts are correct. `scripts/queries.js` holds one
example query per user type. The captured output goes to `docs/evidence/`.

Done when both demonstrations pass on a clean stack and their output is
captured.

Documents to update: `requirements.md`, `dev-journal.md`.

---

## B9, README as system description. Open.

The README describes the purpose, the three user types and what each reads,
how the system fits into the larger landscape with sensors, dashboard,
warning application and operations, with a diagram, a quick start from a
fresh clone, the verification queries, configuration, the project structure,
the data source with its licence, and the stated limits.

Done when a fresh clone on a machine with nothing but Docker works by
following the README alone.

Documents to update: `requirements.md` (R5, R7), `dev-journal.md`.

---

## B10, continuous integration. Open.

A GitHub Actions workflow runs ruff and the unit tests on every push, and a
second job starts the Compose stack and runs the integration tests.

Done when both jobs pass on the remote.

Documents to update: `decision-log.md`, `dev-journal.md`.
