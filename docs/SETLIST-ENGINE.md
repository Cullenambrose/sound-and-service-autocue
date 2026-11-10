# Shared Prep and Live Setlist Engine

`engine/setlist/` is a pure, in-memory recommendation layer. Use
`context_from_event()` to adapt `EventData` or event JSON, then pass that
`SetlistContext` and an already available local-library track collection to
`rank_next_song()` or `rank_prep_crate()`. Live guest requests and DJ memory are
separate adapter arguments; the engine does not fetch or persist them.

Song identities use `library.matcher` normalization, feature extraction and
supported DJ-pool version tags. They contain only normalized primary artist
and title; recommendations retain the selected local record, including its
exact file path/IDs, raw tags, version and other metadata. Requested references
are resolved with `library.matcher.match_track()`. Missing titles stay
unresolved; an absent artist remains absent and is never fabricated.

## Rules

- The only hard exclusions are resolved do-not-play requests and identities
  already played or placed. A do-not-play title is compared by exact matcher-
  normalized song title after supported version and featuring removal. With an
  artist, only that normalized primary artist is excluded; without one, every
  exact title identity across artists is excluded. No fuzzy title ban is used.
  `do_not_play_diagnostics` reports the identities excluded by each ban,
  including whether the artist was unspecified. Do-not-play wins must-play and
  guest-request conflicts; `conflicting_requests` and the backwards-compatible
  `conflicting_must_play` report those collisions.
- Positive must-play, phase-relevant song-cue, play-if-possible, and Live guest requests are resolved
  only when `library.matcher.match_track()` returns `matched`. Ambiguous and
  missing outcomes are not treated as fulfilled. `resolution_diagnostics`
  retains each matcher status, actual confidence, alternatives, and any
  requested-version failure. `unresolved_must_play` remains available for
  callers that used the earlier result shape.
- Song identity is resolved against the complete supplied library before
  checking playback versions. A confident identity with no requested local
  version reports `version_unavailable` consistently, retains the identity and
  matcher evidence, and receives no request-derived fulfillment. Other songs'
  files cannot satisfy that requested version.
- Versioned positive requests must resolve to that exact normalized local
  version; another version remains an ordinary candidate, not fulfillment and
  receives no boost from that request. When same-song requests conflict, the
  selected version comes from the highest-priority category with a confident
  local match: must-play, then phase-relevant song cue, then play-if-possible,
  then (Live only) guest request. The existing lower-priority rules are unchanged.
  A cue without a version requirement does not override lower-priority version
  selection or the ordinary representative.
  Within that category, the lexically smallest normalized available version
  wins. `version_conflicts` reports all requested versions and the selection;
  non-selected version requests are reported in `conflicting_requests`.
- Phase-relevant cues use the same identity-first exact-version resolver as
  other requests and report the `song_cue` diagnostic category in both APIs.
  Cue moment and metadata are retained in the diagnostic request. Inactive
  moments do not select versions. An unavailable cue version retains the
  confidently resolved identity and reports `version_unavailable`. Its
  deduplicated timeline boost remains, but the unavailable cue adds no
  `requested` badge or cue-derived recommendation reason. Labels independently
  earned by valid requests or available cues remain intact. This song-level
  boost also remains when must-play wins a version
  conflict, which is reported explicitly. Ambiguous cues remain unresolved.
- Prep returns every confidently resolved, eligible must-play identity. If
  `limit` is below their count, the result expands and `capacity_overflow`
  explicitly reports the requested limit, required count, and resulting count.
  Remaining slots are filled by rank. Multiple requests for one identity
  produce one recommendation, using the selected version policy above.
- When files duplicate one song, the existing matcher chooses the ordinary
  song representative using its configured version preferences. After identity
  resolution, exact-version selection is restricted to that song identity.
  An available version named in a matcher request takes precedence over those
  preferences; absent or unavailable versions fall back to the preferences.
  Prep/Live still report unavailable requested versions rather than fulfilling
  them with a fallback. Within tied same-song files, an exact raw title and
  artist pair wins before the matcher's existing sorted metadata order.
  Prep/Live fallback selection retains its stable file key (path, IDs, version,
  then complete metadata). Selection always keeps one intact source record.
- Guest requests, their customer badges, boosts, reasons, and diagnostics are
  Live-only. Changing `guest_requests` does not change `rank_prep_crate()`.
- Scoring is additive and deterministic. `SetlistSettings` exposes weights for
  must-play (100), play-if-possible (30), relevant song cue (25), guest request
  (35), DJ-memory floor-filler (18), played-next Markov prior (20), BPM (12),
  Camelot (8), energy slope (10), and genre continuity (6).
