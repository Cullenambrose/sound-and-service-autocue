"""Synthetic, in-memory regression tests for shared Prep/Live song ranking."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from library import event_sync
from library import __main__ as library_cli
from library.adapters import serato as serato_adapter_module
from library.adapters.serato import SeratoAdapter
from library.event_sync_schema import parse_event
from setlist import (
    CapacityOverflow,
    GENRES,
    SetlistContext,
    SetlistSettings,
    SongCue,
    SongIdentity,
    SongRequest,
    context_from_event,
    rank_next_song,
    rank_prep_crate,
    song_identity,
)
from setlist.phase import select_phase


def track(title, artist, **metadata):
    return {"title": title, "artist": artist, **metadata}


def identities(recommendations):
    return [item.song_identity for item in recommendations]


class SetlistEngineTests(unittest.TestCase):
    def test_identity_reuses_matcher_normalization_and_ignores_version_and_feature(self):
        self.assertEqual(
            song_identity("  Le Freak (Radio Edit) feat. Nile Rodgers! ", "Chic ft. Guest"),
            song_identity("le freak", "chic"),
        )
        self.assertIsNone(song_identity("", "Artist"))
        self.assertEqual(song_identity("Song", None), SongIdentity("", "song"))

    def test_do_not_play_and_played_or_placed_are_the_only_hard_exclusions(self):
        library = [
            track("Keep", "A", bpm=250),
            track("Never", "B"),
            track("Already Played", "C"),
            track("Already Placed", "D"),
        ]
        context = SetlistContext(
            do_not_play=(SongRequest("Never", "B"),),
            already_played=frozenset({song_identity("Already Played", "C")}),
            already_placed=frozenset({song_identity("Already Placed", "D")}),
        )
        result = rank_next_song(context, library, limit=20)
        self.assertEqual(identities(result), [song_identity("Keep", "A")])

    def test_do_not_play_wins_a_must_play_conflict_and_reports_it(self):
        context = SetlistContext(
            must_play=(SongRequest("Conflict", "Artist"),),
            do_not_play=(SongRequest("Conflict", "Artist"),),
        )
        result = rank_prep_crate(context, [track("Conflict", "Artist")])
        self.assertEqual(result.items, [])
        self.assertEqual(len(result.conflicting_must_play), 1)
        self.assertEqual(result.conflicting_must_play[0].reason, "do_not_play")

    def test_unavailable_banned_version_excludes_song_and_conflicts_with_must_play(self):
        context = SetlistContext(
            must_play=(SongRequest("Dance (Clean)", "Singer"),),
            do_not_play=(SongRequest("Dance (Extended Mix)", "Singer"),),
        )
        result = rank_prep_crate(
            context, [track("Dance (Clean)", "Singer", file_id="clean")]
        )
        self.assertEqual(result.items, [])
        self.assertEqual(result.conflicting_must_play[0].reason, "do_not_play")
        self.assertEqual(result.do_not_play_diagnostics[0].identities,
                         (song_identity("Dance", "Singer"),))

    def test_title_only_ban_excludes_exact_title_across_artists_but_artist_ban_isolated(self):
        library = [
            track("The Song (Extended Mix)", "Artist One"),
            track("The Song (Clean)", "Artist Two"),
            track("The Song", "Artist Three"),
            track("The Song Reprise", "Artist Four"),
        ]
        title_only = rank_next_song(
            SetlistContext(do_not_play=(SongRequest("The Song (Extended Mix)"),)), library
        )
        self.assertEqual({item.song_identity.artist for item in title_only}, {"artist four"})
        diagnostic = title_only.do_not_play_diagnostics[0]
        self.assertTrue(diagnostic.artist_unspecified)
        self.assertEqual(len(diagnostic.identities), 3)

        artist_specific = rank_next_song(
            SetlistContext(do_not_play=(SongRequest("The Song (Extended Mix)", "Artist One"),)),
            library,
        )
        self.assertEqual({item.song_identity.artist for item in artist_specific},
                         {"artist two", "artist three", "artist four"})

    def test_ambiguous_must_play_is_not_guaranteed_and_retains_match_evidence(self):
        context = SetlistContext(must_play=(SongRequest("Shared Song"),))
        library = [
            track("Shared Song", "Artist A", file_id="a"),
            track("Shared Song", "Artist B", file_id="b"),
        ]
        result = rank_prep_crate(context, library)
        self.assertEqual(len(result.unresolved_must_play), 1)
        self.assertFalse(any("must-play" in reason.casefold()
                             for item in result for reason in item.reasons))
        diagnostic = result.resolution_diagnostics[0]
        self.assertEqual((diagnostic.category, diagnostic.status), ("must_play", "ambiguous"))
        self.assertIsNotNone(diagnostic.confidence)
        self.assertGreaterEqual(len(diagnostic.alternatives), 2)
        self.assertIn("_artist_sim", diagnostic.alternatives[0])

    def test_played_or_placed_must_plays_are_reported_as_conflicts(self):
        for field in ("already_played", "already_placed"):
            context = SetlistContext(must_play=(SongRequest("Used", "Singer"),))
            setattr(context, field, frozenset({song_identity("Used", "Singer")}))
            result = rank_prep_crate(context, [track("Used", "Singer")])
            self.assertEqual(len(result.conflicting_must_play), 1)
            self.assertEqual(result.conflicting_must_play[0].reason, field)

    def test_prep_includes_all_resolvable_must_plays_and_keeps_other_candidates(self):
        library = [track(f"Song {index}", "Artist", path=f"/synthetic/{index}.mp3")
                   for index in range(4)]
        context = SetlistContext(must_play=(
            SongRequest("Song 1", "Artist"), SongRequest("Song 3", "Artist"),
        ))
        result = rank_prep_crate(context, library, limit=1)
        self.assertEqual(set(identities(result)), {
            song_identity("Song 1", "Artist"), song_identity("Song 3", "Artist"),
        })
        self.assertEqual(len(result), 2)
        expanded = rank_prep_crate(context, library, limit=3)
        self.assertEqual(len(expanded), 3)
        self.assertTrue(set(identities(result)).issubset(identities(expanded)))
        self.assertEqual(len(rank_prep_crate(context, library)), 4)

    def test_prep_capacity_overflow_is_explicit(self):
        library = [track(f"Must {index}", "Artist", file_id=str(index)) for index in range(3)]
        context = SetlistContext(must_play=tuple(
            SongRequest(f"Must {index}", "Artist") for index in range(3)
        ))
        result = rank_prep_crate(context, library, limit=1)
        self.assertEqual(len(result), 3)
        self.assertEqual(
            result.capacity_overflow,
            CapacityOverflow(
                requested_limit=1, required_must_play_count=3, resulting_count=3
            ),
        )

    def test_prep_reports_unresolved_must_play_without_inventing_a_track(self):
        context = SetlistContext(must_play=(
            SongRequest("Not Here", "Unknown"), SongRequest(None, "Artist"),
        ))
        result = rank_prep_crate(context, [track("Local", "Artist")])
        self.assertEqual(len(result.unresolved_must_play), 2)
        self.assertEqual([item["title"] for item in
                          [recommendation.track for recommendation in result]], ["Local"])

    def test_requested_guest_cue_memory_markov_and_crate_badges_are_explained(self):
        library = [
            track("Playing", "DJ", bpm=100, key="8A"),
            track("Requested", "Guest", bpm=100, key="8A", genre="Pop"),
        ]
        target = song_identity("Requested", "Guest")
        source = song_identity("Playing", "DJ")
        context = SetlistContext(
            explicit_phase="first_dance",
            must_play=(SongRequest("Requested", "Guest"),),
            play_if_possible=(SongRequest("Requested", "Guest"),),
            song_cues=(SongCue("First Dance", "Requested", "Guest"),),
            guest_requests=(SongRequest("Requested", "Guest"),),
            dj_memory_floor_fillers=frozenset({target}),
            played_next={source: {target: 10.0}},
            crate_identities=frozenset({target}),
        )
        result = rank_next_song(context, library, library[0], limit=10)
        recommendation = next(item for item in result if item.song_identity == target)
        self.assertEqual(recommendation.badges, ("requested", "customer", "in_crate"))
        for reason in (
            "Must-play request", "Play-if-possible request", "Song cue fits first dance",
            "Live guest request", "DJ-memory floor-filler", "Played-next transition memory",
        ):
            self.assertIn(reason, recommendation.reasons)
        self.assertTrue(recommendation.reasons)

    def test_guest_requests_affect_live_only_and_prep_is_identical(self):
        library = [track("Requested", "Guest")]
        request = SongRequest("Requested", "Guest")
        without_guest = SetlistContext()
        with_guest = SetlistContext(guest_requests=(request,))
        prep_without = rank_prep_crate(without_guest, library)
        prep_with = rank_prep_crate(with_guest, library)
        self.assertEqual(prep_without, prep_with)
        live_without = rank_next_song(without_guest, library)[0]
        live_with = rank_next_song(with_guest, library)[0]
        self.assertGreater(live_with.score, live_without.score)
        self.assertIn("customer", live_with.badges)
        self.assertIn("Live guest request", live_with.reasons)

    def test_do_not_play_prevents_guest_boost_and_reports_guest_conflict(self):
        context = SetlistContext(
            guest_requests=(SongRequest("Banned", "Guest"),),
            do_not_play=(SongRequest("Banned", "Guest"),),
        )
        result = rank_next_song(context, [track("Banned", "Guest")])
        self.assertEqual(len(result), 0)
        self.assertEqual(result.conflicting_requests[0].category, "guest_request")
        self.assertEqual(result.conflicting_requests[0].reason, "do_not_play")

    def test_live_positive_requests_select_exact_versions_and_report_unavailable(self):
        clean = track("Dance (Clean)", "Singer", file_id="clean")
        extended = track("Dance (Extended Mix)", "Singer", file_id="extended")
        for requested, expected in (
            (SongRequest("Dance", "Singer", version="Clean"), "clean"),
            (SongRequest("Dance", "Singer", version="Extended Mix"), "extended"),
        ):
            result = rank_next_song(
                SetlistContext(must_play=(requested,)), [extended, clean]
            )
            recommendation = next(item for item in result
                                  if item.song_identity == song_identity("Dance", "Singer"))
            self.assertEqual(recommendation.track["file_id"], expected)

        unavailable = SongRequest("Dance", "Singer", version="Radio Edit")
        result = rank_next_song(SetlistContext(must_play=(unavailable,)), [clean, extended])
        recommendation = next(item for item in result
                              if item.song_identity == song_identity("Dance", "Singer"))
        self.assertEqual(recommendation.track["file_id"], "clean")
        self.assertFalse(any("Must-play request" in reason for reason in recommendation.reasons))
        self.assertEqual(result.resolution_diagnostics[0].status, "version_unavailable")
        self.assertIn(result.resolution_diagnostics[0].match_status, {"matched", "ambiguous"})
        self.assertFalse(result.unresolved_must_play == [])

    def test_conflicting_versions_have_stable_selection_and_diagnostic(self):
        requests = (
            SongRequest("Dance", "Singer", version="Clean"),
            SongRequest("Dance", "Singer", version="Extended Mix"),
        )
        library = [
            track("Dance (Extended Mix)", "Singer", file_id="extended"),
            track("Dance (Clean)", "Singer", file_id="clean"),
        ]
        result = rank_next_song(SetlistContext(must_play=requests), library)
        recommendation = next(item for item in result
                              if item.song_identity == song_identity("Dance", "Singer"))
        self.assertEqual(recommendation.track["file_id"], "clean")
        self.assertEqual(len(result.version_conflicts), 1)
        conflict = result.version_conflicts[0]
        self.assertEqual(conflict.requested_versions, ("clean", "extended mix"))
        self.assertEqual(conflict.selected_version, "clean")
        self.assertTrue(any(item.reason == "version_conflict_not_selected"
                            for item in result.conflicting_requests))

    def test_relevant_cue_boost_is_deduplicated_across_event_sources(self):
        payload = {
            "music_lists": {"special_moments": [
                {"moment": "First Dance", "title": "Moment", "artist": "Singer"},
            ]},
            "planner": {"song_cues": [
                {"moment": "first_dance", "title": "Moment", "artist": "Singer"},
            ]},
            "event_timelines": {"items": [
                {"name": "First Dance", "kind": "First Dance",
                 "song": {"title": "Moment", "artist": "Singer"}},
            ]},
        }
        context = context_from_event(payload)
        self.assertEqual(len(context.song_cues), 3)
        result = rank_next_song(context, [track("Moment", "Singer")])
        self.assertEqual(result[0].score, SetlistSettings().song_cue_weight)
        self.assertEqual(result[0].reasons.count("Song cue fits first dance"), 1)

    def test_special_moment_song_default_and_per_song_moments_are_preserved(self):
        context = context_from_event({"music_lists": {"special_moments": [{
            "moment": "First Dance",
            "songs": [
                {"title": "Shared Default", "artist": "One"},
                {"title": "Song Override", "artist": "Two", "moment": "Entrance"},
            ],
        }]}})
        self.assertEqual(
            [(cue.moment, cue.title) for cue in context.song_cues],
            [("First Dance", "Shared Default"), ("Entrance", "Song Override")],
        )

    def test_timeline_songcue_metadata_is_not_a_song(self):
        context = context_from_event({
            "event_timelines": {"items": [{
                "name": "Fade notes", "kind": "songCue",
                "songCue": {"fade": 9, "version": "Extended", "seconds": 12},
            }]},
        })
        self.assertEqual(context.song_cues, ())
        self.assertEqual(context.event_timelines[0].song_cue,
                         {"fade": 9, "version": "Extended", "seconds": 12})
        self.assertIsNone(context.event_timelines[0].song)

    def test_songcue_kind_with_real_song_is_cue_but_metadata_only_is_not(self):
        context = context_from_event({"event_timelines": {"items": [
            {"name": "First Dance", "kind": "songCue",
             "song": {"title": "Actual Song", "artist": "Singer"},
             "songCue": {"fade": 9, "seconds": 12}},
            {"name": "Fade", "kind": "songCue",
             "songCue": {"version": "Extended", "seconds": 12}},
        ]}})
        self.assertEqual(len(context.song_cues), 1)
        self.assertEqual(context.song_cues[0].title, "Actual Song")
        self.assertEqual(context.song_cues[0].moment, "First Dance")
        self.assertEqual(context.song_cues[0].metadata, {})

    def test_timeline_song_and_kind_become_a_cue_but_retain_source_metadata(self):
        context = context_from_event({
            "event_timelines": {"items": [{
                "name": "Entrance", "kind": "Entrance",
                "song": {"title": "Walk In", "artist": "Band", "version": "Radio Edit",
                         "file_id": "event-song-4"},
            }]},
        })
        cue = context.song_cues[0]
        self.assertEqual((cue.moment, cue.title, cue.artist, cue.version),
                         ("Entrance", "Walk In", "Band", "Radio Edit"))
        self.assertEqual(cue.metadata, {"file_id": "event-song-4"})

    def test_old_new_event_shapes_status_mapping_and_nullable_inquiry(self):
        old = parse_event({"music_lists": {
            "must_play": [{"title": "A", "artist": "B"}],
            "play_if_possible": [], "do_not_play": [],
        }})
        self.assertIsNone(old.inquiry_id)
        self.assertFalse(old.locked)
        self.assertEqual(old.music_lists.must_play[0].title, "A")
        standalone = context_from_event({"inquiry_id": "  ", "event_date": "2026-11-06"})
        self.assertIsNone(standalone.inquiry_id)
        self.assertFalse(standalone.locked)

        confirmed = parse_event({"inquiry_id": "event-1",
                                 "planner_responses": {"status": "confirmed"}})
        locked = parse_event({"planner_responses": {"status": "LOCKED"}})
        explicit = parse_event({"locked": True, "planner_responses": {"status": "draft"}})
        self.assertTrue(confirmed.locked)
        self.assertTrue(locked.locked)
        self.assertTrue(explicit.locked)
        self.assertEqual(confirmed.planner.status, "confirmed")

    def test_live_guest_requests_are_separate_from_event_payload(self):
        payload = {"guest_requests": [{"title": "Ignored", "artist": "Payload"}]}
        context = context_from_event(payload, guest_requests=[{"title": "Live", "artist": "Guest"}])
        self.assertEqual([request.title for request in context.guest_requests], ["Live"])

    def test_planner_kinds_and_existing_timeline_names_map_to_phases(self):
        cases = {
            "Processional": "ceremony", "Entrance": "ceremony", "First Dance": "first_dance",
            "Parent Dances": "first_dance", "Cake": "dinner", "Last Dance": "open_dancing",
            "Ceremony": "ceremony", "Cocktail Hour": "cocktail", "Dinner": "dinner",
            "Open Dancing": "open_dancing", "Unmapped event": "generic",
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                explicit = rank_prep_crate(SetlistContext(), [], phase=label)
                self.assertEqual(explicit.phase, expected)
                timeline = context_from_event({"event_timelines": {"items": [
                    {"name": "Unmapped row", "kind": label},
                ]}})
                self.assertEqual(rank_next_song(timeline, []).phase, expected)

    def test_timed_phase_selection_and_source_order_fallback(self):
        event = context_from_event({"event_timelines": {"items": [
            {"name": "Ceremony", "start_time": "16:00", "end_time": "17:00"},
            {"name": "Cocktail Hour", "start_time": "17:00", "end_time": "18:00"},
            {"name": "Dinner", "start_time": "18:00", "end_time": "19:00"},
        ]}})
        self.assertEqual(rank_next_song(event, [], at="17:30").phase, "cocktail")
        untimed = context_from_event({"event_timelines": {"items": [
            {"name": "Notes"}, {"name": "Cocktail Hour"}, {"name": "Dinner"},
        ]}})
        self.assertEqual(rank_next_song(untimed, []).phase, "cocktail")

    def test_open_dancing_energy_stage_progresses_from_timeline_without_invented_times(self):
        context = context_from_event({"event_timelines": {"items": [
            {"name": "Open Dancing", "start_time": "20:00", "end_time": "22:00"},
        ]}})
        stages = []
        for at in ("20:20", "21:00", "21:45"):
            stages.append(select_phase(context, at).energy_stage)
        self.assertEqual(stages, ["warm_up", "peak", "last_dance"])
        untimed = context_from_event({"event_timelines": {"items": [{"name": "Open Dancing"}]}})
        self.assertIsNone(select_phase(untimed, "21:00").energy_stage)

    def test_opening_energy_target_ranks_stage_energy_without_transition_features(self):
        library = [
            track("Warm", "A", energy=0.4), track("Peak", "B", energy=0.82),
            track("Last", "C", energy=0.52),
        ]
        context = SetlistContext(explicit_phase="open_dancing", energy_stage="warm-up")
        self.assertEqual(rank_next_song(context, library)[0].track["title"], "Warm")
        context.energy_stage = "peak"
        self.assertEqual(rank_next_song(context, library)[0].track["title"], "Peak")
        context.energy_stage = "last-dance"
        self.assertEqual(rank_next_song(context, library)[0].track["title"], "Last")

    def test_energy_slope_rewards_phase_desired_direction(self):
        context = SetlistContext(explicit_phase="open_dancing", energy_stage="warm_up")
        playing = track("Current", "DJ", energy=0.38)
        candidates = [track("At Target", "A", energy=0.4), track("Rising", "B", energy=0.5)]
        result = rank_next_song(context, candidates, playing)
        self.assertEqual(result[0].track["title"], "Rising")
        self.assertTrue(any("energy target" in reason.casefold() for reason in result[0].reasons))

    def test_default_and_custom_bpm_window_are_score_terms_not_filters(self):
        now = track("Current", "DJ", bpm=100)
        candidate = track("Near", "A", bpm=109)
        context = SetlistContext()
        default = rank_next_song(context, [candidate], now)
        custom = rank_next_song(context, [candidate], now, settings=SetlistSettings(bpm_window=0.10))
        self.assertEqual(len(default), 1)
        self.assertEqual(default[0].score, 0)
        self.assertAlmostEqual(custom[0].score, SetlistSettings(bpm_window=0.10).bpm_weight * 0.1)
        self.assertIn("BPM outside preferred window; still eligible", default[0].reasons)

    def test_half_double_time_matching_is_opt_in(self):
        now = track("Current", "DJ", bpm=70)
        candidate = track("Double", "A", bpm=140)
        self.assertEqual(rank_next_song(SetlistContext(), [candidate], now)[0].score, 0)
        enabled = rank_next_song(SetlistContext(), [candidate], now,
                                 settings=SetlistSettings(half_double_time=True))
        self.assertEqual(enabled[0].score, SetlistSettings().bpm_weight)

    def test_played_next_markov_prior_orders_candidates_deterministically(self):
        source = song_identity("Now", "DJ")
        favorite = song_identity("Favorite", "A")
        context = SetlistContext(played_next={source: {
            favorite: 9, song_identity("Other", "B"): 1,
        }})
        library = [track("Other", "B"), track("Favorite", "A")]
        result = rank_next_song(context, library, track("Now", "DJ"))
        self.assertEqual(result[0].song_identity, favorite)
        self.assertIn("Played-next transition memory", result[0].reasons)
        reversed_result = rank_next_song(context, list(reversed(library)), track("Now", "DJ"))
        self.assertEqual(identities(result), identities(reversed_result))

    def test_deterministic_ties_and_returned_file_version_metadata(self):
        versioned = [
            track("Dance (Clean)", "Singer", path="/synthetic/clean.mp3", id="clean",
                  library_file_id="file-clean", bpm=100, genre="Pop"),
            track("Dance (Extended Mix)", "Singer", path="/synthetic/extended.mp3",
                  file_id="file-extended", version="Extended Mix", bpm=100, genre="Pop",
                  vendor={"crate": "source"}),
        ]
        request = SongRequest("Dance", "Singer", version="Extended Mix")
        result = rank_prep_crate(SetlistContext(must_play=(request,)), versioned)
        self.assertEqual(result[0].track["path"], "/synthetic/extended.mp3")
        self.assertEqual(result[0].track["file_id"], "file-extended")
        self.assertEqual(result[0].track["vendor"], {"crate": "source"})

        equal = [track("B", "Artist", path="/b.mp3"), track("A", "Artist", path="/a.mp3")]
        one = rank_next_song(SetlistContext(), equal)
        two = rank_next_song(SetlistContext(), list(reversed(equal)))
        self.assertEqual(identities(one), identities(two))

    def test_duplicate_file_selection_is_stable_under_reversed_and_shuffled_inputs(self):
        library = [
            track("Same (Clean)", "Singer", file_id="same", bpm=100, genre="Pop",
                  source={"z": 2, "a": 1}),
            track("Same (Clean)", "Singer", file_id="same", bpm=120, genre="Rock",
                  source={"a": 1, "z": 2}),
            track("Same (Extended Mix)", "Singer", file_id="extended", bpm=100),
        ]
        context = SetlistContext(must_play=(SongRequest("Same", "Singer", version="Clean"),))
        outputs = [
            rank_next_song(context, ordered)
            for ordered in (library, list(reversed(library)), [library[1], library[2], library[0]])
        ]
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0], outputs[2])

    def test_openers_do_not_receive_bpm_key_or_markov_transition_terms(self):
        source = song_identity("Current", "DJ")
        context = SetlistContext(played_next={source: {song_identity("Candidate", "A"): 1}})
        result = rank_next_song(context, [track("Candidate", "A", bpm=120, key="8A")])
        self.assertFalse(any("BPM" in reason or "Camelot" in reason or "transition" in reason
                             for reason in result[0].reasons))
        self.assertTrue(result[0].reasons)

    def test_missing_features_and_unknown_genres_are_neutral(self):
        self.assertEqual(GENRES, (
            "Pop", "Hip Hop", "R&B", "Rock", "Dance / EDM", "House", "Latin", "Country",
            "Throwback", "Slow / Ballad",
        ))
        playing = track("Current", "DJ", genre="Unlisted")
        candidate = track("Candidate", "A", genre="Unlisted")
        result = rank_next_song(SetlistContext(), [candidate], playing)
        self.assertEqual(result[0].score, 0)
        self.assertFalse(any("Genre" in reason for reason in result[0].reasons))
        self.assertTrue(result[0].reasons)

    def test_known_matching_genre_scores_continuity(self):
        playing = track("Current", "DJ", genre="House")
        same = rank_next_song(SetlistContext(), [track("Same", "A", genre="House")], playing)[0]
        changed = rank_next_song(SetlistContext(), [track("Changed", "B", genre="Rock")], playing)[0]
        self.assertGreater(same.score, changed.score)
        self.assertIn("Genre continuity: House", same.reasons)

    def test_camelot_scoring_handles_same_neighbor_and_relative_keys(self):
        playing = track("Current", "DJ", key="8A")
        same = rank_next_song(SetlistContext(), [track("Same", "A", key="8A")], playing)[0]
        relative = rank_next_song(SetlistContext(), [track("Relative", "A", key="8B")], playing)[0]
        neighbor = rank_next_song(SetlistContext(), [track("Neighbor", "A", key="9A")], playing)[0]
        self.assertGreater(same.score, relative.score)
        self.assertGreater(relative.score, 0)
        self.assertGreater(neighbor.score, 0)

    def test_both_apis_are_pure_and_never_call_serato_or_event_writers(self):
        context = SetlistContext()
        library = [track("Only Local", "Artist", path="/synthetic/local.mp3")]
        with patch.object(SeratoAdapter, "write_crate", side_effect=AssertionError("crate write")), \
                patch.object(event_sync, "write_event_crates",
                             side_effect=AssertionError("event crate write")), \
                patch("library.tag_gate.tag_write", side_effect=AssertionError("tag write")), \
                patch.object(library_cli, "tag_write",
                             side_effect=AssertionError("CLI tag write")), \
                patch.object(serato_adapter_module.serato_writer, "store_serato_frames",
                             side_effect=AssertionError("frame write")), \
                patch.object(serato_adapter_module.serato_writer, "write_mp3_pads",
                             side_effect=AssertionError("pad write")):
            self.assertEqual(len(rank_next_song(context, library)), 1)
            self.assertEqual(len(rank_prep_crate(context, library)), 1)


if __name__ == "__main__":
    unittest.main()
