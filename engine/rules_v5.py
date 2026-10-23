"""Auto-cue rules v5 = v4 plan (cues_v2/allin1/v4_go32) + 8 switchable changes. v4 files are untouched.
Layout (Cullen): A = 8BS>4BS>2BS>1BS that fits (else empty+flag), B START, C 1st Chorus, D Last Chorus (suggest),
G = EXIT - 32 beats, H EXIT, E/F empty, two 4-bar loop suggestions; auto-set only at conf >= threshold (0.85 default).
Changes (FLAGS): loops_phrase, exit_phrase, chorus_verify, drop_robust, tempo_check, cold_start, consensus, genre_params.
Usage: rules_v5.py [--root R] [--out DIR] [--off flag,flag] [--only flag]   (default: all on; writes R/cues_v2/allin1/v5_go32)"""
import json, os, sys, math, ast, re, argparse, numpy as np
TOOLS = os.path.dirname(os.path.abspath(__file__))
FLAGS = ["loops_phrase", "exit_phrase", "chorus_verify", "drop_robust", "tempo_check", "cold_start", "consensus", "genre_params",
         "fix_start", "fix_lead", "fix_chorus", "fix_exit_lead"]   # fix_* = verified Gemini code-review bugs (claims 1, 2, 4, 5)
FLAGS += ["r2_start_vocal", "r2_exit_ok", "r2_exit_from_D", "r2_consensus", "r2_flags", "r2_snap_ver", "r2_pickup"]   # Gemini round-2 fixes
FIXES = [f for f in FLAGS if f.startswith("fix_")]
R2 = [f for f in FLAGS if f.startswith("r2_")]
HARD = ("before track start", "falls before START", "needs review")   # r2_flags: only these block auto-set; others are informational
DEFAULT_OFF = set(FIXES) if os.environ.get("V5_FIXES", "1") == "0" else {"fix_start", "r2_pickup"}   # r2_pickup: no marks affected, -1 vs MIK (13);  fix_start: real bug, but fixing it scored worse vs MIK (35->34) and moved 18/19/20 START earlier; opt in with --off ""
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
        p = f"{R}/out/lead/{nn}.npz"; s.lead = dict(np.load(p)) if os.path.exists(p) else None
    def lead_frac(s, j, n=1):
        if s.lead is None or j < 0: return None
        x = s.lead["lead"]; h = float(s.lead["hop_s"]); x = x / (np.percentile(x, 95) + 1e-9)
        a, b = int(max(0, s.t(j)) / h), int(max(0, s.t(j + n)) / h); y = x[a:b]; return float((y > 0.2).mean()) if len(y) else 0.0
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
    segments = S.words.get("segments", [])
    if segments:
        lines = [(math.floor((w["start"] - S.g["t1"]) / S.g["bar_s"] + 0.25), norm(w.get("text", ""))) for w in segments if w.get("start") is not None]
    else:
        words = sorted((w for w in S.words.get("words", []) if w.get("start") is not None),
                       key=lambda w: w["start"])
        lines = []
        phrase = []
        for word in words:
            if phrase and word["start"] - phrase[-1].get("end", phrase[-1]["start"]) > 0.8:
                lines.append((math.floor((phrase[0]["start"] - S.g["t1"]) / S.g["bar_s"] + 0.25),
                              norm(" ".join(w.get("word", "") for w in phrase))))
                phrase = []
            phrase.append(word)
        if phrase:
            lines.append((math.floor((phrase[0]["start"] - S.g["t1"]) / S.g["bar_s"] + 0.25),
                          norm(" ".join(w.get("word", "") for w in phrase))))
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
    # fix_start (claim 1): v2 pick_start forced any engine START <= bar 4 to bar 8. Keep a real 4-7 bar intro, and keep
    # j < 4 when the lead vocal runs across bars 0-3 (true short/cold start).
    v1B = (S.d1["cues"].get("B") or {}).get("bar")
    if on("fix_start") and B is not None and v1B is not None and "missed intro" in (c["B"].get("reason") or "") and v1B != B:
        lf = S.lead_frac(0, 4); dr = np.mean([S.bar_frac("drums", j, 0.15) or 0 for j in range(4)]) if S.st is not None else 0
        # j < 4 only when lead vocal AND drums run over bars 0-3 (a cappella intros, e.g. 15 Cupid Shuffle 'Aca In', keep v4's START)
        voc4 = (S.lead_frac(v1B) or 0) > 0.25 if S.lead is not None else True   # r2_start_vocal: a bar-4 instrument entry is not START
        if (v1B >= 4 and (voc4 or not on("r2_start_vocal"))) or (v1B < 4 and lf is not None and lf > 0.35 and dr > 0.5):
            why("B", f"fix_start: kept engine START {v1B} ({'real 4+ bar intro' if v1B >= 4 else 'lead vocal runs over bars 0-3'}) instead of forced bar {B}"); B = v1B; c["B"]["bar"] = B
    # fix_lead (claim 2): v_lead stopped at the first vocal run (intro tease) and only looked +-2 bars. Look at every sustained
    # lead run that starts after a >=2-bar vocal gap, within +-4 bars of START, nearest first, phrase-aligned (4-bar) preferred.
    if on("fix_lead") and B is not None and S.lead is not None:
        on_ = [(S.lead_frac(j) or 0) > 0.35 for j in range(S.nb)]
        runs = [j for j in range(S.nb - 3) if on_[j] and sum(on_[j:j + 3]) >= 2 and (j < 2 or not any(on_[j - 2:j]))]
        near = sorted((j for j in runs if abs(j - B) <= 4), key=lambda j: (abs(j - B) > 1 and j % 4 != 0, abs(j - B)))
        if near and on("r2_pickup") and near[0] % 4 == 3:   # pickup bar: prefer the next downbeat if vocals continue and drums/bass jump there
            j = near[0] + 1; a = S.db_level(j - 2, 2); b = S.db_level(j, 1)
            if (S.lead_frac(j) or 0) > 0.35 and len(a) and len(b) and float(b.mean() - a.mean()) >= 0.1: near[0] = j
        if near and near[0] != B: why("B", f"fix_lead: lead vocal run starts at bar {near[0]} (after a vocal gap); moved {B}->{near[0]}"); B = near[0]; c["B"]["bar"] = B
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
        def vdrop(j):
            if S.lead is None or S.st is None: return False
            pre = S.lead_frac(j - 2, 2) or 0; post = S.lead_frac(j, 4) or 0; dr = S.bar_frac("drums", j, 0.15) or 0
            return pre > 0.35 and post < 0.12 and dr > 0.4
        # r2_exit_ok: a bar is an acceptable EXIT if the drum/bass drop holds OR the lead vocal drops out over a running beat
        ok_exit = (lambda j: True if (chk(S, j, P) is True or vdrop(j)) else chk(S, j, P)) if on("r2_exit_ok") else (lambda j: chk(S, j, P))
        # fix_exit_lead (claim 5): on drum outros the beat continues but the lead vocal stops for >= 4 bars. Accept that bar.
        if on("fix_exit_lead") and S.lead is not None and S.st is not None:
            if not vdrop(H) and chk(S, H, P) is not True:
                cand = sorted((j for j in range(H - 2, H + 3) if vdrop(j)), key=lambda j: (abs(j - H), j))
                if cand: why("H", f"fix_exit_lead: lead vocal stops for 4+ bars at {cand[0]} while drums continue; moved {H}->{cand[0]}"); H = cand[0]
        if on("exit_phrase") and B is not None:
            pb = P["exit_phrase_bars"]; r = P["exit_snap_radius"]; D = c.get("D", {}).get("bar")
            anchors = [B] + ([D] if on("r2_exit_from_D") and D is not None else [])   # SPEC 3.1: phrase counted from START or Last Chorus
            onp = lambda j, n: any((j - a) % n == 0 for a in anchors)
            if onp(H, 4): c["H"]["confidence"] += 0.05; why("H", "on the phrase grid from " + ("START" if (H - B) % 4 == 0 else "Last Chorus"))
            else:
                cand = sorted((j for j in range(H - r, H + r + 1) if onp(j, 4)), key=lambda j: (not onp(j, pb), ok_exit(j) is not True, abs(j - H)))
                good = [j for j in cand if ok_exit(j) is not False]
                if good: why("H", f"snapped {H}->{good[0]} onto the phrase grid"); H = good[0]
                else: c["H"]["confidence"] -= 0.15; fl("H", "no phrase boundary near EXIT")
        c["H"]["bar"] = H; c.setdefault("G", {"slot": "G", "flags": [], "name": "GO", "color": "#CC00CC"}).update(bar=H - GL); c["G"]["confidence"] = c["H"]["confidence"] - 0.02
    # A ladder (re-done in case B moved)
    if B is not None and notes.get("cold_start") != "deliberate":
        n = next((k for k in (8, 4, 2, 1) if B - k >= S.first()), None)
        if n: c["A"].update(bar=B - n, bars_before=n, name=f"{n}BS", confidence=c["B"]["confidence"] - 0.02)
        else: c["A"].update(bar=None, name="BS", flags=["needs review: no room for A"])
    # fix_chorus (claim 4): v2 used lyrics/similarity only when allin1 had zero chorus labels after START. When labels give <2
    # distinct choruses after START, score every section start in [START+4, EXIT-4]: label 1.0 + recurrence 0.8 + lyric hook 0.5
    # + drums/bass jump 0.5; C = first, D = last with score >= 1.3. (Adjacent chorus merging already exists in rules.py.)
    if on("fix_chorus") and B is not None:
        lab = sorted({s_["j0"] for s_ in S.segs if s_["cls"] == "chorus" and B + 4 <= s_["j0"] and (H is None or s_["j0"] < H - 4)})
        if len(lab) < 2:
            big = [s_ for s_ in S.segs if s_["cls"] == "chorus" and s_["j1"] - s_["j0"] > 24]
            cands = sorted({s_["j0"] for s_ in S.segs} | {s_["j0"] + k for s_ in big for k in range(8, s_["j1"] - s_["j0"], 8)})
            cands = [j for j in cands if B + 4 <= j and (H is None or j <= H - 4)]
            def jump(j):
                if S.st is None or j < 2: return False
                a = S.db_level(j - 2, 2); b = S.db_level(j, 2); return len(a) and len(b) and float(b.mean() - a.mean()) >= 0.15
            sc = {j: (j in lab) * 1.0 + ((recurrence(S, j) or 0) >= P["chorus_sim_thr"]) * 0.8 + (lyric_repeat(S, j) is True) * 0.5 + bool(jump(j)) * 0.5 for j in cands}
            good = [j for j in cands if sc[j] >= 1.3]; notes["chorus_scores"] = sc
            if good:
                for L, j in (("C", good[0]), ("D", good[-1])):
                    if c[L].get("bar") != j: why(L, f"fix_chorus: section at bar {j} scores {sc[j]:.1f} (label/recurrence/lyrics/energy)")
                    c[L].update(bar=j, confidence=0.55)
                if good[0] == good[-1]: c["D"].setdefault("flags", []).append("single chorus")
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
                if on("r2_snap_ver") and j not in ver and L == "C":   # C only: snapping D cost a correct mark on 20 (last chorus is often a variant)   # snap to the first/last labelled chorus that actually recurs
                    nj = ver[0] if L == "C" else ver[-1]
                    if nj != j: why(L, f"r2_snap_ver: moved {j}->{nj} (recurring labelled chorus)"); c[L]["bar"] = j = nj
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
            d_ = (0.05 if a >= 0.75 else 0 if a >= 0.5 else -0.3 * (1 - a)) if on("r2_consensus") else (0.05 if a >= 0.99 else -0.3 * (1 - a))
            c[L]["confidence"] = round(c[L]["confidence"] + d_, 3); why(L, f"{len(hit)}/{len(src)} boundary sources agree ({','.join(hit)})")
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
        elif x["confidence"] >= P["auto_set_min_conf"] and not [f for f in x.get("flags", []) if f != "on downbeat" and (not on("r2_flags") or any(h in f for h in HARD))]: x["status"] = "set"
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
    off |= DEFAULT_OFF if a.off == "" and "--off" not in sys.argv else set()
    on = (lambda f: f == a.only) if a.only else (lambda f: f not in off)
    r = run(a.root, a.out or f"{a.root}/cues_v2/allin1/v5_go32", on, a.config); print("built", len(r))
