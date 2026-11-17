#!/usr/bin/env python3
"""Read-only artifact checks. A successful build is not distribution clearance."""
import argparse
import hashlib
import json
import plistlib
import subprocess
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=90)
        return result.returncode, result.stdout + result.stderr
    except (OSError, subprocess.TimeoutExpired):
        return -1, ''


def inspect(app, distribution=False, run=command):
    app = Path(app).resolve()
    checks = []
    def check(name, passed):
        checks.append({'check': name, 'passed': bool(passed)})
    contents = app / 'Contents'
    try:
        info = plistlib.loads((contents / 'Info.plist').read_bytes())
        executable = info['CFBundleExecutable']
        check('bundle metadata', info.get('CFBundlePackageType') == 'APPL' and
              bool(info.get('CFBundleShortVersionString')) and bool(info.get('CFBundleVersion')) and
              Path(executable).name == executable)
    except (OSError, ValueError, KeyError, TypeError):
        return {'schema_version': 1, 'profile': 'distribution' if distribution else 'development',
                'passed': False, 'checks': [{'check': 'bundle metadata', 'passed': False}]}
    check('native executable', (contents / 'MacOS' / executable).is_file())
    resources = contents / 'Resources'
    check('bundled backend', (resources / 'backend/studio-backend').is_file())
    files = sorted(path for path in contents.rglob('*') if path.is_file())
    check('no escaping bundle links', all(path.resolve().is_relative_to(app) for path in contents.rglob('*')))
    fingerprint = hashlib.sha256()
    for path in files:
        if path.resolve().is_relative_to(app):
            fingerprint.update(str(path.relative_to(contents)).encode() + b'\0' + digest(path).encode() + b'\n')
    try:
        inventory = json.loads((resources / 'release-inventory.json').read_text())
        entries = [(contents / item['path'], item['sha256']) for item in inventory['native_binaries']]
        for component in inventory['components']:
            entries.extend((resources / item['path'], item['sha256']) for item in component['notices'])
        entries.append((resources / inventory['sbom']['path'], inventory['sbom']['sha256']))
        check('inventory hashes and notices', bool(entries) and all(
            path.resolve().is_relative_to(app) and path.is_file() and digest(path) == expected
            for path, expected in entries))
    except (OSError, ValueError, KeyError, TypeError):
        inventory = {}
        check('inventory hashes and notices', False)
    if distribution:
        check('production bundle identifier', bool(info.get('CFBundleIdentifier')) and
              not info['CFBundleIdentifier'].startswith('local.') and 'development' not in info['CFBundleIdentifier'])
        icon = info.get('CFBundleIconFile', '')
        check('application icon', bool(icon) and (resources / icon).is_file())
        status, details = run(['/usr/bin/codesign', '-d', '--verbose=4', str(app)])
        check('Developer ID and hardened runtime', status == 0 and
              'Authority=Developer ID Application:' in details and 'runtime' in details)
        status, _ = run(['/usr/bin/codesign', '--verify', '--deep', '--strict', '--verbose=2', str(app)])
        check('code signature verification', status == 0)
        status, _ = run(['/usr/bin/xcrun', 'stapler', 'validate', str(app)])
        check('stapled notarization ticket', status == 0)
        status, _ = run(['/usr/sbin/spctl', '--assess', '--type', 'execute', '--verbose=2', str(app)])
        check('Gatekeeper assessment', status == 0)
        check('recorded dependency rights clearance', inventory.get('commercial_rights_cleared') is True)
    return {'schema_version': 1, 'profile': 'distribution' if distribution else 'development',
            'artifact_sha256': fingerprint.hexdigest(), 'passed': all(c['passed'] for c in checks), 'checks': checks,
            'limits': 'Technical artifact checks only; independent clean-machine, DJ compatibility, musical quality, legal and business acceptance remain required. No signing, uploading or publishing occurs.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', type=Path)
    parser.add_argument('--distribution', action='store_true')
    args = parser.parse_args()
    result = inspect(args.app, args.distribution)
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
