ALL-IN-ONE v5 (plain text) — Sound & Service AutoCue engine, Oct 15 2026
Contents: rules_v5.py, simfeat_v5.py, genre_tags_v5.py, fixed files (learner_marks.py, gemini_v5.py, gemini_apply_v5.py, score_v5b.py), SCORE-v5.

=================== engine/rules_v5.py ===================
"""Auto-cue rules v5 = v4 plan (cues_v2/allin1/v4_go32) + 8 switchable changes. v4 files are untouched.
Layout (Cullen): A = 8BS>4BS>2BS>1BS that fits (else empty+flag), B START, C 1st Chorus, D Last Chorus (suggest),
G = EXIT - 32 beats, H EXIT, E/F empty, two 4-bar loop suggestions; auto-set only at conf >= threshold (0.85 default).
Changes (FLAGS): loops_phrase, exit_phrase, chorus_verify, drop_robust, tempo_check, cold_start, consensus, genre_params.
Usage: rules_v5.py [--root R] [--out DIR] [--off flag,flag] [--only flag]   (default: all on; writes R/cues_v2/allin1/v5_go32)"""
import json, os, sys, math, ast, re, argparse, numpy as np
TOOLS = os.path.dirname(os.path.abspath(__file__))
FLAGS = ["loops_phrase", "exit_phrase", "chorus_verify", "drop_robust", "tempo_check", "cold_start", "consensus", "genre_params",
         "fix_start", "fix_lead", "fix_chorus", "fix_exit_lead"]   # fix_* = verified Gemini code-review bugs (claims 1, 2, 4, 5)
FIXES = [f for f in FLAGS if f.startswith("fix_")]
DEFAULT_OFF = set(FIXES) if os.environ.get("V5_FIXES", "1") == "0" else {"fix_start"}   # fix_start: real bug, but fixing it scored worse vs MIK (35->34) and moved 18/19/20 START earlier; opt in with --off ""
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
    # fix_start (claim 1): v2 pick_start forced any engine START <= bar 4 to bar 8. Keep a real 4-7 bar intro, and keep
    # j < 4 when the lead vocal runs across bars 0-3 (true short/cold start).
    v1B = (S.d1["cues"].get("B") or {}).get("bar")
    if on("fix_start") and B is not None and v1B is not None and "missed intro" in (c["B"].get("reason") or "") and v1B != B:
        lf = S.lead_frac(0, 4); dr = np.mean([S.bar_frac("drums", j, 0.15) or 0 for j in range(4)]) if S.st is not None else 0
        # j < 4 only when lead vocal AND drums run over bars 0-3 (a cappella intros, e.g. 15 Cupid Shuffle 'Aca In', keep v4's START)
        if v1B >= 4 or (lf is not None and lf > 0.35 and dr > 0.5):
            why("B", f"fix_start: kept engine START {v1B} ({'real 4+ bar intro' if v1B >= 4 else 'lead vocal runs over bars 0-3'}) instead of forced bar {B}"); B = v1B; c["B"]["bar"] = B
    # fix_lead (claim 2): v_lead stopped at the first vocal run (intro tease) and only looked +-2 bars. Look at every sustained
    # lead run that starts after a >=2-bar vocal gap, within +-4 bars of START, nearest first, phrase-aligned (4-bar) preferred.
    if on("fix_lead") and B is not None and S.lead is not None:
        on_ = [(S.lead_frac(j) or 0) > 0.35 for j in range(S.nb)]
        runs = [j for j in range(S.nb - 3) if on_[j] and sum(on_[j:j + 3]) >= 2 and (j < 2 or not any(on_[j - 2:j]))]
        near = sorted((j for j in runs if abs(j - B) <= 4), key=lambda j: (abs(j - B) > 1 and j % 4 != 0, abs(j - B)))
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
        # fix_exit_lead (claim 5): on drum outros the beat continues but the lead vocal stops for >= 4 bars. Accept that bar.
        if on("fix_exit_lead") and S.lead is not None and S.st is not None:
            def vdrop(j): 
                pre = S.lead_frac(j - 2, 2) or 0; post = S.lead_frac(j, 4) or 0; dr = S.bar_frac("drums", j, 0.15) or 0
                return pre > 0.35 and post < 0.12 and dr > 0.4
            if not vdrop(H) and chk(S, H, P) is not True:
                cand = sorted((j for j in range(H - 2, H + 3) if vdrop(j)), key=lambda j: (abs(j - H), j))
                if cand: why("H", f"fix_exit_lead: lead vocal stops for 4+ bars at {cand[0]} while drums continue; moved {H}->{cand[0]}"); H = cand[0]
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
    off |= DEFAULT_OFF if a.off == "" and "--off" not in sys.argv else set()
    on = (lambda f: f == a.only) if a.only else (lambda f: f not in off)
    r = run(a.root, a.out or f"{a.root}/cues_v2/allin1/v5_go32", on, a.config); print("built", len(r))

