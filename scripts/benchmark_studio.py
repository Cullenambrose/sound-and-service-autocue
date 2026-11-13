"""Bounded synthetic index benchmarks; no source audio or filesystem scans."""
import argparse
import json
import math
from pathlib import Path
import platform
import sqlite3
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]
from web import catalog, library_operations, personal_crates, studio_assistant, studio_ingest, studio_service
from setlist import PreparedLibrary, SetlistContext, SongRequest, rank_next_song
from setlist.planning import plan_set


def distribution(samples, target_ms):
    ordered = sorted(samples)
    return {"samples": len(samples), "milliseconds": samples,
            "p50_ms": statistics.median(samples),
            "p95_ms": ordered[math.ceil(len(samples) * .95) - 1],
            "max_ms": max(samples), "target_ms": target_ms,
            "target_met": ordered[math.ceil(len(samples) * .95) - 1] < target_ms}


def benchmark(count, samples=20):
    with tempfile.TemporaryDirectory(prefix="studio-synthetic-scale-") as directory:
        db = sqlite3.connect(Path(directory) / "index.sqlite3")
        db.row_factory = sqlite3.Row
        try:
            for module in (catalog, library_operations, personal_crates, studio_service, studio_ingest, studio_assistant):
                module.initialize(db)
            for root in range((count + 9999) // 10000):
                db.execute("INSERT INTO library_roots(id,path,device,inode) VALUES(?,?,1,?)",
                           (str(root), str(Path(directory) / f"synthetic-root-{root}"), root))
            db.executemany("INSERT INTO library_files VALUES(?,?,?,?,?,?,?,?,?,1)", (
                (str(index), str(index // 10000), f"synthetic-{index}.wav", f"{index:064x}", 123, 1, 1,
                 catalog.canonical({"title": f"Invented {index}", "artist": f"Invented artist {index % 200}",
                                    "version": "Clean", "genre": "House", "bpm": 120,
                                    "duration_s": 180, "key": "8A", "energy": .5}), "")
                for index in range(count)))
            db.commit()
            if count > studio_service.MAX_INDEXED_FILES:
                try:
                    studio_service.search(db, {})
                except ValueError as error:
                    if "at most 50,000 indexed files" not in str(error):
                        raise
                    return {"files": count, "supported": False, "error": str(error),
                            "explicit_bound_verified": True}
                raise AssertionError("Oversized index was silently admitted.")
            timings = {}
            for name, run in (
                ("inbox_initial_reconcile", lambda: studio_ingest.reconcile(db)),
                ("inbox_unchanged_reconcile", lambda: studio_ingest.reconcile(db)),
                ("bounded_inbox", lambda: studio_ingest.state(db)),
            ):
                started = time.monotonic()
                value = run()
                timings[name] = round(time.monotonic() - started, 4)
                if name == "bounded_inbox":
                    assert value["counts"]["new"] == count and len(value["items"]) == 100
                db.commit()
            search_samples = []
            for index in range(samples + 1):
                # Match HTTP's fresh connection, not the writer's provisional state.
                reader = sqlite3.connect(Path(directory) / "index.sqlite3")
                reader.row_factory = sqlite3.Row
                try:
                    rule = {"clean_only": True, "limit": 100, "sort": "bpm" if index % 2 else "title"}
                    if index % 3 == 0:
                        rule.update(genre="House", bpm_min=120, bpm_max=120)
                    started = time.perf_counter()
                    value = studio_service.search(reader, rule)
                    elapsed = (time.perf_counter() - started) * 1000
                    assert value["total"] == count and len(value["tracks"]) == 100
                    if index:
                        search_samples.append(elapsed)
                    else:
                        timings["indexed_search_cold"] = elapsed / 1000
                finally:
                    reader.close()
            records = [{"id": str(index), "title": f"Synthetic {index}", "artist": f"Artist {index}",
                        "version": "Clean", "path": f"/Synthetic/{index}.wav",
                        "duration_s": 180, "bpm": 120, "energy": .5, "key": "8A"}
                       for index in range(count)]
            prepared = PreparedLibrary(records)
            context = SetlistContext(guest_requests=(SongRequest("Synthetic 0", "Artist 0"),))
            live_samples = []
            for index in range(samples + 1):
                started = time.perf_counter()
                value = rank_next_song(context, prepared, limit=20)
                elapsed = (time.perf_counter() - started) * 1000
                assert any(item.track["id"] == "0" for item in value)
                if index:
                    live_samples.append(elapsed)
                else:
                    timings["ranking_cold"] = elapsed / 1000
            return {"files": count, "supported": True, "seconds": timings,
                    "warm_search": distribution(search_samples, 300),
                    "warm_next_song_engine_only": distribution(live_samples, 200),
                    "all_under_5_seconds": all(t < 5 for t in timings.values())}
        finally:
            db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=20, choices=range(20, 101))
    args = parser.parse_args()
    records = [{"id": str(index), "title": f"Synthetic {index}", "artist": f"Artist {index}",
                "version": "Clean", "path": f"/Synthetic/{index}.wav", "duration_s": 180,
                "bpm": 120, "energy": .5, "key": "8A"} for index in range(5000)]
    planning_samples = []
    for _ in range(3):
        started = time.perf_counter()
        value = plan_set(records, {"target_minutes": 180, "tolerance_minutes": 0, "max_tracks": 60})
        planning_samples.append((time.perf_counter() - started) * 1000)
        assert value["feasible"]
        for alternative in value["alternatives"]:
            assert len(alternative["tracks"]) == 60 and alternative["duration_s"] == 10800
            assert len({record["id"] for record in alternative["tracks"]}) == 60
    report = {"synthetic_only": True, "hardware": platform.machine(), "python": platform.python_version(),
              "runs": [benchmark(count, args.samples) for count in (10000, 50000, 100000)],
              "planning_5000_candidates_60_positions": {
                  **distribution(planning_samples, 5000), "diagnostics": value["diagnostics"]},
              "scope": "Repeated local synthetic measurements. Warm search includes selection and result copying "
                       "over fresh SQLite connections; next-song excludes HTTP/fsync/JSON transport. "
                       "Planner uses full-track lengths. Not real audio, concurrent load or musical quality."}
    supported = [run for run in report["runs"] if run["supported"]]
    report["release_targets_met"] = (
        all(run["warm_search"]["target_met"] and run["warm_next_song_engine_only"]["target_met"]
            for run in supported) and report["planning_5000_candidates_60_positions"]["target_met"])
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["release_targets_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
