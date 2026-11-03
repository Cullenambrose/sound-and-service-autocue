"""Lyric timing layer (box-only): WhisperX words on the Demucs vocal stem + LRCLIB plain lyrics (text only).
Gives: first sung word -> START (pickup rule), repeated hook line -> 1st/last chorus, vocal-energy drop -> EXIT check."""
import json, os, re, math
from collections import Counter, defaultdict
import numpy as np
from synced_lyrics import align_synced_lyrics

R = os.environ.get("AUTOCUE_ANALYSIS_ROOT", "/workspace/research/dj-auto-cues/proof/real")
norm = lambda s: re.sub(r"[^a-z0-9ñáéíóú' ]", "", s.lower()).strip()

def load(nn, root=None):
    root = root or R
    p = f"{root}/out/words/{nn}.json"
    if not os.path.exists(p): return None
    w = json.load(open(p)); lr = f"{root}/out/lrclib/{nn}.json"
    w["lrclib"] = (json.load(open(lr)) or {}).get("plainLyrics") if os.path.exists(lr) else None
    st = f"{root}/out/allin1/{nn}.stems_env.npz"
    w["stems"] = dict(np.load(st)) if os.path.exists(st) else None
    return w

def synced_chorus_alignment(nn, grid, root=None):
    root = root or R
    word_path = f"{root}/out/words/{nn}.json"
    lyric_path = f"{root}/out/lrclib_synced/{nn}.json"
    if not os.path.exists(word_path) or not os.path.exists(lyric_path):
        return {"status": "rejected", "reason": "cached word timings or synced lyrics are unavailable",
                "choruses": [], "matched_lines": 0}
    words = json.load(open(word_path)).get("words", [])
    lyrics = (json.load(open(lyric_path)) or {}).get("syncedLyrics")
    return align_synced_lyrics(lyrics, words, grid["bar_s"])

def first_sung(w, min_run=4, gap=3.0):
    """first word that starts a run of >= min_run words with gaps < gap s (skips lone tags/ad-libs)"""
    ws = [x for x in w["words"] if x.get("score", 1) > 0.2 and norm(x["word"])]
    for i in range(len(ws) - min_run + 1):
        run = ws[i:i + min_run]
        if all(run[k + 1]["start"] - run[k]["end"] < gap for k in range(min_run - 1)): return ws[i]["start"]
    return None

def pickup_bar(t, g):
    """Cullen/handover pickup rule: first word in the 2nd half of a bar -> phrase starts on the next downbeat"""
    x = (t + 0.0 - g["t1"]) / g["bar_s"]; j = math.floor(x)
    return j + 1 if x - j >= 0.5 else j

def lrclib_hooks(text, n=4):
    if not text: return set()
    lines = [norm(l) for l in text.splitlines() if norm(l)]
    c = Counter(lines); return {l for l, k in c.items() if k >= 2 and len(l.split()) >= 2}

def hook_times(w, n=4):
    """times where the most-repeated n-word phrase starts; LRCLIB repeated lines boost phrases they contain"""
    ws = [x for x in w["words"] if norm(x["word"])]
    toks = [norm(x["word"]) for x in ws]
    grams = defaultdict(list)
    for i in range(len(toks) - n + 1): grams[" ".join(toks[i:i + n])].append(ws[i]["start"])
    hooks = lrclib_hooks(w.get("lrclib"))
    def score(gm):
        ts = grams[gm]; k = len(ts); bonus = 2 if any(gm in h for h in hooks) else 1
        return k * bonus
    cand = [g for g in grams if len(grams[g]) >= 2]
    if not cand: return None, []
    best = max(cand, key=score); return best, sorted(grams[best])

def chorus_bars(w, g, jB, jH, phrase=4):
    gm, ts = hook_times(w)
    if not ts: return None
    bars = sorted({math.floor((t - g["t1"]) / g["bar_s"]) for t in ts})
    # cluster occurrences: a new chorus starts when > 8 bars after the previous occurrence
    starts = []
    for b in bars:
        if not starts or b - starts[-1][-1] > 8: starts.append([b])
        else: starts[-1].append(b)
    sb = [c[0] for c in starts]
    # snap each chorus start back to the phrase line from START (hook line often sits mid-chorus)
    if jB is not None: sb = [jB + phrase * math.floor((b - jB) / phrase) for b in sb]
    sb = [b for b in sb if (jB is None or b >= jB + 4) and (jH is None or b < jH)]
    return {"hook": gm, "starts": sb, "n_occ": len(ts), "lrclib": bool(w.get("lrclib"))}

def vocal_drop_bar(w, g, near, win=2):
    """bar (within +-win of `near`) after which the vocal stem goes quiet for >= 2 bars"""
    s = w.get("stems")
    if not s: return None
    v = s["vocals"]; h = float(s["hop_s"]); ref = np.percentile(v, 95) + 1e-9
    frac = lambda j: float(np.mean(v[max(0, int((g["t1"] + j * g["bar_s"]) / h)):max(1, int((g["t1"] + (j + 1) * g["bar_s"]) / h))] > 0.2 * ref))
    for j in range(near - win, near + win + 1):
        if frac(j - 1) > 0.35 and frac(j) < 0.2 and frac(j + 1) < 0.2: return j
    return None
