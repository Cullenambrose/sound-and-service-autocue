"""Opt-in loopback Studio: catalog-backed planning, not the legacy media writer."""
import argparse
import base64
import contextlib
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3
import threading
import urllib.parse

from . import server, catalog, personal_crates, studio_ai, studio_service, product_capabilities
from . import library_operations, record_pools, studio_ingest, studio_organize, studio_assistant
from . import studio_history
from setlist import context_from_event, rank_next_song, song_identity
from setlist.planning import plan_set, validate_policy
from setlist.transitions import transition_report, validate_playback

STATIC = {"/": ("studio.html", "text/html; charset=utf-8"),
          "/catalog-state.js": ("catalog-state.js", "text/javascript; charset=utf-8"),
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
        studio_ingest.reconcile(db)


def event_candidates(tracks, event):
    """Apply the same request/exclusion/version policy before whole-set planning."""
    from setlist import rank_prep_crate
    if not isinstance(event, dict):
        raise ValueError("Event context must be a JSON object.")
    context = context_from_event(event)
    ranked = rank_prep_crate(context, tracks, limit=len(tracks))
    unresolved = [asdict(item) for item in ranked.resolution_diagnostics
                  if item.category == "must_play" and item.status != "matched"]
    if ranked.conflicting_must_play or unresolved:
        raise ValueError("Resolve event must-play conflicts, missing files and exact versions before planning: " +
                         json.dumps({"unresolved": unresolved,
                                     "conflicts": [asdict(item) for item in ranked.conflicting_must_play]}))
    required_songs = {item.identity for item in ranked.resolution_diagnostics
                      if item.category == "must_play" and item.status == "matched"}
    required = [item.track["id"] for item in ranked if item.song_identity in required_songs]
    eligible_identities = {item.song_identity for item in ranked}
    required_identities = {item.song_identity: item.track["id"] for item in ranked
                           if item.track["id"] in required}
    selected = []
    for track in tracks:
        identity = song_identity(track["title"], track["artist"])
        if identity in eligible_identities and (
                identity not in required_identities or track["id"] == required_identities[identity]):
            selected.append(track)
    return selected, required


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
    if endpoint == "plan/propose":
        studio_service.exact_fields(body, {"rule", "policy", "event", "dataset_id", "name", "alternative_index"},
                                    {"playback"})
        index = body["alternative_index"]
        if type(index) is not int or index < 0:
            raise ValueError("alternative_index must be a nonnegative integer.")
        planned = dispatch(db, "plan", {key: body[key] for key in ("rule", "policy", "event", "playback")
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
                                                "alternative": alternative})
    if endpoint == "plan":
        studio_service.exact_fields(body, {"rule", "policy", "event"}, {"playback"})
        playback = validate_playback(body.get("playback", {"windows": [], "overlaps": []}))
        result = studio_service.search(db, body["rule"])
        tracks, required = event_candidates(result["tracks"], body["event"])
        if not isinstance(body["policy"], dict):
            raise ValueError("Planner policy must be an object.")
        policy = dict(body["policy"])
        if not isinstance(policy.get("must_include", []), list):
            raise ValueError("must_include must be an array.")
        if any(not isinstance(key, str) for key in policy.get("must_include", [])):
            raise ValueError("Studio must_include must contain exact string file IDs.")
        policy["must_include"] = sorted(set(policy.get("must_include", []) + required))
        planned = plan_set(tracks, policy)
        for alternative in planned["alternatives"]:
            alternative["transition_evidence"] = transition_report(alternative["tracks"], playback)
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
        context = context_from_event(body["event"], phase=body["phase"],
                                     already_played=[song_identity(tracks[key]["title"], tracks[key]["artist"])
                                                     for key in ids])
        recommendations = rank_next_song(context, list(tracks.values()), now_playing=playing, limit=10)
        return {"recommendations": asdict(recommendations),
                "note": "Read-only Live suggestion. No saved Prep crate or play history was changed."}
    raise ValueError("Unknown Studio action.")


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
        if path == "/api/studio/state":
            try:
                with server.connect() as db:
                    return server.json_response(self, {
                        "rules": studio_service.rules(db), "crates": personal_crates.listing(db),
                        "health": library_operations.health(db), "history": library_operations.play_history(db),
                        "capabilities": product_capabilities.report(studio_assistant.model_settings(db)["model"]),
                        "ai_settings": studio_assistant.model_settings(db),
                        "ingestion": studio_ingest.state(db), "batches": studio_organize.history(db),
                        "edits": studio_organize.edit_history(db),
                        "history_edits": studio_history.edits(db),
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