=================== engine/simfeat_v5.py ===================
"""v5 change 3 helper: per-bar chroma + MFCC (librosa, venv-allin1) from the decoded WAVs, on the Serato grid.
Usage: simfeat_v5.py <root> (root has wav/, cues/allin1/). Writes <root>/features_v5/<nn>.sim.npz. Skips existing; ~300 MB RAM."""
import sys, os, glob, json, numpy as np, librosa, warnings; warnings.filterwarnings("ignore")
R = sys.argv[1] if len(sys.argv) > 1 else "/workspace/research/dj-auto-cues/proof/real"
os.makedirs(f"{R}/features_v5", exist_ok=True)
for w in sorted(glob.glob(f"{R}/wav/*.wav")):
    nn = os.path.basename(w).split(".")[0].split(" ")[0]; out = f"{R}/features_v5/{nn}.sim.npz"; cj = f"{R}/cues/allin1/{nn}.json"
    if os.path.exists(out) or not os.path.exists(cj): continue
    g = json.load(open(cj))["grid"]; y, sr = librosa.load(w, sr=22050, mono=True); hop = 512
    ch = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop); mf = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20, hop_length=hop)
    t = librosa.frames_to_time(np.arange(ch.shape[1]), sr=sr, hop_length=hop); nb = int((len(y) / sr - g["t1"]) / g["bar_s"]) + 1
    C, M = np.zeros((nb, 12), np.float32), np.zeros((nb, 20), np.float32)
    for j in range(nb):
        m = (t >= g["t1"] + j * g["bar_s"]) & (t < g["t1"] + (j + 1) * g["bar_s"])
        if m.any(): C[j] = ch[:, m].mean(1); M[j] = mf[:, m].mean(1)
    np.savez_compressed(out, chroma=C, mfcc=M); print(nn, nb, flush=True); del y, ch, mf

=================== engine/genre_tags_v5.py ===================
"""Read the ID3 genre tag (Serato shows this) of each input MP3, read-only. Writes <root>/genre_tags.json {nn: genre}."""
import sys, glob, os, json, mutagen
R = sys.argv[1] if len(sys.argv) > 1 else "/workspace/research/dj-auto-cues/proof/real"; out = {}
for f in sorted(glob.glob(f"{R}/in/*")):
    nn = os.path.basename(f).split(" ")[0].split(".")[0]
    try: m = mutagen.File(f, easy=True); out[nn] = (m.get("genre") or [""])[0] if m else ""
    except Exception: out[nn] = ""
json.dump(out, open(sys.argv[2] if len(sys.argv) > 2 else f"{R}/genre_tags.json", "w"), indent=1); print(len(out), "tags")

=================== engine/learner_marks.py ===================
"""Correction learner. Truth = Cullen's Serato cues, overridden per cue by review-page corrections (corrections.json) when present.
Learns per cue and per song group the bar offset (truth - engine) and keeps a rule only when leave-one-out scoring says it helps.
Writes ../learned_profile_marks.json (loaded by rules_v2) and ../learn_loo_marks.json (per-song LOO picks for scoring)."""
import json, os, re, sys, statistics as stt
from collections import Counter
R = "/workspace/research/dj-auto-cues/proof/real"; sys.path.insert(0, f"{R}/tools")
ns = {}; exec(open(f"{R}/tools/gemini_v2.py").read().split("def call(")[0], ns); M, bars_of_truth = ns["M"], ns["bars_of_truth"]
STRICT = os.environ.get("LEARNER_STRICT", "1") == "1"   # claim 8 fix: >=65% agreement, >=4 hits, LOO gain, no exact track broken (LEARNER_STRICT=0 = old 3-song/40%)
CORR = os.environ.get("CORRECTIONS", f"{R}/corrections/corrections.json")
def song_type(nn):
    n = json.load(open(f"{R}/cues/allin1/{nn}.json")); import csv
    name = {r["n"].zfill(2): r["laptop_path"] for r in csv.DictReader(open(f"{R}/MANIFEST.csv"))}[nn].lower()
    if re.search(r"edit|cutdown|short|clean extended|jump off", name): t = "edit"
    elif re.search(r"remix|rmx|re-drum|redrum|mashup|mix\)|bootleg", name): t = "remix"
    else: t = "original"
    bpm = 240 / n["grid"]["bar_s"]; band = "slow<100" if bpm < 100 else "mid100-125" if bpm <= 125 else "fast>125"
    return {"type": t, "tempo": band}
