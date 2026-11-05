const assert = require("node:assert/strict");
const test = require("node:test");
const {MatchOutbox, StageOutbox} = require("./catalog-state.js");
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
const stageFields = {snapshot_id:"snapshot", song_id:"song", decision_revision:3,
  file_id:"file", content_hash:"hash"};
const stageReceipt = pending => ({...pending, ok:true, track_hash:pending.content_hash, added:true});
test("staging retries survive reload in an outbox separate from match decisions", () => {
  const {storage, open} = fixture(), match = open();
  match.prepare(fields);
  const stage = new StageOutbox(storage, "dataset", () => "stage-id");
  const pending = stage.prepare(stageFields);
  const restored = new StageOutbox(storage, "dataset");
  assert.deepEqual(restored.pending, pending);
  restored.accept(stageReceipt(pending));
  assert.equal(new StageOutbox(storage, "dataset").pending, null);
  assert.deepEqual(open().pending, match.pending);
});
test("staging acknowledgment must identify the copied bytes and reviewed decision", () => {
  const {storage} = fixture(), stage = new StageOutbox(storage, "dataset", () => "stage-id");
  const pending = stage.prepare(stageFields);
  for (const invalid of [{ok:true}, {...stageReceipt(pending), track_hash:"wrong"},
    {...stageReceipt(pending), decision_revision:4}, {...stageReceipt(pending), added:"true"},
    {...stageReceipt(pending), file_id:"wrong"}, {...stageReceipt(pending), dataset_id:"wrong"}]) {
    assert.throws(() => stage.accept(invalid), /Staging acknowledgment/);
    assert.deepEqual(new StageOutbox(storage, "dataset").pending, pending);
  }
});
test("staging storage errors and stale tabs never clear or replace a retry request", () => {
  const {storage} = fixture(), first = new StageOutbox(storage, "dataset", () => "stage-id");
  const stale = new StageOutbox(storage, "dataset", () => "other-id");
  const pending = first.prepare(stageFields);
  assert.throws(() => stale.prepare(stageFields), /Another tab/);
  assert.throws(() => stale.clear(), /Another tab/);
  storage.removeItem = () => { throw new Error("synthetic quota"); };
  assert.throws(() => first.accept(stageReceipt(pending)), /quota/);
  assert.deepEqual(new StageOutbox(storage, "dataset").pending, pending);
});
