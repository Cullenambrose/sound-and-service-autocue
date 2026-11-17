#!/usr/bin/env python3
"""Read-only comparison of an imported Serato crate with a reviewed export manifest.

Does not import, write crates/tags, infer playback, or certify musical quality.
Audio reads are restricted to the explicitly selected audio root.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.serato_database import parse_snapshot_bytes, SeratoParseError


def read_bounded(path, maximum):
    with Path(path).open('rb') as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError('Input exceeds its supported size.')
    return data


def identity(path):
    return unicodedata.normalize('NFC', str(Path(path).resolve()))


def hash_audio(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 2 * 1024 ** 3:
            raise ValueError('Expected a regular audio file no larger than 2 GiB.')
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('Audio changed during verification.')
        return digest.hexdigest()


def verify(crate, manifest, audio_root, volume_root='/'):
    crate_bytes = read_bounded(crate, 64 * 1024 ** 2)
    manifest_bytes = read_bounded(manifest, 8 * 1024 ** 2)
    document = json.loads(manifest_bytes)
    entries = document.get('entries')
    if (document.get('schema_version') != 1 or document.get('ready') is not True or
            not isinstance(entries, list) or not 1 <= len(entries) <= 10000 or
            any(type(document.get(key)) is not int or document[key] != len(entries)
                for key in ('requested', 'eligible', 'included')) or document.get('blocked') != 0 or
            document.get('blockers') != []):
        raise ValueError('Requires a complete ready version-1 export manifest.')
    expected = []
    for position, entry in enumerate(entries, 1):
        if (not isinstance(entry, dict) or type(entry.get('position')) is not int or entry['position'] != position or
                entry.get('eligible') is not True or not isinstance(entry.get('path'), str) or
                not entry['path'].startswith('/') or any(ord(c) < 32 for c in entry['path']) or
                not re.fullmatch('[0-9a-f]{64}', str(entry.get('content_hash', '')))):
            raise ValueError('Manifest membership identity/order/hash is incomplete.')
        expected.append(Path(entry['path']).resolve())
    root = Path(audio_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('An explicit existing audio directory is required.')
    unsafe = [index for index, path in enumerate(expected, 1) if not path.is_relative_to(root)]
    observed = [track['path'] for track in parse_snapshot_bytes(crate_bytes, kind='crate', volume_root=volume_root)['tracks']]
    expected_ids, observed_ids = list(map(identity, expected)), list(map(identity, observed))
    mismatches = [index + 1 for index in range(max(len(expected), len(observed)))
                  if index >= len(expected) or index >= len(observed) or expected_ids[index] != observed_ids[index]]
    missing, changed, unreadable = [], [], []
    for index, (path, entry) in enumerate(zip(expected, entries), 1):
        if index in unsafe:
            continue
        if not path.is_file():
            missing.append(index)
            continue
        try:
            if hash_audio(path) != entry['content_hash']:
                changed.append(index)
        except (OSError, ValueError):
            unreadable.append(index)
    passed = not any((unsafe, mismatches, missing, changed, unreadable))
    return {'schema_version': 1, 'passed': passed, 'scope': 'Crate membership and source-byte verification only; import provenance, UI sort mode, playback, cues/loops and musical quality are not certified.',
            'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(), 'serato_crate_sha256': hashlib.sha256(crate_bytes).hexdigest(),
            'expected_memberships': len(expected), 'observed_memberships': len(observed),
            'expected_duplicate_memberships': len(expected) - len(set(expected_ids)),
            'observed_duplicate_memberships': len(observed) - len(set(observed_ids)),
            'mismatched_positions': mismatches, 'outside_audio_root_positions': unsafe,
            'missing_file_positions': missing, 'changed_file_positions': changed, 'unreadable_file_positions': unreadable}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--crate', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--audio-root', type=Path, required=True)
    parser.add_argument('--volume-root', default='/')
    args = parser.parse_args()
    try:
        result = verify(args.crate, args.manifest, args.audio_root, args.volume_root)
    except (OSError, ValueError, KeyError, TypeError, SeratoParseError):
        result = {'schema_version': 1, 'passed': False, 'error': 'Invalid, inaccessible or unsupported input; no acceptance recorded.'}
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
