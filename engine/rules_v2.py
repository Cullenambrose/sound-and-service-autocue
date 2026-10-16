"""Auto-cue rules v2: Cullen's fixed layout (approved Oct 15, 2026). See ../../../cue-logic.md.
A = <N>BS (lead_in_beats before START)  B = START  C = 1st Chorus (suggest)  D = Last Chorus (suggest)
G = GO (lead_in_beats before EXIT)  H = EXIT   E, F always empty.
Post-processes the v1 engine drafts in ../cues/<engine>/<nn>.json (segments + grid + v1 cues) plus features/<nn>.npz.
Never touches audio or Serato files. Writes ../cues_v2/<engine>/<lead>/<nn>.json."""
import json, os, glob, math, sys
import numpy as np
import lyrics as LY
REAL = "/workspace/research/dj-auto-cues/proof/real"
PROFILE = {"a_rule": "8BS > 4BS > 2BS > 1BS, largest that fits", "exit_lead_beats": 32, "auto_set_min_conf": 0.85, "exit_drop_db": 2.5, "min_start_bar": 8, "pickup_rule_moves_start": False, "use_learned": False, "learned_loo": False}  # pickup rule moved START a bar late on 07/13/16 vs his cues; kept as confirm-only
ALLOWED_LEAD = (16, 32)
AUTO = set("ABGH"); SUGGEST = set("CD")
COLORS = {"A": "#006838", "B": "#CCCC00", "C": "#0000CC", "D": "#8800CC", "G": "#CC00CC", "H": "#CC0000"}

def bar_energy(nn, g):
    f = np.load(f"{REAL}/features/{nn}.npz"); fps = int(f["fps"]); db = 20 * np.log10(f["rms"] + 1e-6); ref = np.percentile(db, 90)
    def e(j):
        a, b = int((g["t1"] + j * g["bar_s"]) * fps), int((g["t1"] + (j + 1) * g["bar_s"]) * fps)
        return float(np.mean(db[max(0, a):max(1, b)]) - ref) if b > 0 else -60.0
    return e, float(f["dur"])

def pick_start(v1B, segs):
    """START = v1 B, with the no-intro fix: a hot start (B within 4 bars of the top) means the engine
    missed the intro (it labels the opening 'chorus'/'verse'); Cullen still starts at bar 8 -> use min_start_bar."""
    if v1B is None or v1B["status"] == "empty": return None, 0.0, "no START found"
    j = v1B["bar"]; c = v1B["confidence"] or 0
    if j is None: return None, 0.0, "no START found"
    if j <= PROFILE["min_start_bar"] - 4:
        return PROFILE["min_start_bar"], min(c, 0.7), f"engine put START at bar {j} (missed intro / 'no intro'); using bar {PROFILE['min_start_bar']} (DJ-edit intro), confidence capped"
    return j, c, f"engine section boundary at bar {j}" + (" (was withheld in v1)" if v1B["status"] == "withheld" else "")

def pick_exit(v1D, e, jB):
    """EXIT: v1 put it on the first bar after the last chorus block. Cullen's EXIT is the bar where the
    energy actually drops. Look at bars D-1..D+2 and take the first bar that is >= exit_drop_db quieter than the bar before."""
    if v1D is None or v1D["status"] == "empty" or v1D.get("bar") is None: return None, 0.0, "no EXIT found"
    j = v1D["bar"]; c = v1D["confidence"] or 0
    dj, dn = e(j - 1) - e(j), e(j) - e(j + 1)
    if dj >= PROFILE["exit_drop_db"]: return j, c + 0.05, f"last chorus ends at bar {j}; energy drops {dj:.1f} dB right there"
    if dn >= PROFILE["exit_drop_db"]: return j + 1, c, f"section boundary at bar {j} but the chorus tail rings one more bar; energy drops {dn:.1f} dB on bar {j+1}"
    return j, c - 0.1, f"section ends at bar {j}; no clear energy drop nearby"

def chorus_starts(segs):
    return [s["j0"] for s in segs if s["cls"] == "chorus"]

def cue(slot, bar, conf, why, flags=()):
    return {"slot": slot, "bar": bar, "confidence": round(max(0, min(1, conf)), 2), "reason": why, "flags": list(flags), "color": COLORS.get(slot)}

