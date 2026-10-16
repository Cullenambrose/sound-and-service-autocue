"""Auto-cue rules engine (Phase 1 proof). Encodes /workspace/dj-tutor/cue-logic.md + SPEC.md §3.
Reads only box-side analysis outputs and the read-only Serato tag dump. Writes JSON drafts to ../cues/<engine>/<nn>.json.
Never touches audio files. Times are in the DECODED timeline (ffmpeg/browser); time_serato = time - grid_offset."""
import json, os, glob, math, csv
import numpy as np
REAL = "/workspace/research/dj-auto-cues/proof/real"
STRUCT_ENGINES = ["songformer", "allin1"]
THRESH = {s: 0.45 for s in "ABCDEGH"}           # PROPOSED default; calibrate on Cullen's grades
COLORS = {"A": "#006838", "B": "#CCCC00", "C": "#0000CC", "D": "#8800CC", "E": "#CC0000", "F": "#CC8800", "G": "#CC00CC", "H": "#1FAD26"}
NAMES = {"A": "intro", "B": "start", "C": "last chorus", "D": "out", "E": "loop", "F": "scratch", "G": "first hook", "H": "go"}
CLASS = {"start": "edge", "end": "edge", "silence": "edge", "intro": "intro", "verse": "verse", "pre-chorus": "prechorus",
         "prechorus": "prechorus", "chorus": "chorus", "bridge": "bridge", "inst": "inst", "solo": "inst", "break": "inst", "outro": "outro"}

def clamp(x, a=0.0, b=1.0): return max(a, min(b, x))
def fmt(t): return f"{int(t // 60)}:{t % 60:05.2f}"

class Grid:
    """Serato BPM + bar phase, aligned to the decoded audio with Beat This! (fallback: Beat This alone)."""
    def __init__(self, nn, tags, bt, a1=None):
        self.flags = []; b = np.array(bt["beats"]); d = np.array(bt["downbeats"])
        sg = (tags.get(nn) or {}).get("serato_beatgrid")
        if sg and len(sg) == 1:
            p, bpm = sg[0]["pos"], sg[0]["bpm"]; self.source = "serato"
        else:
            if sg: self.flags.append("grid: multi-marker Serato grid not supported in proof, using Beat This")
            k = np.arange(len(b)); m, c = np.polyfit(k, b, 1); p, bpm = c, 60 / m; self.source = "beat_this"
            self.flags.append("grid:fallback")
        self.bpm = bpm; self.spb = 60.0 / bpm; self.bar = 4 * self.spb
        ib = np.median(np.diff(b)); ratio = self.spb / ib
        self.ratio = int(round(ratio)) if ratio > 1.6 else 1
        if self.ratio > 1: self.flags.append(f"tempo: Beat This hears ~{bpm*self.ratio:.0f} BPM, Serato grid is half-time {bpm:.1f}; bars counted on Serato's grid")
        sp = self.spb / self.ratio
        res = ((b - p) / sp - np.round((b - p) / sp)) * sp
        self.offset = float(np.median(res)); self.res_std = float(np.std(res - self.offset))
        self.fit = float(np.mean(np.abs(res - self.offset) < 0.05))
        if abs(self.offset) > 0.06: self.flags.append(f"grid: Serato grid sits {self.offset*1000:+.0f} ms from the detected beats (decoder offset or grid off); check grid")
        if self.fit < 0.8: self.flags.append(f"grid: only {self.fit:.0%} of detected beats sit on the constant grid (tempo drift / swing / breaks)")
        p2 = p + self.offset
        # bar phase vote: Beat This downbeats (+ allin1 downbeats when present)
        votes = np.zeros(4)
        for src in [d] + ([np.array(a1["downbeats"])] if a1 and a1.get("downbeats") else []):
            kd = np.round((src - p2) / self.spb).astype(int)
            ok = np.abs(src - (p2 + kd * self.spb)) < 0.08
            votes += np.bincount(kd[ok] % 4, minlength=4)[:4]
        self.votes = votes.astype(int).tolist()
        ph = int(np.argmax(votes)) if votes.sum() else 0
        if votes.sum() and votes[ph] / votes.sum() >= 0.6:
            self.phase = ph
            if ph: self.flags.append(f"grid: detected downbeats fall {ph} beat(s) after the Serato grid marker; using the detected One")
        else:
            self.phase = 0; self.flags.append("grid: bar phase unclear (downbeat votes split); using Serato marker as the One")
        self.t1 = p2 + self.phase * self.spb
        self.healthy = self.fit >= 0.8 and abs(self.offset) <= 0.06 and self.source == "serato"
    def bar_of(self, t): return (t - self.t1) / self.bar
    def tbar(self, j): return self.t1 + j * self.bar
    def beat_of(self, t): return (t - self.t1) / self.spb
    def tbeat(self, k): return self.t1 + k * self.spb

