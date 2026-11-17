import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from verify_serato_acceptance import verify, SeratoParseError


def tlv(tag, value):
    return tag + struct.pack('>I', len(value)) + value


class SeratoAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.audio = self.root / 'audio'; self.audio.mkdir()
        self.files = [self.audio / name for name in ('First.mp3', 'Café second.m4a')]
        for index, path in enumerate(self.files): path.write_bytes(f'synthetic audio {index}'.encode())
        self.manifest = self.root / 'manifest.json'; self.crate = self.root / 'test.crate'
        self.document = {'schema_version': 1, 'ready': True, 'requested': 2, 'eligible': 2, 'included': 2, 'blocked': 0, 'blockers': [], 'entries': [
            {'position': i, 'eligible': True, 'path': str(path), 'content_hash': hashlib.sha256(path.read_bytes()).hexdigest()}
            for i, path in enumerate(self.files, 1)]}
        self.save_manifest(); self.save_crate(self.files)

    def save_manifest(self): self.manifest.write_text(json.dumps(self.document))
    def save_crate(self, paths):
        self.crate.write_bytes(tlv(b'vrsn', '1.0/Serato ScratchLive Crate'.encode('utf-16-be')) + b''.join(
            tlv(b'otrk', tlv(b'ptrk', str(path).lstrip('/').encode('utf-16-be'))) for path in paths))
    def result(self): return verify(self.crate, self.manifest, self.audio)

    def test_exact_order_unicode_and_hashes_pass_without_writes(self):
        before = {p: p.read_bytes() for p in (*self.files, self.crate, self.manifest)}
        self.assertTrue(self.result()['passed'])
        self.assertTrue(all(p.read_bytes() == data for p, data in before.items()))

    def test_reversed_order_fails(self):
        self.save_crate(list(reversed(self.files)))
        self.assertEqual(self.result()['mismatched_positions'], [1, 2])

    def test_duplicate_memberships_are_preserved(self):
        self.document['entries'][1] = {**self.document['entries'][0], 'position': 2}; self.save_manifest()
        self.save_crate([self.files[0], self.files[0]])
        self.assertTrue(self.result()['passed'])
        self.assertEqual(self.result()['observed_duplicate_memberships'], 1)

    def test_dropped_duplicate_fails(self):
        self.document['entries'][1] = {**self.document['entries'][0], 'position': 2}; self.save_manifest()
        self.save_crate([self.files[0]])
        self.assertFalse(self.result()['passed'])

    def test_changed_bytes_fail(self):
        self.files[0].write_bytes(b'changed')
        self.assertEqual(self.result()['changed_file_positions'], [1])

    def test_missing_file_fails(self):
        self.files[1].unlink()
        self.assertEqual(self.result()['missing_file_positions'], [2])

    def test_outside_root_is_never_hashed(self):
        outside = self.root / 'outside.mp3'; outside.write_bytes(b'not authorized')
        self.document['entries'][0]['path'] = str(outside); self.save_manifest()
        self.save_crate([outside, self.files[1]])
        with patch('verify_serato_acceptance.hash_audio', side_effect=lambda p: self.assertNotEqual(p, outside) or hashlib.sha256(p.read_bytes()).hexdigest()):
            self.assertEqual(self.result()['outside_audio_root_positions'], [1])

    def test_symlink_escape_fails(self):
        outside = self.root / 'outside.mp3'; outside.write_bytes(b'not authorized')
        self.files[0].unlink(); self.files[0].symlink_to(outside)
        self.assertEqual(self.result()['outside_audio_root_positions'], [1])

    def test_malformed_crate_is_rejected(self):
        self.crate.write_bytes(self.crate.read_bytes()[:-1])
        with self.assertRaises(SeratoParseError): self.result()

    def test_unready_manifest_is_rejected(self):
        self.document['ready'] = False; self.save_manifest()
        with self.assertRaises(ValueError): self.result()

    def test_manifest_membership_order_is_validated(self):
        self.document['entries'][0]['position'] = 2; self.save_manifest()
        with self.assertRaises(ValueError): self.result()

    def test_report_does_not_disclose_paths(self):
        self.assertNotIn(str(self.root), json.dumps(self.result()))
