const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {reviewRows, trackRows, seedRows, eligibleRows, acquisitionRows, prepProgress, FlowClient} = require("./setlist.js");

const row = {key:"review-1", category:"must_play",
  request:{title:"Shake It <script>", artist:"Demo Artist", version:""},
  status:"ambiguous", note:"Choose the exact file to play", fulfilled:false,
  needs_review:true, can_confirm:true, candidates:[
    {id:"off", title:"Shake It Off", artist:"Demo Artist", version_label:"Clean", path:"/Demo/Off.mp3"},
    {id:"up", title:"Shake It Up", artist:"Demo Artist", version_label:"Dirty", path:"/Demo/Up.mp3"},
  ]};

test("Prep renders review candidates separately with explicit exact-file Confirm buttons", () => {
  const html = reviewRows([row]);
  assert.match(html, /Shake It &lt;script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /Shake It Off/);
  assert.match(html, /Shake It Up/);
  assert.equal((html.match(/>Confirm</g) || []).length, 2);
  assert.match(html, /data-file-id="off"/);
  assert.match(html, /Dismiss request/);
  assert.doesNotMatch(html, />Matched</);
});

test("missing and unavailable-version rows display backend notes without fulfilled labels", () => {
  const html = reviewRows([
    {...row, status:"missing", candidates:[], note:"Not in your library"},
    {...row, status:"version_unavailable", note:"Have Clean, not Radio Edit"},
  ]);
  assert.match(html, /Not in your library/);
  assert.match(html, /Have Clean, not Radio Edit/);
  assert.doesNotMatch(html, />Matched</);
  assert.match(html, /Use Clean instead/);
  assert.match(html, /Use Dirty instead/);
});

test("matched requests show resolved metadata with success styling and preserve typed input", () => {
  const html = reviewRows([{...row, request:{title:"typed song", metadata:{first_name:"Demo"}},
    fulfilled:true, status:"matched", needs_review:false, selected_file_id:"exact"}],
  [{track:{id:"exact", title:"Resolved Song", artist:"Resolved Artist", path:"/Demo/exact.mp3"},
    version_label:"Short Edit"}]);
  for (const text of ["typed song", "Resolved Song", "Resolved Artist", "Short Edit", "/Demo/exact.mp3"]) {
    assert.ok(html.includes(text));
  }
  assert.match(html, /badge success/);
  assert.doesNotMatch(html, /badge review|data-action="confirm"/);
});

test("Prep completion distinguishes pending decisions, save, export and verification failures", () => {
  const view = {prep_done:false, saved:null, needs_review:[row], verification:{mismatches:null}};
  assert.equal(prepProgress(view), "1 decision before saving");
  assert.equal(prepProgress({...view, needs_review:[]}), "Ready to save");
  assert.equal(prepProgress({...view, needs_review:[], saved:{}}), "Crate saved · Export to finish");
  assert.equal(prepProgress({...view, needs_review:[], saved:{}, saved_current:false}),
    "Working set changed · Save again");
  assert.equal(prepProgress({...view, needs_review:[], prep_done:true}),
    "Saved · Exported · Verified: 0 mismatches");
  assert.equal(prepProgress({...view, verification:{mismatches:2}}), "Verification failed: 2 mismatches");
});

test("review actions have distinct accessible names and unavailable alternatives are explicit", () => {
  const html = reviewRows([{...row, status:"version_unavailable"}]);
  assert.match(html, /aria-label="Use Clean instead for Shake It &lt;script&gt;"/);
  assert.match(html, /aria-label="Dismiss request: Shake It &lt;script&gt;"/);
  assert.match(reviewRows([row]), /aria-label="Confirm Shake It Off by Demo Artist, Clean"/);
});

test("blocked do-not-play and already-played rows cannot be confirmed", () => {
  for (const note of ["Blocked by do-not-play", "Already played this event"]) {
    const html = reviewRows([{...row, status:"blocked", can_confirm:false, note}]);
    assert.match(html, new RegExp(note));
    assert.doesNotMatch(html, />Confirm</);
  }
});

