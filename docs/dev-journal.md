# Development journal

A chronological record of the work, one entry per session, written on the
same day. Each entry states what was built, what was decided, which problems
occurred and how they were resolved. These entries are the main source for
the personal reflection in phase 3, so difficulties are recorded as they
occurred rather than summarised comfortably afterwards.

Template:

    ## YYYY-MM-DD

    What was worked on. What was decided and why. What broke and how it was
    solved. What comes next.

---

## 2026-11-11

The concept for phase 1 had been submitted and the tutor's feedback had
arrived. The feedback named one contradiction, planners looking for long term
trends in a data set that covers a single week, and asked for the users to be
thought through concretely, so that failure cases such as a broken system, a
full disk, network problems and false data become visible, and for indexes
and derived data to be planned explicitly. The concept was revised
accordingly: the week is treated as the start of a continuous feed loaded in
daily batches, the failure cases are named with figures from the data set,
and the summary, latest value and alert collections as well as the indexes
are part of the design. The lecture on alerts the tutor pointed to was
watched, and its points were turned into the alert model in ADR-0007.

The repository was set up with its documentation: the technical concept, the
requirements, the backlog and the first seven decisions. The licence of the
data set was checked and is CC0, so the sample data can be committed. The
replica set was moved from a documented option into the prototype, because a
reliability claim that can be demonstrated by stopping the primary during a
load is stronger than one that is only described.

Next: B0, the repository skeleton and toolchain.

---

## 2026-11-12

B0, the repository skeleton and toolchain, was taken. The item adds
dependencies, so a plan was stated first and the build backend, the
dependency ranges and the interpreter version were put to the student before
any file was written. The decisions were uv_build as the build backend,
compatible ranges in `pyproject.toml` with exact versions only in `uv.lock`,
and Python 3.13 instead of the 3.12 named in the backlog, because 3.13 is
already installed on the development machine. They are recorded in ADR-0008,
and the text of B0 in the backlog was changed to match.

The package `src/citysense/` now holds the nine modules of the planned layout
with docstrings only, `tests/unit/` and `tests/integration/` exist with the
`integration` marker registered, and a `.dockerignore` keeps the history,
caches, documents and tests out of the later image. The placeholder unit test
does more than prove that pytest runs: it parses the four rule modules and
fails if one of them imports pymongo, so the architecture rule is enforced
from the first commit. The first version of that test searched the source text
for the word and needed a string replacement to exclude its own docstring,
which was fragile, so it was rewritten to inspect the import statements.

Resolved versions in `uv.lock` are pymongo 4.19.0, pytest 9.2.0 and ruff
0.17.5.

Next: B1, the sample data in the repository.

---

## 2026-11-12

B1, the sample data in the repository, was taken. The original CSV is 61,926,558
bytes and was verified against the SHA-256 recorded in the course notes before
anything was derived from it. It is committed compressed with `gzip -9 -n`,
which stores neither a file name nor a timestamp, so the same input always
yields the same 6,466,866 byte archive. Both hashes are recorded in
`data/README.md`, and a unit test checks the hash of the archive, because a
silently changed data file would invalidate every figure derived from it.

The profiling script `scripts/profile_data.py` streams the compressed file
with the standard library and reproduces the facts of the concept: 405,184
rows, three devices, 13 exact duplicates, 7 temperature readings of exactly
0.0 and 38 humidity readings below 10 percent. The 13 duplicates on device
and timestamp turned out to be the same 13 rows as the exact duplicates. A
separate test asserts this, because the deterministic identifier of B4 relies
on it: if two different readings shared a device and timestamp, one of them
would be lost silently.

Two problems occurred. The source file lay in a folder that the working
environment was not allowed to read, and a first copy into a readable
location failed without any visible message and left an empty directory, so
the student compressed the file into the repository directly and the hashes
were verified there instead. Furthermore, the first version of the range test
failed on the humidity maximum: the source stores temperature and humidity as
single precision values, so 99.9 appears as 99.9000015258789 and differs from
99.9 by 1.5e-6, which is more than the tolerance of 1e-6 that had been chosen.
The tolerance was not a property of the data but a guess, so it was replaced
by 1e-4 with a comment stating the reason, which is far below the step of 0.1
that the claim is made in. The profile output now prints seven significant
digits for the same reason.

