const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const {PersonalOutbox} = require("./catalog-state.js");
const fields = {crate_id:"crate", base_revision:0, name:"Warmup",
  entries:[{id:"entry-1", snapshot_id:"snapshot", song_id:"song"}]};
const receipt = pending => ({...pending, ok:true, revision:pending.base_revision + 1});
function storage() {
  const values = new Map();
  return {getItem:key => values.get(key) ?? null, setItem:(key, value) => values.set(key, value),
    removeItem:key => values.delete(key)};
}
test("personal crate outbox validates complete ordered membership receipt before clearing", () => {
  const store = storage(), outbox = new PersonalOutbox(store, "dataset", () => "request");
  const pending = outbox.prepare({...fields, entries:[...fields.entries, {...fields.entries[0], id:"entry-2"}]});
  for (const invalid of [{ok:true}, {...receipt(pending), name:"wrong"}, {...receipt(pending), revision:3},
    {...receipt(pending), crate_id:"other"}, {...receipt(pending), dataset_id:"other"},
    {...receipt(pending), entries:[...pending.entries].reverse()},
    {...receipt(pending), entries:[{...pending.entries[0], song_id:"wrong"}, pending.entries[1]]}]) {
    assert.throws(() => outbox.accept(invalid), /acknowledgment/);
    assert.deepEqual(new PersonalOutbox(store, "dataset").pending, pending);
  }
  new PersonalOutbox(store, "dataset").accept(receipt(pending));
  assert.equal(new PersonalOutbox(store, "dataset").pending, null);
});
test("personal outbox storage failures, dataset isolation and stale tabs retain recovery", () => {
  const store = storage(), first = new PersonalOutbox(store, "dataset", () => "request");
  const stale = new PersonalOutbox(store, "dataset");
  const pending = first.prepare(fields);
  assert.throws(() => stale.prepare(fields), /Another tab/);
  assert.throws(() => stale.clear(), /Another tab/);
  assert.equal(new PersonalOutbox(store, "other").pending, null);
  store.removeItem = () => {throw new Error("storage denied");};
  assert.throws(() => first.accept(receipt(pending)), /storage denied/);
  assert.deepEqual(new PersonalOutbox(store, "dataset").pending, pending);
});

