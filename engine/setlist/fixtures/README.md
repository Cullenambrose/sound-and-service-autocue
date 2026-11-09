# Setlist engine Prep/Live demo fixture (synthetic)

All names, artists, venue, and inquiry ID are fictional. No real customer data or music files.

- `mock-wedding-event.json`: raw event payload for `context_from_event(event, guest_requests=...)`. It uses the keys `inquiry_id`, `locked`, `planner.status`, `event_timelines.items[]` (name/kind/start_time/duration_minutes/song/songCue; extra keys become metadata), and `music_lists.{must_play, play_if_possible, do_not_play, special_moments}`.
- Guest requests are Live-only. They live under `_demo_live_inputs.guest_requests` and must be passed in as a separate argument. The parser ignores that key.
- `mock-library.json`: `{"tracks": [...]}`, 60 tracks. Each track has id, file_id, path, title (with the version in parentheses), artist, version, genre, bpm, key, and duration_s. The only genres used are Pop, Hip Hop, R&B, Rock, Dance / EDM, House, Latin, Country, Throwback, and Slow / Ballad.
- `gen.py`: regenerates both files.

## Cases and expected behavior
| # | Case | Input | Expected |
|---|------|-------|----------|
| 1 | Timeline song cues | Processional, Entrance, Parent Dances x2, Cake, Last Dance | Each becomes a SongCue with moment = kind |
| 2 | songCue row with a song | First Dance, kind `songCue`, song "Slow Orbit (Short Edit)" plus fade metadata | At afcb392+ it is a cue with moment "First Dance" and empty metadata, and the Short Edit file is picked over the original. In the old ded052f code it was dropped. |
| 3 | songCue row with metadata only | "Fade Note" `{version: Extended, seconds: 12}` | It is kept as a timeline entry and is NOT a song cue |
| 4 | Must-play | 8 rows: mp-1 to mp-6, plus mp-8 "Shine On (Clean)" | Each resolves to the correct file and gets the must-play boost in Prep and Live |
| 5 | Do-not-play beats must-play | "Chicken Shuffle" is on both must-play and do-not-play | It is excluded and reported as a conflict |
| 6 | Title-only ban | dnp-1 `{"title": "Hello"}` with no artist | Both "Hello" tracks (Ada Moon and The Static Hearts) are excluded regardless of version. "Hello Again" is NOT banned. Guest g-4 is reported as a conflict, and g-5 stays playable. |
| 7 | Other bans | Cha Cha Line, Loud Ex (Dirty) | Excluded for every version |
| 8 | Play-if-possible | pip-1 to pip-4 | Smaller boost (30 vs 100) |
| 9 | Missing song | pip-5 "Not In Library" / Ghost Band | Reported as unresolved. No substitute is chosen. |
| 10 | Unavailable version | g-1 "Midnight Signal" Radio Edit, where only the Extended Mix and Clean exist | Clear "requested version unavailable" message. The engine must not silently treat this as fulfilled. |
| 11 | Clean vs Extended conflict | pip-1 asks for the Extended Mix and guest g-2 asks for Clean, same song | One file is selected (must-play > play-if-possible > guest), and the other is reported as `version_conflict_not_selected` |
| 12 | Decoy, same artist | mp-8 "Shine On" vs the library's "Shine On Me" (Velvet Coast) and "Shine Only" (another artist) | Only "Shine On (Clean)" is fulfilled or boosted. g-3 "Shine On Me" is its own song. |
| 13 | Guest is Live-only | 6 guest requests | `rank_prep_crate()` output is identical with or without guests. `rank_next_song()` shows a "Live guest request" reason for g-6 "Arcade Love". |
| 14 | Status | `locked: false`, `planner.status: "draft"` | `context.locked == False`. Setting the status to "confirmed" locks it. |
| 15 | Available exact version | g-7 Midnight Signal (Clean) | The Clean file (trk-010) is the exact pick in Live, with the guest boost and badge |
| 16 | Guest asks for a do-not-play song | g-8 Cha Cha Line (Clean) | No version is selected, and a guest_request conflict is reported |
| 17 | Ambiguous identity | g-9 `{"title":"Night Drive","version":"Re-Drum"}`, no artist. The library has Night Drive by Amber Lane (Clean) and by Cobalt Youth (Re-Drum). | The request stays unresolved. The fact that only one of them has the Re-Drum version must not break the tie. |
| 18 | Ambiguous control (bare title) | g-10 `{"title":"Moon Harbor"}`, no artist or version. The library has "Moon Harbor" by Iris Dunn and by The Tidewater Band, both with no version text. | `ambiguous`, no fulfillment or boost for either track |

Library is now 64 tracks (trk-063/064 are the g-10 control, appended).

## Acceptance checks (Booth Buddy PM)
Run each check twice: Live with `_demo_live_inputs.guest_requests`, and against `mock-event-no-guests.json`.
1. **Available version.** g-7 resolves to the exact file (trk-010, Clean), with the guest boost, a "Live guest request" reason, and the badge.
2. **Do-not-play.** g-8 gets no version selected and appears in `conflicting_requests` with category guest_request.
3. **Unavailable version.** g-1 (Radio Edit) reports `version_unavailable` with the resolved identity (Kilo Avenue / Midnight Signal) and matcher evidence (candidates and confidence). It gets no boost, badge, or reason. Note that both Midnight Signal files are now boosted by g-7, so to see the Extended Mix (trk-009) and Clean (trk-010) score the same as in the no-guest run, isolate g-1 by itself. The check is that g-1 adds nothing.
4. **No-guest comparison.** Prep output for the event with guests equals Prep output for `mock-event-no-guests.json`. In Live, the only differences are the boosts from g-2, g-3, g-5, g-6, and g-7.
5. **Ambiguous.** g-9 stays unresolved and boosts neither Night Drive.
6. **Shuffled input.** `mock-library-shuffled.json` (the same tracks shuffled with `random.Random(42)`) gives identical Prep and Live output to `mock-library.json`.

## Observed on a8569b7 (Booth Buddy PM run, 2026-11-07)
All 11 Live checks passed (62 tracks, 9 guest requests). Evidence: `/workspace/reviews/booth-live-a8569b7/live-check-results.json`.
**Known matcher failures (expected to fail until the matcher fix lands): mp-4, g-3, g-9.** Their expectations above are correct; do not edit them to match current output.

S&S PM rule reconciliation (/workspace/reviews/ss-pm-a8569b7/summary.md): these are **engine bugs, not expectation errors**. The expectations above stand.
- mp-4 and g-3 come back ambiguous because the matcher ranks titles that still carry version text, plus a clean bonus (matcher.py:198-213). They should be matched.
- g-9 sees both Night Drive candidates but is reported missing (confidence gate before ambiguity, matcher.py:318 vs 327). It should be ambiguous. Outcome (no boost) is correct.
- Case 2: the First Dance cue boosts trk-003 (original), not trk-004 (Short Edit); the engine ignores cue versions (engine.py:304-336).