test("Prep and Live display backend order exact files versions and reasons without ranking", () => {
  const items = [{track:{id:"dirty", title:"Summer (Dirty)", artist:"Demo Artist", path:"/Demo/Dirty.mp3"},
    version_label:"Dirty", reasons:["Live guest request", "Song cue fits first dance"]},
    {track:{id:"orbit", title:"Slow Orbit", artist:"Maya Vale", path:"/Demo/Short.mp3"},
      version_label:"Short Edit", reasons:["Must-play request"]}];
  const html = trackRows(items, true);
  assert.ok(html.indexOf("Summer") < html.indexOf("Slow Orbit"));
  for (const text of ["Dirty", "/Demo/Dirty.mp3", "Live guest request", "Short Edit", "Must-play request"]) {
    assert.ok(html.includes(text));
  }
  assert.equal((html.match(/Mark played/g) || []).length, 2);
});

test("guest form asks only first name optional email and song, with no account step", () => {
  const html = fs.readFileSync(path.join(__dirname, "setlist.html"), "utf8");
  const form = html.match(/<form id="guest-form">([\s\S]*?)<\/form>/)[1];
  assert.deepEqual([...form.matchAll(/name="([^"]+)"/g)].map(match => match[1]),
    ["first_name", "email", "song"]);
  assert.match(form, /id="guest-name"[^>]*required/);
  assert.match(form, /id="guest-song"[^>]*required/);
  assert.doesNotMatch(form, /id="guest-email"[^>]*required/);
  assert.doesNotMatch(form, /password|sign.?up|sign.?in/i);
});

test("FlowClient calls shared-engine endpoints and retains identical requests on lost acknowledgment", async () => {
  let lose = true;
  const sent = [];
  const client = new FlowClient(async (url, options) => {
    if (url === "/api/session") return {ok:true, json:async () => ({token:"synthetic"})};
    if (url === "/api/live") return {ok:true, json:async () => ({mode:"live", revision:7})};
    assert.equal(url, "/api/command");
    assert.equal(options.headers["X-Setlist-Token"], "synthetic");
    sent.push(options.body);
    if (lose) {lose = false; throw new Error("Synthetic lost acknowledgment");}
    return {ok:true, json:async () => ({mode:"live", revision:8})};
  });
  await client.load("live");
  await assert.rejects(client.command("live", {action:"guest", first_name:"Demo", song:"Summer (Dirty)"}),
    /lost acknowledgment/);
  const pending = client.pending;
  await assert.rejects(client.command("live", {action:"guest", first_name:"Demo", song:"Other"}),
    /pending action/);
  await client.retry();
  assert.equal(sent[0], sent[1]);
  assert.equal(JSON.parse(sent[0]).base_revision, 7);
  assert.ok(pending.request_id);
  assert.equal(client.pending, null);
});

test("HTTP errors never look like saved or fulfilled success", async () => {
  const client = new FlowClient(async () => ({ok:false, status:409,
    json:async () => ({error:"Session changed. Reload before trying again."})}));
  client.token = "synthetic";
  client.views.prep = {revision:0};
  await assert.rejects(client.command("prep", {action:"save"}), /Session changed/);
  assert.ok(client.pending);
  assert.equal(client.views.prep.revision, 0);
});

test("imported memberships preserve duplicates and expose explicit occurrence-level edits", () => {
  const rows = [
    {id:"first", title:"Same <song>", artist:"Demo", path:"/Demo/same.mp3", status:"available"},
    {id:"repeat", title:"Same <song>", artist:"Demo", path:"/Demo/same.mp3", status:"available"},
    {id:"missing", path:"/Demo/missing.mp3", status:"missing_metadata"},
  ];
  assert.doesNotMatch(seedRows(rows), /data-action=/);
  const html = seedRows(rows, true);
  assert.equal((html.match(/Same &lt;song&gt;/g) || []).length, 2);
  assert.match(html, /data-entry-id="first"/);
  assert.match(html, /data-entry-id="repeat"/);
  assert.match(html, /missing metadata/);
  assert.match(html, /Move up: entry 2/);
  assert.match(html, /Remove from set: entry 3/);
  assert.equal(prepProgress({needs_review:[], verification:{mismatches:null}, seed_rows:rows}),
    "Resolve imported entries before saving");
});

