"""Read existing DJ annotations as unverified evidence, never as training answer keys."""
import base64
import hashlib
import json
import math

from serato_grid import parse_beatgrid
from serato_tags import FLAC_KEY, MP4_KEY, marker2_entries, vorbis_decode

MAX_TAG_BYTES = 1024 * 1024
ID3_FIELDS = {"Serato Markers2", "Serato Markers_", "Serato BeatGrid", "CuePoints"}


def _label(payload, offset):
    text = payload[offset:]
    if b"\0" not in text:
        raise ValueError("Unterminated Serato marker label.")
    return text.split(b"\0", 1)[0].decode("utf-8")


def _position(value, duration_s):
    if value == 0xFFFFFFFF:
        return None
    seconds = value / 1000
    if duration_s is not None and seconds > duration_s + 0.05:
        raise ValueError("Imported marker lies beyond the audio duration.")
    return seconds


def _markers(data, duration_s):
    if data[:2] != b"\x01\x01":
        raise ValueError("Unsupported Serato Markers2 envelope version.")
    encoded = b"".join(data[2:].split(b"\0", 1)[0].split())
    base64.b64decode(encoded + b"=" * (-len(encoded) % 4), validate=True)
    records, seen = [], set()
    for name, payload in marker2_entries(data):
        record = {"type": name, "payload_b64": base64.b64encode(payload).decode("ascii")}
        if name in {"CUE", "LOOP"}:
            minimum = 13 if name == "CUE" else 21
            if len(payload) < minimum:
                raise ValueError("Truncated Serato " + name + " entry.")
            index = payload[1]
            if (name, index) in seen:
                raise ValueError("Duplicate Serato marker index.")
            seen.add((name, index))
            record["index"] = index
            if name == "CUE":
                record.update(time_s=_position(int.from_bytes(payload[2:6], "big"), duration_s),
                              color="#" + payload[7:10].hex().upper(), name=_label(payload, 12))
            else:
                start = _position(int.from_bytes(payload[2:6], "big"), duration_s)
                end = _position(int.from_bytes(payload[6:10], "big"), duration_s)
                if start is not None and end is not None and end <= start:
                    raise ValueError("Imported loop end must follow its start.")
                record.update(start_s=start, end_s=end, name=_label(payload, 20))
        records.append(record)
    return records


def _reject_constant(value):
    raise ValueError("Non-finite value in CuePoints JSON: " + value)


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        _reject_constant(value)
    return number


def _cuepoints(data):
    raw = data.strip().rstrip(b"\0")
    if not raw.startswith((b"{", b"[")):
        raw = b"".join(raw.split())
        raw = base64.b64decode(raw + b"=" * (-len(raw) % 4), validate=True)
    document = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant, parse_float=_finite_float)
    if not isinstance(document, (dict, list)):
        raise ValueError("CuePoints JSON must be an object or array.")
    # CuePoints has no verified schema here: preserve it without guessing units or event meanings.
    return document


def read_annotations(audio, duration_s):
    """Return versioned per-tag evidence from an already-open native Mutagen container."""
    if duration_s is not None and (type(duration_s) not in (int, float)
                                   or not math.isfinite(duration_s) or duration_s < 0):
        raise ValueError("Invalid audio duration for imported annotations.")
    tags = audio.tags
    fields = []
    if tags is not None and hasattr(tags, "getall"):
        fields = [(frame.desc, bytes(frame.data), False)
                  for frame in tags.getall("GEOB") if frame.desc in ID3_FIELDS]
    elif tags is not None:
        for key in (FLAC_KEY, MP4_KEY):
            for value in tags.get(key, []):
                fields.append((key, value.encode("utf-8") if isinstance(value, str) else bytes(value), True))
    sources = []
    counts = {}
    for field, raw, wrapped in fields:
        counts[field] = counts.get(field, 0) + 1
        source = {
            "field": field, "occurrence": counts[field], "container": type(audio).__name__,
            "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
            "producer": "unknown", "human_verified": False, "label_role": "unverified_import",
            "status": "parsed", "format": "unknown", "records": [],
        }
        sources.append(source)
        if len(raw) > MAX_TAG_BYTES:
            source.update(status="error", error="Annotation tag exceeds the 1 MiB import limit; raw omitted.")
            continue
        source["raw_b64"] = base64.b64encode(raw).decode("ascii")
        try:
            if wrapped:
                encoded = b"".join(raw.split())
                base64.b64decode(encoded + b"=" * (-len(encoded) % 4), validate=True)
                data = vorbis_decode(raw.decode("ascii"))
            else:
                data = raw
            if field in {"Serato Markers2", FLAC_KEY, MP4_KEY}:
                source.update(format="serato_markers2_v1.1", records=_markers(data, duration_s))
            elif field == "Serato BeatGrid":
                markers = parse_beatgrid(data)
                if markers is None:
                    raise ValueError("Malformed or unsupported Serato BeatGrid.")
                if duration_s is not None and markers[-1]["pos"] > duration_s + 0.05:
                    raise ValueError("Imported beatgrid lies beyond the audio duration.")
                source.update(format="serato_beatgrid_v1.0", records=markers)
            elif field == "CuePoints":
                source.update(format="cuepoints_json_unknown_schema", document=_cuepoints(data),
                              status="unsupported", diagnostic="Preserved JSON; cue schema/units and producer unverified.")
            else:
                source.update(status="unsupported", diagnostic="Legacy Serato Markers_ preserved, not decoded.")
        except (ValueError, UnicodeError, RecursionError) as error:
            source.update(status="error", error=str(error))
    for source in sources:
        if counts[source["field"]] > 1:
            source.update(status="error", error="Multiple annotation fields conflict; no source was selected.")
    return {"schema_version": 1, "extractor": "imported_anchors_v1", "sources": sources,
            "error_count": sum(source["status"] == "error" for source in sources),
            "unsupported_count": sum(source["status"] == "unsupported" for source in sources)}
