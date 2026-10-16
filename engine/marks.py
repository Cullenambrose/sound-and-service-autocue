"""Single shared parser for Cullen's review marks (Gemini round-2 claim 1). Reads every corrections/corrections*.json
(both cue-review folders), keeps per track+cue the LAST event that is 'correct' or 'set_here' with a to_beat; a later 'clear' removes it.
truth_beats() -> {(nn, cue): beat}; truth_bars(nn) -> {cue: bar} (beat/4 rounded), {} only when that song has no marks."""
import json, glob
R = "/workspace/research/dj-auto-cues/proof/real"
def events(paths=None):
    ev = []
    for f in sorted(paths or glob.glob(f"{R}/corrections/corrections*.json")): ev += json.load(open(f))
    return sorted(ev, key=lambda e: e.get("saved_at", ""))
def truth_beats(ev=None):
    last = {}
    for e in (events() if ev is None else ev):
        k = (e.get("track"), e.get("cue"))
        if k[1] not in list("ABCDGH"): continue
        if e.get("action") in ("correct", "set_here") and e.get("to_beat") is not None: last[k] = e["to_beat"]
        elif e.get("action") == "clear": last.pop(k, None)
    return last
def truth_bars(nn, ev=None, cues="ABCDGH"):
    return {L: round(b / 4) for (t, L), b in truth_beats(ev).items() if t == nn and L in cues}
def marked_songs(ev=None): return sorted({t for t, _ in truth_beats(ev)})