test("eligible browser filters display metadata without resolving or reranking requests", () => {
  const items = [
    {track:{id:"b", title:"Beta <Demo>", artist:"Artist B", path:"/Demo/b.mp3"},
      version_label:"Dirty", reasons:["Must-play request"]},
    {track:{id:"a", title:"Alpha", artist:"Artist A", path:"/Demo/a.mp3"},
      version_label:"Clean", reasons:[]},
  ];
  const before = JSON.stringify(items);
  const all = eligibleRows(items);
  assert.equal(all.count, 2);
  assert.ok(all.html.indexOf("Beta &lt;Demo&gt;") < all.html.indexOf("Alpha"));
  assert.match(all.html, /disabled data-unavailable/);
  assert.match(all.html, /Exact file/);
  assert.match(all.html, /Must-play request/);
  for (const query of [" DIRTY ", "Artist B", "beta"]) {
    const result = eligibleRows(items, query, {order:[]});
    assert.equal(result.count, 1);
    assert.match(result.html, /data-file-id="b"/);
    assert.doesNotMatch(result.html, /disabled|Alpha/);
  }
  const added = eligibleRows(items, "", {order:["added:b"]});
  assert.match(added.html, /Added to set: Beta &lt;Demo&gt;/);
  assert.equal((added.html.match(/data-unavailable/g) || []).length, 1);
  const missing = eligibleRows(items, "Septemer");
  assert.equal(missing.count, 0);
  assert.match(missing.html, /does not resolve requests/);
  assert.equal(JSON.stringify(items), before);
});

test("Prep workspace has real source set and decision jump destinations and keeps saved order visible", () => {
  const html = fs.readFileSync(path.join(__dirname, "setlist.html"), "utf8");
  for (const id of ["prep-sources", "prep-set", "prep-decisions"]) {
    assert.match(html, new RegExp(`href="#${id}"`));
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(html, /id="eligible-search"[^>]*aria-controls="eligible-tracks"/);
  assert.match(html, /<details id="seed-workspace"/);
  assert.ok(html.indexOf('id="prep-tracks"') < html.indexOf('id="seed-memberships"'));
});

test("acquisition progress stays explicitly separate from playable fulfillment", () => {
  const rows = [{key:"missing", title:"Missing <Demo>", artist:"Artist", version:"Radio Edit",
    query:"Artist Missing", progress:"acquired", providers:[]}];
  const before = JSON.stringify(rows);
  const html = acquisitionRows(rows);
  assert.match(html, /Missing &lt;Demo&gt;/);
  assert.match(html, /Acquired externally; not verified/);
  assert.match(html, /data-progress="acquired"\s+aria-pressed="true"/);
  assert.match(html, /Still unresolved/);
  assert.doesNotMatch(html, /data-action="confirm"|class="badge success"/);
  assert.equal(JSON.stringify(rows), before);
  assert.match(acquisitionRows([]), /No missing-song lookups/);
});

test("imported recovery shows original and replacement paths and explicit reconnect", () => {
  const row = {id:"source:0", title:"Orbit <Demo>", artist:"Demo Band",
    path:"/Demo/Original.mp3", status:"missing_library_file", reconnect_status:"matched",
    reconnect_candidates:[{id:"exact",title:"Orbit <Demo>",artist:"Demo Band",
      version_label:"Radio Edit",path:"/Demo/Acquired.mp3"}]};
  const before = JSON.stringify(row);
  const html = seedRows([row], true);
  assert.match(html, /Original\.mp3/);
  assert.match(html, /Acquired\.mp3/);
  assert.match(html, /data-action="seed_reconnect"/);
  assert.match(html, /Reconnect file for entry 1: Orbit &lt;Demo&gt;/);
  assert.doesNotMatch(seedRows([row]), /data-action="seed_reconnect"/);
  const recovered = seedRows([{...row,status:"available",replacement:row.reconnect_candidates[0]}],true);
  assert.match(recovered, /Original reference retained/);
  assert.doesNotMatch(recovered, /data-action="seed_reconnect"/);
  assert.equal(JSON.stringify(row), before);
});
