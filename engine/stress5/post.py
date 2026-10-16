"""stress5 post-processing: Serato grid -> rules v1 draft -> rules v2/v3 (go16, go32) -> v4 drop-check EXIT -> Gemini on unsure songs
(cached, $ cap) -> /workspace/cue-review-stress5/data.json. Rerunnable; only reads audio copies on the box."""
import json, os, sys, csv, math, glob, warnings, time
warnings.filterwarnings("ignore")
import numpy as np
S = "/workspace/research/dj-auto-cues/proof/stress5"; RT = "/workspace/research/dj-auto-cues/proof/real/tools"
sys.path.insert(0, RT)
import rules as R1, rules_v2 as R2, lyrics as LY, candidates as CA
for m in (R1, R2): m.REAL = S
LY.R = S; CA.R = S
KEEP = json.load(open("/workspace/research/dj-auto-cues/proof/real/variants_score.json"))["keep"]
GEM = "--gemini" in sys.argv; CAP = 10.0
OUT = "/workspace/cue-review-stress5"
man = list(csv.DictReader(open(f"{S}/MANIFEST.csv"))); paths = json.load(open(f"{S}/paths.json"))
ids = sorted(paths)
# --- Serato tags (mp3 only; m4a -> Beat This grid fallback)
src = open(f"{RT}/read_serato.py").read().split("res = {}")[0]; ns = {}; exec(src, ns)
tp = f"{S}/serato_tags.json"; tags = json.load(open(tp)) if os.path.exists(tp) else {}
from mutagen.id3 import ID3
for i in ids:
    f = glob.glob(f"{S}/in/{i}.*")
    if i in tags or not f: continue
    r = {}
    if f[0].endswith(".mp3"):
        try:
            for fr in ID3(f[0]).getall("GEOB"):
                if fr.desc == "Serato BeatGrid": r["serato_beatgrid"] = ns["beatgrid"](fr.data)
        except Exception as e: r["err"] = str(e)[:100]
    tags[i] = r
json.dump(tags, open(tp, "w"))
def v4(nn, src_tag, gl):
    p = json.load(open(f"{S}/cues_v2/allin1/{src_tag}/{nn}.json")); c = p["cues"]; g = json.load(open(f"{S}/cues/allin1/{nn}.json"))["grid"]
    tb = lambda j: round(g["t1"] + j * g["bar_s"] - g["offset_ms"] / 1000, 3)
    nb = int(math.ceil(float(np.load(f"{S}/features/{nn}.npz")["dur"]) / g["bar_s"]))
    st = CA.stem_bar_frac(nn, g, nb); H = c["H"].get("bar")
    lp = f"{S}/out/lead/{nn}.npz"; B0 = c["B"].get("bar")
    if "B" in KEEP.get("lead_vocal", []) and os.path.exists(lp) and B0 is not None:   # v4 lead-vocal START
        z = np.load(lp); x = z["lead"]; h = float(z["hop_s"]); on_ = x > 0.2 * (np.percentile(x, 95) + 1e-9)
        lf = np.array([on_[max(0, int((g["t1"] + j * g["bar_s"]) / h)):max(1, int((g["t1"] + (j + 1) * g["bar_s"]) / h))].mean() if g["t1"] + (j + 1) * g["bar_s"] > 0 else 0 for j in range(nb)])
        on = lf > 0.35
        for j in range(len(on) - 3):
            if on[j] and on[j:j + 3].sum() >= 2:
                if abs(j - B0) <= 2 and j != B0: c["B"]["reason"] += f"; lead_vocal moved {B0}->{j}"; c["B"]["bar"] = j; c["B"]["time"] = tb(j)
                break
    if "H" in KEEP.get("drop_check", []) and st is not None and H is not None:
        e = (st["drums"] + st["bass"]) / 2
        ok = lambda j: 1 <= j < nb and e[j - 1] - e[j] >= 0.25
        if not ok(H):
            cand = CA.build(nn)
            near = sorted((j for j in cand["H"] if abs(j - H) <= 2 and ok(j)), key=lambda j: abs(j - H))
            if near: c["H"]["reason"] += f"; drop_check moved {H}->{near[0]}"; c["H"]["bar"] = near[0]; c["H"]["time"] = tb(near[0])
    B, H = c["B"].get("bar"), c["H"].get("bar"); first = math.ceil(-g["t1"] / g["bar_s"] - 1e-6)
    if B is not None:
        n = next((k for k in (8, 4, 2, 1) if B - k >= max(first, 0)), None)
        if n: c["A"].update(bar=B - n, bars_before=n, name=f"{n}BS", time=tb(B - n))
    if H is not None and "G" in c: c["G"].update(bar=H - gl, time=tb(H - gl))
    return p