class Track:
    def __init__(self, nn, tags, manifest):
        self.nn = nn
        f = np.load(f"{REAL}/features/{nn}.npz"); self.fps = int(f["fps"]); self.dur = float(f["dur"])
        self.rms = f["rms"]; self.peak = f["peak"]
        self.db = 20 * np.log10(self.rms + 1e-6); self.ref = np.percentile(self.db, 90)
        loud = np.where(self.peak > 0.02)[0]
        self.t_on = loud[0] / self.fps if len(loud) else 0.0
        act = np.where(self.db > self.ref - 30)[0]; self.t_off = (act[-1] + 1) / self.fps if len(act) else self.dur
        self.bt = json.load(open(f"{REAL}/out/beat_this/{nn}.json"))
        self.a1 = self._load(f"{REAL}/out/allin1/{nn}.json")
        self.sf = self._load(f"{REAL}/out/songformer/{nn}.json")
        self.grid = Grid(nn, tags, self.bt, self.a1)
        self.stems = None
        sp = f"{REAL}/out/allin1/{nn}.stems_env.npz"
        if os.path.exists(sp):
            s = np.load(sp); self.stems = {k: s[k] for k in ("vocals", "drums", "bass", "other")}; self.stem_hop = float(s["hop_s"])
        self.acts = None
        ap = f"{REAL}/out/allin1/{nn}.activations.npz"
        if os.path.exists(ap): self.acts = dict(np.load(ap))
        self.meta = manifest.get(nn, {})
    def _load(self, p):
        return json.load(open(p)) if os.path.exists(p) else None
    def seg_energy(self, t0, t1):
        a, b = int(t0 * self.fps), max(int(t0 * self.fps) + 1, int(t1 * self.fps)); return float(np.mean(self.db[a:b]) - self.ref)
    def stem_frac(self, stem, t0, t1, rel=0.2):
        if self.stems is None: return None
        x = self.stems[stem]; a, b = int(t0 / self.stem_hop), max(int(t0 / self.stem_hop) + 1, int(t1 / self.stem_hop))
        ref = np.percentile(x, 95) + 1e-6
        return float(np.mean(x[a:b] > rel * ref))
    def vocal_onset_near(self, t, win):
        """first time in [t-win, t+win] where vocals become active and stay active for most of the next bar"""
        if self.stems is None: return None
        x = self.stems["vocals"]; h = self.stem_hop; ref = np.percentile(x, 95) + 1e-6
        on = x > 0.2 * ref; bar_n = int(self.grid.bar / h)
        a, b = max(0, int((t - win) / h)), min(len(x) - bar_n, int((t + win) / h))
        for i in range(a, b):
            if on[i] and not on[max(0, i - 1)] and on[i:i + bar_n].mean() > 0.6 and on[max(0, i - bar_n):i].mean() < 0.3:
                return i * h
        return None
    def boundary_prob(self, t):
        if not self.acts or "segment" not in self.acts: return None
        a = np.asarray(self.acts["segment"]).ravel(); i = int(round(t * 100))
        w = a[max(0, i - 25): i + 26]
        return float(w.max() / (a.max() + 1e-9)) if len(w) else None

