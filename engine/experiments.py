"""Switchable algorithm experiments. Each one re-plans the same cached analysis and is stored beside the baseline.

An experiment prepares overrides for rules_v6.build_plan. It never edits the baseline plan, so the review page
can show every variant's cues side by side and the report can score them against the cues you grade.
"""
import math
import os
import shutil
import subprocess
from pathlib import Path

BASELINE = "baseline"

CATALOG = [
    {"id": "baseline", "label": "Baseline v6", "default": True, "heavy": False,
     "summary": "The current engine, unchanged."},
    {"id": "fuzzy_lyrics", "label": "Fuzzy lyric matching", "default": True, "heavy": False,
     "summary": "Matches mis-heard transcript words to lyrics by similarity, not exact spelling."},
    {"id": "kick_grid", "label": "Kick-snapped grid", "default": True, "heavy": False,
     "summary": "Shifts the bar grid onto the measured kick transients (20-200 Hz)."},
    {"id": "stem_energy", "label": "Stem energy sections", "default": True, "heavy": False,
     "summary": "Uses drums + bass + vocal stem energy for the energy-based chorus and outro fallbacks."},
    {"id": "roformer_stems", "label": "RoFormer vocals", "default": False, "heavy": True,
     "summary": "Separates vocals with a Mel-Band RoFormer model for cleaner vocal timing. Slow; needs audio-separator."},
]
IDS = [item["id"] for item in CATALOG]
ROFORMER_MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
ROFORMER_TIMEOUT_S = int(os.environ.get("AUTOCUE_ROFORMER_TIMEOUT", "900"))


class Unavailable(RuntimeError):
    """The experiment cannot run on this song; its message says why and how to fix it."""


def default_enabled():
    return [item["id"] for item in CATALOG if item["default"]]


def normalize_enabled(value):
    if value is None:
        return default_enabled()
    if not isinstance(value, list) or any(item not in IDS for item in value):
        raise ValueError("experiments must be a list of known experiment ids")
    return [BASELINE] + [item for item in IDS if item in value and item != BASELINE]


def _resample(values, source_hop, target_hop, count=None):
    import numpy as np
    values = np.asarray(values, dtype=float)
    if not len(values):
        return values
    count = count or int(len(values) * source_hop / target_hop)
    source_times = np.arange(len(values)) * source_hop
    return np.interp(np.arange(count) * target_hop, source_times, values)


def _fuzzy_lyrics(context):
    if not context.get("synced_lyrics"):
        raise Unavailable("no synced lyrics were found for this song")
    if not context.get("words"):
        raise Unavailable("no WhisperX word timings; install whisperx")
    config = {**context["config"], "fuzzy_lyrics": True}
    return {"config": config}


def _kick_grid(context):
    import numpy as np
    low, fps = context.get("low_energy_raw"), float(context.get("fps") or 50)
    downbeats = context["analysis"].get("downbeats") or []
    if low is None or len(downbeats) < 8:
        raise Unavailable("needs low-band energy and at least 8 downbeats")
    low = np.asarray(low, dtype=float)
    flux = np.maximum(np.diff(np.log1p(low), prepend=0.0), 0.0)
    offsets = []
    radius = int(round(0.12 * fps))
    for beat in downbeats:
        centre = int(round(beat * fps))
        if centre - radius < 0 or centre + radius >= len(flux):
            continue
        window = flux[centre - radius:centre + radius + 1]
        if window.max() <= 0:
            continue
        offsets.append((int(window.argmax()) - radius) / fps)
    if len(offsets) < 8:
        raise Unavailable("too few clear kick transients near downbeats")
    shift = float(np.median(offsets))
    spread = float(np.median(np.abs(np.asarray(offsets) - shift)))
    if spread > 0.04:
        raise Unavailable(f"kick offsets are inconsistent (spread {spread * 1000:.0f} ms)")
    analysis = dict(context["analysis"])
    grid = dict(analysis["grid"])
    grid["t1"] = round(grid["t1"] + shift, 4)
    grid["kick_shift_ms"] = round(shift * 1000, 1)
    analysis["grid"] = grid
    return {"analysis": analysis, "note": f"grid shifted {shift * 1000:+.0f} ms"}