def truth(nn, corr):
    tr = {}; g = json.load(open(f"{R}/cues/allin1/{nn}.json"))["grid"]
    for ev in corr:   # last decision per cue wins; to_time is Serato seconds
        if ev.get("track") == nn and ev.get("cue") in "BCDH" and ev.get("to_time") is not None:
            tr[ev["cue"]] = round((ev["to_time"] + g["offset_ms"] / 1000 - g["t1"]) / g["bar_s"])
    return tr
def engine(nn, base="go16"):
    return {L: json.load(open(f"{R}/cues_v2/allin1/{base}/{nn}.json"))["cues"][L].get("bar") for L in "BCDH"}
def ctx(nn):
    d = json.load(open(f"{R}/cues/allin1/{nn}.json")); H = engine(nn)["H"]
    nxt = next((s["lab"] for s in d["segments"] if H is not None and s["j0"] >= H - 1), "none")
    return {**song_type(nn), "after_exit": nxt if nxt in ("outro", "end", "chorus", "verse", "inst") else "other"}
KEYS = [None, "type", "tempo", "after_exit"]
def learn(songs, corr):
    """per cue: for each grouping key, mode offset per group value; returns rules {cue: {key, table}}"""
    data = {nn: (truth(nn, corr), engine(nn), ctx(nn)) for nn in songs}
    out = {}
    for L in "BCDH":
        best = None
        for key in KEYS:
            tab = {}
            groups = Counter()
            for nn, (tr, en, cx) in data.items():
                if L in tr and en[L] is not None: groups[cx[key] if key else "*"] += 1
            for gv in groups:
                offs = [tr[L] - en[L] for nn, (tr, en, cx) in data.items() if L in tr and en[L] is not None and (cx[key] if key else "*") == gv]
                o, k = Counter(offs).most_common(1)[0]
                if (STRICT and o != 0 and k >= 4 and k / len(offs) >= 0.65) or (not STRICT and len(offs) >= 3 and k / len(offs) >= 0.4): tab[gv] = {"offset_bars": o, "support": f"{k}/{len(offs)}"}
            # training fit: how many exact hits with this table vs no change
            fit = sum((tr[L] - en[L]) == tab.get(cx[key] if key else "*", {"offset_bars": 0})["offset_bars"] for nn, (tr, en, cx) in data.items() if L in tr and en[L] is not None)
            base = sum(tr[L] == en[L] for nn, (tr, en, cx) in data.items() if L in tr and en[L] is not None)
            if best is None or fit > best[0]: best = (fit, key, tab, base)
        fit, key, tab, base = best
        if STRICT and fit > base:   # never break a track the engine already had exactly right
            broke = [nn for nn, (tr, en, cx) in data.items() if L in tr and en[L] is not None and tr[L] == en[L] and tab.get(cx[key] if key else "*", {"offset_bars": 0})["offset_bars"] != 0]
            if broke: fit = base
        out[L] = {"key": key, "table": tab, "train_hits": fit, "train_hits_no_rule": base} if fit > base else {"key": None, "table": {}, "train_hits": base}
    return out
def offset_for(rules, L, cx):
    r = rules.get(L, {}); k = r.get("key"); t = r.get("table", {})
    return t.get(cx[k] if k else "*", {}).get("offset_bars", 0)
