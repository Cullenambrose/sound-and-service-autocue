#!/usr/bin/env python3
"""Read-only cue arithmetic audit. No audio access, model calls, database changes or Serato writes."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from cue_maps import phrasing_report


def audit_library(path):
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute("SELECT hash,name,plan_json FROM tracks ORDER BY hash").fetchall()
    results = []
    for digest, name, raw in rows:
        if not raw:
            results.append({"track": digest, "name": name, "status": "not_analyzed"})
            continue
        plan = json.loads(raw)
        report = phrasing_report(plan)
        report.update({"track": digest, "name": name, "version": plan.get("version"),
                       "phrasing_version": plan.get("phrasing_version"),
                       "status": "invalid" if report["errors"] else
                           "unversioned" if not plan.get("cue_map", {}).get("resolved") else "arithmetic_checked"})
        results.append(report)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True, help="Local library.sqlite3; opened read-only")
    args = parser.parse_args(argv)
    try:
        results = audit_library(args.library.expanduser())
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as error:
        parser.exit(2, f"Audit failed: {error}\n")
    counts = {status: sum(row["status"] == status for row in results)
              for status in ("invalid", "unversioned", "not_analyzed", "arithmetic_checked")}
    print(json.dumps({"scope": "arithmetic only; no clean-window or audio accuracy certification",
                      "counts": counts, "tracks": results}, indent=2))
    return 1 if counts["invalid"] or counts["unversioned"] or counts["not_analyzed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
