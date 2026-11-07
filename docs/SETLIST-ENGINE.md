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
- Positive must-play, play-if-possible, and Live guest requests are resolved
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
  local match: must-play, then play-if-possible, then (Live only) guest request.
  Within that category, the lexically smallest normalized available version
  wins. `version_conflicts` reports all requested versions and the selection;
  non-selected version requests are reported in `conflicting_requests`.
- Prep returns every confidently resolved, eligible must-play identity. If
  `limit` is below their count, the result expands and `capacity_overflow`
  explicitly reports the requested limit, required count, and resulting count.
  Remaining slots are filled by rank. Multiple requests for one identity
  produce one recommendation, using the selected version policy above.
- When files duplicate one song, the existing matcher chooses the ordinary
  song representative using its configured version preferences. After identity
  resolution, exact-version selection is restricted to that song identity.
  Matcher ties and fallback file selection use a stable key of library/file
  IDs, path, version, then the complete sorted metadata record. This chooses
  one intact source record; it never combines metadata from files.
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

The recommendation result keeps its original `items`,
`unresolved_must_play`, `conflicting_must_play`, and `phase` fields, and adds
`resolution_diagnostics`, `do_not_play_diagnostics`, `conflicting_requests`,
`version_conflicts`, and optional `capacity_overflow` for explicit review
outcomes.
