const AutoCueTiming = (() => {
  function finite(value, name) {
    if (value == null || typeof value === "boolean" ||
        (typeof value === "string" && !value.trim()) || !Number.isFinite(Number(value))) {
      throw new Error(`${name} must be a finite number.`);
    }
    return Number(value);
  }
  function origin(grid) {
    const t1 = finite(grid.t1, "Grid t1"), barS = finite(grid.bar_s, "Grid bar_s");
    if (barS <= 0) throw new Error("Grid bar_s must be positive.");
    return t1 - finite(grid.offset_ms === undefined ? 0 : grid.offset_ms, "Grid offset_ms") / 1000;
  }
  return {
    barToTime: (bar, grid) => finite(origin(grid) + finite(bar, "Bar position") * Number(grid.bar_s), "Audio time"),
    timeToBar: (time, grid) => finite((finite(time, "Audio time") - origin(grid)) / Number(grid.bar_s), "Bar position"),
    cueTime: (bar, cue, grid) => {
      if (cue?.unsnapped && bar === cue.bar) return finite(cue.time_s, "Cue time");
      return finite(origin(grid) + finite(bar, "Bar position") * Number(grid.bar_s), "Audio time");
    },
  };
})();
if (typeof module !== "undefined") module.exports = AutoCueTiming;
