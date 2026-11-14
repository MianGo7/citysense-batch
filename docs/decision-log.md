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

Note added on 2026-11-13: the source of an alert is stored in the required field
`source`, which replaces the phrase "its source device" above, because an alert
about a failed batch has no device. The field `device` stays optional. See
ADR-0010. The text of this entry is otherwise unchanged.

Note added on 2026-11-14 (B7): the thresholds that this entry leaves open are fixed
in ADR-0014, together with the content of an alert, the cap, the resolution and the
way the alerts are evaluated. The text of this entry is otherwise unchanged.

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

---

## ADR-0009, the MongoDB image, cache, port, authentication and member priorities. 2026-11-12, accepted.

The replica set of ADR-0003 runs from the official `mongo` image with the exact
tag `8.0.38`, so a fresh clone starts the version that was tested. The tag
`latest` was rejected, and so was a tag that names only the major or minor
version, because both move over time and the same clone would then behave
differently from one month to the next. The 8.0 line was chosen over 7.0,
which is older, and over 9.0, which is a newer major version and less
established. Docker Compose is given the project name `citysense`, so that
container and volume names do not depend on the name of the directory that was
cloned.

Every node starts with `--wiredTigerCacheSizeGB 0.25`, which is the smallest
value MongoDB accepts. Without the option each node sizes its cache from the
memory of the machine, and three nodes on one machine would each claim a large
share of it. The small cache is a deliberate cost: it limits how much of the
working set stays in memory, which the load in B5 will show. If the measured
load time proves unacceptable, the value is revisited in a new entry instead of
being adjusted silently.

Only `mongo1` publishes a port, `127.0.0.1:27017`, for inspection with a
database tool. The prototype runs without authentication, which is a deliberate
limit and not an oversight. Key file authentication, the usual way to secure a
replica set, was rejected because the key would either have to be committed to
the repository or be generated in a manual setup step, and the fresh clone is
required to start with one command and without credentials. The loopback binding
compensates for the missing authentication, since the port is not reachable from
the network. Publishing all three nodes was rejected because it widens the
exposure for no gain, and publishing no port was rejected because inspection
would then need a shell inside a container. A production setup would enable
authentication and transport encryption. The members advertise their internal
host names, which the host cannot resolve, so a tool on the host connects with
`directConnection=true`.

The members have priorities, 2 for `mongo1` and 1 for `mongo2` and `mongo3`.
With equal priorities the primary after a clean start is arbitrary, so the
published port could reach a secondary and the documentation could not describe
the state of the stack as fixed. With the priorities, `mongo1` is the primary
after a clean start and takes over again when it returns after a failure, which
costs a second election. The loader of B5 therefore has to survive two
elections in a failover test, the loss of the primary and the return of
`mongo1`.

The replica set is initiated by the one shot service `mongo-setup`, which uses
the same image and therefore adds no second image. Its script treats the error
`NotYetInitialized` as the signal to initiate and does nothing otherwise, so
that it is safe to run on every start of the stack. It waits for a primary in
both cases and exits with code 0, so that a service depending on it with
`service_completed_successfully` starts against a writable replica set. A flag
file was rejected as the signal, because the state of the replica set lives in
the data volumes and a file outside of them could disagree with it. Mounting an
initialisation script into the data directory of the nodes was rejected,
because such scripts run only on an empty data directory and before the other
members exist.

The image declares anonymous volumes for `/data/db` and `/data/configdb`. Each
recreation of a container would leave them behind, because only `down -v`
removes them. The directory `/data/configdb` only holds the metadata of sharded
config servers, so it is mounted as `tmpfs` on the nodes, and both directories
are mounted as `tmpfs` on `mongo-setup`, which stores nothing. The data of the
nodes is held by one named volume each, mounted at `/data/db`.

One compatibility note belongs in the limits of the README (B9): MongoDB 5.0 and
later require a processor with the AVX instruction set on x86 machines. The
official image is published for several architectures, so it runs on x86 and on
Apple silicon.

Note added on 2026-11-13: the statement above that `mongo1` is the primary after
a clean start held only some seconds after the setup service had finished,
because another member sometimes wins the first election and `mongo1` takes over
through its priority later. The service had exited as soon as any member was the
primary, so a test that connects to the published port of `mongo1` failed now
and then. The script `db/init-replica-set.js` now waits after the first
initiation until `mongo1` itself is the primary, and a repeated start still
accepts any primary, because `mongo1` may be taking over after a failure. The
text of this entry is otherwise unchanged.

---

## ADR-0010, the data model, the validators, the indexes and the retention. 2026-11-13, accepted.

The six collections of the concept are created by the script
`db/setup-database.js`, which the service `mongo-setup` runs after the replica
set has been initiated. Every collection has a `$jsonSchema` validator with the
validation level `strict` and the validation action `error`, so that an invalid
document is rejected instead of being stored with a warning. The validators
require the core fields and the types of the known metrics, and they allow every
other field. A reading of a future sensor with a metric that is not known yet is
therefore accepted without a schema change, which is the first point of the
database requirements. A schema that forbids additional fields was rejected for
exactly that reason, and so was running without validators, because the loader
would then be the only place that enforces the structure and a second writer
could store anything. The action `warn` was rejected because a warning that
nobody reads does not protect the summaries and alerts that are derived from
the readings.

A reading is one flat document. The metrics are top level fields and not
members of a nested `metrics` subdocument, because a nested level would have to
be written in every query and the concept describes new metrics as further
fields. The required fields are `_id`, `device`, `ts`, `ingested_at`, `batch`
and `quality`. The metrics of the current sensors, `co`, `humidity`, `lpg`,
`smoke` and `temp` as numbers and `light` and `motion` as booleans, are optional
and typed when present, so that a future sensor that lacks one of them is still
valid. The name `ts` is kept from the source, because the one to one mapping to
the source column matters more than a more descriptive name. It is stored as a
BSON date, which keeps milliseconds, and not as the number of the source, so
that the database can truncate it to hours and days for the summaries and so
that dates are compared as dates.

The identifier of a reading is the string `<device>|<epoch milliseconds>`, for
instance `b8:27:eb:bf:9d:51|1594512094385`. The milliseconds are the source
timestamp multiplied by 1000 and truncated, not rounded, because a BSON date
truncates as well, so that the identifier and `ts` always describe the same
instant. The validator checks the format of the identifier with a pattern, not
its agreement with `device` and `ts`. B4 builds the identifier in `identity.py`
and pins in the profile script that no two readings of one device share a
millisecond, which is the condition under which the truncation loses nothing.
Random identifiers with a unique index were rejected in ADR-0004.

