# Deterministic whole-set planner

`engine/setlist/planning.py` is an I/O-free service over a caller-selected scope.
It does not read the catalog, inspect paths, read now-playing, interpret prompts,
write crates, or call AI providers. Existing matcher identity/version logic and
`score_candidate` supply song identity, edit preferences, transition continuity,
key and energy scoring. No new matching algorithm replaces those services.

## API

```python
from setlist.planning import plan_set, validate_policy

result = plan_set(
    [{"id": "file-1", "title": "Example", "artist": "Artist",
      "version": "Clean Intro", "path": "/selected/example.mp3",
      "duration_s": 180, "bpm": 110, "key": "8A", "energy": 0.7}],
    {"target_minutes": 3, "tolerance_minutes": 0, "max_tracks": 10,
     "clean_only": True, "must_include": ["file-1"], "exclude": [],
     "pinned": [{"position": 0, "file_id": "file-1"}],
     "bpm_min": 100, "bpm_max": 120, "energy_target": 0.7},
)
```

`validate_policy(dict)` returns an independent normalized policy. Invalid input
raises `ValueError`; contradictory but well-shaped constraints return conflicts
from `plan_set`, not a silently relaxed policy.

| Policy field | Contract |
| --- | --- |
| `target_minutes` | Required finite number, `0 < value <= 1440` |
| `tolerance_minutes` | Required finite number, `0 <= value <= 1440` |
| `max_tracks` | Required integer, `1..60` |
| `clean_only` | Boolean, defaults to false |
| `must_include` | Unique exact file IDs, defaults to `[]`, at most 60 |
| `exclude` | Unique exact file IDs, defaults to `[]`, at most 10,000 |
| `pinned` | At most 60 `{position, file_id}` dictionaries; defaults to `[]`; integer positions `0..max_tracks-1` |
| `bpm_min`, `bpm_max` | Optional finite numbers in `(0, 1000]`; minimum cannot exceed maximum |
| `energy_target` | Optional finite number in `[0, 1]`; soft preference only |
| `phases` | Optional ordered list of 1..12 nonempty phase policies; targets must sum exactly to the global target |

Unknown fields, null optional numbers, numeric strings, booleans masquerading as
numbers, NaN and infinity are rejected. There is deliberately no `allow_repeats`,
event-context or search-budget override. IDs are nonblank strings or nonboolean
integers: `1` and `"1"` are distinct. Missing excluded IDs are harmless; missing
required or pinned IDs are conflicts.

### Ordered phases (planner 1.1)

Each phase has required `phase`, `target_minutes` and `tolerance_minutes`.
Supported phase names are `ceremony`, `cocktail`, `dinner`, `first_dance`,
`open_dancing` and `generic`. Optional `label` is nonblank text of at most 80
characters; it defaults to the phase name. Optional `energy_target`,
`bpm_min`, `bpm_max`, `clean_only` and `must_include` follow the global field
contracts. No other phase fields are accepted.

```json
{
  "target_minutes": 6,
  "tolerance_minutes": 0,
  "max_tracks": 2,
  "phases": [
    {"phase": "dinner", "label": "Warm up", "target_minutes": 3,
     "tolerance_minutes": 0, "energy_target": 0.3},
    {"phase": "open_dancing", "label": "Peak", "target_minutes": 3,
     "tolerance_minutes": 0, "energy_target": 0.85}
  ]
}
```

The planner jointly searches track order and contiguous phase boundaries.
Every phase must contain at least one full track, meet its own inclusive
duration interval and satisfy its phase-specific requirements. Global timing,
clean/BPM bounds, must-plays, exclusions, no-repeat identities and absolute
pins remain enforced across the entire set. A phase cannot weaken a global
constraint. A phase-specific exact must-play cannot be consumed by another
phase; requiring one file in multiple phases is an explicit conflict.

Shared scoring uses the actual phase context and phase energy preference,
falling back to the global energy preference when absent. Repeat a phase name
with different labels/targets to express warm-up, peak and closing energy
steps. Missing energy is not invented and energy preferences are not hard
bounds. Omitting `phases` preserves the previous single-set behavior.

These are relative full-track budgets, not a wall-clock event scheduler.
There is no silence padding, skipped phase, truncated track or invented mix
overlap to meet a budget. A globally correct runtime cannot excuse an empty or
out-of-budget phase.

