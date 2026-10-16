"""Gemini v5 = gemini_v3 with the claim-7 fixes: reads the v5 plan (cues_v2/allin1/v5_go32, else v4_go32) instead of go16 (v3);
C clip centred on the best chorus candidate (plan C, else top v5 chorus score, else labelled chorus), never cs[0]=START+4;
apply = gemini_apply_v5 (never overrides a confident B/H; recomputes A/G). Writes gemini_v5/. NOT run yet (no new spend).
Original: Gemini v3 (expanded candidates + closest-song few-shot from truth+corrections, leave-one-out). Based on v2:
Gemini v2 tiebreak + QC, gemini-3.1-pro-preview (paid key). One call per song, checkpointed per song.
Sends: cue guide, leave-one-out few-shot examples (Cullen's truth cues on OTHER songs, as bar numbers), the beat grid,
sections, lyric times, engine candidates, and <=4 short mono clips (<=20 s each) around candidate groups. Never a full song."""
import sys
sys.path.insert(0, "/workspace/research/dj-auto-cues/proof/real/tools")
import json, os, base64, subprocess, tempfile, urllib.request, urllib.error, glob, copy, time, math
R = "/workspace/research/dj-auto-cues/proof/real"; MODEL = "gemini-3.1-pro-preview"
PRICE_IN, PRICE_OUT = 2.00, 12.00   # USD / 1M tokens, <=200k prompt, paid tier
ns = {}; exec(open(f"{R}/tools/score_vs_truth.py").read().split("# engine slot")[0].replace("open('truth_tags.json')", f"open('{R}/truth_tags.json')"), ns); T, M = ns["T"], ns["M"]
GUIDE = """CUE GUIDE (Cullen, wedding/party DJ, Serato hot cues). All cues sit on a downbeat of the Serato beat grid.
- B START: the downbeat where the song proper starts, normally where the vocal phrase begins. If the first sung word is a pickup (lands late in the previous bar), START is the bar that HOLDS the pickup word, not the next one. DJ-edit intros are usually 8 or 16 bars; ignore lone vocal tags/ad-libs/samples in the intro.
- A: 8 bars before START (8BS); if that falls before the track starts, 4, then 2, then 1 bar (derived, you do not pick it).
- C 1st Chorus: first downbeat of the first chorus/hook/drop AFTER START (never on START itself).
- D Last Chorus: first downbeat of the final chorus/hook/drop before EXIT.
- H EXIT: the downbeat where the last chorus is over and the outro/breakdown begins (energy and vocals drop). In his cues EXIT is often ONE BAR AFTER the analyzer's section boundary, because the last chorus line rings into that bar.
- G GO: 32 beats (8 bars) before EXIT (derived).
Bars are counted on the grid given; bar k starts at t1 + k*bar_seconds."""
def bars_of_truth(nn):
    g = json.load(open(f"{R}/cues/allin1/{nn}.json"))["grid"]; off = g["offset_ms"] / 1000
    tc = {c["index"]: c for c in T[nn]["markers2"] if c["type"] == "CUE"}
    out = {}
    for s, cat in M[nn].items():
        if cat in ("START", "FC", "LC", "EXIT"): out[{"START": "B", "FC": "C", "LC": "D", "EXIT": "H"}[cat]] = round((tc["ABCDEFGH".index(s)]["ms"] / 1000 + off - g["t1"]) / g["bar_s"])
    return out
def call(parts):
    body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}
    for k in range(5):
        req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent", json.dumps(body).encode(),
                                     {"Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]})
        try: r = json.load(urllib.request.urlopen(req, timeout=150)); break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and k < 4: time.sleep(15 * (k + 1)); continue
            raise
        except Exception:
            if k < 4: time.sleep(10); continue
            raise
    u = r.get("usageMetadata", {})
    txt = "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"] if not p.get("thought"))
    cost = (u.get("promptTokenCount", 0) * PRICE_IN + (u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)) * PRICE_OUT) / 1e6
    return json.loads(txt), cost, u
def clip(nn, t0, t1):
    f = tempfile.mktemp(suffix=".mp3"); t0 = max(0, t0); t1 = min(t1, t0 + 20)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t0:.2f}", "-t", f"{t1-t0:.2f}", "-i", f"{R}/wav/{nn}.wav", "-ac", "1", "-b:a", "64k", f], check=True)
    b = base64.b64encode(open(f, "rb").read()).decode(); os.remove(f); return b, t0, t1