function fixture() {
  const store = storage(), crates = new Map(), receipts = new Map();
  let loseAck = false, loseExportAck = false, conflict = false, failDetail = false, failExport = false, failHistory = false, counter = 0;
  const history = [];
  const downloads = [];
  const source = fs.readFileSync(path.join(__dirname, "personal.js"), "utf8");
  const enrich = value => ({...value, entries:value.entries.map(entry => ({...entry,
    song:{artist:"Invented", title:entry.song_id, version:"Clean"}, match_state:"unreviewed", decision_revision:0}))});
  async function api(url, options) {
    if (url.startsWith("/api/catalog/personal/exports?")) {
      if (failHistory) throw new Error("Synthetic history unavailable");
      const offset = Number(new URLSearchParams(url.split("?")[1]).get("offset"));
      return {exports:history.slice(offset, offset + 20), offset, total:history.length};
    }
    if (url === "/api/catalog/personal/export/preview") {
      if (failExport) throw new Error("Synthetic export unavailable");
      const request = JSON.parse(options.body), crate = crates.get(request.crate_id);
      return {preview_id:"proof", manifest:{requested:crate.entries.length, eligible:crate.entries.length,
        blocked:0, ready:crate.entries.length > 0, blockers:crate.entries.length ? [] : ["Empty crate"],
        entries:crate.entries.map((entry, index) => ({...entry, position:index + 1,
          song:{artist:"Invented", title:entry.song_id}, eligible:true, path:"/synthetic.wav"}))}};
    }
    if (url === "/api/catalog/personal/export/download") {
      if (failExport) throw new Error("Synthetic export stale");
      if (!history.length) history.push({state:"historical", filename:"proof.zip", export_id:"proof",
        crate:{name:"Saved synthetic", revision:1}, entries:1, saved_at:100, size_bytes:200});
      if (loseExportAck) {loseExportAck = false; throw new Error("Synthetic lost export acknowledgment after publication");}
      return {preview_id:"proof", manifest:{ready:true}, filename:"proof.zip", bundle_base64:"emlw"};
    }
    if (url === "/api/session") return {};
    if (url === "/api/catalog/personal") return {crates:[...crates.values()].map(crate => ({...crate, entries:crate.entries.length}))};
    if (url.startsWith("/api/catalog/personal/detail?")) {
      if (failDetail) throw new Error("Synthetic detail unavailable");
      return enrich(structuredClone(crates.get(new URLSearchParams(url.split("?")[1]).get("crate_id"))));
    }
    if (url === "/api/catalog/personal/save") {
      const body = JSON.parse(options.body);
      if (conflict) throw new Error("Synthetic revision conflict");
      if (!receipts.has(body.request_id)) {
        const reply = receipt(body);
        receipts.set(body.request_id, reply);
        crates.set(body.crate_id, {id:body.crate_id, name:body.name, revision:reply.revision, entries:body.entries});
      }
      if (loseAck) {loseAck = false; throw new Error("Lost personal crate acknowledgment after commit");}
      return receipts.get(body.request_id);
    }
    throw new Error("Unexpected request: " + url);
  }
  async function open() {
    const elements = new Map(), buttons = new Map();
    const $ = selector => {
      if (!elements.has(selector)) elements.set(selector, {value:"", textContent:"", innerHTML:"", hidden:false,
        classList:{toggle(){}}, setAttribute(){}});
      return elements.get(selector);
    };
    const $$ = selector => {
      const kind = /data-personal-(up|down|remove)/.exec(selector)?.[1];
      if (!kind) return [];
      const rows = [...$("#personal-entries").innerHTML.matchAll(new RegExp(`data-personal-${kind}="(\\d+)"`, "g"))];
      return rows.map(match => {
        const key = `${kind}-${match[1]}`;
        if (!buttons.has(key)) buttons.set(key, {dataset:{[{up:"personalUp", down:"personalDown", remove:"personalRemove"}[kind]]:match[1]}});
        return buttons.get(key);
      });
    };
    const context = vm.createContext({$, $$, api, PersonalOutbox, datasetId:"dataset", localStorage:store,
      crypto:{randomUUID:() => `generated-${++counter}`}, escapeHtml:value => String(value),
      confirm:() => true, atob:value => Buffer.from(value, "base64").toString("binary"),
      Uint8Array, Blob, URLSearchParams, URL:{createObjectURL:() => "blob:synthetic", revokeObjectURL(){}},
      document:{createElement:() => {const link = {click:() => downloads.push(link.download)}; return link;}},
      setTimeout:run => run()});
    vm.runInContext(source, context);
    await new Promise(resolve => setImmediate(resolve));
    return {$, context, buttons};
  }
  return {open, store, crates, receipts, downloads, history, set failHistory(value) {failHistory = value;},
    set failExport(value) {failExport = value;},
    set loseExportAck(value) {loseExportAck = value;},
    set loseAck(value) {loseAck = value;},
    set failDetail(value) {failDetail = value;}, set conflict(value) {conflict = value;}};
}
test("actual personal controller recovers lost create acknowledgment after reload without duplicating crate", async () => {
  const app = fixture(), ui = await app.open();
  app.loseAck = true;
  ui.$("#personal-new-name").value = "Warmup";
  await ui.$("#personal-create").onclick();
  assert.match(ui.$("#personal-status").textContent, /Lost personal crate acknowledgment/);
  assert.equal(ui.$("#personal-pending").hidden, false);
  const restored = await app.open();
  await restored.$("#personal-retry").onclick();
  assert.equal(app.crates.size, 1);
  assert.equal(app.receipts.size, 1);
  assert.equal(restored.$("#personal-pending").hidden, true);
});
test("actual controller adds repeated songs, reorders, renames and removes only selected membership", async () => {
  const app = fixture(), ui = await app.open();
  ui.$("#personal-new-name").value = "Warmup";
  await ui.$("#personal-create").onclick();
  await ui.context.PersonalCrates.add("snapshot", "first");
  await ui.context.PersonalCrates.add("snapshot", "first");
  const ids = [...app.crates.values()][0].entries.map(entry => entry.id);
  assert.notEqual(ids[0], ids[1]);
  await ui.buttons.get("up-1").onclick();
  assert.deepEqual([...app.crates.values()][0].entries.map(entry => entry.id), [...ids].reverse());
  ui.$("#personal-name").value = "Renamed";
  await ui.$("#personal-rename").onclick();
  await ui.buttons.get("remove-0").onclick();
  const current = [...app.crates.values()][0];
  assert.equal(current.name, "Renamed");
  assert.deepEqual(current.entries.map(entry => entry.id), [ids[0]]);
  assert.equal(current.revision, 6);
});
test("conflict retains pending full crate change and prevents subsequent additions until reconciliation", async () => {
  const app = fixture(), ui = await app.open();
  ui.$("#personal-new-name").value = "Warmup";
  await ui.$("#personal-create").onclick();
  app.conflict = true;
  await assert.rejects(ui.context.PersonalCrates.add("snapshot", "song"), /revision conflict/);
  const pending = new PersonalOutbox(app.store, "dataset").pending;
  await assert.rejects(ui.context.PersonalCrates.add("snapshot", "other"), /pending personal crate/);
  assert.deepEqual(new PersonalOutbox(app.store, "dataset").pending, pending);
  app.conflict = false;
  await ui.$("#personal-discard").onclick();
  assert.equal(new PersonalOutbox(app.store, "dataset").pending, null);
  assert.equal([...app.crates.values()][0].entries.length, 0);
});
test("failed refresh cannot leave an older crate editable under a newly selected name", async () => {
  const app = fixture(), ui = await app.open();
  ui.$("#personal-new-name").value = "First crate";
  await ui.$("#personal-create").onclick();
  const before = app.receipts.size;
  app.failDetail = true;
  await ui.$("#personal-refresh").onclick();
  assert.match(ui.$("#personal-status").textContent, /detail unavailable/);
  await assert.rejects(ui.context.PersonalCrates.add("snapshot", "song"), /Choose or create/);
  assert.equal(app.receipts.size, before);
});
test("catalog filtering never hides or dereferences personal entries sharing the card style", () => {
  const source = fs.readFileSync(path.join(__dirname, "catalog.js"), "utf8");
  const catalogCard = {dataset:{catalogSearch:"invented artist"}, hidden:false};
  const personalCard = {dataset:{}, hidden:false};
  const context = vm.createContext({
    $:() => ({value:"different"}),
    $$:selector => selector === "#catalog-songs .catalog-song" ? [catalogCard] : [catalogCard, personalCard],
  });
  vm.runInContext(source.slice(source.indexOf("  function filterSongs()"), source.indexOf("  async function sendPending()")), context);
  context.filterSongs();
  assert.equal(catalogCard.hidden, true);
  assert.equal(personalCard.hidden, false);
});
test("export controller requires inspected preview and clears it after edits or failed revalidation", async () => {
  const app = fixture(), ui = await app.open();
  ui.$("#personal-new-name").value = "Export set";
  await ui.$("#personal-create").onclick();
  await ui.$("#personal-export-preview").onclick();
  assert.equal(ui.$("#personal-export-download").disabled, true);
  assert.match(ui.$("#personal-export-status").textContent, /Empty/);
  await ui.context.PersonalCrates.add("snapshot", "first");
  await ui.$("#personal-export-preview").onclick();
  assert.equal(ui.$("#personal-export-download").disabled, false);
  await ui.$("#personal-export-download").onclick();
  assert.deepEqual(app.downloads, ["proof.zip"]);
  assert.equal(ui.$("#personal-export-download").disabled, true);
  await ui.$("#personal-export-download").onclick();
  assert.match(ui.$("#personal-status").textContent, /preview first/);
  await ui.$("#personal-export-preview").onclick();
  app.failExport = true;
  await ui.$("#personal-export-download").onclick();
  assert.deepEqual(app.downloads, ["proof.zip"]);
  assert.match(ui.$("#personal-export-status").textContent, /Export failed/);
  app.failExport = false;
  await ui.$("#personal-export-preview").onclick();
  await ui.context.PersonalCrates.add("snapshot", "second");
  assert.equal(ui.$("#personal-export-download").disabled, true);
  app.failExport = true;
  await ui.$("#personal-export-preview").onclick();
  assert.equal(ui.$("#personal-export-download").disabled, true);
  assert.match(ui.$("#personal-export-status").textContent, /Verification failed/);
});
test("saved export controller pages historical recovery links without needing a selected crate", async () => {
  const app = fixture();
  for (let index = 0; index < 21; index++)
    app.history.push({state:"historical", filename:`proof-${index}.zip`, export_id:`proof-${index}`,
      crate:{name:`Historical ${index}`, revision:1}, entries:2, saved_at:100, size_bytes:200});
  const ui = await app.open();
  assert.match(ui.$("#personal-exports").innerHTML, /Historical 0/);
  assert.doesNotMatch(ui.$("#personal-exports").innerHTML, /Historical 20/);
  assert.match(ui.$("#personal-exports").innerHTML, /dataset_id=dataset/);
  assert.match(ui.$("#personal-exports").innerHTML, /Download historical ZIP/);
  assert.match(ui.$("#personal-exports").innerHTML, /NOT been verified/);
  assert.equal(ui.$("#personal-exports-previous").disabled, true);
  assert.equal(ui.$("#personal-exports-next").disabled, false);
  await ui.$("#personal-exports-next").onclick();
  assert.match(ui.$("#personal-exports").innerHTML, /Historical 20/);
  assert.equal(ui.$("#personal-exports-next").disabled, true);
  await ui.$("#personal-exports-previous").onclick();
  assert.match(ui.$("#personal-exports").innerHTML, /Historical 0/);
  app.history[0] = {state:"unavailable", filename:"bad.zip", error:"Synthetic corrupt ZIP"};
  await ui.$("#personal-exports-refresh").onclick();
  assert.match(ui.$("#personal-exports").innerHTML, /Synthetic corrupt ZIP/);
  assert.doesNotMatch(ui.$("#personal-exports").innerHTML, /download="bad.zip"/);
  app.failHistory = true;
  await ui.$("#personal-exports-refresh").onclick();
  assert.equal(ui.$("#personal-exports").innerHTML, "");
  assert.equal(ui.$("#personal-exports-next").disabled, true);
  assert.match(ui.$("#personal-exports-status").textContent, /history unavailable/);
});
test("history loading failure does not disable independent personal crate operations", async () => {
  const app = fixture();
  app.failHistory = true;
  const ui = await app.open();
  ui.$("#personal-new-name").value = "Still usable";
  await ui.$("#personal-create").onclick();
  await ui.context.PersonalCrates.add("snapshot", "song");
  assert.equal([...app.crates.values()][0].entries.length, 1);
});
test("lost publication acknowledgment is recoverable from saved history after reload", async () => {
  const app = fixture(), ui = await app.open();
  ui.$("#personal-new-name").value = "Recovery";
  await ui.$("#personal-create").onclick();
  await ui.context.PersonalCrates.add("snapshot", "song");
  await ui.$("#personal-export-preview").onclick();
  app.loseExportAck = true;
  await ui.$("#personal-export-download").onclick();
  assert.match(ui.$("#personal-status").textContent, /lost export acknowledgment/);
  assert.equal(app.downloads.length, 0);
  const restored = await app.open();
  assert.match(restored.$("#personal-exports").innerHTML, /Saved synthetic/);
  assert.match(restored.$("#personal-exports").innerHTML, /Download historical ZIP/);
  assert.equal(app.history.length, 1);
});
