"""Honest destination/release inventory and original, editable crate recipes."""
from . import studio_ai

TEMPLATES = [
    {"id": "dinner", "name": "Low-tempo dinner discovery", "folder": "Event preparation",
     "rule": {"bpm_max": 95, "clean_only": True, "limit": 40, "sort": "bpm"},
     "note": "Original rule, not a supplied music collection. Tagged clean is not verified audible content."},
    {"id": "rediscovery", "name": "Not observed in my play history", "folder": "Discovery",
     "rule": {"never_played": True, "limit": 60, "sort": "title"},
     "note": "No recorded play in this app is not evidence that a song has never been played."},
    {"id": "house", "name": "House warm-up candidates", "folder": "Event preparation",
     "rule": {"genre": "House", "bpm_min": 118, "bpm_max": 124, "limit": 40, "sort": "bpm"},
     "note": "Requires available genre and BPM metadata. Review versions and suitability."},
]

DESTINATIONS = [
    {"id": "m3u8", "name": "Verified local playlist ZIP", "state": "available",
     "note": "Existing byte-verified M3U8/CSV/manifest export; extract before importing. No audio is included."},
    {"id": "serato", "name": "Serato", "state": "manual-import",
     "note": "Use the extracted M3U8 and verify count/order/playback. Studio never writes Serato databases or tags."},
    {"id": "rekordbox", "name": "rekordbox", "state": "manual-import",
     "note": "M3U import workflow only; the repository native adapter is a stub. Actual software acceptance pending."},
    {"id": "virtualdj", "name": "VirtualDJ", "state": "manual-import",
     "note": "Portable playlist only; no native database writer or now-playing connection in Studio."},
    {"id": "spotify", "name": "Spotify playlists", "state": "unavailable",
     "note": "Requires approved API access and product-specific commercial/AI-policy review; no audio playback."},
    {"id": "apple", "name": "Apple Music playlists", "state": "unavailable",
     "note": "No authorized account/API adapter configured."},
    {"id": "tidal", "name": "Tidal playlists", "state": "unavailable",
     "note": "No authorized account/API adapter configured."},
    {"id": "connected-pools", "name": "Connected pool downloads", "state": "unavailable",
     "note": "No provider credentials, permitted download adapter or account agreement configured. Use browser links."},
]

RELEASE_GATES = [
    {"id": "real-software", "state": "pending", "requirement": "Disposable real-library and advertised DJ-software export/playback verification."},
    {"id": "musical-quality", "state": "pending", "requirement": "Independent listening, cue/transition and whole-set evaluation."},
    {"id": "signed-installers", "state": "pending", "requirement": "Signed/notarized macOS and separately validated Windows installers and signed update service."},
    {"id": "billing", "state": "pending", "requirement": "Deployed billing/entitlement lifecycle, offline licensing and business accounts."},
    {"id": "rights-privacy", "state": "pending", "requirement": "Dependency/model/content rights and privacy/terms review, including Mutagen GPL-2.0-or-later distribution/source obligations."},
    {"id": "independent-qa", "state": "pending", "requirement": "Independent usability/accessibility, clean-machine and platform certification."},
    {"id": "safe-cleanup", "state": "pending", "requirement": "Recoverable source cleanup and supported reference-repair round trips; Studio has no cleanup writer."},
    {"id": "providers", "state": "pending", "requirement": "Authorized providers/streaming connections and permitted test access."},
]


def report(model=None):
    return {"schema_version": 1, "product_state": "local-alpha", "commercial_ready": False,
            "destinations": DESTINATIONS, "templates": TEMPLATES, "release_gates": RELEASE_GATES,
            "ai": studio_ai.status(model),
            "boundaries": ["Only explicitly enabled root monitors; no model downloads", "No original-file/tag/Serato writes",
                           "Live is read-only and cannot rewrite saved Prep crates",
                           "Source-byte verification on approval/export, not on ordinary search",
                           "No paid requests, provider login, purchases or connected downloads"]}
