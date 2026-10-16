"""Re-score v4, v5 (8 changes), v5-fixed (+ claims 1,2,4,5 fixes), each with/without CACHED Gemini (gemini_v2 = review page picks,
gemini_v3), plus per-fix ablation. Truth = Cullen's review marks only (both cue-review folders if present); MIK = 'vs Mixed In Key'."""
import json, os, sys, glob, collections, tempfile, shutil
R = "/workspace/research/dj-auto-cues/proof/real"; sys.path.insert(0, f"{R}/tools"); import rules_v5 as V, gemini_apply_v5 as GA
import warnings; warnings.filterwarnings("ignore")
ev = []
for f in sorted(glob.glob(f"{R}/corrections/corrections*.json")): ev += json.load(open(f))
last = {}
for e in ev:
    if e.get("cue") in list("ABCDGH") and e["action"] in ("correct", "set_here") and e.get("to_beat") is not None: last[(e["track"], e["cue"])] = e["to_beat"]
    if e["action"] == "clear": last.pop((e["track"], e.get("cue")), None)
ns = {}; exec(open(f"{R}/tools/gemini_v2.py").read().split("def call(")[0], ns); BT = ns["bars_of_truth"]
def sc(plans):
    m = collections.defaultdict(lambda: [0, 0]); t = collections.defaultdict(lambda: [0, 0])
    for (nn, L), tb in last.items():
        if nn in plans: v = plans[nn]["cues"].get(L, {}).get("bar"); m[L][1] += 1; m[L][0] += v is not None and abs(v * 4 - tb) <= 1
    for nn, p in plans.items():
        try: tr = BT(nn)
        except Exception: continue
        for L, x in tr.items(): v = p["cues"].get(L, {}).get("bar"); t[L][1] += 1; t[L][0] += v is not None and v == x
    tot = lambda D: [sum(v[0] for v in D.values()), sum(v[1] for v in D.values())]
    nset = sum(x.get("status") == "set" for p in plans.values() for L, x in p["cues"].items() if L in "ABGH")
    return {"marks": dict(m), "marks_total": tot(m), "mik_total": tot(t), "mik": dict(t), "auto_set": nset}
load = lambda d: {f[:-5]: json.load(open(f"{d}/{f}")) for f in os.listdir(d) if f.endswith(".json")}
tmp = tempfile.mkdtemp(); runs = {"v4": load(f"{R}/cues_v2/allin1/v4_go32")}
runs["v5"] = V.run(R, f"{tmp}/v5", lambda f: f not in V.FIXES)
runs["v5_fixed"] = V.run(R, f"{tmp}/v5f", lambda f: f not in V.DEFAULT_OFF)
runs["v5_fixed + fix_start"] = V.run(R, f"{tmp}/v5fs", lambda f: True)
for fx in V.FIXES:
    runs[f"v5 + only {fx}"] = V.run(R, f"{tmp}/o{fx}", lambda f, fx=fx: f not in V.FIXES or f == fx)
    runs[f"v5_fixed(all 4 fixes) - {fx}"] = V.run(R, f"{tmp}/m{fx}", lambda f, fx=fx: f != fx)
res = {k: sc(v) for k, v in runs.items()}
for k in ("v4", "v5", "v5_fixed"):
    for src in ("gemini_v2", "gemini_v3"):
        res[f"{k} + {src} (cached, fixed apply)"] = sc({nn: GA.apply(p, nn, src) for nn, p in runs[k].items()})
    res[f"{k} + gemini_v3 (old apply: overrides B/H)"] = sc({nn: GA.apply(p, nn, "gemini_v3", keep_confident=False) for nn, p in runs[k].items()})
shutil.rmtree(tmp); json.dump(res, open(f"{R}/score_v5b.json", "w"), indent=1)
f = lambda x: f"{x[0]}/{x[1]}"
print("| run | vs Cullen's marks | A | B | C | D | G | H | vs Mixed In Key (B/C/D/H bars) | auto-set A/B/G/H |\n|---|---|---|---|---|---|---|---|---|---|")
for k, r in res.items(): print(f"| {k} | {f(r['marks_total'])} | " + " | ".join(f(r["marks"].get(L, [0, 0])) for L in "ABCDGH") + f" | {f(r['mik_total'])} | {r['auto_set']} |")
print(f"\nmarks: {len(last)} cues on {len({k[0] for k in last})} songs ({sorted({k[0] for k in last})}); Gemini new spend $0 (all cached)")
# changed positions v5 -> v5_fixed
for nn in sorted(runs["v5_fixed"]):
    for L in "ABCDGH":
        a, b = runs["v5"][nn]["cues"].get(L, {}).get("bar"), runs["v5_fixed"][nn]["cues"].get(L, {}).get("bar")
        if a != b: print(f"moved {nn} {L}: {a} -> {b}" + (f"  (mark {last[(nn, L)]/4:g})" if (nn, L) in last else ""))
