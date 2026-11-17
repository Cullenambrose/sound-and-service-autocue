const assert = require("node:assert/strict");
const test = require("node:test");
const {compareObservedSet, musicMetadataSummary, planDraftSummary, trackReasons} = require("./studio-plan-view.js");

test("brief read-back separates timing, filters, phases and quoted evidence", () => {
  const summary = planDraftSummary({
    intent: {
      rule: {genre:"House"},
      policy: {target_minutes:30, tolerance_minutes:3, max_tracks:20,
        clean_only:true,
        phases:[{phase:"dinner",label:"Dinner",target_minutes:10,tolerance_minutes:1}]},
      unsupported: [],
    },
    constraint_evidence: [
      {field:"genre",value:"House",quote:"House",scope:"rule"},
      {field:"clean_only",value:true,quote:"tagged-clean",phase_index:0},
    ],
    note: "Review source quotes.",
  });
  assert.equal(summary.overview, "30 minutes, +/-3 minutes, up to 20 tracks.");
  assert.deepEqual(summary.filters, ["Library filter - Genre: House", "Set preference - Tagged-clean required: yes"]);
  assert.deepEqual(summary.phases, ["Dinner: 10 minutes (+/-1 minutes)"]);
  assert.deepEqual(summary.evidence, [
    '"House" interpreted as Genre in library filters: House',
    '"tagged-clean" interpreted as Tagged-clean required in phase 1: yes',
  ]);
  assert.match(summary.caveat, /not proof/i);
});

test("planner explanations stay attached to their exact files and selected order", () => {
  const alternative = {
    tracks: [{id:"first"}, {id:"second"}],
    track_reasons: [
      {file_id:"second", reasons:["BPM fits preferred transition window"]},
      {file_id:"first", reasons:["Eligible local-library track for dinner phase"]},
    ],
  };
  assert.deepEqual(trackReasons(alternative), [
    {file_id:"first", reasons:["Eligible local-library track for dinner phase"]},
    {file_id:"second", reasons:["BPM fits preferred transition window"]},
  ]);
});

test("planner explanation mismatches and invalid reasons fail visibly", () => {
  for (const alternative of [
    {tracks:[{id:"file"}], track_reasons:[]},
    {tracks:[{id:"file"}], track_reasons:[{file_id:"other", reasons:["Signal"]}]},
    {tracks:[{id:"file"}], track_reasons:[{file_id:"file", reasons:[" "]}]},
  ]) {
    assert.throws(() => trackReasons(alternative), /Planner/);
  }
});

test("music metadata summary distinguishes local tags, unverified sources and unknown values", () => {
  assert.equal(musicMetadataSummary({
    bpm: 98, key: "8A", music_metadata_evidence: {
      bpm: "local_file_tag", key: "unverified",
    },
  }), "BPM 98 (local file tag; not independently verified); key 8A (source unverified)");
  assert.equal(musicMetadataSummary({
    bpm: null, key: "", music_metadata_evidence: {bpm: "unknown", key: "unknown"},
  }), "BPM unknown (unknown); key unknown (unknown)");
  assert.equal(musicMetadataSummary({bpm: 120, key: "2A"}),
    "BPM 120 (source not recorded); key 2A (source not recorded)");
});

test("post-gig comparison uses only selected exact observations and sorts by observed time", () => {
  const result = compareObservedSet({tracks:[{id:"a"}, {id:"b"}, {id:"c"}]}, [
    {request_id:"late", file_id:"b", played_at:"2026-11-14T20:10:00Z"},
    {request_id:"extra", file_id:"x", played_at:"2026-11-14T20:00:00Z"},
    {request_id:"again", file_id:"b", played_at:"2026-11-14T20:20:00Z"},
  ]);
  assert.deepEqual(result.actual_order.map(event => event.request_id), ["extra", "late", "again"]);
  assert.deepEqual(result.not_observed.map(track => track.id), ["a", "c"]);
  assert.deepEqual(result.outside_plan.map(event => event.file_id), ["x"]);
  assert.deepEqual(result.repeats, [{file_id:"b", count:2}]);
  assert.deepEqual(result.planned_tracks.map(item => item.selected_play_count), [0, 2, 0]);
});

test("post-gig comparison rejects ambiguous plan IDs and malformed selected observations", () => {
  assert.throws(() => compareObservedSet({tracks:[{id:"a"}, {id:"a"}]}, []), /duplicate/);
  assert.throws(() => compareObservedSet({tracks:[{id:"a"}]}, [
    {request_id:"same", file_id:"a", played_at:"2026-11-14T20:00:00Z"},
    {request_id:"same", file_id:"a", played_at:"2026-11-14T20:01:00Z"},
  ]), /unique IDs/);
});

test("incomplete brief drafts and malformed phase evidence fail visibly", () => {
  assert.throws(() => planDraftSummary({intent:{}}), /incomplete/);
  assert.throws(() => planDraftSummary({intent:{rule:{},policy:{
    target_minutes:10,tolerance_minutes:1,max_tracks:5,phases:[{}],
  }}}), /invalid phase/);
});
