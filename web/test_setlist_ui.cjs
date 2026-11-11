const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {reviewRows, trackRows, FlowClient} = require("./setlist.js");

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
