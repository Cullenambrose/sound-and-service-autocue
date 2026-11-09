"""Acceptance checks for the unchanged original Desktop demo payloads."""
import copy
import json
from pathlib import Path
import sys
import unittest

ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from setlist import context_from_event, rank_next_song, rank_prep_crate, song_identity


FIXTURES = Path(__file__).with_name("fixtures")


class DemoAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library = json.loads((FIXTURES / "mock-library.json").read_text())["tracks"]
        cls.shuffled = json.loads((FIXTURES / "mock-library-shuffled.json").read_text())["tracks"]
        cls.event = json.loads((FIXTURES / "mock-wedding-event.json").read_text())
        cls.no_guests = json.loads((FIXTURES / "mock-event-no-guests.json").read_text())
        cls.guests = cls.event["_demo_live_inputs"]["guest_requests"]

    def context(self, guests=None):
        return context_from_event(self.event, guest_requests=self.guests if guests is None else guests,
                                  phase="first_dance")

    def result(self, ranker=rank_next_song, guests=None):
        return ranker(self.context(guests), self.library, limit=len(self.library))

    def diagnostic(self, result, request_id):
        return next(d for d in result.resolution_diagnostics if d.request.id == request_id)

    def item(self, result, track_id):
        return next(item for item in result if item.track["id"] == track_id)

    def test_01_timeline_cues(self):
        cues = self.context().song_cues
        self.assertEqual([(c.moment, c.title) for c in cues if c.moment != "First Dance"], [
            ("processional", "Golden Aisle"), ("entrance", "Here We Come"),
            ("parent_dance", "Porch Light"), ("parent_dance", "Mama's Waltz"),
            ("cake", "Sugar Frosting"), ("last_dance", "Last Call Lullaby"),
        ])

    def test_02_first_dance(self):
        cue = next(c for c in self.context().song_cues if c.moment == "First Dance")
        self.assertEqual((cue.title, cue.version, cue.metadata), ("Slow Orbit", "Short Edit", {}))
        for ranker in (rank_prep_crate, rank_next_song):
            item = self.item(self.result(ranker), "trk-004")
            self.assertIn("Song cue fits first dance", item.reasons)
            self.assertIn("requested", item.badges)

    def test_03_metadata_only_row(self):
        context = self.context()
        row = next(r for r in context.event_timelines if r.name == "Fade Note")
        self.assertIsNone(row.song)
        self.assertEqual(row.song_cue, {"version": "Extended", "seconds": 12})
        self.assertFalse(any(c.moment == "Fade Note" for c in context.song_cues))

    def test_04_must_plays(self):
        expected = {"mp-1": "trk-024", "mp-2": "trk-025", "mp-3": "trk-033",
                    "mp-4": "trk-038", "mp-5": "trk-039", "mp-6": "trk-040",
                    "mp-8": "trk-013"}
        for ranker in (rank_prep_crate, rank_next_song):
            result = self.result(ranker)
            for request_id, track_id in expected.items():
                with self.subTest(ranker=ranker.__name__, request=request_id):
                    self.assertEqual(self.diagnostic(result, request_id).status, "matched")
                    self.assertIn("Must-play request", self.item(result, track_id).reasons)
                    context = self.context()
                    context.must_play = tuple(r for r in context.must_play if r.id != request_id)
                    baseline = ranker(context, self.library, limit=len(self.library))
                    self.assertEqual(self.item(result, track_id).score
                                     - self.item(baseline, track_id).score, 100)

    def test_05_ban_over_must_play(self):
        for ranker in (rank_prep_crate, rank_next_song):
            result = self.result(ranker)
            self.assertNotIn("trk-019", [i.track["id"] for i in result])
            self.assertTrue(any(c.request.id == "mp-7" and c.reason == "do_not_play"
                                for c in result.conflicting_must_play))

    def test_06_title_only_ban(self):
        result = self.result()
        ids = {i.track["id"] for i in result}
        self.assertFalse(ids.intersection({"trk-016", "trk-017"}))
        self.assertIn("trk-018", ids)
        self.assertTrue(any(c.request.id == "g-4" and c.reason == "do_not_play"
                            for c in result.conflicting_requests))
        self.assertIn("Live guest request", self.item(result, "trk-018").reasons)

    def test_07_other_bans(self):
        for ranker in (rank_prep_crate, rank_next_song):
            self.assertFalse({"trk-020", "trk-021"}.intersection(
                i.track["id"] for i in self.result(ranker)))

    def test_08_play_if_possible(self):
        for ranker in (rank_prep_crate, rank_next_song):
            result = self.result(ranker, guests=[])
            for request_id, track_id in (("pip-1", "trk-012"), ("pip-2", "trk-045"),
                                         ("pip-3", "trk-048"), ("pip-4", "trk-051")):
                self.assertEqual(self.diagnostic(result, request_id).status, "matched")
                item = self.item(result, track_id)
                self.assertIn("Play-if-possible request", item.reasons)
                context = self.context(guests=[])
                context.play_if_possible = tuple(r for r in context.play_if_possible
                                                 if r.id != request_id)
                baseline = ranker(context, self.library, limit=len(self.library))
                other = next(i for i in baseline if i.song_identity == item.song_identity)
                self.assertEqual(item.score - other.score, 30)

    def test_09_missing_song(self):
        for ranker in (rank_prep_crate, rank_next_song):
            diagnostic = self.diagnostic(self.result(ranker), "pip-5")
            self.assertEqual(diagnostic.status, "missing")
            self.assertIsNone(diagnostic.identity)

    def test_10_unavailable_version(self):
        guest = next(g for g in self.guests if g["id"] == "g-1")
        result = self.result(guests=[guest])
        diagnostic = self.diagnostic(result, "g-1")
        self.assertEqual((diagnostic.status, diagnostic.identity),
                         ("version_unavailable", song_identity("Midnight Signal", "Kilo Avenue")))
        self.assertIsNotNone(diagnostic.confidence)
        self.assertTrue(diagnostic.alternatives)
        self.assertEqual(result.items, self.result(guests=[]).items)

    def test_11_version_conflict(self):
        result = self.result()
        item = self.item(result, "trk-012")
        self.assertNotIn("Live guest request", item.reasons)
        self.assertNotIn("customer", item.badges)
        self.assertTrue(any(c.request.id == "g-2" and c.reason == "version_conflict_not_selected"
                            for c in result.conflicting_requests))
        conflict = next(c for c in result.version_conflicts if c.identity == item.song_identity)
        self.assertEqual(conflict.selected_version, "extended mix")

    def test_12_decoy_same_artist(self):
        result = self.result()
        self.assertIn("Must-play request", self.item(result, "trk-013").reasons)
        self.assertIn("Live guest request", self.item(result, "trk-014").reasons)
        self.assertNotIn("Must-play request", self.item(result, "trk-014").reasons)
        self.assertNotIn("requested", self.item(result, "trk-015").badges)
        self.assertEqual(self.diagnostic(result, "g-3").identity,
                         song_identity("Shine On Me", "Velvet Coast"))
        self.assertEqual(self.diagnostic(result, "g-3").status, "matched")

    def test_13_live_only_and_shuffle(self):
        for ranker in (rank_prep_crate, rank_next_song):
            result = self.result(ranker)
            self.assertEqual(result, ranker(self.context(), self.shuffled, limit=len(self.library)))
            no_guests = ranker(context_from_event(self.no_guests, phase="first_dance"),
                               self.library, limit=len(self.library))
            if ranker is rank_prep_crate:
                self.assertEqual(result, no_guests)
        self.assertIn("Live guest request", self.item(self.result(), "trk-032").reasons)

    def test_14_lock_status(self):
        self.assertFalse(self.context().locked)
        event = copy.deepcopy(self.event)
        event["planner"]["status"] = "confirmed"
        self.assertTrue(context_from_event(event).locked)

    def test_15_available_guest_version(self):
        result = self.result()
        self.assertEqual(self.diagnostic(result, "g-7").status, "matched")
        item = self.item(result, "trk-010")
        self.assertIn("Live guest request", item.reasons)
        self.assertIn("customer", item.badges)

    def test_16_guest_ban(self):
        result = self.result()
        self.assertNotIn("trk-020", [i.track["id"] for i in result])
        self.assertTrue(any(c.request.id == "g-8" and c.category == "guest_request"
                            and c.reason == "do_not_play" for c in result.conflicting_requests))

    def assert_ambiguous_without_fulfillment(self, request_id, track_ids):
        guest = next(g for g in self.guests if g["id"] == request_id)
        result = self.result(guests=[guest])
        diagnostic = self.diagnostic(result, request_id)
        self.assertEqual((diagnostic.status, diagnostic.match_status, diagnostic.confidence),
                         ("ambiguous", "ambiguous", 0.6))
        self.assertIsNone(diagnostic.identity)
        self.assertGreaterEqual(len(diagnostic.alternatives), 2)
        baseline = self.result(guests=[])
        for track_id in track_ids:
            item = self.item(result, track_id)
            self.assertEqual(item, self.item(baseline, track_id))
            self.assertNotIn("customer", item.badges)
            self.assertNotIn("requested", item.badges)
            self.assertNotIn("Live guest request", item.reasons)

    def test_17_ambiguous_version_identity(self):
        self.assert_ambiguous_without_fulfillment("g-9", ("trk-022", "trk-023"))

    def test_18_bare_title_ambiguity(self):
        self.assert_ambiguous_without_fulfillment("g-10", ("trk-063", "trk-064"))


if __name__ == "__main__":
    unittest.main()