def segments_for(tr, engine):
    raw = tr.a1["segments"] if engine == "allin1" else tr.sf
    g = tr.grid; segs = []; snap_err = {}
    for s in raw:
        c = CLASS.get(s["label"].lower(), "inst")
        j0, j1 = round(g.bar_of(s["start"])), round(g.bar_of(s["end"]))
        snap_err[j0] = abs(g.bar_of(s["start"]) - j0)
        if j1 <= j0: continue
        segs.append({"cls": c, "lab": s["label"], "j0": j0, "j1": j1, "t0": s["start"], "t1": s["end"], "parts": 1, "pieces": [(j0, j1)]})
    merged = []
    for s in segs:
        if merged and merged[-1]["cls"] == s["cls"] and merged[-1]["j1"] >= s["j0"]:
            merged[-1]["j1"] = s["j1"]; merged[-1]["t1"] = s["t1"]; merged[-1]["parts"] += 1; merged[-1]["pieces"].append((s["j0"], s["j1"]))
        else: merged.append(dict(s))
    return merged, snap_err

def make_cue(slot, status, **kw):
    c = {"slot": slot, "color": COLORS[slot], "status": status, "label": None, "time": None, "time_serato": None,
         "bar": None, "confidence": None, "reason": "", "flags": []}
    c.update(kw); return c