The field `quality` is required and holds `flags`, an array of strings that may
be empty. An empty array means that the reading was checked and found
plausible, whereas a missing field would mean that it was never checked, an
ambiguity that a strict validator should not allow. Summaries and alerts select
the valid readings with `{"quality.flags": {$size: 0}}`. The field `batch` is the
UTC day of the batch as `YYYY-MM-DD`, which is also the identifier of the entry
in `batch_runs`.

The keys of the derived collections are fixed so that the pipelines of B6 can
merge on `_id` without a further index. The identifier of `summaries_hourly` and
`summaries_daily` is the object `{device, period_start}`, and both values are
repeated as top level fields, because a query by device and period reads them
directly, whereas a range over a member of `_id` cannot use the index of `_id`.
The identifier of `latest` is the device, so that there is one document per
device, and the identifier of `batch_runs` is the UTC day. Their required fields
are the minimum that the concept commits to: counts and coverage for the
summaries, the measurement time for `latest`, and status, attempts and start
time for `batch_runs`. The shape of the statistics per metric is left to B6.
Because the setup script replaces the validators on every run through `collMod`,
tightening them later is a change to this file and reaches existing volumes.
Dropping and recreating a collection was rejected because it deletes the data.

An alert requires `type`, one of `threshold`, `sensor_silent` and
`batch_failed`, `status`, one of `open`, `acknowledged` and `resolved`, and
`source`, a string that is the device for a sensor alert and `loader` for a
failed batch. The field `source` replaces the phrase "its source device" in
ADR-0007, because an alert about a failed batch has no device. The field
`device` stays optional, and every other field of the alert model stays optional
until the alert rules of B7 fix them.

Three indexes are created, each with a fixed name so that it can be found and
changed by name. The index `device_ts` on `readings`, device ascending and
timestamp descending, serves the time range per device and the latest reading
per device. The index `status_type_device` on `alerts` serves the open alerts
per sensor. The index `ingested_at_ttl` on `readings` removes raw readings after
the retention period. The retention is the environment variable `RETENTION_DAYS`
with the default 365, which Compose passes to `mongo-setup`, and it applies to
the ingestion time, because the sample readings date from 2020 and would expire
immediately on the measurement time. A value that is not a positive whole number
fails the setup with a non zero exit code, and a changed value is applied to the
existing index through `collMod`, because `createIndex` with different options
on an existing index fails. An existing index with the same name and a different
key is reported as an error and not dropped. No other collection has a TTL
index, so the summaries are kept permanently.

The setup is a script for `mongosh` and not a Python module, because the task
asks for a script that sets up the database inside the container, and the image
of `mongo-setup` already contains `mongosh`, so the setup needs no Python and no
further image. The first call of the service is a direct connection to `mongo1`,
because the replica set does not exist yet. The second call connects through the
replica set, so that it reaches the primary, which is not always `mongo1` after
a restart.

Two points are left open on purpose. The summary collections have no index on
`device` and `period_start`, because the concept lists none, and B6 decides
whether the queries of the planners need one once the volumes are known.
Furthermore, the validator does not compare `_id` with `device` and `ts`, so a
loader that builds an inconsistent identifier would not be stopped by the
database. That agreement is meant to be a property of `identity.py`, which B4 builds and
tests.

Note added on 2026-11-14 (B7): the fields of an alert that this entry leaves
optional are fixed in ADR-0014. The validator of `alerts` now requires the
identifier, the type, the severity, the status, the source, the message, the
recommended action, `first_seen`, `last_seen` and `occurrences`, and allows the
device, the metric, the value, the threshold, the basis, `resolved_at` and
`acknowledged_at`. The index `source_first_seen` on `source` and `first_seen` is
added to the three indexes above. The text of this entry is otherwise unchanged.

---

## ADR-0011, identifiers, batch slicing and plausibility rules. 2026-11-13, accepted.

The rules of the loader live in three modules that do not import pymongo,
`identity.py`, `batches.py` and `validation.py`, so that they are tested without
a database. A reading that is implausible is flagged and kept, never dropped,
because deleting it would hide the fault from the data engineer and the
concept demands flags. The flags are strings in `quality.flags`, named
`<metric>_range` and `<metric>_jump`, and a reading is valid exactly when the
list is empty, which is the selection that summaries and alerts use. The figures
in this entry are produced by `scripts/profile_data.py` and pinned in
`tests/unit/test_sample_data.py`, so the entry states what the loader does with
the sample file.

The identifier of a reading is `<device>|<epoch milliseconds>`, with the
milliseconds computed from the raw text of the timestamp by exact decimal
arithmetic and truncated, as fixed in ADR-0010. A conversion through a float was
rejected, because the unit tests contain timestamps with more digits than the
sample on which a float lands on the neighbouring millisecond, so that the
identifier and `ts` would disagree. On the sample the truncation leaves the 13
duplicate rows as 13 duplicates on device and millisecond, which is the
condition under which absorbing them loses no reading. A device name that
contains the separator is rejected, because the identifier would be ambiguous.

The source is sliced into one batch per UTC day, in chronological order, and the
day is taken from the truncated millisecond, like the identifier, so that a
reading just before midnight is never moved to the next day. The sample yields
nine batches, from 12 to 20 July, with 50,462, 50,121, 50,719, 51,058, 51,040,
50,386, 50,074, 51,196 and 128 rows. The file is read once, and only the batch
in progress is held in memory. Time must not go backwards for one device, and
equal timestamps are allowed, because a strict check would fail the batch on the
duplicate rows before they reach the identifier logic. A reading from an earlier
day than the batch in progress is an error as well, since that batch has been
handed on already. A row that cannot be parsed fails the batch with its line
number. Flagging such a row was rejected, because a value that is not a number
cannot be stored in a typed field, and dropping it was rejected, because that is
the silent loss that the batch log exists to prevent. An unknown column in the
header also fails the batch, since a new column is a new metric that has to be
added on purpose.

The range rules are deliberately wide. Temperature must lie between -40 and 60
degrees, taken as Celsius, because the observed values are consistent with
Celsius although the source states Fahrenheit. Humidity must lie between 0 and
100 percent. The gases `co`, `lpg` and `smoke` must be finite and not negative,
without an upper bound, because the source does not explain the unit "ppm (%)"
and a ceiling could not be justified. These rules flag no reading of the sample,
as the count of flags other than jumps, which is 0, shows. A fixed lower bound
of 10 percent for the humidity was rejected, because it has no physical basis
here and because the jump rule already flags all 38 humidity readings below 10
percent.

