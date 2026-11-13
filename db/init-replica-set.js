// Initiates the replica set rs0 and does nothing when it is already initiated.
//
// The script runs on every start of the stack, so it has to be safe to repeat.
// The check for the error NotYetInitialized is used instead of a flag file,
// because the state of the replica set lives in the database volumes and a flag
// outside of them could disagree with it.

const REPLICA_SET = "rs0";
const WAIT_FOR_PRIMARY_MS = 60000;

// Mongo1 gets the highest priority so that it is the primary after a clean
// start and again after it has returned from a failure. The port that is
// published to the host belongs to mongo1, so it normally reaches the primary.
const MEMBERS = [
  { _id: 0, host: "mongo1:27017", priority: 2 },
  { _id: 1, host: "mongo2:27017", priority: 1 },
  { _id: 2, host: "mongo3:27017", priority: 1 },
];

function isInitiated() {
  try {
    rs.status();
    return true;
  } catch (error) {
    if (error.codeName === "NotYetInitialized") {
      return false;
    }
    throw error;
  }
}

// With no host given, any member counts as the primary.
function hasPrimary(host) {
  try {
    return rs
      .status()
      .members.some(
        (member) => member.stateStr === "PRIMARY" && (host === undefined || member.name === host),
      );
  } catch (error) {
    return false;
  }
}

let awaited;
if (isInitiated()) {
  print(`Replica set ${REPLICA_SET} is already initiated, nothing to do.`);
} else {
  print(`Initiating replica set ${REPLICA_SET}.`);
  rs.initiate({ _id: REPLICA_SET, members: MEMBERS });
  // After the first initiation another member sometimes wins the first election,
  // and mongo1 takes over only some seconds later through its priority. The
  // published port belongs to mongo1, so the first start waits until it is the
  // primary, and a clean start is in the same state every time. A repeated start
  // accepts any primary, because mongo1 may be taking over after a failure.
  awaited = MEMBERS[0].host;
}

// Services that depend on this one start against a writable replica set, also
// after a restart of the stack, when an election may still be running.
const deadline = Date.now() + WAIT_FOR_PRIMARY_MS;
while (!hasPrimary(awaited)) {
  if (Date.now() > deadline) {
    const which = awaited ? ` ${awaited}` : "";
    print(`No primary${which} was elected within ${WAIT_FOR_PRIMARY_MS} ms.`);
    quit(1);
  }
  sleep(500);
}
print(`Replica set ${REPLICA_SET} has a primary${awaited ? `, ${awaited}` : ""}.`);
