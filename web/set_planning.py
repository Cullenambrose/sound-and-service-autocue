"""Display planning for exact memberships; ranking stays in the shared engine."""
import math

PHASES = ("unassigned", "ceremony", "dinner", "first_dance", "dancing")
MODES = ("seed", "source", "library")


def summary(entries, source):
    planning = (source or {}).get("planning", {})
    phases = {phase: {"known_seconds": 0, "unknown": 0, "entries": 0,
                      "target_minutes": (source or {}).get("targets", {}).get(phase)}
              for phase in PHASES}
    for entry in entries:
        phase = planning.get(entry.get("seed_entry_id"), {}).get("phase", "unassigned")
        row = phases[phase]
        row["entries"] += 1
        seconds = entry["track"].get("duration_s")
        if type(seconds) in (int, float) and math.isfinite(seconds) and seconds >= 0:
            row["known_seconds"] += seconds
        else:
            row["unknown"] += 1
    return phases


def sequence(source, recommendations):
    """Keep pinned positions, group remaining memberships by phase then engine order."""
    ranks = {item["track"]["id"]: index for index, item in enumerate(recommendations)}
    entries = {entry["id"]: entry for entry in source["entries"]}
    planning = source.get("planning", {})
    movable = [key for key in source["order"] if not planning.get(key, {}).get("pinned")]
    movable.sort(key=lambda key: (
        PHASES.index(planning.get(key, {}).get("phase", "unassigned")),
        ranks.get(source.get("replacements", {}).get(key) or entries[key]["file_id"], len(ranks)),
        source["order"].index(key),
    ))
    iterator = iter(movable)
    return [key if planning.get(key, {}).get("pinned") else next(iterator) for key in source["order"]]


def duplicate_report(tracks, identity):
    groups = {}
    for track in tracks:
        key = identity(track.get("title"), track.get("artist"))
        if key is None:
            continue
        groups.setdefault(key, []).append(track)
    duplicates = [sorted(items, key=lambda track: str(track["id"])) for key, items in sorted(groups.items())
                  if len(items) > 1]
    return {"tracks": len(tracks), "song_identities": len(groups),
            "multiple_file_songs": len(duplicates), "groups": duplicates[:20],
            "note": "Up to 20 song groups. Versions and exact files are not disposable duplicates. No cleanup actions."}
