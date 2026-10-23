"""Shared Serato tag envelopes. These functions never open or write music files."""
import base64

ENVELOPE_PREFIX = b"application/octet-stream\x00\x00"
VORBIS_HEADER = ENVELOPE_PREFIX + b"Serato Markers2\x00"
MP4_KEY = "----:com.serato.dj:markersv2"
MP4_BEATGRID_KEY = "----:com.serato.dj:beatgrid"
MP4_TEMPOMAP_KEY = "----:com.serato.dj:tempomap"
FLAC_KEY = "SERATO_MARKERS_V2"


def marker2_entries(data):
    if not data:
        return []
    encoded = data[2:].replace(b"\n", b"").split(b"\x00", 1)[0]
    encoded += b"=" * (-len(encoded) % 4)
    try:
        decoded = base64.b64decode(encoded, validate=False)
    except ValueError as error:
        raise ValueError("Could not decode the existing Serato Markers2 tag.") from error
    if not decoded.startswith(b"\x01\x01"):
        raise ValueError("Existing Serato Markers2 tag has an unsupported header.")
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
    raw = "".join(text.split()).encode("ascii")
    raw += b"=" * (-len(raw) % 4)
    decoded = base64.b64decode(raw)
    header = ENVELOPE_PREFIX + field.encode("ascii") + b"\x00"
    if not decoded.startswith(header):
        raise ValueError("Existing " + field + " tag has an unsupported header.")
    return decoded[len(header):]