def one(nn):
    out_p = f"{R}/gemini_v5/{nn}.json"
    if os.path.exists(out_p) and "error" not in json.load(open(out_p)): return json.load(open(out_p))
    d1 = json.load(open(f"{R}/cues/allin1/{nn}.json")); g = d1["grid"]; pp = f"{R}/cues_v2/allin1/v5_go32/{nn}.json"; plan = json.load(open(pp if os.path.exists(pp) else f"{R}/cues_v2/allin1/v4_go32/{nn}.json"))
    tb = lambda j: g["t1"] + j * g["bar_s"]
    wp = f"{R}/out/words/{nn}.json"; words = json.load(open(wp))["words"] if os.path.exists(wp) else []
    pc = plan["cues"]; segs = d1["segments"]
    B = pc["B"].get("bar"); H = pc["H"].get("bar"); v1D = d1["cues"]["D"].get("bar")
    lyr = plan.get("lyrics", {}); fw = lyr.get("first_word_s")
    import candidates as CA; cand = CA.build(nn)
    parts = []; clipinfo = []
    for L in "BHCD":
        cs = cand[L]
        if not cs: continue
        chs = plan.get("v5", {}).get("chorus_scores") or {}; best = max(chs, key=lambda k: chs[k]) if chs else None
        lab = [s_["j0"] for s_ in segs if s_["cls"] == "chorus" and B is not None and s_["j0"] >= B + 4]
        cC = pc["C"].get("bar") if pc["C"].get("bar") is not None else (int(best) if best is not None else (lab[0] if lab else None))
        cD = pc["D"].get("bar") if pc["D"].get("bar") is not None else (lab[-1] if lab else None)
        anc = {"B": B, "H": H, "C": cC, "D": cD}[L]
        if anc is None: anc = cs[len(cs) // 2]
        a, b = tb(anc) - 9, tb(anc) + 11   # 20 s window centred on the engine's pick
        if any(abs(a - x[1]) < 4 for x in clipinfo): continue
        data, t0, t1 = clip(nn, a, b); clipinfo.append((L, t0, t1)); parts.append({"inline_data": {"mime_type": "audio/mpeg", "data": data}})
    import learner as LN, marks as MK   # round-2 claim 1: few-shot ONLY from songs Cullen actually marked, never Mixed In Key cues
    me = LN.ctx(nn); ev = [e for e in MK.events() if e.get("track") != nn]
    def dist(x):
        c = LN.ctx(x); return (c["type"] != me["type"]) * 2 + (c["tempo"] != me["tempo"])
    close = sorted((x for x in MK.marked_songs(ev) if x != nn and MK.truth_bars(x, ev)), key=dist)[:8]
    def example2(x):
        tr = MK.truth_bars(x, ev); d = json.load(open(f"{R}/cues/allin1/{x}.json"))
        return f"Example song {x} ({LN.ctx(x)['type']}, {LN.ctx(x)['tempo']} BPM): sections " + ", ".join(f"{s['lab']}@{s['j0']}" for s in d["segments"]) + f"; analyzer last-chorus end bar {d['cues']['D'].get('bar')}; Cullen's review-page marks (bars): " + ", ".join(f"{k}={v}" for k, v in sorted(tr.items()))
    ex = "\n".join(example2(x) for x in close)
    lyr_txt = "; ".join(f"{w['start']:.1f}s {w['word']}" for w in words[:120])
    prompt = (GUIDE + "\n\nFEW-SHOT (other songs, Cullen's review-page marks vs the analyzer):\n" + ex +
              f"\n\nTHIS SONG {nn}: BPM {240/g['bar_s']:.2f}, bar_seconds {g['bar_s']:.4f}, t1 {g['t1']:.3f}s (bar 0). Sections (label@bar): " +
              ", ".join(f"{s['lab']}@{s['j0']}-{s['j1']}" for s in segs) +
              f". Analyzer last-chorus end: bar {v1D}. Engine plan: START bar {B} (conf {pc['B'].get('confidence')}), EXIT bar {H} (conf {pc['H'].get('confidence')})." +
              f"\nFirst sung word: {fw}s. Lyric word times (track seconds): {lyr_txt or 'none'}.\nHook phrase: {lyr.get('hook')}" +
              "\nCANDIDATES (bar: track seconds): " + "; ".join(f"{L}: " + ", ".join(f"{j}:{tb(j):.2f}s" for j in cand[L]) for L in "BHCD") +
              "\nAudio clips attached in order: " + "; ".join(f"clip {i+1} = track {t0:.2f}s to {t1:.2f}s (around {L} candidates)" for i, (L, t0, t1) in enumerate(clipinfo)) +
              '\n\nPick each cue ONLY from its candidate list, or null if none fit. Then QC the resulting plan. Reply JSON: '
              '{"B":{"pick":int|null,"why":"..."},"H":{...},"C":{...},"D":{...},"qc_flags":["..."]}')
    rec = {"track": nn, "model": MODEL, "candidates": cand, "clips": clipinfo}
    try:
        ans, cost, u = call(parts + [{"text": prompt}]); rec.update(answer=ans, cost_usd=round(cost, 5), usage=u)
    except Exception as e: rec["error"] = str(e)[:300]
    os.makedirs(f"{R}/gemini_v5", exist_ok=True); json.dump(rec, open(out_p, "w"), indent=1); return rec
def apply(nn, rec):
    import gemini_apply_v5 as GA
    pp = f"{R}/cues_v2/allin1/v5_go32/{nn}.json"; plan = json.load(open(pp if os.path.exists(pp) else f"{R}/cues_v2/allin1/v4_go32/{nn}.json"))
    new = GA.apply(plan, nn, "gemini_v5"); os.makedirs(f"{R}/cues_v2/allin1/v5_gem", exist_ok=True); json.dump(new, open(f"{R}/cues_v2/allin1/v5_gem/{nn}.json", "w"), indent=1)
if __name__ == "__main__":
    for nn in (sys.argv[1:] or sorted(M)):
        rec = one(nn)
        if "error" not in rec: apply(nn, rec)
        print(nn, rec.get("error") or f"${rec['cost_usd']:.4f} " + str(rec['answer'])[:80], flush=True)
