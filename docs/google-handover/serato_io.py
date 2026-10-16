import struct
import base64
from pathlib import Path
from typing import List
import mutagen.id3
from mutagen.mp3 import MP3
from models_and_rules import SeratoCuePoint, SeratoSavedLoop

class SeratoTagAndCrateManager:
@staticmethod
def _build_cue_entry_bytes(cue: SeratoCuePoint) -> bytes:
    r, g, b = cue.color_rgb
    name_bytes = cue.name.encode("utf-8") + b"\x00"
    payload = struct.pack(">BBI B BBB BB", 0x00, cue.slot, cue.position_ms, 0x00, r, g, b, 0x00, 0x00) + name_bytes
    return b"CUE\x00" + struct.pack(">I", len(payload)) + payload

@staticmethod
def _build_loop_entry_bytes(loop: SeratoSavedLoop) -> bytes:
    r, g, b = loop.color_rgb
    name_bytes = loop.name.encode("utf-8") + b"\x00"
    payload = struct.pack(">BBI I I B BBB BB", 0x00, loop.slot, loop.start_ms, loop.end_ms, 0xFFFFFFFF, 0x00, r, g, b, 0x00, 1 if loop.locked else 0) + name_bytes
    return b"LOOP\x00" + struct.pack(">I", len(payload)) + payload

@classmethod
def write_markers2_cues_and_loops(cls, audio_path: str, cues: List[SeratoCuePoint], loops: List[SeratoSavedLoop]):
    raw_entries = bytearray()
    raw_entries.extend(b"COLOR\x00\x00\x00\x00\xff\xff\xff")
    for c in cues: raw_entries.extend(cls._build_cue_entry_bytes(c))
    for l in loops: raw_entries.extend(cls._build_loop_entry_bytes(l))
    b64_payload = base64.b64encode(bytes(raw_entries)).decode("ascii")
    geob_data = b"\x01\x01" + b64_payload.encode("ascii") + b"\x00"

    audio = MP3(audio_path)
    if audio.tags is None: audio.add_tags()
    audio.tags.delall("GEOB:Serato Markers2")
    audio.tags.add(mutagen.id3.GEOB(encoding=0, mime="application/octet-stream", desc="Serato Markers2", data=geob_data))
    audio.save()
