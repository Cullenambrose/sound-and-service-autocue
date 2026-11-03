import concurrent.futures
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gemini_assist as assist
import gemini_config as config


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.location = patch.object(config, "ROOT", self.root)
        self.location.start()

    def tearDown(self):
        self.location.stop()
        self.environment.stop()
        self.directory.cleanup()

    def test_file_precedence_quotes_comments_and_environment(self):
        (self.root / ".env").write_text('GEMINI_API_KEY="base-value"\nGEM_MODEL=gemini-3.1-pro-preview\n')
        (self.root / ".env.local").write_text('export GEMINI_API_KEY="local-value" # comment\n')
        self.assertEqual(config.configuration()["key"], "local-value")
        self.assertEqual(config.status()["key_source"], ".env.local")
        os.environ["GEMINI_API_KEY"] = "environment-value"
        os.environ["GEM_MODEL"] = "models/gemini-3.1-pro-preview"
        self.assertEqual(assist.load_api_key(), "environment-value")
        self.assertEqual(config.status()["key_source"], "environment")
        self.assertEqual(assist.model_name(), "gemini-3.1-pro-preview")
        rendered = json.dumps(assist.configuration_status())
        for value in ("base-value", "local-value", "environment-value"):
            self.assertNotIn(value, rendered)
        self.assertFalse(config.status()["access_verified"])

    def test_explicit_file_is_literal_and_never_executed(self):
        path = self.root / "local.conf"
        path.write_text('GEMINI_API_KEY="$(touch /tmp/not-executed)"\n')
        os.environ["AUTOCUE_ENV_FILE"] = str(path)
        self.assertEqual(assist.load_api_key(), "$(touch /tmp/not-executed)")
        self.assertEqual(config.status()["key_source"], "AUTOCUE_ENV_FILE")

    def test_invalid_or_missing_configuration_is_actionable_without_secrets(self):
        path = self.root / ".env.local"
        path.write_text('GEMINI_API_KEY="do-not-print\n')
        self.assertIn("syntax", config.status()["error"])
        self.assertNotIn("do-not-print", json.dumps(config.status()))
        path.unlink()
        os.environ["AUTOCUE_ENV_FILE"] = str(path)
        self.assertIn("error", config.status())
        os.environ.pop("AUTOCUE_ENV_FILE")
        os.environ["GEM_MODEL"] = "https://example.com/key"
        self.assertIn("error", config.status())


class SpendingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.cache = Path(self.directory.name)
        self.environment = patch.dict(os.environ, {"GEMINI_API_KEY": "fixture-key",
                                                   "GEM_MODEL": config.DEFAULT_MODEL}, clear=True)
        self.environment.start()
        self.location = patch.object(config, "ROOT", self.cache)
        self.location.start()

    def tearDown(self):
        self.location.stop()
        self.environment.stop()
        self.directory.cleanup()

    def request(self, context=None, cap=3, caller=None):
        return assist._answer(context or {}, "track", self.cache, cap,
                              caller or (lambda *_: ({"map": "DROP"}, 0.01)))

    def ledger(self):
        return json.loads((self.cache / "spending.ledger").read_text())

    def test_cache_reuse_serializes_concurrent_requests(self):
        calls = []
        def caller(*_):
            calls.append(1)
            return {"map": "DROP"}, 0.01
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.request(caller=caller), range(8)))
        self.assertEqual(len(calls), 1)
        self.assertEqual(sum(not result[2] for result in results), 1)
        self.assertEqual(self.ledger()["spent_usd"], 0.01)
        self.assertIsNone(self.ledger()["pending"])

    def test_preflight_cap_blocks_before_caller_and_accounts_legacy_cache(self):
        (self.cache / "old.json").write_text(json.dumps({"cost_usd": 2.99}))
        calls = []
        with self.assertRaisesRegex(RuntimeError, "spend cap"):
            self.request(caller=lambda *_: calls.append(1))
        self.assertEqual(calls, [])
        self.assertEqual(self.ledger()["spent_usd"], 2.99)

    def test_actual_overrun_is_recorded_and_blocks_further_spending(self):
        with self.assertRaisesRegex(assist.ResponseError, "spending blocked"):
            self.request(cap=0.1, caller=lambda *_: ({"map": "DROP"}, 0.2))
        self.assertEqual(self.ledger()["spent_usd"], 0.2)
        self.assertTrue(self.ledger()["blocked"])
        with self.assertRaisesRegex(RuntimeError, "spending blocked"):
            self.request({"different": True}, caller=lambda *_: self.fail("must not call"))

    def test_timeout_reservation_survives_restart_until_provider_reconciliation(self):
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            self.request(caller=lambda *_: (_ for _ in ()).throw(TimeoutError("timeout")))
        self.assertIsNotNone(self.ledger()["pending"])
        with self.assertRaisesRegex(RuntimeError, "unreconciled"):
            self.request({"different": True}, caller=lambda *_: self.fail("must not retry"))
        status = assist.reconcile_pending(self.cache, 0.005)
        self.assertEqual(status["spent_usd"], 0.005)
        self.assertFalse(status["pending_reconciliation"])
        self.assertEqual(len(self.ledger()["reconciliations"]), 1)
        self.request()
        self.assertEqual(self.ledger()["spent_usd"], 0.015)

    def test_corrupt_cache_and_ledger_fail_closed(self):
        path = self.cache / "old.json"
        path.write_text("not json")
        with self.assertRaisesRegex(RuntimeError, "accounting"):
            self.request(caller=lambda *_: self.fail("must not call"))
        path.unlink()
        (self.cache / "spending.ledger").write_text('{"version": 1}')
        with self.assertRaisesRegex(RuntimeError, "ledger"):
            self.request(caller=lambda *_: self.fail("must not call"))

    def test_prompt_version_and_settings_change_cache_identity(self):
        self.assertFalse(self.request()[2])
        self.assertTrue(self.request()[2])
        with patch.object(assist, "RESPONSE_VERSION", 999):
            self.assertFalse(self.request()[2])
        self.assertFalse(self.request({"mix_lengths": {"intro": [16, 8, 4]}})[2])
        self.assertAlmostEqual(self.ledger()["spent_usd"], 0.03)
        self.assertNotIn("fixture-key", "".join(p.read_text() for p in self.cache.glob("*.json")))

    def test_unknown_pricing_and_oversized_context_do_not_make_requests(self):
        with patch.dict(os.environ, {"GEM_MODEL": "unknown-model"}):
            with self.assertRaisesRegex(ValueError, "pricing"):
                self.request(caller=lambda *_: self.fail("must not call"))
        with self.assertRaisesRegex(ValueError, "bounded text"):
            self.request({"text": "x" * assist.MAX_PROMPT_BYTES})

    def test_known_usage_of_invalid_json_is_charged_and_cached_without_retry(self):
        calls = []
        def caller(*_):
            calls.append(1)
            return assist._parse_response({
                "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 10},
                "candidates": [{"content": {"parts": [{"text": "truncated {"}]}}]})
        for _ in range(2):
            with self.assertRaisesRegex(assist.ResponseError, "truncated"):
                self.request(caller=caller)
        self.assertEqual(len(calls), 1)
        self.assertAlmostEqual(self.ledger()["spent_usd"], 0.00032)

    def test_cache_write_failure_retains_accounted_reservation(self):
        original = assist._atomic_json
        def write(path, record):
            if path.suffix == ".json":
                raise OSError("disk full")
            return original(path, record)
        with patch.object(assist, "_atomic_json", side_effect=write):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.request()
        self.assertEqual(self.ledger()["spent_usd"], 0.01)
        self.assertEqual(self.ledger()["pending"]["accounted_usd"], 0.01)
        self.assertAlmostEqual(assist.reconcile_pending(self.cache, 0.012)["spent_usd"], 0.012)

    def test_http_errors_are_actionable_without_echoing_provider_body_or_key(self):
        error = urllib.error.HTTPError("https://example.com/fixture-key", 403, "fixture-key", {}, io.BytesIO(b"secret"))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "HTTP 403") as raised:
                assist._call({}, "fixture-key")
        self.assertNotIn("fixture-key", str(raised.exception))

    def test_confident_local_plan_skips_cloud_and_context_exposes_units_not_audio(self):
        plan = {"map_type": "DROP", "grid": {"bpm": 120}, "cues": {
            "B": {"bar": 8, "confidence": 0.9, "candidate_bars": [7, 8, 9], "meaning": "start"}}}
        with patch.object(assist, "_answer", side_effect=AssertionError("must not request")):
            result = assist.assist(plan, None, "track", self.cache)
        self.assertFalse(result["gemini"]["used"])
        self.assertIn("No eligible", result["gemini"]["reason"])
        context = assist.build_context(plan)
        self.assertEqual(context["bpm"], 120)
        self.assertEqual(context["cues"]["B"]["meaning"], "start")
        self.assertIn("four beats", context["timing"]["units"])
        body = assist._request_body(context)
        self.assertEqual(body["generationConfig"]["thinkingConfig"]["thinkingLevel"], "low")
        self.assertIn("responseSchema", body["generationConfig"])


if __name__ == "__main__":
    unittest.main()
