const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function editor(api) {
  const source = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
  const wave = {hash: "first", loadId: 1, duration: 100, viewStart: 10, viewSpan: 2};
  const timers = new Map();
  const errors = [];
  let nextTimer = 0, draws = 0;
  const context = vm.createContext({
    wave, api, AbortController, Math, Number,
    $: () => ({open: true}),
    toast: message => errors.push(message),
    drawWave: () => draws++,
    setTimeout: fn => {timers.set(++nextTimer, fn); return nextTimer;},
    clearTimeout: id => timers.delete(id),
  });
  // Exercise the browser's actual cache loading/render-selection functions without a DOM.
  vm.runInContext(source.slice(source.indexOf("async function loadWaveData("),
                               source.indexOf("function sizeCanvas(")), context);
  return {context, wave, errors, timers, get draws() {return draws;}};
}

test("opening uses cached waveform JSON, not browser audio decoding", async () => {
  const requests = [];
  const overview = {duration: 100, start: 0, hop_s: 1, columns: [[-0.2, 0.4, 0.1, 0.2, 0.3]]};
  const fixture = editor(async url => {requests.push(url); return overview;});
  const loaded = await fixture.context.loadWaveData({hash: "first", url: "/audio/first"}, new AbortController().signal);
  assert.equal(loaded.duration, 100);
  assert.deepEqual(requests, ["/api/waveform/first?detail=1"]);
  Object.assign(fixture.wave, loaded);
  await fixture.context.loadWaveData({hash: "first"}, new AbortController().signal);
  assert.equal(requests.length, 1);
});

test("zoom requests are debounced, bounded and cannot overwrite another track", async () => {
  let resolve, request, signal;
  const fixture = editor((url, options) => {request = url; signal = options.signal; return new Promise(done => {resolve = done;});});
  fixture.context.refineWave(5000);
  fixture.context.refineWave(5000);
  assert.equal(fixture.timers.size, 1);
  const pending = [...fixture.timers.values()][0]();
  assert.match(request, /start=10&end=12&points=4096$/);
  assert.equal(signal.aborted, false);
  fixture.wave.hash = "second"; fixture.wave.loadId++;
  resolve({columns: ["stale"]});
  await pending;
  assert.equal(fixture.wave.detail, undefined);
  assert.equal(fixture.draws, 0);
});

test("new viewport cancels old refinement and only current errors are shown", async () => {
  let reject, signal;
  const fixture = editor((url, options) => {signal = options.signal; return new Promise((_, fail) => {reject = fail;});});
  fixture.context.refineWave(800);
  const old = [...fixture.timers.values()][0]();
  fixture.wave.viewStart = 20;
  fixture.context.refineWave(800);
  assert.equal(signal.aborted, true);
  reject(new Error("stale failure"));
  await old;
  assert.deepEqual(fixture.errors, []);
  const current = [...fixture.timers.values()].at(-1)();
  reject(new Error("cache unavailable"));
  await current;
  assert.match(fixture.errors[0], /cache unavailable/);
});

test("overview colours and amplitude use overview data, never a partial zoom", () => {
  const fixture = editor(async () => {});
  fixture.wave.overview = {start: 0, hop_s: 1, columns: Array.from({length: 4}, () => [-0.2, 0.3, 0.2, 0.4, 0.6])};
  fixture.wave.detail = {start: 1, hop_s: 0.5, columns: [[-0.8, 0.9, 0.8, 0.7, 0.6], [-0.6, 0.7, 0.4, 0.3, 0.2]]};
  assert.equal(fixture.context.waveBands(1, 1.5)[1], 0.9);
  assert.equal(fixture.context.waveBands(1, 1.5, true)[1], 0.3);
  assert.equal(fixture.context.waveBands(0, 1)[1], 0.3);
  assert.equal(fixture.context.waveBands(3.9, 4)[1], 0.3);
});
