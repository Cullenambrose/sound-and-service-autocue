from email.message import Message
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.build_macos_app import release_inventory


class ReleaseInventoryTests(unittest.TestCase):
    def fixture(self, root, *, missing=None, unsafe=None):
        distributions = {}
        for name in ("numpy", "scipy", "mutagen", "pyinstaller"):
            directory = root / name
            directory.mkdir()
            license = directory / "LICENSE.txt"
            license.write_text(f"Synthetic {name} license fixture\n")
            metadata = Message()
            if name == "mutagen":
                metadata["License-Expression"] = "GPL-2.0-or-later"
            metadata["Classifier"] = "License :: Synthetic license fixture"
            distributions[name] = SimpleNamespace(version="0.test", metadata=metadata,
                files=[] if missing == name else [Path("../COPYING") if unsafe == name else Path("LICENSE.txt")],
                locate_file=lambda path, directory=directory: directory / path)
        runtime = root / "stdlib"
        runtime.mkdir()
        (runtime / "LICENSE.txt").write_text("Synthetic Python license fixture\n")
        contents = root / "app" / "Contents"
        (contents / "Resources" / "backend").mkdir(parents=True)
        (contents / "MacOS").mkdir()
        (contents / "Resources" / "backend" / "vendor.dylib").write_bytes(b"synthetic native library")
        (contents / "MacOS" / "AutoCueStudio").write_bytes(b"synthetic shell")
        return contents, distributions, runtime

    def test_exact_notices_and_native_hashes_ship_without_rights_clearance_or_private_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contents, distributions, runtime = self.fixture(root)
            with patch("scripts.build_macos_app.distribution", side_effect=distributions.__getitem__), \
                    patch("scripts.build_macos_app.sysconfig.get_path", return_value=str(runtime)):
                report = release_inventory(contents)
            self.assertFalse(report["commercial_rights_cleared"])
            self.assertEqual(report["bundled_models"], [])
            self.assertEqual(len(report["native_binaries"]), 2)
            self.assertEqual(len(report["components"]), 5)
            self.assertEqual(report["components"][2]["license_expression"], "GPL-2.0-or-later")
            resources = contents / "Resources"
            for component in report["components"]:
                for notice in component["notices"]:
                    data = (resources / notice["path"]).read_bytes()
                    self.assertEqual(hashlib.sha256(data).hexdigest(), notice["sha256"])
            text = (resources / "release-inventory.json").read_text()
            self.assertNotIn(str(root), text)
            self.assertEqual(json.loads(text), report)

    def test_missing_or_unsafe_notices_block_package_completion(self):
        for options in ({"missing": "mutagen"}, {"unsafe": "numpy"}):
            with tempfile.TemporaryDirectory() as directory:
                contents, distributions, runtime = self.fixture(Path(directory), **options)
                with patch("scripts.build_macos_app.distribution", side_effect=distributions.__getitem__), \
                        patch("scripts.build_macos_app.sysconfig.get_path", return_value=str(runtime)), \
                        self.assertRaises((RuntimeError, ValueError)):
                    release_inventory(contents)
                self.assertFalse((contents / "Resources" / "release-inventory.json").exists())


if __name__ == "__main__":
    unittest.main()