done = []
for nn in ids:
    if not os.path.exists(f"{S}/out/allin1/{nn}.json"): continue
    try:
        if True:
            tr = R1.Track(nn, tags, {}); d = R1.draft(tr, "allin1"); cs = d["cues"]
            for s in "BDCGAHE":
                c = cs[s]
                if c["status"] == "set" and c["confidence"] < R1.THRESH[s]: c["status"] = "withheld"
                if s == "A" and cs["B"]["status"] != "set" and c["status"] == "set": c["status"] = "withheld"
                if s == "H" and cs["D"]["status"] != "set" and c["status"] == "set": c["status"] = "withheld"
            g = tr.grid
            d["grid"] = {"source": g.source, "bpm": round(g.bpm, 3), "offset_ms": round(g.offset * 1000, 1), "fit": round(g.fit, 2), "phase": g.phase, "flags": g.flags, "t1": round(g.t1, 4), "bar_s": round(g.bar, 5)}
            os.makedirs(f"{S}/cues/allin1", exist_ok=True); json.dump(d, open(f"{S}/cues/allin1/{nn}.json", "w"), indent=1)
            for gl, tag in ((16, "go16"), (32, "go32")):
                os.makedirs(f"{S}/cues_v2/allin1/{tag}", exist_ok=True); json.dump(R2.build(d, nn, 16, gl), open(f"{S}/cues_v2/allin1/{tag}/{nn}.json", "w"), indent=1)
            for gl, tag in ((4, "go16"), (8, "go32")):
                os.makedirs(f"{S}/cues_v2/allin1/v4_{tag}", exist_ok=True); json.dump(v4(nn, tag, gl), open(f"{S}/cues_v2/allin1/v4_{tag}/{nn}.json", "w"), indent=1)
        done.append(nn)
    except Exception as e:
        print(nn, "POST FAIL", repr(e)[:200])
# --- Gemini on unsure songs
spent = sum(json.load(open(p)).get("cost_usd", 0) for p in glob.glob(f"{S}/gemini_v2/*.json"))
if GEM:
    import gemini_s5 as G
    for nn in done:
        p = json.load(open(f"{S}/cues_v2/allin1/v4_go32/{nn}.json"))
        # Gemini ONLY when A/B/G/H under 0.85 — never for C or D alone
        unsure = any((p["cues"].get(L, {}).get("confidence") or 0) < 0.85 for L in "ABGH")
        if not unsure or os.path.exists(f"{S}/gemini_v2/{nn}.json") and "error" not in json.load(open(f"{S}/gemini_v2/{nn}.json")): continue
        if spent >= CAP - 0.15: print("Gemini cap reached", round(spent, 2)); break
        rec = G.one(nn); spent += rec.get("cost_usd", 0); print(nn, "gemini", rec.get("error") or f"${rec.get('cost_usd')}", flush=True)
