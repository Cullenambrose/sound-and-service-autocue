"""Apply CACHED Gemini picks (gemini_v2/ or gemini_v3/, no new API calls, $0) onto any plan (v4/v5), fixing gemini_v3.apply (claim 7):
reads the given plan instead of go16 (v3); never overrides a confident B/H (status 'set' or conf >= 0.85, switch keep_confident);
afterwards recomputes A (8/4/2/1 ladder), G = EXIT - 32 beats, and statuses. Gemini cues stay suggestions (status 'suggest')."""
import json, os, math, copy
R = "/workspace/research/dj-auto-cues/proof/real"
def picks(nn, src):
    p = f"{R}/{src}/{nn}.json"
    if not os.path.exists(p): return {}, {}
    r = json.load(open(p)); a = r.get("answer", {}); a = a[0] if isinstance(a, list) and a else a
    if not isinstance(a, dict): return {}, {}
    return {L: (a.get(L) or {}).get("pick") for L in "BCDH"}, r.get("candidates", {})
def apply(plan, nn, src="gemini_v3", keep_confident=True):
    g = json.load(open(f"{R}/cues/allin1/{nn}.json"))["grid"]; new = copy.deepcopy(plan); c = new["cues"]
    tb = lambda j: round(g["t1"] + j * g["bar_s"] - g["offset_ms"] / 1000, 3); pk, cand = picks(nn, src)
    for L, j in pk.items():
        if not isinstance(j, int) or (cand and j not in cand.get(L, [])) or L not in c: continue
        if keep_confident and L in "BH" and (c[L].get("status") == "set" or (c[L].get("confidence") or 0) >= 0.85): continue
        if c[L].get("bar") != j: c[L].update(bar=j, time=tb(j), gemini=True, status="suggest")
    first = max(0, math.ceil(-g["t1"] / g["bar_s"] - 1e-6)); B, H = c["B"].get("bar"), c["H"].get("bar")
    if B is not None and c.get("A", {}).get("bar") is not None or (B is not None and c.get("B", {}).get("gemini")):
        n = next((k for k in (8, 4, 2, 1) if B - k >= first), None)
        c["A"].update(bar=None if n is None else B - n, bars_before=n, name=f"{n or ''}BS", time=None if n is None else tb(B - n))
        if c["B"].get("gemini"): c["A"]["status"] = "suggest"
    if H is not None and c.get("H", {}).get("gemini"): c.setdefault("G", {}).update(bar=H - 8, time=tb(H - 8), status="suggest")
    return new
