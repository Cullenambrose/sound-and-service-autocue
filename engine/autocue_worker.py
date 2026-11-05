"""Versioned single-job worker contract around the existing Python cue engine."""
import argparse
from contextlib import redirect_stdout
from importlib.metadata import PackageNotFoundError, version
import json
import math
import os
from pathlib import Path
import platform
import signal
import sys
import tempfile
from typing import Callable, Literal, TypedDict

import analyze
import cue_maps
import experiments
from project_config import engine_config
import rules_v6

SCHEMA_VERSION = 1
ENGINE_VERSION = "0.1.0"
PROFILES = ("local", "prepared")


class WorkerRequest(TypedDict, total=False):
    schema_version: int
    job_id: str
    source: str
    profile: Literal["local", "prepared"]
    artist: str
    title: str
    genre: str
    cache_dir: str
    config_path: str
    beats_path: str
    track_hash: str
    cue_maps: dict
    experiments: list[str]
    allow_lyrics: bool
    gemini: bool


class WorkerMessage(TypedDict):
    schema_version: int
    job_id: str
    sequence: int
    kind: str
    status: str
    data: dict


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def content_id(value):
    import hashlib
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def validate_request(request):
    if not isinstance(request, dict):
        raise ValueError("Worker request must be a JSON object.")
    if set(request) - set(WorkerRequest.__annotations__):
        raise ValueError("Unknown worker request fields: " + ", ".join(sorted(set(request) - set(WorkerRequest.__annotations__))))
    if type(request.get("schema_version")) is not int or request["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported worker request schema_version.")
    for key in ("job_id", "source", "profile"):
        if not isinstance(request.get(key), str) or not request[key].strip():
            raise ValueError(f"Worker request requires {key}.")
    if len(request["job_id"]) > 128:
        raise ValueError("Worker job_id exceeds 128 characters.")
    if request["profile"] not in PROFILES:
        raise ValueError("Worker profile must be local or prepared.")
    for key in ("artist", "title", "genre", "cache_dir", "config_path", "beats_path", "track_hash"):
        if key in request and (not isinstance(request[key], str) or (key.endswith("_path") and not request[key])):
            raise ValueError(f"Worker {key} must be a string.")
    for key in ("source", "cache_dir", "config_path", "beats_path"):
        if key in request and not Path(request[key]).expanduser().is_absolute():
            raise ValueError(f"Worker {key} must be an absolute path.")
    for key in ("allow_lyrics", "gemini"):
        if key in request and type(request[key]) is not bool:
            raise ValueError(f"Worker {key} must be a boolean.")
    if "cue_maps" in request:
        cue_maps.normalize_cue_maps(request["cue_maps"])
    experiments.normalize_enabled(request.get("experiments"))
    if request["profile"] == "prepared":
        if not request.get("beats_path"):
            raise ValueError("Prepared profile requires explicit beats_path observations.")
        if request.get("allow_lyrics") or request.get("gemini"):
            raise ValueError("Prepared profile cannot enable network lookups or Gemini.")
    elif "beats_path" in request:
        raise ValueError("beats_path requires the prepared profile.")
    canonical(request)


def load_beats(path):
    with Path(path).expanduser().open(encoding="utf-8") as source:
        record = json.load(source)
    if not isinstance(record, dict):
        raise ValueError("Beat observations must be an object.")
    result = {}
    for key, minimum in (("beats", 4), ("downbeats", 4)):
        values = record.get(key)
        if not isinstance(values, list) or len(values) < minimum:
            raise ValueError(f"Beat observations require at least {minimum} {key}.")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
               for v in values) or any(b <= a for a, b in zip(values, values[1:])):
            raise ValueError(f"{key} must contain finite, nonnegative, strictly increasing times.")
        result[key] = values
    return result


def dependency_versions():
    versions = {}
    for name in ("numpy", "scipy", "mutagen", "beat-this", "torch", "demucs", "allin1", "whisperx"):
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = None
    return versions


