// One example query per user of the system, see the table of users in the concept.
//
// Usage, with the stack running and loaded:
//   docker compose cp scripts/queries.js mongo2:/tmp/queries.js
//   docker compose exec -T mongo2 mongosh --quiet \
//     "mongodb://mongo1:27017,mongo2:27017,mongo3:27017/?replicaSet=rs0" --file /tmp/queries.js
//
// The queries only read. The last section writes, and only if the environment
// variable ACKNOWLEDGE holds the identifier of an open alert, which shows how an
// engineer acknowledges it.

const d = db.getSiblingDB("citysense");
const iso = (moment) => moment.toISOString().replace(".000Z", "Z");
const round = (value, digits) => (value === null ? "null" : value.toFixed(digits));

function heading(text) {
  print("");
  print(`-- ${text}`);
}

// 1. Urban planner. How do conditions develop per sensor over the day, and how do
// locations differ? Both read the summaries and never the raw readings, so the
// cost does not grow with the number of readings. The count of readings and the
// coverage stand next to every mean, because a mean of few readings must not look
// as trustworthy as a mean of many.

const DEVICE = "1c:bf:ce:15:ec:4d";
const FROM = new Date("2020-07-15T06:00:00Z");
const TO = new Date("2020-07-15T18:00:00Z");

heading(`Planner: hourly temperature of ${DEVICE} from ${iso(FROM)} to ${iso(TO)}`);
print("hour                  readings  coverage  min     mean    max");
d.summaries_hourly
  .find({ device: DEVICE, period_start: { $gte: FROM, $lt: TO } })
  .sort({ period_start: 1 })
  .forEach((s) => {
    const t = s.metrics.temp;
    print(
      [
        iso(s.period_start),
        String(s.reading_count).padStart(8),
        round(s.coverage, 3).padStart(8),
        round(t.min, 1).padStart(7),
        round(t.mean, 2).padStart(7),
        round(t.max, 1).padStart(7),
      ].join("  "),
    );
  });

heading("Planner: how locations differ, the mean of the daily means over the stored days");
print("device               days  temp    humidity");
d.summaries_daily
  .aggregate([
    { $match: { reading_count: { $gt: 0 } } },
    {
      $group: {
        _id: "$device",
        days: { $sum: 1 },
        temp: { $avg: "$metrics.temp.mean" },
        humidity: { $avg: "$metrics.humidity.mean" },
      },
    },
    { $sort: { _id: 1 } },
  ])
  .forEach((row) => {
    print(
      [row._id.padEnd(17), String(row.days).padStart(4), round(row.temp, 2), round(row.humidity, 2)].join("  "),
    );
  });

// 2. Citizen, through the warning application. Is the air acceptable right now, and
// how current is that statement? The latest value always carries its measurement
// time, so that a sensor that stopped appears as an old value and never as an all
// clear, and the open alerts say what is wrong and what to do.

heading("Citizen: the latest valid value of every sensor, with its measurement time");
print("device               measured at           temp   humidity  co       batch");
d.latest.find().sort({ _id: 1 }).forEach((l) => {
  print(
    [l.device.padEnd(17), iso(l.ts), round(l.temp, 1).padStart(5), round(l.humidity, 1).padStart(8), round(l.co, 4), l.batch].join("  "),
  );
});

heading("Citizen: the alerts that are open or acknowledged");
const open = d.alerts.find({ status: { $in: ["open", "acknowledged"] } }).sort({ first_seen: 1 }).toArray();
if (open.length === 0) {
  print("none, which is only an all clear if the sensors are reporting, see the latest values");
}
open.forEach((a) => {
  print(`${a.severity} ${a.status} ${a._id}`);
  print(`  ${a.message}`);
  print(`  since ${iso(a.first_seen)}, ${a.occurrences} hours, action: ${a.recommended_action}`);
});

// 3. Data engineer. Did every batch arrive and load, how many readings were
// rejected, and is the storage growing as expected?

heading("Engineer: the batch log");
d.batch_runs.find().sort({ _id: 1 }).forEach((r) => {
  const c = r.counts || {};
  print(
    [
      r._id,
      r.status.padEnd(9),
      `attempts ${r.attempts}`,
      `read ${c.read}`,
      `inserted ${c.inserted}`,
      `present ${c.already_present}`,
      `flagged ${c.flagged}`,
      `alerts opened ${c.alerts_opened} resolved ${c.alerts_resolved} suppressed ${c.alerts_suppressed}`,
    ].join("  "),
  );
});

heading("Engineer: the readings that were flagged, by rule");
d.readings
  .aggregate([
    { $unwind: "$quality.flags" },
    { $group: { _id: "$quality.flags", readings: { $sum: 1 } } },
    { $sort: { readings: -1, _id: 1 } },
  ])
  .forEach((row) => print(`${row._id.padEnd(18)} ${row.readings}`));

heading("Engineer: alerts about the loader that are not resolved");
const failures = d.alerts.find({ type: "batch_failed", status: { $ne: "resolved" } }).toArray();
print(failures.length === 0 ? "none" : failures.map((a) => `${a._id}: ${a.message}`).join("\n"));

heading("Engineer: storage");
const size = d.runCommand({ collStats: "readings" });
print(
  `readings: ${size.count} documents, ${(size.size / 1048576).toFixed(1)} MiB of data, ` +
    `${(size.storageSize / 1048576).toFixed(1)} MiB on disk, ${size.avgObjSize} bytes per document`,
);
const kept = d.alerts.countDocuments({});
print(`alerts: ${kept} documents in total`);

// 4. Acknowledging an alert. The filter names the status open, so that an alert
// that is resolved, or is acknowledged already, is not changed. The evaluation of
// the next batch keeps the status while the condition persists and resolves the
// alert when it clears, and it keeps the time of the acknowledgement.

const target = process.env.ACKNOWLEDGE;
heading("Engineer: acknowledging an alert");
if (!target) {
  print("not run. Set ACKNOWLEDGE to the identifier of an open alert to acknowledge it.");
} else {
  const result = d.alerts.updateOne(
    { _id: target, status: "open" },
    { $set: { status: "acknowledged", acknowledged_at: new Date() } },
  );
  print(`${target}: matched ${result.matchedCount}, modified ${result.modifiedCount}`);
  const after = d.alerts.findOne({ _id: target });
  print(`status ${after.status}, acknowledged at ${iso(after.acknowledged_at)}`);
}
