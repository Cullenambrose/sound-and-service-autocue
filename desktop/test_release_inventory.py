from email.message import Message
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.build_macos_app import build_backend, release_inventory


class ReleaseInventoryTests(unittest.TestCase):
    def test_failed_freeze_preserves_diagnostics_and_uses_private_work_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            contents = Path(directory) / "Fixture.app" / "Contents"
            contents.mkdir(parents=True)
            installed = SimpleNamespace(locate_file=lambda name: Path(directory) / name)
            diagnostic = io.StringIO()
            with patch("scripts.build_macos_app.platform.machine", return_value="arm64"), \
                    patch("scripts.build_macos_app.sys.platform", "darwin"), \
                    patch("scripts.build_macos_app.distribution", return_value=installed), \
                    patch("scripts.build_macos_app.subprocess.run", return_value=SimpleNamespace(returncode=2, stdout="Synthetic freeze failure detail")) as run, \
                    patch("scripts.build_macos_app.sys.stderr", diagnostic), \
                    patch("scripts.build_macos_app.release_inventory") as inventory:
                with self.assertRaises(SystemExit) as failure:
                    build_backend(contents)
                self.assertEqual(failure.exception.code, 2)
                self.assertIn("Synthetic freeze failure detail", diagnostic.getvalue())
                self.assertEqual(Path(run.call_args.kwargs["env"]["PYINSTALLER_CONFIG_DIR"]).parent, run.call_args.kwargs["cwd"])
                inventory.assert_not_called()

    def fixture(self, root, *, missing=None, unsafe=None):
        distributions = {}
        for name in ("numpy", "scipy", "mutagen", "pyinstaller"):
            directory = root / name
            directory.mkdir()
            license = directory / "LICENSE.txt"
            license.write_text(f"Synthetic {name} license fixture\n")
            metadata = Message()
            metadata["Name"] = name
            if name == "mutagen":
                metadata["License-Expression"] = "GPL-2.0-or-later"
            metadata["Classifier"] = "License :: Synthetic license fixture"
            if name == "numpy":
                metadata.add_header("Requires-Dist", "synthetic-base>=1")
            if name == "pyinstaller":
                metadata.add_header("Requires-Dist", "synthetic-builddep>=1")
                metadata.add_header("Requires-Dist", 'skipped-package; python_version < "3"')
            distributions[name] = SimpleNamespace(version="0.test", metadata=metadata,
                requires=metadata.get_all("Requires-Dist", []),
                files=[] if missing == name else [Path("../COPYING") if unsafe == name else Path("LICENSE.txt")],
                locate_file=lambda path, directory=directory: directory / path)
        for name, requirements in (
            ("sound-service-autocue", ["numpy>=1", "scipy>=1", "mutagen>=1"]),
            ("synthetic-base", ["synthetic-leaf>=1"]),
            ("synthetic-leaf", []),
            ("synthetic-builddep", []),
        ):
            metadata = Message()
            metadata["Name"] = name
            for requirement in requirements:
                metadata.add_header("Requires-Dist", requirement)
            distributions[name] = SimpleNamespace(version="0.test", metadata=metadata,
                requires=metadata.get_all("Requires-Dist", []), files=[])
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
            sbom_path = resources / report["sbom"]["path"]
            self.assertEqual(hashlib.sha256(sbom_path.read_bytes()).hexdigest(), report["sbom"]["sha256"])
            sbom = json.loads(sbom_path.read_text())
            self.assertEqual(sbom["spdxVersion"], "SPDX-2.3")
            package_names = {package["name"] for package in sbom["packages"]}
            self.assertTrue({"AutoCue Studio", "CPython", "synthetic-base", "synthetic-leaf",
                             "synthetic-builddep"} <= package_names)
            self.assertNotIn("skipped-package", package_names)
            self.assertIn("not a complete shipped-binary SBOM", sbom["comment"])
            package_ids = {package["SPDXID"] for package in sbom["packages"]}
            package_ids.add(sbom["SPDXID"])
            relationships = {
                (item["spdxElementId"], item["relationshipType"], item["relatedSpdxElement"])
                for item in sbom["relationships"]
            }
            self.assertIn(("SPDXRef-AutoCueStudio", "DEPENDS_ON", "SPDXRef-Package-numpy"),
                          relationships)
            self.assertIn(("SPDXRef-Package-numpy", "DEPENDS_ON", "SPDXRef-Package-synthetic-base"),
                          relationships)
            for relationship in sbom["relationships"]:
                self.assertIn(relationship["spdxElementId"], package_ids)
                self.assertIn(relationship["relatedSpdxElement"], package_ids)
            for component in report["components"]:
                for notice in component["notices"]:
                    data = (resources / notice["path"]).read_bytes()
                    self.assertEqual(hashlib.sha256(data).hexdigest(), notice["sha256"])
            text = (resources / "release-inventory.json").read_text()
            self.assertNotIn(str(root), text)
            self.assertEqual(json.loads(text), report)
            self.assertIn("not a complete shipped-binary SBOM", report["scope"])

    def test_missing_or_unsafe_notices_block_package_completion(self):
        for options in ({"missing": "mutagen"}, {"unsafe": "numpy"}):
            with tempfile.TemporaryDirectory() as directory:
                contents, distributions, runtime = self.fixture(Path(directory), **options)
                with patch("scripts.build_macos_app.distribution", side_effect=distributions.__getitem__), \
                        patch("scripts.build_macos_app.sysconfig.get_path", return_value=str(runtime)), \
                        self.assertRaises((RuntimeError, ValueError)):
                    release_inventory(contents)
                self.assertFalse((contents / "Resources" / "release-inventory.json").exists())

    def test_xcode_inventory_excludes_shell_signed_after_packaging(self):
        with tempfile.TemporaryDirectory() as directory:
            contents, distributions, runtime = self.fixture(Path(directory))
            with patch("scripts.build_macos_app.distribution", side_effect=distributions.__getitem__), \
                    patch("scripts.build_macos_app.sysconfig.get_path", return_value=str(runtime)):
                report = release_inventory(contents, exclude_macos=True)
            self.assertEqual([item["path"] for item in report["native_binaries"]],
                             ["Resources/backend/vendor.dylib"])
            self.assertIn("Xcode signs them after", report["scope"])


if __name__ == "__main__":
    unittest.main()