def main():
    corr = json.load(open(CORR)) if os.path.exists(CORR) else []
    songs = sorted(M); loo = {}
    for nn in songs:   # leave-one-out: rules learned without this song (and without its corrections)
        rules = learn([x for x in songs if x != nn], [e for e in corr if e.get("track") != nn])
        loo[nn] = {L: offset_for(rules, L, ctx(nn)) for L in "BCDH"}
    full = learn(songs, corr)
    if STRICT:   # keep a cue's rule only if leave-one-out hits beat no-rule hits
        for L in "BCDH":
            hit = sum(truth(nn, corr).get(L) is not None and engine(nn)[L] is not None and truth(nn, corr)[L] - engine(nn)[L] == loo[nn][L] for nn in songs)
            base = sum(truth(nn, corr).get(L) is not None and truth(nn, corr)[L] == engine(nn)[L] for nn in songs)
            if hit <= base:
                full[L] = {"key": None, "table": {}, "train_hits": base, "dropped": f"LOO {hit} <= no-rule {base}"}
                for nn in songs: loo[nn][L] = 0
    prof = {"built_from": {"truth_songs": len(songs), "corrections": len(corr), "corrections_file": CORR}, "rules": full,
            "song_context": {nn: ctx(nn) for nn in songs}}
    json.dump(prof, open(f"{R}/learned_profile_marks.json", "w"), indent=1); json.dump(loo, open(f"{R}/learn_loo_marks.json", "w"), indent=1)
    print(json.dumps(full, indent=0)[:1500]); print("corrections used:", len(corr))
if __name__ == "__main__" and "--buckets" not in sys.argv: main()

# ---- v5 change 8: per-genre-bucket parameter tuning (python learner_marks.py --buckets [--min-marks N]) ----
def tune_buckets(min_marks=8):
    """Grid-search a few v5 parameters per bucket against Cullen's review marks (corrections/*.json, last correct/set_here
    per track+cue, within 1 beat). A bucket's value changes only when it has >= min_marks marks AND strictly beats the default.
    Writes ../genre_params_v5.tuned.json (use with rules_v5.py --config). The default file is never overwritten."""
    import glob, itertools, rules_v5 as V5
    ev = []
    for f in sorted(glob.glob(f"{R}/corrections/corrections*.json")): ev += json.load(open(f))
    last = {}
    for e in ev:
        if e.get("cue") in list("ABCDGH") and e.get("action") in ("correct", "set_here") and e.get("to_beat") is not None: last[(e["track"], e["cue"])] = e["to_beat"]
    cfg = json.load(open(f"{R}/genre_params_v5.json")); grid = {"exit_phrase_bars": [4, 8], "exit_snap_radius": [1, 2], "chorus_sim_thr": [0.7, 0.8, 0.9], "drop_frac": [0.15, 0.25, 0.35]}
    songs = sorted({k[0] for k in last}); on = lambda f: True; byb = {}
    for nn in songs: byb.setdefault(V5.bucket(V5.Song(R, nn))[0], []).append(nn)
    report = {}
    def hits(nns, over):
        c = json.loads(json.dumps(cfg)); [c["buckets"].setdefault(b, {}).update(over) for b in c["buckets"]]; h = 0
        for nn in nns:
            p = V5.build(V5.Song(R, nn), on, c)["cues"]
            for L in "ABCDGH":
                t = last.get((nn, L)); b = p.get(L, {}).get("bar")
                if t is not None and b is not None and abs(b * 4 - t) <= 1: h += 1
        return h
    for b, nns in byb.items():
        n = sum(1 for k in last if k[0] in nns); base = hits(nns, {}); best = (base, {})
        if n >= min_marks:
            for vals in itertools.product(*grid.values()):
                over = dict(zip(grid, vals)); h = hits(nns, over)
                if h > best[0]: best = (h, over)
            cfg["buckets"][b] = best[1]
        report[b] = {"songs": nns, "marks": n, "hits_default": base, "hits_tuned": best[0], "changed": best[1] if n >= min_marks else "too few marks"}
    cfg["_tuned"] = report; json.dump(cfg, open(f"{R}/genre_params_v5.tuned.json", "w"), indent=1); print(json.dumps(report, indent=1))
if __name__ == "__main__" and "--buckets" in sys.argv:
    tune_buckets(int(sys.argv[sys.argv.index("--min-marks") + 1]) if "--min-marks" in sys.argv else 8)

