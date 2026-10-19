"""Cache text-only LRCLIB synced lyric lookups using metadata from a CSV manifest."""
import argparse
import csv
import json
import os
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request


def _normalize(value):
    decomposed = unicodedata.normalize("NFKD", (value or "").casefold())
    return "".join(char for char in decomposed if char.isalnum())


def lookup(artist, title, timeout=20):
    query = urllib.parse.urlencode({"artist_name": artist, "track_name": title})
    request = urllib.request.Request(
        "https://lrclib.net/api/search?" + query,
        headers={"User-Agent": "sound-and-service-autocue/0.6"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        results = json.load(response)
    candidates = [result for result in results if result.get("syncedLyrics")]
    if not candidates:
        return None
    exact = [result for result in candidates
             if _normalize(result.get("artistName")) == _normalize(artist)
             and _normalize(result.get("trackName")) == _normalize(title)]
    return exact[0] if exact else candidates[0]


def cache_manifest(manifest_path, cache_dir, id_column="id", artist_column="artist", title_column="title"):
    os.makedirs(cache_dir, exist_ok=True)
    counts = {"synced": 0, "missing": 0, "errors": 0, "cached": 0}
    with open(manifest_path, newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        required = {id_column, artist_column, title_column}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError("manifest is missing required columns: " + ", ".join(sorted(missing)))
        for row in reader:
            track_id = row[id_column].strip()
            if not track_id:
                raise ValueError("manifest contains a row with an empty track ID")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", track_id):
                raise ValueError(f"manifest track ID is not a safe cache filename: {track_id!r}")
            path = os.path.join(cache_dir, track_id + ".json")
            if os.path.exists(path):
                record = json.load(open(path, encoding="utf-8"))
                counts["cached"] += 1
                counts["synced" if record.get("syncedLyrics") else "missing"] += 1
                continue
            record = {"track_id": track_id, "artist": row[artist_column], "title": row[title_column]}
            if not row[artist_column].strip() or not row[title_column].strip():
                raise ValueError(f"manifest row {track_id} has no artist/title for a lyrics lookup")
            try:
                result = lookup(row[artist_column], row[title_column])
                if result:
                    record.update(result)
                    counts["synced"] += 1
                else:
                    record["syncedLyrics"] = None
                    counts["missing"] += 1
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
                record["error"] = str(error)
                counts["errors"] += 1
            with open(path, "w", encoding="utf-8") as target:
                json.dump(record, target, ensure_ascii=False, indent=2)
            time.sleep(0.25)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--cache-dir", required=True, help="Local output directory; keep outside Git")
    parser.add_argument("--id-column", default="id")
    parser.add_argument("--artist-column", default="artist")
    parser.add_argument("--title-column", default="title")
    args = parser.parse_args()
    counts = cache_manifest(args.manifest, args.cache_dir, args.id_column, args.artist_column, args.title_column)
    print(json.dumps(counts, sort_keys=True))
    if counts["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
