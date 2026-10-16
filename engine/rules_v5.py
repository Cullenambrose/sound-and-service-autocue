"""Auto-cue rules v5 = v4 plan (cues_v2/allin1/v4_go32) + 8 switchable changes. v4 files are untouched.
Layout (Cullen): A = 8BS>4BS>2BS>1BS that fits (else empty+flag), B START, C 1st Chorus, D Last Chorus (suggest),
G = EXIT - 32 beats, H EXIT, E/F empty, two 4-bar loop suggestions; auto-set only at conf >= threshold (0.85 default).
Changes (FLAGS): loops_phrase, exit_phrase, chorus_verify, drop_robust, tempo_check, cold_start, consensus, genre_params.
Usage: rules_v5.py [--root R] [--out DIR] [--off flag,flag] [--only flag]   (default: all on; writes R/cues_v2/allin1/v5_go32)"""
import json, os, sys, math, ast, re, argparse, numpy as np
TOOLS = os.path.dirname(os.path.abspath(__file__))
FLAGS = ["loops_phrase", "exit_phrase", "chorus_verify", "drop_robust", "tempo_check", "cold_start", "consensus", "genre_params"]
GL = 8  # GO = EXIT - 32 beats = 8 bars

def load(p, default=None):
    return json.load(open(p)) if os.path.exists(p) else default

class Song:
    def __init__(s, R, nn):
        s.R, s.nn = R, nn
        s.plan = json.load(open(f"{R}/cues_v2/allin1/v4_go32/{nn}.json")); s.d1 = json.load(open(f"{R}/cues/allin1/{nn}.json"))
        s.g = s.d1["grid"]; s.segs = s.d1["segments"]; f = np.load(f"{R}/features/{nn}.npz"); s.dur = float(f["dur"])
        s.nb = int(math.ceil((s.dur - s.g["t1"]) / s.g["bar_s"]))
        p = f"{R}/out/allin1/{nn}.stems_env.npz"; s.st = dict(np.load(p)) if os.path.exists(p) else None
        p = f"{R}/features_v5/{nn}.sim.npz"; s.sim = dict(np.load(p)) if os.path.exists(p) else None
        s.words = load(f"{R}/out/words/{nn}.json"); s.sf = load(f"{R}/cues/songformer/{nn}.json")
        if s.sf is None:   # change 7: raw SongFormer output (seconds) from the box or copied back from the Mac (out/songformer/<nn>.json)
            raw = load(f"{R}/out/songformer/{nn}.json")
            if isinstance(raw, dict): raw = raw.get("segments")
            if raw: s.sf = {"segments": [{"j0": round((x["start"] - s.g["t1"]) / s.g["bar_s"]), "j1": round((x["end"] - s.g["t1"]) / s.g["bar_s"]), "cls": x.get("label", "")} for x in raw]}
        s.raw = load(f"{R}/out/allin1/{nn}.json"); s.bt = load(f"{R}/out/beat_this/{nn}.json")
        s.tags = load(f"{R}/serato_tags.json", {}).get(nn, {}); s.genre = load(os.environ.get("V5_GENRE_TAGS", f"{R}/genre_tags.json"), {}).get(nn, "")
    def t(s, j): return s.g["t1"] + j * s.g["bar_s"]
    def first(s): return max(0, math.ceil(-s.g["t1"] / s.g["bar_s"] - 1e-6))
    def frames(s, j, n=1):
        h = float(s.st["hop_s"]); return int(max(0, s.t(j)) / h), int(max(0, s.t(j + n)) / h)
    def env(s, k):
        x = s.st[k]; return x / (np.percentile(x, 95) + 1e-9)
    def bar_frac(s, k, j, thr):
        if s.st is None or j < 0: return None
        a, b = s.frames(j); x = s.env(k)[a:b]; return float((x > thr).mean()) if len(x) else 0.0
    def db_level(s, j, n=1):   # drums+bass normalised level over bars j..j+n
        a, b = s.frames(j, n); x = (s.env("drums") + s.env("bass"))[a:b] / 2; return x