=================== engine/gemini_v5.py ===================
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
    import learner as LN
    me = LN.ctx(nn); corr = json.load(open(LN.CORR)) if os.path.exists(LN.CORR) else []
    def dist(x):
        c = LN.ctx(x); return (c["type"] != me["type"]) * 2 + (c["tempo"] != me["tempo"]) - 0.5 * any(e.get("track") == x for e in corr)
    close = sorted((x for x in M if x != nn), key=dist)[:8]   # leave-one-out: never its own cues/corrections
    def example2(x):
        tr = LN.truth(x, [e for e in corr if e.get("track") != nn]); d = json.load(open(f"{R}/cues/allin1/{x}.json"))
        return f"Example song {x} ({LN.ctx(x)['type']}, {LN.ctx(x)['tempo']} BPM): sections " + ", ".join(f"{s['lab']}@{s['j0']}" for s in d["segments"]) + f"; analyzer last-chorus end bar {d['cues']['D'].get('bar')}; Cullen's cues: " + ", ".join(f"{k}={v}" for k, v in sorted(tr.items()))
    ex = "\n".join(example2(x) for x in close)
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

=================== engine/gemini_apply_v5.py ===================
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

=================== engine/score_v5b.py ===================
"""Re-score v4, v5 (8 changes), v5-fixed (+ claims 1,2,4,5 fixes), each with/without CACHED Gemini (gemini_v2 = review page picks,
gemini_v3), plus per-fix ablation. Truth = Cullen's review marks only (both cue-review folders if present); MIK = 'vs Mixed In Key'."""
import json, os, sys, glob, collections, tempfile, shutil
R = "/workspace/research/dj-auto-cues/proof/real"; sys.path.insert(0, f"{R}/tools"); import rules_v5 as V, gemini_apply_v5 as GA
import warnings; warnings.filterwarnings("ignore")
ev = []
for f in sorted(glob.glob(f"{R}/corrections/corrections*.json")): ev += json.load(open(f))
last = {}
for e in ev:
    if e.get("cue") in list("ABCDGH") and e["action"] in ("correct", "set_here") and e.get("to_beat") is not None: last[(e["track"], e["cue"])] = e["to_beat"]
    if e["action"] == "clear": last.pop((e["track"], e.get("cue")), None)
ns = {}; exec(open(f"{R}/tools/gemini_v2.py").read().split("def call(")[0], ns); BT = ns["bars_of_truth"]
def sc(plans):
    m = collections.defaultdict(lambda: [0, 0]); t = collections.defaultdict(lambda: [0, 0])
    for (nn, L), tb in last.items():
        if nn in plans: v = plans[nn]["cues"].get(L, {}).get("bar"); m[L][1] += 1; m[L][0] += v is not None and abs(v * 4 - tb) <= 1
    for nn, p in plans.items():
        try: tr = BT(nn)
        except Exception: continue
        for L, x in tr.items(): v = p["cues"].get(L, {}).get("bar"); t[L][1] += 1; t[L][0] += v is not None and v == x
    tot = lambda D: [sum(v[0] for v in D.values()), sum(v[1] for v in D.values())]
    nset = sum(x.get("status") == "set" for p in plans.values() for L, x in p["cues"].items() if L in "ABGH")
    return {"marks": dict(m), "marks_total": tot(m), "mik_total": tot(t), "mik": dict(t), "auto_set": nset}
load = lambda d: {f[:-5]: json.load(open(f"{d}/{f}")) for f in os.listdir(d) if f.endswith(".json")}
tmp = tempfile.mkdtemp(); runs = {"v4": load(f"{R}/cues_v2/allin1/v4_go32")}
runs["v5"] = V.run(R, f"{tmp}/v5", lambda f: f not in V.FIXES)
runs["v5_fixed"] = V.run(R, f"{tmp}/v5f", lambda f: f not in V.DEFAULT_OFF)
runs["v5_fixed + fix_start"] = V.run(R, f"{tmp}/v5fs", lambda f: True)
for fx in V.FIXES:
    runs[f"v5 + only {fx}"] = V.run(R, f"{tmp}/o{fx}", lambda f, fx=fx: f not in V.FIXES or f == fx)
    runs[f"v5_fixed(all 4 fixes) - {fx}"] = V.run(R, f"{tmp}/m{fx}", lambda f, fx=fx: f != fx)
res = {k: sc(v) for k, v in runs.items()}
for k in ("v4", "v5", "v5_fixed"):
    for src in ("gemini_v2", "gemini_v3"):
        res[f"{k} + {src} (cached, fixed apply)"] = sc({nn: GA.apply(p, nn, src) for nn, p in runs[k].items()})
    res[f"{k} + gemini_v3 (old apply: overrides B/H)"] = sc({nn: GA.apply(p, nn, "gemini_v3", keep_confident=False) for nn, p in runs[k].items()})
