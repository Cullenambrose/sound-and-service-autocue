import json
import plistlib
import tempfile
import unittest
from pathlib import Path
from release_preflight import digest, inspect


class ReleasePreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Path(self.temp.name) / 'Synthetic.app'
        self.contents = self.app / 'Contents'
        self.resources = self.contents / 'Resources'
        for name in ('MacOS', 'Resources/backend'):
            (self.contents / name).mkdir(parents=True)
        (self.contents / 'Info.plist').write_bytes(plistlib.dumps({
            'CFBundleExecutable': 'Synthetic', 'CFBundleIdentifier': 'local.synthetic.development',
            'CFBundlePackageType': 'APPL', 'CFBundleShortVersionString': '1.0', 'CFBundleVersion': '1'}))
        for path in (self.contents / 'MacOS/Synthetic', self.resources / 'backend/studio-backend', self.resources / 'notice', self.resources / 'sbom'):
            path.write_bytes(b'synthetic fixture only')
        self.inventory = {'native_binaries': [{'path': 'Resources/backend/studio-backend', 'sha256': digest(self.resources / 'backend/studio-backend')}],
                          'components': [{'notices': [{'path': 'notice', 'sha256': digest(self.resources / 'notice')}]}],
                          'sbom': {'path': 'sbom', 'sha256': digest(self.resources / 'sbom')}, 'commercial_rights_cleared': False}
        (self.resources / 'release-inventory.json').write_text(json.dumps(self.inventory))

    def test_development_inventory_passes(self):
        self.assertTrue(inspect(self.app)['passed'])

    def test_tampered_backend_fails(self):
        (self.resources / 'backend/studio-backend').write_bytes(b'changed')
        self.assertFalse(inspect(self.app)['passed'])

    def test_missing_notices_fail(self):
        (self.resources / 'notice').unlink()
        self.assertFalse(inspect(self.app)['passed'])

    def test_unsigned_development_cannot_be_distributed(self):
        report = inspect(self.app, True, run=lambda args: (1, ''))
        self.assertFalse(report['passed'])
        self.assertGreaterEqual(sum(not check['passed'] for check in report['checks']), 6)

    def test_signature_success_does_not_clear_rights(self):
        report = inspect(self.app, True, run=lambda args: (0, 'Authority=Developer ID Application: Synthetic\nflags=runtime'))
        self.assertFalse(report['passed'])
        self.assertFalse(next(c['passed'] for c in report['checks'] if c['check'] == 'recorded dependency rights clearance'))

    def test_escaping_symlink_fails(self):
        (self.resources / 'escape').symlink_to(Path(self.temp.name))
        self.assertFalse(inspect(self.app)['passed'])

    def test_build_fingerprint_changes_with_content(self):
        before = inspect(self.app)['artifact_sha256']
        (self.contents / 'MacOS/Synthetic').write_bytes(b'changed')
        self.assertNotEqual(before, inspect(self.app)['artifact_sha256'])

    def test_invalid_bundle_fails_without_commands(self):
        (self.contents / 'Info.plist').unlink()
        self.assertFalse(inspect(self.app, True, run=lambda args: self.fail('command should not run'))['passed'])
