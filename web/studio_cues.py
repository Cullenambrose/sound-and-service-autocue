"""Read existing app analysis by whole-file hash without probing source media."""
import json

from timing import bar_to_time
if __package__:
    from . import catalog
else:
    import catalog


def analysis_evidence(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tracks'").fetchone():
        return {}
    rows = db.execute(
        """SELECT DISTINCT t.hash,t.plan_json,t.reviewed,t.review_revision,t.error
           FROM tracks t JOIN library_files f ON f.content_hash=t.hash
           WHERE t.plan_json IS NOT NULL ORDER BY t.hash"""
    ).fetchall()
    result = {}
    for row in rows:
        plan = json.loads(row["plan_json"])
        if not isinstance(plan, dict) or not isinstance(plan.get("cues", {}), dict):
            raise ValueError("Invalid stored cue plan for hash " + row["hash"] + "; review it in the main app.")
        cues = {}
        for slot, cue in plan.get("cues", {}).items():
            if not isinstance(cue, dict):
                raise ValueError("Invalid stored cue slot for hash " + row["hash"])
            seconds = cue.get("time_s")
            issue = None
            if seconds is None and cue.get("bar") is not None:
                try:
                    seconds = bar_to_time(cue["bar"], plan.get("grid", {}))
                except (ValueError, TypeError, AttributeError) as error:
                    issue = "Cue time cannot be resolved: " + str(error)
            cues[slot] = {key: cue.get(key) for key in
                         ("name", "meaning", "confidence", "flags", "reason", "mix_window_evidence")}
            cues[slot].update(time_s=seconds, timing_issue=issue)
        evidence = {
            "content_hash": row["hash"], "reviewed": bool(row["reviewed"]),
            "review_revision": row["review_revision"], "analysis_digest": catalog.digest(plan),
            "error": row["error"], "cues": cues,
            "flags": plan.get("flags", []), "grid_diagnostics": plan.get("grid_diagnostics"),
            "mix_window_evidence": plan.get("mix_window_evidence"),
            "outro_vocal_evidence": plan.get("outro_vocal_evidence"),
            "note": "Existing analysis matched by indexed SHA-256, not fresh audio verification. "
                    "Candidate stem windows are not audible-clean, phrase or loop-seam guarantees.",
        }
        # Reject malformed/nonfinite stored evidence rather than hide a bad plan.
        catalog.canonical(evidence)
        result[row["hash"]] = evidence
    return result
