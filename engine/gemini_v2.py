"""Gemini v2 tiebreak + QC, gemini-3.1-pro-preview (paid key). One call per song, checkpointed per song.
Sends: cue guide, leave-one-out few-shot examples (Cullen's truth cues on OTHER songs, as bar numbers), the beat grid,
sections, lyric times, engine candidates, and <=4 short mono clips (<=20 s each) around candidate groups. Never a full song."""
import json, os, sys, base64, subprocess, tempfile, urllib.request, urllib.error, glob, copy, time, math
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
def example(nn):
    d = json.load(open(f"{R}/cues/allin1/{nn}.json")); tr = bars_of_truth(nn)
    segs = ", ".join(f"{s['lab']}@{s['j0']}" for s in d["segments"])
    return f"Example song {nn}: analyzer sections (label@bar): {segs}. Analyzer last-chorus end bar: {d['cues']['D'].get('bar')}. Cullen's cues (bars): " + ", ".join(f"{k}={v}" for k, v in sorted(tr.items()))
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
    out_p = f"{R}/gemini_v2/{nn}.json"
    if os.path.exists(out_p) and "error" not in json.load(open(out_p)): return json.load(open(out_p))
    d1 = json.load(open(f"{R}/cues/allin1/{nn}.json")); g = d1["grid"]; plan = json.load(open(f"{R}/cues_v2/allin1/go16/{nn}.json"))
    tb = lambda j: g["t1"] + j * g["bar_s"]
    wp = f"{R}/out/words/{nn}.json"; words = json.load(open(wp))["words"] if os.path.exists(wp) else []
    pc = plan["cues"]; segs = d1["segments"]
    B = pc["B"].get("bar"); H = pc["H"].get("bar"); v1D = d1["cues"]["D"].get("bar")
    lyr = plan.get("lyrics", {}); fw = lyr.get("first_word_s")
    cand = {}
    cb = {x for x in [B, d1["cues"]["B"].get("bar")] if x is not None}
    if fw is not None: cb |= {math.floor((fw - g["t1"]) / g["bar_s"]), math.floor((fw - g["t1"]) / g["bar_s"]) + 1}
    cb |= {s["j0"] for s in segs if B is not None and abs(s["j0"] - B) <= 8 and s["j0"] > 0}
    cand["B"] = sorted(x for x in cb if x >= 0)[:6]
    ch = {x for x in [H, v1D] if x is not None}; ch |= {x + 1 for x in list(ch)}
    ch |= {s["j0"] for s in segs if H is not None and abs(s["j0"] - H) <= 8}
    cand["H"] = sorted(ch)[:6]
    cc = sorted({s["j0"] for s in segs if s["cls"] == "chorus" and (B is None or s["j0"] >= B + 4)} | set(lyr.get("hook_starts", [])))
    cand["C"] = cc[:4]; cand["D"] = cc[-4:]
    parts = []; clipinfo = []
    for L in "BHCD":
        cs = cand[L]
        if not cs: continue
        a, b = tb(min(cs)) - 3, tb(max(cs)) + 5
        if b - a > 20: a, b = tb(cs[0]) - 3, tb(cs[0]) + 15  # keep to one short window
        if any(abs(a - x[1]) < 4 for x in clipinfo): continue
        data, t0, t1 = clip(nn, a, b); clipinfo.append((L, t0, t1)); parts.append({"inline_data": {"mime_type": "audio/mpeg", "data": data}})
    ex = "\n".join(example(x) for x in sorted(M) if x != nn)   # leave-one-out
    lyr_txt = "; ".join(f"{w['start']:.1f}s {w['word']}" for w in words[:120])
    prompt = (GUIDE + "\n\nFEW-SHOT (other songs, Cullen's real cues vs the analyzer):\n" + ex +
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
    os.makedirs(f"{R}/gemini_v2", exist_ok=True); json.dump(rec, open(out_p, "w"), indent=1); return rec
def apply(nn, rec):
    plan = json.load(open(f"{R}/cues_v2/allin1/go16/{nn}.json")); g = json.load(open(f"{R}/cues/allin1/{nn}.json"))["grid"]
    tb = lambda j: round(g["t1"] + j * g["bar_s"] - g["offset_ms"] / 1000, 3); new = copy.deepcopy(plan); a = rec.get("answer", {})
    if isinstance(a, list): a = a[0] if a and isinstance(a[0], dict) else {}
    for L in "BHCD":
        pk = (a.get(L) or {}).get("pick")
        if not isinstance(pk, int) or pk not in rec["candidates"].get(L, []): continue
        c = new["cues"][L]
        if L in "BH" and c.get("status") == "set": continue   # confident engine cue stays
        c.update(bar=pk, time=tb(pk), gemini=True, reason=(c.get("reason") or "") + f"; Google picked bar {pk}: {(a[L].get('why') or '')[:160]}")
    B, H = new["cues"]["B"].get("bar"), new["cues"]["H"].get("bar")
    first = math.ceil(-g["t1"] / g["bar_s"] - 1e-6)
    if B is not None:
        n = next((k for k in (8, 4, 2, 1) if B - k >= max(first, 0)), None)
        if n: new["cues"]["A"].update(bar=B - n, bars_before=n, name=f"{n}BS", time=tb(B - n))
    if H is not None and "G" in new["cues"]: new["cues"]["G"].update(bar=H - 4, time=tb(H - 4))   # 16-beat G for scoring vs his truth
    os.makedirs(f"{R}/cues_v2/allin1/go16_gem2", exist_ok=True); json.dump(new, open(f"{R}/cues_v2/allin1/go16_gem2/{nn}.json", "w"), indent=1)
if __name__ == "__main__":
    for nn in (sys.argv[1:] or sorted(M)):
        rec = one(nn)
        if "error" not in rec: apply(nn, rec)
        print(nn, rec.get("error") or f"${rec['cost_usd']:.4f} " + str(rec['answer'])[:80], flush=True)
