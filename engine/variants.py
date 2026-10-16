"""Score variants by letter vs Cullen's truth (bars; within 1 beat == exact bar here since all cues are bar-snapped).
base = rules v2 (go16, lyrics). Each variant changes only some cues; we keep a variant per cue only where it scores higher."""
import json, os, sys, math, numpy as np
R = "/workspace/research/dj-auto-cues/proof/real"; sys.path.insert(0, f"{R}/tools")
import candidates as CA
ns = {}; exec(open(f"{R}/tools/gemini_v2.py").read().split("def call(")[0], ns); M, BT = ns["M"], ns["bars_of_truth"]
def grid(nn): return json.load(open(f"{R}/cues/allin1/{nn}.json"))["grid"]
def base(nn): return {L: json.load(open(f"{R}/cues_v2/allin1/go16/{nn}.json"))["cues"][L].get("bar") for L in "BCDH"}
def conf(nn): return {L: json.load(open(f"{R}/cues_v2/allin1/go16/{nn}.json"))["cues"][L].get("status") for L in "BCDH"}
def lead_frac(nn, g, nb):
    p = f"{R}/out/lead/{nn}.npz"
    if not os.path.exists(p): return None
    s = np.load(p); x = s["lead"]; h = float(s["hop_s"]); on = x > 0.2 * (np.percentile(x, 95) + 1e-9)
    return np.array([on[max(0, int((g["t1"] + j * g["bar_s"]) / h)):max(1, int((g["t1"] + (j + 1) * g["bar_s"]) / h))].mean() if g["t1"] + (j+1) * g["bar_s"] > 0 else 0 for j in range(nb)])
def nbars(nn, g): return int(math.ceil(float(np.load(f"{R}/features/{nn}.npz")["dur"]) / g["bar_s"]))
def v_lead(nn):
    """START = first bar where the LEAD vocal (not backing/ad-libs) is on for >=2 of the next 3 bars; EXIT = bar after the lead's last sustained run near engine EXIT"""
    g = grid(nn); b = base(nn); lf = lead_frac(nn, g, nbars(nn, g)); out = dict(b)
    if lf is None: return out
    on = lf > 0.35
    for j in range(len(on) - 3):
        if on[j] and on[j:j + 3].sum() >= 2:
            if b["B"] is not None and abs(j - b["B"]) <= 2: out["B"] = j
            break
    if b["H"] is not None:
        for j in range(b["H"] - 2, b["H"] + 3):
            if 0 < j < len(on) - 2 and on[j - 1] and not on[j] and not on[j + 1]: out["H"] = j; break
    return out
def be(nn, g, nb):
    st = CA.stem_bar_frac(nn, g, nb); return None if st is None else (st["drums"] + st["bass"]) / 2
def v_drop(nn):
    """handover drop check: chorus/drop cues must sit where drums+bass jump up vs the bar before; EXIT where they fall.
    Move each cue to the nearest candidate (expanded list) within 2 bars passing the check; keep it if none do."""
    g = grid(nn); b = base(nn); nb = nbars(nn, g); e = be(nn, g, nb); out = dict(b)
    if e is None: return out
    cand = CA.build(nn)
    def ok(j, up): return 1 <= j < nb and ((e[j] - e[j - 1] >= 0.25) if up else (e[j - 1] - e[j] >= 0.25))
    for L, up in (("C", True), ("D", True), ("H", False)):
        if b[L] is None or ok(b[L], up): continue
        near = sorted((j for j in cand[L] if abs(j - b[L]) <= 2 and ok(j, up)), key=lambda j: abs(j - b[L]))
        if near: out[L] = near[0]
    return out
def v_learn(nn):
    loo = json.load(open(f"{R}/learn_loo.json"))[nn]; b = base(nn)
    return {L: (None if b[L] is None else b[L] + loo.get(L, 0)) for L in b}
VAR = {"base": base, "lead_vocal": v_lead, "drop_check": v_drop, "learned_LOO": v_learn}
def score(fn):
    res = {L: [0, 0] for L in "BCDH"}
    for nn in sorted(M):
        tr = BT(nn); p = fn(nn)
        for L in "BCDH":
            if L in tr: res[L][0] += 1; res[L][1] += p[L] is not None and p[L] == tr[L]
    return res
if __name__ == "__main__":
    S = {k: score(f) for k, f in VAR.items()}
    print("| variant | " + " | ".join("BCDH") + " |")
    for k, r in S.items(): print(f"| {k} | " + " | ".join(f"{r[L][1]}/{r[L][0]}" for L in "BCDH") + " |")
    keep = {k: [L for L in "BCDH" if S[k][L][1] > S["base"][L][1]] for k in VAR if k != "base"}
    print("keep (cues where variant beats base):", keep); json.dump({"scores": S, "keep": keep}, open(f"{R}/variants_score.json", "w"), indent=1)
