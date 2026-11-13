const assert = require("node:assert/strict");
const test = require("node:test");
const {StudioOutbox} = require("./catalog-state.js");
function storage() {
  const values = new Map();
  return {getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key)};
}
const approval = {endpoint: "/api/studio/approve", proposal_id: "proposal"};
const accepted = request => ({ok: true, request_id: request.request_id, dataset_id: request.dataset_id,
  proposal_id: request.proposal_id, crate_id: "studio-" + request.proposal_id, revision: 1, entries: 3});
test("Studio lost acknowledgments survive reload and validate exact proposal receipts", () => {
  const store = storage(), outbox = new StudioOutbox(store, "dataset", () => "request");
  const pending = outbox.prepare(approval);
  for (const change of [{ok:false}, {request_id:"other"}, {dataset_id:"other"}, {proposal_id:"other"},
    {crate_id:"wrong"}, {revision:2}, {entries:0}]) {
    assert.throws(() => outbox.accept({...accepted(pending), ...change}), /acknowledgment/);
    assert.deepEqual(new StudioOutbox(store, "dataset").pending, pending);
  }
  new StudioOutbox(store, "dataset").accept(accepted(pending));
  assert.equal(new StudioOutbox(store, "dataset").pending, null);
});
test("Studio stale tabs cannot overwrite or clear another tab's pending action", () => {
  const store = storage(), first = new StudioOutbox(store, "dataset", () => "request");
  const stale = new StudioOutbox(store, "dataset");
  first.prepare(approval);
  assert.throws(() => stale.prepare(approval), /Another tab/);
  assert.throws(() => stale.clear(), /Another tab/);
  assert.equal(new StudioOutbox(store, "other").pending, null);
});
test("Studio storage failures preserve in-memory recovery and never pretend acknowledgment", () => {
  const store = storage(), outbox = new StudioOutbox(store, "dataset", () => "request");
  const pending = outbox.prepare(approval);
  store.removeItem = () => { throw new Error("storage denied"); };
  assert.throws(() => outbox.accept(accepted(pending)), /storage denied/);
  assert.deepEqual(outbox.pending, pending);
  const denied = new StudioOutbox({getItem:()=>null,setItem:()=>{throw new Error("quota");}}, "dataset");
  assert.throws(() => denied.prepare(approval), /quota/);
  assert.equal(denied.pending, null);
});
test("Studio smart-rule acknowledgments bind rule ID and revision", () => {
  const outbox = new StudioOutbox(storage(), "dataset", () => "request");
  const pending = outbox.prepare({endpoint:"/api/studio/rules/save", id:"rule", base_revision:2});
  const receipt = {ok:true,request_id:pending.request_id,dataset_id:"dataset",id:"rule",revision:3};
  assert.throws(() => outbox.accept({...receipt,revision:2}), /acknowledgment/);
  outbox.accept(receipt);
  assert.equal(outbox.pending, null);
});
test("Studio play receipts bind file identity and normalized UTC time", () => {
  const outbox = new StudioOutbox(storage(), "dataset", () => "request");
  const pending = outbox.prepare({endpoint:"/api/studio/history/record", file_id:"file",
    played_at:"2026-11-12T18:00:00.000Z"});
  const receipt = {ok:true,request_id:pending.request_id,dataset_id:"dataset",file_id:"file",
    played_at:"2026-11-12T18:00:00.000000Z"};
  assert.throws(() => outbox.accept({...receipt,file_id:"other"}), /acknowledgment/);
  outbox.accept(receipt);
});
test("Studio scan receipts use the existing scan acknowledgment contract", () => {
  const outbox = new StudioOutbox(storage(), "dataset", () => "request");
  const pending = outbox.prepare({endpoint:"/api/catalog/scan",path:"/selected"});
  const receipt = {...pending,ok:true,job_id:"a".repeat(32)};
  assert.throws(() => outbox.accept({...receipt,path:"/other"}), /acknowledgment/);
  outbox.accept(receipt);
});
test("History removal and undo receipts bind exact reviewed observations and operation across reload", () => {
  for (const operation of ["approve", "undo"]) {
    const store = storage(), outbox = new StudioOutbox(store, "dataset", () => "request");
    const pending = outbox.prepare({endpoint:`/api/studio/history/${operation}`, edit_id:"edit",
      event_ids:["first","second"]});
    const receipt = {ok:true,request_id:pending.request_id,dataset_id:"dataset",edit_id:"edit",
      operation,event_ids:["first","second"]};
    for (const change of [{edit_id:"other"}, {operation:"record"}, {event_ids:[]},
      {event_ids:["second","first"]}, {event_ids:["first","first"]}, {event_ids:["first"]}]) {
      assert.throws(() => outbox.accept({...receipt,...change}), /acknowledgment/);
      assert.deepEqual(new StudioOutbox(store, "dataset").pending, pending);
    }
    new StudioOutbox(store, "dataset").accept(receipt);
    assert.equal(new StudioOutbox(store, "dataset").pending, null);
  }
});
test("Studio organization receipts bind exact batch and all child identities", () => {
  const store = storage(), outbox = new StudioOutbox(store, "dataset", () => "request");
  const pending = outbox.prepare({endpoint:"/api/studio/batch/approve",batch_id:"batch"});
  const crate = {ok:true,dataset_id:"dataset",proposal_id:"proposal",crate_id:"studio-proposal",revision:1,entries:3};
  const receipt = {ok:true,request_id:pending.request_id,dataset_id:"dataset",batch_id:"batch",crates:[crate],entries:3};
  for (const change of [{batch_id:"wrong"},{entries:4},{crates:[]},{crates:[{...crate,crate_id:"wrong"}]}]) {
    assert.throws(() => outbox.accept({...receipt,...change}), /acknowledgment/);
    assert.deepEqual(new StudioOutbox(store, "dataset").pending, pending);
  }
  new StudioOutbox(store, "dataset").accept(receipt);
});
test("Studio inbox monitor exclusion feedback and model settings bind state and revision", () => {
  for (const [endpoint,identity,state,value] of [
    ["inbox/review","file_id","status","reviewed"],["monitor/save","root_id","enabled",true],
    ["exclusion/save","file_id","excluded",false],["feedback/save","file_id","rating",-1],
    ["ai/configure","model","model","qwen2.5:7b"],
  ]) {
    const store = storage(), outbox = new StudioOutbox(store, "dataset", () => "request");
    const body = {[identity]:"identity",[state]:value,base_revision:1};
    const pending = outbox.prepare({endpoint:"/api/studio/"+endpoint,...body});
    const receipt = {ok:true,request_id:pending.request_id,dataset_id:"dataset",...body,revision:2};
    for (const change of [{revision:1},{[identity]:"wrong"},{[state]:"wrong"}]) {
      assert.throws(() => outbox.accept({...receipt,...change}), /acknowledgment/);
    }
    new StudioOutbox(store, "dataset").accept(receipt);
  }
});
test("Studio conversation retry requires exact message identity and next revision", () => {
  const outbox = new StudioOutbox(storage(), "dataset", () => "request");
  const pending = outbox.prepare({endpoint:"/api/studio/assistant/chat",conversation_id:"conversation",
    base_revision:2,message:"Keep clean, lower BPM"});
  const receipt = {ok:true,request_id:pending.request_id,dataset_id:"dataset",conversation_id:"conversation",
    revision:3,turn:{message:pending.message,draft:{action:"search"}}};
  for (const change of [{revision:2},{conversation_id:"wrong"},{turn:{message:"different",draft:{action:"search"}}}]) {
    assert.throws(() => outbox.accept({...receipt,...change}), /acknowledgment/);
  }
  outbox.accept(receipt);
});
test("Studio existing-crate approve and undo survive reload with distinct matching receipts", () => {
  for (const operation of ["approve","undo"]) {
    const store=storage(), outbox=new StudioOutbox(store,"dataset",()=>"request");
    const pending=outbox.prepare({endpoint:"/api/studio/edit/"+operation,edit_id:"edit"});
    const crate={crate_id:"crate",base_revision:2,revision:3,name:"Renamed",
      entries:[{id:"membership",snapshot_id:"snapshot",song_id:"song"}]};
    const receipt={ok:true,request_id:pending.request_id,dataset_id:"dataset",edit_id:"edit",operation,crates:[crate]};
    for (const change of [{edit_id:"other"},{operation:"wrong"},{crates:[]},
      {crates:[{...crate,revision:2}]},{crates:[{...crate,entries:[{id:"incomplete"}]}]}]) {
      assert.throws(()=>outbox.accept({...receipt,...change}),/acknowledgment/);
      assert.deepEqual(new StudioOutbox(store,"dataset").pending,pending);
    }
    new StudioOutbox(store,"dataset").accept(receipt);
    assert.equal(new StudioOutbox(store,"dataset").pending,null);
  }
});
test("Studio edit receipts bind exact reviewed names and ordered memberships regardless of key order", () => {
  const outbox=new StudioOutbox(storage(),"dataset",()=>"request");
  const crate={crate_id:"crate",base_revision:1,revision:2,name:"Name",
    entries:[{id:"occurrence",snapshot_id:"snapshot",song_id:"song"}]};
  const pending=outbox.prepare({endpoint:"/api/studio/edit/approve",edit_id:"edit",expected_crates:[crate]});
  const receipt={ok:true,request_id:pending.request_id,dataset_id:"dataset",edit_id:"edit",operation:"approve",
    crates:[{name:"Name",revision:2,base_revision:1,crate_id:"crate",entries:[{song_id:"song",id:"occurrence",snapshot_id:"snapshot"}]}]};
  assert.throws(()=>outbox.accept({...receipt,crates:[{...crate,name:"Unreviewed"}]}),/acknowledgment/);
  assert.throws(()=>outbox.accept({...receipt,crates:[{...crate,entries:[{id:"different",snapshot_id:"snapshot",song_id:"song"}]}]}),/acknowledgment/);
  outbox.accept(receipt);
});
