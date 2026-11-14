#!/usr/bin/env bash
# Demonstration of C5 and C11: the primary of the replica set is killed in the
# middle of a batch, a new primary is elected, the loader goes on, and the data
# equals that of a load that never saw a failure.
#
# The node is killed with SIGKILL and not stopped, because a graceful stop makes
# the primary hand over its role within a fraction of a second, whereas a kill
# leaves the cluster to notice the loss, which takes about ten seconds (ADR-0009).
# The load is read through a direct connection to a secondary, because a read from
# the primary would wait for the election and hide the stall that is to be shown.
#
# Usage: scripts/demo_failover.sh. The output is written to the evidence file as
# well. The script resets the stack of this project and takes about four minutes.

source "$(dirname "$0")/demo_common.sh"

main() {
  step "1. The standard: an uninterrupted load on a clean stack"
  reference=$(reference_digest)
  say "$reference"

  step "2. The same load, with the primary killed in the middle of the batch 2020-07-14"
  stack_reset
  say "before: $(docker compose exec -T mongo3 mongosh --quiet --eval 'print(rs.status().members.map((m) => m.name + " " + m.stateStr).join(", "))')"
  docker compose run -d --name "$LOADER" -e CHUNK_SIZE="$SLOW_CHUNK" loader >/dev/null 2>&1
  wait_for_batch 2020-07-14 20000
  killed=$SECONDS
  docker compose kill mongo1 >/dev/null 2>&1
  say "mongo1, the primary, was killed"

  step "3. The election and the progress of the loader, one sample per second"
  elected=""
  previous=""
  while [ "$(docker inspect -f '{{.State.Running}}' "$LOADER")" = "true" ]; do
    if [ -z "$elected" ]; then
      primary=$(docker compose exec -T mongo3 mongosh --quiet --eval \
        'const p = rs.status().members.find((m) => m.stateStr === "PRIMARY"); print(p ? p.name : "none")' 2>/dev/null || echo none)
      if [ "$primary" != "none" ] && [ "$primary" != "mongo1:27017" ]; then
        elected=$((SECONDS - killed))
        say "t+${elected}s a new primary is elected: $primary"
      fi
    fi
    state=$(progress 2>/dev/null || echo "unreadable")
    if [ "$state" != "$previous" ]; then
      say "t+$((SECONDS - killed))s batch and stored readings: $state"
      previous=$state
    fi
    sleep 1
    [ $((SECONDS - killed)) -lt 240 ] || fail "the loader did not finish within 240 seconds"
  done
  code=$(docker inspect -f '{{.State.ExitCode}}' "$LOADER")
  say "the loader ended with exit code $code"
  docker logs "$LOADER" 2>&1 | grep -E 'retrying|completed|failed|finished' || true
  docker rm "$LOADER" >/dev/null
  [ "$code" = "0" ] || fail "the loader did not survive the loss of the primary"
  [ -n "$elected" ] || fail "no election was observed"

  step "4. The node that was killed rejoins"
  docker compose start mongo1 >/dev/null 2>&1
  started=$SECONDS
  while [ $((SECONDS - started)) -lt 180 ]; do
    states=$(mongo --eval 'print(rs.status().members.map((m) => m.name + " " + m.stateStr).join(", "))' 2>/dev/null || true)
    case "$states" in
      *STARTUP*|*RECOVERING*|*"(not reachable"*|"") sleep 2 ;;
      *) break ;;
    esac
  done
  say "after: $states"

  step "5. The result"
  batch_table
  actual=$(digest)
  say "$actual"
  compare_digests "$reference" "$actual"
  printf '%s\n' "$actual" | awk -v n="$STORED_READINGS" '$1 == "readings" && $2 != n {exit 1}' \
    || fail "the number of readings is not $STORED_READINGS"
  step "PASS: the loader survived the loss of the primary, new primary after about ${elected}s"
}

run_demo docs/evidence/failover.txt main
exit $?
