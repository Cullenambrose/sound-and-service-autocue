"""Build the self-contained review page: ../review/index.html (inline JS/CSS, embedded waveform peaks,
audio via relative paths to the box copies in ../in/). No network, no CDN."""
import json, os, glob, csv, base64, math, urllib.parse
import numpy as np
REAL = "/workspace/research/dj-auto-cues/proof/real"
ENG = ["songformer", "allin1"]
SLOTS = "ABCDEFGH"
PRIOR = {"songformer": 0.03, "allin1": 0.0}   # published: SongFormer better boundaries/labels (arXiv 2510.02797)

def score(d):
    v = []
    for s in "ABCDEGH":
        c = d["cues"][s]
        v.append(c["confidence"] if c["status"] == "set" else 0.35 if c["status"] == "withheld" else 0.45)
    return float(np.mean(v))

def disagreement(a, b, bar_s):
    out = {}
    for s in "ABCDEGH":
        x, y = a["cues"][s], b["cues"][s]
        xs, ys = x["status"] == "set", y["status"] == "set"
        if not xs and not ys: out[s] = "both_empty"
        elif xs != ys: out[s] = "one_empty"
        elif abs(x["time"] - y["time"]) > bar_s * 1.01: out[s] = "time"
        elif x["label"] != y["label"]: out[s] = "label"
        else: out[s] = "agree"
    return out

def b64(a): return base64.b64encode(a.astype(np.uint8).tobytes()).decode()

def main():
    manifest = {r["n"].zfill(2): r for r in csv.DictReader(open(f"{REAL}/MANIFEST.csv"))}
    tracks = []; stats = []
    for nn in sorted(manifest):
        m = manifest[nn]; fname = os.path.basename(m["box_path"])
        drafts = {e: json.load(open(p)) for e in ENG for p in [f"{REAL}/cues/{e}/{nn}.json"] if os.path.exists(p)}
        f = np.load(f"{REAL}/features/{nn}.npz"); fps = int(f["fps"])
        pk = f["peak"]; pk = np.clip(pk / (np.percentile(pk, 99.5) + 1e-9), 0, 1) * 255
        bands = np.stack([f["lo"], f["mi"], f["hi"]]) ** 0.5
        bands = bands / (bands.sum(0) + 1e-9) * 255
        t = {"nn": nn, "title": fname[5:-4], "src": "../in/" + urllib.parse.quote(fname), "genre": m.get("genre_note", ""),
             "dur": float(f["dur"]), "fps": fps, "peak": b64(pk), "lo": b64(bands[0]), "mi": b64(bands[1]), "hi": b64(bands[2]),
             "drafts": drafts, "default": None, "why_default": "", "disagree": None}
        if not drafts: t["why_default"] = "no engine output (failed)"
        elif len(drafts) == 1:
            t["default"] = list(drafts)[0]; t["why_default"] = f"only {t['default']} finished"
        else:
            sc = {e: score(drafts[e]) + PRIOR[e] for e in ENG}
            t["default"] = max(ENG, key=lambda e: sc[e])
            t["why_default"] = "higher mean cue confidence (+0.03 prior for SongFormer): " + ", ".join(f"{e} {sc[e]:.2f}" for e in ENG)
            g = drafts[ENG[0]]["grid"]
            t["disagree"] = disagreement(drafts["songformer"], drafts["allin1"], g["bar_s"])
        tracks.append(t)
    data = json.dumps({"tracks": tracks, "slots": list(SLOTS), "built": __import__("time").strftime("%Y-%m-%d %H:%M PT")}, separators=(",", ":"))
    html = open(f"{REAL}/tools/review_template.html").read().replace("/*__DATA__*/null", data)
    open(f"{REAL}/review/index.html", "w").write(html)
    json.dump([{"nn": t["nn"], "default": t["default"], "why": t["why_default"], "disagree": t["disagree"]} for t in tracks],
              open(f"{REAL}/review/summary.json", "w"), indent=1)
    print("wrote", f"{REAL}/review/index.html", os.path.getsize(f"{REAL}/review/index.html") // 1024, "KB")
if __name__ == "__main__": main()
