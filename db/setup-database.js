// Creates the collections with their validators and the indexes, see ADR-0010.
//
// The script runs on every start of the stack, so it converges instead of
// failing on a second run. A missing collection is created, an existing one has
// its validator replaced through collMod, so that a changed rule reaches data
// that lives in the volumes. Dropping and recreating was rejected because it
// would delete that data. The same holds for the retention period of the TTL
// (time to live) index, which collMod changes in place, whereas createIndex
// with different options on an existing index fails.
//
// The validators require the core fields and the types of the known metrics,
// and they allow every other field, so that a future sensor with a metric that
// is not known yet is accepted without a schema change.

const DATABASE = "citysense";

// The retention applies to the ingestion time, not to the measurement time,
// because the sample readings date from 2020 and would expire immediately.
const RETENTION_DAYS_TEXT = process.env.RETENTION_DAYS ?? "365";
const SECONDS_PER_DAY = 86400;

if (!/^[1-9][0-9]*$/.test(RETENTION_DAYS_TEXT)) {
  print(`RETENTION_DAYS must be a positive whole number, got "${RETENTION_DAYS_TEXT}".`);
  quit(1);
}
const RETENTION_SECONDS = Number(RETENTION_DAYS_TEXT) * SECONDS_PER_DAY;

const database = db.getSiblingDB(DATABASE);

const INTEGER = ["int", "long"];

// The identifier of a reading is the device and the measurement time in whole
// milliseconds since the epoch, truncated as the BSON date truncates, for
// instance b8:27:eb:bf:9d:51|1594512094385.
const READING_ID_PATTERN = "^[^|]+\\|[0-9]+$";
const DAY_PATTERN = "^[0-9]{4}-[0-9]{2}-[0-9]{2}$";

const SCHEMAS = {
  readings: {
    bsonType: "object",
    required: ["_id", "device", "ts", "ingested_at", "batch", "quality"],
    properties: {
      _id: { bsonType: "string", pattern: READING_ID_PATTERN },
      device: { bsonType: "string" },
      ts: { bsonType: "date" },
      ingested_at: { bsonType: "date" },
      batch: { bsonType: "string", pattern: DAY_PATTERN },
      // An empty array means checked and found plausible. A missing field would
      // mean never checked, an ambiguity that a strict validator must not allow.
      quality: {
        bsonType: "object",
        required: ["flags"],
        properties: { flags: { bsonType: "array", items: { bsonType: "string" } } },
      },
      // The metrics of the current sensors are optional, so that a future sensor
      // that lacks one of them is still valid, and they are typed when present.
      co: { bsonType: "number" },
      humidity: { bsonType: "number" },
      lpg: { bsonType: "number" },
      smoke: { bsonType: "number" },
      temp: { bsonType: "number" },
      light: { bsonType: "bool" },
      motion: { bsonType: "bool" },
    },
  },

  batch_runs: {
    bsonType: "object",
    required: ["_id", "status", "attempts", "started_at"],
    properties: {
      _id: { bsonType: "string", pattern: DAY_PATTERN },
      status: { enum: ["running", "completed", "failed"] },
      attempts: { bsonType: INTEGER },
      started_at: { bsonType: "date" },
      finished_at: { bsonType: "date" },
      counts: { bsonType: "object" },
      error: { bsonType: "string" },
    },
  },

  // The statistics per metric are an object per metric with minimum, maximum and
  // mean over valid readings, see ADR-0013. A coverage or an expected count of
  // null means that the device has no baseline yet.
  summaries_hourly: summarySchema(),
  summaries_daily: summarySchema(),

  latest: {
    bsonType: "object",
    required: ["_id", "device", "ts"],
    properties: {
      _id: { bsonType: "string" },
      device: { bsonType: "string" },
      ts: { bsonType: "date" },
    },
  },

  // The alert model of ADR-0007 as fixed by ADR-0014. The identifier has the form
  // type|source|metric or -|ISO start hour or -. The source is the device for a
  // sensor alert and loader for a failed batch, see ADR-0010. The metric, the
  // value, the threshold and the basis are absent or null where they do not apply,
  // for instance for a failed batch, and the basis is null for an hour without any
  // summary. The times of resolution and acknowledgement exist only after the
  // alert reached that status.
  alerts: {
    bsonType: "object",
    required: [
      "_id",
      "type",
      "severity",
      "status",
      "source",
      "message",
      "recommended_action",
      "first_seen",
      "last_seen",
      "occurrences",
    ],
    properties: {
      _id: { bsonType: "string" },
      type: { enum: ["threshold", "sensor_silent", "batch_failed"] },
      severity: { enum: ["warning", "critical"] },
      status: { enum: ["open", "acknowledged", "resolved"] },
      source: { bsonType: "string" },
      device: { bsonType: "string" },
      metric: { bsonType: "string" },
      value: { bsonType: "number" },
      threshold: { bsonType: "number" },
      basis: { bsonType: ["object", "null"] },
      message: { bsonType: "string" },
      recommended_action: { bsonType: "string" },
      first_seen: { bsonType: "date" },
      last_seen: { bsonType: "date" },
      occurrences: { bsonType: INTEGER },
      resolved_at: { bsonType: "date" },
      acknowledged_at: { bsonType: "date" },
    },
  },
};

