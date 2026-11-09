"""Track matching for requested songs against library database."""
import re
import unicodedata
import json
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any
from difflib import SequenceMatcher

# Common DJ-pool version tags to normalize
VERSION_TAGS = [
    "Clean",
    "Dirty",
    "Explicit",
    "Intro",
    "Re-Drum",
    "Re Drum",
    "ReDrum",
    "Short Edit",
    "Chorus Intro",
    "Radio Edit",
    "Extended",
    "Extended Mix",
    "Club Mix",
    "Instrumental",
    "Acapella",
    "A Cappella",
    "Bonus Beat",
    "Clean Intro",
    "Dirty Intro",
]


@dataclass
class MatchConfig:
    """Configuration for matching preferences."""
    prefer_clean: bool = True
    prefer_intro: bool = True
    prefer_short_edit: bool = False
    min_confidence: float = 0.7
    fuzzy_threshold: float = 0.8
    

@dataclass
class TrackVersion:
    """Parsed track version information."""
    tags: List[str] = field(default_factory=list)
    is_clean: bool = False
    is_dirty: bool = False
    is_intro: bool = False
    is_short_edit: bool = False
    is_re_drum: bool = False
    is_chorus_intro: bool = False


@dataclass
class MatchResult:
    """Result of matching a requested song."""
    request: Dict[str, str]
    status: str  # "matched", "ambiguous", "missing"
    confidence: float = 0.0
    matched_track: Optional[Dict[str, Any]] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    reason: Optional[str] = None


@dataclass
class CratePlan:
    """Complete crate plan output."""
    matched: List[MatchResult]
    ambiguous: List[MatchResult]
    missing: List[MatchResult]
    do_not_play_found: List[Dict[str, Any]]
    ordered_track_ids: List[int]
    metadata: Dict[str, Any]


def normalize_text(text: str) -> str:
    """Normalize text for comparison: lowercase, remove punctuation, collapse whitespace."""
    if not text:
        return ""
    
    # Unicode normalize
    text = unicodedata.normalize("NFKD", text)
    
    # Lowercase
    text = text.lower()
    
    # Replace hyphens and punctuation with spaces
    text = re.sub(r"[^\w\s]", " ", text)
    
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    
    return text


def extract_featuring(text: str) -> Tuple[str, List[str]]:
    """
    Extract featuring artists from a title or artist field.
    Returns (main_text, [featured_artists])
    
    Examples:
        "Song ft. Artist" -> ("Song", ["Artist"])
        "Artist feat. Guest" -> ("Artist", ["Guest"])
        "Song (featuring X & Y)" -> ("Song", ["X", "Y"])
    """
    featuring_patterns = [
        r"\s+f(?:ea)?t\.?\s+(.+?)(?:\)|$)",
        r"\s+featuring\s+(.+?)(?:\)|$)",
        r"\(f(?:ea)?t\.?\s+(.+?)\)",
        r"\(featuring\s+(.+?)\)",
        r"\s+with\s+(.+?)(?:\)|$)",
    ]
    
    featured = []
    main_text = text
    
    for pattern in featuring_patterns:
        match = re.search(pattern, main_text, re.IGNORECASE)
        if match:
            featured_str = match.group(1)
            # Split by & or , or "and"
            featured = re.split(r"\s*[&,]\s*|\s+and\s+", featured_str, flags=re.IGNORECASE)
            featured = [f.strip() for f in featured if f.strip()]
            
            # Remove the featuring part from main text
            main_text = main_text[:match.start()] + main_text[match.end():]
            break
    
    return normalize_text(main_text), [normalize_text(f) for f in featured]