def draft(tr, engine):
    g = tr.grid; flags = list(g.flags)
    segs, snap_err = segments_for(tr, engine)
    if not segs: return None
    base = 0.6 + (0.1 if g.healthy else -0.15 if g.fit < 0.8 else -0.05)
    if g.ratio > 1: base -= 0.05
    def bconf(j):  # boundary-evidence term
        c = 0; e = snap_err.get(j)
        if e is not None: c -= 0.4 * e
        if engine == "allin1":
            p = tr.boundary_prob(g.tbar(j))
            if p is not None: c += 0.2 * (p - 0.5)
        return c
    seg_desc = " | ".join(f"{s['lab']} {s['j1']-s['j0']}b" for s in segs)
    cues = {}
    # ---- B ----
    content = [s for s in segs if s["cls"] not in ("edge", "intro")]
    intro = [s for s in segs if s["cls"] == "intro"]
    j_on = math.ceil(g.bar_of(tr.t_on) - 0.1)
    if not content:
        cues["B"] = make_cue("B", "empty", reason="engine found no section after the intro", flags=["needs review: no song-proper section"])
    else:
        s = content[0]; jB = s["j0"]; why = f"{engine} marks '{s['lab']}' starting here"
        c = base + bconf(jB); fl = []
        if not intro or jB <= j_on:
            why += " (no intro: hot start)"; jB = max(jB, j_on)
        vo = tr.vocal_onset_near(g.tbar(jB), g.bar * 1.25)
        if vo is not None:
            jv = math.floor(g.bar_of(vo) + 0.25)          # vocal pickup up to 1 beat early counts for the next One
            if jv == jB: c += 0.1; why += "; vocal enters on this bar"
            elif abs(jv - jB) <= 1: why += f"; moved {jv-jB:+d} bar to where the vocal enters"; jB = jv; c += 0.03; fl.append("B refined by vocal onset")
        elif tr.stems is not None: why += "; no clear vocal entry nearby"
        else: c -= 0.05; fl.append("vocal check unavailable (allin1 stems missing)")
        cues["B"] = make_cue("B", "set", label="start", bar=jB, confidence=c, reason=why, flags=fl)
    B = cues["B"]
    jB = B["bar"]
    # ---- A ----
    if B["status"] != "set":
        cues["A"] = make_cue("A", "empty", reason="depends on B", flags=["needs review: B missing"])
    else:
        L = jB - j_on
        if L >= 16:
            cues["A"] = make_cue("A", "set", label="16", bar=jB - 16, confidence=B["confidence"] - 0.02,
                                 reason=f"intro is {L} bars, so A goes exactly 16 bars before B" + (" (DJ edit long intro, no extra cue)" if L >= 32 else ""))
        elif L <= 0:
            cues["A"] = make_cue("A", "empty", reason="track starts on the song proper (no intro bars before B)", flags=["needs review: no intro"])
        else:
            beats_before = (j_on - g.bar_of(tr.t_on)) * 4
            fl = []; c = B["confidence"] - 0.02
            if 0.75 <= beats_before <= 3.25:
                nb = int(round(beats_before)); beatno = 4 - nb + 1
                cues["A"] = make_cue("A", "set", label=f"the {beatno}", beat=g.beat_of(g.tbar(j_on)) - nb, confidence=c - 0.1,
                                     reason=f"music starts {nb} beat(s) before the first full bar: pickup on beat {beatno}; intro then runs {L} bars to B",
                                     flags=["pickup intro"])
            else:
                if L not in (1, 2, 4, 8): fl.append(f"non-standard intro length {L}"); c -= 0.12
                cues["A"] = make_cue("A", "set", label=str(L), bar=j_on, confidence=c, reason=f"intro starts on the first downbeat and runs {L} bars to B", flags=fl)
    # ---- chorus blocks ----
    blocks = [s for s in segs if s["cls"] == "chorus" and (jB is None or s["j1"] > jB)]
    drop_fallback = False
    if not blocks and jB is not None:
        inst = [s for s in segs if s["cls"] == "inst" and s["j0"] >= jB and s["j1"] - s["j0"] >= 4]
        if inst:
            en = [tr.seg_energy(g.tbar(s["j0"]), g.tbar(s["j1"])) for s in segs if s["cls"] != "edge"]
            hi = np.percentile(en, 75)
            blocks = [s for s in inst if tr.seg_energy(g.tbar(s["j0"]), g.tbar(s["j1"])) >= hi - 0.5]
            drop_fallback = bool(blocks)
    split_note = None
    if blocks and blocks[-1]["j1"] - blocks[-1]["j0"] > 16 and len(blocks[-1]["pieces"]) > 1:
        lb = blocks[-1]; pcs = lb["pieces"]; k = len(pcs) - 1
        while k > 0 and lb["j1"] - pcs[k - 1][0] <= 16: k -= 1
        tailb = dict(lb, j0=pcs[k][0], parts=len(pcs) - k, pieces=pcs[k:])
        split_note = f"{lb['j1']-lb['j0']}-bar run of back-to-back '{lb['lab']}' sections: last chorus taken as the final {tailb['j1']-tailb['j0']} bars (interpretation)"
        if len(blocks) == 1:
            headb = dict(lb, j1=pcs[k][0], parts=k, pieces=pcs[:k]); blocks = [headb, tailb]
        else: blocks = blocks[:-1] + [tailb]
        flags.append(split_note)
    if drop_fallback: flags.append("no 'chorus' label: high-energy instrumental sections used as drops (flag)")
    def blk_flags(s):
        fl = []; n = s["j1"] - s["j0"]
        if (s["j0"] - jB) % 4 if jB is not None else False: fl.append("not on a 4-bar phrase line from B")
        return fl
    if not blocks:
        why = "no chorus/hook/drop found (edge case 5)"
        cues["G"] = make_cue("G", "empty", reason=why, flags=["no chorus"]); cues["C"] = make_cue("C", "empty", reason=why, flags=["no chorus"])
    else:
        first, last = blocks[0], blocks[-1]
        cG = base + bconf(first["j0"]) - (0.1 if drop_fallback else 0); flG = blk_flags(first)
        if flG: cG -= 0.1
        whyG = f"first {'drop' if drop_fallback else first['lab']} block starts here ({first['j1']-first['j0']} bars)"
        if jB is not None and first["j0"] == jB: whyG += "; same beat as B (straight into the hook/drop, edge case 4)"
        cues["G"] = make_cue("G", "set", label="hook", bar=first["j0"], confidence=cG, reason=whyG,
                             flags=flG + ["lyric label is a suggestion only: 'hook' written"])
        n = last["j1"] - last["j0"]; flC = blk_flags(last); cC = base + bconf(last["j0"]) - (0.1 if drop_fallback else 0)
        if flC: cC -= 0.1
        if n not in (8, 16): flC.append(f"non-standard last chorus length {n} bars"); cC -= 0.1
        whyC = f"last {'drop' if drop_fallback else last['lab']} block starts here, {n} bars long"
        if last["parts"] > 1: whyC += f" (double chorus: {last['parts']} parts merged, edge case 6)"
        if len(blocks) == 1: flC.append("single chorus: C and G on the same beat"); cC -= 0.05
        if split_note: flC.append("long chorus run split"); cC -= 0.1
        cues["C"] = make_cue("C", "set", label=f"last {n}", bar=last["j0"], confidence=cC, reason=whyC, flags=flC)
    # ---- D ----
    j_end_music = g.bar_of(tr.t_off)
    if blocks:
        last = blocks[-1]; jD = last["j1"]; tail = j_end_music - jD
        nxt = [s for s in segs if s["j0"] >= jD and s["cls"] != "edge"]
        cD = base + bconf(jD); fl = []; lab = "out"
        why = f"first beat after the last {'drop' if drop_fallback else 'chorus'} block"
        if nxt:
            why += f" ({engine}: '{nxt[0]['lab']}' starts here)"
            if nxt[0]["cls"] == "verse": why += "; skips the final verse (edge case 3)"
            if nxt[0]["cls"] == "outro" and nxt[0]["j1"] - nxt[0]["j0"] >= 8:
                e_o = tr.seg_energy(g.tbar(nxt[0]["j0"]), g.tbar(nxt[0]["j1"])); e_c = tr.seg_energy(g.tbar(last["j0"]), g.tbar(last["j1"]))
                if e_o >= e_c - 1.5: fl.append("outro is as loud as the chorus: may be another chorus (C/D could be later)"); cD -= 0.1
        if tail < 2:
            end = ending_type(tr)
            if end["type"] == "cold":
                cues["D"] = make_cue("D", "set", label="cold", beat=end["beat"], confidence=base - 0.05,
                                     reason=f"last chorus runs to the end and the music stops cold at {fmt(g.tbeat(end['beat']))}", flags=["cold ending"])
            elif end["type"] == "fade":
                jf = end["bar"]
                if jB is not None: jf = jB + 4 * math.floor((jf - jB) / 4)
                cues["D"] = make_cue("D", "set", label="out", bar=jf, confidence=base - 0.1,
                                     reason="last chorus fades out; D on the phrase line where the fade begins", flags=["fade-out ending"])
            else:
                cues["D"] = make_cue("D", "set", label="out", bar=jD, confidence=cD - 0.1, reason=why + "; ending type unclear", flags=fl + ["ending type unclear"])
        else:
            cues["D"] = make_cue("D", "set", label="out", bar=jD, confidence=cD, reason=why, flags=fl)
    else:
        outro = [s for s in segs if s["cls"] == "outro"]
        if outro:
            cues["D"] = make_cue("D", "set", label="out", bar=outro[-1]["j0"], confidence=base - 0.05 + bconf(outro[-1]["j0"]),
                                 reason="no chorus: D at the start of the outro (edge case 5)", flags=["no chorus"])
        elif jB is not None:
            jd = jB + 4 * math.floor((j_end_music - 4 - jB) / 4)
            cues["D"] = make_cue("D", "set", label="out", bar=jd, confidence=base - 0.2,
                                 reason="no chorus and no outro: last clean phrase line (edge case 5)", flags=["no chorus", "no outro"])
        else:
            cues["D"] = make_cue("D", "empty", reason="no structure to place D", flags=["needs review"])
    # ---- H ----
    D = cues["D"]; G = cues.get("G")
    if D["status"] != "set" or D.get("bar") is None and D.get("beat") is None:
        cues["H"] = make_cue("H", "empty", reason="depends on D", flags=["needs review: D missing"])
    else:
        jD = D["bar"] if D.get("bar") is not None else math.floor(D["beat"] / 4)
        anchor = G["bar"] if G and G["status"] == "set" else jB
        if jD - 16 >= (anchor if anchor is not None else -1e9):
            cues["H"] = make_cue("H", "set", label="go", bar=jD - 16, confidence=D["confidence"] - 0.02, reason="exactly 16 bars before D")
            if cues.get("C", {}).get("bar") == jD - 16: cues["H"]["reason"] += " (same beat as C: last chorus is 16 bars, edge case 1)"
        else:
            N = next((n for n in (12, 8, 4) if anchor is None or jD - n >= anchor), None)
            if N:
                cues["H"] = make_cue("H", "set", label=f"go {N}", bar=jD - N, confidence=D["confidence"] - 0.08,
                                     reason=f"16 bars before D falls before {'Pink' if G and G['status']=='set' else 'B'}; furthest phrase line after it is {N} bars before D (edge case 7)", flags=["short: go N"])
            else:
                cues["H"] = make_cue("H", "empty", reason="fewer than 4 bars between Pink and D", flags=["needs review: too short for H"])
    # ---- E ----
    cues["E"] = place_loop(tr, cues, base, j_end_music)
    cues["F"] = make_cue("F", "empty", reason="F is never auto-set (scratch sample is yours)", flags=[])
    # finalize times
    for c in cues.values():
        if c.get("bar") is not None: c["time"] = g.tbar(c["bar"]); c["beat"] = c["bar"] * 4
        elif c.get("beat") is not None: c["time"] = g.tbeat(c["beat"])
        if c["time"] is not None:
            c["time"] = round(float(c["time"]), 3); c["time_serato"] = round(c["time"] - g.offset, 3)
            if jB is not None and c.get("bar") is not None: c["bar_from_B"] = c["bar"] - jB
        if c["confidence"] is not None: c["confidence"] = round(clamp(c["confidence"]), 2)
    return {"track": tr.nn, "engine": engine, "segments": [{k: s[k] for k in ("lab", "cls", "j0", "j1", "parts")} for s in segs],
            "segments_desc": seg_desc, "cues": cues, "track_flags": flags, "drop_fallback": drop_fallback}