Tracks must be an iterable of at most 10,000 unique-ID mappings. Each needs
`id`, nonblank `title`, `artist`, `path`, and string-or-null `version`. All record
metadata must be finite JSON data; output retains every original field/value
without adding matcher-private fields or substituting another file/edit.
The path is a reference, not evidence that the file exists or is playable.

## Hard constraints and missing evidence

- Every returned alternative has at least one track and at most `max_tracks`.
- Sum of **full file durations** must be within the inclusive target/tolerance
  interval. No mix-overlap, trimming or invented durations are assumed.
  Comparisons use exact rational arithmetic over decimal number spellings;
  JSON output durations/deltas are ordinary numbers, rounded to float precision.
- Missing, nonpositive, boolean or string duration values are ineligible, even
  when the tolerance interval includes zero.
- With either BPM bound supplied, missing/nonpositive/boolean/string BPM is
  ineligible. Boundaries are inclusive; no half/double-time substitution.
- Clean-only requires explicit clean tags from the existing version helper
  applied separately to title and version. Dirty/explicit evidence in either
  field wins. “Radio Edit”, absent dirty tags, or an `is_clean` flag is not proof.
- Musts and pins refer to exact files. Exclusions cannot be overridden by them.
- Shared matcher-compatible identity prevents the same song appearing twice,
  even as different edits or feature-artist spellings. Requiring two different
  files of that identity is an explicit conflict.
- Pins are absolute, contiguous-list positions, not suggestions. Conflicting
  pins, an unreachable pin, or too many required songs cannot be relaxed.

The caller **must prefilter event scope and all event/song-level do-not-play
rules**, expanding song bans to all relevant file IDs where appropriate. This
API has no event adapter and cannot enforce rules that were not supplied.

## Result

The return value is JSON-friendly:

```text
{
  planner_version: "1.1",
  feasible: boolean,
  alternatives: [{
    tracks: [complete original file records, in playback order],
    duration_s: number,
    delta_s: number,                 # actual minus target
    score: number,                   # additive existing score_candidate terms
    reasons: [string],
    track_reasons: [{file_id: ID, reasons: [string]}]
  }],
  diagnostics: {
    status: "feasible" | "infeasible" | "search_exhausted",
    search_complete: boolean,
    truncated_by: ["candidate_limit" | "beam_width" | "expansion_limit"],
    input_count: integer,
    eligible_count: integer,         # before required-song edit reservation
    candidate_count: integer,        # searched pool, 0 if rejected before search
    filtered: {reason_code: count},
    expansions: integer,
    limits: {input_tracks, candidates, beam_width, expansions, alternatives, max_tracks}
  },
  unresolved: [{code: string, ...conflict-specific file IDs/reasons/positions}]
}
```

With an explicit phase policy, each alternative additionally includes
`phases`: ordered records containing `phase_index`, `phase`, `label`,
`start_position` (inclusive), `end_position` (exclusive), `start_s`, `end_s`,
`duration_s`, `delta_s`, `energy_target` and ordered exact `file_ids`.
All times are relative full-file sums. The same file sequence with different
valid phase partitions can appear as separate alternatives.

`feasible` means at least one fully checked alternative exists. False with
`status="infeasible"` is proof under the supplied scope/metadata, from a hard
conflict, safe duration/capacity bound, or exhaustive search.
False with `status="search_exhausted"` means **unknown**, not impossible.
Never present it to the DJ as proof of infeasibility.
An existing feasible result can still have `search_complete=false`: it is valid
but not a guarantee of globally optimal quality or coverage. Returned alternatives
are the best three encountered, not a complete list of all feasible sets.

Conflict codes include `required_file_missing`, `required_file_ineligible`,
`required_song_repeated`, `pin_position_conflict`, `file_pinned_twice`,
`required_capacity`, `duration_or_capacity_impossible`, `no_feasible_set`,
and `search_exhausted`. Ineligibility codes include `excluded`,
`unknown_duration`, `unknown_bpm`, `bpm_out_of_bounds`, `dirty`,
and `clean_unconfirmed`.
Phase conflict codes additionally include `file_required_in_multiple_phases`,
`phase_required_file_ineligible` and `phase_capacity`.

