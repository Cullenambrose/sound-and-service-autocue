"""Correction learner. Truth = Cullen's Serato cues, overridden per cue by review-page corrections (corrections.json) when present.
Learns per cue and per song group the bar offset (truth - engine) and keeps a rule only when leave-one-out scoring says it helps.
Writes ../learned_profile_marks.json (loaded by rules_v2) and ../learn_loo_marks.json (per-song LOO picks for scoring)."""
import json, os, re, sys, statistics as stt
from collections import Counter
R = "/workspace/research/dj-auto-cues/proof/real"; sys.path.insert(0, f"{R}/tools")
ns = {}; exec(open(f"{R}/tools/gemini_v2.py").read().split("def call(")[0], ns); M, bars_of_truth = ns["M"], ns["bars_of_truth"]
CORR = os.environ.get("CORRECTIONS", f"{R}/corrections/corrections.json")
def song_type(nn):
    n = json.load(open(f"{R}/cues/allin1/{nn}.json")); import csv
    name = {r["n"].zfill(2): r["laptop_path"] for r in csv.DictReader(open(f"{R}/MANIFEST.csv"))}[nn].lower()
    if re.search(r"edit|cutdown|short|clean extended|jump off", name): t = "edit"
    elif re.search(r"remix|rmx|re-drum|redrum|mashup|mix\)|bootleg", name): t = "remix"
    else: t = "original"
    bpm = 240 / n["grid"]["bar_s"]; band = "slow<100" if bpm < 100 else "mid100-125" if bpm <= 125 else "fast>125"
    return {"type": t, "tempo": band}
def truth(nn, corr):
    tr = {}; g = json.load(open(f"{R}/cues/allin1/{nn}.json"))["grid"]
    for ev in corr:   # last decision per cue wins; to_time is Serato seconds
        if ev.get("track") == nn and ev.get("cue") in "BCDH" and ev.get("to_time") is not None:
            tr[ev["cue"]] = round((ev["to_time"] + g["offset_ms"] / 1000 - g["t1"]) / g["bar_s"])
    return tr
def engine(nn, base="go16"):
    return {L: json.load(open(f"{R}/cues_v2/allin1/{base}/{nn}.json"))["cues"][L].get("bar") for L in "BCDH"}
def ctx(nn):
    d = json.load(open(f"{R}/cues/allin1/{nn}.json")); H = engine(nn)["H"]
    nxt = next((s["lab"] for s in d["segments"] if H is not None and s["j0"] >= H - 1), "none")
    return {**song_type(nn), "after_exit": nxt if nxt in ("outro", "end", "chorus", "verse", "inst") else "other"}
KEYS = [None, "type", "tempo", "after_exit"]
def learn(songs, corr):
    """per cue: for each grouping key, mode offset per group value; returns rules {cue: {key, table}}"""
    data = {nn: (truth(nn, corr), engine(nn), ctx(nn)) for nn in songs}
    out = {}
    for L in "BCDH":
        best = None
        for key in KEYS:
            tab = {}
            groups = Counter()
            for nn, (tr, en, cx) in data.items():
                if L in tr and en[L] is not None: groups[cx[key] if key else "*"] += 1
            for gv in groups:
                offs = [tr[L] - en[L] for nn, (tr, en, cx) in data.items() if L in tr and en[L] is not None and (cx[key] if key else "*") == gv]
                o, k = Counter(offs).most_common(1)[0]
                if len(offs) >= 3 and k / len(offs) >= 0.4: tab[gv] = {"offset_bars": o, "support": f"{k}/{len(offs)}"}
            # training fit: how many exact hits with this table vs no change
            fit = sum((tr[L] - en[L]) == tab.get(cx[key] if key else "*", {"offset_bars": 0})["offset_bars"] for nn, (tr, en, cx) in data.items() if L in tr and en[L] is not None)
            base = sum(tr[L] == en[L] for nn, (tr, en, cx) in data.items() if L in tr and en[L] is not None)
            if best is None or fit > best[0]: best = (fit, key, tab, base)
        fit, key, tab, base = best
        out[L] = {"key": key, "table": tab, "train_hits": fit, "train_hits_no_rule": base} if fit > base else {"key": None, "table": {}, "train_hits": base}
    return out