The jump rule compares a reading of temperature or humidity with the reference
of its device, which is the last unflagged reading, and flags it when the
difference exceeds 3 degrees or 10 percentage points. The comparison is made
only if the reference is at most 300 seconds old, and the largest interval
between two readings of one device in the sample is 40.2 seconds. Comparing with
the previous raw reading was rejected, because it flags a single spike and the
normal reading after it, whereas the reference flags only the spike. Judging a
reading by both of its neighbours was rejected, because the last reading of a
batch has no successor, so a batch would not be final until the next one
arrives. A limit per second was rejected as the simpler rule suffices together
with the maximum age of the reference. The gases have no jump rule, because
without an explained unit no limit can be justified, and a limit read off the
sample would be fitted to it.

On the 405,171 stored readings the rules flag 580, which is about 0.14 percent:
261 by the temperature jump only, 230 by the humidity jump only and 89 by both.
All 7 temperature readings of exactly 0.0 and all 38 humidity readings below 10
percent are among them, and the other 535 flagged readings are explained by the
same phenomenon. The flagged jumps are single readings that leave the level of
the device by a distinct amount. The sizes of the temperature jumps cluster at
3.2, 6.4, 12.8, 19.2 and 25.6 degrees and those of the humidity jumps at 12.8,
19.2, 25.6 and 51.2 percentage points, and 349 of the 350 temperature jumps and
306 of the 319 humidity jumps lie within 0.4 of a multiple of 3.2, whereas sizes
spread evenly would do so in a quarter of the cases. This is an observation, and
no cause is claimed for it. The device `b8:27:eb:bf:9d:51` has no flagged jump,
the device `00:0f:00:70:91:0a` has 236 temperature and 223 humidity jumps, and
the device `1c:bf:ce:15:ec:4d` has 114 and 96. The limits lie below the
smallest class of each metric, around 3.2 for the temperature and around 12.8
for the humidity.

A real fast change, for instance a fire, is flagged in its first two readings,
and the third is accepted as the new reference, so that a change of level cannot
lock a device out. This is the trade-off of the rule. Two flagged readings span
about twice the median interval, which is 5.4 seconds for two devices and 3.7
for the third, so roughly 7 to 11 seconds. That is far below the batch interval
of one day, and alerts are only as current as the batch interval, so the delay
does not change how current an alert is. The measured cost on the sample is two
re-baselines, both for humidity, a rise of the device `1c:bf:ce:15:ec:4d` on 14
July at 21:00 and one of the device `00:0f:00:70:91:0a` on 16 July at 20:12, each
with two flagged readings before the third was accepted. The alternative of the
limits 5 degrees and 30 points was rejected, because it lets the smaller spike
classes through. Among the flagged jumps there are 46 temperature jumps of 5
degrees or less and 196 humidity jumps of 30 points or less, and every one of
them would remain in the hourly minimum and maximum that planners read, where a
single spike is far more visible than in the mean.

The state of a device consists of the reference, the length of the current run of
jump flags and the identifier of the last reading. A reading that repeats the
identifier of the previous one receives the flags of the first and changes
nothing, because only the first is stored. The state is rebuilt after a restart
by passing the last valid reading of the device and every reading after it
through the rules from an empty state. A unit test checks that this reproduces
the flags of an uninterrupted run at every possible cut, so a rerun of a batch
yields the same flags. The loader of B5 reads that history from the database.

The limits are stated openly as limits. They are derived from three sensors, one
of which shows no spikes, and they have to be revisited for every new sensor
type, as a metric that is not listed has no rule until it is added. The range of
the temperature assumes Celsius, and the stored values are kept as delivered
(ADR-0010).

Note added on 2026-11-13 (B5): the last paragraph of this entry says that the
state of a device is rebuilt by passing the last valid reading and every reading
after it through the rules. The loader obtains the same state from two values,
the last valid reading before the batch and the number of readings after it that
carry a jump flag, see ADR-0012. A unit test shows that the two values are
enough at every point of long synthetic histories, among them readings that the
streak accepts while a range rule flags them. The text of this entry is
otherwise unchanged.

---

## ADR-0012, the loader, its retries and its image. 2026-11-13, accepted.

The loader walks through the daily batches in chronological order. A batch whose
entry in `batch_runs` is completed is skipped. Any other batch, including one
that is still marked as running, is processed again from its first row, in full.
A running entry on a rerun means that the earlier attempt was interrupted, and
the attempt counter of the entry records how many tries a day needed. Resuming
from a checkpoint inside a batch was rejected, because it would add a second
kind of state that has to stay consistent with the stored readings and would
save nothing, since repeating a chunk costs one comparison of identifiers per
reading.

Readings are written in chunks of 5,000 as unordered bulk updates with an upsert
and `$setOnInsert`, with the write concern `majority`. A stored reading is never
changed, so a repeated reading, a repeated chunk and a repeated batch alter
nothing, and the 13 duplicate rows of the source are absorbed by the same
mechanism. Plain inserts were rejected, because a duplicate would raise an error
that has to be told apart from a failure, and `$set` was rejected, because it
would let a rerun overwrite readings with flags that were computed from another
state. The writes are unordered so that one rejected document does not hide the
others, and the cost is that an interrupted write can leave gaps, which is why
the state of the rules never comes from the batch in progress. A chunk size of
5,000 keeps one request small and makes a repeated chunk cheap. The value is a
setting.

The flags of a reading depend on the readings before it, so the state of the
rules has to be the same on the first try and on every rerun. The state of each
device is therefore rebuilt from the stored readings before the start of the
batch and from nothing else. It consists of the last reading without flags and
the number of readings after it that carry a jump flag. The streak counts jump
flags and not all flags, because a reading that a range rule alone flags leaves
the streak as it was. The whole batch is then processed from its first row, and
the readings that an interrupted attempt wrote stay untouched, so the flags are
identical however far that attempt got. Taking the state from the last stored
reading of the day was rejected, because a gap left by an unordered write would
then give a rerun a state that the first attempt never had. The integration
tests interrupt a batch in three ways, with a first chunk written, with a later
chunk written alone and with every third row written, and compare all 405,171
stored flags with those of an uninterrupted load. A change that took the state
from the end of the day instead of its start made one of these tests fail.

The batch log keeps four counts per batch, the rows read, the readings inserted,
the readings that were already present and the readings flagged. The counts
describe the last attempt. After an interrupted attempt, the readings that it
wrote are counted as already present, so the sum of inserted and already present
is the number of rows read, and the number of stored readings of the day does
not depend on the history. The flagged count is over distinct stored readings,
so a repeated row is not counted twice. On the sample the loader reads 405,184
rows, inserts 405,171, finds 13 already present and flags 580, as pinned per day
in `tests/integration/test_loading.py`, and the 580 equal the count that the
profile script finds in the same file.

