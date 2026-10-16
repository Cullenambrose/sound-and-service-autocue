"""v4 plans = v3 rules (go16/go32) + kept variants: lead-vocal START (B), drop-check EXIT (H). A ladder and G recomputed.
Writes cues_v2/allin1/v4_go16 (scoring vs his 16-beat truth) and v4_go32 (Cullen's profile, used by the review page)."""
import json, os, sys, math, copy
R = "/workspace/research/dj-auto-cues/proof/real"; sys.path.insert(0, f"{R}/tools")
import warnings; warnings.filterwarnings("ignore")
import variants as V
KEEP = json.load(open(f"{R}/variants_score.json"))["keep"]
for nn in sorted(V.M):
    g = V.grid(nn); tb = lambda j: round(g["t1"] + j * g["bar_s"] - g["offset_ms"] / 1000, 3)
    lead = V.v_lead(nn); drop = V.v_drop(nn)
    for gl, src in ((4, "go16"), (8, "go32")):
        p = json.load(open(f"{R}/cues_v2/allin1/{src}/{nn}.json")); c = p["cues"]
        for L, var, name in (("B", lead, "lead_vocal"), ("H", drop, "drop_check")):
            if L in KEEP.get(name, []) and var[L] is not None and var[L] != c[L].get("bar"):
                c[L]["reason"] = (c[L].get("reason") or "") + f"; {name} moved {c[L].get('bar')}->{var[L]}"; c[L]["bar"] = var[L]; c[L]["time"] = tb(var[L])
        B, H = c["B"].get("bar"), c["H"].get("bar"); first = math.ceil(-g["t1"] / g["bar_s"] - 1e-6)
        if B is not None:
            n = next((k for k in (8, 4, 2, 1) if B - k >= max(first, 0)), None)
            if n: c["A"].update(bar=B - n, bars_before=n, name=f"{n}BS", time=tb(B - n))
        if H is not None and "G" in c: c["G"].update(bar=H - gl, time=tb(H - gl))
        od = f"{R}/cues_v2/allin1/v4_{src}"; os.makedirs(od, exist_ok=True); json.dump(p, open(f"{od}/{nn}.json", "w"), indent=1)
print("ok")
