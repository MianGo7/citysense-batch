// Prints one canonical line per document of the collection in the environment
// variable COLLECTION, in the order of the identifiers, so that a hash of the
// output compares two databases for equality of content.
//
// The keys are sorted, because two documents are the same document whatever the
// order of their fields. The ingestion time of a reading is left out, because it
// is the time of the write and differs between two loads by design.

const database = db.getSiblingDB("citysense");
const name = process.env.COLLECTION;
const VOLATILE = { readings: ["ingested_at"] };

function canonical(value) {
  if (Array.isArray(value)) {
    return value.map(canonical);
  }
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonical(value[key])]),
    );
  }
  return value;
}

database.getCollection(name).find().sort({ _id: 1 }).forEach((document) => {
  for (const field of VOLATILE[name] ?? []) {
    delete document[field];
  }
  print(JSON.stringify(canonical(JSON.parse(EJSON.stringify(document)))));
});
