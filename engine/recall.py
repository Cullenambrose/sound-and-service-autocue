import json, os, sys
sys.path.insert(0, os.path.dirname(__file__)); import candidates as C
R = C.R; ns = {}; exec(open(f"{R}/tools/gemini_v2.py").read().split("def call(")[0], ns); M = ns["M"]
for mode in ("old", "new"):
    hit = {L: [0, 0, 0] for L in "BHCD"}
    for nn in sorted(M):
        tr = ns["bars_of_truth"](nn); cd = C.build(nn, mode)
        for L in "BHCD":
            if L in tr: hit[L][0] += 1; hit[L][1] += any(abs(x - tr[L]) == 0 for x in cd.get(L, [])); hit[L][2] += len(cd.get(L, []))
    print(mode, " ".join(f"{L}: recall {h[1]}/{h[0]} avg {h[2]/max(1,h[0]):.1f} cands" for L, h in hit.items()))