Operations that touch the database are repeated after a lost connection or a
change of primary. The driver keeps retryable writes switched on and waits up to
30,000 milliseconds for a suitable server. Beyond that, the loader repeats a
read, a write to the batch log or a chunk after `AutoReconnect`,
`NotPrimaryError`, `ServerSelectionTimeoutError` or `WriteConcernError`, waiting
2, 4, 8, 16 and 30 seconds between the six attempts, which adds up to one
minute. The gap between the loss of a primary and the election of the next one
was measured at about 11 seconds during B2 (journal of 2026-11-12) and is made
reproducible by B8, so the schedule covers it several times. A shorter schedule
would give up inside one election, and a constant wait would add load while a
member recovers, hence the exponential growth with a cap. The numbers are
constants of the code and not settings, because a deployment has no reason to
change them. An error of another kind, such as a document that the validator
rejects, would fail again in the same way, so it is not repeated. The driver
reports a write concern error of a bulk write inside a bulk error that cannot be
told apart from a rejected document, so the store raises it as its own class. A
batch that exhausts its attempts is marked as failed with the text of the
error, at most 1,000 characters, and the process exits with the code 1, and no
later batch is loaded, because the rules of the next day need the stored
readings of this one. B7 turns the failed entry into an alert of the type
`batch_failed`. A source that cannot be read, for instance because a row is
malformed, also ends the run with the code 1. Nothing of that batch has been
written by then, so it has no entry in the log, and the alert for this case is
left to B7. A setting with an invalid value ends the run with the code 2 before
any connection is made.

The settings are read from the environment and are all optional. `MONGODB_URI`
defaults to the published port of `mongo1`, and Compose sets the replica set
address, so that the loader finds the primary whichever node it is. `DATABASE`
defaults to `citysense` and exists so that the integration tests can load into
scratch databases, as a test hook in the production code was rejected. `CHUNK_SIZE`
and `SOURCE_FILE` complete the list. `MAX_BATCHES` stops a run after that many
batches were loaded, and unset means all. A batch that is skipped as completed
does not count, so that two runs with the limit 3 load the first six days. The
limit also simulates the daily arrival of data, which the tests use instead of a
hook. The loader writes one line per batch with the day, the counts and the
duration, and a last line with the totals and the total duration.

The image is built in two stages from `python:3.13.18-slim-trixie`, and the uv
binary is copied from `ghcr.io/astral-sh/uv:0.12.23`. Both tags were pulled on
2026-11-13 to verify that they exist. A moving tag such as `python:3.13-slim` was
rejected for the reason given in ADR-0009 for the database image, and installing
uv with pip was rejected, because its version would then follow the package
index and not the tag. The full Python image was rejected, because it carries
compilers that the loader does not need. An Alpine based image was not tried,
as the size of the finished image, 229 MB, is not a requirement. The dependencies
are installed with `uv sync --locked`, which fails if `uv.lock` does not match
`pyproject.toml`, so the image never resolves a version on its own, and the build
log shows that building the package of the project fetched nothing from the
network. The final stage holds the environment, the sample file and nothing
else, and the process runs as the user `loader` with the identifier 10001
without privileges. The Compose service has `restart: "no"`, waits until
`mongo-setup` has completed successfully, because documents written before the
validators exist would escape them, and runs with `init: true`, because the first
process of a container ignores the default action of SIGTERM and `docker stop`
would otherwise wait ten seconds and then kill it.

The cache of 0.25 GB per node, which ADR-0009 left to be checked by this load,
is sufficient for the sample. The loader needs 18.6 seconds for the first load
from clean volumes in one run and 16.8 seconds in another, as printed by its last
line, so the cache is not revisited. Two limits remain. A write that is in flight
when the primary is killed is covered by the retries, but a reproducible
demonstration with a real kill belongs to B8, and the count of a batch that was
retried after a partial write is a count of the last attempt only.

Note added on 2026-11-14 (B6): the two points that this entry leaves open are
decided in ADR-0013. The summary collections receive the index `device_period`,
and the statistics per metric take the shape that ADR-0013 describes. The
validators of the summaries now allow a null coverage and a null expected count,
because a device without a baseline has neither. The text of this entry is
otherwise unchanged.

---

## ADR-0013, the summaries, the coverage and the latest values. 2026-11-13, accepted.

The hourly and daily summaries and the latest value of every device are derived
by aggregation pipelines that end in `$merge`, so that the database does the work
next to the data and the loader reads only a few counts. They run inside the
batch, after the readings of the day are stored and before the batch is marked as
completed. Running them after the completion was rejected, because a failure
there would leave a completed batch without summaries, and the rerun skips
completed batches, so nothing would repair it. Inside the batch a failure leaves
the batch running, and the next run repeats the writes of the readings, which
change nothing, and the pipelines, which replace their documents by identical
ones. The pipelines select by device and time range, so that the index
`device_ts` serves them, and not by the field `batch`, which has no index.

A summary is keyed by `{device, period_start}` as fixed in ADR-0010 and carries
`reading_count`, which is the number of valid readings, `flagged_count`,
`expected_count`, `coverage`, an object `metrics` with `min`, `max` and `mean` for
each of the metrics `co`, `humidity`, `lpg`, `smoke` and `temp`, and the counts
`light_true` and `motion_true` for the two boolean metrics. Minimum, maximum and
mean are taken over valid readings only, so the value of a flagged reading cannot
reach a statistic, and the counts of the hourly summaries add up to the 404,591
valid readings of the sample, with 580 flagged, which an integration test checks
per hour and per day against the readings themselves. A mean of the booleans was
rejected in favour of a count, because a count of readings in which motion was
detected is what a planner can interpret. An hour in which every reading is
flagged still gets a document, with a count of zero and null statistics, so that
a fully flagged hour can be told apart from a silent one. An hour without any
reading gets no document, so the alert rules of B7 have to read a missing hour as
a count of zero. The daily summary is computed from the readings of the day and
not from the hourly means, because a mean of means is wrong whenever the hours
hold different numbers of readings. The readings are sorted before they are
grouped, so that the floating point sum of a mean is formed in the same order on
every run and a rerun does not change the last digits.

The coverage of a period is its reading count divided by its expected count. The
expected count of an hour is the median `reading_count` of the full hours of the
device before the batch day. The earlier hourly summaries of the device are taken
in time order and the first one is dropped, because a device starts to report at
an arbitrary moment and its first hour is therefore partial. A device without
earlier hours takes the baseline from the hours of the day itself, again without
its first hour, and only from hours that end at or before the newest reading of
the batch, because the later ones are cut short by the end of the data. A device
that has no such hour has no baseline, and its expected count and coverage are
null. The expected count of a day is the median of the earlier daily counts
without the first one, and a device with fewer days takes 24 times its expected
hourly count, so that the first days have a baseline as well. A rerun of a day
depends only on the earlier days, so it yields the same expected count however
many days follow, and the first day bootstraps itself from its own full hours.

