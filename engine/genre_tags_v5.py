"""Read the ID3 genre tag (Serato shows this) of each input MP3, read-only. Writes <root>/genre_tags.json {nn: genre}."""
import sys, glob, os, json, mutagen
R = sys.argv[1] if len(sys.argv) > 1 else "/workspace/research/dj-auto-cues/proof/real"; out = {}
for f in sorted(glob.glob(f"{R}/in/*")):
    nn = os.path.basename(f).split(" ")[0].split(".")[0]
    try: m = mutagen.File(f, easy=True); out[nn] = (m.get("genre") or [""])[0] if m else ""
    except Exception: out[nn] = ""
json.dump(out, open(sys.argv[2] if len(sys.argv) > 2 else f"{R}/genre_tags.json", "w"), indent=1); print(len(out), "tags")
