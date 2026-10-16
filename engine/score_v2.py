"""Score v2 (by letter) against Cullen's cues. A/G scored at 16 beats (his truth uses 16). 32-beat run: placement checks only."""
import json, os
R = "/workspace/research/dj-auto-cues/proof/real"
src = open(f"{R}/tools/score_vs_truth.py").read().split("# engine slot")[0]; ns = {}; os.chdir(R); exec(src, ns); T, M = ns["T"], ns["M"]
CAT = {"PRE": "A", "START": "B", "FC": "C", "LC": "D", "GO": "G", "EXIT": "H"}
OLD = {"A": "A", "B": "B", "C": "G", "D": "C", "G": "H", "H": "D"}  # v1 slot that meant the same thing
rows = []
for eng in ["allin1", "songformer"]:
    for nn in sorted(M):
        p = f"{R}/cues_v2/{eng}/go16/{nn}.json"
        if not os.path.exists(p): continue
        v2 = json.load(open(p))["cues"]; v1 = json.load(open(f"{R}/cues/{eng}/{nn}.json"))["cues"]; v0 = json.load(open(f"{R}/cues_v2/{eng}/nolyr_go16/{nn}.json"))["cues"]
        beat = 60 / T[nn]["serato_beatgrid"][-1]["bpm"]
        tc = {c["index"]: c for c in T[nn]["markers2"] if c["type"] == "CUE"}
        for s, cat in M[nn].items():
            if cat not in CAT: continue
            L = CAT[cat]; t = tc["ABCDEFGH".index(s)]["ms"] / 1000
            o = v1.get(OLD[L], {}); ot = o.get("time_serato") if o.get("status") == "set" else None
            n = v2.get(L, {"status": "empty"}); nt = n.get("time"); z = v0.get(L, {}); zt = z.get("time")
            if L == "A" and n.get("bars_before") != 4: nt = None  # truth A is 16 beats: compare only 4BS cases
            if L == "A" and z.get("bars_before") != 4: zt = None
            rows.append(dict(eng=eng, nn=nn, L=L, truth=t, before=None if ot is None else (ot - t) / beat,
                             after=None if nt is None else (nt - t) / beat, mid=None if zt is None else (zt - t) / beat, a_name=n.get("name"), status=n["status"], conf=n.get("confidence"), why=n.get("reason")))
json.dump(rows, open(f"{R}/score_v2_rows.json", "w"), indent=1)
ok = lambda d, w=1.0: d is not None and abs(d) <= w
def pct(a, b): return f"{a}/{b} ({100*a//b if b else 0}%)"
print("| engine | cue | n | v1 (±1 beat) | v2 no lyrics | v2 + lyrics | auto-set (conf≥0.85) | auto-set correct |")
for eng in ["allin1", "songformer"]:
    for L in "ABCDGH":
        rs = [r for r in rows if r["eng"] == eng and r["L"] == L]
        if L == "A": rs = [r for r in rs if r["a_name"] == "4BS"]
        if not rs: continue
        au = [r for r in rs if r["status"] == "set"]
        print(f"| {eng} | {L} | {len(rs)} | {pct(sum(ok(r['before']) for r in rs), len(rs))} | {pct(sum(ok(r['mid']) for r in rs), len(rs))} | {pct(sum(ok(r['after']) for r in rs), len(rs))} | {len(au)} | {pct(sum(ok(r['after']) for r in au), len(au))} |")
print("\nMisses (allin1, after):")
for r in rows:
    if r["eng"] == "allin1" and not ok(r["after"]):
        print(r["nn"], r["L"], "off", None if r["after"] is None else round(r["after"], 1), "beats", r["status"], r["conf"], "|", r["why"])
from collections import Counter
for eng in ["allin1", "songformer"]:
    lv = Counter(); g32 = []; lp = Counter()
    for nn in sorted(M):
        p = f"{R}/cues_v2/{eng}/go32/{nn}.json"
        if not os.path.exists(p): continue
        d = json.load(open(p)); c = d["cues"]; lv[c["A"].get("name") if c.get("A", {}).get("bar") is not None else "empty"] += 1
        G, B = c.get("G", {}), c.get("B", {})
        if G.get("bar") is not None and (G["flags"] or (G.get("time") or 0) < 0): g32.append(f"{nn}:{G['flags']}")
        for l in d["loops"]: lp[(l["loop"], "clean" if l["clean"] else ("unchecked" if l["bar"] is not None else "none"))] += 1
    print(f"\n{eng} A levels: {dict(lv)}; GO@32 all bar-snapped (downbeats); GO@32 problems: {g32 or 'none'}; loops: {dict(lp)}")
