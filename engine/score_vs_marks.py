"""Score engine (v4_go32) and Gemini (gemini_v2 picks, as shown on the review page) against Cullen's review marks
(last 'correct' / 'set_here' per track+cue in corrections/corrections.json). Match = within 1 beat. Also old Serato truth for comparison."""
import json, os, collections
R = "/workspace/research/dj-auto-cues/proof/real"
ev = json.load(open(f"{R}/corrections/corrections.json")); last = {}
for e in ev:
    if e["cue"] in "ABCDGH" and e["action"] in ("correct", "set_here") and e.get("to_beat") is not None: last[(e["track"], e["cue"])] = e["to_beat"]
ns = {}; exec(open(f"{R}/tools/gemini_v2.py").read().split("def call(")[0], ns); BT = ns["bars_of_truth"]
def eng(nn): c = json.load(open(f"{R}/cues_v2/allin1/v4_go32/{nn}.json"))["cues"]; return {L: (None if c.get(L, {}).get("bar") is None else c[L]["bar"] * 4) for L in "ABCDGH"}
def gem(nn):
    p = f"{R}/gemini_v2/{nn}.json"; out = {}
    if not os.path.exists(p): return out
    r = json.load(open(p)); a = r.get("answer", {}); a = a[0] if isinstance(a, list) and a else a
    for L in "BCDH":
        pk = (a.get(L) or {}).get("pick") if isinstance(a, dict) else None
        if isinstance(pk, int): out[L] = pk * 4
    e = eng(nn)
    if "B" in out and e["A"] is not None and e["B"] is not None: out["A"] = out["B"] - (e["B"] - e["A"])
    if "H" in out: out["G"] = out["H"] - 32
    return out
S = {k: collections.defaultdict(lambda: [0, 0, 0]) for k in ("engine", "gemini")}; rows = []
for (nn, L), t in sorted(last.items()):
    e, g = eng(nn).get(L), gem(nn).get(L)
    for k, v in (("engine", e), ("gemini", g)):
        s = S[k][L]; s[1] += 1
        if v is not None and abs(v - t) <= 1: s[0] += 1
        elif v is not None and abs(v - t) % 16 <= 1 or v is not None and abs(v - t) % 16 >= 15: s[2] += 1
    rows.append((nn, L, t, e, g))
T = {k: collections.defaultdict(lambda: [0, 0]) for k in ("engine", "gemini")}
for nn in sorted({k[0] for k in last} | set(f"{i:02d}" for i in range(1, 21))):
    try: tr = BT(nn)
    except Exception: continue
    for L, b in tr.items():
        for k, v in (("engine", eng(nn).get(L)), ("gemini", gem(nn).get(L))):
            T[k][L][1] += 1; T[k][L][0] += v is not None and abs(v - b * 4) <= 1
print("| cue | marks n | engine vs marks | Gemini vs marks | engine off by whole 4-bar phrase(s) | Gemini off by phrase(s) | engine vs old Serato truth | Gemini vs old truth |")
for L in "ABCDGH":
    m = S["engine"][L][1]
    if not m and not T["engine"][L][1]: continue
    f = lambda x: f"{x[0]}/{x[1]}"
    print(f"| {L} | {m} | {f(S['engine'][L])} | {f(S['gemini'][L])} | {S['engine'][L][2]} | {S['gemini'][L][2]} | {f(T['engine'][L]) if T['engine'][L][1] else '-'} | {f(T['gemini'][L]) if T['gemini'][L][1] else '-'} |")
tot = lambda D, i=0: (sum(v[0] for v in D.values()), sum(v[1] for v in D.values()))
print("TOTAL marks: engine %d/%d, gemini %d/%d; old truth: engine %d/%d, gemini %d/%d" % (*tot(S["engine"]), *tot(S["gemini"]), *tot(T["engine"]), *tot(T["gemini"])))
for r in rows: print("row", r)
