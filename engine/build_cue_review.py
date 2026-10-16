"""Build /workspace/cue-review/data.json from v3 plans (allin1, go32 profile) + Gemini picks.
Fallback per cue: engine confident (>=0.85) -> auto; else Gemini pick -> google; else empty."""
import json, os, csv, numpy as np
R = "/workspace/research/dj-auto-cues/proof/real"; OUT = "/workspace/cue-review"
man = {r["n"].zfill(2): r for r in csv.DictReader(open(f"{R}/MANIFEST.csv"))}
tracks = []
for nn in sorted(man):
    p = f"{R}/cues_v2/allin1/v4_go32/{nn}.json"
    if not os.path.exists(p): continue
    d = json.load(open(p)); g = json.load(open(f"{R}/cues/allin1/{nn}.json"))["grid"]
    off = g["offset_ms"] / 1000; t1s = g["t1"] - off; bs = g["bar_s"]
    tb = lambda j: round(t1s + j * bs, 3)
    gem = {}; gp = f"{R}/gemini_v3/{nn}.json"   # gemini-3.1-pro-preview picks (v3: expanded candidates, closest-song few-shot)
    if os.path.exists(gp):
        r = json.load(open(gp)); a = r.get("answer", {}); a = a[0] if isinstance(a, list) and a else a
        for L in "BCDH":
            pk = (a.get(L) or {}).get("pick") if isinstance(a, dict) else None
            if isinstance(pk, int) and pk in r["candidates"].get(L, []): gem[L] = (pk, (a[L].get("why") or ""))
    cues = {}
    for s in "BCDH":
        c = d["cues"].get(s, {})
        if c.get("status") == "set": cues[s] = {"bar": c["bar"], "source": "auto", "why": c["reason"], "conf": c["confidence"]}
        elif s in gem: cues[s] = {"bar": gem[s][0], "source": "google", "why": "Google suggestion: " + gem[s][1], "conf": None}
        else: cues[s] = {"bar": None, "source": "empty", "why": "no confident pick" + (f" (engine guess bar {c.get('bar')}, conf {c.get('confidence')})" if c.get("bar") is not None else ""), "conf": None, "engine_guess": c.get("bar")}
    first = int(np.ceil(-t1s / bs - 1e-6))
    B = cues["B"]
    if B["bar"] is not None:
        n = next((k for k in (8, 4, 2, 1) if B["bar"] - k >= max(first, 0)), None)
        cues["A"] = {"bar": None if n is None else B["bar"] - n, "name": f"{n}BS" if n else "BS", "source": B["source"] if n else "empty", "why": f"{n} bars before START" if n else "no room before START"}
    else: cues["A"] = {"bar": None, "name": "8BS", "source": "empty", "why": "START not set"}
    H = cues["H"]
    cues["G"] = {"bar": None if H["bar"] is None else H["bar"] - 8, "source": H["source"], "why": "32 beats before EXIT"}
    for s in "EF": cues[s] = {"bar": None, "source": "empty", "why": "stays empty"}
    for s, c in cues.items():
        c["beat"] = None if c["bar"] is None else c["bar"] * 4  # position in beats from bar 0 (fine adjust in beats)
    loops = [{"loop": l["loop"], "bar": l["bar"], "beats": 16, "why": l["reason"], "source": "suggest" if l["bar"] is not None else "empty"} for l in d["loops"]]
    f = np.load(f"{R}/features/{nn}.npz"); pk = f["peak"]; n = 1600; step = max(1, len(pk) // n)
    peaks = [round(float(x), 3) for x in pk[: step * (len(pk) // step)].reshape(-1, step).max(1)]
    name = os.path.basename(man[nn]["laptop_path"])
    tracks.append({"id": nn, "name": os.path.splitext(name)[0], "file": name, "t1": round(t1s, 4), "bar_s": bs, "bpm": round(240 / bs, 2),
                   "dur": float(f["dur"]), "peaks": peaks, "cues": cues, "loops": loops})
json.dump({"built": __import__("time").strftime("%Y-%m-%d %H:%M PT"), "tracks": tracks}, open(f"{OUT}/data.json", "w"))
print(len(tracks), "tracks", sum(c["source"] == "google" for t in tracks for c in t["cues"].values()), "google cues")