def run(request: WorkerRequest, on_message: Callable[[WorkerMessage], None] | None = None,
        cancelled: Callable[[], bool] | None = None):
    """Run one validated request. Errors and cancellation never return a completed plan."""
    validate_request(request)
    sequence = 0

    def emit(kind, status, data):
        nonlocal sequence
        sequence += 1
        if on_message:
            on_message({"schema_version": SCHEMA_VERSION, "job_id": request["job_id"],
                        "sequence": sequence, "kind": kind, "status": status, "data": data})

    def check_cancelled():
        if cancelled and cancelled():
            raise analyze.AnalysisCancelled("Analysis cancelled.")

    emit("job", "running", {"profile": request["profile"]})
    try:
        check_cancelled()
        source = Path(request["source"]).expanduser().resolve(strict=True)
        if not source.is_file():
            raise ValueError("Worker source must be a regular file.")
        digest = analyze.file_hash(source)
        if request.get("track_hash", digest) != digest:
            raise ValueError("Worker source does not match the requested track_hash.")
        profile = request["profile"]
        beats = load_beats(request["beats_path"]) if profile == "prepared" else None
        config_path = Path(request.get("config_path") or engine_config()).expanduser().resolve(strict=True)
        with config_path.open(encoding="utf-8") as config_source:
            config = json.load(config_source)
        if not isinstance(config, dict):
            raise ValueError("Engine configuration must be a JSON object.")
        if not isinstance(config.get("switches", {}), dict):
            raise ValueError("Engine configuration switches must be an object.")
        normalized_maps = cue_maps.normalize_cue_maps(request.get("cue_maps"))
        enabled = experiments.normalize_enabled(request.get("experiments", ["baseline"] if beats else None))
        if beats is not None and enabled != ["baseline"]:
            raise ValueError("Prepared profile supports only the rules-only baseline.")
        cache = Path(request["cache_dir"]).expanduser().resolve() if request.get("cache_dir") else analyze.cache_root()
        if beats is not None:
            cache = cache / "prepared-v1" / content_id(beats)
        effective = {**rules_v6.DEFAULT_CONFIG, **config}
        effective["switches"] = {**rules_v6.DEFAULT_CONFIG["switches"], **config.get("switches", {})}
        provenance = {
            "schema_version": SCHEMA_VERSION, "engine_version": ENGINE_VERSION,
            "track_hash": digest, "profile": profile,
            "settings_hash": content_id({"config": effective, "cue_maps": normalized_maps, "experiments": enabled,
                                         "engine_version": ENGINE_VERSION,
                                         "metadata": {key: request.get(key, "") for key in ("artist", "title", "genre")},
                                         "allow_lyrics": request.get("allow_lyrics", False),
                                         "gemini": request.get("gemini", False)}),
            "beats_hash": content_id(beats) if beats else None,
            "python": platform.python_version(), "dependencies": dependency_versions(),
        }

        def progress(step, state, detail):
            check_cancelled()
            emit("progress", state, {"step": step, "detail": detail})

        plan = analyze.analyze(
            source, request.get("artist", ""), request.get("title", ""), request.get("genre", ""),
            progress, cache, config_path,
            cue_maps=normalized_maps, experiments=enabled,
            gemini=request.get("gemini", False), enabled_analyzers=[] if beats else None,
            allow_lyrics=request.get("allow_lyrics", False), beats_record=beats, cancelled=cancelled,
            config_data=config,
        )
        check_cancelled()
        if analyze.file_hash(source) != digest:
            raise RuntimeError("Audio changed during the worker job; result identity is no longer valid.")
        plan["provenance"] = provenance
        result = {"schema_version": SCHEMA_VERSION, "job_id": request["job_id"],
                  "status": "completed", "track_hash": digest, "plan": plan}
        canonical(result)
    except analyze.AnalysisCancelled as error:
        emit("job", "cancelled", {"error": str(error)})
        raise
    except Exception as error:
        emit("job", "failed", {"error": str(error), "error_type": type(error).__name__})
        raise
    emit("job", "completed", {"track_hash": digest, "settings_hash": provenance["settings_hash"]})
    return result


def write_result(path, result, protected=()):
    target = Path(path).expanduser().resolve()
    if target.suffix.lower() != ".json" or target in {Path(p).expanduser().resolve() for p in protected}:
        raise ValueError("Result must be a .json file distinct from all job inputs.")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(canonical(result) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Report installed packages/tools without loading models.")
    execute = commands.add_parser("run", help="Run a schema-v1 request; emit JSONL job messages.")
    execute.add_argument("--request", required=True, type=Path)
    execute.add_argument("--out", required=True, type=Path)
    fixture = commands.add_parser("fixture", help="Analyze generated audio with explicit synthetic beats, offline.")
    fixture.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "check":
        print(canonical({"schema_version": SCHEMA_VERSION, "engine_version": ENGINE_VERSION,
                         "analyzers": analyze.environment_report(), "dependencies": dependency_versions()}))
        return 0
    was_cancelled = False

    def cancel(_signum, _frame):
        nonlocal was_cancelled
        was_cancelled = True

    previous_handlers = {sig: signal.signal(sig, cancel) for sig in (signal.SIGINT, signal.SIGTERM)}

    last_message = None

    def message(event):
        nonlocal last_message
        last_message = event
        if event["status"] != "completed":
            print(canonical(event), file=sys.__stdout__, flush=True)

    try:
        if args.command == "fixture":
            from worker_fixture import fixture_request
            with tempfile.TemporaryDirectory(prefix="autocue-fixture-") as directory:
                request = fixture_request(Path(directory))
                with redirect_stdout(sys.stderr):
                    result = run(request, message, lambda: was_cancelled)
                if was_cancelled:
                    raise analyze.AnalysisCancelled("Analysis cancelled before writing its result.")
                write_result(args.out, result)
        else:
            with args.request.open(encoding="utf-8") as source:
                request = json.load(source)
            # Analyzer libraries may print diagnostics; stdout is reserved for protocol messages.
            with redirect_stdout(sys.stderr):
                result = run(request, message, lambda: was_cancelled)
            protected = [args.request, request["source"], request.get("config_path") or engine_config()]
            if request.get("beats_path"):
                protected.append(request["beats_path"])
            if os.environ.get("AUTOCUE_PROJECT"):
                protected.append(os.environ["AUTOCUE_PROJECT"])
            if was_cancelled:
                raise analyze.AnalysisCancelled("Analysis cancelled before writing its result.")
            write_result(args.out, result, protected)
        print(canonical({**last_message, "data": {**last_message["data"],
                          "result_path": str(args.out.resolve())}}), flush=True)
        return 0
    except analyze.AnalysisCancelled as error:
        if last_message and last_message["status"] == "completed":
            print(canonical({**last_message, "status": "cancelled",
                             "data": {"error": str(error)}}), flush=True)
        print(str(error), file=sys.stderr)
        return 130
    except (ValueError, OSError, RuntimeError, ImportError) as error:
        if last_message and last_message["status"] == "completed":
            print(canonical({**last_message, "status": "failed",
                             "data": {"error": str(error), "error_type": type(error).__name__}}), flush=True)
        print(f"Worker failed: {error}", file=sys.stderr)
        return 1
    finally:
        for sig, previous in previous_handlers.items():
            signal.signal(sig, previous)


if __name__ == "__main__":
    raise SystemExit(main())
