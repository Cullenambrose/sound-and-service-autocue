"""READ-ONLY parse of Serato BeatGrid + Markers2 + MIK CuePoints from the box copies."""
import glob, struct, base64, json, sys
from mutagen.id3 import ID3
def beatgrid(data):
    n = struct.unpack(">I", data[2:6])[0]; off = 6; m = []
    for i in range(n):
        pos = struct.unpack(">f", data[off:off+4])[0]
        if i == n-1: m.append({"pos": pos, "bpm": struct.unpack(">f", data[off+4:off+8])[0]})
        else: m.append({"pos": pos, "beats_to_next": struct.unpack(">I", data[off+4:off+8])[0]})
        off += 8
    return m
def markers2(data):
    b = data[2:]; b = b.replace(b"\n", b"").split(b"\x00")[0]
    b += b"=" * (-len(b) % 4)
    raw = base64.b64decode(b, validate=False)
    raw = raw[2:]; out = []; off = 0
    while off < len(raw):
        e = raw.find(b"\x00", off)
        if e < 0: break
        name = raw[off:e].decode("latin1"); 
        if not name: break
        ln = struct.unpack(">I", raw[e+1:e+5])[0]; d = raw[e+5:e+5+ln]; off = e+5+ln
        if name == "CUE":
            idx = d[1]; pos = struct.unpack(">I", d[2:6])[0]; rgb = d[7:10].hex().upper()
            label = d[12:].split(b"\x00")[0].decode("utf8", "replace")
            out.append({"type": "CUE", "index": idx, "ms": pos, "rgb": rgb, "name": label})
        elif name == "LOOP":
            s, en = struct.unpack(">II", d[2:10]); lab = d[20:].split(b"\x00")[0].decode("utf8", "replace")
            out.append({"type": "LOOP", "index": d[1], "start_ms": s, "end_ms": en, "name": lab})
        elif name in ("BPMLOCK", "COLOR", "FLIP"):
            out.append({"type": name, "hex": d.hex()})
    return out
res = {}
for f in sorted(glob.glob(sys.argv[1] + "/*.mp3")):
    t = ID3(f); r = {}
    for fr in t.getall("GEOB"):
        if fr.desc == "Serato BeatGrid": r["serato_beatgrid"] = beatgrid(fr.data)
        if fr.desc == "Serato Markers2": r["markers2"] = markers2(fr.data)
        if fr.desc == "CuePoints":
            try: r["mik_cuepoints"] = json.loads(base64.b64decode(fr.data + b"=" * (-len(fr.data) % 4)))
            except Exception as ex:
                try: r["mik_cuepoints"] = json.loads(fr.data)
                except Exception: r["mik_cuepoints_raw"] = fr.data[:120].decode("latin1")
    res[f.split("/")[-1][:2]] = r
json.dump(res, open(sys.argv[2], "w"), indent=1)