// The identifier is the pair of device and period start, so that the pipelines
// of B6 can merge on _id without a further index. The two values are repeated as
// top level fields, because queries by device and period read them directly.
function summarySchema() {
  return {
    bsonType: "object",
    required: ["_id", "device", "period_start", "reading_count", "flagged_count", "coverage"],
    properties: {
      _id: {
        bsonType: "object",
        required: ["device", "period_start"],
        properties: { device: { bsonType: "string" }, period_start: { bsonType: "date" } },
      },
      device: { bsonType: "string" },
      period_start: { bsonType: "date" },
      reading_count: { bsonType: INTEGER },
      flagged_count: { bsonType: INTEGER },
      coverage: { bsonType: ["number", "null"] },
      expected_count: { bsonType: ["number", "null"] },
      metrics: { bsonType: "object" },
      light_true: { bsonType: INTEGER },
      motion_true: { bsonType: INTEGER },
    },
  };
}

function applyCollection(name, schema) {
  const options = {
    validator: { $jsonSchema: schema },
    validationLevel: "strict",
    validationAction: "error",
  };
  if (database.getCollectionInfos({ name }).length === 0) {
    database.createCollection(name, options);
    print(`created collection ${name}`);
  } else {
    database.runCommand({ collMod: name, ...options });
    print(`updated validator of ${name}`);
  }
}

function sameKey(left, right) {
  const normalise = (key) =>
    JSON.stringify(Object.entries(key).map(([field, direction]) => [field, Number(direction)]));
  return normalise(left) === normalise(right);
}

// Indexes carry fixed names, so that they can be found and changed by name.
function applyIndex(collection, name, key, expireAfterSeconds) {
  const target = database.getCollection(collection);
  const existing = target.getIndexes().find((index) => index.name === name);
  const options = expireAfterSeconds === undefined ? { name } : { name, expireAfterSeconds };
  if (existing === undefined) {
    target.createIndex(key, options);
    print(`created index ${collection}.${name}`);
    return;
  }
  // A different key under the same name is a conflict that must not be fixed
  // silently, because dropping an index on a large collection is not cheap.
  if (!sameKey(existing.key, key)) {
    throw new Error(`Index ${collection}.${name} exists with a different key.`);
  }
  if (expireAfterSeconds !== undefined && existing.expireAfterSeconds !== expireAfterSeconds) {
    database.runCommand({ collMod: collection, index: { name, expireAfterSeconds } });
    print(`changed retention of ${collection}.${name} to ${expireAfterSeconds} seconds`);
  }
}

for (const [name, schema] of Object.entries(SCHEMAS)) {
  applyCollection(name, schema);
}

// Serves the time range per device and the latest reading per device alike.
applyIndex("readings", "device_ts", { device: 1, ts: -1 });
applyIndex("readings", "ingested_at_ttl", { ingested_at: 1 }, RETENTION_SECONDS);
// Serves the summaries of one device over a period, which is what a planner asks.
applyIndex("summaries_hourly", "device_period", { device: 1, period_start: 1 });
applyIndex("summaries_daily", "device_period", { device: 1, period_start: 1 });
// Serves the open alerts per sensor.
applyIndex("alerts", "status_type_device", { status: 1, type: 1, device: 1 });
// Serves the alerts of one source from a day on, which the daily cap and the
// evaluation of an episode that began earlier read, see ADR-0014.
applyIndex("alerts", "source_first_seen", { source: 1, first_seen: 1 });

print(`Database ${DATABASE} is set up, retention ${RETENTION_DAYS_TEXT} days.`);