# ---------- change 8: genre bucket ----------
def bucket(S):
    g = (S.genre or "").lower(); bpm = 240 / S.g["bar_s"]
    perc = None
    if S.st is not None: d = S.env("drums"); perc = float((d > 0.15).mean())
    if re.search(r"latin|reggaeton|salsa|bachata|cumbia|merengue|banda", g): return "latin", perc
    if re.search(r"hip ?hop|rap|r&b|rnb|trap", g): return "hiphop_rnb", perc
    if re.search(r"house|edm|dance|techno|trance|electro|disco", g): return "house_edm", perc
    if re.search(r"pop", g): return "pop", perc
    if not g:   # no tag: guess from BPM + percussion density
        if 118 <= bpm <= 132 and (perc or 0) > 0.7: return "house_edm", perc
        if bpm < 105: return "hiphop_rnb", perc
    return "other", perc

def params(S, on, cfg):
    p = dict(cfg["default"]); b, perc = bucket(S)
    if on("genre_params"): p.update(cfg["buckets"].get(b, {}))
    return p, b, perc

# ---------- change 4: robust drop check ----------
def drop_ok(S, j, P):
    """EXIT bar j passes if drums+bass are clearly lower for the WHOLE bar j than in bars j-2..j-1:
    >= drop_hold_frac of bar-j frames sit below the midpoint between before-level and floor, and the mean falls >= drop_frac."""
    if S.st is None or j < 2 or j >= S.nb: return None
    pre = S.db_level(j - 2, 2); bar = S.db_level(j, 1)
    if not len(pre) or not len(bar): return None
    lp = float(np.median(pre)); mid = lp * 0.6
    return bool(lp - float(np.mean(bar)) >= P["drop_frac"] and (bar < mid).mean() >= P["drop_hold_frac"])
def drop_ok_v4(S, j, P):   # single-bar mean step (what v4 drop_check used)
    if S.st is None or j < 1: return None
    f = lambda k: (S.bar_frac("drums", k, 0.15) + S.bar_frac("bass", k, 0.15)) / 2
    return f(j - 1) - f(j) >= 0.25

