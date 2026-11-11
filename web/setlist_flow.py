"""Synthetic event workflow; matching, ranking and hard exclusions belong to setlist."""
import copy
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading

from library.matcher import extract_version_tags
from setlist import PreparedLibrary, context_from_event, rank_next_song, rank_prep_crate, song_identity


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
        raise ValueError("Every playlist entry needs an absolute, single-line synthetic path.")
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
        self._typed_diagnostics = {}
        self.lock = threading.RLock()
        self.state = {
            "revision": 0, "prep_decisions": {}, "live_decisions": {}, "played": [],
            "guests": copy.deepcopy(self.event.get("_demo_live_inputs", {}).get("guest_requests", [])),
            "saved": None, "exported": None, "receipts": {},
        }
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())
            if set(self.state) != {
                "revision", "prep_decisions", "live_decisions", "played", "guests",
                "saved", "exported", "receipts",
            }:
                raise ValueError("Unsupported synthetic session; use a new 05- output directory.")

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
                    track = next((track for track in self.library if track["id"] == decision["file_id"]), None)
                    if track is None:
                        raise FlowConflict("Confirmed synthetic file is no longer available.")
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
        return ranker(context, self.prepared_library, limit=len(self.library))

    def _view(self, mode, state):
        result = self._rank(mode, state)
        # Original diagnostics preserve exactly what was typed after a confirmation.
        event_key = canonical(self.event)
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
            raise ValueError("Confirmation changed the request list; reload the synthetic session.")
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
            {"song_identity": asdict(item.song_identity), "track": copy.deepcopy(clean_track(item.track)),
             "score": item.score, "reasons": item.reasons, "badges": item.badges,
             "version_label": version_label(item.track)}
            for item in result if item.song_identity not in held - fulfilled
        ]
        saved = state["saved"]
        verification = self.verify(state)
        return {
            "mode": mode, "revision": state["revision"], "event_name": self.event.get("event_name", "Demo wedding"),
            "phase": result.phase, "requests": rows,
            "needs_review": [row for row in rows if row["needs_review"]],
            "recommendations": recommendations,
            "saved": saved, "verification": verification,
            "prep_done": bool(
                saved and saved["decisions"] == state["prep_decisions"]
                and (mode != "prep" or not any(row["needs_review"] for row in rows))
                and state["exported"] == verification.get("export_sha256")
                and verification["mismatches"] == 0
            ),
        }

    def view(self, mode):
        if mode not in {"prep", "live"}:
            raise ValueError("Mode must be prep or live.")
        with self.lock:
            return self._view(mode, self.state)

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
                if previous != canonical(body):
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
            }
            if action not in fields or set(body) - (
                {"request_id", "base_revision", "mode", "action"} | fields[action]
            ):
                raise ValueError("Unknown workflow action or fields.")
            if ((action in {"guest", "played"} and mode != "live")
                    or (action in {"save", "export"} and mode != "prep")):
                raise ValueError("Guest/played actions are Live-only; save/export actions are Prep-only.")
            if action in {"confirm", "dismiss"}:
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
                    raise ValueError("This synthetic session supports up to 100 guest requests.")
                state["guests"].append({
                    "id": request_id, "title": song.strip(),
                    "metadata": {"first_name": first_name.strip(), "email": email.strip()},
                })
            elif action == "played":
                track = next((track for track in self.library if track["id"] == body.get("file_id")), None)
                if track is None:
                    raise ValueError("Choose a synthetic library file.")
                state["played"].append({"title": track["title"], "artist": track.get("artist")})
            elif action == "save":
                prep = self._view("prep", state)
                if prep["needs_review"]:
                    raise FlowConflict("Confirm or dismiss every Needs review row before saving.")
                state["saved"] = {"entries": prep["recommendations"],
                                  "decisions": copy.deepcopy(state["prep_decisions"])}
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
                raise ValueError("Unknown synthetic workflow action.")
            state["revision"] += 1
            state["receipts"][request_id] = canonical(body)
            result = self._view(mode, state)
            self._write(self.state_path, (canonical(state) + "\n").encode())
            self.state = state
            return result
