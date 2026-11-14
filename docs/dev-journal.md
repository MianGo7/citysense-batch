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

---

## 2026-11-13

B4, the pure rules for identity, slicing and validation, was taken. The item
requires the ranges and the jump limits to be proposed with measured counts
before anything is implemented, so the data was analysed first. The analysis
changed the picture. The 7 temperature zeros and the 38 low humidity readings
that the concept names are a small part of a larger family of single readings
that leave the level of their device and return at the next reading. With the
adopted rules 580 of the 405,171 stored readings are flagged, so 535 lie beyond
the two named groups. The sizes of the jumps are not spread evenly but cluster
at distinct values, which are pinned and described in ADR-0011 without a claimed
cause. One device shows none of it. The ranges, the limits of 3 degrees and 10
percentage points, the re-baseline after two consecutive jump flags, the
maximum age of 300 seconds for the reference and the failure on parse errors
were approved, together with an order check per device that allows equal
timestamps, because a strict check would fail the batch on the 13 duplicate
rows.

Several things went differently from the first expectation. Comparing each
reading with the previous raw reading would flag a spike and the normal reading
after it, which was concluded by reasoning and not simulated, so the reference
became the last unflagged reading. That in turn can lock a device out once the
level has changed, and a simulated run with a temperature limit of 10 produced
46 consecutive flags behind a reference that was itself a glitch value. That run
made the risk visible and led to the re-baseline. The re-baseline triggers twice on
the sample, both times for a humidity rise that persisted, on 14 July at 21:00
and on 16 July at 20:12, and the cost of two flagged readings per event is named
in the ADR as the trade-off. A reading that repeats the identifier of the
previous one had to be handled in the state as well, because otherwise two
identical rows would count as two flags in the run before the re-baseline, but
only one reading is stored.

Two statements had to be corrected. In the proposal the sizes of the jumps were
described as multiples of 6.4. The pinned classes contain a class at 3.2, which
is half of 6.4, so the ADR says multiples of 3.2, and 349 of 350 temperature
jumps and 306 of 319 humidity jumps lie within 0.4 of one. Furthermore, the
first unit test that was meant to show why the identifier uses decimal
arithmetic failed on its own premise: a float does give the right millisecond for
three digit fractions at the magnitude of the sample, and the examples that
had been assumed to fail do not. A search found texts with ten digits after the decimal
point on which the float lands on the neighbouring millisecond, and the test uses
those. The consequence is that the sample cannot demonstrate the benefit of the
decimal conversion, which only the unit test does.

Four checks were added because a pass at the first attempt proves little. Ten
deliberate breaks of the rules, among them an exclusive bound, a limit that is
inclusive, a reference that is updated by a flagged reading, a re-baseline after
three flags, a missing repeat shortcut and a rounded float conversion, were each
caught by at least one unit test, and the sources were restored afterwards. A
unit test rebuilds the state at every cut of a synthetic sequence from the last
valid reading and compares the flags with those of the uninterrupted run, which
is the property that the loader of B5 depends on. All figures that the ADR
quotes are pinned in `tests/unit/test_sample_data.py`, including the median
intervals, the largest interval of 40.2 seconds that a comment in
`validation.py` mentions and the two re-baseline events. The counts of the
alternative limits 5 and 30 that were simulated beforehand are not quoted,
because no script of the repository produces them, and the ADR quotes the 46
and 196 jumps below those limits that follow from the pinned classes instead.

The item touches neither the database nor the containers, so the integration
tests were not run. 119 unit tests pass and ruff reports nothing. Open for B5:
the loader has to rebuild the state of every device from the database before a
batch, and `batches.py` is stricter than the order check that was asked for,
because a reading from an earlier day than the batch in progress cannot be
placed.

Next: B5, the loader and its container.


## 2026-11-13

B5, the loader and its container, was taken after the plan had been approved
with a set of fixed numbers: the images, the treatment of a batch that is still
running on a rerun, the retry schedule of 2, 4, 8, 16 and 30 seconds, a chunk size of 5,000,
unordered writes and four counts per batch. The result is `config.py`, `store.py`,
`loader.py` and `__main__.py`, a two stage `Dockerfile` and the service `loader`
in Compose, and the decisions are in ADR-0012. Both base images were pulled with
their exact tags before they were written down. A clean start with
`docker compose down -v && docker compose up --build` loads 405,184 rows into
405,171 readings, finds the 13 repeated rows already present, stores 580 flagged
readings, and the loader container exits with the code 0. The last line of the
loader reported 18.6 seconds for the first load in one run and 16.8 seconds in
another, so the cache of 0.25 GB per node that ADR-0009 left to be checked here
is sufficient and is not revisited. These are two observations from the log of
the loader, not a benchmark.

