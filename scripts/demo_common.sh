# Helpers shared by the demonstration scripts, which source this file.
#
# Everything runs through `docker compose`, so that a machine with nothing but
# Docker can repeat the demonstrations. The demonstrations reset the stack of this
# project, which removes the volumes `citysense_*` and nothing else, because the
# demonstrations have to start from a clean database to be reproducible.
#
# The syntax stays within what the bash 3.2 of macOS accepts: no arrays that can
# be empty, no `mapfile` and no associative arrays.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# The queries go through the replica set, from inside the container of mongo2, so
# that they find the primary and keep working while mongo1 is down.
URI='mongodb://mongo1:27017,mongo2:27017,mongo3:27017/?replicaSet=rs0'
LOADER=citysense-demo-loader

# A smaller chunk makes a batch last about ten seconds instead of two, which gives
# the demonstrations a window that a polling loop can hit. The data and the result
# do not depend on the chunk size.
SLOW_CHUNK=10

# Counts of the first load, which a test of the repository pins as well.
STORED_READINGS=405171

step() { printf '\n== %s\n' "$*"; }
say() { printf '%s\n' "$*"; }
fail() {
  printf 'FAILED: %s\n' "$*" >&2
  exit 1
}

# Runs a function and copies its output to an evidence file. The exit code of the
# function is kept, which `tee` alone would hide.
run_demo() {
  local out=$1
  shift
  mkdir -p "$(dirname "$out")"
  "$@" 2>&1 | tee "$out"
  return "${PIPESTATUS[0]}"
}

# Clean volumes, the three nodes up and the database set up. The loader is not
# started, so that the caller decides how it runs.
stack_reset() {
  docker rm -f "$LOADER" >/dev/null 2>&1 || true
  docker compose down -v --remove-orphans >/dev/null 2>&1 || true
  docker compose build loader >/dev/null 2>&1 || fail "the loader image could not be built"
  docker compose up -d mongo-setup >/dev/null 2>&1 || fail "the stack did not start"
  docker compose wait mongo-setup >/dev/null || fail "the database setup did not finish"
}

mongo() { docker compose exec -T mongo2 mongosh --quiet "$URI" "$@"; }

# A direct connection to mongo3 that accepts a secondary, so that the progress of
# a load can be read while mongo1 is down and no primary exists yet.
progress() {
  docker compose exec -T mongo3 mongosh --quiet --eval '
    db.getMongo().setReadPref("secondaryPreferred");
    const d = db.getSiblingDB("citysense");
    const run = d.batch_runs.findOne({status: "running"});
    print(run ? run._id + " " + d.readings.countDocuments({batch: run._id}) : "none 0");'
}

# Waits until the given batch is running and at least `minimum` of its readings are
# stored, which means that the loader is in the middle of it.
wait_for_batch() {
  local day=$1 minimum=$2 started=$SECONDS state
  while [ $((SECONDS - started)) -lt 180 ]; do
    state=$(progress)
    case "$state" in
      "$day "*)
        if [ "${state#* }" -ge "$minimum" ]; then
          say "the loader is in the middle of batch $day, $(( ${state#* } )) of its readings are stored"
          return 0
        fi
        ;;
    esac
    sleep 0.5
  done
  fail "batch $day did not reach $minimum stored readings within 180 seconds"
}

# One line per collection of the derived and stored data: its name, its number of
# documents and the start of a hash over its content. The ingestion time of a
# reading is left out, because it is the time of the write, and the batch log is
# left out, because its attempts differ by design. Everything else must agree.
digest() {
  local collection count hash
  docker compose cp scripts/digest.js mongo2:/tmp/digest.js >/dev/null 2>&1
  for collection in readings summaries_hourly summaries_daily latest alerts; do
    count=$(mongo --eval "db.getSiblingDB('citysense').getCollection('$collection').countDocuments({})")
    hash=$(docker compose exec -T -e COLLECTION="$collection" mongo2 sh -c \
      'mongosh --quiet "$0" --file /tmp/digest.js | sha256sum | cut -c1-16' "$URI")
    printf '%-17s %7s documents  sha256 %s\n' "$collection" "$count" "$hash"
  done
}

# The batch log as a table, which is what an engineer reads first.
batch_table() {
  mongo --eval '
    const d = db.getSiblingDB("citysense");
    d.batch_runs.find().sort({_id: 1}).forEach((r) => {
      const c = r.counts;
      const counts = c ? ["read " + c.read, "inserted " + c.inserted,
        "present " + c.already_present, "flagged " + c.flagged] : ["no counts yet"];
      print([r._id, (r.status).padEnd(9), "attempts " + r.attempts, ...counts].join("  "));
    });'
}

# An uninterrupted load on a clean stack, as the standard to compare with. Only the
# digest is printed, so that the caller can keep it in a variable.
reference_digest() {
  stack_reset
  docker compose run --rm loader >/dev/null 2>&1 || fail "the uninterrupted load failed"
  digest
}

compare_digests() {
  local reference=$1 actual=$2
  if [ "$reference" = "$actual" ]; then
    say "every collection equals the uninterrupted load, document for document"
  else
    diff <(printf '%s\n' "$reference") <(printf '%s\n' "$actual") || true
    fail "the data differs from the uninterrupted load"
  fi
}
