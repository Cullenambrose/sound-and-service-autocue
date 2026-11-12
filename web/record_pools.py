"""Browser-only provider navigation; never queries catalogs or downloads audio."""
from urllib.parse import quote

SOURCE = "https://www.cratehackers.com/"
PROVIDERS = (
    {"name": "Direct Music Service", "kind": "Record pool", "url": "https://www.directmusicservice.com/",
     "search_note": "Qualification screen; search link not verified. Copy the text, open the site and search after your normal sign-in.",
     "reference_support": "Crate Hackers advertises connected downloads"},
    {"name": "Late Night Record Pool", "kind": "Record pool", "url": "https://www.latenightrecordpool.com/",
     "search_note": "Public search requires login; no reusable search URL verified. Copy the text and search on the site.",
     "reference_support": "Crate Hackers advertises connected downloads"},
    {"name": "BPM Supreme", "kind": "Record pool", "url": "https://bpmmusic.io/supreme",
     "search_note": "No public search URL verified. Copy the text and search in your normal provider account.",
     "reference_support": "Crate Hackers advertises search support"},
    {"name": "Beatport", "kind": "Store / streaming", "url": "https://www.beatport.com/",
     "search_base": "https://www.beatport.com/search?q=",
     "search_note": "Opens Beatport search. Check the exact version and acquisition rights on the provider site.",
     "reference_support": "Crate Hackers advertises search support"},
    {"name": "Beatsource (now Beatport)", "kind": "Store / streaming",
     "url": "https://www.beatport.com/open-format",
     "search_note": "Opens the migrated Open Format destination, not a verified scoped search. Copy the text and search on the site.",
     "reference_support": "Crate Hackers lists Beatsource; beatsource.com now announces migration to Beatport"},
    {"name": "DJcity", "kind": "Record pool", "url": "https://www.djcity.com/",
     "search_base": "https://www.djcity.com/search-overview?q=",
     "search_note": "Opens DJcity search. Availability and downloads may require your own subscription.",
     "reference_support": "Crate Hackers advertises search support"},
)


def provider_links(query):
    return [{
        "name": provider["name"], "kind": provider["kind"],
        "url": provider.get("search_base", "") + quote(query, safe="")
        if provider.get("search_base") else provider["url"],
        "homepage_url": provider["url"],
        "link_mode": "search" if provider.get("search_base") else "homepage",
        "note": provider["search_note"], "checked_on": "2026-11-12",
    } for provider in PROVIDERS]


def acquisition_rows(requests, progress=None):
    progress = progress or {}
    rows = [{
        "key": row["key"], "title": row["request"]["title"],
        "artist": row["request"].get("artist"), "version": row["request"].get("version"),
        "status": row["status"],
        "progress": progress.get(row["key"], "not_started"),
        "query": " ".join(str(value) for value in (
            row["request"].get("artist"), row["request"]["title"], row["request"].get("version")) if value),
    } for row in requests if row["needs_review"] and row["status"] in {"missing", "version_unavailable"}]
    return [{**row, "providers": provider_links(row["query"])} for row in rows]
