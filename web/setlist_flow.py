"""Shared event workflow; matching, ranking and hard exclusions belong to setlist."""
import copy
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading

from library.matcher import extract_version_tags
from setlist import PreparedLibrary, SongRequest, SetlistContext, context_from_event, rank_next_song, rank_prep_crate, song_identity

if __package__:
    from . import serato_import, record_pools, acquisition, set_planning, set_intake
else:
    import serato_import
    import record_pools
    import acquisition
    import set_planning
    import set_intake

FIXTURES = Path(__file__).resolve().parents[1] / "engine/setlist/fixtures"


class FlowConflict(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def request_key(category, request):
    fields = {key: getattr(request, key, None) for key in ("id", "title", "artist", "version", "moment")}
    return category + ":" + digest(canonical(fields).encode())[:16]


def version_label(track):
    if track.get("version"):
        return str(track["version"])
    _, version = extract_version_tags(track.get("title") or "")
    return ", ".join(tag.title() for tag in version.tags) or "Original"


def clean_track(track):
    return {key: value for key, value in track.items() if not key.startswith("_")}


def playlist(entries):
    paths = [entry["track"]["path"] for entry in entries]
    if any(not isinstance(path, str) or not path.startswith("/") or "\n" in path or "\r" in path
           for path in paths):
        raise ValueError("Every playlist entry needs an absolute, single-line local path.")
    return ("#EXTM3U\n" + "".join(path + "\n" for path in paths)).encode("utf-8")


class SetlistFlow:
    def __init__(self, directory, *, event=None, library=None, output_prefix="05"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        if output_prefix not in {"05", "06"}:
            raise ValueError("Synthetic output prefix must be 05 or 06.")
        self.output_prefix = output_prefix
        self.state_path = self.directory / f"{output_prefix}-session.json"
        self.saved_path = self.directory / f"{output_prefix}-prep-saved.json"
        self.export_path = self.directory / f"{output_prefix}-prep-export.m3u8"
        self.event = copy.deepcopy(event if event is not None else json.loads(
            (FIXTURES / "mock-wedding-event.json").read_text()))
        self.library = copy.deepcopy(library if library is not None else json.loads(
            (FIXTURES / "mock-library.json").read_text())["tracks"])
        self.prepared_library = PreparedLibrary(self.library)
        self._added_snapshot = None
        self._seed_resolution_cache = {}
        self._overview = None
        self._typed_diagnostics = {}
        self.lock = threading.RLock()
        self.state = {
            "revision": 0, "prep_decisions": {}, "live_decisions": {}, "played": [],
            "guests": copy.deepcopy(self.event.get("_demo_live_inputs", {}).get("guest_requests", [])),
            "saved": None, "exported": None, "receipts": {},
            "serato_source": None,
            "acquisition_progress": {}, "library_additions": [],
            "prep_mode": "seed",
        }
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())
            self.state.setdefault("serato_source", None)
            self.state.setdefault("acquisition_progress", {})
            self.state.setdefault("library_additions", [])
            self.state.setdefault("prep_mode", "seed")
            if set(self.state) != {
                "revision", "prep_decisions", "live_decisions", "played", "guests",
                "saved", "exported", "receipts", "serato_source",
                "acquisition_progress", "library_additions",
                "prep_mode",
            }:
                raise ValueError("Unsupported workflow session; use a new output directory.")

    def _library_snapshot(self, state):
        additions = state["library_additions"]
        if not additions:
            return self.library, self.prepared_library
        key = canonical(additions)
        if self._added_snapshot is None or self._added_snapshot[0] != key:
            tracks = self.library + copy.deepcopy(additions)
            self._added_snapshot = (key, tracks, PreparedLibrary(tracks))
        return self._added_snapshot[1:]

    def _write(self, path, data):
        descriptor, temporary = tempfile.mkstemp(prefix=f"{self.output_prefix}-write-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _context(self, mode, state):
        context = context_from_event(
            self.event, guest_requests=state["guests"] if mode == "live" else (),
            already_played=state["played"] if mode == "live" else (), phase="first_dance",
        )
        decisions = state[mode + "_decisions"]
        for category, field in (
            ("must_play", "must_play"), ("play_if_possible", "play_if_possible"),
            ("song_cue", "song_cues"), ("guest_request", "guest_requests"),
        ):
            requests = []
            for request in getattr(context, field):
                decision = decisions.get(request_key(category, request))
                if decision and decision["action"] == "confirm":
                    track = next((track for track in self._library_snapshot(state)[0]
                                  if track["id"] == decision["file_id"]), None)
                    if track is None:
                        raise FlowConflict("Confirmed library file is no longer available.")
                    request = replace(
                        request, title=track["title"], artist=track.get("artist"),
                        version=track.get("version"),
                        metadata={**request.metadata, "confirmed_file_id": track["id"]},
                    )
                requests.append(request)
            setattr(context, field, tuple(requests))
        return context

    def _rank(self, mode, state):
        context = self._context(mode, state)
        ranker = rank_prep_crate if mode == "prep" else rank_next_song
        tracks, prepared = self._library_snapshot(state)
        return ranker(context, prepared, limit=len(tracks))

    def _view(self, mode, state):
        result = self._rank(mode, state)
        # Original diagnostics preserve exactly what was typed after a confirmation.
        event_key = canonical(self.event) + canonical(state["library_additions"])
        guest_keys = tuple(canonical(guest) for guest in state["guests"]) if mode == "live" else ()
        cached = self._typed_diagnostics.get(mode)
        if cached is not None and cached[:2] == (event_key, guest_keys):
            typed_diagnostics = cached[2]
        elif (cached is not None and cached[0] == event_key
              and guest_keys[:len(cached[1])] == cached[1]):
            # Guest commands append requests; existing typed diagnostics stay valid.
            previous = cached[2]
            typed_diagnostics = previous + result.resolution_diagnostics[len(previous):]
            self._typed_diagnostics[mode] = (event_key, guest_keys, typed_diagnostics)
        else:
            original_state = {**state, mode + "_decisions": {}}
            original = self._rank(mode, original_state) if state[mode + "_decisions"] else result
            typed_diagnostics = original.resolution_diagnostics
            self._typed_diagnostics[mode] = (event_key, guest_keys, typed_diagnostics)
        if len(result.resolution_diagnostics) != len(typed_diagnostics):
            raise ValueError("Confirmation changed the request list; reload the session.")
        rows, held, fulfilled = [], set(), set()
        for diagnostic, typed in zip(result.resolution_diagnostics, typed_diagnostics):
            key = request_key(typed.category, typed.request)
            decision = state[mode + "_decisions"].get(key)
            conflicts = [conflict.reason for conflict in result.conflicting_requests
                         if conflict.category == diagnostic.category and conflict.request == diagnostic.request]
            blocked = next((reason for reason in ("do_not_play", "already_played", "already_placed")
                            if reason in conflicts), None)
            status = "blocked" if blocked else diagnostic.status
            if not blocked and "version_conflict_not_selected" in conflicts:
                status = "version_conflict"
            is_fulfilled = status == "matched"
            if is_fulfilled and diagnostic.identity:
                fulfilled.add(diagnostic.identity)
            alternatives = []
            for track in typed.alternatives:
                track = clean_track(track)
                identity = song_identity(track.get("title"), track.get("artist"))
                if (not is_fulfilled or typed.status != "matched") and identity:
                    held.add(identity)
                alternatives.append({**track, "version_label": version_label(track)})
            notes = {
                "missing": "Not in your library", "ambiguous": "Choose the exact file to play",
                "version_conflict": "A higher-priority request selected another version",
                "do_not_play": "Blocked by do-not-play", "already_played": "Already played this event",
                "already_placed": "Already placed in this event",
            }
            note = notes.get(blocked or status, "")
            if status == "version_unavailable":
                versions = sorted({version_label(track) for track in alternatives})
                requested = typed.request.version or version_label({"title": typed.request.title})
                note = f"Have {', '.join(versions)}, not {requested}"
            selected = next((item.track for item in result if item.song_identity == diagnostic.identity), None)
            rows.append({
                "key": key, "category": typed.category, "request": asdict(typed.request),
                "status": status, "note": note, "fulfilled": is_fulfilled,
                "decision": decision["action"] if decision else None,
                "needs_review": not is_fulfilled and not decision,
                "candidates": alternatives[:3],
                "can_confirm": not blocked and status != "matched",
                "selected_file_id": selected.get("id") if selected and is_fulfilled else None,
            })
        recommendations = [
            {"song_identity": asdict(item.song_identity), "track": clean_track(item.track),
             "score": item.score, "reasons": item.reasons, "badges": item.badges,
             "version_label": version_label(item.track)}
            for item in result if item.song_identity not in held - fulfilled
        ]
        saved = state["saved"]
        source = state["serato_source"] if mode == "prep" else None
        seed_rows, crate_entries = self._seed(source, recommendations, state)
        out_of_pool = []
        if mode == "prep" and state["prep_mode"] == "source":
            source_ids = {
                source.get("replacements", {}).get(entry["id"]) or entry["file_id"]
                for entry in source["entries"] if entry["id"] in source["order"]}
            tracks = [track for track in self._library_snapshot(state)[0] if track["id"] in source_ids]
            scoped_context = self._context("prep", state)
            for field in ("must_play", "play_if_possible", "song_cues"):
                setattr(scoped_context, field, tuple(
                    replace(request, metadata={key: value for key, value in request.metadata.items()
                                               if key != "confirmed_file_id"})
                    if request.metadata.get("confirmed_file_id") not in source_ids else request
                    for request in getattr(scoped_context, field)))
            scoped = rank_prep_crate(scoped_context, tracks, limit=len(tracks))
            selected = {item.song_identity: item.track["id"] for item in scoped}
            outside_confirmations = set()
            for row, diagnostic in zip(rows, scoped.resolution_diagnostics):
                if row["fulfilled"]:
                    confirmed_outside = row["decision"] == "confirm" and row["selected_file_id"] not in source_ids
                    if confirmed_outside or diagnostic.status != "matched" or diagnostic.identity not in selected:
                        out_of_pool.append(row)
                        if confirmed_outside:
                            outside_confirmations.add(diagnostic.identity)
                    else:
                        row["selected_file_id"] = selected[diagnostic.identity]
            recommendations = [
                {"song_identity": asdict(item.song_identity), "track": copy.deepcopy(clean_track(item.track)),
                 "score": item.score, "reasons": item.reasons, "badges": item.badges,
                 "version_label": version_label(item.track)}
                for item in scoped if item.song_identity not in (held - fulfilled) | outside_confirmations]
            crate_entries = recommendations
        elif mode == "prep" and state["prep_mode"] == "library":
            crate_entries = recommendations
        saved_current = bool(saved and saved["decisions"] == state["prep_decisions"]
                             and saved.get("prep_mode", "seed") == state["prep_mode"]
                             and saved.get("library_revision") == (
                                 digest(canonical(state["library_additions"]).encode())
                                 if state["library_additions"] else None)
                             and saved.get("seed_revision") == (
                                 digest(canonical(state["serato_source"]).encode())
                                 if state["serato_source"] else None))
        verification = self.verify(state)
        overview = None
        if mode == "prep":
            library_key = canonical(state["library_additions"])
            if self._overview is None or self._overview[0] != library_key:
                self._overview = (library_key, set_planning.duplicate_report(
                    self._library_snapshot(state)[0], song_identity))
            overview = self._overview[1]
        return {
            "mode": mode, "revision": state["revision"], "event_name": self.event.get("event_name", "Demo wedding"),
            "phase": result.phase, "requests": rows,
            "needs_review": [row for row in rows if row["needs_review"]],
            "recommendations": recommendations,
            "acquisition_queue": (
                record_pools.acquisition_rows(rows, state["acquisition_progress"])
                + self._seed_acquisitions(seed_rows, state) if mode == "prep" else []),
            "serato_source": source, "seed_rows": seed_rows, "crate_entries": crate_entries,
            "prep_mode": state["prep_mode"], "out_of_pool": out_of_pool,
            "planning": set_planning.summary(crate_entries, source),
            "library_overview": overview,
            "saved": saved, "saved_current": saved_current, "verification": verification,
            "prep_done": bool(
                saved_current
                and (mode != "prep" or not any(row["needs_review"] for row in rows))
                and (mode != "prep" or not out_of_pool)
                and state["exported"] == verification.get("export_sha256")
                and verification["mismatches"] == 0
            ),
        }

    def _seed_candidates(self, entry, state):
        if not entry["title"] or not entry["artist"]:
            return "missing_metadata", []
        key = canonical((entry["title"], entry["artist"], entry.get("version"), state["library_additions"]))
        if key not in self._seed_resolution_cache:
            tracks, prepared = self._library_snapshot(state)
            result = rank_prep_crate(
                SetlistContext(must_play=(SongRequest(title=entry["title"], artist=entry["artist"],
                                                      version=entry.get("version")),)),
                prepared, limit=len(tracks))
            diagnostic = result.resolution_diagnostics[0]
            candidates = [
                {**clean_track(item.track), "version_label": version_label(item.track)}
                for item in result if item.song_identity == diagnostic.identity
            ] if diagnostic.status == "matched" else []
            if len(self._seed_resolution_cache) >= serato_import.MAX_MEMBERSHIPS:
                self._seed_resolution_cache.clear()
            self._seed_resolution_cache[key] = diagnostic.status, candidates
        return self._seed_resolution_cache[key]

    def _seed_acquisitions(self, rows, state):
        requests, labels = [], {}
        for index, row in enumerate(rows):
            if (row["status"] == "missing_library_file" and row["title"] and row["artist"]
                    and row["reconnect_status"] in {"missing", "version_unavailable"}):
                key = "seed:" + row["id"]
                requests.append({"key": key, "request": {"title": row["title"], "artist": row["artist"],
                                                         "version": row.get("version")},
                                 "needs_review": True, "status": "version_unavailable"
                                 if row["reconnect_status"] == "version_unavailable" else "missing"})
                labels[key] = f"Imported entry {index + 1}"
        return [{**row, "source_label": labels[row["key"]]} for row in
                record_pools.acquisition_rows(requests, state["acquisition_progress"])]

    def _seed(self, source, recommendations, state):
        if source is None:
            return [], recommendations
        files = {item["track"]["id"]: item for item in recommendations}
        rows, entries = [], []
        source_entries = {entry["id"]: entry for entry in source["entries"]}
        for entry_id in source["order"]:
            entry = source_entries[entry_id]
            replacement_id = source.get("replacements", {}).get(entry_id)
            item = files.get(replacement_id or entry["file_id"])
            status = entry["status"] if entry["status"] != "available" else (
                "available" if item else "blocked_or_version_conflict"
            )
            reconnect_status, candidates = None, []
            if entry["status"] == "missing_library_file":
                reconnect_status, candidates = self._seed_candidates(entry, state)
                candidates = [candidate for candidate in candidates if candidate["id"] in files]
            if replacement_id:
                status = "available" if item else "blocked_or_version_conflict"
            rows.append({**entry, "status": status, "reconnect_status": reconnect_status,
                         "replacement": item["track"] if replacement_id and item else None,
                         "planning": source.get("planning", {}).get(entry_id, {"phase": "unassigned", "pinned": False}),
                         "reconnect_candidates": candidates if not replacement_id else []})
            if status == "available":
                entries.append({**item, "seed_entry_id": entry_id,
                                "reasons": (*item["reasons"], "Added by DJ" if entry["position"] is None
                                            else "Imported starting set" if source["source"].get("format") in {"csv", "m3u", "catalog"}
                                            else "Imported Serato playlist",
                                            *(("Reconnected by DJ",) if replacement_id else ()))})
        # Matched event requests and cues preserve imported membership order.
        identities = {song_identity(item["track"]["title"], item["track"]["artist"]) for item in entries}
        for item in recommendations:
            identity = song_identity(item["track"]["title"], item["track"]["artist"])
            if ({"requested", "customer"} & set(item["badges"])) and identity not in identities:
                entries.append(item)
                identities.add(identity)
        return rows, entries

    def view(self, mode):
        if mode not in {"prep", "live"}:
            raise ValueError("Mode must be prep or live.")
        with self.lock:
            return self._view(mode, self.state)

    def sequence_preview(self, body):
        if not isinstance(body, dict) or set(body) != {"base_revision"}:
            raise ValueError("Sequence preview requires base_revision.")
        with self.lock:
            if type(body["base_revision"]) is not int or body["base_revision"] != self.state["revision"]:
                raise FlowConflict("Session changed. Reload before previewing a sequence.")
            if self.state["prep_mode"] != "seed" or not self.state["serato_source"]:
                raise FlowConflict("Accept a starting set and choose its ordered mode first.")
            view = self._view("prep", self.state)
            if any(row["status"] != "available" for row in view["seed_rows"]):
                raise FlowConflict("Resolve unavailable imported memberships before sequencing.")
            source = self.state["serato_source"]
            order = set_planning.sequence(source, view["recommendations"])
            return {"id": digest(canonical({"revision": self.state["revision"], "order": order}).encode()),
                    "order": order, "rows": [next(row for row in view["seed_rows"] if row["id"] == key)
                                             for key in order],
                    "note": "Phase grouping and shared-engine rank, with pinned positions retained. Not a beat-matched performance mix."}

    def acquisition_preview(self, body):
        if not isinstance(body, dict) or set(body) != {"key", "track", "base_revision"}:
            raise ValueError("A reconciliation preview needs key, track and base_revision.")
        with self.lock:
            if type(body["base_revision"]) is not int or body["base_revision"] != self.state["revision"]:
                raise FlowConflict("Session changed. Reload before previewing the file.")
            before = self._view("prep", self.state)
            row = next((row for row in before["acquisition_queue"] if row["key"] == body["key"]), None)
            if row is None:
                raise FlowConflict("This acquisition request is no longer pending.")
            track = self._acquisition_track(body["track"])
            tracks = self._library_snapshot(self.state)[0]
            if len(self.state["library_additions"]) >= 100:
                raise ValueError("This session supports at most 100 acquired records.")
            if any(item["id"] == track["id"] or item.get("path") == track["path"] for item in tracks):
                raise FlowConflict("That file ID or path is already in the loaded library.")
            draft = copy.deepcopy(self.state)
            draft["library_additions"].append(track)
            after = self._view("prep", draft)
            resolved = next((item for item in after["requests"] if item["key"] == body["key"]), None)
            if resolved is None:
                seed = next(item for item in after["seed_rows"] if "seed:" + item["id"] == body["key"])
                candidates = seed["reconnect_candidates"]
                resolved = {"status": "ready_to_reconnect" if candidates else seed["reconnect_status"],
                            "note": "Imported membership requires an explicit Reconnect file decision.",
                            "fulfilled": False, "candidates": candidates, "selected_file_id": None}
            changes = [
                {"title": current["request"]["title"], "before": previous["status"],
                 "after": current["status"], "selected_file_id": current["selected_file_id"]}
                for previous, current in zip(before["requests"], after["requests"])
                if (previous["status"], previous["selected_file_id"], previous["candidates"])
                != (current["status"], current["selected_file_id"], current["candidates"])
            ]
            for previous, current in zip(before["seed_rows"], after["seed_rows"]):
                if previous["reconnect_candidates"] != current["reconnect_candidates"]:
                    changes.append({"title": current["title"], "before": previous["reconnect_status"],
                                    "after": "ready_to_reconnect" if current["reconnect_candidates"]
                                    else current["reconnect_status"], "selected_file_id": None})
            return {"id": digest(canonical(body).encode()), "track": track,
                    "status": resolved["status"], "note": resolved["note"],
                    "fulfilled": resolved["fulfilled"], "candidates": resolved["candidates"],
                    "selected_file_id": resolved["selected_file_id"],
                    "recommendation_count": len(after["recommendations"]),
                    "changes": changes,
                    "note_scope": "Synthetic metadata only; no audio file was inspected. Saved Prep stays unchanged."}

    def _acquisition_track(self, value):
        return acquisition.synthetic_track(value)

    def serato_preview(self, body):
        with self.lock:
            return serato_import.preview(body, self._library_snapshot(self.state)[0])

    def verify(self, state=None):
        state = self.state if state is None else state
        if not state["saved"] or not self.saved_path.exists() or not self.export_path.exists():
            return {"mismatches": None, "note": "Save and export the Prep crate to verify."}
        expected_saved = (canonical(state["saved"]) + "\n").encode()
        expected_export = playlist(state["saved"]["entries"])
        mismatches = int(self.saved_path.read_bytes() != expected_saved)
        actual_export = self.export_path.read_bytes()
        mismatches += int(actual_export != expected_export)
        return {"mismatches": mismatches, "note": f"Verify: {mismatches} mismatches",
                "saved_sha256": digest(expected_saved), "export_sha256": digest(actual_export)}

    def command(self, body):
        if not isinstance(body, dict):
            raise ValueError("Expected a JSON command.")
        request_id = body.get("request_id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
            raise ValueError("A request_id is required.")
        with self.lock:
            previous = self.state["receipts"].get(request_id)
            if previous:
                if previous not in {canonical(body), "sha256:" + digest(canonical(body).encode())}:
                    raise FlowConflict("Request ID was reused with different content.")
                return self.view(body.get("mode", "prep"))
            if type(body.get("base_revision")) is not int or body["base_revision"] != self.state["revision"]:
                raise FlowConflict("Session changed. Reload before trying again.")
            mode = body.get("mode", "prep")
            if mode not in {"prep", "live"}:
                raise ValueError("Mode must be prep or live.")
            state = copy.deepcopy(self.state)
            action = body.get("action")
            fields = {
                "confirm": {"key", "file_id"}, "dismiss": {"key"},
                "guest": {"first_name", "email", "song"}, "played": {"file_id"},
                "save": set(), "export": set(),
                "serato_import": {"upload", "preview_id"},
                "seed_remove": {"entry_id"}, "seed_move": {"entry_id", "direction"},
                "seed_add": {"file_id"},
                "seed_reconnect": {"entry_id", "file_id"},
                "acquisition_progress": {"key", "progress"},
                "acquisition_accept": {"key", "track", "preview_id"},
                "prep_mode": {"value"}, "seed_plan": {"entry_id", "phase", "pinned"},
                "phase_target": {"phase", "minutes"}, "sequence_accept": {"preview_id"},
                "portable_import": {"upload", "preview_id"},
            }
            if action not in fields or set(body) - (
                {"request_id", "base_revision", "mode", "action"} | fields[action]
            ):
                raise ValueError("Unknown workflow action or fields.")
            if ((action in {"guest", "played"} and mode != "live")
                    or (action in {"save", "export", "serato_import", "seed_remove", "seed_move", "seed_add",
                                   "acquisition_progress", "acquisition_accept", "seed_reconnect",
                                   "prep_mode", "seed_plan", "phase_target", "sequence_accept", "portable_import"} and mode != "prep")):
                raise ValueError("Guest/played actions are Live-only; save/export actions are Prep-only.")
            if action == "prep_mode":
                if body.get("value") not in set_planning.MODES:
                    raise ValueError("Choose seed, source or library mode.")
                if body["value"] in {"seed", "source"} and not state["serato_source"]:
                    raise FlowConflict("Accept an imported starting set first.")
                state["prep_mode"] = body["value"]
            elif action in {"seed_plan", "phase_target", "sequence_accept"}:
                source = state["serato_source"]
                if not source or state["prep_mode"] != "seed":
                    raise FlowConflict("Planning requires an imported set in ordered starting-set mode.")
                if action == "sequence_accept":
                    preview = self.sequence_preview({"base_revision": state["revision"]})
                    if preview["id"] != body.get("preview_id"):
                        raise FlowConflict("Sequence changed. Preview it again.")
                    source["order"] = preview["order"]
                elif action == "phase_target":
                    minutes = body.get("minutes")
                    if body.get("phase") not in set_planning.PHASES or (
                        type(minutes) not in (int, float) or not 0 <= minutes <= 1440):
                        raise ValueError("Choose a planning phase and a finite target between 0 and 1440 minutes.")
                    source.setdefault("targets", {})[body["phase"]] = minutes
                else:
                    if (body.get("entry_id") not in source["order"]
                            or body.get("phase") not in set_planning.PHASES
                            or type(body.get("pinned")) is not bool):
                        raise ValueError("Choose a current membership, phase and explicit pinned boolean.")
                    source.setdefault("planning", {})[body["entry_id"]] = {
                        "phase": body["phase"], "pinned": body["pinned"]}
            elif action == "acquisition_progress":
                queue = self._view("prep", state)["acquisition_queue"]
                if not any(row["key"] == body.get("key") for row in queue):
                    raise FlowConflict("This acquisition request is no longer pending.")
                if not isinstance(body.get("progress"), str) or body["progress"] not in acquisition.PROGRESS:
                    raise ValueError("Choose not_started, searching or acquired acquisition progress.")
                state["acquisition_progress"][body["key"]] = body["progress"]
            elif action == "acquisition_accept":
                preview = self.acquisition_preview({
                    "key": body.get("key"), "track": body.get("track"),
                    "base_revision": body["base_revision"],
                })
                if preview["id"] != body.get("preview_id"):
                    raise FlowConflict("Reconciliation preview changed; preview the file again.")
                state["library_additions"].append(preview["track"])
            elif action in {"serato_import", "portable_import"}:
                adapter = serato_import if action == "serato_import" else set_intake
                source = (self.serato_preview(body.get("upload")) if action == "serato_import"
                          else adapter.preview(body.get("upload"), self._library_snapshot(state)[0]))
                if source["id"] != body.get("preview_id"):
                    raise FlowConflict("Serato preview changed; preview the selected snapshots again.")
                source["order"] = [entry["id"] for entry in source["entries"]]
                state["serato_source"] = source
                state["prep_mode"] = "seed"
            elif action in {"seed_remove", "seed_move", "seed_add", "seed_reconnect"}:
                source = state["serato_source"]
                if source is None:
                    raise FlowConflict("Import and accept a Serato snapshot first.")
                if action == "seed_reconnect":
                    view = self._view("prep", state)
                    row = next((row for row in view["seed_rows"] if row["id"] == body.get("entry_id")), None)
                    if not row or row["status"] != "missing_library_file":
                        raise FlowConflict("This imported membership is no longer waiting for a file.")
                    if body.get("file_id") not in {track["id"] for track in row["reconnect_candidates"]}:
                        raise ValueError("Choose the shared-engine exact-version reconnect candidate.")
                    source.setdefault("replacements", {})[row["id"]] = body["file_id"]
                elif action == "seed_add":
                    if len(source["order"]) >= serato_import.MAX_MEMBERSHIPS:
                        raise ValueError("Working sets support at most 1000 memberships.")
                    view = self._view("prep", state)
                    item = next((item for item in view["recommendations"]
                                 if item["track"]["id"] == body.get("file_id")), None)
                    if item is None:
                        raise ValueError("Choose an eligible recommendation from the shared engine.")
                    track = item["track"]
                    entry_id = "added:" + str(track["id"])
                    if entry_id in source["order"]:
                        raise FlowConflict("That added file is already in the working set.")
                    if not any(entry["id"] == entry_id for entry in source["entries"]):
                        source["entries"].append({"id": entry_id, "position": None, "song_id": None,
                            "raw_path": track["path"], "path": track["path"], "title": track["title"],
                            "artist": track["artist"], "status": "available", "file_id": track["id"]})
                    source["order"].append(entry_id)
                else:
                    entry_id = body.get("entry_id")
                    if entry_id not in source["order"]:
                        raise FlowConflict("Working-set membership changed; reload before editing.")
                    position = source["order"].index(entry_id)
                    if action == "seed_remove":
                        source["order"].remove(entry_id)
                    else:
                        if source.get("planning", {}).get(entry_id, {}).get("pinned"):
                            raise FlowConflict("Unpin this membership before moving it.")
                        direction = body.get("direction")
                        if direction not in {"up", "down"}:
                            raise ValueError("Move direction must be up or down.")
                        target = position + (-1 if direction == "up" else 1)
                        if not 0 <= target < len(source["order"]):
                            raise ValueError("Membership is already at the edge of the working set.")
                        if source.get("planning", {}).get(source["order"][target], {}).get("pinned"):
                            raise FlowConflict("Unpin the adjacent membership before moving across it.")
                        source["order"][position], source["order"][target] = source["order"][target], entry_id
            elif action in {"confirm", "dismiss"}:
                view = self._view(mode, state)
                row = next((row for row in view["requests"] if row["key"] == body.get("key")), None)
                if not row or not row["needs_review"]:
                    raise FlowConflict("This review row is no longer pending.")
                decision = {"action": action}
                if action == "confirm":
                    if not row["can_confirm"] or body.get("file_id") not in {t["id"] for t in row["candidates"]}:
                        raise ValueError("Choose an available, unblocked candidate from this review row.")
                    decision["file_id"] = body["file_id"]
                state[mode + "_decisions"][row["key"]] = decision
            elif action == "guest":
                first_name, email, song = (body.get(key, "") for key in ("first_name", "email", "song"))
                if (not all(isinstance(value, str) for value in (first_name, email, song))
                        or not first_name.strip() or not song.strip()
                        or len(first_name) > 80 or len(email) > 254 or len(song) > 200):
                    raise ValueError("Enter a first name and a song; email is optional.")
                if len(state["guests"]) >= 100:
                    raise ValueError("This session supports up to 100 guest requests.")
                state["guests"].append({
                    "id": request_id, "title": song.strip(),
                    "metadata": {"first_name": first_name.strip(), "email": email.strip()},
                })
            elif action == "played":
                track = next((track for track in self._library_snapshot(state)[0]
                              if track["id"] == body.get("file_id")), None)
                if track is None:
                    raise ValueError("Choose a loaded library file.")
                state["played"].append({"title": track["title"], "artist": track.get("artist")})
            elif action == "save":
                prep = self._view("prep", state)
                if prep["needs_review"]:
                    raise FlowConflict("Confirm or dismiss every Needs review row before saving.")
                if prep["out_of_pool"]:
                    raise FlowConflict("Matched event requests are outside this source. Choose full library or ordered starting-set mode.")
                if state["prep_mode"] == "seed" and any(row["status"] != "available" for row in prep["seed_rows"]):
                    raise FlowConflict("Remove unavailable or blocked imported entries before saving.")
                state["saved"] = {"entries": prep["crate_entries"],
                                  "decisions": copy.deepcopy(state["prep_decisions"])}
                if state["prep_mode"] != "seed":
                    state["saved"]["prep_mode"] = state["prep_mode"]
                if state["library_additions"]:
                    state["saved"]["library_revision"] = digest(canonical(state["library_additions"]).encode())
                if state["serato_source"]:
                    state["saved"]["seed_revision"] = digest(canonical(state["serato_source"]).encode())
                    state["saved"]["source"] = state["serato_source"]["source"]
                    if state["serato_source"].get("planning") or state["serato_source"].get("targets"):
                        state["saved"]["planning"] = copy.deepcopy(state["serato_source"].get("planning", {}))
                        state["saved"]["targets"] = copy.deepcopy(state["serato_source"].get("targets", {}))
                state["exported"] = None
                self._write(self.saved_path, (canonical(state["saved"]) + "\n").encode())
            elif action == "export":
                if not state["saved"]:
                    raise FlowConflict("Save the Prep crate before exporting.")
                if self.saved_path.read_bytes() != (canonical(state["saved"]) + "\n").encode():
                    raise FlowConflict("Saved crate integrity check failed; save again before export.")
                data = playlist(state["saved"]["entries"])
                self._write(self.export_path, data)
                state["exported"] = digest(data)
            else:
                raise ValueError("Unknown workflow action.")
            state["revision"] += 1
            state["receipts"][request_id] = (
                "sha256:" + digest(canonical(body).encode()) if action in {"serato_import", "portable_import"} else canonical(body))
            result = self._view(mode, state)
            self._write(self.state_path, (canonical(state) + "\n").encode())
            self.state = state
            return result