def _stem_energy(context):
    import numpy as np
    stems, hop = context.get("stems"), float(context.get("hop_s") or 0.05)
    fps = float(context.get("fps") or 50)
    if not stems or not all(key in stems for key in ("drums", "bass", "vocals")):
        raise Unavailable("Demucs stem energies are unavailable; install demucs")
    count = len(context["energy"])
    mixed = sum(_resample(stems[key], hop, 1 / fps, count) for key in ("drums", "bass", "vocals"))
    return {"energy": np.asarray(mixed, dtype=float).tolist()}


def _separator_command():
    root = Path(__file__).resolve().parent.parent
    for candidate in (os.environ.get("AUTOCUE_SEPARATOR"), root / ".venv-separator" / "bin" / "audio-separator",
                      shutil.which("audio-separator")):
        if candidate and Path(candidate).exists():
            return str(candidate)
    raise Unavailable("audio-separator is not installed. Run ./setup.sh --separator "
                      "(or: python3 -m venv .venv-separator && .venv-separator/bin/pip install audio-separator)")


def _roformer_timeout(context):
    # About 2x real time on Apple Silicon (MPS) at overlap 2; allow generous headroom.
    return max(ROFORMER_TIMEOUT_S, int(float(context.get("duration_s") or 0) * 5))


def _roformer(context):
    import numpy as np
    command = _separator_command()
    paths = context.get("paths")
    if paths is None or context.get("decoded_wav") is None:
        raise Unavailable("no decoded audio to separate")
    target = paths.root / "work" / "roformer_vocals.wav"
    if not target.exists():
        output = paths.root / "work" / "roformer"
        output.mkdir(parents=True, exist_ok=True)
        done = subprocess.run([command, str(context["decoded_wav"]), "--model_filename", ROFORMER_MODEL,
                               "--single_stem", "Vocals", "--output_format", "WAV", "--output_dir", str(output),
                               "--mdxc_overlap", "2", "--mdxc_batch_size", "4"],
                              capture_output=True, text=True, timeout=_roformer_timeout(context))
        found = sorted(output.glob("*Vocals*.wav"))
        if done.returncode or not found:
            raise Unavailable("RoFormer failed: " + (done.stderr or done.stdout or "no vocal file").strip()[-300:])
        found[0].replace(target)
        shutil.rmtree(output, ignore_errors=True)
    import analyze
    data, rate = analyze.read_mono(target)
    step = int(round(float(context.get("hop_s") or 0.05) * rate))
    count = len(data) // step
    envelope = np.sqrt((data[:count * step].reshape(count, step) ** 2).mean(1))
    stems = dict(context.get("stems") or {})
    stems["vocals"] = envelope.tolist()
    return {"stems": stems}


RUNNERS = {
    "fuzzy_lyrics": _fuzzy_lyrics,
    "kick_grid": _kick_grid,
    "stem_energy": _stem_energy,
    "roformer_stems": _roformer,
}


def summarize(plan, label, detail=""):
    grid = plan.get("grid", {})
    return {
        "label": label, "status": "ok", "detail": detail, "map_type": plan.get("map_type"),
        "t1": grid.get("t1"), "bar_s": grid.get("bar_s"),
        "flags": plan.get("flags", []),
        "cues": {slot: {key: cue.get(key) for key in ("name", "bar", "time_s", "confidence", "reason", "meaning")}
                 for slot, cue in plan.get("cues", {}).items()},
    }


def run_variants(base_kwargs, context, baseline_plan, enabled, build_plan):
    """Return {id: summary}. `base_kwargs` are the exact build_plan arguments used for the baseline."""
    variants = {BASELINE: summarize(baseline_plan, "Baseline v6")}
    labels = {item["id"]: item["label"] for item in CATALOG}
    for variant in enabled:
        if variant == BASELINE or variant not in RUNNERS:
            continue
        try:
            overrides = dict(RUNNERS[variant](context))
            note = overrides.pop("note", "")
            kwargs = {**base_kwargs, **overrides}
            plan = build_plan(**kwargs)
            variants[variant] = summarize(plan, labels[variant], note)
        except Unavailable as reason:
            variants[variant] = {"label": labels[variant], "status": "unavailable", "detail": str(reason), "cues": {}}
        except Exception as error:
            variants[variant] = {"label": labels[variant], "status": "failed", "detail": str(error), "cues": {}}
    return variants