# --- review data
crates = {}
for r in man: crates.setdefault(r["crate"], []).append(r["id"])
meta = {r["id"]: r for r in man}
tracks = []; stats = {"conf": {}, "A": {}, "gem_sent": 0, "gem_cost": 0.0, "gem_agree": [0, 0], "gem_by": {}}
for nn in done:
    d = json.load(open(f"{S}/cues_v2/allin1/v4_go32/{nn}.json")); d1 = json.load(open(f"{S}/cues/allin1/{nn}.json")); g = d1["grid"]
    off = g["offset_ms"] / 1000; t1s = g["t1"] - off; bs = g["bar_s"]
    gem = {}; gp = f"{S}/gemini_v2/{nn}.json"
    if os.path.exists(gp):
        r = json.load(open(gp))
        if "error" not in r:
            stats["gem_sent"] += 1; stats["gem_cost"] += r.get("cost_usd", 0)
            a = r.get("answer", {}); a = a[0] if isinstance(a, list) and a else a
            for L in "BCDH":
                pk = (a.get(L) or {}).get("pick") if isinstance(a, dict) else None
                if isinstance(pk, int) and pk in r["candidates"].get(L, []): gem[L] = (pk, (a[L].get("why") or ""))
    cues = {}
    for s in "BCDH":
        c = d["cues"].get(s, {}); eb = c.get("bar"); cf = c.get("confidence")
        if s in gem and eb is not None:
            stats["gem_agree"][1] += 1; stats["gem_agree"][0] += gem[s][0] == eb
            k = stats["gem_by"].setdefault(s, [0, 0]); k[1] += 1; k[0] += gem[s][0] == eb
        if c.get("status") == "set": cues[s] = {"bar": eb, "source": "auto", "why": c["reason"], "conf": cf}
        elif s in gem: cues[s] = {"bar": gem[s][0], "source": "google", "why": f"Google suggestion (engine guess bar {eb}, conf {cf}): " + gem[s][1], "conf": cf, "engine_guess": eb}
        elif eb is not None: cues[s] = {"bar": eb, "source": "review" if s in "BH" else "suggest", "why": f"engine guess, not sure (conf {cf}): " + (c.get("reason") or ""), "conf": cf, "engine_guess": eb}
        else: cues[s] = {"bar": None, "source": "empty", "why": c.get("reason") or "no pick", "conf": None}
    first = int(np.ceil(-t1s / bs - 1e-6)); B = cues["B"]
    if B["bar"] is not None:
        n = next((k for k in (8, 4, 2, 1) if B["bar"] - k >= max(first, 0)), None)
        cues["A"] = {"bar": None if n is None else B["bar"] - n, "name": f"{n}BS" if n else "BS", "source": B["source"] if n else "empty", "why": f"{n} bars before START" if n else "needs review: not even 1 bar fits before START"}
    else: n = None; cues["A"] = {"bar": None, "name": "8BS", "source": "empty", "why": "START not set"}
    k = f"{n}BS" if n else "empty"; stats["A"][k] = stats["A"].get(k, 0) + 1
    H = cues["H"]; cues["G"] = {"bar": None if H["bar"] is None else H["bar"] - 8, "source": H["source"], "why": "32 beats before EXIT"}
    for s in "EF": cues[s] = {"bar": None, "source": "empty", "why": "stays empty"}
    for s, c in cues.items(): c["beat"] = None if c["bar"] is None else c["bar"] * 4
    for L in "ABGH":
        cf = d["cues"].get(L, {}).get("confidence"); b = "none" if cf is None else ">=0.85" if cf >= 0.85 else "0.70-0.84" if cf >= 0.7 else "0.50-0.69" if cf >= 0.5 else "<0.50"
        stats["conf"].setdefault(L, {}); stats["conf"][L][b] = stats["conf"][L].get(b, 0) + 1
    loops = [{"loop": l["loop"], "bar": l["bar"], "beats": 16, "why": l["reason"], "source": "suggest" if l["bar"] is not None else "empty"} for l in d["loops"]]
    f = np.load(f"{S}/features/{nn}.npz"); pk = f["peak"]; npk = 1600; step = max(1, len(pk) // npk)
    peaks = [round(float(x), 3) for x in pk[: step * (len(pk) // step)].reshape(-1, step).max(1)]
    a1 = json.load(open(f"{S}/out/allin1/{nn}.json")); bt = json.load(open(f"{S}/out/beat_this/{nn}.json"))
    m = meta[nn]; path = paths[nn]
    tracks.append({"id": nn, "name": f"{m['artist']} - {m['title']}", "file": os.path.basename(path), "path": path,
                   "crates": [c for c, v in crates.items() if nn in v], "t1": round(t1s, 4), "bar_s": bs, "bpm": round(240 / bs, 2),
                   "dur": float(f["dur"]), "peaks": peaks, "beats": [round(x, 3) for x in bt["beats"]], "downbeats": [round(x, 3) for x in bt["downbeats"]],
                   "grid_source": g["source"], "grid_flags": g.get("flags", []), "cues": cues, "loops": loops})
os.makedirs(OUT, exist_ok=True)
cr = [{"name": c, "ids": [i for i in v if i in done]} for c, v in crates.items()]
json.dump({"built": time.strftime("%Y-%m-%d %H:%M PT"), "engine": "rules v4 (allin1 harmonix-fold0, no lyrics/lead-vocal pass)", "crates": cr, "tracks": tracks}, open(f"{OUT}/data.json.tmp", "w"))
os.replace(f"{OUT}/data.json.tmp", f"{OUT}/data.json")
json.dump(stats, open(f"{S}/stats.json", "w"), indent=1)
print(len(tracks), "tracks;", json.dumps(stats))
