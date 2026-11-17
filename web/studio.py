"""Opt-in loopback Studio: catalog-backed planning, not the legacy media writer."""
import argparse
import base64
import contextlib
from collections import Counter, OrderedDict
from dataclasses import asdict, replace
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import urllib.parse
import uuid

from . import server, catalog, personal_crates, studio_ai, studio_service, product_capabilities
from . import library_operations, record_pools, studio_ingest, studio_organize, studio_assistant, studio_cleanup
from . import studio_history, studio_event_reviews
from setlist import SetlistContext, SongRequest, context_from_event, rank_next_song, rank_prep_crate, song_identity
from setlist.planning import plan_set, validate_policy
from setlist.transitions import (full_file_timeline, project_wall_clock_timeline, transition_report,
                                 rebase_wall_clock_timeline, validate_playback, validate_wall_clock_start)

_AUDITION_HASH_CACHE = OrderedDict()
_AUDITION_HASH_LOCK = threading.Lock()
_AUDITION_HASH_CACHE_SIZE = 128
_AUDIO_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")

STATIC = {"/": ("studio.html", "text/html; charset=utf-8"),
          "/catalog-state.js": ("catalog-state.js", "text/javascript; charset=utf-8"),
          "/studio-plan-view.js": ("studio-plan-view.js", "text/javascript; charset=utf-8"),
          "/studio.js": ("studio.js", "text/javascript; charset=utf-8"),
          "/studio.css": ("studio.css", "text/css; charset=utf-8")}


def initialize():
    server.initialize()
    with server.connect() as db:
        library_operations.initialize(db)
        studio_service.initialize(db)
        studio_ingest.initialize(db)
        studio_organize.initialize(db)
        studio_assistant.initialize(db)
        studio_history.initialize(db)
        studio_event_reviews.initialize(db)
        studio_cleanup.initialize(db)
        studio_ingest.reconcile(db)


def _playlist_source_names(sources):
    counts = Counter(source["title"] for source in sources)
    return ", ".join(
        f'{source["title"]} [{source["source_id"]}]' if counts[source["title"]] > 1
        else source["title"]
        for source in sources
    )


