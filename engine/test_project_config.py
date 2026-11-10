import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from project_config import project_path, research_root


class ConfigurationTests(unittest.TestCase):
    def test_relative_paths_use_project_location_and_environment_wins(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            root = Path(directory).resolve()
            config = root / "project.json"
            config.write_text(json.dumps({"data_dir": "local-data", "research_root": "research"}))
            os.environ["AUTOCUE_PROJECT"] = str(config)
            self.assertEqual(project_path("data_dir", "AUTOCUE_WEB_DATA"), root / "local-data")
            self.assertEqual(research_root(), root / "research")
            os.environ["AUTOCUE_WEB_DATA"] = str(root / "override")
            self.assertEqual(project_path("data_dir", "AUTOCUE_WEB_DATA"), root / "override")

    def test_missing_research_root_and_invalid_config_fail_explicitly(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "AUTOCUE_RESEARCH_ROOT"):
                research_root()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            config = Path(directory) / "invalid.json"
            os.environ["AUTOCUE_PROJECT"] = str(config)
            for value in ([], {"cache_dir": ""}, {"cache_dir": 12}):
                config.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    project_path("cache_dir", "AUTOCUE_CACHE_DIR")
