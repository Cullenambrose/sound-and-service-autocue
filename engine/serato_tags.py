"""Shared Serato tag envelopes and Markers2/Markers_ codecs. These functions never open or write music files."""
import base64
import re

ENVELOPE_PREFIX = b"application/octet-stream\x00\x00"
VORBIS_HEADER = ENVELOPE_PREFIX + b"Serato Markers2\x00"
MP4_KEY = "----:com.serato.dj:markersv2"
MP4_BEATGRID_KEY = "----:com.serato.dj:beatgrid"
MP4_TEMPOMAP_KEY = "----:com.serato.dj:tempomap"
FLAC_KEY = "SERATO_MARKERS_V2"
MARKERS2_HEADER = b"\x01\x01"
MARKERS2_MIN_SIZE = 470
MARKERS_V1_HEADER = b"\x02\x05"
MARKERS_V1_CUES = 5
MARKERS_V1_LOOPS = 9
MARKERS_V1_ENTRY_SIZE = 22
DEFAULT_TRACK_COLOR = b"\xff\xff\xff"
ENERGY_CUE = re.compile(r"^\s*energy\s*\d+\s*$", re.IGNORECASE)


def marker2_entries(data):
    if not data:
        return []
    encoded = data[2:].replace(b"\n", b"").split(b"\x00", 1)[0]
    if len(encoded) % 4 == 1:
        encoded = encoded[:-1]
    encoded += b"=" * (-len(encoded) % 4)
    try:
        decoded = base64.b64decode(encoded, validate=False)
    except ValueError as error:
        raise ValueError("Could not decode the existing Serato Markers2 tag.") from error
    if not decoded.startswith(MARKERS2_HEADER):
        raise ValueError("Existing Serato Markers2 tag has an unsupported header.")
    # Entries begin after the 2-byte inner version header, which must never be read as an entry name.
    entries, offset = [], 2
    while offset < len(decoded):
        if not decoded[offset:].strip(b"\x00"):
            break
        end_name = decoded.find(b"\x00", offset)
        if end_name < 0 or end_name + 5 > len(decoded):
            raise ValueError("Existing Serato Markers2 entry is malformed; refusing to overwrite it.")
        try:
            name = decoded[offset:end_name].decode("ascii")
        except UnicodeDecodeError as error:
            raise ValueError("Existing Serato Markers2 entry has an unknown encoding.") from error
        length = int.from_bytes(decoded[end_name + 1:end_name + 5], "big")
        end = end_name + 5 + length
        if end > len(decoded):
            raise ValueError("Existing Serato Markers2 entry length is invalid.")
        entries.append((name, decoded[end_name + 5:end]))
        offset = end
    return entries


def vorbis_decode(text, field="Serato Markers2"):
    if isinstance(text, bytes):
        text = text.decode("ascii")
    if not isinstance(text, str):
        raise ValueError("Existing " + field + " tag must contain base64 text.")
    raw = "".join(text.split()).encode("ascii")
    raw += b"=" * (-len(raw) % 4)
    decoded = base64.b64decode(raw, validate=True)
    header = ENVELOPE_PREFIX + field.encode("ascii") + b"\x00"
    if not decoded.startswith(header):
        raise ValueError("Existing " + field + " tag has an unsupported header.")
    return decoded[len(header):]


def hex_to_rgb(value):
    text = str(value or "").strip()
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", text):
        raise ValueError(f"Cue color must be #RRGGBB, not {value!r}.")
    return bytes.fromhex(text[1:])


def _entry(name, payload):
    return name.encode("ascii") + b"\x00" + len(payload).to_bytes(4, "big") + payload


def cue_payload(index, position_ms, color, name):
    if type(index) is not int or not 0 <= index <= 7:
        raise ValueError("Serato cue index must be 0-7.")
    if type(position_ms) is not int or not 0 <= position_ms < 0xFFFFFFFF:
        raise ValueError("Serato cue position must be a nonnegative millisecond integer.")
    label = str(name).encode("utf-8")
    if b"\x00" in label:
        raise ValueError("Serato cue labels cannot contain NUL characters.")
    return (bytes((0, index)) + position_ms.to_bytes(4, "big") + b"\x00" + hex_to_rgb(color)
            + b"\x00\x00" + label + b"\x00")


def parse_cue_payload(payload):
    if len(payload) < 13 or payload[0] != 0:
        raise ValueError("Serato CUE entry is malformed.")
    name = payload[12:].split(b"\x00", 1)[0].decode("utf-8")
    return {"index": payload[1], "position_ms": int.from_bytes(payload[2:6], "big"),
            "color": "#" + payload[7:10].hex().upper(), "name": name}


def is_energy_cue(name):
    return bool(ENERGY_CUE.match(name or ""))


def decode_markers2(data):
    """Decoded Markers2 GEOB data: inner payload bytes, raw entries, CUEs, track color and BPM lock."""
    entries = marker2_entries(data)
    cues = [parse_cue_payload(payload) for name, payload in entries if name == "CUE"]
    color = next((payload for name, payload in entries if name == "COLOR"), None)
    lock = next((payload for name, payload in entries if name == "BPMLOCK"), None)
    encoded = data[2:].replace(b"\n", b"").split(b"\x00", 1)[0]
    if len(encoded) % 4 == 1:
        encoded = encoded[:-1]
    inner = base64.b64decode(encoded + b"=" * (-len(encoded) % 4))
    return {"inner": inner, "entries": entries, "cues": sorted(cues, key=lambda cue: cue["index"]),
            "track_color": None if color is None else "#" + color[1:4].hex().upper(),
            "bpm_lock": None if lock is None else bool(lock[:1] == b"\x01")}


