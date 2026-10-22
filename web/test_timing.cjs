const assert = require("node:assert/strict");
const test = require("node:test");
const timing = require("./timing.js");
const cases = require("../engine/timing_cases.json");

test("browser conversions match engine cases including offsets and fractional bars", () => {
  for (const {grid, bar, time_s} of cases) {
    assert.ok(Math.abs(timing.barToTime(bar, grid) - time_s) < 1e-9);
    assert.ok(Math.abs(timing.timeToBar(time_s, grid) - bar) < 1e-9);
  }
});

test("invalid grids and positions cannot silently produce timestamps", () => {
  for (const grid of [{}, {t1: null, bar_s: 2}, {t1: 0, bar_s: 0}, {t1: 0, bar_s: -2},
    {t1: Infinity, bar_s: 2}, {t1: 0, bar_s: 2, offset_ms: NaN},
    {t1: 0, bar_s: 2, offset_ms: null}]) {
    assert.throws(() => timing.barToTime(1, grid), /Grid/);
    assert.throws(() => timing.timeToBar(1, grid), /Grid/);
  }
  for (const value of [null, "", true, NaN, Infinity]) {
    assert.throws(() => timing.barToTime(value, {t1: 0, bar_s: 2}), /Bar position/);
    assert.throws(() => timing.timeToBar(value, {t1: 0, bar_s: 2}), /Audio time/);
  }
});