Two findings concern the source itself and are documented in `data/README.md`
without being resolved. The temperature is stated as Fahrenheit, but its
observed range of 0.0 to 30.6 is consistent with Celsius, and the unit
"ppm (%)" of the three gas columns is not explained. The system stores every
value as delivered, so the interpretation is left open and is not decided in
the code. In `requirements.md` the commitment C3 stays open, because only the
fact is pinned and the loader does not absorb the duplicates yet. A new
commitment C13 records that the quoted figures are reproduced and pinned, and
it is done.

Next: B2, the MongoDB replica set in Docker Compose.

---

## 2026-11-12 to 2026-11-13

B2, the MongoDB replica set in Docker Compose, was taken on 2026-11-12 and
finished after midnight, on 2026-11-13. The image tag, the cache size and the
published port were put to the student first, because the item names them as
questions, and were approved together with the member priorities, the details
of the compose file and the points of ADR-0009. The stack consists of `mongo1`, `mongo2` and `mongo3` from `mongo:8.0.38`, each with
a named volume, a health check and a cache of 0.25 GB, and of the one shot
service `mongo-setup`, which runs `db/init-replica-set.js`. The script
initiates the replica set only when the error `NotYetInitialized` shows that
it is not initiated yet, waits for a primary in both cases and exits with code
0, so it passed on the first start and on every repeated start. The integration
test `tests/integration/test_stack.py` checks one primary and two secondaries,
the priorities, mongo1 as primary and the cache limit, and passes against the
running stack.

One problem appeared during the work and needed two attempts. The `mongo` image
declares anonymous volumes for `/data/db` and `/data/configdb`, and every
recreation of a container left them behind, because only `down -v` removes
them. The first fix mounted `/data/configdb` as `tmpfs` on the three nodes. A
check after `down` and `up` still showed more dangling volumes, and inspecting
the containers showed that `mongo-setup` carried the same two anonymous volumes,
because it uses the same image and the image declares them whether or not the
container uses them. The second fix mounted both directories as `tmpfs` there.
The count of dangling volumes was not a usable test in the end, since the
machine also holds the volumes of unrelated projects and the total changed
independently of this stack. The mounts of the four containers, listed with
`docker inspect`, are the evidence: the nodes have only their named volume and
`mongo-setup` only its bind mount.

The times below were measured on one Apple silicon laptop with Docker 29.9.3
and about 8.3 GB of memory for the Docker virtual machine, with the image
already pulled. The first start, which included pulling the image, took 37 s
until the command returned and is not representative. They come from a helper
script that is not part of the repository, it polls with `docker exec`, so the
resolution is about half a second, and they are single observations on one
machine. The reproducible versions belong to B8, and these figures are to be
replaced by the captured output of its scripts.

- Clean start with the volumes removed, three runs: `docker compose up -d`
  returned after 5.9 s in each run, and mongo1 was the primary with
  `mongo-setup` finished after 17.5 s, 17.0 s and 17.0 s.
- `docker compose down` followed by `up` with the volumes kept, five runs:
  `mongo-setup` logged that the replica set was already initiated and exited
  with code 0 each time, and the stack had a primary again after 12.1 s, 22.1 s,
  12.5 s, 21.2 s and 11.2 s. The spread was not investigated. A cold restart of
  all three members presumably has to wait for an election timeout, but that
  has not been verified.
- Election after stopping mongo1 with `docker compose stop`, two runs: the
  command returned after 3.3 s, and a new primary was visible 0.2 s after the
  command was issued in both runs, because a stopping primary steps down first.
- Election after killing mongo1 with `docker kill`, two runs: a new primary was
  visible 11.3 s and 10.4 s after the kill, which matches the default election
  timeout of 10 s.
- Return of mongo1 after `docker compose start mongo1`: mongo1 was the primary
  again 11.0 s and 11.1 s after the command in the two graceful runs, and 12.0 s
  and 11.1 s in the two runs after a kill.

A write probe confirmed what the loader will experience. It ran inside `mongo3`
and inserted one document with write concern majority every 50 ms, without
retryable writes, across the stop or kill and the return of mongo1. After the
graceful stop it recorded no failed write in 1,259 and 1,270 attempts. After the
kill it recorded one window of failed writes in each run, of 10.7 s and 9.8 s,
with 20 and 18 failed attempts. The return of mongo1 caused no window of failed
writes in any of the four runs. The probe shows that the second election of the
failover test is harmless for short writes and that the loss of a primary by a
crash is a gap of about ten seconds. It does not show what happens to a long
bulk write that is in flight when the primary steps down, which can fail even
in the graceful case. That case is left to B5 and B8, and it is the reason the
loader needs retries with a backoff that is longer than the election timeout.

