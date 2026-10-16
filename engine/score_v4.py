"""Final by-letter score (exact bar on the Serato grid == within 1 beat, since all cues are bar-snapped) vs Cullen's truth.
Variants: v3, v3+gemini v2, v4 (v3 + lead START + drop-check EXIT), v4+gemini v3 (picks applied to non-auto cues only)."""
import json, os, sys, math
R = "/workspace/research/dj-auto-cues/proof/real"; sys.path.insert(0, f"{R}/tools")
ns = {}; exec(open(f"{R}/tools/gemini_v2.py").read().split("def call(")[0], ns); M, BT = ns["M"], ns["bars_of_truth"]
def plan(nn, d): return json.load(open(f"{R}/cues_v2/allin1/{d}/{nn}.json"))["cues"]
def with_gem(nn, d, gdir):
    c = plan(nn, d); gp = f"{R}/{gdir}/{nn}.json"; out = {L: c.get(L, {}).get("bar") for L in "ABCDGH"}
    if not os.path.exists(gp): return out, 0
    r = json.load(open(gp)); a = r.get("answer", {}); a = a[0] if isinstance(a, list) and a else a
    if not isinstance(a, dict): return out, r.get("cost_usd", 0)
    for L in "BHCD":
        pk = (a.get(L) or {}).get("pick")
        if isinstance(pk, int) and pk in r["candidates"].get(L, []) and not (L in "BH" and c[L].get("status") == "set"): out[L] = pk
    return out, r.get("cost_usd", 0)
def derived(o, nn):
    g = json.load(open(f"{R}/cues/allin1/{nn}.json"))["grid"]; first = math.ceil(-g["t1"] / g["bar_s"] - 1e-6)
    if o["B"] is not None:
        n = next((k for k in (8, 4, 2, 1) if o["B"] - k >= max(first, 0)), None); o["A"] = o["B"] - n if n else None; o["An"] = n
    if o["H"] is not None: o["G"] = o["H"] - 4
    return o
def truthA(nn):
    ns2 = ns; tr = BT(nn); g = json.load(open(f"{R}/cues/allin1/{nn}.json"))["grid"]
    tc = {c["index"]: c for c in ns["T"][nn]["markers2"] if c["type"] == "CUE"}; off = g["offset_ms"] / 1000
    for s, cat in M[nn].items():
        if cat in ("PRE", "GO"): tr[{"PRE": "A", "GO": "G"}[cat]] = round((tc["ABCDEFGH".index(s)]["ms"] / 1000 + off - g["t1"]) / g["bar_s"])
    return tr
VARS = {"v3": lambda nn: (derived({L: plan(nn, "go16").get(L, {}).get("bar") for L in "ABCDGH"}, nn), 0),
        "v3+gemini_v2": lambda nn: (lambda o: (derived(o[0], nn), o[1]))(with_gem(nn, "go16", "gemini_v2")),
        "v4": lambda nn: (derived({L: plan(nn, "v4_go16").get(L, {}).get("bar") for L in "ABCDGH"}, nn), 0),
        "v4+gemini_v3": lambda nn: (lambda o: (derived(o[0], nn), o[1]))(with_gem(nn, "v4_go16", "gemini_v3"))}
rows = {}
for k, f in VARS.items():
    sc = {L: [0, 0] for L in "ABCDGH"}; cost = 0
    for nn in sorted(M):
        o, c = f(nn); cost += c; tr = truthA(nn)
        for L in "ABCDGH":
            if L not in tr: continue
            if L == "A" and o.get("An") != 4: continue    # his truth A is 16 beats: compare 4BS cases only
            sc[L][0] += 1; sc[L][1] += o.get(L) is not None and o[L] == tr[L]
    rows[k] = (sc, cost)
print("| variant | A (4BS only) | B START | C 1st Ch | D Last Ch | G GO@16 | H EXIT | total | Gemini $ |")
for k, (sc, cost) in rows.items():
    tot = sum(v[1] for v in sc.values()), sum(v[0] for v in sc.values())
    print(f"| {k} | " + " | ".join(f"{sc[L][1]}/{sc[L][0]}" for L in "ABCDGH") + f" | {tot[0]}/{tot[1]} | {cost:.2f} |")