The central point is that the flags of a reading depend on the readings before
it, so that an interrupted and repeated batch could yield other flags than an
uninterrupted one. The state of every device is therefore rebuilt from the
stored readings before the start of the batch only, and the batch is processed
from its first row. This differs in its mechanism from the sentence at the end of
ADR-0011, which describes a replay from the last valid reading. The loader reads
the last valid reading and the number of jump flags after it instead, which is
two cheap queries on the index `device_ts`, and a note under ADR-0011 says so. A
unit test checks on twenty random histories, with spikes, steps, range
violations, gaps longer than the maximum age and repeated rows, that the two
values equal the state of the rules at every point. Counting all flags instead of
the jump flags made all twenty fail, so the test does discriminate.

The integration tests interrupt a batch in three ways, by writing a running entry
in the batch log together with the first chunk of a day, with a later chunk alone,
and with every third row, as an unordered write can leave gaps. After the rerun
every one of the 405,171 flags equals the flags of an uninterrupted load in a
scratch database. A change that took the state from the end of the day instead of
the start was caught by one of these three, but not by the other two, which is why
the case with every third row matters. The unit tests with an in-memory store
catch the same change in the two of their five variants that leave gaps, the late
rows alone and the mixed case.

Several checks failed before they passed. The first expected counts
in the unit scenario were wrong, because the first day has 15 rows and not 14,
and the formula for the readings that were already present was wrong when the
rows left behind contained both copies of the repeated row. Both were errors of
the test and not of the loader. A deliberate break of the flagged count, from
distinct readings to rows, was not caught at first, because the repeated rows of
the scenario were not flagged, so that rows and distinct readings gave the same
number. A flagged repeated row was added to the scenario and the break is now
caught. A deliberate break of the streak query in the store, from jump flags to
all flags, survived the integration tests on the sample, because the sample has
no flag of a range rule at all and no run of flags across midnight. A store level
test with readings made for the question now catches it. One break did not run at
all, because the substitution left unbalanced brackets, and it was repeated in
a valid form. Finally, two test modules named `test_loader.py` in the unit and the
integration directory collided during collection, and the integration module was
renamed `test_loading.py`.

One trial was made without a record in the repository: the primary `mongo1` was
killed with `docker kill` during a load into a scratch database. The batch in
flight took visibly longer than the others, no retry was logged, so the driver
handled the failover by itself within its server selection timeout, and the
scratch database ended with the same readings and the same 580 flags. This is
not evidence, because no script produces it, and no figure of it is quoted. The
reproducible demonstration of the loss of the primary during a load is B8. The
requirement C5 therefore stays open, since the retry schedule is only tested with
injected errors, and so does C11.

Points that were added or decided on the way and need the student's attention.
The setting `DATABASE` was added, because the integration tests load into scratch
databases and a hook in the production code was rejected. The setting
`MAX_BATCHES` counts the batches loaded in one run and not those completed in
total, so two runs with the limit 3 load six days. A source that cannot be parsed
ends the run with the code 1 but leaves no entry in the batch log, because the
day is not known when the slicing fails, and the alert for it is left to B7. The
counts in the batch log describe the last attempt, which is stated in ADR-0012.
The image was built once from a copy of the files that a commit would contain, in
a directory of the session, to make sure that it does not depend on files that are
untracked. The README and the demonstration of a restart come with B8 and B9.

Next: B6, derived data, after the commit of B5.

## 2026-11-13 to 2026-11-14

B6, the derived data, was taken after the proposal had been approved with the
coverage definition fixed in advance: the expected count is the median reading
count of the device's full hours before the batch day. The proposal was based on
a measurement made for the purpose, which compared two definitions of the expected
count on the stored data. The first divides an hour by the median interval, and
the second takes the median of the hourly counts. That measurement was a script in
the scratch directory of the session, so the figures were moved into
`scripts/profile_data.py` and pinned in `tests/unit/test_sample_data.py` before
ADR-0013 quotes them: 7 and 59 healthy hours below a coverage of 0.8 for two
devices by the median interval, and none by the median of the hourly counts.

The implementation is `aggregation.py`, three pipelines that end in `$merge`, and
a step in the loader that runs them inside the batch, before the batch is marked
as completed. The step reads the hourly counts of the day first, because a device
that has no earlier hours takes its baseline from its own full hours, and the
pipelines then receive the expected counts as constants. On the sample the stack
holds 579 hourly summaries, 27 daily summaries and three latest documents, the
hourly counts add up to 404,591 valid readings and 580 flagged ones, and the full
load takes 21.3 seconds in one run, which is a few tenths of a second per batch
more than before.

Several things needed a decision that the approval did not cover, and are recorded
in ADR-0013. A summary stores its `expected_count` next to the `coverage`, so that
the basis of the figure can be read and tested. The coverage and the expected count
are null for a device without a baseline, which required the validators to allow
null for these two fields. The first hour of every device is dropped from its own
baseline, because it is partial by construction, and the daily baseline falls back
on 24 times the hourly one for the first days, because a median of one earlier day
does not exist. The pipelines select by device and time and not by the field
`batch`, because only the former has an index. The readings are sorted before they
are grouped, so that the mean is summed in the same order on every run, since the
summaries have to be identical after a rerun.

