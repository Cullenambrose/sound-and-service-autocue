"""Verified provider destinations, not catalog/API or download integrations."""

SOURCE = "https://www.cratehackers.com/"
PROVIDERS = (
    {"name": "Direct Music Service", "kind": "Record pool", "url": "https://www.directmusicservice.com/",
     "reference_support": "Crate Hackers advertises connected downloads"},
    {"name": "Late Night Record Pool", "kind": "Record pool", "url": "https://www.latenightrecordpool.com/",
     "reference_support": "Crate Hackers advertises connected downloads"},
    {"name": "BPM Supreme", "kind": "Record pool", "url": "https://bpmmusic.io/supreme",
     "reference_support": "Crate Hackers advertises search support"},
    {"name": "Beatport", "kind": "Store / streaming", "url": "https://www.beatport.com/",
     "reference_support": "Crate Hackers advertises search support"},
    {"name": "Beatsource (now Beatport)", "kind": "Store / streaming",
     "url": "https://www.beatport.com/open-format",
     "reference_support": "Crate Hackers lists Beatsource; beatsource.com now announces migration to Beatport"},
    {"name": "DJcity", "kind": "Record pool", "url": "https://www.djcity.com/",
     "reference_support": "Crate Hackers advertises search support"},
)


def acquisition_rows(requests):
    return [{
        "key": row["key"], "title": row["request"]["title"],
        "artist": row["request"].get("artist"), "version": row["request"].get("version"),
        "status": row["status"],
        "query": " ".join(str(value) for value in (
            row["request"].get("artist"), row["request"]["title"], row["request"].get("version")) if value),
        "providers": PROVIDERS,
    } for row in requests if row["needs_review"] and row["status"] in {"missing", "version_unavailable"}]
