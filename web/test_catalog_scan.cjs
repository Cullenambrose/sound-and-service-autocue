const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const {MatchOutbox, StageOutbox, ScanOutbox} = require("./catalog-state.js");

const source = fs.readFileSync(path.join(__dirname, "catalog.js"), "utf8");
const settle = () => new Promise(resolve => setImmediate(resolve));
function fixture() {
  const values = new Map(), requests = [], receipts = new Map();
  const storage = {
    getItem:key => values.get(key) ?? null,
    setItem:(key, value) => values.set(key, value),
    removeItem:key => values.delete(key),
  };
  let jobs = [], roots = [], loseAck = false, failPoll = false, nextId = 0;
  const confirmations = [];
  const job = (status = "running") => ({id:"a".repeat(32), path:"/synthetic/music", status,
    progress:{phase:"indexing", scanned:1, reused:0, errors:0, skipped:0, current_path:"Clean.wav"},
    error:"", result:status === "completed" ? {scanned:1, reused:0, errors:[], skipped_symlinks:[]} : null});
  async function api(url, options) {
    requests.push(url);
    if (url === "/api/session") return {};
    if (url === "/api/catalog/snapshots") return {snapshots:[]};
    if (url.startsWith("/api/catalog/crates?")) return {crates:[]};
    if (url === "/api/catalog/roots") return {roots};
    if (url.startsWith("/api/catalog/files?")) return {files:[], total:0};
    if (url === "/api/catalog/scans") {
      if (failPoll) throw new Error("synthetic disconnected status");
      return {jobs:structuredClone(jobs)};
    }
    if (url === "/api/catalog/scan") {
      const body = JSON.parse(options.body);
      if (!receipts.has(body.request_id)) {
        receipts.set(body.request_id, {...body, ok:true, job_id:"a".repeat(32)});
        jobs = [job("queued")];
      }
      if (loseAck) { loseAck = false; throw new Error("synthetic lost admission acknowledgment"); }
      return receipts.get(body.request_id);
    }
    if (url === "/api/catalog/scans/cancel") {
      jobs[0].status = jobs[0].status === "queued" ? "cancelled" : "cancelling";
      return {ok:true, job:jobs[0]};
    }
    throw new Error("Unexpected request: " + url);
  }
  async function open() {
    const elements = new Map(), timers = [];
    function $(selector) {
      if (!elements.has(selector)) elements.set(selector, {textContent:"", innerHTML:"", value:"", hidden:false,
        classList:{toggle(){}, add(){}, remove(){}}, setAttribute(){}, replaceChildren(){}});
      return elements.get(selector);
    }
    const cancelButton = {dataset:{catalogScanCancel:"a".repeat(32)}};
    const reassociateButton = {dataset:{catalogReassociate:"offline-root"}};
    const context = vm.createContext({
      $, $$:selector => {
        if (selector === "[data-catalog-scan-cancel]" && /data-catalog-scan-cancel/.test($("#catalog-scans").innerHTML)) return [cancelButton];
        if (selector === "[data-catalog-reassociate]" && /data-catalog-reassociate/.test($("#catalog-roots").innerHTML)) return [reassociateButton];
        return [];
      },
      api, datasetId:"dataset", localStorage:storage, MatchOutbox, StageOutbox, ScanOutbox,
      crypto:{randomUUID:() => `request-${++nextId}`}, confirm:message => {confirmations.push(message); return true;},
      escapeHtml:value => String(value).replaceAll("<", "&lt;"),
      setTimeout:fn => {timers.push(fn); return timers.length;},
    });
    vm.runInContext(source, context);
    await settle();
    return {$, timers, cancelButton, reassociateButton};
  }
  return {open, requests, receipts, storage, job, confirmations,
    set roots(value) {roots = value;},
    set jobs(value) {jobs = value;}, get jobs() {return jobs;},
    set loseAck(value) {loseAck = value;}, set failPoll(value) {failPoll = value;}};
}

