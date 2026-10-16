"""Gemini tiebreaker + QC (Cullen OK'd, Oct 15). Sends ONLY short clips (<=20 s, mono 64 kbps) around candidate bars
plus timed lyrics in that window; never a full song. Key from env GEMINI_API_KEY (never printed).
Reads cues_v2/allin1/go16, writes gemini/<nn>.json and cues_v2/allin1/go16_gem/<nn>.json."""
import json, os, sys, base64, subprocess, tempfile, urllib.request, glob, copy, time
R = "/workspace/research/dj-auto-cues/proof/real"; MODEL = os.environ.get("GEM_MODEL", "gemini-3.8-flash"); FALLBACK = "gemini-3.8-flash" if MODEL != "gemini-3.8-flash" else "gemini-3-flash-preview"
PRICE = {"text": 0.75, "audio": 0.75, "out": 3.75}  # USD per 1M tokens, gemini-3.8-flash intro price thru Dec 31 2026 (preview fallback priced the same, estimate)
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
def call(parts):
    body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}
    for k in range(6):
        m = MODEL if k % 2 == 0 else FALLBACK
        req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent", json.dumps(body).encode(), {"Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]})
        try: r = json.load(urllib.request.urlopen(req, timeout=180)); break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and k < 5: time.sleep(8 * (k + 1)); continue
            raise
    USED.append(m)
    u = r.get("usageMetadata", {}); txt = "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"] if not p.get("thought"))
    au = sum(d["tokenCount"] for d in u.get("promptTokensDetails", []) if d.get("modality") == "AUDIO")
    tot_in = u.get("promptTokenCount", 0); out = u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
    cost = (au * PRICE["audio"] + (tot_in - au) * PRICE["text"] + out * PRICE["out"]) / 1e6
    return json.loads(txt), cost
USED = []
def clip(nn, t0, t1):
    f = tempfile.mktemp(suffix=".mp3")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0,t0):.2f}", "-t", f"{min(20, t1 - max(0,t0)):.2f}", "-i", f"{R}/wav/{nn}.wav", "-ac", "1", "-b:a", "64k", f], check=True)
    b = base64.b64encode(open(f, "rb").read()).decode(); os.remove(f); return b
NAMES = {"B": "START (first downbeat of the song proper, where the vocal phrase starts; a pickup word in the second half of the previous bar belongs to the next bar)",
         "H": "EXIT (the downbeat where the last chorus has ended and the energy/vocals drop for the outro)",
         "C": "1st Chorus (first downbeat of the first chorus/hook)", "D": "Last Chorus (first downbeat of the final chorus/hook)"}