def ending_type(tr):
    g = tr.grid; fps = tr.fps; db = tr.db - tr.ref
    i_off = int(tr.t_off * fps)
    pre = db[max(0, i_off - int(0.5 * fps)): i_off]
    post = db[i_off: i_off + int(1.0 * fps)]
    if len(pre) and np.mean(pre) > -18 and (len(post) == 0 or np.mean(post) < -35 or i_off >= len(db) - 2):
        # last strong hit within the final 2 bars
        a = max(1, i_off - int(2 * g.bar * fps)); d = np.diff(db[a - 1:i_off])
        hits = np.where(d > 6)[0]
        t_hit = (a + hits[-1]) / fps if len(hits) else tr.t_off - 0.25
        return {"type": "cold", "beat": round(g.beat_of(t_hit))}
    # fade: bar energies over the last 16 bars steadily falling by >= 8 dB
    jend = math.floor(g.bar_of(tr.t_off)); bars = list(range(max(0, jend - 16), jend))
    e = [np.mean(db[int(g.tbar(j) * fps): int(g.tbar(j + 1) * fps)]) for j in bars if g.tbar(j) >= 0]
    if len(e) >= 6:
        e = np.array(e); top = int(np.argmax(e[:-3])) if len(e) > 3 else 0
        tail = e[top:]
        if e[top] - e[-1] >= 8 and np.all(np.diff(tail) < 1.5):
            return {"type": "fade", "bar": bars[0] + top}
    return {"type": "unclear"}