def extract_version_tags(text: str) -> Tuple[str, TrackVersion]:
    """
    Extract version tags from title/artist.
    Returns (text_without_tags, TrackVersion)
    """
    version = TrackVersion()
    clean_text = text
    
    # Sort tags by length (longest first) to match "Chorus Intro" before "Intro"
    sorted_tags = sorted(VERSION_TAGS, key=len, reverse=True)
    
    found_tags = []
    for tag in sorted_tags:
        # Look for tag in parentheses, brackets, or standalone
        patterns = [
            rf"\({re.escape(tag)}\)",
            rf"\[{re.escape(tag)}\]",
            rf"\b{re.escape(tag)}\b",
        ]
        
        for pattern in patterns:
            if re.search(pattern, clean_text, re.IGNORECASE):
                found_tags.append(tag.lower())
                clean_text = re.sub(pattern, "", clean_text, flags=re.IGNORECASE)
                break
    
    # Clean up the text
    clean_text = re.sub(r"\s*[(\[]\s*[)\]]\s*", " ", clean_text)  # Remove empty parens
    clean_text = re.sub(r"\s+", " ", clean_text).strip()
    
    version.tags = found_tags
    version.is_clean = any("clean" in t for t in found_tags)
    version.is_dirty = any("dirty" in t or "explicit" in t for t in found_tags)
    version.is_intro = any("intro" in t for t in found_tags) and "chorus intro" not in " ".join(found_tags)
    version.is_short_edit = any("short" in t and "edit" in t for t in found_tags)
    version.is_re_drum = any("drum" in t for t in found_tags)
    version.is_chorus_intro = any("chorus" in t and "intro" in t for t in found_tags)
    
    return clean_text, version


def compute_similarity(s1: str, s2: str) -> float:
    """Compute similarity score between two strings (0.0 to 1.0)."""
    if not s1 or not s2:
        return 0.0
    
    # Use SequenceMatcher for fuzzy matching
    return SequenceMatcher(None, s1, s2).ratio()


def _identity_fields(track: Dict[str, Any]) -> Tuple[str, str]:
    title, _ = extract_version_tags(track.get("title") or "")
    title, _ = extract_featuring(title)
    artist, _ = extract_featuring(track.get("artist") or "")
    return artist, normalize_text(title)


def _version_preference(track: Dict[str, Any], config: MatchConfig) -> float:
    _, version = extract_version_tags(
        (track.get("title") or "") + " " + str(track.get("version") or "")
    )
    return (
        10 * (config.prefer_clean and version.is_clean)
        + 8 * (config.prefer_intro and version.is_intro)
        + 5 * version.is_chorus_intro
        + 7 * (config.prefer_short_edit and version.is_short_edit)
        - 5 * (config.prefer_clean and version.is_dirty)
    )


def _file_tie_key(track: Dict[str, Any]) -> str:
    return json.dumps(
        {key: value for key, value in track.items() if not key.startswith("_")},
        sort_keys=True, ensure_ascii=False, default=str,
    )


def rank_candidates(
    candidates: List[Dict[str, Any]],
    request_artist: str,
    request_title: str,
    config: MatchConfig
) -> List[Tuple[Dict[str, Any], float]]:
    """
    Rank identities by similarity, using version preferences only within an identity.
    Returned scores are identity evidence only, never preference bonuses.
    """
    scored = []
    
    for track in candidates:
        score = 0.0
        
        # Base similarity score
        track_artist, track_title = _identity_fields(track)
        
        artist_sim = compute_similarity(request_artist, track_artist)
        title_sim = compute_similarity(request_title, track_title)
        
        # Weighted average (title more important)
        similarity = (title_sim * 0.6) + (artist_sim * 0.4)
        score += similarity * 100
        
        scored.append((track, score))
    
    # Sort by score descending
    scored.sort(key=lambda item: (
        -item[1], _identity_fields(item[0]),
        -_version_preference(item[0], config), _file_tie_key(item[0]),
    ))
    
    return scored


