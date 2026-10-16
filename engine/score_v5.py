"""Score v4 vs v5 + per-change ablation. Truth = Cullen's review marks only (last correct/set_here per track+cue, within 1 beat).
The Serato cues in truth_tags.json are Mixed In Key's: reported only as 'vs Mixed In Key'. Writes ../score_v5.json"""
import json, os, sys, collections, shutil, tempfile
R = "/workspace/research/dj-auto-cues/proof/real"; sys.path.insert(0, f"{R}/tools"); import rules_v5 as V
import warnings; warnings.filterwarnings("ignore")
ev = json.load(open(f"{R}/corrections/corrections.json")); last = {}
for e in ev:
    if e["cue"] in "ABCDGH" and e["action"] in ("correct", "set_here") and e.get("to_beat") is not None: last[(e["track"], e["cue"])] = e["to_beat"]
    if e["action"] == "clear": last.pop((e["track"], e["cue"]), None)
ns = {}; exec(open(f"{R}/tools/gemini_v2.py").read().split("def call(")[0], ns); BT = ns["bars_of_truth"]
def beats(plan): return {L: (None if plan["cues"].get(L, {}).get("bar") is None else plan["cues"][L]["bar"] * 4) for L in "ABCDGH"}
def score(plans):
    m = collections.defaultdict(lambda: [0, 0]); t = collections.defaultdict(lambda: [0, 0]); auto = [0, 0]
    for (nn, L), tb in last.items():
        if nn not in plans: continue
        v = beats(plans[nn])[L]; m[L][1] += 1; ok = v is not None and abs(v - tb) <= 1; m[L][0] += ok
        if plans[nn]["cues"].get(L, {}).get("status") == "set": auto[1] += 1; auto[0] += ok
    for nn, p in plans.items():
        try: tr = BT(nn)
        except Exception: continue
        b = beats(p)
        for L, x in tr.items(): t[L][1] += 1; t[L][0] += b.get(L) is not None and abs(b[L] - x * 4) <= 1
    nset = sum(x["status"] == "set" for p in plans.values() for L, x in p["cues"].items() if L in "ABGH")
    tot = lambda D: [sum(v[0] for v in D.values()), sum(v[1] for v in D.values())]
    return {"marks": {L: m[L] for L in "ABCDGH"}, "marks_total": tot(m), "mik": {L: t[L] for L in "ABCDGH"}, "mik_total": tot(t), "auto_set_cues": nset, "auto_set_marked_correct": auto}
def load_dir(d): return {f[:-5]: json.load(open(f"{d}/{f}")) for f in os.listdir(d) if f.endswith(".json")}
res = {"v4": score(load_dir(f"{R}/cues_v2/allin1/v4_go32"))}
tmp = tempfile.mkdtemp()
cfg = sys.argv[1] if len(sys.argv) > 1 else None
res["v5_all"] = score(V.run(R, f"{tmp}/all", lambda f: True, cfg))
for fl in V.FLAGS:
    res[f"only_{fl}"] = score(V.run(R, f"{tmp}/o_{fl}", lambda f, fl=fl: f == fl, cfg))
    res[f"all_minus_{fl}"] = score(V.run(R, f"{tmp}/m_{fl}", lambda f, fl=fl: f != fl, cfg))
shutil.rmtree(tmp); json.dump(res, open(f"{R}/score_v5.json", "w"), indent=1)
f = lambda x: f"{x[0]}/{x[1]}"
print("| run | marks total | A | B | C | D | G | H | vs Mixed In Key total | auto-set A/B/G/H cues | auto-set & marked right |")
for k, r in res.items(): print(f"| {k} | {f(r['marks_total'])} | " + " | ".join(f(r["marks"][L]) for L in "ABCDGH") + f" | {f(r['mik_total'])} | {r['auto_set_cues']} | {f(r['auto_set_marked_correct'])} |")
print("marks:", len(last), "cues on", len({k[0] for k in last}), "songs")