test("catalog scan admission does not block actions and lost acknowledgment survives reload", async () => {
  const app = fixture(), first = await app.open();
  app.loseAck = true;
  first.$("#catalog-folder").value = "/synthetic/music";
  await first.$("#catalog-scan").onclick();
  assert.match(first.$("#catalog-status").textContent, /lost admission acknowledgment/);
  assert.equal(first.$("#catalog-scan-pending").hidden, false);
  assert.equal(app.receipts.size, 1);
  const reopened = await app.open();
  assert.equal(reopened.$("#catalog-scan-pending").hidden, false);
  await reopened.$("#catalog-scan-retry").onclick();
  assert.equal(app.receipts.size, 1);
  assert.equal(reopened.$("#catalog-scan-pending").hidden, true);
  assert.match(reopened.$("#catalog-scans").innerHTML, /queued/);
});

test("reload discovers a completed scan and refreshes the indexed view immediately", async () => {
  const app = fixture();
  app.jobs = [app.job("completed")];
  const ui = await app.open();
  assert.match(ui.$("#catalog-scans").innerHTML, /completed.*1 audio paths/);
  assert.ok(app.requests.includes("/api/catalog/files?offset=0"));
  const before = app.requests.filter(url => url === "/api/catalog/files?offset=0").length;
  await ui.timers.shift()();
  assert.equal(app.requests.filter(url => url === "/api/catalog/files?offset=0").length, before);
});

test("background scan progress and cancellation remain visible without cancelling browser polling", async () => {
  const app = fixture();
  app.jobs = [app.job()];
  const ui = await app.open();
  assert.match(ui.$("#catalog-scans").innerHTML, /indexing.*Clean.wav/);
  await ui.cancelButton.onclick();
  assert.equal(app.jobs[0].status, "cancelling");
  assert.match(ui.$("#catalog-status").textContent, /acknowledged: cancelling/);
  app.jobs[0].status = "cancelled";
  app.jobs[0].error = "Scan cancelled; prior index preserved.";
  await ui.timers.shift()();
  assert.match(ui.$("#catalog-scans").innerHTML, /cancelled; prior index preserved/);
  assert.ok(app.requests.includes("/api/catalog/files?offset=0"));
});

test("poll failures are explicit and retry automatically instead of implying completion", async () => {
  const app = fixture();
  app.failPoll = true;
  const ui = await app.open();
  assert.match(ui.$("#catalog-scan-status").textContent, /unavailable.*disconnected.*Retrying/);
  app.failPoll = false;
  app.jobs = [app.job()];
  await ui.timers.shift()();
  assert.match(ui.$("#catalog-scan-status").textContent, /background/);
  assert.equal(ui.timers.length, 1);
});

test("discarding pending scan admission does not cancel the admitted server job", async () => {
  const app = fixture(), ui = await app.open();
  app.loseAck = true;
  ui.$("#catalog-folder").value = "/synthetic/music";
  await ui.$("#catalog-scan").onclick();
  await ui.$("#catalog-scan-discard").onclick();
  assert.equal(ui.$("#catalog-scan-pending").hidden, true);
  assert.equal(app.jobs[0].status, "queued");
  assert.ok(!app.requests.includes("/api/catalog/scans/cancel"));
});
test("offline root reassociation requires deliberate confirmation and persists root revision on retry", async () => {
  const app = fixture();
  app.roots = [{id:"offline-root", path:"/old/music", state:"offline", scan_revision:3}];
  const ui = await app.open();
  assert.match(ui.$("#catalog-roots").innerHTML, /Verify and reassociate/);
  ui.$('[data-catalog-replacement="offline-root"]').value = "/new/music";
  app.loseAck = true;
  await ui.reassociateButton.onclick();
  assert.match(app.confirmations[0], /Every previously available.*identical bytes/);
  assert.match(app.confirmations[0], /write links are NOT moved/);
  const pending = new ScanOutbox(app.storage, "dataset").pending;
  assert.equal(pending.root_id, "offline-root");
  assert.equal(pending.base_revision, 3);
  assert.equal(pending.path, "/new/music");
  const restored = await app.open();
  await restored.$("#catalog-scan-retry").onclick();
  assert.equal(app.receipts.size, 1);
  assert.equal(new ScanOutbox(app.storage, "dataset").pending, null);
});
test("online roots never offer reassociation controls", async () => {
  const app = fixture();
  app.roots = [{id:"online-root", path:"/music", state:"online", scan_revision:3}];
  const ui = await app.open();
  assert.doesNotMatch(ui.$("#catalog-roots").innerHTML, /data-catalog-reassociate/);
});