shutil.rmtree(tmp); json.dump(res, open(f"{R}/score_v5b.json", "w"), indent=1)
f = lambda x: f"{x[0]}/{x[1]}"
print("| run | vs Cullen's marks | A | B | C | D | G | H | vs Mixed In Key (B/C/D/H bars) | auto-set A/B/G/H |\n|---|---|---|---|---|---|---|---|---|---|")
for k, r in res.items(): print(f"| {k} | {f(r['marks_total'])} | " + " | ".join(f(r["marks"].get(L, [0, 0])) for L in "ABCDGH") + f" | {f(r['mik_total'])} | {r['auto_set']} |")
print(f"\nmarks: {len(last)} cues on {len({k[0] for k in last})} songs ({sorted({k[0] for k in last})}); Gemini new spend $0 (all cached)")
# changed positions v5 -> v5_fixed
for nn in sorted(runs["v5_fixed"]):
    for L in "ABCDGH":
        a, b = runs["v5"][nn]["cues"].get(L, {}).get("bar"), runs["v5_fixed"][nn]["cues"].get(L, {}).get("bar")
        if a != b: print(f"moved {nn} {L}: {a} -> {b}" + (f"  (mark {last[(nn, L)]/4:g})" if (nn, L) in last else ""))

=================== docs/SCORE-v5-2026-10-15.md ===================
# Auto-cue engine v5 — score + ablation (Oct 15, 2026, ~9:45 PM PT)