def encode_markers2(cues, existing_data=None, min_size=MARKERS2_MIN_SIZE):
    """GEOB:Serato Markers2 data for pads `cues` [{index, position_ms, color, name}].

    Layout: outer b"\\x01\\x01" + 72-column base64 of the inner payload + b"\\x00" (zero padded like Serato).
    Inner payload: b"\\x01\\x01", COLOR, CUE entries in slot order, other entries (loops, flips), BPMLOCK, b"\\x00".
    Pads 1-8 (indices 0-7) are always replaced, including Mixed In Key "Energy N" cues; cues 9+ are kept.
    """
    existing = marker2_entries(existing_data) if existing_data else []
    color = next((payload for name, payload in existing if name == "COLOR"), b"\x00" + DEFAULT_TRACK_COLOR)
    lock = next((payload for name, payload in existing if name == "BPMLOCK"), b"\x00")
    others = [(name, payload) for name, payload in existing
              if name not in ("COLOR", "BPMLOCK", "CUE")
              or (name == "CUE" and len(payload) >= 13 and payload[1] >= 8
                  and not is_energy_cue(parse_cue_payload(payload)["name"]))]
    ordered = sorted(cues, key=lambda cue: cue["index"])
    if len({cue["index"] for cue in ordered}) != len(ordered):
        raise ValueError("Each Serato pad can only hold one cue.")
    inner = bytearray(MARKERS2_HEADER)
    inner += _entry("COLOR", color)
    for cue in ordered:
        if is_energy_cue(cue["name"]):
            raise ValueError("Refusing to write a Mixed In Key Energy cue label.")
        inner += _entry("CUE", cue_payload(cue["index"], cue["position_ms"], cue["color"], cue["name"]))
    for name, payload in others:
        inner += _entry(name, payload)
    inner += _entry("BPMLOCK", lock) + b"\x00"
    encoded = base64.b64encode(bytes(inner)).rstrip(b"=")
    data = MARKERS2_HEADER + b"\n".join(encoded[index:index + 72] for index in range(0, len(encoded), 72)) + b"\x00"
    return data + b"\x00" * max(0, min_size - len(data))


def serato32_encode(value):
    a, b, c = value
    return bytes(((a >> 5) & 0x7F, ((b >> 6) | (a << 2)) & 0x7F, ((c >> 7) | (b << 1)) & 0x7F, c & 0x7F))


def serato32_decode(value):
    w, x, y, z = value
    return bytes((((x & 0x7F) >> 2) | ((w & 0x07) << 5),
                  ((y & 0x7F) >> 1) | ((x & 0x03) << 6),
                  (z & 0x7F) | ((y & 0x01) << 7)))


_V1_UNSET = b"\x7f" * 10
_V1_FIELD5 = b"\x00\x7f\x7f\x7f\x7f\x7f"


def encode_markers_v1(cues, track_color=DEFAULT_TRACK_COLOR):
    """GEOB:Serato Markers_ (v1) data matching the first five pads, so Serato never sees two cue sets."""
    by_index = {cue["index"]: cue for cue in cues}
    data = bytearray(MARKERS_V1_HEADER + (MARKERS_V1_CUES + MARKERS_V1_LOOPS).to_bytes(4, "big"))
    for index in range(MARKERS_V1_CUES):
        cue = by_index.get(index)
        if cue is None:
            data += _V1_UNSET + _V1_FIELD5 + b"\x00\x00\x00\x00" + b"\x00\x00"
            continue
        position = cue["position_ms"]
        if type(position) is not int or not 0 <= position <= 0xFFFFFF:
            raise ValueError("Serato Markers_ cue positions must fit in 24 bits of milliseconds.")
        data += (b"\x00" + serato32_encode(position.to_bytes(3, "big")) + b"\x7f" + b"\x7f" * 4
                 + _V1_FIELD5 + serato32_encode(hex_to_rgb(cue["color"])) + b"\x01\x00")
    for _ in range(MARKERS_V1_LOOPS):
        data += _V1_UNSET + _V1_FIELD5 + b"\x00\x00\x00\x00" + b"\x03\x00"
    return bytes(data + serato32_encode(track_color))


def decode_markers_v1(data):
    if len(data) < 6 or data[:2] != MARKERS_V1_HEADER:
        raise ValueError("Serato Markers_ tag has an unsupported header.")
    count = int.from_bytes(data[2:6], "big")
    end = 6 + count * MARKERS_V1_ENTRY_SIZE
    if len(data) < end + 4:
        raise ValueError("Serato Markers_ tag is truncated.")
    entries = []
    for number in range(count):
        raw = data[6 + number * MARKERS_V1_ENTRY_SIZE:6 + (number + 1) * MARKERS_V1_ENTRY_SIZE]
        start_set = raw[0] == 0x00
        entries.append({
            "slot": number, "type": raw[20], "locked": bool(raw[21]),
            "position_ms": int.from_bytes(serato32_decode(raw[1:5]), "big") if start_set else None,
            "color": "#" + serato32_decode(raw[16:20]).hex().upper(),
        })
    return {"entries": entries, "track_color": "#" + serato32_decode(data[end:end + 4]).hex().upper()}


def track_color_from_markers2(data):
    """Track color bytes from an existing Markers2 tag, defaulting to Serato white."""
    for name, payload in marker2_entries(data) if data else []:
        if name == "COLOR" and len(payload) >= 4:
            return payload[1:4]
    return DEFAULT_TRACK_COLOR