def place_loop(tr, cues, base, j_end_music):
    A, D = cues.get("A"), cues.get("D"); B = cues.get("B")
    cands = []
    if A and A["status"] == "set" and A.get("bar") is not None and B and B["status"] == "set":
        cands.append(("intro", A["bar"], B["bar"] - A["bar"]))
    if D and D["status"] == "set" and D["label"] != "cold" and D.get("bar") is not None:
        cands.append(("outro", D["bar"], int(math.floor(j_end_music - D["bar"]))))
    g = tr.grid; tried = []
    for where, j0, room in cands:
        for n in (8, 4):
            if room < n: continue
            t0, t1 = g.tbar(j0), g.tbar(j0 + n)
            voc = tr.stem_frac("vocals", t0, t1); dr = tr.stem_frac("drums", t0, t1, rel=0.15)
            bars_e = [tr.seg_energy(g.tbar(j), g.tbar(j + 1)) for j in range(j0, j0 + n)]
            steady = (max(bars_e) - min(bars_e)) <= 6
            c = base - 0.05; why = f"{n}-bar loop from the start of the {where}"
            fl = ["saved loop: same start, %d bars" % n]
            if voc is None: c -= 0.1; fl.append("vocals unchecked (no stems)")
            elif voc > 0.25: tried.append(f"{where} {n}b has vocals"); continue
            else: why += ", no vocals detected"
            if dr is not None:
                if dr < 0.3: tried.append(f"{where} {n}b has no steady drums"); continue
                why += ", drums present"
            if not steady: tried.append(f"{where} {n}b energy not steady"); continue
            return make_cue("E", "set", label="loop", bar=j0, loop_bars=n, confidence=c, reason=why, flags=fl)
    return make_cue("E", "empty", reason="no clean 4/8-bar drums-only section at intro or outro" + (f" ({'; '.join(tried)})" if tried else ""),
                    flags=["needs review: no loop"])