The rejected alternative is to divide an hour by the median interval between the
valid readings of the device, which is what the backlog suggested first. The
intervals of two devices have a long tail of gaps, so their mean lies above their
median, 6.20 against 5.43 seconds for the device `00:0f:00:70:91:0a` and 6.54
against 5.42 seconds for `1c:bf:ce:15:ec:4d`, and healthy hours fall below the
expectation. Among the 191 full hours of each device, 7 hours of the first and 59
hours of the second lie below a coverage of 0.8 by this definition, against none
by the median of the hourly counts, and the third device, whose intervals have no
tail, has none by either. A coverage of 0.8 for a healthy device would be
mistaken for data loss by the planners and would make every threshold on the
coverage depend on the device. The mean interval, or a mean of the hourly counts,
was rejected as well, because an outage lowers a mean and therefore lowers the
expectation by which the outage is judged, so that the outage hides itself,
whereas the median does not move for a few hours. A unit test shows that ten hours
of outage lower a mean of 110 hourly counts below 530 and leave the median at 580.
With the chosen definition the coverage of the full hours of the sample lies
between 0.893 and 1.104 for `00:0f:00:70:91:0a`, between 0.835 and 1.213 for
`1c:bf:ce:15:ec:4d` and between 0.996 and 1.001 for `b8:27:eb:bf:9d:51`, as an
integration test pins.

The definition has limits that are stated here. A device that degrades slowly
over many weeks lowers its own baseline, so the coverage detects outages and not
gradual drift, and the baseline would have to be fixed to a reference period to
detect drift. An hour without a reading is absent from the summaries and
therefore from the baseline, which does not matter for the median as long as
fewer than half of the hours are missing. The first hour of a device is dropped
even if the device started exactly on the hour. A batch day is taken as complete
once it is loaded, and a completed batch is skipped, so a day that arrives in two
parts cannot be extended. The last day of the sample is such a case, it ends at
00:03:37 on 20 July, so its summaries report the plain figures, a coverage below
0.07 for the last hour of each device and below 0.01 for the last day, as
integration tests pin.

The consequence for B7 is the evaluation horizon, which is the latest `ts` loaded
across all devices. Threshold and sensor silent alerts are evaluated only for
hours that end at or before the horizon, and a later hour is still open and not
silent. This removes the false alert for the partial last hour, and the partial
day of 20 July is treated in the same way. It is the usual watermark approach. A
single device that goes silent while the others continue lies within the horizon
and is still detected. The rule is written into B7 of the backlog.

The latest value of a device is the newest valid reading of the batch, as a
document with the measurement time `ts`, the identifier of the reading as
`reading_id`, its batch and its metrics. It replaces the stored document only if
its time is not older, so loading an earlier day again can never move the latest
value backwards, which an integration test shows by repeating the second day
after all days are loaded. A device whose readings of a day are all flagged keeps
its earlier latest value. Replacing unconditionally was rejected for the reason
just given, and comparing in the loader was rejected, because the check and the
write would then be two operations that another run could interleave.

Both summary collections receive an index `device_period` on the device and the
period start, which ADR-0010 left open. It serves the query of a planner for one
device over a stretch of time, and an `explain` test shows that the plan uses it
and bounds both fields. The sample holds only 579 hourly and 27 daily summaries,
so the index is design and not yet a necessity. Its cost is small, because the
summaries are written once per batch, and a scan of every summary would grow with
every month of the feed, whereas the summaries are kept permanently. Omitting the
index was rejected for that reason. The validators accept a null coverage and a
null expected count, and require every other field to have its type.

With the derivation, the loader reported 21.3 seconds for the first load from
clean volumes in one run, against 16.8 and 18.6 seconds before, so the derivation
adds a few tenths of a second per batch.

---

## ADR-0014, the alert rules, their limits and their evaluation. 2026-11-14, accepted.

The alerts follow the model of ADR-0007. Threshold alerts are judged on the hourly
mean of the valid readings of one metric of one device, never on a single reading,
and an hour is judged only if it is complete, that is if it ends at or before the
horizon fixed in ADR-0013. Sensor silent alerts are judged on the coverage of an
hour. An hour in which a device sent no reading at all has no summary, and it is
read as a reading count of zero, so that silence is detected by the rule and not
by the absence of a document. Because the summaries hold valid readings only, a
flagged value cannot raise an alert, which an integration test shows with a
flagged reading of 99 degrees. An hour in which every reading is flagged has a
coverage of zero and is silent as well, because a sensor whose readings are all
implausible gives no usable value, and the recommended action tells the reader to
treat its values as unknown. Batch failed alerts are raised by the loader.

The rules are pure functions in `alert_rules.py`, and `alerting.py` reads the
summaries, hands them to the rules and writes the difference to the stored alerts.
The module `alerting.py` is new and was not in the planned layout, because
`store.py` is meant to hold single reads and writes, and `loader.py` to call an
evaluation and not to contain it. Both are written so that the evaluation can be
repeated at any time.

### The limits and their basis

The task speaks of recommended values, and none can be applied here, for two
reasons. The source does not explain the unit "ppm (%)" of carbon monoxide,
liquefied petroleum gas and smoke (ADR-0011), so no published limit for a gas
can be compared with a reading. The placement of the sensors, indoors or
outdoors, is not known, so neither a limit for a workplace nor a limit of a
weather service applies cleanly to temperature and humidity. The limits are
therefore operating limits, chosen from the measured distribution of the hourly
means of the sample week, configurable, and meant to be replaced by official
values once the units and the placement are known. They are

| Metric | Limit on the hourly mean | Alerts over the sample week |
| --- | --- | --- |
| temp | 28.0 | 5, all for `1c:bf:ce:15:ec:4d` |
| humidity | 85.0 | 1, for `1c:bf:ce:15:ec:4d` |
| co | 0.0075 | 3 |
| lpg | 0.010 | 3 |
| smoke | 0.030 | 2 |

