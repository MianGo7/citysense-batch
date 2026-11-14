#!/usr/bin/env bash
# Demonstration of C2: a loader that is killed in the middle of a batch leaves an
# interrupted batch behind, and the next run repeats that batch and ends with
# exactly the data of a load that was never interrupted.
#
# The container is killed with SIGKILL, which gives the process no chance to clean
# up, as a crash or the loss of the machine would. `docker stop` was rejected,
# because its SIGTERM lets the process end in an orderly way and shows nothing.
#
# Usage: scripts/demo_crash_rerun.sh. The output is written to the evidence file as
# well. The script resets the stack of this project and takes about four minutes.

source "$(dirname "$0")/demo_common.sh"

main() {
  step "1. The standard: an uninterrupted load on a clean stack"
  reference=$(reference_digest)
  say "$reference"

  step "2. The same load, killed in the middle of the batch 2020-07-14"
  stack_reset
  docker compose run -d --name "$LOADER" -e CHUNK_SIZE="$SLOW_CHUNK" loader >/dev/null 2>&1
  wait_for_batch 2020-07-14 20000
  docker kill "$LOADER" >/dev/null
  say "exit code of the killed loader: $(docker inspect -f '{{.State.ExitCode}}' "$LOADER") (137 is SIGKILL)"
  docker logs "$LOADER" 2>&1 | grep -E 'completed|failed|finished' || true
  docker rm "$LOADER" >/dev/null

  step "3. What the crash left behind"
  batch_table
  interrupted=$(mongo --eval 'print(db.getSiblingDB("citysense").batch_runs.findOne({_id: "2020-07-14"}).status)')
  [ "$interrupted" = "running" ] || fail "the batch 2020-07-14 should be left running, it is $interrupted"
  say "the batch 2020-07-14 is still running, and no later batch exists"

  step "4. The next run"
  second=$(docker compose run --rm loader 2>&1) || fail "the second run did not end with exit code 0"
  printf '%s\n' "$second" | grep -E 'skipped|completed|failed|finished'

  step "5. The result"
  batch_table
  attempts=$(mongo --eval 'print(db.getSiblingDB("citysense").batch_runs.findOne({_id: "2020-07-14"}).attempts)')
  [ "$attempts" = "2" ] || fail "the interrupted batch should have 2 attempts, it has $attempts"
  say "the interrupted batch was repeated: 2 attempts, status completed"
  actual=$(digest)
  say "$actual"
  compare_digests "$reference" "$actual"
  say "readings stored: $(printf '%s\n' "$actual" | awk '$1 == "readings" {print $2}'), expected $STORED_READINGS"
  printf '%s\n' "$actual" | awk -v n="$STORED_READINGS" '$1 == "readings" && $2 != n {exit 1}' \
    || fail "the number of readings is not $STORED_READINGS"
  step "PASS: the rerun after a crash equals the uninterrupted load"
}

run_demo docs/evidence/crash_rerun.txt main
exit $?