def event_candidates(tracks, event, playlist_evidence=None):
    """Apply the same request/exclusion/version policy before whole-set planning."""
    if not isinstance(event, dict):
        raise ValueError("Event context must be a JSON object.")
    playlist_evidence = playlist_evidence or {"support": {}, "next": {}}
    context = context_from_event(
        event,
        curated_playlist_support=playlist_evidence.get("support", {}),
        curated_playlist_next=playlist_evidence.get("next", {}),
    )
    ranked = rank_prep_crate(context, tracks, limit=len(tracks))
    required_categories = {"must_play", "song_cue"}
    cue_diagnostics = []
    cue_selected = {}
    cue_conflicts = []
    seen_cues = set()
    for cue in context.song_cues:
        cue_key = (cue.moment, cue.title, cue.artist, cue.version)
        if cue_key in seen_cues or not (cue.title or cue.artist):
            continue
        seen_cues.add(cue_key)
        cue_context = context_from_event(
            event, phase=cue.moment,
            curated_playlist_support=playlist_evidence.get("support", {}),
            curated_playlist_next=playlist_evidence.get("next", {}),
        )
        cue_ranked = rank_prep_crate(cue_context, tracks, limit=len(tracks))
        diagnostic = next((item for item in cue_ranked.resolution_diagnostics
                           if item.category == "song_cue"
                           and (item.request.moment, item.request.title, item.request.artist,
                                item.request.version) == cue_key), None)
        if diagnostic is None:
            request = SongRequest(
                title=cue.title, artist=cue.artist, version=cue.version,
                moment=cue.moment, metadata=cue.metadata,
            )
            cue_ranked = rank_prep_crate(SetlistContext(
                must_play=(request,),
                do_not_play=context.do_not_play,
                curated_playlist_support=context.curated_playlist_support,
                curated_playlist_next=context.curated_playlist_next,
            ), tracks, limit=len(tracks))
            diagnostic = replace(cue_ranked.resolution_diagnostics[0], category="song_cue")
            cue_conflicts.extend(asdict(item) for item in cue_ranked.conflicting_must_play)
        else:
            cue_conflicts.extend(asdict(item) for item in cue_ranked.conflicting_requests
                                 if item.category in required_categories)
        cue_diagnostics.append(diagnostic)
        if diagnostic.status == "matched" and diagnostic.identity:
            selected = next((item.track["id"] for item in cue_ranked
                             if item.song_identity == diagnostic.identity), None)
            if selected:
                cue_selected[diagnostic.identity] = selected

    diagnostics = [*ranked.resolution_diagnostics, *cue_diagnostics]
    unresolved = [asdict(item) for item in diagnostics
                  if item.category in required_categories and item.status != "matched"]
    conflicts = [asdict(item) for item in ranked.conflicting_requests
                 if item.category in required_categories] + cue_conflicts
    if ranked.conflicting_must_play or unresolved or conflicts:
        raise ValueError(
            "Resolve event must-play and special-moment conflicts, missing files and exact versions before planning: "
            + json.dumps({"unresolved": unresolved, "conflicts": conflicts})
        )
    required_songs = {
        item.identity for item in diagnostics
        if item.category in required_categories and item.status == "matched"
    }
    selected_by_identity = {item.song_identity: item.track["id"] for item in ranked}
    required_by_identity = {
        item.identity: selected_by_identity[item.identity]
        for item in ranked.resolution_diagnostics
        if item.category == "must_play" and item.status == "matched"
        and item.identity in selected_by_identity
    }
    for identity in required_songs:
        if identity in cue_selected:
            required_by_identity[identity] = cue_selected[identity]
        elif identity in selected_by_identity:
            required_by_identity[identity] = selected_by_identity[identity]
    required = sorted(set(required_by_identity.values()))
    effective_selected_by_identity = {**selected_by_identity, **required_by_identity}
    file_reasons = {}
    play_if_possible_ids = set()
    for diagnostic in diagnostics:
        if diagnostic.category not in required_categories | {"play_if_possible"} \
                or diagnostic.status != "matched" or diagnostic.identity not in effective_selected_by_identity:
            continue
        file_id = effective_selected_by_identity[diagnostic.identity]
        request = diagnostic.request
        song = " - ".join(value for value in (request.artist, request.title) if value)
        if diagnostic.category == "must_play":
            label = "Booking must-play"
        elif diagnostic.category == "song_cue":
            label = "Booking special moment" + (f" ({request.moment})" if request.moment else "")
        else:
            playlist_title = request.metadata.get("client_playlist_title")
            playlist_position = request.metadata.get("client_playlist_position")
            if isinstance(playlist_title, str) and playlist_title.strip():
                item_position = (f", item {playlist_position + 1}"
                                 if type(playlist_position) is int and playlist_position >= 0 else "")
                label = f"Booking client playlist ({playlist_title}{item_position})"
            else:
                label = "Booking play-if-possible"
            play_if_possible_ids.add(file_id)
        file_reasons.setdefault(file_id, []).append(f"{label}: {song}")
    eligible_identities = {item.song_identity for item in ranked}
    selected = []
    for track in tracks:
        identity = song_identity(track["title"], track["artist"])
        if identity in eligible_identities and (
                identity not in required_by_identity or track["id"] == required_by_identity[identity]):
            selected.append(track)
    unresolved_preferences = [
        asdict(item) for item in diagnostics
        if item.category == "play_if_possible" and item.status != "matched"
    ]
    return selected, required, {
        "file_reasons": file_reasons,
        "play_if_possible_ids": sorted(play_if_possible_ids),
        "unresolved_play_if_possible": unresolved_preferences,
    }


