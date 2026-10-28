"""v6.1 Pop/Vocal 8-pad spec: BeatGrid ruler, un-snapped START, chorus EXIT, OUT detection and Serato tags.

All audio here is synthetic (step envelopes and invented lyrics), so the suite runs without the music library.
"""
import base64
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import pop_cues
import serato_grid
import serato_tags
import serato_writer
from rules_v6 import build_plan
from timing import bar_to_time

SPEC_COLORS = {"A": "#CC0000", "B": "#CC8800", "D": "#CCCC00", "E": "#CC00CC",
               "F": "#00CC00", "G": "#00CCCC", "H": "#8800CC"}
SPEC_NAMES = {"A": "Intro 8 bars (32 beats)", "B": "START", "D": "GO 8 bars (32 beats) > EXIT",
              "E": "Mix 4 bars (16 beats) > EXIT",
              "F": "EXIT 1st Chorus", "G": "GO 8 bars (32 beats) > OUT", "H": "OUT"}
CHORUS = ["paper lanterns over harbor", "carry me across the water",
          "silver morning hold the line", "we keep turning into light"]


def _verse(number):
    return f"window {number} quiet marble road {number} drifting"


def synthetic_track(pos0, bpm, onset_s, chorus1, chorus2, vocal_end_bar, end_bar, tail_s,
                    fade_from_bar=None):
    """Words, LRC, stems and RMS for a vocal pop edit on a Serato-style ruler.

    Lines sit every 2 bars from bar 8; chorus1/chorus2 are (first, last) bars of repeated lines;
    vocals stop at vocal_end_bar; music plays at full level to end_bar (or fades from fade_from_bar).
    """
    grid = pop_cues.beatgrid_ruler(pos0, bpm)
    duration = bar_to_time(end_bar, grid) + tail_s
    lines, verse = [], 0
    for bar in range(8, vocal_end_bar, 2):
        if chorus1[0] <= bar < chorus1[1]:
            text = CHORUS[(bar - chorus1[0]) // 2 % 4]
        elif chorus2[0] <= bar < chorus2[1]:
            text = CHORUS[(bar - chorus2[0]) // 2 % 4]
        elif chorus1[1] <= bar < chorus1[1] + 4:
            continue  # instrumental turnaround after the first chorus
        else:
            verse += 1
            text = _verse(verse)
        start = onset_s if bar == 8 else bar_to_time(bar, grid) + 0.05
        lines.append((start, text))
    words, lrc = [], []
    for start, text in lines:
        minutes, seconds = divmod(start, 60)
        lrc.append(f"[{int(minutes):02d}:{seconds:05.2f}]{text}")
        for index, token in enumerate(text.split()):
            words.append({"word": token, "start": round(start + index * 0.45, 3),
                          "end": round(start + index * 0.45 + 0.4, 3), "score": 0.9})
    hop, fps = 0.05, 50
    vocals = [0.0] * int(duration / hop + 1)
    for word in words:
        for index in range(int(word["start"] / hop), min(len(vocals), int(word["end"] / hop) + 1)):
            vocals[index] = 0.3
    energy = []
    for frame in range(int(duration * fps)):
        time_s = frame / fps
        level = 0.5
        if fade_from_bar is not None and time_s >= bar_to_time(fade_from_bar, grid):
            level = 0.5 * 0.7 ** ((time_s - bar_to_time(fade_from_bar, grid)) / grid["bar_s"] + 1)
        energy.append(level if time_s >= pos0 else 0.0)
    fine_hop = 0.001
    fine = [0.0] * int((onset_s + 12) / fine_hop)
    for index in range(int(round(onset_s / fine_hop)), len(fine)):
        fine[index] = 0.3
    return {"grid": grid, "duration": duration, "words": words, "lrc": "\n".join(lrc),
            "vocals": vocals, "hop": hop, "energy": energy, "fps": fps, "fine": fine, "fine_hop": fine_hop}


def plan_for(track):
    return build_plan("synthetic", {"grid": track["grid"], "segments": []}, {"cues": {}}, track["duration"],
                      genre="pop", words=track["words"], synced_lyrics=track["lrc"],
                      stems={"vocals": track["vocals"]}, hop_s=track["hop"], energy=track["energy"],
                      fps=track["fps"], vocal_onset_env=track["fine"], vocal_onset_hop_s=track["fine_hop"])


class V61GroundTruthTests(unittest.TestCase):
    def check_pads(self, plan, expected, spec_tolerance_ms):
        pads = {pad["slot"]: pad for pad in serato_writer.plan_pads(plan)}
        self.assertEqual(plan["cue_spec"], "v6.1-pop-vocal")
        self.assertNotIn("C", pads, "Pad 3 must stay empty")
        self.assertEqual(sorted(pads), sorted(expected))
        for slot, (bar, spec_ms) in expected.items():
            with self.subTest(slot=slot):
                pad = pads[slot]
                self.assertEqual(pad["name"], SPEC_NAMES[slot])
                self.assertEqual(pad["color"], SPEC_COLORS[slot])
                self.assertEqual(pad["bar"], bar)
                if slot != "B":
                    self.assertEqual(pad["position_ms"], round(bar_to_time(bar, plan["grid"]) * 1000))
                self.assertLessEqual(abs(pad["position_ms"] - spec_ms), spec_tolerance_ms)
        return pads

    def test_track1_happy_reference_cues(self):
        track = synthetic_track(0.0167, 80.0039, 23.094, (24, 32), (48, 56), 84, 92, 1.0)
        self.assertAlmostEqual(track["grid"]["bar_s"], 2.99985, places=5)
        plan = plan_for(track)
        pads = self.check_pads(plan, {
            "A": (0, 17), "B": (8, 23094), "D": (24, 72015), "E": (28, 84015),
            "F": (32, 96015), "G": (84, 252026), "H": (92, 276025),
        }, spec_tolerance_ms=25)
        self.assertEqual(pads["B"]["position_ms"], 23094)
        self.assertLess(pads["B"]["position_ms"], round(bar_to_time(8, plan["grid"]) * 1000))
        self.assertEqual(plan["start_anchor_bar"], 8)
        self.assertEqual(plan["out_detection"]["kind"], "drum_outro")

    def test_track2_into_the_mystic_reference_cues(self):
        track = synthetic_track(0.045958, 84.0002, 22.970, (32, 40), (56, 64), 74, 82, 0.6)
        self.assertAlmostEqual(track["grid"]["bar_s"], 2.85714, places=5)
        plan = plan_for(track)
        pads = self.check_pads(plan, {
            "A": (0, 46), "B": (8, 22970), "D": (32, 91475), "E": (36, 102903),
            "F": (40, 114332), "G": (74, 211474), "H": (82, 234331),
        }, spec_tolerance_ms=2)
        self.assertEqual(pads["B"]["position_ms"], 22970)
        self.assertEqual(plan["start_anchor_bar"], 8)
        self.assertEqual(plan["out_detection"]["kind"], "drum_outro")

    def test_pop_layout_can_be_switched_off(self):
        track = synthetic_track(0.0167, 80.0039, 23.094, (24, 32), (48, 56), 84, 92, 1.0)
        plan = build_plan("synthetic", {"grid": track["grid"], "segments": []}, {"cues": {}},
                          track["duration"], genre="pop", config={"switches": {"pop_v61": False}})
        self.assertEqual(plan["cue_spec"], "configurable")
        self.assertFalse(plan["cues"]["B"].get("unsnapped"))


class V61AlgorithmTests(unittest.TestCase):
    def test_beatgrid_ruler_math(self):
        grid = pop_cues.beatgrid_ruler(0.045958, 84.0002)
        self.assertAlmostEqual(grid["bar_s"], 4 * 60 / 84.0002, places=8)
        self.assertAlmostEqual(bar_to_time(40, grid), 0.045958 + 40 * grid["bar_s"], places=9)
        with self.assertRaises(ValueError):
            pop_cues.beatgrid_ruler(0.0, 0.0)

    def test_reconcile_never_shifts_serato_phase(self):
        detected = {"source": "beat_this_downbeats", "t1": 1.5166, "bar_s": 2.99985, "bpm": 80.0039}
        grid, _ = serato_grid.reconcile(detected, [{"pos": 0.0167, "bpm": 80.0039}])
        self.assertEqual(grid["source"], "serato_beatgrid")
        self.assertEqual(grid["t1"], 0.0167)

    def test_vocal_pickup_keeps_exact_millisecond_but_anchors_to_bar(self):
        grid = pop_cues.beatgrid_ruler(0.0167, 80.0039)
        fine = [0.0] * 23094 + [0.3] * 3000
        found = pop_cues.refine_vocal_onset(23.15, grid, fine, 0.001)
        self.assertEqual((found["time_s"], found["anchor_bar"]), (23.094, 8))
        self.assertLess(found["offset_from_downbeat_s"], 0)
        self.assertEqual(found["source"], "vocal_stem_energy_onset")

    def test_onset_outside_window_falls_back_to_word_time(self):
        grid = pop_cues.beatgrid_ruler(0.0, 120.0)
        fine = [0.0] * 10000 + [0.3] * 1000  # energy edge 6 s before the bar-8 downbeat at 16 s
        found = pop_cues.refine_vocal_onset(15.9, grid, fine, 0.001)
        self.assertEqual((found["time_s"], found["source"]), (15.9, "whisperx_first_word"))

    def test_exit_moves_to_stronger_vocal_drop_off(self):
        grid = pop_cues.beatgrid_ruler(0.0, 120.0)
        hop = 0.05
        vocals = [0.3 if 16 <= index * hop < bar_to_time(36, grid) else 0.0 for index in range(int(120 / hop))]
        bar, evidence = pop_cues.refine_chorus_exit(32, grid, 8, 60, vocals, hop)
        self.assertEqual((bar, evidence["status"]), (36, "moved"))
        bar, evidence = pop_cues.refine_chorus_exit(36, grid, 8, 60, vocals, hop)
        self.assertEqual((bar, evidence["status"]), (36, "kept"))

    def test_fade_out_places_out_on_last_full_volume_phrase(self):
        track = synthetic_track(0.0, 120.0, 16.0, (24, 32), (48, 56), 70, 88, 0.0, fade_from_bar=78)
        result = pop_cues.detect_out(track["energy"], track["fps"], track["grid"], 8, track["duration"],
                                     track["vocals"], track["hop"])
        self.assertEqual(result["kind"], "fade_out")
        self.assertEqual((result["out_bar"], result["go_bar"]), (76, 68))
        self.assertGreaterEqual(result["drop_bar"], 78)

    def test_drum_outro_keeps_final_bar(self):
        track = synthetic_track(0.0, 120.0, 16.0, (24, 32), (48, 56), 70, 88, 0.0)
        result = pop_cues.detect_out(track["energy"], track["fps"], track["grid"], 8, track["duration"],
                                     track["vocals"], track["hop"])
        self.assertEqual((result["kind"], result["out_bar"], result["go_bar"]), ("drum_outro", 88, 80))


class V61SeratoTagTests(unittest.TestCase):
    pads = [
        {"index": 0, "name": "Intro - 8BTS", "position_ms": 17, "color": "#CC0000"},
        {"index": 1, "name": "START", "position_ms": 23094, "color": "#CC8800"},
        {"index": 3, "name": "GO-8Exit", "position_ms": 72013, "color": "#CCCC00"},
        {"index": 4, "name": "Mix-4exit", "position_ms": 84013, "color": "#CC00CC"},
        {"index": 5, "name": "EXIT 1st Chorus", "position_ms": 96012, "color": "#00CC00"},
        {"index": 6, "name": "GO-8OUT", "position_ms": 252004, "color": "#00CCCC"},
        {"index": 7, "name": "OUT", "position_ms": 276003, "color": "#8800CC"},
    ]

    @staticmethod
    def legacy_markers2_with_energy_cues():
        def entry(name, payload):
            return name.encode() + b"\x00" + len(payload).to_bytes(4, "big") + payload
        inner = b"\x01\x01" + entry("COLOR", b"\x00\x99\xff\x99")
        inner += entry("CUE", serato_tags.cue_payload(0, 1000, "#CC0000", "Energy 6"))
        inner += entry("CUE", serato_tags.cue_payload(2, 5000, "#CC8800", "Energy 7"))
        inner += entry("LOOP", b"\x00\x00" + b"\x00" * 18)
        inner += entry("BPMLOCK", b"\x01") + b"\x00"
        return b"\x01\x01" + base64.b64encode(inner).rstrip(b"=") + b"\x00"

    def test_markers2_round_trip_header_labels_and_unsnapped_ms(self):
        data = serato_tags.encode_markers2(self.pads, self.legacy_markers2_with_energy_cues())
        self.assertEqual(data[:2], b"\x01\x01")
        self.assertTrue(all(len(line) <= 72 for line in data[2:].split(b"\x00", 1)[0].split(b"\n")))
        decoded = serato_tags.decode_markers2(data)
        self.assertEqual(decoded["inner"][:2], b"\x01\x01")
        self.assertEqual(decoded["inner"][-1:], b"\x00")
        self.assertEqual([name for name, _ in decoded["entries"]],
                         ["COLOR"] + ["CUE"] * 7 + ["LOOP", "BPMLOCK"])
        self.assertFalse(any(serato_tags.is_energy_cue(cue["name"]) for cue in decoded["cues"]))
        self.assertEqual([(cue["index"], cue["name"], cue["position_ms"], cue["color"]) for cue in decoded["cues"]],
                         [(pad["index"], pad["name"], pad["position_ms"], pad["color"]) for pad in self.pads])
        self.assertEqual(decoded["cues"][1]["position_ms"], 23094)
        self.assertEqual(decoded["track_color"], "#99FF99")
        self.assertTrue(decoded["bpm_lock"])

    def test_markers2_first_entry_is_not_swallowed_by_inner_header(self):
        entries = serato_tags.marker2_entries(self.legacy_markers2_with_energy_cues())
        self.assertEqual(entries[0][0], "COLOR")

    def test_cues_beyond_pad_8_are_preserved(self):
        def entry(index, name):
            payload = bytearray(serato_tags.cue_payload(0, 5000, "#CC0000", name))
            payload[1] = index
            return b"CUE\x00" + len(payload).to_bytes(4, "big") + bytes(payload)
        inner = b"\x01\x01" + entry(9, "Keep") + entry(10, "Energy 4") + b"\x00"
        extra = b"\x01\x01" + base64.b64encode(inner).rstrip(b"=") + b"\x00"
        cues = serato_tags.decode_markers2(serato_tags.encode_markers2(self.pads, extra))["cues"]
        self.assertEqual([cue["index"] for cue in cues], [0, 1, 3, 4, 5, 6, 7, 9])

    def test_utf8_labels_survive_and_energy_labels_are_refused(self):
        decoded = serato_tags.decode_markers2(serato_tags.encode_markers2(
            [{"index": 0, "name": "Intro – Café ♪", "position_ms": 0, "color": "#CC0000"}]))
        self.assertEqual(decoded["cues"][0]["name"], "Intro – Café ♪")
        with self.assertRaises(ValueError):
            serato_tags.encode_markers2([{"index": 0, "name": "Energy 5", "position_ms": 0, "color": "#CC0000"}])

    def test_markers_v1_matches_pads_and_documented_vectors(self):
        self.assertEqual(serato_tags.serato32_encode(bytes.fromhex("CC0000")), bytes.fromhex("06300000"))
        self.assertEqual(serato_tags.serato32_decode(bytes.fromhex("06300000")), bytes.fromhex("CC0000"))
        data = serato_tags.encode_markers_v1(self.pads)
        self.assertEqual(data[:2], b"\x02\x05")
        self.assertEqual(int.from_bytes(data[2:6], "big"), 14)
        self.assertEqual(len(data), 6 + 14 * 22 + 4)
        decoded = serato_tags.decode_markers_v1(data)
        cues = [(entry["slot"], entry["position_ms"], entry["color"]) for entry in decoded["entries"][:5]
                if entry["position_ms"] is not None]
        self.assertEqual(cues, [(pad["index"], pad["position_ms"], pad["color"]) for pad in self.pads
                                if pad["index"] < 5])
        self.assertIsNone(decoded["entries"][2]["position_ms"])
        self.assertTrue(all(entry["type"] == 3 for entry in decoded["entries"][5:]))
        self.assertEqual(decoded["track_color"], "#FFFFFF")

    def test_mp3_writer_updates_both_tags_and_keeps_audio_identical(self):
        from mutagen.id3 import ID3, GEOB
        from web import fixtures
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.mp3"
            path.write_bytes(fixtures.mp3())
            tags = ID3()
            tags.add(GEOB(encoding=0, mime="application/octet-stream", desc="Serato Markers2",
                          data=self.legacy_markers2_with_energy_cues()))
            tags.save(path, v2_version=3)
            audio_before = serato_writer.mp3_audio_sha256(path)
            result = serato_writer.write_mp3_pads(path, self.pads)
            self.assertEqual(result["audio_sha256_before"], result["audio_sha256_after"])
            self.assertEqual(serato_writer.mp3_audio_sha256(path), audio_before)
            frames = serato_writer.read_serato_frames(path)
            decoded = serato_tags.decode_markers2(frames["Serato Markers2"])
            self.assertEqual(decoded["inner"][:2], b"\x01\x01")
            self.assertEqual([cue["name"] for cue in decoded["cues"]], [pad["name"] for pad in self.pads])
            v1 = serato_tags.decode_markers_v1(frames["Serato Markers_"])
            self.assertEqual(v1["entries"][1]["position_ms"], 23094)
            self.assertEqual(v1["track_color"], "#99FF99")

    def test_mp3_writer_refuses_other_formats(self):
        with self.assertRaises(ValueError):
            serato_writer.write_mp3_pads("song.flac", self.pads)


class SingleTrackScriptTests(unittest.TestCase):
    @staticmethod
    def script():
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "apply_single_track_qa", HERE.parent / "scripts" / "apply_single_track_qa.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        main = module.main

        def quiet_main(argv):
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return main(argv)
        module.quiet_main = quiet_main
        return module

    def test_refuses_track1_reference_fixture_and_open_serato(self):
        from unittest.mock import patch
        script = self.script()
        with tempfile.TemporaryDirectory() as directory:
            happy = Path(directory) / "Happy (N And D Chorus Intro) - 4A - 80.mp3"
            happy.write_bytes(b"do not touch")
            with patch.object(script, "analyze_track") as analyze:
                self.assertEqual(script.quiet_main(["--track", str(happy)]), 2)
                analyze.assert_not_called()
            self.assertEqual(happy.read_bytes(), b"do not touch")
            other = Path(directory) / "Other.mp3"
            other.write_bytes(b"x")
            with patch.object(script, "serato_running", return_value=["Serato DJ Pro"]), \
                    patch.object(script, "analyze_track") as analyze:
                self.assertEqual(script.quiet_main(["--track", str(other)]), 2)
                analyze.assert_not_called()

    def test_backup_write_and_read_back_on_a_synthetic_mp3(self):
        from unittest.mock import patch
        from web import fixtures
        script = self.script()
        track = synthetic_track(0.045958, 84.0002, 22.970, (32, 40), (56, 64), 74, 82, 0.6)
        plan = plan_for(track)
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory) / "Synthetic Re-Drum.mp3"
            song.write_bytes(fixtures.mp3())
            original = song.read_bytes()
            audio_before = serato_writer.mp3_audio_sha256(song)
            backups = Path(directory) / "backups"
            with patch.object(script, "serato_running", return_value=[]), \
                    patch.object(script, "analyze_track", return_value=plan):
                self.assertEqual(script.quiet_main(["--track", str(song), "--dry-run"]), 0)
                self.assertEqual(song.read_bytes(), original)
                self.assertEqual(script.quiet_main(["--track", str(song), "--backup-dir", str(backups)]), 0)
            saved = [path for path in backups.iterdir() if path.suffix == ".mp3"]
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0].read_bytes(), original)
            self.assertEqual(serato_writer.mp3_audio_sha256(song), audio_before)
            cues = serato_tags.decode_markers2(serato_writer.read_serato_frames(song)["Serato Markers2"])["cues"]
            self.assertEqual([(cue["index"], cue["name"]) for cue in cues],
                             [(index, SPEC_NAMES[slot]) for index, slot in enumerate("ABCDEFGH") if slot != "C"])
            self.assertEqual(cues[1]["position_ms"], 22970)


if __name__ == "__main__":
    unittest.main()
