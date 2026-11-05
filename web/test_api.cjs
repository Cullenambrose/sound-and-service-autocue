const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
function apiContext(fetch) {
  const context = vm.createContext({fetch});
  vm.runInContext("let sessionToken, sessionPromise, datasetId;\n" +
    source.slice(source.indexOf("async function api("), source.indexOf("function toast(")), context);
  return context.api;
}
const response = (data, ok = true) => ({ok, headers:{get:() => "application/json"}, json:async () => data});
test("parallel startup requests share one session handshake", async () => {
  let sessions = 0;
  const api = apiContext(async (url, options) => {
    if (url === "/api/session") {
      sessions++;
      await new Promise(resolve => setTimeout(resolve, 5));
      return response({token:"local-token", dataset_id:"dataset"});
    }
    assert.equal(options.headers["X-AutoCue-Token"], "local-token");
    return response({ok:true});
  });
  await Promise.all(Array.from({length:8}, () => api("/api/catalog/snapshots")));
  assert.equal(sessions, 1);
});
test("failed handshake is visible and a later request can retry", async () => {
  let sessions = 0;
  const api = apiContext(async url => {
    if (url === "/api/session") {
      sessions++;
      return sessions === 1 ? response({error:"synthetic unavailable"}, false) :
        response({token:"local-token", dataset_id:"dataset"});
    }
    return response({ok:true});
  });
  const failures = await Promise.allSettled([api("/one"), api("/two")]);
  assert.ok(failures.every(result => result.status === "rejected" && /unavailable/.test(result.reason.message)));
  assert.equal(sessions, 1);
  assert.equal((await api("/retry")).ok, true);
  assert.equal(sessions, 2);
});