def dispatch(db, endpoint, body, dataset):
    if endpoint == "backup":
        from . import studio_backup
        studio_service.exact_fields(body, set())
        bundle, manifest = studio_backup.create(db)
        return {"filename": "autocue-studio-database-backup.zip",
                "bundle_base64": base64.b64encode(bundle).decode("ascii"), "manifest": manifest}
    operations = {"inbox/review": studio_ingest.review, "monitor/save": studio_ingest.monitor,
                  "exclusion/save": studio_ingest.exclude, "batch/preview": studio_organize.preview,
                  "batch/approve": studio_organize.approve, "assistant/chat": studio_assistant.chat,
                  "feedback/save": studio_assistant.feedback, "ai/configure": studio_assistant.configure_model}
    operations.update({"edit/preview": studio_organize.edit_preview, "edit/approve": studio_organize.edit_approve,
                       "edit/undo": studio_organize.edit_undo})
    operations.update({"history/preview": studio_history.preview, "history/approve": studio_history.approve,
                       "history/undo": studio_history.undo})
    operations["review/save"] = studio_event_reviews.save
    operations.update({"file-repair/preview": studio_cleanup.preview,
                       "file-repair/approve": studio_cleanup.approve,
                       "file-repair/undo": studio_cleanup.undo})
    if endpoint in operations:
        return operations[endpoint](db, body, dataset)
    if endpoint == "inbox":
        studio_service.exact_fields(body, set())
        return studio_ingest.state(db)
    if endpoint == "assistant/load":
        studio_service.exact_fields(body, {"conversation_id"})
        return studio_assistant.conversation(db, body["conversation_id"])
    if endpoint == "providers":
        studio_service.exact_fields(body, {"title", "artist", "version"})
        for field in ("title", "artist", "version"):
            catalog.text(body[field], field, required=field == "title")
        query = " ".join(body[field].strip() for field in ("artist", "title", "version") if body[field].strip())
        return {"query": query, "providers": record_pools.provider_links(query),
                "note": "Browser navigation only; no provider was queried, no file was downloaded and no request was fulfilled."}
    if endpoint == "search":
        studio_service.exact_fields(body, {"rule"})
        return studio_service.search(db, body["rule"])
    if endpoint == "interpret":
        studio_service.exact_fields(body, {"message"})
        return studio_ai.interpret(body["message"], model=studio_assistant.model_settings(db)["model"])
    if endpoint == "interpret/plan":
        studio_service.exact_fields(body, {"message"})
        return studio_ai.interpret_plan(body["message"], model=studio_assistant.model_settings(db)["model"])
    if endpoint == "rules/save":
        return studio_service.save_rule(db, body, dataset)
    if endpoint == "propose":
        return studio_service.propose(db, body, dataset)
    if endpoint == "approve":
        return studio_service.approve(db, body, dataset)
    if endpoint == "crate/evidence":
        return studio_service.crate_evidence(db, body, dataset)
    if endpoint == "history/record":
        return library_operations.record_play(db, body, dataset)
    if endpoint == "plan/validate":
        studio_service.exact_fields(body, {"policy"})
        return {"policy": validate_policy(body["policy"])}
    if endpoint == "plan/reforecast":
        studio_service.exact_fields(body, {"projection", "current_file_id", "actual_start_time"},
                                    {"actual_day_offset"})
        return rebase_wall_clock_timeline(body["projection"], body["current_file_id"],
                                          body["actual_start_time"], body.get("actual_day_offset", 0))
    if endpoint == "plan/propose":
        studio_service.exact_fields(body, {"rule", "policy", "event", "dataset_id", "name", "alternative_index"},
                                    {"playback", "optimize_playback", "event_start_time"})
        index = body["alternative_index"]
        if type(index) is not int or index < 0:
            raise ValueError("alternative_index must be a nonnegative integer.")
        planned = dispatch(db, "plan", {key: body[key] for key in (
            "rule", "policy", "event", "playback", "optimize_playback", "event_start_time")
                                       if key in body}, dataset)
        if index >= len(planned["alternatives"]):
            raise catalog.Conflict("The selected alternative is no longer available; generate a new plan.")
        alternative = planned["alternatives"][index]
        return studio_service.propose(db, {"dataset_id": body["dataset_id"], "name": body["name"],
                                           "rule": body["rule"],
                                           "file_ids": [track["id"] for track in alternative["tracks"]]}, dataset,
                                      planning={"policy": body["policy"], "event": body["event"],
                                                "planner_version": planned["planner_version"],
                                                "playback": body.get("playback", {"windows": [], "overlaps": []}),
                                                "optimize_playback": body.get("optimize_playback", False),
                                                "event_start_time": body.get("event_start_time"),
                                                "alternative": alternative})
    if endpoint == "plan":
        studio_service.exact_fields(body, {"rule", "policy", "event"},
                                    {"playback", "optimize_playback", "event_start_time"})
        playback = validate_playback(body.get("playback", {"windows": [], "overlaps": []}))
        optimize_playback = body.get("optimize_playback", False)
        if type(optimize_playback) is not bool:
            raise ValueError("optimize_playback must be a boolean.")
        event_start_time = (validate_wall_clock_start(body["event_start_time"])
                            if "event_start_time" in body else None)
        result = studio_service.search(db, body["rule"])
        playlist_evidence = studio_service.curated_playlist_evidence(db)
        tracks, required, event_evidence = event_candidates(
            result["tracks"], body["event"], playlist_evidence
        )
        if not isinstance(body["policy"], dict):
            raise ValueError("Planner policy must be an object.")
        policy = dict(body["policy"])
        if not isinstance(policy.get("must_include", []), list):
            raise ValueError("must_include must be an array.")
        if any(not isinstance(key, str) for key in policy.get("must_include", [])):
            raise ValueError("Studio must_include must contain exact string file IDs.")
        policy["must_include"] = sorted(set(policy.get("must_include", []) + required))
        planned = plan_set(
            tracks, policy, playback=playback if optimize_playback else None,
            curated_playlist_support=playlist_evidence["support"],
            curated_playlist_next=playlist_evidence["next"],
            play_if_possible_ids=event_evidence["play_if_possible_ids"],
        )
        for alternative in planned["alternatives"]:
            reasons_by_id = {entry["file_id"]: entry for entry in alternative["track_reasons"]}
            for entry in alternative["track_reasons"]:
                event_reasons = event_evidence["file_reasons"].get(entry["file_id"], [])
                entry["reasons"] = list(dict.fromkeys(event_reasons + entry["reasons"]))
            previous_identity = None
            for track in alternative["tracks"]:
                identity = song_identity(track["title"], track["artist"])
                entry = reasons_by_id[track["id"]]
                membership_sources = playlist_evidence["membership_sources"].get(identity, [])
                if membership_sources:
                    entry["reasons"].append(
                        "DJ-curated CSV playlist membership: "
                        + _playlist_source_names(membership_sources)
                    )
                if previous_identity is not None:
                    sequence_sources = playlist_evidence["sequence_sources"].get(
                        (previous_identity, identity), []
                    )
                    if sequence_sources:
                        entry["reasons"].append(
                            "DJ-curated CSV sequence precedent: "
                            + _playlist_source_names(sequence_sources)
                        )
                previous_identity = identity
        planned["event_evidence"] = {
            "required_file_ids": required,
            "play_if_possible_file_ids": event_evidence["play_if_possible_ids"],
            "unresolved_play_if_possible": event_evidence["unresolved_play_if_possible"],
            "curated_csv_playlist_count": playlist_evidence["playlist_count"],
        }
        if not optimize_playback:
            for alternative in planned["alternatives"]:
                alternative["transition_evidence"] = transition_report(alternative["tracks"], playback)
        if event_start_time is not None:
            for alternative in planned["alternatives"]:
                if optimize_playback:
                    evidence = alternative.get("transition_evidence", {})
                    timeline = evidence.get("timeline") if evidence.get("status") == "estimated" else None
                    basis = "reviewed_playback_estimate"
                    if timeline is None:
                        alternative["wall_clock_timeline"] = {
                            "status": "incomplete", "basis": basis,
                            "issues": evidence.get("issues", []),
                            "note": "Reviewed playback timing is incomplete; no full-file fallback is substituted.",
                        }
                        continue
                else:
                    timeline = full_file_timeline(alternative["tracks"])
                    basis = "full_file_duration"
                alternative["wall_clock_timeline"] = project_wall_clock_timeline(
                    timeline, event_start_time, basis)
        return {**planned, "library_revision": result["library_revision"], "rule": result["rule"],
                "event": body["event"], "evidence": result["evidence"]}
    if endpoint == "live":
        studio_service.exact_fields(body, {"rule", "event", "now_playing", "played_ids", "phase"})
        result = studio_service.search(db, body["rule"])
        tracks = {track["id"]: track for track in result["tracks"]}
        if not isinstance(body["played_ids"], list) or any(not isinstance(key, str) for key in body["played_ids"]):
            raise ValueError("played_ids must be an array of selected file IDs.")
        if body["now_playing"] is not None and not isinstance(body["now_playing"], str):
            raise ValueError("now_playing must be a file ID or null.")
        if set(body["played_ids"]) - tracks.keys() or (body["now_playing"] is not None
                                                      and body["now_playing"] not in tracks):
            raise ValueError("Live IDs must refer to the current eligible library scope.")
        if body["phase"] not in {"ceremony", "cocktail", "dinner", "first_dance", "open_dancing", "generic"}:
            raise ValueError("Unsupported Live phase.")
        playing = tracks.get(body["now_playing"])
        ids = body["played_ids"] + ([playing["id"]] if playing else [])
        playlist_evidence = studio_service.curated_playlist_evidence(db)
        context = context_from_event(
                                     body["event"], phase=body["phase"],
                                     curated_playlist_support=playlist_evidence["support"],
                                     curated_playlist_next=playlist_evidence["next"],
                                     already_played=[song_identity(tracks[key]["title"], tracks[key]["artist"])
                                                     for key in ids])
        recommendations = rank_next_song(context, list(tracks.values()), now_playing=playing, limit=10)
        return {"recommendations": asdict(recommendations),
                "note": "Read-only Live suggestion. No saved Prep crate or play history was changed."}
    raise ValueError("Unknown Studio action.")


