"""Round-2 re-score: v4, v5 (8 changes), v5_fixed (round-1 fixes), v5_r2 (+ round-2 fixes), fix_start on/off, per-r2 ablation,
cached Gemini (gemini_v2 = review-page picks, $0). Truth = Cullen's marks via marks.py; Mixed In Key reported separately.
Also: auto-set count and auto-set precision on marked cues."""
import json, os, sys, collections, tempfile, shutil
R = "/workspace/research/dj-auto-cues/proof/real"; sys.path.insert(0, f"{R}/tools"); import rules_v5 as V, gemini_apply_v5 as GA, marks as MK
import warnings; warnings.filterwarnings("ignore")
last = MK.truth_beats()
ns = {}; exec(open(f"{R}/tools/gemini_v2.py").read().split("def call(")[0], ns); BT = ns["bars_of_truth"]
def sc(plans):
    m = collections.defaultdict(lambda: [0, 0]); t = [0, 0]; ap = [0, 0]
    for (nn, L), tb in last.items():
        if nn not in plans: continue
        x = plans[nn]["cues"].get(L, {}); v = x.get("bar"); ok = v is not None and abs(v * 4 - tb) <= 1; m[L][1] += 1; m[L][0] += ok
        if x.get("status") == "set": ap[1] += 1; ap[0] += ok
    for nn, p in plans.items():
        try: tr = BT(nn)
        except Exception: continue
        for L, x in tr.items(): t[1] += 1; t[0] += p["cues"].get(L, {}).get("bar") == x
    return {"marks": dict(m), "marks_total": [sum(v[0] for v in m.values()), sum(v[1] for v in m.values())], "mik_total": t,
            "auto_set": sum(x.get("status") == "set" for p in plans.values() for L, x in p["cues"].items() if L in "ABGH"), "auto_set_on_marks": ap}
load = lambda d: {f[:-5]: json.load(open(f"{d}/{f}")) for f in os.listdir(d) if f.endswith(".json")}
tmp = tempfile.mkdtemp(); base_off = set(V.DEFAULT_OFF)
r1 = lambda f: f not in V.R2 and f not in base_off
r2 = lambda f: f not in base_off
runs = {"v4": load(f"{R}/cues_v2/allin1/v4_go32"),
        "v5 (8 changes)": V.run(R, f"{tmp}/a", lambda f: f not in V.FIXES and f not in V.R2),
        "v5_fixed (round 1)": V.run(R, f"{tmp}/b", r1),
        "v5_r2 (round 1+2)": V.run(R, f"{tmp}/c", r2),
        "v5_r2 + fix_start + r2_pickup (all on)": V.run(R, f"{tmp}/d", lambda f: True),
        "v5_fixed + fix_start (no r2_start_vocal)": V.run(R, f"{tmp}/e", lambda f: f not in V.R2)}
for fx in V.R2:
    runs[f"v5_fixed + only {fx}"] = V.run(R, f"{tmp}/o{fx}", lambda f, fx=fx: r1(f) or f == fx)
    runs[f"v5_r2 - {fx}"] = V.run(R, f"{tmp}/m{fx}", lambda f, fx=fx: r2(f) and f != fx)
res = {k: sc(v) for k, v in runs.items()}
for k in ("v4", "v5_fixed (round 1)", "v5_r2 (round 1+2)"):
    res[f"{k} + Gemini v2 picks (cached)"] = sc({nn: GA.apply(p, nn, "gemini_v2") for nn, p in runs[k].items()})
shutil.rmtree(tmp); json.dump(res, open(f"{R}/score_v5c.json", "w"), indent=1)
f = lambda x: f"{x[0]}/{x[1]}"
print("| run | vs Cullen's marks | A | B | C | D | G | H | vs Mixed In Key | auto-set A/B/G/H | auto-set right on marks |\n|---|---|---|---|---|---|---|---|---|---|---|")
for k, r in res.items(): print(f"| {k} | {f(r['marks_total'])} | " + " | ".join(f(r["marks"].get(L, [0, 0])) for L in "ABCDGH") + f" | {f(r['mik_total'])} | {r['auto_set']} | {f(r['auto_set_on_marks'])} |")
print(f"\nmarks: {len(last)} cues on {len(MK.marked_songs())} songs {MK.marked_songs()}; Gemini new spend $0")
for nn in sorted(runs["v5_r2 (round 1+2)"]):
    for L in "ABCDGH":
        a, b = runs["v5_fixed (round 1)"][nn]["cues"].get(L, {}).get("bar"), runs["v5_r2 (round 1+2)"][nn]["cues"].get(L, {}).get("bar")
        if a != b: print(f"moved r1->r2 {nn} {L}: {a} -> {b}" + (f" (mark bar {last[(nn, L)]/4:g})" if (nn, L) in last else ""))
