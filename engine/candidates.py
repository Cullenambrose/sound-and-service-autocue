"""Expanded candidate spots per cue (bars on the Serato grid). Used by Gemini v3 and the learner."""
import json, os, math, numpy as np
R = "/workspace/research/dj-auto-cues/proof/real"
def stem_bar_frac(nn, g, nbars):
    p = f"{R}/out/allin1/{nn}.stems_env.npz"
    if not os.path.exists(p): return None
    s = dict(np.load(p)); h = float(s["hop_s"]); out = {}
    for k in ("vocals", "drums", "bass"):
        x = s[k]; ref = np.percentile(x, 95) + 1e-9; on = x > (0.2 if k == "vocals" else 0.15) * ref; fr = []
        for j in range(nbars):
            a, b = int((g["t1"] + j * g["bar_s"]) / h), int((g["t1"] + (j + 1) * g["bar_s"]) / h)
            fr.append(float(on[max(0, a):max(1, b)].mean()) if b > 0 and a < len(x) else 0.0)
        out[k] = np.array(fr)
    return out
def stem_changes(st, thr=0.4):
    if st is None: return set()
    ch = set()
    for k, f in st.items():
        for j in range(1, len(f)):
            if abs(f[j] - f[j - 1]) >= thr: ch.add(j)
    return ch
def lyric_line_bars(nn, g):
    p = f"{R}/out/words/{nn}.json"
    if not os.path.exists(p): return set()
    w = json.load(open(p)); out = set()
    for s in w.get("segments", []):
        if s.get("start") is not None: out.add(math.floor((s["start"] - g["t1"]) / g["bar_s"] + 0.25))
    return out
def build(nn, mode="new"):
    d1 = json.load(open(f"{R}/cues/allin1/{nn}.json")); g = d1["grid"]; plan = json.load(open(f"{R}/cues_v2/allin1/go16/{nn}.json"))
    pc = plan["cues"]; segs = d1["segments"]; B = pc["B"].get("bar"); H = pc["H"].get("bar"); v1D = d1["cues"]["D"].get("bar")
    nbars = int(math.ceil(float(np.load(f"{R}/features/{nn}.npz")["dur"]) / g["bar_s"])) + 1
    if mode == "old":   # what gemini_v2 used
        return json.load(open(f"{R}/gemini_v2/{nn}.json"))["candidates"]
    starts = {s["j0"] for s in segs}; ends = {s["j1"] for s in segs}
    st = stem_bar_frac(nn, g, nbars); sc = stem_changes(st); ly = lyric_line_bars(nn, g)
    anchors0 = [0] + ([B] if B is not None else [])
    phrase = {a + k * n for a in anchors0 for n in (4, 8, 16) for k in range(-40, 40)}
    edge = starts | {e + d for e in ends for d in (-1, 0, 1)}
    pool = (phrase | edge | sc | ly)
    def near(c, r): return sorted(j for j in pool if c is not None and abs(j - c) <= r and 0 <= j < nbars)
    cand = {}
    cand["B"] = near(B, 8)
    hc = H if H is not None else v1D
    cand["H"] = sorted(set(near(hc, 10)) | set(near(v1D, 6)))
    body = sorted(j for j in (starts | sc | ly | {j for j in phrase if B is not None and (j - B) % 8 == 0}) if B is not None and B + 4 <= j < (hc or nbars))
    cand["C"] = body[:20]; cand["D"] = body[-20:]
    return cand