def offset_for(rules, L, cx):
    r = rules.get(L, {}); k = r.get("key"); t = r.get("table", {})
    return t.get(cx[k] if k else "*", {}).get("offset_bars", 0)
def main():
    corr = json.load(open(CORR)) if os.path.exists(CORR) else []
    songs = sorted(M); loo = {}
    for nn in songs:   # leave-one-out: rules learned without this song (and without its corrections)
        rules = learn([x for x in songs if x != nn], [e for e in corr if e.get("track") != nn])
        loo[nn] = {L: offset_for(rules, L, ctx(nn)) for L in "BCDH"}
    full = learn(songs, corr)
    prof = {"built_from": {"truth_songs": len(songs), "corrections": len(corr), "corrections_file": CORR}, "rules": full,
            "song_context": {nn: ctx(nn) for nn in songs}}
    json.dump(prof, open(f"{R}/learned_profile_marks.json", "w"), indent=1); json.dump(loo, open(f"{R}/learn_loo_marks.json", "w"), indent=1)
    print(json.dumps(full, indent=0)[:1500]); print("corrections used:", len(corr))
if __name__ == "__main__" and "--buckets" not in sys.argv: main()

# ---- v5 change 8: per-genre-bucket parameter tuning (python learner_marks.py --buckets [--min-marks N]) ----
def tune_buckets(min_marks=8):
    """Grid-search a few v5 parameters per bucket against Cullen's review marks (corrections/*.json, last correct/set_here
    per track+cue, within 1 beat). A bucket's value changes only when it has >= min_marks marks AND strictly beats the default.
    Writes ../genre_params_v5.tuned.json (use with rules_v5.py --config). The default file is never overwritten."""
    import glob, itertools, rules_v5 as V5
    ev = []
    for f in sorted(glob.glob(f"{R}/corrections/corrections*.json")): ev += json.load(open(f))
    last = {}
    for e in ev:
        if e.get("cue") in list("ABCDGH") and e.get("action") in ("correct", "set_here") and e.get("to_beat") is not None: last[(e["track"], e["cue"])] = e["to_beat"]
    cfg = json.load(open(f"{R}/genre_params_v5.json")); grid = {"exit_phrase_bars": [4, 8], "exit_snap_radius": [1, 2], "chorus_sim_thr": [0.7, 0.8, 0.9], "drop_frac": [0.15, 0.25, 0.35]}
    songs = sorted({k[0] for k in last}); on = lambda f: True; byb = {}
    for nn in songs: byb.setdefault(V5.bucket(V5.Song(R, nn))[0], []).append(nn)
    report = {}
    def hits(nns, over):
        c = json.loads(json.dumps(cfg)); [c["buckets"].setdefault(b, {}).update(over) for b in c["buckets"]]; h = 0
        for nn in nns:
            p = V5.build(V5.Song(R, nn), on, c)["cues"]
            for L in "ABCDGH":
                t = last.get((nn, L)); b = p.get(L, {}).get("bar")
                if t is not None and b is not None and abs(b * 4 - t) <= 1: h += 1
        return h
    for b, nns in byb.items():
        n = sum(1 for k in last if k[0] in nns); base = hits(nns, {}); best = (base, {})
        if n >= min_marks:
            for vals in itertools.product(*grid.values()):
                over = dict(zip(grid, vals)); h = hits(nns, over)
                if h > best[0]: best = (h, over)
            cfg["buckets"][b] = best[1]
        report[b] = {"songs": nns, "marks": n, "hits_default": base, "hits_tuned": best[0], "changed": best[1] if n >= min_marks else "too few marks"}
    cfg["_tuned"] = report; json.dump(cfg, open(f"{R}/genre_params_v5.tuned.json", "w"), indent=1); print(json.dumps(report, indent=1))
if __name__ == "__main__" and "--buckets" in sys.argv:
    tune_buckets(int(sys.argv[sys.argv.index("--min-marks") + 1]) if "--min-marks" in sys.argv else 8)