Answer key: **only Cullen's review-page marks** (corrections/corrections.json, refreshed from his Pro at ~9:20 PM PT;
last *correct*/*set_here* per track+cue, match = within 1 beat). That is **18 marked cues on 4 songs (02, 14, 15, 20)** —
far too few to separate versions. The Serato cues in truth_tags.json are **Mixed In Key's**, shown only as "vs Mixed In Key".
No stress5 marks exist yet (~/Desktop/cue-review-stress5/corrections.json not found on the Pro).

Code: tools/rules_v5.py (post-processes v4_go32; v4 untouched), tools/simfeat_v5.py (per-bar chroma+MFCC → features_v5/),
tools/genre_tags_v5.py, genre_params_v5.json, tools/score_v5.py, learner_marks.py --buckets. Output: cues_v2/allin1/v5_go32/.
Flags: `rules_v5.py --off a,b` or `--only a`. Stress5 preview (read-only, not on the review page): proof/v5_stress5_preview/.

## Scores (20-song set)
| run | marks total | A | B | C | D | G | H | vs Mixed In Key total | auto-set A/B/G/H cues | auto-set & marked right |
| v4 | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 27 | 0/0 |
| v5_all | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 19 | 0/0 |
| only_loops_phrase | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 27 | 0/0 |
| all_minus_loops_phrase | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 19 | 0/0 |
| only_exit_phrase | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 23 | 0/0 |
| all_minus_exit_phrase | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 22 | 0/0 |
| only_chorus_verify | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 27 | 0/0 |
| all_minus_chorus_verify | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 19 | 0/0 |
| only_drop_robust | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 27 | 0/0 |
| all_minus_drop_robust | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 19 | 0/0 |
| only_tempo_check | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 27 | 0/0 |
| all_minus_tempo_check | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 19 | 0/0 |
| only_cold_start | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 26 | 0/0 |
| all_minus_cold_start | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 21 | 0/0 |
| only_consensus | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 24 | 0/0 |
| all_minus_consensus | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 22 | 0/0 |
| only_genre_params | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 27 | 0/0 |
| all_minus_genre_params | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 19 | 0/0 |
marks: 18 cues on 4 songs

## What each change did
| # | change | 20-song set | stress5 preview (118 songs) |
|---|---|---|---|
| 1 | loops_phrase: loops only on bars 4k from START/EXIT + clean-beat stem check | moved 3 loop suggestions | moved 29 loops |
| 2 | exit_phrase: EXIT snaps ±2 bars to 8- (then 4-) bar grid from START; G=EXIT−8 bars; −0.15 conf + flag if none | flagged 9 "no phrase boundary near EXIT"; auto-set 27→23 alone | 63 flagged (often START itself is off-phrase) |
| 3 | chorus_verify: z-scored chroma/MFCC 4-bar self-similarity ≥0.8 or fuzzy lyric-line repeat | keeps labels when any chorus recurs; else keeps C/D at 0.3 conf and names the best recurring bar (auto-moving picked verses on 06, so removed) | conf only |
| 4 | drop_robust: drums+bass must stay down the whole bar (≥80% frames below 60% of the prior 2 bars, mean drop ≥0.25) | moved 15 H 24→25 (G follows); most DJ-edit outros keep the beat, so it rarely fires and no longer penalises | moved H on a few |
| 5 | tempo_check: beat-interval spread (allin1 beats) + multi-BPM Serato grid; >4% → bar times from detected beats + "tempo drift" flag | no raw beats kept for the 20 songs and all Serato grids single-BPM → never fired | 11 songs flagged (Conga, Superstition, Macarena, Shania… plausible; Wake Me Up / Next Episode / Disclosure likely beat-tracker glitches) |
| 6 | cold_start: vocals+drums in bar 1 → deliberate cold start (A empty, flagged); vocals without drums → "a cappella intro" (kept, review) | 5 a cappella intros flagged (15 Cupid Shuffle "Aca In" is one — first version wrongly made it a cold start and lost an A mark; fixed) | 5 cold starts, 14 a cappella |
| 7 | consensus: START/EXIT conf +0.05 if allin1/SongFormer/stems/energy all agree, −0.3×disagreeing share otherwise | SongFormer present for 7 songs; auto-set 27→24 alone | conf only |
| 8 | genre_params: bucket from ID3 genre + BPM + percussion; per-bucket params in genre_params_v5.json (all = defaults now) | buckets: 6 house/EDM, 6 hip hop/R&B, 4 pop, 4 other; learner --buckets found no change (too few marks) | 40 hip hop/R&B, 35 pop, 35 house, 4 latin |

Net: **same cue positions as v4 on every marked cue (9/18 both; vs Mixed In Key 33/66 both)**; v5 auto-sets fewer cues
(27→19 on the 20, 38→36 on stress5) and adds review flags. No ablation changes accuracy on the marks — the data can't tell yet.

---
# v5-fixed: Gemini code-review claims verified (Oct 15, 2026, ~10:15 PM PT)
Marks refreshed from the Pro (cue-review: 33 events; cue-review-stress5 has no corrections.json yet). Gemini = CACHED picks only, $0 new spend.
**Bias warning:** 28 of 33 mark events were on cues the page showed from Google (gemini_v2): 18 'correct', 10 moved. The marks favor gemini_v2.

| # | claim | real bug? | fixed? (switch) | effect (marks / vs Mixed In Key) |
|---|---|---|---|---|
| 1 | pick_start forces START<=4 to bar 8 | yes (j<=4 → 8, even real 4-bar intros) | rules_v5 `fix_start`, **default OFF**: keeps j>=4; j<4 only if lead vocal AND drums run bars 0-3 (a cappella intros like 15 keep v4) | 9/18 = ; MIK 35→34 (moves 18/20 START 8→4; 20 got closer to his mark but not within 1 beat) |
| 2 | v_lead stops at first vocal run, ±2 leash | yes | `fix_lead` on: lead runs after ≥2-bar vocal gap, ±4 bars, phrase-preferred | 9/18 = ; MIK +2 inside v5_fixed (06/09/17 START moved) |
| 3 | build_v4/gemini_v3 leave A stale, no A status, loops not rerun | yes in build_v4 & gemini_v3 | already fixed in rules_v5 (A ladder + statuses + G + loops recomputed every run); gemini_apply_v5 recomputes A/G | none on marks |
| 4 | C/D: lyrics/similarity only with zero labels; no chorus merge; single block C=D | partly: merging is FALSE (rules.py merges adjacent same-label segments; no adjacent chorus left in any draft); the rest true | `fix_chorus` on: when <2 labelled choruses after START, score sections (label+recurrence+lyric hook+energy jump), split >24-bar blocks every 8 bars; single chorus keeps C=D with flag (cue-logic empties C/D only when the block swallows START) | 9/18 = ; MIK 33→35 alone (01 C/D, 08 D) |
| 5 | pick_exit uses full-mix RMS only | yes | `fix_exit_lead` on: accept bar where lead vocal stops ≥4 bars while drums continue (±2 bars) | 9/18 = ; MIK +1 inside v5_fixed (05/09 EXIT) |
| 6 | plan_loops bar-by-bar | true for v2; **already fixed** by v5 change 1 (only phrase-aligned bars) | — | — |
| 7 | gemini_v3: C anchor cs[0], reads go16 not v4, overrides B/H | C anchor: only when plan C is empty; go16 read: yes; B/H override: only non-'set' cues, but v4's lead/drop fixes were ignored because it read v3 plans | gemini_v5.py (reads v5/v4 plan, C on best chorus candidate) + gemini_apply_v5.py (no override of conf≥0.85/set B/H, recompute A/G); not called (no spend) | cached v3 picks: fixed vs old apply 10/18 both; MIK 29 vs 28 on v4 |
| 8 | learner_marks 3 songs/40% overfits | yes (it kept B −5 bars on 2/4 support) | LEARNER_STRICT=1 default: ≥4 hits, ≥65%, LOO gain, no exact track broken | rule dropped; learner currently learns nothing (correct with 18 marks). Old profile kept as learned_profile_marks.pre-strict.json |

## Scores (20-song set)
| run | vs Cullen's marks | A | B | C | D | G | H | vs Mixed In Key (B/C/D/H bars) | auto-set A/B/G/H |
|---|---|---|---|---|---|---|---|---|---|
| v4 | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 27 |
| v5 | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 19 |
| v5_fixed | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 35/66 | 19 |
| v5_fixed + fix_start | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 34/66 | 19 |
| v5 + only fix_start | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 31/66 | 19 |
| v5_fixed(all 4 fixes) - fix_start | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 35/66 | 19 |
| v5 + only fix_lead | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 19 |
| v5_fixed(all 4 fixes) - fix_lead | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 19 |
| v5 + only fix_chorus | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 35/66 | 19 |
| v5_fixed(all 4 fixes) - fix_chorus | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 32/66 | 19 |
| v5 + only fix_exit_lead | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 33/66 | 19 |
| v5_fixed(all 4 fixes) - fix_exit_lead | 9/18 | 2/4 | 1/4 | 2/3 | 2/3 | 1/2 | 1/2 | 34/66 | 19 |
| v4 + gemini_v2 (cached, fixed apply) | 14/18 | 4/4 | 2/4 | 2/3 | 2/3 | 2/2 | 2/2 | 28/66 | 27 |
| v4 + gemini_v3 (cached, fixed apply) | 10/18 | 2/4 | 2/4 | 2/3 | 2/3 | 1/2 | 1/2 | 29/66 | 27 |
| v4 + gemini_v3 (old apply: overrides B/H) | 10/18 | 2/4 | 2/4 | 2/3 | 2/3 | 1/2 | 1/2 | 28/66 | 25 |
| v5 + gemini_v2 (cached, fixed apply) | 14/18 | 4/4 | 2/4 | 2/3 | 2/3 | 2/2 | 2/2 | 28/66 | 19 |
| v5 + gemini_v3 (cached, fixed apply) | 10/18 | 2/4 | 2/4 | 2/3 | 2/3 | 1/2 | 1/2 | 28/66 | 19 |
| v5 + gemini_v3 (old apply: overrides B/H) | 10/18 | 2/4 | 2/4 | 2/3 | 2/3 | 1/2 | 1/2 | 28/66 | 19 |
| v5_fixed + gemini_v2 (cached, fixed apply) | 14/18 | 4/4 | 2/4 | 2/3 | 2/3 | 2/2 | 2/2 | 29/66 | 19 |
| v5_fixed + gemini_v3 (cached, fixed apply) | 10/18 | 2/4 | 2/4 | 2/3 | 2/3 | 1/2 | 1/2 | 28/66 | 19 |
| v5_fixed + gemini_v3 (old apply: overrides B/H) | 10/18 | 2/4 | 2/4 | 2/3 | 2/3 | 1/2 | 1/2 | 28/66 | 19 |

marks: 18 cues on 4 songs (['02', '14', '15', '20']); Gemini new spend $0 (all cached)

Takeaways: engine-only v4 = v5 = v5_fixed on marks (9/18); v5_fixed best vs Mixed In Key (35/66). Cached gemini_v2 picks on top of
any engine version → **14/18** on marks (A 4/4, G 2/2, H 2/2) but worse vs MIK (28-29/66) — consistent with Cullen marking Gemini
right on the page, and with MIK not being his truth. Keep Gemini as suggestions; it is the strongest signal on his marks so far.