The checks found two weak spots. A deliberate break of the lower bound of the
baseline query, from strictly before to before or equal, passed the first unit and
loader tests, because the query is only run against the database, and it was
caught only after a test of the earlier counts against the real collection was
added. The sample has no hour in which every reading is flagged and no silent hour,
so these cases are tested on readings made for the purpose in
`tests/integration/test_derived.py`, which includes the null statistics, the absence
of a document for an empty hour and the replacement of a summary when its readings
change. Seven deliberate breaks are caught: removing the guard on the latest value,
letting flagged values into the statistics, not counting flagged readings, keeping
the first hour in the baseline, replacing the median by the mean, running the
derivation after the completion and not at all. A scratch script named `inspect.py` shadowed the standard library module of the
same name, failed with a circular import and cost a few minutes.

One run of the whole integration suite failed with 17 failures and 16 errors
shortly after a clean start, among them the tests of `test_derived.py`, which pass
when run alone. Only the end of its output was kept, so the messages were lost and
the cause is not established. The suite was run twice more, once on the same stack
and once after another clean start, and passed completely both times, 98 tests in 6
and in 8 minutes. The tests connect directly to `mongo1`, so a change of the
primary during a run would explain such a burst, but this is a conjecture, and the
failure is recorded as unexplained. The runs of the suite now keep their full output.

The evaluation horizon for B7 was decided together with the approval and is written
into the backlog item. The last hour of the sample holds 36, 32 and 60 readings, so
its coverage is below 0.07, which would be a false sensor silent alert without that
rule. The summaries report the plain figure, and the alert rule leaves such an hour
out. Open for B7: an hour without any reading has no document, so the rule must read
it as a count of zero.

Next: B7, the alerts, which needs a proposal of the thresholds with measured counts
before anything is implemented.

## 2026-11-14

B7, the alerts, was taken after the student had approved the limits and the other
parameters on the basis of measured counts: the limits 28, 85, 0.0075, 0.010 and
0.030, a silence limit of 0.5 for the coverage, a cap of 10 new alerts per source
and day, resolution after one complete clear hour, and an alert of the type batch
failed for the loader, including an unreadable source file. The counts had been
measured first with a scratch query on the stored summaries. They were then moved
into `scripts/alert_candidates.py` and pinned by an integration test before any of
them was written into ADR-0014, as for B6.

The result is `alert_rules.py` with the pure rules, a new module `alerting.py` that
reads the summaries and writes the alerts, the limits in `config/alert_thresholds.toml`
read with `tomllib` and mounted into the loader by Compose, a stricter validator of
`alerts` and the index `source_first_seen`. The evaluation runs inside the batch
before the completion, like the derivation. The sample week raises 14 threshold
alerts, none silent, none suppressed, and the log entries of the batches show them
as opened, resolved and suppressed counts. An alert that spans midnight, which two
of the 14 do, keeps the identifier of its first hour, and repeating each of the nine
days after the full load changes nothing in the collection.

The hardest part was the idempotence of the evaluation. The first idea, to judge
only the hours of the batch day, fails twice: the last hour of a day is not
complete when the day is loaded, and an episode can begin on one day and end on
the next. The design that remained judges every complete hour from the first
pending hour, and from the first hour of any alert that is still unresolved, up to
the horizon, so that the result is a function of the summaries and the stored
alerts. A watermark per device was rejected as a second kind of state, and judging
the whole history on every batch was rejected because its cost grows with the
retention. The limits of the design are written into ADR-0014, among them that a
device that never delivers anything has no baseline and is not detected, and that
an episode suppressed by the cap is judged again from the first pending hour.

Several mistakes of the work itself were found by the checks and not by reading. A test of
the horizon had two devices without a summary in the hour in question and so
rightly found two silent alerts, a test of a device that is silent for a whole
batch had given that device no reading, so that it was not known to the evaluation
at all, and a test of the flagged readings sliced an unordered result and failed
once in four runs until the query was sorted. The mutation checks left one
survivor, the baseline taken from the summaries before the first judged hour,
which a new test now covers. A review of the draft of ADR-0014 found three
statements that no figure supported, among them that a higher limit for
temperature would have missed particular periods of heat and that the busiest
device raised 5 alerts, where two devices raise 6. They were replaced by the
pinned counts. The existing test of the validator, which inserted an alert with
three fields, had to be rewritten, because the contract changed with ADR-0014, and
the rule that applies is that its cases keep rejecting what they rejected before.

One small change outside the feature was needed. The handling of a write concern
error inside a bulk write moved from `insert_readings` into a function `bulk_write`
of `store.py`, because the alerts need the same handling, and the behaviour of the
readings did not change.

Next is B8, with the demonstrations and the verification queries, which include the
acknowledgement of an alert by an engineer.
