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
