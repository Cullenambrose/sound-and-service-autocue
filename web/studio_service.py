"""Grounded library queries, saved rules and approval-bound personal-crate proposals."""
from collections import OrderedDict
import copy
import json
import math
from pathlib import Path
import threading
import time
import uuid

from library.matcher import extract_version_tags

if __package__:
    from . import catalog, personal_crates
else:
    import catalog
    import personal_crates

MAX_INDEXED_FILES = 50000
RULE_FIELDS = {"q", "artist", "genre", "bpm_min", "bpm_max", "energy_min",
               "energy_max", "clean_only", "never_played", "scope_ids", "sort", "limit"}
SORTS = {"title", "bpm", "energy", "last_played", "preference"}
PROPOSAL_SECONDS = 1800
_INDEX_TABLES = ("library_files", "library_roots", "library_play_events", "library_play_removals",
                 "tracks", "studio_exclusions", "studio_feedback")
_SEARCH_SNAPSHOTS = OrderedDict()
_SEARCH_LOCK = threading.Lock()


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS studio_rules (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, folder TEXT NOT NULL,
            revision INTEGER NOT NULL, rule_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS studio_proposals (
            id TEXT PRIMARY KEY, document_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS studio_receipts (
            request_id TEXT PRIMARY KEY, body_json TEXT NOT NULL, receipt_json TEXT NOT NULL
        );
    """)
    initialize_search_cache(db)


def initialize_search_cache(db):
    """Track committed index evidence, including writes from other connections."""
    db.executescript(f"""CREATE TABLE IF NOT EXISTS studio_index_generation (
        id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT NOT NULL, revision INTEGER NOT NULL);
        INSERT OR IGNORE INTO studio_index_generation VALUES(1,'{uuid.uuid4()}',0);
    """)
    tables = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in _INDEX_TABLES:
        if table not in tables:
            continue
        for operation in ("INSERT", "UPDATE", "DELETE"):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS studio_index_{table}_{operation.lower()}
                AFTER {operation} ON {table} BEGIN
                    UPDATE studio_index_generation SET revision=revision+1 WHERE id=1;
                END""")


def _search_snapshot(db):
    if not db.in_transaction:
        db.execute("BEGIN")
    # Never publish provisional writes: a rollback can reuse their generation.
    if db.total_changes:
        return _prepare_search(db)
    objects = {(row["type"], row["name"]) for row in db.execute(
        "SELECT type,name FROM sqlite_master WHERE type IN ('table','trigger')")}
    if ("table", "studio_index_generation") not in objects:
        return _prepare_search(db)
    for table in _INDEX_TABLES:
        if ("table", table) in objects and any(
                ("trigger", f"studio_index_{table}_{operation}") not in objects
                for operation in ("insert", "update", "delete")):
            return _prepare_search(db)
    database = next(row["file"] for row in db.execute("PRAGMA database_list") if row["name"] == "main")
    if not database:
        return _prepare_search(db)
    generation = db.execute("SELECT identity,revision FROM studio_index_generation WHERE id=1").fetchone()
    if generation is None:
        raise ValueError("Missing Studio index generation; restart to initialize app state.")
    stat = Path(database).stat()
    key = (str(Path(database).resolve()), stat.st_dev, stat.st_ino,
           generation["identity"], generation["revision"], db.execute("PRAGMA schema_version").fetchone()[0])
    with _SEARCH_LOCK:
        cached = _SEARCH_SNAPSHOTS.get(key)
        if cached is not None:
            _SEARCH_SNAPSHOTS.move_to_end(key)
            return cached
    snapshot = _prepare_search(db)
    with _SEARCH_LOCK:
        _SEARCH_SNAPSHOTS[key] = snapshot
        _SEARCH_SNAPSHOTS.move_to_end(key)
        while len(_SEARCH_SNAPSHOTS) > 2:
            _SEARCH_SNAPSHOTS.popitem(last=False)
    return snapshot


def _prepare_search(db):
    tracks, revision = indexed_library(db)
    index = {}
    for track in tracks:
        title = track.get("title", "")
        artist = track.get("artist", "")
        _, version = extract_version_tags(str(track.get("version") or "") + " " + title)
        index[track["id"]] = (
            " ".join(str(track.get(key) or "") for key in ("title", "artist", "version", "genre")).casefold(),
            artist.casefold(), str(track.get("genre") or "").casefold(),
            version.is_clean and not version.is_dirty, (title.casefold(), artist.casefold()),
        )
    return tracks, revision, index


def exact_fields(body, required, optional=()):
    if not isinstance(body, dict) or set(body) - set(required) - set(optional) or set(required) - set(body):
        raise ValueError("Expected fields: " + ", ".join(sorted(required)) +
                         ("; optional: " + ", ".join(sorted(optional)) if optional else ""))


def validate_rule(rule):
    if not isinstance(rule, dict) or set(rule) - RULE_FIELDS:
        raise ValueError("Unknown library filter; use the documented typed rule fields.")
    normalized = {"q": "", "artist": "", "genre": "", "clean_only": False,
                  "never_played": False, "sort": "title", "limit": 100}
    normalized.update(rule)
    for field in ("q", "artist", "genre"):
        catalog.text(normalized[field], field)
    for field in ("clean_only", "never_played"):
        if type(normalized[field]) is not bool:
            raise ValueError(field + " must be a boolean.")
    if not isinstance(normalized["sort"], str) or normalized["sort"] not in SORTS:
        raise ValueError("sort must be title, bpm, energy, last_played or preference.")
    if type(normalized["limit"]) is not int or not 1 <= normalized["limit"] <= 1000:
        raise ValueError("limit must be an integer between 1 and 1000.")
    for stem in ("bpm", "energy"):
        for suffix in ("min", "max"):
            field = stem + "_" + suffix
            if field in normalized:
                value = normalized[field]
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError(field + " must be a finite nonnegative number.")
                if stem == "energy" and value > 1:
                    raise ValueError("Energy filters use the range 0..1.")
        if (stem + "_min" in normalized and stem + "_max" in normalized
                and normalized[stem + "_min"] > normalized[stem + "_max"]):
            raise ValueError(stem + "_min must not exceed " + stem + "_max.")
    if "scope_ids" in normalized:
        ids = normalized["scope_ids"]
        if not isinstance(ids, list) or not 1 <= len(ids) <= 1000 \
                or any(not isinstance(key, str) for key in ids) or len(set(ids)) != len(ids):
            raise ValueError("scope_ids must contain 1..1000 distinct file IDs.")
        for key in ids:
            catalog.text(key, "scope file ID", required=True)
    return normalized


def _number(value):
    if type(value) not in (str, int, float):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def indexed_library(db):
    """Read published index evidence only; queries never probe or hash media."""
    if __package__:
        from . import library_operations, studio_cues
    else:
        import library_operations
        import studio_cues
    if not db.in_transaction:
        db.execute("BEGIN")
    rows = db.execute("""
        SELECT f.*,r.path AS root_path,r.scan_revision,r.error AS root_error
        FROM library_files f JOIN library_roots r ON r.id=f.root_id ORDER BY f.id
        LIMIT ?
    """, (MAX_INDEXED_FILES + 1,)).fetchall()
    if len(rows) > MAX_INDEXED_FILES:
        raise ValueError("Studio currently supports at most 50,000 indexed files; narrow the catalog before use.")
    history = library_operations.history_summary(db)
    analyses = studio_cues.analysis_evidence(db)
    exclusions = {}
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='studio_exclusions' AND type='table'").fetchone():
        exclusions = {row["file_id"]: dict(row) for row in db.execute("SELECT * FROM studio_exclusions")}
    feedback = {}
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='studio_feedback' AND type='table'").fetchone():
        feedback = {row["file_id"]: dict(row) for row in db.execute("SELECT * FROM studio_feedback")}
    evidence, tracks = [], []
    for row in rows:
        raw = dict(row)
        metadata = json.loads(row["metadata_json"])
        if not isinstance(metadata, dict) or any(
                key in metadata and not isinstance(metadata[key], str) for key in ("title", "artist")):
            raise ValueError("Invalid indexed title/artist metadata for file " + row["id"] + "; rescan the selected root.")
        evidence.append(raw)
        usable = bool(row["available"] and row["content_hash"] and not row["error"] and not row["root_error"])
        tracks.append({
            **metadata, "id": row["id"], "path": str(Path(row["root_path"]) / row["relative_path"]),
            "bpm": _number(metadata.get("bpm")), "energy": _number(metadata.get("energy")),
            "root_id": row["root_id"], "content_hash": row["content_hash"],
            "indexed_available": usable, "index_error": row["error"] or row["root_error"],
            "play_count": history.get(row["id"], {}).get("play_count", 0),
            "last_played": history.get(row["id"], {}).get("last_played"),
            "cue_evidence": analyses.get(row["content_hash"]),
            "studio_excluded": bool(exclusions.get(row["id"], {}).get("excluded") and
                                    exclusions[row["id"]]["content_hash"] == row["content_hash"]),
            "dj_rating": feedback.get(row["id"], {}).get("rating", 0),
            "feedback_revision": feedback.get(row["id"], {}).get("revision", 0),
        })
    used_analyses = {track["content_hash"]: track["cue_evidence"] for track in tracks if track["cue_evidence"]}
    return tracks, catalog.digest({"files": evidence, "history": history, "cue_analyses": used_analyses,
                                   "exclusions": exclusions, "feedback": feedback})


def select_tracks(tracks, rule, *, index=None):
    rule = validate_rule(rule)
    tokens = rule["q"].casefold().split()
    artist = rule["artist"].casefold()
    genre = rule["genre"].casefold()
    scope = set(rule["scope_ids"]) if "scope_ids" in rule else None
    found = []
    for track in tracks:
        if not track["indexed_available"] or track.get("studio_excluded"):
            continue
        if not track.get("title", "").strip() or not track.get("artist", "").strip():
            continue
        if scope is not None and track["id"] not in scope:
            continue
        prepared = index.get(track["id"]) if index is not None else None
        searchable = prepared[0] if prepared else " ".join(
            str(track.get(key) or "") for key in ("title", "artist", "version", "genre")).casefold()
        if any(token not in searchable for token in tokens):
            continue
        if artist and artist not in (prepared[1] if prepared else track["artist"].casefold()):
            continue
        if genre and genre != (prepared[2] if prepared else str(track.get("genre") or "").casefold()):
            continue
        if rule["never_played"] and track["play_count"]:
            continue
        if rule["clean_only"]:
            if prepared:
                clean = prepared[3]
            else:
                _, version = extract_version_tags(str(track.get("version") or "") + " " + track["title"])
                clean = version.is_clean and not version.is_dirty
            if not clean:
                continue
        eligible = True
        for stem in ("bpm", "energy"):
            value = _number(track.get(stem))
            if stem == "energy" and value is not None and 1 < value <= 100:
                value /= 100
            for suffix in ("min", "max"):
                field = stem + "_" + suffix
                if field in rule and (value is None or
                                      (value < rule[field] if suffix == "min" else value > rule[field])):
                    eligible = False
        if eligible:
            found.append(track)
    def key(track):
        field = rule["sort"]
        value = track.get(field)
        if field in ("bpm", "energy"):
            value = _number(value)
        elif field == "title":
            value = index[track["id"]][4] if index is not None else (
                track["title"].casefold(), track["artist"].casefold())
        elif field == "preference":
            value = (-track.get("dj_rating", 0), track["title"].casefold())
        return value is None, value if value is not None else "", track["id"]
    found.sort(key=key)
    return found[:rule["limit"]], len(found)


def search(db, rule):
    rule = validate_rule(rule)
    tracks, revision, index = _search_snapshot(db)
    selected, total = select_tracks(tracks, rule, index=index)
    return {"rule": rule, "tracks": copy.deepcopy(selected), "total": total, "library_revision": revision,
            "evidence": "Indexed metadata only. File availability and bytes are rechecked on approval/export. "
                        "Clean means tagged clean, not a listening certification."}


def rules(db):
    return [{**dict(row), "rule": json.loads(row["rule_json"])}
            for row in db.execute("SELECT * FROM studio_rules ORDER BY folder,name,id")]


def _begin_request(db, body, dataset, *, begin=True):
    for key in ("request_id", "dataset_id"):
        catalog.text(body[key], key, required=True)
    if body["dataset_id"] != dataset:
        raise catalog.Conflict("Studio action belongs to a different dataset.")
    if begin:
        db.execute("BEGIN IMMEDIATE")
    previous = db.execute("SELECT * FROM studio_receipts WHERE request_id=?", (body["request_id"],)).fetchone()
    if previous:
        if previous["body_json"] != catalog.canonical(body):
            raise catalog.Conflict("Studio request ID was already used with different content.")
        return json.loads(previous["receipt_json"])
    return None


def _receipt(db, body, result):
    receipt = {"ok": True, "request_id": body["request_id"], "dataset_id": body["dataset_id"], **result}
    db.execute("INSERT INTO studio_receipts VALUES(?,?,?)",
               (body["request_id"], catalog.canonical(body), catalog.canonical(receipt)))
    return receipt


def save_rule(db, body, dataset):
    exact_fields(body, {"request_id", "dataset_id", "id", "base_revision", "name", "folder", "rule"})
    for key in ("id", "name", "folder"):
        catalog.text(body[key], key, required=key != "folder")
    if type(body["base_revision"]) is not int or body["base_revision"] < 0:
        raise ValueError("Rule base_revision must be a nonnegative integer.")
    rule = validate_rule(body["rule"])
    previous = _begin_request(db, body, dataset)
    if previous:
        return previous
    row = db.execute("SELECT revision FROM studio_rules WHERE id=?", (body["id"],)).fetchone()
    revision = row["revision"] if row else 0
    if revision != body["base_revision"]:
        raise catalog.Conflict("Smart rule changed; refresh before saving.")
    db.execute("""
        INSERT INTO studio_rules VALUES(?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET name=excluded.name,folder=excluded.folder,
            revision=excluded.revision,rule_json=excluded.rule_json
    """, (body["id"], body["name"], body["folder"], revision + 1, catalog.canonical(rule)))
    return _receipt(db, body, {"id": body["id"], "revision": revision + 1})


def propose(db, body, dataset, *, planning=None, release_snapshot=True):
    exact_fields(body, {"dataset_id", "name", "rule"}, {"file_ids"})
    if body["dataset_id"] != dataset:
        raise catalog.Conflict("Proposal belongs to a different dataset.")
    catalog.text(body["name"], "name", required=True)
    result = search(db, body["rule"])
    selected = result["tracks"]
    if "file_ids" in body:
        ids = body["file_ids"]
        if not isinstance(ids, list) or not ids or any(not isinstance(key, str) for key in ids) \
                or len(set(ids)) != len(ids):
            raise ValueError("A proposal needs distinct ordered file IDs.")
        by_id = {track["id"]: track for track in selected}
        if set(ids) - by_id.keys():
            raise ValueError("Proposed file IDs must come from the displayed eligible search results.")
        selected = [by_id[key] for key in ids]
    if not selected:
        raise ValueError("No eligible files; revise the filters or explicitly index a selected folder.")
    document = {"dataset_id": dataset, "name": body["name"], "rule": result["rule"],
                "library_revision": result["library_revision"], "tracks": selected,
                "planning": planning, "created_at": time.time(),
                "expires_at": time.time() + PROPOSAL_SECONDS,
                "note": "New static personal crate only. No audio, tags or external crates are changed."}
    proposal_id = catalog.digest(document)
    # search holds a read snapshot; release it before the short app-owned write.
    if release_snapshot:
        db.commit()
    db.execute("INSERT OR IGNORE INTO studio_proposals VALUES(?,?)",
               (proposal_id, catalog.canonical(document)))
    return {"proposal_id": proposal_id, **document}


def proposal(db, proposal_id, dataset):
    row = db.execute("SELECT document_json FROM studio_proposals WHERE id=?", (proposal_id,)).fetchone()
    if not row:
        raise ValueError("Proposal not found.")
    document = json.loads(row["document_json"])
    if not isinstance(document, dict) or catalog.digest(document) != proposal_id \
            or document.get("dataset_id") != dataset:
        raise catalog.Conflict("Proposal evidence or dataset is invalid.")
    return document


def crate_evidence(db, body, dataset):
    if __package__:
        from . import studio_cues
    else:
        import studio_cues
    exact_fields(body, {"crate_id"})
    catalog.text(body["crate_id"], "crate_id", required=True)
    if not body["crate_id"].startswith("studio-"):
        raise ValueError("This crate has no Studio creation journal.")
    crate = personal_crates.detail(db, body["crate_id"])
    document = proposal(db, body["crate_id"][len("studio-"):], dataset)
    expected = [(track["id"], track["content_hash"]) for track in document["tracks"]]
    current = [(entry["selected_file"]["id"], entry["selected_file"]["content_hash"])
               if entry["selected_file"] else (None, None) for entry in crate["entries"]]
    analyses = studio_cues.analysis_evidence(db)
    analysis_matches = all(track.get("cue_evidence") == analyses.get(track["content_hash"])
                           for track in document["tracks"])
    return {
        "crate_id": crate["id"], "revision": crate["revision"],
        "current_selection_matches": current == expected,
        "current_analysis_matches": analysis_matches,
        "current_analysis_revision": catalog.digest({
            track["content_hash"]: analyses.get(track["content_hash"]) for track in document["tracks"]}),
        "creation": document,
        "note": "Historical creation evidence, not current availability or musical timing certification. "
                "Later crate edits or cue reviews do not rewrite this journal. "
                "Check both selection and analysis comparisons; export freshly verifies selected files.",
    }


def approve(db, body, dataset, *, begin=True):
    exact_fields(body, {"request_id", "dataset_id", "proposal_id"})
    previous = _begin_request(db, body, dataset, begin=begin)
    if previous:
        return previous
    document = proposal(db, body["proposal_id"], dataset)
    if time.time() > document["expires_at"]:
        raise catalog.Conflict("Proposal expired; preview the current library again.")
    _, revision = indexed_library(db)
    if revision != document["library_revision"]:
        raise catalog.Conflict("Library or play history changed; preview the current proposal again.")
    songs = [{key: value for key, value in track.items()
              if key not in {"path", "root_id", "content_hash", "indexed_available", "index_error",
                             "play_count", "last_played"}}
             for track in document["tracks"]]
    snapshot = {"schema_version": 1, "source": "Studio approved exact-file proposal " + body["proposal_id"],
                "crates": [{"id": "approved", "title": document["name"], "available": True, "songs": songs}]}
    imported = catalog.import_snapshot(db, snapshot, begin=False)
    entries = []
    for index, track in enumerate(document["tracks"]):
        catalog.decide(db, {"request_id": "studio:" + body["proposal_id"] + ":" + track["id"],
                           "dataset_id": dataset, "snapshot_id": imported["snapshot_id"], "song_id": track["id"],
                           "base_revision": 0, "file_id": track["id"], "content_hash": track["content_hash"],
                           "note": "Human approved the exact indexed file in the Studio proposal."}, begin=False)
        entries.append({"id": str(index), "snapshot_id": imported["snapshot_id"], "song_id": track["id"]})
    crate_id = "studio-" + body["proposal_id"]
    personal_crates.save(db, {"request_id": "studio-save:" + body["proposal_id"], "dataset_id": dataset,
                             "crate_id": crate_id, "base_revision": 0, "name": document["name"],
                             "entries": entries}, dataset, begin=False)
    return _receipt(db, body, {"crate_id": crate_id, "revision": 1, "proposal_id": body["proposal_id"],
                               "entries": len(entries)})
