"""Compile the production POSIX path helper; compare with independent OS resolution."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import unicodedata


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'Requires macOS Swift')
class NativePathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='booth-native-path-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name).resolve()
        source = Path(__file__).with_name('StudioApp.swift').read_text()
        helper = source[source.index('// Foundation shortens'):source.index('// Branded foreground')]
        swift = cls.root / 'main.swift'
        swift.write_text('import Foundation\nimport Darwin\n' + helper + '\ndo { print(try canonicalNativeFileURL(URL(fileURLWithPath: CommandLine.arguments[1])).path) } catch { exit(1) }\n')
        cls.binary = cls.root / 'canonical'
        built = subprocess.run(['xcrun', 'swiftc', '-module-cache-path', str(cls.root / 'modules'), str(swift), '-o', str(cls.binary)], capture_output=True, text=True, timeout=120)
        if built.returncode:
            raise AssertionError(built.stderr)

    def resolved(self, path):
        return subprocess.check_output([str(self.binary), str(path)], text=True).strip()

    def test_private_tmp_spelling_matches_backend(self):
        self.assertEqual(self.resolved('/tmp'), str(Path('/tmp').resolve()))
        self.assertEqual(self.resolved('/private/tmp'), str(Path('/private/tmp').resolve()))

    def test_new_nested_destination_is_canonical(self):
        path = self.root / 'new-folder' / 'child'
        self.assertEqual(self.resolved(path), str(path.resolve()))
        self.assertFalse(path.exists())

    def test_symlinked_parent_resolves_before_new_child(self):
        link = self.root / 'alias'
        link.symlink_to(self.root, target_is_directory=True)
        path = link / 'new-folder'
        self.assertEqual(self.resolved(path), str(path.resolve()))

    def test_unicode_and_spaces_survive(self):
        path = self.root / 'Synthetic café workspace'
        path.mkdir()
        resolved = self.resolved(path)
        # macOS realpath can return a decomposed Unicode spelling for the same inode.
        self.assertEqual(unicodedata.normalize('NFC', resolved), unicodedata.normalize('NFC', str(path.resolve())))
        self.assertTrue(Path(resolved).samefile(path))