which gives 14 threshold alerts, and an integration test pins them per metric and
device and by their identifiers. For each metric a lower and a higher candidate
were counted by `scripts/alert_candidates.py`, and an integration test pins the
figures. The lower candidates are rejected because they raise alerts for hours
that lie inside the usual range of a device. A limit of 0.006 for carbon monoxide
raises 5 alerts and fires on 47 hours of `b8:27:eb:bf:9d:51`, whereas the chosen
limit is exceeded in one hour of that device. A limit of 0.009 for liquefied
petroleum gas raises 8 alerts, 6 of them for the same device, a limit of 0.025 for
smoke raises 4 alerts, and a limit of 25 for temperature raises 8 alerts over 125
hours, which is about 16 hours of each of the 8 days, so that the alert would
describe the daily cycle of `1c:bf:ce:15:ec:4d` and not an exception. The higher
candidates are rejected because they leave a value that is already unusual without
an alert: 0.010 for carbon monoxide raises one alert, against 3 at the chosen
limit, 0.012 for liquefied petroleum gas one against 3, 0.035 for smoke one against
2, and 30 for temperature two against 5. For humidity the candidates 80, 85 and 90
all raise the same single alert, so the choice does not matter on the sample, and
85 is kept as the middle value. The gas limits are the weakest part of this
choice, because they rest on the distribution of one week and on three sensors.

The limits live in one committed file, `config/alert_thresholds.toml`, which the
loader reads with `tomllib` from the standard library at the start of a run, so
that no dependency is added. The format is TOML, Tom's Obvious Minimal Language. Compose mounts the directory `config` read only into
the loader, so that an operator can change a limit on the host without building
the image again, and the image holds a copy as a default, so that it also runs
without the mount. Every entry carries a comment that states its basis. A file
that is missing, is not valid TOML or holds a value that cannot be used stops the
loader with exit code 2 before it reads the database, because an alert rule that
is silently off is worse than a loader that does not start. A metric without an
entry is not judged, which is how an operator switches one off. Constants in the
code were rejected because a change would need a new image, and environment
variables were rejected because seven values with comments belong in a file. A
limit that is relative to the baseline of each device was rejected, because it
needs weeks of history that the first week does not have, and because it would
accept a sensor that is permanently too high.

A changed limit applies to the evaluations that run after the change. An alert
that is stored carries the limit that it was raised with in the field `threshold`
and is not rewritten when the limit changes, with two exceptions that follow from
the lifecycle. An alert that is unresolved and whose condition no longer holds
under the new limit is resolved by the next evaluation, because it would otherwise
stay open for ever, and a stored alert that is resolved is final. A day that is
evaluated again after a change is judged under the new limit and may raise alerts
that the old limit did not. This is the intended behaviour, and the test that
repeats every day uses the committed limits.

### The other parameters

A sensor is silent in an hour whose coverage is below 0.5. The lowest coverage of
a healthy full hour of the sample is 0.835 for `1c:bf:ce:15:ec:4d`, as ADR-0013
pins, so a limit of 0.8 would raise no alert on the sample either, but it lies only
0.035 below the lowest coverage of a healthy hour, so that normal variation of one
device would come close to it. The value 0.5 means that half of the usual readings are missing.
The first hour of a device is not judged, because it is partial by construction,
as in ADR-0013, and an hour of a device that has no baseline is unknown and not
silent, which means that a device that never delivers anything is not detected by
this rule. The sample week raises no silent alert, so tests with made up data
show the rule.

An alert is resolved when the next complete hour is clear, without a margin band
below the limit. The alert is one episode of consecutive hours, so a value that
leaves the limit for one hour and returns raises a second alert, as the limits
above were counted. A margin band was rejected because it adds a second parameter
per metric that has no measured basis, and the full hour of hysteresis already
stops a value that hovers around a limit within an hour from opening and closing
alerts. Longer hysteresis was rejected for the same reason. The time of
resolution is the end of the clear hour, which depends on the data and not on the
clock.

An alert is identified by `<type>|<source>|<metric or ->|<ISO start hour or ->`,
for instance `threshold|1c:bf:ce:15:ec:4d|temp|2020-07-12T18:00:00Z`. The hour is
the first hour of the episode, so that the same episode always has the same
identifier and a repeated evaluation updates it and never raises it again. This is
the deduplication, and it needs no separate lookup. An alert carries its type, its
severity, its status, its source, the device, the metric where there is one, the
highest hourly mean or the lowest coverage of the episode as `value`, the limit as
`threshold`, a message, a recommended action, `first_seen`, `last_seen` and
`occurrences`. The message names the metric, the device, the value and the limit,
for instance "Hourly mean temperature 28.46 above limit 28.0 at 1c:bf:ce:15:ec:4d".
The field `basis` refers to the hourly summary of the highest or lowest hour by
collection and identifier, and it is null for an hour without a summary, because a
reference to a document that does not exist would mislead. The times `first_seen`
and `last_seen` are the start of the first and of the last hour of the episode, and
`occurrences` is the number of hours in it. The recommended action is one fixed
sentence per type. For a threshold alert it is to check the location and inform the
affected district, for a sensor silent alert to inspect the sensor and its
connection and to treat its values as unknown, not as normal, and for a batch
failed alert to inspect the loader log and rerun the batch. The severity is
warning for threshold and sensor silent alerts and critical for a failed batch.

An engineer acknowledges an alert by setting the status to `acknowledged` with the
time in `acknowledged_at`. The evaluation keeps that status while the condition
persists and resolves the alert when it clears, and it writes with the stored
status as part of the filter, so that an acknowledgement made between the read and
the write is not overwritten. A write that finds nothing is made by the next
evaluation, which reads the alert again.

The cap is 10 new alerts per source and UTC day, over all types, counted by the day
of `first_seen`. An alert that is open keeps being updated after the cap is
reached, and the alerts that were held back are counted as `alerts_suppressed` in
the log entry of the batch, so that a cap is never silent. The cap keeps the
earliest alerts of the day, whatever the order in which the episodes were found.
The sample never reaches it, as the busiest devices raise 6 alerts in the whole
week, and unit and integration tests with 12 episodes in one day show it. A cap per type was
rejected because it would allow up to three times the number of alerts for one
device,
and no cap was rejected because the lecture names the cap as the protection
against an alert that is ignored because it is repeated. A suppressed episode has no
stored alert to continue from, so the next batch judges it again from its first
pending hour, with an identifier of its own, and the cap of the day of that hour
decides. This is accepted as a limit.

A failed batch raises an alert of the type `batch_failed` with the source `loader`
after the retries are exhausted, and the alert is resolved when the same day is
completed later, also when the batch is skipped as completed, which repairs an
alert that stayed open because a run ended in between. A source file that cannot be
parsed leaves no batch log entry, so it raises the same type with no batch day, the
identifier `batch_failed|loader|-|-`, and it is resolved by the next run that reads
the source to its end. A second failure of the same batch or source updates the
alert, counts an occurrence and reopens it if it was resolved. The times of these
alerts are the wall clock, because there is no hour of data to take them from. An
alert that cannot be written, for instance because the database is the reason for
the failure, is logged and does not hide the failure of the batch.

### The evaluation

