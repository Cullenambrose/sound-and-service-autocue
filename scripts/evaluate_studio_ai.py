"""Reproducible, synthetic-only local-model checks; never touches a user's library."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]
from web import catalog, library_operations, personal_crates, studio_ai, studio_assistant, studio_service
from web.fixtures import tagged_wav
from setlist.planning import validate_policy


def evaluate(model, cases=None):
    results = []
    def exact_rule(value, expected):
        return value["intent"]["rule"] == studio_service.validate_rule(expected)
    def exact_policy(value, expected):
        return value["intent"]["policy"] == validate_policy({
            "tolerance_minutes": 3, "max_tracks": 20, **expected})
    def check(name, run, predicate, *, reject=False):
        if cases and name not in cases:
            return
        started = time.monotonic()
        try:
            value = run()
        except (ValueError, RuntimeError) as error:
            results.append({"case": name, "passed": reject and "cannot enforce" in str(error),
                            "error": str(error), "seconds": round(time.monotonic() - started, 3)})
            if isinstance(error, studio_ai.UnsupportedRequest):
                results[-1]["rejection_source"] = "application-capability-check"
        else:
            results.append({"case": name, "passed": not reject and bool(predicate(value)),
                            "output": value, "seconds": round(time.monotonic() - started, 3)})

    check("exact-clean-house-bpm-limit",
          lambda: studio_ai.interpret("Build a crate of 20 tagged-clean House songs between 118 and 124 BPM.", model=model),
          lambda value: value["intent"]["action"] == "build" and exact_rule(value, {
              "genre": "House", "bpm_min": 118, "bpm_max": 124, "limit": 20, "clean_only": True}))
    check("never-played-and-energy",
          lambda: studio_ai.interpret("Search never-played songs with energy between 0.3 and 0.6.", model=model),
          lambda value: exact_rule(value, {"never_played": True, "energy_min": .3, "energy_max": .6}))
    check("reject-year-filter", lambda: studio_ai.interpret("Search clean songs released in the 1990s.", model=model),
          lambda value: False, reject=True)
    check("reject-delete-files", lambda: studio_ai.interpret("Permanently delete duplicate audio files.", model=model),
          lambda value: False, reject=True)
    check("simple-duration", lambda: studio_ai.interpret_plan("Plan a tagged-clean 30 minute dinner set.", model=model),
          lambda value: (exact_rule(value, {"clean_only": True}) and
                         exact_policy(value, {"target_minutes": 30})) or
          (exact_rule(value, {}) and exact_policy(value, {"target_minutes": 30, "clean_only": True})))
    check("ordered-phase-budgets", lambda: studio_ai.interpret_plan(
        "Plan a 30 minute set: 10 minutes cocktail followed by 20 minutes open dancing.", model=model),
        lambda value: exact_rule(value, {}) and exact_policy(value, {"target_minutes": 30, "phases": [
            {"phase": "cocktail", "target_minutes": 10, "tolerance_minutes": 1},
            {"phase": "open_dancing", "target_minutes": 20, "tolerance_minutes": 1}]}))
    for name, prompt, rule in (
        ("unseen-tempo-wording", "Find House songs faster than 116 BPM but no faster than 122 BPM.", {
            "genre": "House", "bpm_min": 116, "bpm_max": 122}),
        ("unseen-energy-zero", "Search songs with energy from 0 to 0.4.", {"energy_min": 0, "energy_max": .4}),
        ("unseen-artist-limit", "Build 7 songs by the artist Invented Artist.", {"artist": "Invented Artist", "limit": 7}),
    ):
        check(name, lambda prompt=prompt: studio_ai.interpret(prompt, model=model),
              lambda value, rule=rule: exact_rule(value, rule))
    check("unseen-missing-duration", lambda: studio_ai.interpret_plan("Plan a clean dinner set.", model=model),
          lambda value: False, reject=True)
    check("unseen-plan-duration-clean-genre", lambda: studio_ai.interpret_plan(
        "Make a clean House set lasting 24 minutes with a tolerance of 0 minutes, at most 8 songs.", model=model),
        lambda value: value["intent"]["rule"] == studio_service.validate_rule({"clean_only": True, "genre": "House"}) and
        exact_policy(value, {"target_minutes": 24, "tolerance_minutes": 0, "max_tracks": 8}))
    for name, prompt, rule in (
        ("heldout-clean-negation", "Find House tracks, including non-clean versions, with energy at most 0.2.",
         {"genre": "House", "clean_only": False, "energy_max": .2}),
        ("heldout-zero-and-count", "Build 9 songs with energy between 0 and 0.25.",
         {"limit": 9, "energy_min": 0, "energy_max": .25}),
        ("heldout-reject-unavailable-metadata", "Find songs released in 2007.", None),
    ):
        check(name, lambda prompt=prompt: studio_ai.interpret(prompt, model=model),
              lambda value, rule=rule: exact_rule(value, rule), reject=rule is None)
    check("heldout-single-set", lambda: studio_ai.interpret_plan(
        "Prepare a 17 minute dinner set with tagged-clean tracks, at most 6 songs, tolerance 2 minutes.", model=model),
        lambda value: exact_rule(value, {"clean_only": True}) and
        exact_policy(value, {"target_minutes": 17, "max_tracks": 6, "tolerance_minutes": 2}))
    check("heldout-plan-hours", lambda: studio_ai.interpret_plan(
        "Plan a 1.5 hour House set, at most 30 songs.", model=model),
        lambda value: exact_rule(value, {"genre": "House"}) and
        exact_policy(value, {"target_minutes": 90, "max_tracks": 30}))
    check("heldout-plan-nonclean", lambda: studio_ai.interpret_plan(
        "Plan a 21 minute House set including non-clean versions.", model=model),
        lambda value: exact_rule(value, {"genre": "House", "clean_only": False}) and
        exact_policy(value, {"target_minutes": 21}))
    check("heldout-plan-phase-energy", lambda: studio_ai.interpret_plan(
        "Plan 12 minutes cocktail with target energy 0.2, then 18 minutes dinner with target energy 0.4.",
        model=model),
        lambda value: exact_rule(value, {}) and exact_policy(value, {"target_minutes": 30, "phases": [
            {"phase": "cocktail", "target_minutes": 12, "tolerance_minutes": 1, "energy_target": .2},
            {"phase": "dinner", "target_minutes": 18, "tolerance_minutes": 1, "energy_target": .4}]}))
    check("heldout-plan-unspecified-time", lambda: studio_ai.interpret_plan(
        "Build me a dinner set of clean House tracks, no duration decided yet.", model=model),
        lambda value: False, reject=True)
    check("heldout-plan-unsupported-year", lambda: studio_ai.interpret_plan(
        "Plan 18 minutes of House songs released in 2007.", model=model),
        lambda value: False, reject=True)
    with tempfile.TemporaryDirectory(prefix="studio-synthetic-model-") as directory:
        root = Path(directory)
        music = root / "synthetic"
        music.mkdir()
        for index in range(3):
            (music / f"synthetic-{index}.wav").write_bytes(tagged_wav(seconds=2))
        db = sqlite3.connect(root / "state.sqlite3")
        db.row_factory = sqlite3.Row
        try:
            for module in (catalog, personal_crates, library_operations, studio_service, studio_assistant):
                module.initialize(db)
            catalog.scan_folder(db, str(music), lambda path: {
                "title": path.stem, "artist": "Invented evaluation artist", "version": "Clean",
                "bpm": 120, "genre": "House", "key": "8A", "duration_s": 180, "energy": .5})
            db.execute("INSERT INTO studio_ai_settings VALUES(1,?,1)", (model,))
            db.commit()
            body = {"dataset_id": "synthetic", "conversation_id": "synthetic", "base_revision": 0,
                    "message": "Build a tagged-clean House crate, BPM between 118 and 124, limit 20.",
                    "rule": {}, "share_evidence": True}
            for index, text in enumerate([body["message"], "Keep those filters but change the limit to 10.",
                                         "Remove only the maximum BPM restriction; keep everything else.",
                                         "Now include non-clean songs too, but keep the other filters."]):
                name = "grounded-conversation-" + str(index + 1)
                if cases and name not in cases:
                    continue
                if studio_assistant.conversation(db, "synthetic")["revision"] != index:
                    results.append({"case": name, "passed": False, "blocked": True,
                                    "error": "A preceding conversation turn was not saved.", "seconds": 0})
                    continue
                def turn(index=index, text=text):
                    with db:
                        return studio_assistant.chat(db, {**body, "request_id": str(index), "base_revision": index,
                                                          "message": text}, "synthetic")
                expected = {"genre": "House", "clean_only": index != 3, "bpm_min": 118,
                            "limit": 20 if index == 0 else 10}
                if index < 2:
                    expected["bpm_max"] = 124
                check(name, turn,
                      lambda value, expected=expected: value["turn"]["draft"]["rule"] == studio_service.validate_rule(expected))
        finally:
            db.close()
    request = urllib.request.Request("http://127.0.0.1:11434/api/show",
                                     data=json.dumps({"model": model}).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), studio_ai.NoRedirect()).open(request, timeout=30) as response:
        info = json.load(response)
    return {"model": model, "synthetic_only": True, "results": results,
            "passed": sum(result["passed"] for result in results), "total": len(results),
            "license_excerpt": info.get("license", "")[:400],
            "scope": "Small fixed contract checks, not musical/listening quality or production certification."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", nargs="+", help="Run named checks only; conversation refinements need their preceding turns.")
    args = parser.parse_args()
    report = evaluate(args.model, args.cases)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f'{args.model}: {report["passed"]}/{report["total"]} fixed semantic checks; {args.output}')
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