- The BPM window defaults to 8%. Outside-window BPM gets no compatibility
  points but never excludes a song. Half/double-time matching is off by default.
  Played-next transition weights are normalized as probabilities across the
  current song's outgoing transitions. Missing BPM, key, energy or genre has no
  fabricated value or compatibility score.
- The only recognized genre buckets are Pop, Hip Hop, R&B, Rock, Dance / EDM,
  House, Latin, Country, Throwback, and Slow / Ballad. Any other genre is
  neutral.
- Direct request, customer, and crate-membership badges are `requested`,
  `customer`, and `in_crate`. Customer badges/reasons are Live-only.
  Song-cue boosts are deduplicated by song identity.
- With no `now_playing`, BPM/key transition, genre-continuity, and Markov terms
  are omitted. Phase-relevant cues and an available open-dancing energy target
  can still rank openers.

Timeline phase selection uses the active row's kind/name/title and timing.
Recognized planner kinds include Processional, Entrance, First Dance, Parent
Dances, Cake, and Last Dance; existing ceremony, cocktail, dinner, first-dance,
and open-dancing timeline names are also supported. When no timeline row has
usable timing (or no `at` is provided), the first recognized row in canonical
source order is the deterministic fallback; unknown kinds remain generic.
Timed open-dancing rows divide their actual duration into warm-up, peak, and
last-dance thirds. No schedule time is inferred. Live may alternatively supply
an explicit energy stage or target.

## Safety and integration boundary

The engine performs no file, network, database, tag, or crate I/O and makes no
LLM calls. It does not decide event write eligibility: downstream event-crate
writing remains governed by `locked`. Prep may pass recommendation paths to the
existing crate planner/writer as a separate step. Any tag change must use
`tag_gate`; existing dry-run, explicit-apply, and Serato-closed safeguards
remain downstream. The Python matcher remains the source of truth.

No events API route was changed. The current events API may only provide
must-play, play-if-possible, and do-not-play; sending special moments, timeline
song metadata, guest requests, or DJ-memory data through that route is later
integration work. Synthetic in-memory tests establish software behavior, not
musical quality, cue accuracy, or real-world transition quality.

The shared matcher now scores normalized song identities without playback-version
text or preference bonuses. Confidence remains 60% title similarity plus 40%
primary-artist similarity; the setlist cutoff is still inclusive at 0.6.
Confident matches require the same whole normalized title after version and
featuring removal, comparing with whitespace removed so apostrophes and dotted
initialisms remain punctuation-equivalent (`Dont Stop Believin` and `YMCA`).
Punctuation-equivalent titles receive exact title similarity, including when
the artist is absent. If no exact title exists, same-primary-artist or artist-free
requests expose whole-word title prefixes and title similarity at or above the
configured cutoff (0.6 in Prep/Live) as `ambiguous` review candidates. These
suggestions never select a file or earn a boost, badge, or fulfillment, even for
a single candidate. Their confidence retains the weighted title/artist evidence;
a partial title without an artist can therefore be ambiguous below 0.6 without
lowering the confident-match cutoff. Exact titles always win over suggestions.
`Intro Edit` is stripped as a complete version tag, not as `Intro` plus a
leftover song-title word.
Ambiguity compares distinct song identities within the existing five-point
window, not duplicate files of one identity. Clean/Intro/Short Edit preferences
only order files inside an identity, after an available requested version and
before raw-title/artist and deterministic metadata tie-breaking.
Unrelated titles and non-exact suggestions from a different artist stay missing
regardless of version preference.

The original synthetic Desktop demo is preserved byte-for-byte in
`engine/setlist/fixtures/`, including its historical README observations.
`engine/setlist/test_demo.py` exercises all 18 original expectations, including
`mp-4` -> `trk-038`, `g-3` -> `trk-014`, and ambiguous `g-9`/`g-10` at exactly
0.6 without fulfillment. The separate `fixtures/followup-cases.json` adds
cases 19-21: Corazon is missing against Horizon, Shine On lists Shine On Me as
ambiguous without fulfillment, and a
Summer (Dirty) cue picks Dirty over Clean and Clean Intro. All 21 cases cover
Prep/Live and shuffled library input. Run them against checkout modules with
`PYTHONPATH=engine .venv/bin/python -m unittest setlist.test_demo -v`.

The recommendation result keeps its original `items`,
`unresolved_must_play`, `conflicting_must_play`, and `phase` fields, and adds
`resolution_diagnostics`, `do_not_play_diagnostics`, `conflicting_requests`,
`version_conflicts`, and optional `capacity_overflow` for explicit review
outcomes.