The evaluation runs inside the batch, after the summaries and before the batch is
marked as completed, for the reason given in ADR-0013. It judges every complete
hour from the first pending hour to the horizon. The first pending hour is the one
that holds the newest reading before the day, which was open when the previous
batch was evaluated, and for the first batch it is the first hour of the data. If a
day without any data lies in between, its hours are pending as well, and every
device is silent in them. The summaries are read from there, and from the first
hour of an alert that is unresolved or ended in the hour before, so that an episode
that spans midnight keeps the identifier of its first hour and is not taken for a
new one. The result is a function of the summaries, the limits and the stored
alerts, with no wall clock in it, so a rerun of any day leaves the alerts
unchanged, which an integration test shows for each of the nine days of the sample
after all days are loaded. Remembering a watermark per device was rejected as a
second kind of state to keep consistent, and judging the whole history on every
batch was rejected because its cost grows with the retention. The alerts of one
source are read through a new index `source_first_seen` on `source` and
`first_seen`, which serves the cap and the lookup of an episode that began earlier.
The validator of `alerts` now requires the fields above, apart from those that do
not apply to every type, and the loader writes through the same bulk call as the
readings, so that a write concern failure is retried and a rejected document is not.

The counts of an evaluation are `alerts_opened`, `alerts_resolved` and
`alerts_suppressed`, and they are kept in the log entry of the batch, where the
sample week shows 14 opened and none suppressed. A rerun of a day opens and
resolves nothing.

### Limits of this design

The limits rest on one week of three sensors. An hour is judged once per batch, so
an alert is as current as the batch interval. The deduplication and the
cap depend on the stored alerts, so an alert document that is deleted by hand is
raised again at the next evaluation of its hours. A device without readings in
the database is not known to the evaluation, so only a device that has delivered
before can be silent.

---

## ADR-0015, the demonstrations of the failures and the verification queries. 2026-11-14, accepted.

Two failures are shown on the real stack and not only simulated in the tests: a
loader that is killed in the middle of a batch, and a primary that is killed in
the middle of a load. `scripts/demo_crash_rerun.sh` and `scripts/demo_failover.sh`
run them, `scripts/demo_queries.sh` shows the queries of the three users and the
acknowledgement of an alert, and each script keeps its output in `docs/evidence/`.
The scripts start from clean volumes, because a demonstration has to be
reproducible, and they therefore remove the volumes `citysense_*` of this project
and nothing else. A run in a scratch database next to the data of the stack was
rejected, because a scratch database has neither the validators nor the indexes of
the setup script, so that it would show a system that differs from the delivered
one. The scripts need Docker and a shell and nothing else, and the hashing runs
inside the container of the database, so that no tool of the host is assumed.

Both demonstrations compare the result with a load that was never interrupted,
which the script produces first on a clean stack. The comparison is a hash over
every document of `readings`, `summaries_hourly`, `summaries_daily`, `latest` and
`alerts`, with the keys of each document sorted, so that two databases are equal
when every document is equal, and not only when the counts agree. A comparison of
the counts alone was rejected, because a rerun could repeat the right number of
readings with a wrong flag or a wrong mean and the counts would still agree. Two
things are left out of the hash by design: the ingestion time of a reading, which
is the time of the write, and the batch log, whose attempts and counts differ
because the interrupted batch was written twice, which is what the demonstration
shows. The sample has 405,171 readings, 579 hourly and 27 daily summaries, 3
latest values and 14 alerts, and the five hashes of the interrupted loads equal
those of the uninterrupted one.

The loader is killed with SIGKILL, and the primary with `docker compose kill`,
which is SIGKILL as well. A graceful stop was rejected for both. The loader gets a
termination signal from `docker stop` and could end in an orderly way, and a
primary that is stopped hands over its role within a fraction of a second, as
measured in B2, whereas a kill leaves the cluster to notice the loss, which takes
about 11 seconds. A batch lasts about 2 seconds, which is too short to hit with a
polling loop, so the demonstrations load with a chunk size of 10 instead of 5,000.
A batch then lasts about 10 seconds, and the loader is killed as soon as the third
batch has stored 20,000 of its readings. The data do not depend on the chunk size,
and the hashes show that.

The crash leaves the batch 2020-07-14 with the status running and part of its
readings stored. The next run skips the two completed days, repeats the batch,
finds the stored readings already present and inserts the rest, and the batch log
shows two attempts and the status completed. The readings that were written
before the crash are recognised by their identifiers and are not written twice,
which is the idempotence of ADR-0004 and ADR-0012 shown on a real kill and not on a
simulated one.

The loss of the primary has a finding that is stated here, because it limits what
the demonstration shows. The loader logs no retry in the demonstration. The driver
repeats a single write once after a failover by itself, as `retryWrites` asks
(ADR-0012), and it first waits for a new primary for up to the server selection
timeout of 30 seconds, which is longer than the election of about 11 seconds. The
write that was in flight therefore completes after the election without an error
reaching the loader, and the batch in progress takes about 18 seconds instead of
about 9. The schedule of the loader itself, which waits 2, 4, 8, 16 and 30 seconds,
covers an outage that outlasts the wait of the driver, and it is tested with
injected errors in `tests/unit/test_loader.py` but not shown on the stack. Forcing
an outage longer than 30 seconds would need a second node to be stopped as well,
which leaves no majority, and the writes would then wait for the node to return and
not show a retry of the loader. The demonstration shows the first layer of the
defence and the tests show the second one. The node that was killed rejoins as a
secondary after it is started again, and the primary stays where the election put
it for the moment of the check, although `mongo1` has the highest priority.

The queries of `scripts/queries.js` answer one question per user of the concept.
The planner reads the hourly profile of a device and the daily means per device
from the summaries, with the count of readings and the coverage next to every
mean. The citizen reads the latest value of each sensor with its measurement time,
and the alerts that are open or acknowledged with their recommended action. The
engineer reads the batch log, the flagged readings by rule, the alerts about the
loader that are not resolved and the storage of the readings. The file only reads,
except for the last section, which acknowledges an alert if the environment
variable `ACKNOWLEDGE` names an open alert, and the filter names the status open,
so that an alert that is resolved is not touched. `scripts/demo_queries.sh` loads
the first day alone, because the sample week has no open alert at its end, and
acknowledges an alert that is still open. After the other days are loaded, the
alert is resolved and keeps the time of its acknowledgement, which shows the
status `acknowledged` of the alert model on real data and not only in a test.

---

## ADR-0016, continuous integration with GitHub Actions. 2026-11-14, accepted.

