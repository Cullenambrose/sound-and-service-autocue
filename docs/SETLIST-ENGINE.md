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
  already played or placed. Do-not-play wins a must-play conflict. Conflicts
  and unresolved must-plays are returned explicitly.
- Prep returns every resolvable, eligible must-play. If `limit` is less than
  that count, the result expands to include all of them, then fills remaining
  slots with the highest-ranked other songs. Duplicate must-play requests for
  the same normalized identity are represented once, using the first resolved
  request's selected local version.
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
  `customer`, and `in_crate`. Song-cue boosts are deduplicated by song identity.
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