def main():
    v1d = lambda nn: json.load(open(f"{R}/cues/allin1/{nn}.json"))
    total = {}
    for p in sorted(glob.glob(f"{R}/cues_v2/allin1/go16/*.json")):
        nn = os.path.basename(p)[:2]
        if len(sys.argv) > 1 and nn not in sys.argv[1:]: continue
        gp = f"{R}/gemini/{nn}.json"   # checkpoint: skip songs already done cleanly
        if os.path.exists(gp):
            old = json.load(open(gp))
            if old.get("cost_usd", 0) > 0 and not any("error" in t for t in old["tiebreaks"]) and "error" not in (old.get("qc") or {}): continue
        plan = json.load(open(p)); d1 = v1d(nn); g = d1["grid"]
        tb = lambda j: g["t1"] + j * g["bar_s"]
        words = (json.load(open(f"{R}/out/words/{nn}.json")) if os.path.exists(f"{R}/out/words/{nn}.json") else {"words": []})["words"]
        segs = [s["j0"] for s in d1["segments"]]
        rec = {"track": nn, "model": MODEL, "tiebreaks": [], "qc": None, "cost_usd": 0.0}; new = copy.deepcopy(plan)
        for L in "BHCD":
            c = plan["cues"].get(L, {})
            if c.get("bar") is not None and c.get("confidence", 0) >= 0.85 and L in "BH": continue
            base = c.get("bar"); old = d1["cues"].get({"B": "B", "H": "D", "C": "G", "D": "C"}[L], {}).get("bar")
            cands = sorted({x for x in [base, old] + ([base - 1, base + 1] if base is not None and L in "BH" else []) +
                            [j for j in segs if base is not None and abs(j - base) <= 4] if x is not None and x >= 0})
            if not cands: continue
            cands = cands[:5]; t0 = tb(min(cands)) - 4; t1 = min(t0 + 20, tb(max(cands)) + 6)
            cands = [j for j in cands if t0 <= tb(j) <= t1]
            if not cands: continue
            lyr = [f"{w['start']-max(0,t0):.1f}s {w['word']}" for w in words if t0 <= w["start"] <= t1]
            prompt = (f"You are helping a wedding/party DJ place Serato hot cues. Audio clip starts at track time {max(0,t0):.2f}s. "
                      f"Tempo {60/(g['bar_s']/4):.1f} BPM, one bar = {g['bar_s']:.3f}s. Candidate downbeats (bar number: seconds into the clip): "
                      + ", ".join(f"{j}: {tb(j)-max(0,t0):.2f}s" for j in cands) +
                      f". Which candidate is the best {NAMES[L]}? Timed lyrics in this clip (seconds into clip): {'; '.join(lyr) or 'none transcribed'}. "
                      'Reply JSON {"pick": <bar number or null if none fit>, "why": "<one sentence>"}')
            try:
                ans, cost = call([{"inline_data": {"mime_type": "audio/mpeg", "data": clip(nn, t0, t1)}}, {"text": prompt}])
            except Exception as e: rec["tiebreaks"].append({"cue": L, "error": str(e)[:200]}); continue
            rec["cost_usd"] += cost; rec["tiebreaks"].append({"cue": L, "candidates": cands, "engine": base, "clip_s": [round(max(0,t0),1), round(t1,1)], **ans})
            pk = ans.get("pick")
            if isinstance(pk, int) and pk in cands:
                nc = new["cues"][L]; nc["bar"] = pk; nc["time"] = round(tb(pk) - g["offset_ms"] / 1000, 3); nc["reason"] += f"; Gemini picked bar {pk}: {ans.get('why','')}"
                if L in "BH": nc["confidence"] = round(min(0.84, nc.get("confidence", 0) + 0.1), 2)  # Gemini alone never makes it auto-set
        # re-derive A / G from new B / H (A = 8BS>4BS>2BS>1BS rule; G = 16 beats before EXIT for scoring)
        B, H = new["cues"]["B"].get("bar"), new["cues"]["H"].get("bar")
        if B is not None and new["cues"].get("A", {}).get("bars_before"):
            first = -(-(-g["t1"]) // g["bar_s"])
            for n in (8, 4, 2, 1):
                if B - n >= max(first, 0): new["cues"]["A"].update(bar=B - n, bars_before=n, name=f"{n}BS", time=round(tb(B - n) - g["offset_ms"] / 1000, 3)); break
        if H is not None and "G" in new["cues"]: new["cues"]["G"].update(bar=H - 4, time=round(tb(H - 4) - g["offset_ms"] / 1000, 3))
        planx = {L: {k: c.get(k) for k in ("name", "bar", "time", "confidence", "status")} for L, c in new["cues"].items() if L in "ABCDGH"}
        q = (f"Check this DJ cue plan for one song (bars on the Serato grid, bar_s={g['bar_s']:.3f}s, track length {d1.get('dur', 'unknown')}). "
             f"Rules: A is 8/4/2/1 bars before START; B START; C 1st chorus after START; D last chorus after C and before EXIT; G is 4 bars before EXIT; H EXIT after D. "
             f"Engine sections: {d1['segments_desc']}. Plan: {json.dumps(planx)}. Loops: {json.dumps(new.get('loops'))}. "
             'Flag anything illogical. Reply JSON {"ok": true|false, "flags": ["..."]}')
        try: ans, cost = call([{"text": q}]); rec["qc"] = ans; rec["cost_usd"] += cost
        except Exception as e: rec["qc"] = {"error": str(e)[:200]}
        rec["models_used"] = sorted(set(USED)); USED.clear()
        os.makedirs(f"{R}/gemini", exist_ok=True); json.dump(rec, open(f"{R}/gemini/{nn}.json", "w"), indent=1)
        os.makedirs(f"{R}/cues_v2/allin1/go16_gem", exist_ok=True); json.dump(new, open(f"{R}/cues_v2/allin1/go16_gem/{nn}.json", "w"), indent=1)
        print(nn, f"${rec['cost_usd']:.4f}", [(t['cue'], t.get('pick'), t.get('engine')) for t in rec["tiebreaks"]], "QC ok" if (rec["qc"] or {}).get("ok") else f"QC {(rec['qc'] or {}).get('flags', rec['qc'])}"[:160], flush=True)
if __name__ == "__main__": main()
