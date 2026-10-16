"""Correction learner. Truth = Cullen's Serato cues, overridden per cue by review-page corrections (corrections.json) when present.
Learns per cue and per song group the bar offset (truth - engine) and keeps a rule only when leave-one-out scoring says it helps.
Writes ../learned_profile.json (loaded by rules_v2) and ../learn_loo.json (per-song LOO picks for scoring)."""
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
    tr = bars_of_truth(nn); g = json.load(open(f"{R}/cues/allin1/{nn}.json"))["grid"]
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
    json.dump(prof, open(f"{R}/learned_profile.json", "w"), indent=1); json.dump(loo, open(f"{R}/learn_loo.json", "w"), indent=1)
    print(json.dumps(full, indent=0)[:1500]); print("corrections used:", len(corr))
if __name__ == "__main__": main()
