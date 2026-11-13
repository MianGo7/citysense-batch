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

  // The statistics per metric are left open until B6 fixes their shape.
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

  // Only the fields that identify and manage an alert are required. The others
  // stay optional until the alert rules of B7 fix them. The field source is the
  // device for a sensor alert and loader for a failed batch, see ADR-0010.
  alerts: {
    bsonType: "object",
    required: ["type", "status", "source"],
    properties: {
      type: { enum: ["threshold", "sensor_silent", "batch_failed"] },
      status: { enum: ["open", "acknowledged", "resolved"] },
      source: { bsonType: "string" },
      device: { bsonType: "string" },
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
      coverage: { bsonType: "number" },
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
// Serves the open alerts per sensor.
applyIndex("alerts", "status_type_device", { status: 1, type: 1, device: 1 });

print(`Database ${DATABASE} is set up, retention ${RETENTION_DAYS_TEXT} days.`);