def _open_audition_file(file_id):
    try:
        if str(uuid.UUID(file_id)) != file_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Audition requires an exact indexed file ID.") from error
    with contextlib.closing(server.connect()) as db:
        row = db.execute("""
            SELECT f.*,r.path AS root_path,r.device AS root_device,r.inode AS root_inode,
                   r.scanned_at,r.error AS root_error,r.scan_revision
            FROM library_files f JOIN library_roots r ON r.id=f.root_id WHERE f.id=?
        """, (file_id,)).fetchone()
        if not row:
            raise FileNotFoundError("Indexed audio file was not found.")
        root = {"id": row["root_id"], "path": row["root_path"], "device": row["root_device"],
                "inode": row["root_inode"], "scanned_at": row["scanned_at"],
                "error": row["root_error"], "scan_revision": row["scan_revision"]}
        try:
            file_status = catalog.file_status(row, root)
        except (OSError, RuntimeError) as error:
            raise catalog.Conflict("Indexed audio path is unsafe or unavailable; rescan the selected root.") from error
        if file_status != "available":
            raise catalog.Conflict("Indexed audio changed or is unavailable; rescan the selected root.")
        if (not isinstance(row["content_hash"], str) or len(row["content_hash"]) != 64
                or any(character not in "0123456789abcdef" for character in row["content_hash"])):
            raise catalog.Conflict("Indexed audio has no valid content hash; rescan before audition.")
        try:
            root_path = Path(root["path"]).resolve(strict=True)
            path = (root_path / row["relative_path"]).resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise catalog.Conflict("Indexed audio path is unsafe or unavailable; rescan the selected root.") from error
        if not path.is_relative_to(root_path):
            raise catalog.Conflict("Indexed audio path is outside its selected root.")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        source = os.fdopen(descriptor, "rb")
        try:
            before = os.fstat(source.fileno())
            expected_signature = (row["size"], row["mtime_ns"], row["ctime_ns"])
            if not stat.S_ISREG(before.st_mode) or catalog.signature(before) != expected_signature:
                raise catalog.Conflict("Indexed audio changed before audition; rescan the selected root.")
            cache_key = (file_id, row["content_hash"], before.st_dev, before.st_ino, *expected_signature)
            with _AUDITION_HASH_LOCK:
                verified = cache_key in _AUDITION_HASH_CACHE
                if verified:
                    _AUDITION_HASH_CACHE.move_to_end(cache_key)
            if not verified:
                digest = hashlib.sha256()
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
                after = os.fstat(source.fileno())
                if (catalog.signature(after) != expected_signature
                        or digest.hexdigest() != row["content_hash"]):
                    raise catalog.Conflict("Indexed audio hash changed; rescan the selected root.")
                with _AUDITION_HASH_LOCK:
                    _AUDITION_HASH_CACHE[cache_key] = True
                    _AUDITION_HASH_CACHE.move_to_end(cache_key)
                    while len(_AUDITION_HASH_CACHE) > _AUDITION_HASH_CACHE_SIZE:
                        _AUDITION_HASH_CACHE.popitem(last=False)
            source.seek(0)
            return source, path.name, before.st_size, row["content_hash"]
        except (catalog.Conflict, OSError, RuntimeError, ValueError, sqlite3.Error):
            source.close()
            raise


