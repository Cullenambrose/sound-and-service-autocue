const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf("function outroEvidenceText("),
                             source.indexOf("function firstBar(")), context);
const text = context.outroEvidenceText;

test("missing, incomplete and invalid vocal evidence never looks verified", () => {
  assert.match(text({}), /unverified.*unavailable.*Listen/);
  assert.match(text({outro_vocal_evidence: {status: "unverified", reason: "coverage is incomplete"}}),
               /unverified.*coverage is incomplete/);
});

test("grid diagnostics distinguish unavailable, aligned and drift review states", () => {
  assert.match(text({}), /Grid check unavailable.*reanalyze/);
  const message = text({grid_diagnostics: {status: "review_required", issues: [
    {code: "accumulated_drift", message: "Timing drifts more than a quarter beat."},
    {code: "possible_half_time_grid", message: "Possible half-time BPM."},
  ]}});
  assert.match(message, /Grid check: review_required/);
  assert.match(message, /Timing drifts more than a quarter beat/);
  assert.match(message, /Possible half-time BPM/);
  assert.match(text({grid_diagnostics: {status: "aligned", issues: []}}), /Grid check: aligned/);
});

test("detected instrumental endings show timing, limitations and rejected words", () => {
  const message = text({outro_vocal_evidence: {
    status: "analyzed", last_vocal_end_s: 160.125, ignored_words: 2,
  }});
  assert.match(message, /160.13s/);
  assert.match(message, /leakage\/reverb and missed vocals require listening/);
  assert.match(message, /2 invalid or stem-unsupported word timings ignored/);
});

test("song-end fallback is not presented as an instrumental outro", () => {
  const message = text({no_vocal_free_outro: true, outro_vocal_evidence: {
    status: "analyzed", last_vocal_end_s: 175.31, ignored_words: 0,
  }});
  assert.match(message, /No full detected vocal-free outro bar remains/);
  assert.match(message, /not an instrumental outro/);
  assert.doesNotMatch(message, /No later stem activity/);
});

test("outro intro-loop proposals are explicitly unapplied and unsupported for export", () => {
  const message = text({mix_window_evidence: {
    intro: {status: "unavailable"},
    intro_loop_fallback: {status: "needs_serato_fixture", proposed_start_bar: 0,
      loop: {bars: 8, beats: 32, start_bar: 64, end_bar: 72}},
  }});
  assert.match(message, /8 bars \(32 beats\).*64–72/);
  assert.match(message, /Proposed START: bar 0/);
  assert.match(message, /export is not yet verified; nothing applied/);
});

const scores = vm.createContext({
  escapeHtml: text => String(text).replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;"),
});
vm.runInContext(source.slice(source.indexOf("function renderScoreReport("),
                             source.indexOf('$("#experiments-open").onclick')), scores);

test("evaluation has no success-shaped accuracy when ground truth is absent", () => {
  const report = scores.renderScoreReport({graded_songs: 0, variants: {}, skipped: [{reason:"no review"}]});
  assert.match(report, /No accuracy estimate/);
  assert.match(report, /1 tracks skipped/);
  assert.doesNotMatch(report, /100%/);
});

test("evaluation separates positioned and empty targets and escapes variant labels", () => {
  const report = scores.renderScoreReport({
    graded_songs: 1, variants: {baseline: {
      label: '<img src=x onerror="bad">', positioned_right: 0, positioned_total: 1,
      empty_right: 7, empty_total: 7, accuracy: 0.875, right: 7, total: 8,
      by_map: {DROP: {right: 7, total: 8, positioned_right: 0, positioned_total: 1}},
      wins: 0, losses: 0,
    }}, gemini_cost: {new_cost_usd: 0.0123}, skipped: [], unavailable_predictions: [],
  });
  assert.match(report, /0%.*\(0\/1\)/);
  assert.match(report, /7\/7/);
  assert.match(report, /Drop positioned/);
  assert.doesNotMatch(report, /88%/);
  assert.match(report, /&lt;img/);
  assert.doesNotMatch(report, /<img/);
  assert.match(report, /\$0.0123/);
  assert.match(report, /Different cue meanings\/maps are excluded/);
});

const eventElements = new Map(["#event-review-status", "#event-review-state", "#event-review-note",
  "#event-label-list"].map(selector => [selector, {textContent:"", value:"", innerHTML:""}]));
const eventUi = vm.createContext({
  activeTrack: {hash:"a".repeat(64)},
  $: selector => eventElements.get(selector),
  $$: () => [],
  escapeHtml: value => String(value).replace(/[&<>"']/g, char =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char])),
});
vm.runInContext(source.slice(source.indexOf("function renderEventReviewLoading("),
                             source.indexOf("async function addEventLabel(")), eventUi);

test("event review renderer preserves uncertain provenance and escapes annotation notes", () => {
  eventUi.renderEventReview({
    track_hash:"a".repeat(64),
    review:{state:"uncertain",note:"Needs another listen",updated_at:"2026-10-22T12:00:00Z"},
    annotations:[{
      id:1,event_type:"drop",certainty:"uncertain",time_s:42.125,bar:20.5,
      note:'<img src=x onerror="bad">',source:"human_review",created_at:"2026-10-22T11:00:00Z",
    }],
  });
  const rendered = eventElements.get("#event-label-list").innerHTML;
  assert.equal(eventElements.get("#event-review-state").value, "uncertain");
  assert.match(rendered, /Drop · uncertain/);
  assert.match(rendered, /Human · 2026-10-22/);
  assert.match(rendered, /&lt;img/);
  assert.doesNotMatch(rendered, /<img/);
  assert.match(eventElements.get("#event-review-status").textContent, /1 active label · uncertain/);
});

test("event annotation UI exposes the required taxonomy, certainty and skip controls", () => {
  const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
  for (const label of ["drop", "build", "breakdown", "chorus", "other", "uncertain", "skipped"]) {
    assert.match(html, new RegExp(`value="${label}"`));
  }
  assert.match(html, /Add at playhead/);
  assert.match(html, /Export labels/);
});
