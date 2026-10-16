"""LRCLIB lookup by artist + clean title (text only, no audio leaves the box)."""
import csv, json, re, os, urllib.request, urllib.parse
R = "/workspace/research/dj-auto-cues/proof/real"
Q = {"01": ("Lady Gaga", "Bad Romance"), "02": ("Haddaway", "What Is Love"), "03": ("Taylor Swift", "The Fate of Ophelia"),
     "04": ("Third Eye Blind", "Jumper"), "05": ("Bad Bunny", "Dakiti"), "06": ("YG", "Paranoid"), "07": ("Smash Mouth", "Walkin' On The Sun"),
     "08": ("Village People", "Macho Man"), "09": ("Pretty Ricky", "On The Hotline"), "10": ("D4L", "Laffy Taffy"), "11": ("Saxsquatch", "Moondance"),
     "12": ("Shawn Mendes", "Senorita"), "13": ("Culture Beat", "Mr. Vain"), "14": ("Talking Heads", "Burning Down The House"),
     "15": ("Cupid", "Cupid Shuffle"), "16": ("Marvin Gaye", "Let's Get It On"), "17": ("Nelly", "Hot In Herre"),
     "18": ("Dustin Lynch", "Small Town Boy"), "19": ("Drake", "God's Plan"), "20": ("Martin Garrix", "In the Name of Love")}
for nn, (a, t) in Q.items():
    u = "https://lrclib.net/api/search?" + urllib.parse.urlencode({"artist_name": a, "track_name": t})
    try:
        r = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "sns-autocue-research/0.1"}), timeout=20))
    except Exception as e: r = []; print(nn, "ERR", e)
    best = next((x for x in r if x.get("plainLyrics")), None)
    json.dump(best, open(f"{R}/out/lrclib/{nn}.json", "w"))
    print(nn, a, t, "->", (best or {}).get("artistName"), (best or {}).get("trackName"), "lines", len((best or {}).get("plainLyrics") or "".splitlines() and (best or {})["plainLyrics"].splitlines()) if best else 0)
