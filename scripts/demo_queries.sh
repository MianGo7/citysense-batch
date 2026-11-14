#!/usr/bin/env bash
# Demonstration of the queries of the three users and of the status `acknowledged`.
#
# The first day is loaded alone, so that alerts are still open when an engineer
# acknowledges one of them. The rest of the sample is loaded afterwards, and the
# alert that was acknowledged is then resolved by the evaluation, which keeps the
# time of the acknowledgement. This is the lifecycle of ADR-0014 shown on real data.
#
# Usage: scripts/demo_queries.sh. The output is written to the evidence file as
# well. The script resets the stack of this project and takes about one minute.

source "$(dirname "$0")/demo_common.sh"

queries() {
  docker compose cp scripts/queries.js mongo2:/tmp/queries.js >/dev/null 2>&1
  docker compose exec -T -e ACKNOWLEDGE="${1:-}" mongo2 mongosh --quiet "$URI" --file /tmp/queries.js
}

main() {
  step "1. A clean stack with the first day loaded"
  stack_reset
  docker compose run --rm -e MAX_BATCHES=1 loader 2>&1 | grep -E 'completed|failed|finished|stopped'

  step "2. The queries of the three users, with alerts still open"
  queries

  step "3. An engineer acknowledges the first open alert"
  target=$(mongo --eval '
    const a = db.getSiblingDB("citysense").alerts.find({status: "open"}).sort({_id: 1}).limit(1).toArray();
    print(a.length ? a[0]._id : "");')
  [ -n "$target" ] || fail "no alert is open after the first day"
  queries "$target" | sed -n '/acknowledging an alert/,$p'

  step "4. The rest of the sample is loaded"
  docker compose run --rm loader 2>&1 | grep -E 'completed|failed|finished'

  step "5. The acknowledged alert after the evaluation of the later days"
  result=$(mongo --eval '
    const a = db.getSiblingDB("citysense").alerts.findOne({_id: "'"$target"'"});
    print([a.status, a.acknowledged_at ? "acknowledged_at " + a.acknowledged_at.toISOString() : "no time", "resolved_at " + a.resolved_at.toISOString()].join(", "));')
  say "$target: $result"
  case "$result" in
    resolved,*"acknowledged_at "*) ;;
    *) fail "the acknowledged alert should be resolved and keep the time of the acknowledgement" ;;
  esac

  step "6. The queries of the three users on the whole sample"
  queries

  step "PASS: the queries answer the three users, and an acknowledged alert is resolved with its time kept"
}

run_demo docs/evidence/queries.txt main
exit $?
