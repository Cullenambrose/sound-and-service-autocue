"""Generated selected-file pilot benchmark; no real media or library discovery."""
import argparse
import copy
import cProfile
import http.client
import io
import json
import math
from pathlib import Path
import pstats
import statistics
import threading
import time

from mutagen.id3 import TIT2, TPE1
from mutagen.wave import WAVE

from .fixtures import tagged_wav
from .read_only_prep import ReadOnlyPrepFlow
from .setlist_demo import OUTPUTS, demo_server


def measure(directory, count, samples=200, seconds=2, fixtures=None, prepare_only=False):
    directory.mkdir()
    paths = []
    start = time.perf_counter()
    template = tagged_wav(seconds=seconds)
    source = fixtures or directory
    for index in range(count):
        stream = io.BytesIO(template)
        audio = WAVE(stream)
        audio.tags.add(TIT2(encoding=3, text=[f"Generated Song {index}"]))
        audio.tags.add(TPE1(encoding=3, text=[f"Generated Artist {index}"]))
        audio.save(stream)
        path = source / f"generated-{index}.wav"
        if fixtures is None:
            path.write_bytes(stream.getvalue())
        paths.append(str(path))
    acquired = source / "acquired.wav"
    if fixtures is None:
        acquired.write_bytes(tagged_wav("Dirty", seconds=seconds))
    generated_ms = (time.perf_counter() - start) * 1000
    if prepare_only:
        return {"prepared": True, "tracks": count, "generation_ms": generated_ms}
    selection = {"schema_version": 1, "library_files": paths, "acquisition_files": [str(acquired)]}
    event = {"music_lists": {"must_play": [
        {"title": "Synthetic Song", "artist": "Invented Artist", "version": "Dirty"}]}}
    start = time.perf_counter()
    flow = ReadOnlyPrepFlow(directory / "session", selection=selection, event=event)
    startup_ms = (time.perf_counter() - start) * 1000
    profile = cProfile.Profile()
    baseline = copy.deepcopy(flow.state)
    server = demo_server(flow)
    thread = threading.Thread(target=lambda: profile.runcall(server.serve_forever))
    thread.start()
    # Request handlers run in their own threads; profile the command there too.
    original = flow.command
    command_profile = cProfile.Profile()
    flow.command = lambda body: command_profile.runcall(original, body)
    connection = http.client.HTTPConnection(*server.server_address, timeout=60)
    times = []
    cold = None
    try:
        connection.request("GET", "/api/session")
        response = connection.getresponse()
        token = json.loads(response.read())["token"]
        for index in range(samples + 2):
            with flow.lock:
                flow.state = copy.deepcopy(baseline)
            body = {"request_id": f"generated-{index}", "base_revision": 0, "mode": "live",
                    "action": "guest", "first_name": "Generated", "song": "Generated Song 0"}
            start = time.perf_counter()
            connection.request("POST", "/api/command", json.dumps(body),
                               {"X-Setlist-Token": token, "Content-Type": "application/json"})
            response = connection.getresponse()
            result = json.loads(response.read())
            elapsed = (time.perf_counter() - start) * 1000
            if response.status != 200 or not result["requests"][-1]["fulfilled"]:
                for track in flow.library:
                    info = Path(track["path"]).stat()
                    actual = {"device": info.st_dev, "inode": info.st_ino, "size": info.st_size,
                              "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns}
                    changed = {key: (track["verification"][key], value) for key, value in actual.items()
                               if track["verification"][key] != value}
                    if changed:
                        print("CHANGED_GENERATED_FILE", track["path"], changed, flush=True)
                raise ValueError(f"Benchmark request did not fulfill: {result}")
            if index == 0:
                cold = elapsed
            elif index > 1:
                times.append(elapsed)
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join()
        with (directory / "profile.txt").open("w") as stream:
            pstats.Stats(command_profile, stream=stream).sort_stats("cumulative").print_stats(30)
    flow.command = original
    flow.state = baseline
    row = flow.view("prep")["acquisition_queue"][0]
    body = {"key": row["key"], "track": {"path": str(acquired)}, "base_revision": 0}
    start = time.perf_counter()
    preview = flow.acquisition_preview(body)
    preview_ms = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    flow.command({"mode": "prep", "action": "acquisition_accept", "request_id": "acquired",
                  **body, "preview_id": preview["id"]})
    accept_ms = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    ReadOnlyPrepFlow(directory / "session", selection=selection, event=event).view("live")
    restart_ms = (time.perf_counter() - start) * 1000
    report = {"tracks": count, "samples": samples, "warmup": 1, "format": "generated tagged WAV",
              "selected_bytes": sum(Path(path).stat().st_size for path in paths),
              "acquired_bytes": acquired.stat().st_size, "generation_ms": generated_ms,
              "startup_ms": startup_ms, "restart_ms": restart_ms, "preview_ms": preview_ms,
              "accept_ms": accept_ms, "cold_ms": cold, "p50_ms": statistics.median(times),
              "p95_ms": sorted(times)[math.ceil(samples * .95) - 1],
              "p99_ms": sorted(times)[math.ceil(samples * .99) - 1], "max_ms": max(times),
              "milliseconds": times, "target_ms": 200,
              "scope": "Profiled loopback HTTP guest POST, selected-file validation, rank, durable save, JSON and response read"}
    report["target_met"] = report["p95_ms"] < 200
    (directory / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tracks", type=int, choices=(100, 1000), default=1000)
    parser.add_argument("--seconds", type=int, choices=(2, 60), default=2)
    parser.add_argument("--fixtures", type=Path, help="Reuse only an earlier generated 06-readiness benchmark directory.")
    parser.add_argument("--prepare-only", action="store_true", help="Generate fixture containers and exit before profiling.")
    args = parser.parse_args()
    directory = args.output.absolute()
    if directory.parent.resolve() != OUTPUTS.resolve() or not directory.name.startswith("06-"):
        parser.error("Use a new 06-* output directory.")
    fixtures = args.fixtures.absolute() if args.fixtures else None
    if fixtures and (fixtures.parent.resolve() != OUTPUTS.resolve()
                     or not fixtures.name.startswith("06-readiness-") or fixtures.is_symlink()):
        parser.error("Reuse only a generated 06-readiness-* benchmark directory.")
    if args.prepare_only and fixtures:
        parser.error("Choose fixture generation or reuse, not both.")
    report = measure(directory, args.tracks, seconds=args.seconds, fixtures=fixtures, prepare_only=args.prepare_only)
    print(json.dumps({key: value for key, value in report.items() if key != "milliseconds"}, indent=2))
    raise SystemExit(0 if report.get("prepared") or report["target_met"] else 1)


if __name__ == "__main__":
    main()
