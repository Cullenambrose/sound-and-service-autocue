"""Minimal valid audio containers for tests; the 'audio' payload is arbitrary but must never change."""
import struct

AUDIO = bytes(range(256)) * 8


def tagged_wav(version="Clean", *, seconds=2, rate=8000):
    import io
    import wave
    from mutagen.wave import WAVE
    from mutagen.id3 import TIT2, TPE1, TXXX
    stream = io.BytesIO()
    with wave.open(stream, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(b"\0\0" * (rate * seconds))
    stream.seek(0)
    audio = WAVE(stream)
    audio.add_tags()
    audio.tags.add(TPE1(encoding=3, text=["Invented Artist"]))
    audio.tags.add(TIT2(encoding=3, text=["Synthetic Song"]))
    audio.tags.add(TXXX(encoding=3, desc="VERSION", text=[version]))
    audio.save(stream)
    return stream.getvalue()


def mp3(frames=3):
    return (b"\xff\xfb\x90\x64" + b"\x00" * 413) * frames


def aiff():
    comm = struct.pack(">hIh", 2, 100, 16) + bytes.fromhex("400EAC44000000000000")
    ssnd = struct.pack(">II", 0, 0) + AUDIO
    body = b"AIFF" + b"COMM" + struct.pack(">I", len(comm)) + comm + b"SSND" + struct.pack(">I", len(ssnd)) + ssnd
    return b"FORM" + struct.pack(">I", len(body)) + body


def flac():
    rate, channels, bits, total = 44100, 2, 16, 44100
    packed = (rate << 44) | ((channels - 1) << 41) | ((bits - 1) << 36) | total
    streaminfo = struct.pack(">HH", 4096, 4096) + (0).to_bytes(3, "big") * 2 + packed.to_bytes(8, "big") + b"\x00" * 16
    return b"fLaC" + bytes((0x80, 0, 0, len(streaminfo))) + streaminfo + b"\xff\xf8" + AUDIO


def _atom(kind, payload):
    return struct.pack(">I", len(payload) + 8) + kind + payload


def m4a():
    mdhd = _atom(b"mdhd", struct.pack(">IIIIIHH", 0, 0, 0, 44100, 44100, 0x55C4, 0))
    hdlr = _atom(b"hdlr", struct.pack(">I4s4s", 0, b"\0\0\0\0", b"soun") + b"\0" * 12 + b"\0")
    stsd = _atom(b"stsd", struct.pack(">II", 0, 0))
    stco = _atom(b"stco", struct.pack(">III", 0, 1, 0))
    stbl = _atom(b"stbl", stsd + stco)
    mdia = _atom(b"mdia", mdhd + hdlr + _atom(b"minf", stbl))
    mvhd = _atom(b"mvhd", struct.pack(">IIIII", 0, 0, 0, 44100, 44100) + b"\0" * 80)
    ftyp = _atom(b"ftyp", b"M4A \0\0\0\0M4A mp42isom")
    moov_size = 8 + len(mvhd) + 8 + len(_atom(b"trak", mdia))
    mdat_offset = len(ftyp) + moov_size + 8
    stco = _atom(b"stco", struct.pack(">III", 0, 1, mdat_offset))
    stbl = _atom(b"stbl", stsd + stco)
    mdia = _atom(b"mdia", mdhd + hdlr + _atom(b"minf", stbl))
    moov = _atom(b"moov", mvhd + _atom(b"trak", mdia))
    return ftyp + moov + _atom(b"mdat", AUDIO)
