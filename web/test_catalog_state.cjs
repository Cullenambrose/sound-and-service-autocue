const assert = require("node:assert/strict");
const test = require("node:test");
const {MatchOutbox} = require("./catalog-state.js");
function fixture() {
  const values = new Map(), storage = {
    getItem:key => values.get(key) ?? null,
    setItem:(key, value) => values.set(key, value),
    removeItem:key => values.delete(key),
  };
  return {storage, open:(dataset = "dataset") => new MatchOutbox(storage, dataset, () => "request-id")};
}
const fields = {snapshot_id:"snapshot", song_id:"song", base_revision:2,
  file_id:"file", content_hash:"hash", override:false, note:"Reviewed"};
const receipt = pending => ({...pending, ok:true, revision:pending.base_revision + 1});
test("lost acknowledgment and browser reload preserve the exact match request", () => {
  const {open} = fixture(), first = open(), pending = first.prepare(fields);
  const restored = open();
  assert.deepEqual(restored.pending, pending);
  restored.accept(receipt(pending));
  assert.equal(open().pending, null);
});
test("new choice cannot replace an unresolved decision", () => {
  const {open} = fixture(), outbox = open(), pending = outbox.prepare(fields);
  assert.throws(() => outbox.prepare({...fields, file_id:"other"}), /pending match/);
  assert.deepEqual(open().pending, pending);
});
test("invalid acknowledgment cannot remove recovery state", () => {
  const {open} = fixture(), outbox = open(), pending = outbox.prepare(fields);
  for (const invalid of [{ok:true}, {...receipt(pending), revision:1},
    {...receipt(pending), content_hash:"other"}, {...receipt(pending), dataset_id:"other"},
    {...receipt(pending), song_id:"other"}, {...receipt(pending), file_id:"other"},
    {...receipt(pending), snapshot_id:"other"}, {...receipt(pending), request_id:"other"}]) {
    assert.throws(() => outbox.accept(invalid), /acknowledgment/);
    assert.deepEqual(open().pending, pending);
  }
});
test("missing decisions require a null file/hash acknowledgment", () => {
  const {open} = fixture(), outbox = open();
  const pending = outbox.prepare({...fields, file_id:null, content_hash:null});
  outbox.accept(receipt(pending));
  assert.equal(open().pending, null);
});
test("storage errors prevent sending and preserve retry state after commit", () => {
  const {storage, open} = fixture(), outbox = open();
  const write = storage.setItem;
  storage.setItem = () => { throw new Error("quota"); };
  assert.throws(() => outbox.prepare(fields), /quota/);
  assert.equal(outbox.pending, null);
  storage.setItem = write;
  const pending = outbox.prepare(fields);
  storage.removeItem = () => { throw new Error("denied"); };
  assert.throws(() => outbox.accept(receipt(pending)), /denied/);
  assert.deepEqual(open().pending, pending);
});
test("dataset scoping and stale-tab protection prevent silent overwrites", () => {
  const {open} = fixture(), first = open(), second = open();
  const pending = first.prepare(fields);
  assert.equal(open("another").pending, null);
  assert.throws(() => second.prepare(fields), /Another tab/);
  assert.throws(() => second.clear(), /Another tab/);
  assert.deepEqual(open().pending, pending);
});