def build(d, nn, lead, exit_lead=None):
    exit_lead = exit_lead or PROFILE["exit_lead_beats"]; assert exit_lead in ALLOWED_LEAD; xb = exit_lead // 4
    g = d["grid"]; segs = d["segments"]; v1 = d["cues"]; e, dur = bar_energy(nn, g)
    lb = lead // 4; out = {}; tflags = []
    jB, cB, wB = pick_start(v1.get("B"), segs)
    jH, cH, wH = pick_exit(v1.get("D"), e, jB)
    lw = LY.load(nn) if PROFILE.get("use_lyrics", True) else None
    lyr = {}
    if lw:
        t0 = LY.first_sung(lw)
        if t0 is not None:
            jL = LY.pickup_bar(t0, g); lyr["first_word_s"] = round(t0, 2)
            jF = math.floor((t0 - g["t1"]) / g["bar_s"])   # bar that contains the first word
            if jB is not None and PROFILE.get("pickup_rule_moves_start") and abs(jL - jB) <= 1:
                cB = max(cB, 0.8) + 0.1; wB += f"; first sung word at {t0:.1f}s -> bar {jL} (pickup rule), agrees"; jB = jL
            elif jB is not None and jB in (jF, jL):
                cB = max(cB, 0.8) + 0.1; wB += f"; first sung word at {t0:.1f}s lands in/just before bar {jB}, confirms"
            else: wB += f"; first sung word at {t0:.1f}s -> bar {jL} disagrees (transcript may have missed early words), kept engine"; cB -= 0.05; lyr["start_disagree"] = jL
        if jH is not None:
            vd = LY.vocal_drop_bar(lw, g, jH)
            if vd is not None and vd == jH: wH += f"; vocals drop out on this bar too"; cH += 0.1
            elif vd is not None: wH += f"; vocal drop at bar {vd} disagrees"; cH -= 0.1
            else: wH += "; no clear vocal drop"
    out["B"] = cue("B", jB, cB, wB)
    out["H"] = cue("H", jH, cH, wH)
    if jB is not None:
        # A rule (Cullen, Oct 15 10:13): 8 bars before vocal START ("8BS"); if that is before the track / first downbeat, 4 bars ("4BS"); else empty
        first_db = math.ceil((0 - g["t1"]) / g["bar_s"] - 1e-6)   # first full bar inside the track
        for nbars in (8, 4, 2, 1):
            jA = jB - nbars
            if jA >= first_db and jA >= 0 - 0: break
        else: jA = None
        if jA is None: out["A"] = cue("A", None, 0, "not even 1 bar fits before START", ["needs review: no room for A"])
        else:
            out["A"] = cue("A", jA, cB - 0.02, f"{jB-jA} bars before START"); out["A"]["bars_before"] = jB - jA
    if jH is not None:
        jG = jH - xb; fl = []
        if jB is not None and jG < jB: fl.append(f"GO ({exit_lead} beats before EXIT) falls before START")
        out["G"] = cue("G", jG, cH - 0.02 - (0.3 if fl else 0), f"{exit_lead} beats before EXIT", fl)
    cs = chorus_starts(segs)
    ch = LY.chorus_bars(lw, g, jB, jH) if lw else None
    if ch and ch["starts"]:
        lyr["hook"] = ch["hook"]; lyr["hook_lrclib"] = ch["lrclib"]; lyr["hook_starts"] = ch["starts"]
        if not [j for j in cs if jB is not None and j >= jB + 4] and ch["lrclib"] and ch["n_occ"] >= 3: cs = ch["starts"]  # only when sections gave nothing
    # 1st chorus must start AFTER START (fix: v1 put it on the START beat when the first section was labeled chorus)
    after = [j for j in cs if jB is not None and j >= jB + 4 and (jH is None or j < jH)]
    if after:
        out["C"] = cue("C", after[0], 0.5, "first chorus section that starts after START")
        out["D"] = cue("D", after[-1], 0.5, "last chorus section before EXIT", ["single chorus"] if len(after) == 1 else [])
    else:
        out["C"] = cue("C", None, 0, "no chorus starts after START (engine merged it into one long block)")
        out["D"] = cue("D", None, 0, "no chorus starts after START")
    for s, c in out.items():
        c["time"] = None if c["bar"] is None else round(g["t1"] + c["bar"] * g["bar_s"] - g["offset_ms"] / 1000, 3)  # Serato timeline
        c["name"] = {"A": f"{c.get('bars_before', '')}BS", "B": "START", "C": "1st Chorus", "D": "Last Chorus", "G": "GO", "H": "EXIT"}[s]
        c["downbeat"] = c["bar"] is not None  # bar-snapped on the Serato grid
        if c["time"] is not None and c["time"] < 0: c["flags"].append("before track start")
        if c["bar"] is None: c["status"] = "empty"
        elif s in SUGGEST: c["status"] = "suggest"
        elif c["confidence"] >= PROFILE["auto_set_min_conf"] and not c["flags"]: c["status"] = "set"
        else: c["status"] = "review"
    # learned profile (tools/learner.py): per-cue bar offsets by song group; applied only when the learner kept a rule
    lp = f"{REAL}/learned_profile.json"
    if PROFILE.get("use_learned") and os.path.exists(lp):
        L_ = json.load(open(lp)); cx = L_["song_context"].get(nn, {})
        if PROFILE.get("learned_loo"): offs = json.load(open(f"{REAL}/learn_loo.json")).get(nn, {})
        else: offs = {k: (r["table"].get(cx.get(r["key"]) if r["key"] else "*", {}) or {}).get("offset_bars", 0) for k, r in L_["rules"].items()}
        for k, o in offs.items():
            if o and out.get(k, {}).get("bar") is not None:
                out[k]["bar"] += o; out[k]["time"] = round(out[k]["time"] + o * g["bar_s"], 3); out[k]["reason"] += f"; learned offset {o:+d} bars"
                dep = {"B": "A", "H": "G"}.get(k)
                if dep and out.get(dep, {}).get("bar") is not None: out[dep]["bar"] += o; out[dep]["time"] = round(out[dep]["time"] + o * g["bar_s"], 3)
    loops = plan_loops(nn, g, out, dur)
    out["E"] = {"slot": "E", "status": "empty", "reason": "E stays empty (no loops)"}
    out["F"] = {"slot": "F", "status": "empty", "reason": "F stays empty (no scratch cues)"}
    return {"track": nn, "lyrics": lyr, "engine": d["engine"], "lead_in_beats": lead, "exit_lead_beats": exit_lead, "cues": out, "loops": loops}