def agree(c1, c2, g):
    if c1["status"] != "set" or c2["status"] != "set": return None
    return abs(c1["time"] - c2["time"]) <= g.bar * 1.01 + 1e-6

def main():
    tags = json.load(open(f"{REAL}/serato_tags.json"))
    manifest = {r["n"].zfill(2): r for r in csv.DictReader(open(f"{REAL}/MANIFEST.csv"))}
    out = {}
    for nn in sorted(tags):
        tr = Track(nn, tags, manifest)
        drafts = {}
        for e in STRUCT_ENGINES:
            if (e == "allin1" and tr.a1) or (e == "songformer" and tr.sf):
                d = draft(tr, e)
                if d: drafts[e] = d
        # cross-engine agreement -> confidence, then leave-empty-and-flag (edge case 9)
        if len(drafts) == 2:
            a, b = drafts["songformer"]["cues"], drafts["allin1"]["cues"]
            for s in "ABCDEGH":
                ag = agree(a[s], b[s], tr.grid)
                for x, y in ((a[s], b[s]), (b[s], a[s])):
                    if x["status"] != "set": continue
                    if ag is True:
                        x["confidence"] = round(clamp(x["confidence"] + 0.15), 2); x["reason"] += "; other engine agrees within 1 bar"
                        if x["label"] != y["label"]: x["flags"].append(f"other engine label: {y['label']}")
                    elif ag is False:
                        x["confidence"] = round(clamp(x["confidence"] - 0.15), 2); x["reason"] += f"; other engine puts it at {fmt(y['time'])}"
                    else:
                        x["confidence"] = round(clamp(x["confidence"] - 0.05), 2); x["reason"] += "; other engine left it empty"
        for e, d in drafts.items():
            cs = d["cues"]
            for s in "BDCGAHE":   # parents first
                c = cs[s]
                if c["status"] == "set" and c["confidence"] < THRESH[s]:
                    c["status"] = "withheld"; c["flags"].append(f"needs review: confidence {c['confidence']} < {THRESH[s]}")
                if s == "A" and cs["B"]["status"] != "set" and c["status"] == "set": c["status"] = "withheld"; c["flags"].append("parent B withheld")
                if s == "H" and cs["D"]["status"] != "set" and c["status"] == "set": c["status"] = "withheld"; c["flags"].append("parent D withheld")
                if s == "E" and c["status"] == "set":
                    src = "A" if c["reason"].find("intro") >= 0 else "D"
                    if cs[src]["status"] != "set": c["status"] = "withheld"; c["flags"].append(f"parent {src} withheld")
            for c in cs.values():
                if c["label"] and len(c["label"]) > 10: c["flags"].append("label over 10 characters")
            g = tr.grid
            d["grid"] = {"source": g.source, "bpm": round(g.bpm, 3), "offset_ms": round(g.offset * 1000, 1), "fit": round(g.fit, 2),
                         "phase": g.phase, "phase_votes": g.votes, "ratio": g.ratio, "t1": round(g.t1, 4), "bar_s": round(g.bar, 5)}
            os.makedirs(f"{REAL}/cues/{e}", exist_ok=True)
            json.dump(d, open(f"{REAL}/cues/{e}/{nn}.json", "w"), indent=1)
        out[nn] = drafts
        print(nn, {e: " ".join(f"{s}:{c['label'] if c['status']=='set' else ('~' if c['status']=='withheld' else '-')}@{fmt(c['time']) if c['time'] is not None else ''}" for s, c in sorted(d["cues"].items())) for e, d in drafts.items()})
    return out
if __name__ == "__main__": main()
