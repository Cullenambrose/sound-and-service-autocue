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
    {"name": "Barbangerz", "kind": "Record pool", "url": "https://barbangerz.com/",
     "search_note": "Official Bar Bangerz site verified. No public music-search field; copy the text and search after your normal sign-in."},
    {"name": "Club Killers", "kind": "Record pool", "url": "https://djtools.clubkillers.com/sign-in",
     "search_note": "Official store links to this DJ-tools sign-in. Copy the text and search after login; no search URL verified."},
    {"name": "Country DJ Pool", "kind": "Record pool", "url": "https://www.countrydjpool.com/",
     "search_note": "Official pool homepage verified. No public catalog search verified; copy the text and follow the site's access requirements."},
    {"name": "Crate Gang", "kind": "Record pool", "url": "https://www.crategang.com/",
     "search_note": "Official homepage offers sign-in/sign-up only. Copy the text and search in your existing account."},
    {"name": "Digital DJ Pool", "kind": "Record pool", "url": "https://digitaldjpool.com/",
     "search_note": "Official homepage verified; no public music-search field observed. Copy the text and search after your normal sign-in."},
    {"name": "Digital Music Pool", "kind": "Record pool", "url": "https://digitalmusicpool.com/",
     "search_note": "Public New Releases redirects to login. Copy the text and search after your normal sign-in; no query URL verified."},
    {"name": "Doing The Damage", "kind": "Record pool", "url": "https://doingthedamage.com/",
     "search_note": "Official professional-DJ application site verified. No public catalog search; copy the text and use your existing authorized access."},
    {"name": "Headliner Music Club", "kind": "Record pool", "url": "https://headlinermusicclub.com/",
     "search_note": "Official member login verified. Copy the text and search after your normal sign-in; no search URL verified."},
    {"name": "Heavy Hits", "kind": "Record pool", "url": "https://heavyhits.com/",
     "search_base": "https://heavyhits.com/search/?q=", "search_login": True,
     "search_note": "Normal search form generated this URL; direct reopening requires login. Search results and query restoration after login remain unverified."},
    {"name": "iDJPool", "kind": "Record pool", "url": "https://www.idjpool.com/",
     "search_note": "Professional-DJ qualification gate; not entered. Copy the text and follow the site's eligibility requirements before searching."},
    {"name": "Latino Music Pool", "kind": "Record pool", "url": "https://latinomusicpool.com/",
     "search_note": "Official homepage works, but its public search returned HTTP 500 during checking. Copy the text and try on the site; no working search link enabled."},
    {"name": "MP3 For DJs", "kind": "Record pool", "url": "https://www.mp3fordjs.com/",
     "search_note": "Site restricts eligibility to specified US service groups and directs other DJs to iDJPool. Copy text; no public search verified."},
    {"name": "MyMP3Pool", "kind": "Record pool", "url": "https://mp3poolonline.com/",
     "search_note": "Current official MyMP3Pool site verified; old mymp3pool.com did not respond. Copy the text and search after your normal sign-in."},
    {"name": "Radio DJ Pool", "kind": "Record pool", "url": "https://www.radiodjpool.com/",
     "search_note": "Site restricts access to eligible US radio DJs; other DJs are directed to iDJPool. Copy text; no public search verified."},
    {"name": "Urban DJ Pool", "kind": "Record pool", "url": "https://www.urbandjpool.com/",
     "search_note": "Official pool site and its iDJPool membership link verified. Copy text and follow provider access requirements; no search URL verified."},
    {"name": "XMiX", "kind": "Record pool", "url": "https://www.xmixdigital.com/",
     "search_base": "https://www.xmixdigital.com/DOWNLOADS?searchtext=", "search_login": True,
     "search_note": "Official XMiX site forwards here. Normal artist/title search generated this URL; reopening requires account registration/login. Results after login are unverified."},
    {"name": "XtendaMix", "kind": "Video pool", "url": "https://www.xtendamix.com/",
     "search_note": "Official video pool verified. Public search submit is disabled before sign-in; copy text and use your account. Music-video availability is not an audio-file match."},
    {"name": "ZIPDJ", "kind": "Record pool", "url": "https://www.zipdj.com/",
     "search_note": "Official product site exposes member login but no public music search. Copy the text and search in your existing account."},
    {"name": "Live DJ Service", "kind": "Record pool", "url": "https://livedjservice.com/login",
     "search_note": "Official destination redirects to member login. Copy the text and search after sign-in; no query URL verified."},
)


def provider_links(query):
    return [{
        "name": provider["name"], "kind": provider["kind"],
        "url": provider.get("search_base", "") + quote(query, safe="")
        if provider.get("search_base") else provider["url"],
        "homepage_url": provider["url"],
        "link_mode": ("search_login" if provider.get("search_login") else "search")
        if provider.get("search_base") else "homepage",
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