## Search and reproducibility

Public module constants bound input size, 120 candidates, beam width 128,
200,000 attempted expansions, 60 tracks, and three alternatives. Required and
pinned files survive candidate selection. Other edits of a required song are
reserved out; otherwise different edits remain separate candidates.
The effective beam can be smaller than 128: the planner reserves expansion
capacity for every requested position, accounting for fixed pins and the two
possible phase-boundary branches. Diagnostics report that effective width.
This prevents a long feasible request from consuming the entire budget in its
early positions. Actual pruning still marks `search_complete=false`; a narrower
beam does not imply exhaustive or globally optimal planning.
When the pool exceeds 120, half the remaining slots favor the best shared score
across the requested phases and matcher edit preference, and half sample the
duration-sorted remainder.
This keeps duration diversity without pretending to search the entire library.

Each depth expands legal files, enforces reserved pins and no-repeat identities,
and applies safe duration/mandatory-capacity bounds. The beam prefers completed
requirements, progress toward minimum duration, shared score, then stable ID
order. Completed alternatives sort by absolute duration delta, descending shared
score and stable ID sequence. Scoring uses the current phase and preceding
planned tracks, never live now-playing. Unknown soft metadata contributes no
invented evidence.

Identical records/policy produce identical output, regardless of track input,
must/exclude list or pin-list order. Input objects are not mutated.
This is bounded heuristic optimization: a small beam or budget can miss a valid
set, especially in large/long constrained scopes. A score is not a transition
safety certification. Full-duration arithmetic is not an estimated audible mix
length, and metadata correctness remains the caller's responsibility.

## Validation

No installs, personal library access or paid requests are needed:

```sh
python3 -m unittest discover -s engine/setlist -p 'test_planning.py'
```

Tests compare small feasible and impossible fixtures against an independent
exhaustive permutation oracle, and cover exact tolerance boundaries, exact edits,
pins, musts, exclusions, unknown evidence, strict clean/BPM rules, stable ordering,
invalid policies/records, bounded-search failures, and the 60-track pinned limit.
The phase oracle additionally enumerates every nonempty contiguous partition
for two- and three-phase fixtures, including exact moment placement, pins,
alternate edits, phase bounds and decimal-exact timing.

## Separate transition/timing overlay

`setlist.transitions.transition_report(ordered_tracks, playback=None)` is a
separate pure service. Studio attaches its result as `transition_evidence` to
each returned alternative and recomputes it for approval. `plan_set` itself
does not change its full-track constraints or use the overlay to relax timing.
`validate_playback` rejects unknown fields, duplicates, nonfinite/negative
numbers, invalid hashes and malformed window/pair shapes.

Playback schema and review workflow are documented in
[Studio](STUDIO.md#cue-evidence-and-explicit-playback-timing). Each exact
hash-bound window has entry/exit seconds and explicit review state. Adjacent
overlaps, including zero, must be specified. Arithmetic uses decimal rational
values and checks individual and combined overlaps against playable lengths.
No omitted window becomes a whole-file default; no missing overlap becomes
an implicit cut.

The result includes:

- `status`: `estimated` only when complete valid arithmetic is possible,
  otherwise `incomplete`. Neither status asserts musical safety.
- `full_file_runtime_s`, `play_window_subtotal_s`, `estimated_elapsed_s`:
  separate numbers, or null when their required evidence is unavailable.
- `timeline`: relative window start/end times only when elapsed time is known.
- `tracks`: exact IDs/hashes, submitted windows, validation warnings and
  optional existing cue evidence; this evidence is not consumed as automatic
  window approval.
- `transitions`: exact ordered pairs/edits, raw BPM, shared Camelot parsing,
  tempo difference, explicit overlap, assumptions and audition/grid/vocal
  warnings. No half/double-tempo or key-shift assumption is invented.
- `issues`, `unused_window_ids`, `unused_overlap_pairs`: missing/stale/
  invalid evidence and assumptions not used by that alternative.

`estimated_elapsed_s` equals `sum(play windows) - sum(overlaps)`, without
tempo changes, loop extensions, silence padding or render/playback proof.
The report is descriptive evidence, not mixed-duration feasibility
optimization or an assertion that the source has been auditioned.
