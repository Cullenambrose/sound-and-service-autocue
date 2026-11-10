const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const {ReviewDraft} = require("./review-state.js");
const source = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");

function editor(revision = 1) {
  const values = new Map(), storage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
  };
  const cues = {A:{name:"Intro",bar:4}};
  const draft = new ReviewDraft(storage, "dataset", "hash", 1, () => "request-id");
  draft.prepare(cues, "VOCAL");
  const state = vm.createContext({
    activeTrack:{hash:"hash", map_type:"VOCAL", review_revision:revision, cues:{A:{bar:2}}},
    reviewDraft:draft, reviewSaving:false, reviewSaved:"original", reviewSaveError:"",
    reviewSnapshot:()=>cues, setReviewBusy:()=>{}, selected:new Set(),
    toast:()=>{}, reload:async()=>{},
  });
  vm.runInContext(source.slice(source.indexOf("async function persistReview("),
                               source.indexOf("async function saveReview(")), state);
  return {state, draft, values};
}

function receipt(request) {
  return {ok:true,request_id:request.request_id,dataset_id:"dataset",track_hash:"hash",revision:2,sequence:1};
}

test("the editor retains failures and retries the identical request before reporting success", async () => {
  const {state,draft,values} = editor();
  const requests = [];
  state.api = async (_url, options) => {
    const request = JSON.parse(options.body);
    requests.push(request);
    if (requests.length === 1) throw new Error("Lost response after commit");
    return receipt(request);
  };
  assert.equal(await state.persistReview(), false);
  assert.match(state.reviewSaveError, /Not saved:.*Lost response/);
  assert.equal(state.reviewSaved, "original");
  assert.ok(draft.record.pending);
  assert.equal(await state.persistReview(), true);
  assert.deepEqual(requests[0], requests[1]);
  assert.equal(values.size, 0);
  assert.equal(state.activeTrack.review_revision, 2);
});

test("a recovered older receipt cannot masquerade as the latest saved review", async () => {
  const {state,draft} = editor(3);
  state.api = async (_url, options) => receipt(JSON.parse(options.body));
  assert.equal(await state.persistReview(), false);
  assert.match(state.reviewSaveError, /newer review is saved/);
  assert.equal(state.activeTrack.review_revision, 3);
  assert.equal(state.activeTrack.cues.A.bar, 2);
  assert.ok(draft.record.pending);
});

test("success-shaped responses without receipts never mark edits saved", async () => {
  const {state,draft} = editor();
  state.api = async () => ({ok:true});
  assert.equal(await state.persistReview(), false);
  assert.match(state.reviewSaveError, /acknowledgment/);
  assert.equal(state.reviewSaved, "original");
  assert.ok(draft.record.pending);
});
