# Decision log

Architecture and tooling decisions in the order they were taken. Each entry
states the context, the decision, the alternatives that were rejected and the
reason for rejecting them, and the consequences that follow. When a decision
changes, a new entry is added rather than an existing one edited, so that the
reasoning at each point in time remains visible.

---

## ADR-0001, MongoDB as the database. 2026-11-11, accepted.

The task asks for a database that accepts metrics whose structure is not yet
known, offers the most straightforward data model possible, and can move from
a single machine to a distributed setup for reliability and horizontal
scalability. MongoDB stores each reading as a document, so a future metric is
an additional field, and it provides replica sets for redundancy and sharding
for horizontal scaling within the same product.

PostgreSQL was rejected because its schema on write approach requires a
migration for every new sensor type, and horizontal scaling depends on
extensions. Apache Cassandra was rejected because its query driven table
design requires one table per access pattern, which contradicts the demand
for the simplest possible data model. Dedicated time series databases fit the
shape of the data but weaken the argument for a flexible, general data model.

The consequence is that structure is enforced by validators and by the loader
rather than by the database schema, and that indexes and derived collections
have to be designed explicitly for the access patterns of the users.

---

## ADR-0002, Docker Compose with the official image and a separate loader image. 2026-11-11, accepted.

The database runs from the official `mongo` image on Docker Hub, and the
loader runs from an image built by the repository's `Dockerfile`. Docker
Compose starts both, in order, so that `docker compose up --build` performs
the whole process from a fresh clone.

A single image containing both the database server and the loader was
rejected, because it runs two unrelated processes in one container and makes
it impossible to stop the database independently, which the failure
demonstrations require. A local installation of MongoDB was rejected because
it binds the system to the machine.

The consequence is that the only host requirement is Docker.

---

## ADR-0003, a three node replica set in the prototype, sharding as a documented path. 2026-11-11, accepted.

The concept argues reliability through replica sets. The prototype therefore
runs three `mongod` nodes as the replica set `rs0`, with a small cache each,
and writes with write concern majority. This makes the reliability claim
demonstrable: the primary can be stopped during a load and the loader has to
survive the election.

A single node was rejected because the reliability argument would remain a
claim. A sharded cluster was rejected for the prototype because it needs
a config server replica set, a query router and at least two shards, each of
them a replica set in production, while three sensors do not produce data volumes that sharding would serve;
the concept describes it as the migration path with device and timestamp as
the shard key.

The consequence is that the loader has to handle elections, and that the
compose file contains a setup step that initiates the replica set exactly
once.

---

## ADR-0004, daily batches with deterministic identifiers and a batch log. 2026-11-11, accepted.

The task demands that any failure can be recovered by retrying. The sample
data is therefore replayed as one batch per calendar day in UTC. Every
reading receives an identifier derived from device and timestamp, writes only
insert identifiers that do not yet exist, and a `batch_runs` collection
records the status of every batch. A rerun skips completed batches and
repeats an interrupted one without creating duplicates.

Importing the file with `mongoimport` was rejected because it offers neither
validation nor a record of what was loaded. Random identifiers combined with
deleting and reloading an interrupted batch were rejected because the delete
step can itself fail halfway.

The consequence is that the 13 duplicate rows in the source are absorbed by
the same mechanism, so 405,171 readings are expected after loading.

---

## ADR-0005, retention through a TTL index on the ingestion time. 2026-11-11, accepted.

Raw readings grow with every batch and every new sensor, while planners need
long term trends. Raw readings are therefore removed by a TTL index after a
configurable retention period, summaries are kept permanently, and the
original batch files remain the archive from which everything can be rebuilt.

The TTL index is placed on the ingestion time rather than the measurement
time, because the sample readings date from 2020 and would expire
immediately. Keeping all raw readings indefinitely was rejected because it
leads to the full disk the tutor's feedback names.

---

## ADR-0006, Python with pymongo and the standard library for reading the data. 2026-11-11, accepted.

The task requires a Python script. The loader uses pymongo and reads the
compressed CSV with the standard library modules `gzip` and `csv`, streaming
it row by row. pandas was rejected because the loader needs no analysis
functions and the dependency would enlarge the image considerably. The
asynchronous driver was rejected because the batches are processed strictly
in order, so concurrency would add complexity without benefit.

---

## ADR-0007, the alert model. 2026-11-11, proposed, thresholds open.

The alert design follows the course lecture on alerts. An alert is an
information object with a type (threshold exceeded, sensor silent, batch
failed), a severity, its source device, the metric, the observed value, the
threshold, the basis it was raised on with a reference to the summary, a
short message, a status (open, acknowledged, resolved), the first and last
time the condition was seen, and the number of occurrences.

Threshold alerts are evaluated on hourly summaries of valid readings, never
on single raw values, because single readings produce exactly the irregular,
spurious alerts that teach users to ignore the source. While a condition
persists, the existing open alert is updated rather than a new one raised. An
alert is resolved only after the condition has cleared for a full hour, so a
value hovering around the threshold does not open and close alerts
repeatedly. Alerts per device and day are capped. Because silence must not be
read as an all clear, missing data raises its own alert from the hourly
coverage, and a failed batch raises an alert as well.

Open: the concrete thresholds. They are proposed in B7 with the number of
alerts each choice produces over the sample week.

---

## ADR-0008, Python 3.13 with uv, uv_build and ruff. 2026-11-12, accepted.

The loader needs a reproducible Python toolchain that behaves identically on
the development machine, in the continuous integration run and in the loader
image. The project therefore uses Python 3.13, declared as
`requires-python = ">=3.13,<3.14"`, with uv for environments and dependency
resolution, uv_build as the build backend, and ruff for linting and
formatting. Dependency ranges in `pyproject.toml` are compatible ranges, for
instance `pymongo>=4.19,<5`, while the exact versions are pinned only in
`uv.lock`. The loader image has to install with `uv sync --locked`, so that a
lock file which no longer matches `pyproject.toml` fails the build instead of
being silently regenerated.

The backlog originally named Python 3.12. The version was changed to 3.13
because 3.13 is already installed on the development machine, so uv needs no
interpreter download there, and because pymongo, pytest and ruff all support
it. The upper bound `<3.14` keeps the interpreter from moving unnoticed to a
version that has not been tested.

For the dependency management, pip with hand maintained requirements files
was rejected because it records no resolved lock file and keeps development
and runtime dependencies apart only by convention. Poetry was rejected
because it adds a further tool to install in the image for no capability that
uv lacks. For the build backend, hatchling was rejected because it is one more
package in the lock file, while uv_build is maintained together with uv and
needs no additional configuration for the `src/` layout. For the code quality
tools, flake8 combined with black and isort was rejected because three tools
with three configurations are replaced by ruff, which lints, sorts imports and
formats from one configuration in `pyproject.toml`.

The rule set of ruff is the pycodestyle errors, pyflakes, import sorting,
bugbear, pyupgrade and the simplification checks, with a line length of 100 as
in `.editorconfig`. The consequence is that pymongo is the only runtime
dependency, pytest and ruff are development dependencies in a separate group,
and the image does not need to install the latter.
