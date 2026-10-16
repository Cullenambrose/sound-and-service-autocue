import json, os, glob
R = "/workspace/research/dj-auto-cues/proof/real"; os.chdir(R)
ns = {}; exec(open("tools/score_vs_truth.py").read().split("# engine slot")[0], ns); T, M = ns["T"], ns["M"]
CAT = {"PRE": "A", "START": "B", "FC": "C", "LC": "D", "GO": "G", "EXIT": "H"}
ok = lambda d: d is not None and abs(d) <= 1
res = {L: [0, 0, 0, 0, 0] for L in "ABCDGH"}  # n, without, with, auto-without-correct/auto-n tracked separately
cost = []; changes = []
for nn in sorted(M):
    pg = f"{R}/cues_v2/allin1/go16_gem/{nn}.json"
    if not os.path.exists(pg): continue
    a = json.load(open(f"{R}/cues_v2/allin1/go16/{nn}.json"))["cues"]; b = json.load(open(pg))["cues"]
    gm = json.load(open(f"{R}/gemini/{nn}.json"))
    if gm["cost_usd"] == 0 or any("error" in t for t in gm["tiebreaks"]): continue
    cost.append(gm["cost_usd"])
    beat = 60 / T[nn]["serato_beatgrid"][-1]["bpm"]; tc = {c["index"]: c for c in T[nn]["markers2"] if c["type"] == "CUE"}
    for s, cat in M[nn].items():
        if cat not in CAT: continue
        L = CAT[cat]; t = tc["ABCDEFGH".index(s)]["ms"] / 1000
        if L == "A" and (a[L].get("bars_before") != 4 or b[L].get("bars_before") != 4): continue
        da = None if a.get(L, {}).get("time") is None else (a[L]["time"] - t) / beat
        db = None if b.get(L, {}).get("time") is None else (b[L]["time"] - t) / beat
        res[L][0] += 1; res[L][1] += ok(da); res[L][2] += ok(db)
        if a.get(L, {}).get("bar") != b.get(L, {}).get("bar"): changes.append(f"{nn} {L}: {a[L].get('bar')}->{b[L].get('bar')} {'better' if ok(db) and not ok(da) else 'worse' if ok(da) and not ok(db) else 'same'}")
print("| cue | n | without Gemini | with Gemini |")
for L, (n, x, y, *_ ) in res.items():
    if n: print(f"| {L} | {n} | {x}/{n} | {y}/{n} |")
print("changes:", changes)
if cost: print(f"songs {len(cost)}, total ${sum(cost):.4f}, per song ${sum(cost)/len(cost):.4f}")