# ---------- change 5: tempo stability ----------
def tempo(S, P):
    beats = (S.raw or {}).get("beats") or (S.bt or {}).get("beats"); out = {"drift": False, "source": None}
    if beats and len(beats) > 16:
        ibi = np.diff(np.array(beats)); ibi = ibi[(ibi > 0.2) & (ibi < 1.5)]
        w = max(8, len(ibi) // 16); loc = np.array([np.mean(ibi[i:i + w]) for i in range(0, len(ibi) - w, w)])
        spread = float((loc.max() - loc.min()) / np.median(ibi)) if len(loc) > 1 else 0.0
        out.update(source="detected beats", spread=round(spread, 4), drift=spread > P["tempo_drift_tol"], beats=beats)
    try: grid = ast.literal_eval(S.tags.get("serato_beatgrid", "[]"))
    except Exception: grid = []
    bpms = [m.get("bpm") for m in grid if m.get("bpm")]
    if len(bpms) > 1:
        sp = (max(bpms) - min(bpms)) / np.median(bpms); out["serato_markers"] = len(grid)
        if sp > P["tempo_drift_tol"]: out.update(drift=True, source=(out["source"] or "") + "+serato multi-BPM grid")
    return out
def drift_time(S, T, j):
    """time of bar j counted from detected downbeats (bar 0 = first downbeat at/after grid t1) instead of fixed bar_s"""
    b = np.array(T.get("beats") or []); 
    if not len(b): return None
    i0 = int(np.argmin(np.abs(b - S.g["t1"]))); k = i0 + 4 * j
    return round(float(b[k]) - S.g["offset_ms"] / 1000, 3) if 0 <= k < len(b) else None

# ---------- change 3: chorus recurrence ----------
def recurrence(S, j, n=4):
    """max similarity of the 4-bar block at j to any block >= 8 bars away. Chroma and MFCC are z-scored per song
    (so the always-positive chroma does not make everything look alike), then cosine; 0.6*chroma + 0.4*MFCC."""
    if S.sim is None or j < 0 or j + n > len(S.sim["chroma"]): return None
    if not hasattr(S, "_z"):
        z = lambda X: (X - X.mean(0)) / (X.std(0) + 1e-9); S._z = (z(S.sim["chroma"]), z(S.sim["mfcc"]))
    C, M = S._z
    def blk(k):
        c = C[k:k + n].ravel(); m = M[k:k + n].ravel(); return c / (np.linalg.norm(c) + 1e-9), m / (np.linalg.norm(m) + 1e-9)
    c0, m0 = blk(j); best = -1
    for k in range(0, len(C) - n + 1):
        if abs(k - j) < 8: continue
        c1, m1 = blk(k); best = max(best, 0.6 * float(c0 @ c1) + 0.4 * float(m0 @ m1))
    return best
def lyric_repeat(S, j):
    if not S.words: return None
    norm = lambda x: re.sub(r"[^a-z ]", "", x.lower()).strip()
    lines = [(math.floor((w["start"] - S.g["t1"]) / S.g["bar_s"] + 0.25), norm(w.get("text", ""))) for w in S.words.get("segments", []) if w.get("start") is not None]
    here = [t for b, t in lines if j <= b < j + 3 and len(t) > 6]
    if not here: return None
    jac = lambda a, b: len(set(a.split()) & set(b.split())) / max(1, len(set(a.split()) | set(b.split())))
    return any(jac(t, t2) >= 0.6 and abs(b2 - j) >= 8 for t in here for b2, t2 in lines)

# ---------- change 7: multi-source boundary agreement ----------
def sources(S):
    src = {"allin1": {s["j0"] for s in S.segs} | {s["j1"] for s in S.segs}}
    if S.sf: src["songformer"] = {s["j0"] for s in S.sf["segments"]} | {s["j1"] for s in S.sf["segments"]}
    if S.st is not None:
        ch = set(); f = {k: np.array([S.bar_frac(k, j, 0.2 if k == "vocals" else 0.15) for j in range(S.nb)]) for k in ("vocals", "drums", "bass")}
        for k, v in f.items(): ch |= {j for j in range(1, len(v)) if abs(v[j] - v[j - 1]) >= 0.4}
        src["stems"] = ch
    ff = np.load(f"{S.R}/features/{S.nn}.npz"); fps = int(ff["fps"]); db = 20 * np.log10(ff["rms"] + 1e-6)
    e = [float(db[int(max(0, S.t(j)) * fps):int(max(0, S.t(j + 1)) * fps) + 1].mean()) for j in range(S.nb)]
    src["energy"] = {j for j in range(1, len(e)) if abs(e[j] - e[j - 1]) >= 3}
    return src
def agreement(src, j):
    if j is None: return None, []
    hit = [k for k, v in src.items() if any(abs(j - x) <= 1 for x in v)]; return len(hit) / len(src), hit

def build(S, on, cfg):
    P, buck, perc = params(S, on, cfg); c = json.loads(json.dumps(S.plan["cues"])); notes = {"bucket": buck, "percussion_density": perc, "params": P}
    B = c["B"].get("bar"); H = c["H"].get("bar"); fl = lambda L, m: c[L].setdefault("flags", []).append(m)
    why = lambda L, m: c[L].__setitem__("reason", (c[L].get("reason") or "") + "; v5: " + m)
    # 6 cold start
    if on("cold_start") and S.st is not None:
        v0 = max(S.bar_frac("vocals", S.first(), 0.2) or 0, S.bar_frac("vocals", S.first() + 1, 0.2) or 0)
        e0 = max(S.bar_frac("drums", S.first(), 0.15) or 0, S.bar_frac("drums", S.first() + 1, 0.15) or 0)
        if v0 >= P["cold_vocal_frac"] and e0 < 0.5:
            c["B"]["confidence"] = min(c["B"]["confidence"], 0.7); fl("B", "a cappella intro"); why("B", "vocals but no drums in bar 1: a cappella intro, kept START, review"); notes["cold_start"] = "acapella_intro"
        elif v0 >= P["cold_vocal_frac"]:
            B = S.first(); c["B"].update(bar=B, confidence=min(c["B"]["confidence"], 0.7)); why("B", f"vocals and drums both start in bar 1 -> deliberate cold start"); fl("B", "cold start")
            c["A"] = {"slot": "A", "bar": None, "confidence": 0, "reason": "cold start: no room for a lead-in", "flags": ["cold start: A empty"], "color": c["A"].get("color"), "name": "BS"}
            notes["cold_start"] = "deliberate"
        elif B is not None and B >= 8 and "missed intro" in (c["B"].get("reason") or "") and e0 >= 0.5:
            c["B"]["confidence"] = min(c["B"]["confidence"], 0.6); why("B", "engine saw no intro but the beat runs from bar 1: likely missed intro, review"); notes["cold_start"] = "missed_intro?"
    # 4 robust drop + 2 phrase snap for EXIT
    chk = drop_ok if on("drop_robust") else drop_ok_v4
    if H is not None:
        if on("drop_robust") and S.st is not None and drop_ok(S, H, P) is False:
            cand = sorted((j for j in range(H - 2, H + 3) if drop_ok(S, j, P)), key=lambda j: (abs(j - H), j))
            if cand: why("H", f"drums+bass not down for a full bar at {H}; moved to {cand[0]}"); H = cand[0]
            else: why("H", "no bar nearby where drums+bass stay down a full bar (beat keeps running; kept)")
        if on("exit_phrase") and B is not None:
            pb = P["exit_phrase_bars"]; r = P["exit_snap_radius"]
            if (H - B) % 4 == 0: c["H"]["confidence"] += 0.05; why("H", "on the phrase grid from START")
            else:
                cand = sorted((j for j in range(H - r, H + r + 1) if (j - B) % pb == 0 or (j - B) % 4 == 0), key=lambda j: ((j - B) % pb != 0, chk(S, j, P) is not True, abs(j - H)))
                good = [j for j in cand if chk(S, j, P) is not False]
                if good: why("H", f"snapped {H}->{good[0]} onto the {4 if (good[0]-B)%pb else pb}-bar phrase grid from START"); H = good[0]
                else: c["H"]["confidence"] -= 0.15; fl("H", "no phrase boundary near EXIT")
        c["H"]["bar"] = H; c.setdefault("G", {"slot": "G", "flags": [], "name": "GO", "color": "#CC00CC"}).update(bar=H - GL); c["G"]["confidence"] = c["H"]["confidence"] - 0.02
    # A ladder (re-done in case B moved)
    if B is not None and notes.get("cold_start") != "deliberate":
        n = next((k for k in (8, 4, 2, 1) if B - k >= S.first()), None)
        if n: c["A"].update(bar=B - n, bars_before=n, name=f"{n}BS", confidence=c["B"]["confidence"] - 0.02)
        else: c["A"].update(bar=None, name="BS", flags=["needs review: no room for A"])
    # 3 chorus verification
    if on("chorus_verify") and B is not None:
        cs = [s["j0"] for s in S.segs if s["cls"] == "chorus" and s["j0"] >= B + 4 and (H is None or s["j0"] < H)]
        starts = sorted({s["j0"] for s in S.segs if s["j0"] >= B + 4 and (H is None or s["j0"] < H)})
        score = lambda j: ((recurrence(S, j) or 0) >= P["chorus_sim_thr"]) + (lyric_repeat(S, j) is True)
        ver = [j for j in cs if score(j) >= 1]; notes["chorus_recur"] = {j: (round(recurrence(S, j) or 0, 3), lyric_repeat(S, j)) for j in starts}
        if ver:   # at least one labelled chorus recurs: trust the labels, keep v4 C/D; confidence per cue from its own recurrence
            for L in "CD":
                j = c[L].get("bar")
                if j is None: continue
                ok = j in ver or score(j) >= 1 or (recurrence(S, j) or 0) >= P["chorus_sim_thr"] - 0.15
                c[L]["confidence"] = 0.65 if ok else 0.4; why(L, "section recurs (chroma/MFCC/lyrics)" if ok else "this section does not clearly recur; review")
        elif S.sim is not None:
            rec = [j for j in starts if score(j) >= 1 and (H is None or j <= H - 8)]
            # labelled choruses do not recur: keep them (moving to recurring verses was wrong on 06), lower confidence, name the alternative
            for L, alt in (("C", rec[0] if rec else None), ("D", rec[-1] if rec else None)):
                if c[L].get("bar") is not None:
                    c[L]["confidence"] = 0.3; why(L, "chorus not confirmed by repetition" + (f"; best recurring section is bar {alt}" if alt is not None and alt != c[L]["bar"] else ""))
    # 7 consensus confidence for B and H (G follows H, A follows B)
    if on("consensus"):
        src = sources(S); notes["sources"] = sorted(src)
        for L in "BH":
            a, hit = agreement(src, c[L].get("bar"))
            if a is None: continue
            c[L]["confidence"] = round(c[L]["confidence"] + (0.05 if a >= 0.99 else -0.3 * (1 - a)), 3); why(L, f"{len(hit)}/{len(src)} boundary sources agree ({','.join(hit)})")
        if c["A"].get("bar") is not None: c["A"]["confidence"] = c["B"]["confidence"] - 0.02
        if c.get("G", {}).get("bar") is not None: c["G"]["confidence"] = c["H"]["confidence"] - 0.02
    # 5 tempo
    T = tempo(S, P) if on("tempo_check") else {"drift": False}
    notes["tempo"] = {k: v for k, v in T.items() if k != "beats"}
    for L, x in c.items():
        if x.get("bar") is None: x["time"] = None; continue
        x["time"] = round(S.t(x["bar"]) - S.g["offset_ms"] / 1000, 3)
        if T.get("drift"):
            dt = drift_time(S, T, x["bar"])
            if dt is not None: x["time"] = dt
            x.setdefault("flags", []).append("tempo drift")
    # status
    for L, x in c.items():
        if L in "EF": continue
        x["confidence"] = round(max(0, min(1, x.get("confidence") or 0)), 2)
        if x.get("bar") is None: x["status"] = "empty"
        elif L in "CD": x["status"] = "suggest"
        elif x["confidence"] >= P["auto_set_min_conf"] and not [f for f in x.get("flags", []) if f != "on downbeat"]: x["status"] = "set"
        else: x["status"] = "review"
    loops = plan_loops(S, c, on)
    return {**S.plan, "version": "v5", "flags_on": [f for f in FLAGS if on(f)], "v5": notes, "cues": c, "loops": loops}

# ---------- change 1: loops ----------
def clean(S, j, n=4):
    if S.st is None: return None
    a, b = S.frames(j, n)
    if b > len(S.st["vocals"]) or b <= a: return False
    return bool((S.env("vocals")[a:b] > 0.2).mean() < 0.15 and (S.env("drums")[a:b] > 0.15).mean() > 0.4)
def plan_loops(S, c, on):
    old = S.plan.get("loops", [])
    if not on("loops_phrase"): return old
    A, B, G, H = (c.get(k, {}).get("bar") for k in "ABGH"); n = 4; last = S.nb - n
    aligned = lambda j: (B is not None and (j - B) % 4 == 0) or (H is not None and (H - j) % 4 == 0)
    mk = lambda w, j, why, ok: {"loop": w, "bar": j, "beats": 16, "status": "suggest" if j is not None else "empty", "time": None if j is None else round(S.t(j) - S.g["offset_ms"] / 1000, 3), "reason": why, "clean": ok, "phrase_aligned": j is not None}
    intro = mk("intro", None, "no clean phrase-aligned 4-bar beat section before START", None)
    if B is not None:
        for j in ([A] if A is not None else []) + list(range(S.first(), B - n + 1)):
            if j is not None and j + n <= B and aligned(j) and clean(S, j) is not False:
                intro = mk("intro", j, "phrase-aligned 4 bars" + (" from A" if j == A else ""), clean(S, j)); break
    outro = mk("outro", None, "no clean phrase-aligned 4-bar beat section at GO or after EXIT", None)
    for j in ([G] if G is not None else []) + (list(range(H, last + 1)) if H is not None else []):
        if j is not None and aligned(j) and clean(S, j): outro = mk("outro", j, "phrase-aligned clean 4 bars" + (" from GO" if j == G else " after EXIT"), True); break
    return [intro, outro]

def run(R, out, on, cfg_path=None, songs=None):
    cfg = json.load(open(cfg_path or f"{R}/genre_params_v5.json")) if os.path.exists(cfg_path or f"{R}/genre_params_v5.json") else json.load(open(f"{TOOLS}/../genre_params_v5.json"))
    os.makedirs(out, exist_ok=True); res = {}
    for f in sorted(os.listdir(f"{R}/cues_v2/allin1/v4_go32")):
        nn = f[:-5]
        if songs and nn not in songs: continue
        try: r = build(Song(R, nn), on, cfg)
        except Exception as e: print("fail", nn, repr(e)); continue
        json.dump(r, open(f"{out}/{nn}.json", "w"), indent=1); res[nn] = r
    return res
if __name__ == "__main__":
    import warnings; warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(); ap.add_argument("--root", default="/workspace/research/dj-auto-cues/proof/real"); ap.add_argument("--out"); ap.add_argument("--off", default=""); ap.add_argument("--only"); ap.add_argument("--config")
    a = ap.parse_args(); off = set(filter(None, a.off.split(",")))
    on = (lambda f: f == a.only) if a.only else (lambda f: f not in off)
    r = run(a.root, a.out or f"{a.root}/cues_v2/allin1/v5_go32", on, a.config); print("built", len(r))