Next: B3, the database setup script.

---

## 2026-11-13

B3, the database setup script, was taken. The item defines the data model, so
the shape of every collection was put to the student first, and the answer
fixed more than had been proposed: the identifier of a reading as the string
`<device>|<epoch milliseconds>` with truncated milliseconds, `quality.flags` as
a required array that may be empty, the object `{device, period_start}` as the
identifier of the summaries, the device and the UTC day as the identifiers of
`latest` and `batch_runs`, and a required `source` on alerts. The alert type
that had been proposed as `threshold_exceeded` became `threshold`. All of it is
recorded in ADR-0010, and a short note under ADR-0007 points to the field
`source` that replaces "its source device", without editing the entry itself.

`db/setup-database.js` runs as the second call of `mongo-setup`, through the
replica set URI, so that it reaches the primary. It creates the six collections
with strict validators that allow additional fields, creates the three indexes
of the concept and applies the retention from `RETENTION_DAYS`. A second run
updates the validators and changes nothing else, and a changed retention is
applied to the existing TTL index through `collMod`. The integration tests in
`tests/integration/test_database.py` cover the collections and indexes, the
acceptance of a reading with unknown extra metrics, the rejection of a reading
without a device and of the malformed variants of every rule, the repeated
setup, the configurable and the invalid retention, and the expiry. With the
tests of B2, 38 integration tests and 14 unit tests pass, and ruff reports
nothing.

C10 is demonstrated in this item and not left open. The test inserts a reading
whose ingestion time lies 400 days in the past and a reading with a current
ingestion time, and polls until the TTL monitor has removed the first one, with
a limit of 150 seconds. The monitor wakes up every 60 seconds, so the wait
depends on where in that cycle the insert happens. In three runs the test took
20 s, 30 s and 40 s, as reported by `pytest --durations`, and the current
reading was still present each time.

Four problems occurred. The first draft of the script reached the database
through a helper function that also declared a constant named `db`, which would
have clashed with the `db` object of the shell, and it was simplified to one
named handle before the first run. Furthermore, the first version of the test
cleanup deleted its documents by device. To find out whether the rejection tests
could fail at all, the validation of `readings` was switched off by hand and the
tests were run again, and nine of them failed as they should. That run also
inserted a document without a device, which the cleanup by device could not
find, and it made a later run fail with a duplicate identifier after the
validator had been restored. The cleanup now deletes by the prefix of the
identifier and runs before each test as well as after it, so that the leftovers
of an interrupted run cannot break the next one. The third problem was minor: a
manual check of the exit code of the setup was swallowed by a pipe, which is why
the tests assert the return code directly.
the tests assert the return code directly.

The fourth problem was the most important, and it was found only by the final
check from a clean start: one run of the integration tests ended with 17 errors
and one failure. The 17 errors were exactly the tests whose cleanup writes to
the database, and the failure was the test that expects `mongo1` to be the
primary, so `mongo1` had not been the primary at that moment. The service
`mongo-setup` had exited as soon as any member was the primary, and after a first
initiation another member sometimes wins the first election. The state was read
at the moment the service exited in 14 clean starts. In 12 of them `mongo1` was
the primary and in 2 of them `mongo2` was, so the statement of ADR-0009 that
`mongo1` is the primary after a clean start was true only some seconds later.
These counts come from an ad hoc loop that is not part of the repository. The
cause was not the tests but the setup, so `init-replica-set.js` now waits after
the first initiation until `mongo1` is the primary, while a repeated start still
accepts any primary. After the change the same loop gave 14 clean starts of 14
with `mongo1` as the primary, which does not prove that the case cannot occur,
but the script now waits for exactly that state. A note under ADR-0009 records
the correction. This also matters for B5: the loader must not assume that the
primary is `mongo1`, and it connects through the replica set for that reason.

Open for the following items. The summary collections have no index on device
and period, which B6 decides once the volumes are known. The validator checks
the format of the identifier but not its agreement with `device` and `ts`, which
`identity.py` has to guarantee in B4. The figure of 13 duplicates on device and
millisecond, measured ad hoc before this item with the truncation fixed in
ADR-0010, is not quoted anywhere and is pinned in the profile script in B4.

Next: B4, the pure rules for identity, slicing and validation.
