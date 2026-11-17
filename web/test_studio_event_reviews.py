import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from web import catalog, studio_event_reviews, studio_service


class StudioEventReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db = sqlite3.connect(Path(self.temporary.name) / "studio.sqlite3")
        self.db.row_factory = sqlite3.Row
        studio_service.initialize(self.db)
        studio_event_reviews.initialize(self.db)
        self.dataset = "review-dataset"
        self.body = {
            "request_id": "review-request", "dataset_id": self.dataset,
            "crate_id": "studio-planned-set", "assessment": "worked",
            "note": "The energy arc fit this event.", "event_ids": ["play-1", "play-2"],
        }
        self.planned = {"creation": {"planning": {"alternative": {"tracks": [{"id": "file-1"}]}}}}
        self.observation = {"event": {"request_id": "play-1", "dataset_id": self.dataset},
                            "removal": None}

    def tearDown(self):
        self.db.close()
        self.temporary.cleanup()

    def save(self, body=None, *, evidence=None, observation=None):
        with patch.object(studio_service, "crate_evidence",
                          return_value=evidence or self.planned), \
                patch("web.studio_history._event",
                      return_value=observation or self.observation):
            with self.db:
                return studio_event_reviews.save(
                    self.db, body or self.body, self.dataset
                )

    def test_saves_event_assessment_separately_and_replays_idempotently(self):
        receipt = self.save()
        self.assertEqual(receipt["assessment"], "worked")
        self.assertEqual(receipt["event_ids"], ["play-1", "play-2"])
        self.assertEqual(self.save(), receipt)
        history = studio_event_reviews.history(self.db, self.dataset)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["note"], self.body["note"])
        self.assertEqual(history[0]["crate_id"], self.body["crate_id"])

    def test_rejects_unplanned_sets_invalid_assessment_and_removed_observations(self):
        with self.assertRaisesRegex(ValueError, "no saved planned set"):
            self.save(evidence={"creation": {"planning": None}})
        with self.assertRaisesRegex(ValueError, "assessment must be"):
            self.save({**self.body, "assessment": "crowd_loved_it"})
        with self.assertRaisesRegex(catalog.Conflict, "removed"):
            self.save(observation={**self.observation,
                                   "removal": {"active": 1}})

    def test_request_id_cannot_be_reused_for_another_assessment(self):
        self.save()
        with self.assertRaisesRegex(catalog.Conflict, "different content"):
            self.save({**self.body, "assessment": "mixed"})


if __name__ == "__main__":
    unittest.main()