The claim that a fresh clone starts with one command rests on a single development
machine until another machine confirms it. A workflow in `.github/workflows/ci.yml`
therefore runs on every push and every pull request. The first job installs the
locked environment, runs ruff for the lint and the format check, and runs the unit
tests, which need no database and finish within a minute. The second job starts only
if the first one has passed, builds and starts the Compose stack as the README does,
waits for the loader and runs the integration tests against it. The integration job
was made dependent on the first one, so that a lint error does not cost the eight
minutes of the stack, although the two jobs could also have run in parallel for a
faster answer. GitHub Actions was chosen because the repository is to be hosted on
GitHub (R5), so that no second account or service is needed. A self hosted runner
and an external service were rejected for the same reason, because each needs a host
or an account that a reader of the repository could not reproduce.

The workflow starts the stack with `docker compose up --build --detach` and then
waits for the container of the loader with `docker wait`, failing the step if its
exit code is not 0. The option `--abort-on-container-exit` with `--exit-code-from
loader` was rejected, because it stops the three database nodes as soon as the loader
ends, and the integration tests need them running. The option `--wait` was rejected,
because the setup service and the loader are one shot services that end by design,
and the behaviour of the option for a service that has ended is a risk that the
explicit wait does not carry. The exact commands of the step were run on the
development machine before they were written into the workflow.

Two actions are used, `actions/checkout` and `actions/setup-python`, both from the
organisation of the platform. Each is pinned to the full identifier of its commit,
with the tag as a comment, because a tag can be moved after the fact and a commit
cannot. The identifiers were read from the tags of the two repositories on
2026-11-14: `actions/checkout` v7.0.1 and `actions/setup-python` v7.1.0. The action
`astral-sh/setup-uv` was rejected, because it would add a third owner whose code runs
with the token of the workflow, to save one line. The uv tool is installed with `pip
install uv==0.12.23`, the version of the tag in the `Dockerfile`, so that the image
and the checks use the same tool. The reason that the `Dockerfile` gives against
installing uv with pip, that the version would follow the package index, does not
apply here, because the version is fixed. The interpreter is the one that
`actions/setup-python` provides from `.python-version`, and `UV_PYTHON_DOWNLOADS` is
set to `never`, as in the image. The runner is `ubuntu-24.04` and not
`ubuntu-latest`, because the latter moves to a new release without a change of the
repository. A unit test, `tests/unit/test_workflow.py`, checks that the version of uv
in the workflow equals the tag in the `Dockerfile`, that every action is pinned to a
commit, that the commands of both jobs are present and that the token can only read
the repository.

The consequences are the following. The pins age, and nothing updates them
automatically; an update is made by hand and noted in this log, because an automatic
update service would be one more dependency. The hosted runners have an x86
processor, so the first successful run is also the first test of the stack on an
architecture other than Apple silicon, which the README states as untested. That
statement is not changed until a run has been observed. Finally, the workflow was
validated as far as it can be without the remote: the file was parsed, the commands
of both jobs were run on the development machine, and the guard tests pass. The
done condition of B10, that both jobs pass on the remote, is outstanding until the
history has been published and the run has been seen.

Note of 2026-11-14: the integration job described here ran the whole integration
suite on every push and was cancelled at its limit of 30 minutes in the first run on
the platform. ADR-0017 replaces that part with a selection that leaves out the slow
tests, and adds a job for the whole suite that is started by hand.

---

## ADR-0017, a push run without the slow integration tests and a full run on request. 2026-11-14, accepted.

This entry supersedes the part of ADR-0016 that gave the integration job the whole
integration suite on every push and a limit of 30 minutes for it. The remainder of
ADR-0016 stands. The first run on the platform (run 43615698440) passed the lint and
the unit tests in 28 seconds and started the stack and the loader in 3 minutes and 5
seconds, so the stack starts and loads on an x86 runner. The step that ran the whole
integration suite began at 16:31:13 UTC and was cancelled at the limit at 16:58:14
UTC, after 27 minutes. The log shows the progress of the suite: the files
`test_alerts.py`, `test_database.py` and `test_derived.py` had completed 92 tests by
16:32:29, which is 76 seconds after the start, and `test_loading.py` had completed 38
of its 51 tests at 16:57:33, which is 25 minutes later. The suite was therefore
progressing and not stuck. The output of plain pytest carries no time per test, so the
log cannot show whether a single test took very long, and the workflow now runs pytest
with `-v --durations=25 -o faulthandler_timeout=900`, which prints every test name, the
25 slowest tests and a stack dump of all threads after 15 minutes without progress.
These options are part of pytest and add no dependency.

The suite takes 518 seconds on the development machine, measured with the same
options. Of this time about 500 seconds belong to 27 of the 147 tests, which are
the following. Eleven tests load the whole sample again in the test process into a
scratch database: the reference load, the repeated and the interrupted loads, and the
repeated alert evaluations, which take between 30 and 68 seconds each. Fifteen tests
read the same collections through the fixture `loaded`, whose scratch case needs that
reference load, so its cost is the one load that they share. One test waits for the
timer of the server that removes expired readings, which wakes up every 60 seconds and
takes 60 seconds by construction. On the runner, the 38 completed tests of `test_loading.py` took 25 minutes, while the
whole of that file takes about 450 seconds on the development machine, the sum of its
measured durations. The runner was therefore slower by a factor of at least three for
this file. The cause is not established. The shared virtual machine under three
database nodes with majority writes is a candidate, but it was not measured, and the
first complete run on the runner will show it.

The tests are marked `slow`, which is registered in `pyproject.toml`. The workflow
runs `pytest -m "integration and not slow"` on every push and keeps the limit of 30
minutes. The 120 tests that remain are what a fresh clone depends on: the stack, the
roles of the nodes, the setup and the validators, the exit code of the loader, and the
stored counts, summaries and alerts of the stack itself, each of which is checked
against the pinned figures of the sample. This selection takes 47 seconds on the
development machine. A third job, `integration-full`, runs the whole suite with a limit
of 90 minutes, and only when a person starts the workflow by hand, so that one
complete run on the runner can be recorded. The event is part of the concurrency group
of the workflow, because otherwise a push would cancel a complete run that is still
under way on the same branch. The whole suite remains the check on the development
machine before every commit.

Three alternatives were rejected. Raising the limit of the single job to 90 minutes
was rejected, because every push would then wait up to that time and the cost on the
platform would grow with each change, while the 120 tests answer the question that a
push asks. Deleting or shortening the slow tests was rejected, because the figures that
they pin, for instance that a load interrupted at any chunk ends equal to an
uninterrupted one, are the correctness argument of the system. Running the slow tests
on a schedule was rejected, because the manual start gives the same result and needs no
timer that runs unattended on the repository. The consequence is that the push run no
longer proves the slow cases. A change that breaks only one of them is found on the
development machine, or by the manual run, and not by the push run.