def match_track(
    request: Dict[str, str],
    library_tracks: List[Dict[str, Any]],
    config: MatchConfig
) -> MatchResult:
    """
    Match a single requested track against the library.
    
    Args:
        request: Dict with "artist" and "title" keys
        library_tracks: List of track dicts from database
        config: Matching configuration
        
    Returns:
        MatchResult with match status and candidates
    """
    request_artist = request.get("artist", "")
    request_title = request.get("title", "")
    
    # Normalize and extract features
    norm_artist, norm_title = _identity_fields({"artist": request_artist, "title": request_title})
    
    # Find candidates by fuzzy matching
    candidates = []
    
    for track in library_tracks:
        track_artist = normalize_text(track.get("artist") or "")
        track_title_raw = track.get("title") or ""
        track_title, track_version = extract_version_tags(track_title_raw)
        track_title = normalize_text(track_title)
        
        # Extract featuring from track
        track_artist_main, track_featured = extract_featuring(track_artist)
        track_title_main, track_title_featured = extract_featuring(track_title)
        
        # Compute similarity
        artist_sim = compute_similarity(norm_artist, track_artist_main)
        title_sim = compute_similarity(norm_title, track_title_main)
        
        # At least one must match well
        if artist_sim >= config.fuzzy_threshold or title_sim >= config.fuzzy_threshold:
            candidates.append({
                **track,
                "_artist_sim": artist_sim,
                "_title_sim": title_sim,
                "_version": track_version,
            })
        elif artist_sim >= 0.6 and title_sim >= 0.6:
            # Both are decent
            candidates.append({
                **track,
                "_artist_sim": artist_sim,
                "_title_sim": title_sim,
                "_version": track_version,
            })
    
    if not candidates:
        return MatchResult(
            request=request,
            status="missing",
            reason="No matching tracks found in library"
        )
    
    # Rank candidates
    ranked = rank_candidates(candidates, norm_artist, norm_title, config)
    
    best_track, best_score = ranked[0]
    best_identity = _identity_fields(best_track)
    
    # Compute confidence (normalize score to 0-1 range)
    confidence = min(best_score / 100, 1.0)
    
    # Check if ambiguous (multiple similar matches)
    is_ambiguous = False
    other_identity = next((item for item in ranked
                           if _identity_fields(item[0]) != best_identity), None)
    if other_identity is not None:
        second_score = other_identity[1]
        # If second best is within 5 points and both are high confidence, it's ambiguous
        if abs(best_score - second_score) < 5 and confidence >= config.min_confidence:
            is_ambiguous = True
    
    if confidence < config.min_confidence:
        return MatchResult(
            request=request,
            status="missing",
            confidence=confidence,
            candidates=[t for t, _ in ranked[:5]],
            reason=f"Best match confidence {confidence:.2f} below threshold {config.min_confidence}"
        )
    
    if is_ambiguous:
        return MatchResult(
            request=request,
            status="ambiguous",
            confidence=confidence,
            matched_track=best_track,
            candidates=[t for t, _ in ranked[:5]],
            reason="Multiple song identities have similar identity scores"
        )
    
    return MatchResult(
        request=request,
        status="matched",
        confidence=confidence,
        matched_track=best_track,
        candidates=[t for t, _ in ranked[:5]],
        reason=f"Song identity matched at confidence {confidence:.2f}; version preference applied within identity"
    )


def build_crate_plan(
    match_results: List[MatchResult],
    do_not_play_results: List[MatchResult],
    request_order: List[Dict[str, str]],
    crate_name: Optional[str] = None
) -> CratePlan:
    """
    Build a complete crate plan from match results.
    
    Args:
        match_results: Results from matching requested tracks
        do_not_play_results: Results from matching do-not-play list
        request_order: Original request order to preserve
        crate_name: Optional name for the crate
        
    Returns:
        CratePlan with ordered tracks and metadata
    """
    matched = [r for r in match_results if r.status == "matched"]
    ambiguous = [r for r in match_results if r.status == "ambiguous"]
    missing = [r for r in match_results if r.status == "missing"]
    
    # Extract do-not-play tracks that were found
    do_not_play_found = []
    do_not_play_ids = set()
    for dnp in do_not_play_results:
        if dnp.status in ("matched", "ambiguous") and dnp.matched_track:
            track_id = dnp.matched_track.get("id")
            if track_id:
                do_not_play_ids.add(track_id)
                do_not_play_found.append(dnp.matched_track)
    
    # Build ordered track list, excluding do-not-play
    # Include both matched and ambiguous (ambiguous still picks the best match)
    ordered_track_ids = []
    for req in request_order:
        # Find the match result for this request
        for result in matched + ambiguous:
            if (result.request.get("artist") == req.get("artist") and
                result.request.get("title") == req.get("title")):
                if result.matched_track:
                    track_id = result.matched_track.get("id")
                    # Exclude do-not-play tracks
                    if track_id and track_id not in do_not_play_ids:
                        ordered_track_ids.append(track_id)
                break
    
    metadata = {
        "crate_name": crate_name or "Generated Crate",
        "total_requested": len(match_results),
        "matched_count": len(matched),
        "ambiguous_count": len(ambiguous),
        "missing_count": len(missing),
        "do_not_play_found": len(do_not_play_found),
        "do_not_play_excluded": len([r for r in matched + ambiguous if r.matched_track and r.matched_track.get("id") in do_not_play_ids]),
    }
    
    return CratePlan(
        matched=matched,
        ambiguous=ambiguous,
        missing=missing,
        do_not_play_found=do_not_play_found,
        ordered_track_ids=ordered_track_ids,
        metadata=metadata
    )