class Handler(server.Handler):
    def _local_request(self):
        return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}",
                                           f"localhost:{self.server.server_port}"}

    def do_GET(self):
        if not self._local_request() or not self._valid_origin() \
                or self.headers.get("Sec-Fetch-Site") == "cross-site":
            return server.json_response(self, {"error": "Use the same-origin loopback Studio URL."}, 403)
        path = urllib.parse.urlparse(self.path).path
        if path in STATIC:
            filename, kind = STATIC[path]
            return self._static(server.WEB / filename, kind)
        if path.startswith("/api/studio/audition/"):
            fetch_site = self.headers.get("Sec-Fetch-Site")
            referer = urllib.parse.urlparse(self.headers.get("Referer", ""))
            same_origin_referer = referer.scheme == "http" and referer.netloc == self.headers.get("Host", "")
            if fetch_site == "cross-site" or (
                    fetch_site != "same-origin" and not self.headers.get("Origin") and not same_origin_referer):
                return server.json_response(self, {"error": "Cross-site audio audition is not allowed."}, 403)
            file_id = path.removeprefix("/api/studio/audition/")
            try:
                source, filename, size, digest = _open_audition_file(file_id)
            except catalog.Conflict as error:
                return server.json_response(self, {"error": str(error)}, 409)
            except FileNotFoundError as error:
                return server.json_response(self, {"error": str(error)}, 404)
            except (OSError, RuntimeError, sqlite3.Error) as error:
                self.log_error("Audition audio could not be opened: %s", error)
                return server.json_response(self, {"error": "Audition audio could not be opened: " + str(error)}, 503)
            except ValueError as error:
                return server.json_response(self, {"error": str(error)}, 400)
            with source:
                start, end = 0, size - 1
                status = 200
                range_header = self.headers.get("Range")
                if range_header:
                    match = _AUDIO_RANGE.fullmatch(range_header)
                    parts = match.groups() if match else ()
                    if (not match or "," in range_header or size <= 0 or not any(parts)
                            or any(len(part) > 20 for part in parts)):
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Accept-Ranges", "bytes")
                        self.end_headers()
                        return
                    first, last = match.groups()
                    if first:
                        start = int(first)
                        end = min(int(last), size - 1) if last else size - 1
                    else:
                        suffix = int(last) if last else 0
                        if not suffix:
                            self.send_response(416)
                            self.send_header("Content-Range", f"bytes */{size}")
                            self.send_header("Accept-Ranges", "bytes")
                            self.end_headers()
                            return
                        start = max(0, size - suffix)
                    if start >= size or end < start:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Accept-Ranges", "bytes")
                        self.end_headers()
                        return
                    status = 206
                length = end - start + 1
                source.seek(start)
                self.send_response(status)
                self.send_header("Content-Type", mimetypes.guess_type(filename)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("ETag", f'"{digest}"')
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.end_headers()
                remaining = length
                while remaining:
                    block = source.read(min(1024 * 1024, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)
                return
        if path == "/api/studio/state":
            try:
                with server.connect() as db:
                    return server.json_response(self, {
                        "rules": studio_service.rules(db), "crates": personal_crates.listing(db),
                        "health": library_operations.health(db), "history": library_operations.play_history(db),
                        "file_repairs": studio_cleanup.history(db),
                        "capabilities": product_capabilities.report(studio_assistant.model_settings(db)["model"]),
                        "ai_settings": studio_assistant.model_settings(db),
                        "ingestion": studio_ingest.state(db), "batches": studio_organize.history(db),
                        "edits": studio_organize.edit_history(db),
                        "history_edits": studio_history.edits(db),
                        "event_reviews": studio_event_reviews.history(db, server.review_store.dataset_id(db)),
                    })
            except (ValueError, sqlite3.Error) as error:
                self.log_error("Studio state could not be read: %s", error)
                return server.json_response(self, {"error": "Studio state could not be read: " + str(error)}, 503)
        if path in {"/api/session", "/api/catalog/personal", "/api/catalog/personal/detail",
                    "/api/catalog/personal/exports", "/api/catalog/personal/exports/download",
                    "/api/catalog/scans", "/api/catalog/roots"}:
            return super().do_GET()
        return server.json_response(self, {"error": "Endpoint is not enabled in the lightweight Studio."}, 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path in {"/api/catalog/scan", "/api/catalog/scans/cancel",
                    "/api/catalog/personal/export/preview", "/api/catalog/personal/export/download"}:
            return super().do_POST()
        if not self._check_mutation():
            return
        if not path.startswith("/api/studio/"):
            return server.json_response(self, {"error": "Source/media mutations are not enabled in Studio."}, 403)
        try:
            body = self._body_json(max_length=128 * 1024)
            with server.connect() as db:
                result = dispatch(db, path.removeprefix("/api/studio/"), body, server.review_store.dataset_id(db))
            return server.json_response(self, result)
        except catalog.Conflict as error:
            return server.json_response(self, {"error": str(error)}, 409)
        except (sqlite3.Error, OSError, RuntimeError) as error:
            self.log_error("Studio action failed: %s", error)
            return server.json_response(self, {"error": "Studio action was not completed: " + str(error)}, 503)
        except (ValueError, TypeError) as error:
            return server.json_response(self, {"error": str(error)}, 400)

    def do_PUT(self):
        return server.json_response(self, {"error": "Source/media mutations are not enabled in Studio."}, 403)


def configure_data(path):
    path = Path(path).expanduser().absolute()
    if path.is_symlink() or path.resolve() != path:
        raise ValueError("Studio data must be a canonical path without symlinks.")
    server.DATA, server.UPLOADS, server.DB_PATH = path, path / "audio", path / "library.sqlite3"


def create_demo():
    from .fixtures import tagged_wav
    directory = server.DATA / "studio-generated-demo"
    if directory.exists():
        raise ValueError("Generated demo already exists. Resume without --demo; existing files were not overwritten.")
    directory.mkdir()
    for index in range(12):
        (directory / f"demo-{index:02}.wav").write_bytes(tagged_wav(seconds=2))
    def metadata(path):
        index = int(path.stem.split("-")[1])
        return {"title": f"Invented Song {index:02}", "artist": f"Invented Artist {index % 4}",
                "version": "Clean" if index % 3 else "Dirty", "bpm": 80 + index * 4,
                "key": "8A", "genre": "R&B" if index < 6 else "House",
                "energy": index / 12, "duration_s": 180, "provenance": "generated-demo-metadata"}
    with server.connect() as db:
        catalog.scan_folder(db, str(directory), metadata)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=os.environ.get("AUTOCUE_STUDIO_DATA",
                        str(Path.home() / "Library/Application Support/AutoCue Studio")))
    parser.add_argument("--port", type=int, default=8772)
    parser.add_argument("--check", action="store_true", help="Initialize/check app-owned state without starting HTTP.")
    parser.add_argument("--demo", action="store_true", help="Create and index generated files in a new app-owned demo folder.")
    recovery = parser.add_mutually_exclusive_group()
    recovery.add_argument("--backup", type=Path, help="Save a private database-only backup to a new ZIP path and exit.")
    recovery.add_argument("--restore", type=Path, help="Restore a database backup into a NEW --data directory and exit.")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535.")
    if args.backup or args.restore:
        from . import studio_backup
        if args.demo:
            parser.error("--demo cannot be combined with backup/restore.")
        if args.restore:
            manifest = studio_backup.restore(studio_backup.read(args.restore), Path(args.data).expanduser().absolute())
        else:
            path = Path(args.data).expanduser().resolve() / "library.sqlite3"
            with contextlib.closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                bundle, manifest = studio_backup.create(db)
            studio_backup.save(bundle, args.backup)
        print(json.dumps(manifest, indent=2))
        return
    configure_data(args.data)
    initialize()
    if args.demo:
        create_demo()
    if args.check:
        with server.connect() as db:
            if [row[0] for row in db.execute("PRAGMA quick_check")] != ["ok"]:
                raise RuntimeError("SQLite integrity check failed; preserve the data directory for recovery.")
        print(json.dumps({"state": "local-alpha", "data_initialized": True,
                          "commercial_ready": False, "release_gates": product_capabilities.RELEASE_GATES}, indent=2))
        return
    http = server.LocalHTTPServer(("127.0.0.1", args.port), Handler)
    stop = threading.Event()
    # Only explicitly submitted catalog scans run; analysis/media workers never start.
    with server.connect() as db:
        server.scan_jobs.recover(db)
    scans = threading.Thread(target=server.scan_jobs.worker, args=(server.connect, stop), daemon=True)
    scans.start()
    with server.connect() as db:
        dataset = server.review_store.dataset_id(db)
    monitoring = threading.Thread(target=studio_ingest.worker, args=(server.connect, dataset, stop), daemon=True)
    monitoring.start()
    print(f"AutoCue Studio alpha: http://127.0.0.1:{http.server_port}", flush=True)
    try:
        http.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        http.server_close()
        scans.join(timeout=5)
        monitoring.join(timeout=5)
        if scans.is_alive():
            print("A selected scan is still inside filesystem I/O; next startup will mark it interrupted.", flush=True)


if __name__ == "__main__":
    main()
