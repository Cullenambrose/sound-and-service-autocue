const assert = require("node:assert/strict");
const test = require("node:test");
const {ReviewDraft} = require("./review-state.js");

function fixture() {
  const values = new Map();
  const storage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
  };
  let id = 0;
  const open = (dataset = "library-one", hash = "content-hash") =>
    new ReviewDraft(storage, dataset, hash, 7, () => `request-${++id}`);
  return {values, storage, open};
}
const cues = {A: {bar: 0, name: "Intro"}};
const receipt = pending => ({
  ok: true, request_id: pending.request_id, dataset_id: pending.dataset_id,
  track_hash: "content-hash", revision: 8, sequence: 12,
});

test("lost response and reload retry exactly the same durable request", () => {
  const {open} = fixture(), draft = open();
  const request = draft.prepare(cues, "VOCAL");
  const restored = open();
  assert.deepEqual(restored.record.cues, cues);
  assert.deepEqual(restored.prepare(cues, "VOCAL"), request);
  restored.accept(receipt(request), cues);
  assert.equal(open().record.pending, null);
});

test("new edits do not mutate an ambiguous pending request", () => {
  const {open} = fixture(), draft = open(), pending = draft.prepare(cues, "VOCAL");
  const changed = {A: {bar: 8, name: "New"}};
  assert.deepEqual(draft.prepare(changed, "DROP").cues, cues);
  draft.accept(receipt(pending), changed);
  const next = draft.prepare(changed, "DROP");
  assert.notEqual(next.request_id, pending.request_id);
  assert.equal(next.base_revision, 8);
  assert.deepEqual(next.cues, changed);
});

test("HTTP success without a matching committed receipt cannot clear the outbox", () => {
  const {open} = fixture(), draft = open(), pending = draft.prepare(cues, "VOCAL");
  for (const invalid of [{ok:true}, {...receipt(pending), dataset_id:"other"},
    {...receipt(pending), request_id:"other"}, {...receipt(pending), revision:1},
    {...receipt(pending), sequence:0}, {...receipt(pending), track_hash:"other"}]) {
    assert.throws(() => draft.accept(invalid, cues), /acknowledgment/);
    assert.deepEqual(open().record.pending, pending);
  }
});

test("dataset and content identities isolate drafts; discard affects only one track", () => {
  const {open} = fixture(), draft = open();
  draft.save(cues);
  assert.equal(open("another-library").record.cues, null);
  assert.equal(open("library-one", "another-file").record.cues, null);
  assert.deepEqual(open().record.cues, cues);
  draft.clear();
  assert.equal(open().record.cues, null);
});

test("storage failures are explicit and retain the prior retry request", () => {
  const {open, storage} = fixture(), draft = open();
  const pending = draft.prepare(cues, "VOCAL");
  storage.setItem = () => { throw new Error("Quota exceeded"); };
  assert.throws(() => draft.accept(receipt(pending), cues), /Quota exceeded/);
  assert.deepEqual(draft.record.pending, pending);
  assert.deepEqual(open().record.pending, pending);
});

test("a stale tab cannot silently overwrite or delete another tab's draft", () => {
  const {open} = fixture(), first = open(), second = open();
  first.save(cues);
  assert.throws(() => second.save({A:{bar:20,name:"Other edit"}}), /Another tab/);
  assert.throws(() => second.clear(), /Another tab/);
  assert.deepEqual(open().record.cues, cues);
  assert.equal(second.record.cues.A.bar, 20);
});