def bar_energy_table(stems, low_energy_raw, rms, hop_s, fps, grid, duration_s):
    """Per-bar energies saved with each song so later analysis does not need the audio again."""
    if not rms or grid["bar_s"] <= 0:
        return []
    bar_count = max(0, int(math.ceil((duration_s - grid["t1"]) / grid["bar_s"])))
    table = []

    def mean(values, step, bar):
        if not values:
            return None
        left = max(0, int((grid["t1"] + bar * grid["bar_s"]) / step))
        right = max(left + 1, int((grid["t1"] + (bar + 1) * grid["bar_s"]) / step))
        chunk = values[left:right]
        return round(float(sum(chunk) / len(chunk)), 5) if chunk else None

    for bar in range(max(0, int(math.ceil(-grid["t1"] / grid["bar_s"]))), bar_count):
        table.append({
            "bar": bar,
            "rms": mean(rms, 1 / fps, bar),
            "low": mean(low_energy_raw, 1 / fps, bar),
            "drums": mean((stems or {}).get("drums"), hop_s, bar),
            "bass": mean((stems or {}).get("bass"), hop_s, bar),
            "vocals": mean((stems or {}).get("vocals"), hop_s, bar),
        })
    return table


def _truth_time(cue, grid):
    if cue.get("bar") is None or grid.get("t1") is None:
        return None
    return float(grid["t1"]) + float(cue["bar"]) * float(grid["bar_s"])


def score_tracks(tracks):
    """Score every variant against the cues you reviewed.

    `tracks` is a list of dicts: {"name", "plan"} where plan["cues"] holds the saved (graded) cues and
    plan["variants"] holds each experiment's cues. A variant cue is right when it is within one beat of the
    saved cue, or when both are empty.
    """
    results, per_track = {}, []
    for track in tracks:
        plan = track["plan"]
        answer = plan.get("answer_key") or {"grid": plan.get("grid", {}), "cues": plan.get("cues", {})}
        grid = plan.get("grid", {})
        key_grid = answer.get("grid") or grid
        variants = plan.get("variants") or {}
        if not grid.get("bar_s") or not key_grid.get("bar_s") or not variants:
            continue
        beat = float(grid["bar_s"]) / 4
        row = {"name": track["name"], "map_type": plan.get("map_type"), "slots": {}}
        for slot in "ABCDEFGH":
            truth = _truth_time(answer["cues"].get(slot, {}), key_grid)
            row["slots"][slot] = {"truth": truth, "variants": {}}
            for key, variant in variants.items():
                if variant.get("status") != "ok":
                    continue
                cue = variant["cues"].get(slot, {})
                time_s = cue.get("time_s") if cue.get("bar") is not None else None
                right = (time_s is None and truth is None) or (
                    time_s is not None and truth is not None and abs(time_s - truth) <= beat + 1e-6)
                row["slots"][slot]["variants"][key] = {"time_s": time_s, "right": right}
                entry = results.setdefault(key, {"label": variant.get("label", key), "right": 0, "total": 0,
                                                 "by_slot": {}, "by_map": {}, "wins": 0, "losses": 0})
                entry["total"] += 1
                entry["right"] += int(right)
                slot_entry = entry["by_slot"].setdefault(slot, {"right": 0, "total": 0})
                slot_entry["total"] += 1
                slot_entry["right"] += int(right)
                map_entry = entry["by_map"].setdefault(str(plan.get("map_type")), {"right": 0, "total": 0})
                map_entry["total"] += 1
                map_entry["right"] += int(right)
            base = row["slots"][slot]["variants"].get(BASELINE)
            for key, value in row["slots"][slot]["variants"].items():
                if base and key != BASELINE and value["right"] != base["right"]:
                    results[key]["wins" if value["right"] else "losses"] += 1
        per_track.append(row)
    for entry in results.values():
        entry["accuracy"] = round(entry["right"] / entry["total"], 3) if entry["total"] else None
    return {"variants": results, "tracks": per_track, "graded_songs": len(per_track)}