def _stems(nn):
    p = f"{REAL}/out/allin1/{nn}.stems_env.npz"
    return dict(np.load(p)) if os.path.exists(p) else None

def clean_beat(st, g, j, n=4):
    """True/False/None: bars j..j+n have drums but (almost) no vocals"""
    if st is None: return None
    h = float(st["hop_s"]); a, b = int((g["t1"] + j * g["bar_s"]) / h), int((g["t1"] + (j + n) * g["bar_s"]) / h)
    if a < 0 or b > len(st["vocals"]) or b <= a: return False
    v = st["vocals"][a:b] > 0.2 * (np.percentile(st["vocals"], 95) + 1e-9); d = st["drums"][a:b] > 0.15 * (np.percentile(st["drums"], 95) + 1e-9)
    return bool(v.mean() < 0.15 and d.mean() > 0.4)

def plan_loops(nn, g, cues, dur, n=4):
    """Saved-loop SUGGESTIONS (never auto-set): 4-bar intro loop at A (or first clean beat bar before vocals),
    4-bar outro loop at GO (or first clean beat section after EXIT). Always on downbeats (bar-snapped)."""
    st = _stems(nn); last_bar = math.floor((dur - g["t1"]) / g["bar_s"]) - n
    first = math.ceil(-g["t1"] / g["bar_s"] - 1e-6)
    def mk(where, j, why, ok):
        return {"loop": where, "bar": j, "beats": 4 * n, "status": "suggest" if j is not None else "empty",
                "time": None if j is None else round(g["t1"] + j * g["bar_s"] - g["offset_ms"] / 1000, 3), "reason": why, "clean": ok}
    A, B, G, H = (cues.get(k, {}).get("bar") for k in "ABGH")
    intro = mk("intro", None, "no clean 4-bar beat section before START", None)
    if A is not None and B is not None and A + n <= B:
        ok = clean_beat(st, g, A, n)
        if ok is not False: intro = mk("intro", A, "4 bars from A" + ("" if ok else " (vocals unchecked)"), ok)
    if intro["bar"] is None and B is not None:
        for j in range(max(first, 0), B - n + 1):
            if clean_beat(st, g, j, n): intro = mk("intro", j, "first clean beat-only 4 bars before vocals", True); break
    outro = mk("outro", None, "no clean 4-bar beat section at GO or after EXIT", None)
    if G is not None and clean_beat(st, g, G, n): outro = mk("outro", G, "4 bars from GO, beats only", True)
    elif H is not None:
        for j in range(H, last_bar + 1):
            if clean_beat(st, g, j, n): outro = mk("outro", j, "first clean beat-only 4 bars after EXIT", True); break
    return [intro, outro]

def main(exit_lead=None, tag=None):
    exit_lead = exit_lead or PROFILE["exit_lead_beats"]
    for p in sorted(glob.glob(f"{REAL}/cues/*/*.json")):
        d = json.load(open(p)); nn = os.path.basename(p)[:2]
        r = build(d, nn, 16, exit_lead); od = f"{REAL}/cues_v2/{d['engine']}/{tag or exit_lead}"; os.makedirs(od, exist_ok=True)
        json.dump(r, open(f"{od}/{nn}.json", "w"), indent=1)
if __name__ == "__main__":
    import warnings; warnings.filterwarnings("ignore")
    PROFILE["use_lyrics"] = False; main(16, "nolyr_go16")
    PROFILE["use_lyrics"] = True; main(16, "go16"); main(32, "go32")   # go16 = scored vs his truth; go32 = Cullen's default profile
