"""Fresh-audio pipeline: one audio file in, a v7 review-candidate cue plan out.

Runs the analyzers a v7 plan needs and caches every result by content hash in the v5 cache layout,
so `rules_v6` consumes it unchanged. The audio file is only read. Nothing but text lookups
(LRCLIB artist/title) leaves the machine.

Steps (the web UI's step chips): beats, sections, vocals, lyrics, drops, cues.
A missing analyzer raises no error when a fallback exists: the step is recorded as skipped, the
plan is flagged, and confidence is capped so the track lands in "Needs a look". Cues are never invented.

    python3 engine/analyze.py song.mp3 --artist "A" --title "T" --genre House
"""
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

TRACK_ID = "00"
STEPS = ("beats", "sections", "vocals", "lyrics", "drops", "cues")
FPS = 50
STEM_HOP = 2205  # 50 ms at 44.1 kHz, the v5 stems_env format
DEMUCS_TIMEOUT_S = int(os.environ.get("AUTOCUE_DEMUCS_TIMEOUT", "600"))
DEGRADED_CONFIDENCE_CAP = 0.55  # below the default 0.6 review threshold

INSTALL = {
    "ffmpeg": "brew install ffmpeg",
    "numpy": "pip install numpy",
    "torch": "pip install torch torchaudio",
    "beat_this": "pip install https://github.com/CPJKU/beat_this/archive/main.zip",
    "demucs": "pip install demucs",
    "allin1": "pip install natten allin1  (NATTEN needs a source build on Apple Silicon; see setup.sh)",
    "whisperx": "pip install whisperx",
    "songformer": "optional; see README (MuQ weights are non-commercial)",
}


class AnalyzerMissing(RuntimeError):
    def __init__(self, name):
        self.name = name
        super().__init__(f"{name} is not installed. Install it with: {INSTALL.get(name, 'see README')}")


def cache_root():
    override = os.environ.get("AUTOCUE_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library" / "Application Support" / "AutoCue" / "cache"


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def available(module):
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def environment_report():
    return {
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "numpy": available("numpy"),
        "torch": available("torch"),
        "beat_this": available("beat_this"),
        "demucs": available("demucs"),
        "allin1": available("allin1"),
        "whisperx": available("whisperx"),
        "songformer": available("songformer"),
    }


def torch_device():
    if not available("torch"):
        return "cpu"
    import torch
    return "mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "cpu"


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class Paths:
    """v5 cache layout under <cache>/<hash>/ with the single track id 00."""

    def __init__(self, directory):
        self.root = directory
        self.beats = directory / "out" / "beat_this" / (TRACK_ID + ".json")
        self.sections = directory / "cues" / "allin1" / (TRACK_ID + ".json")
        self.allin1_raw = directory / "out" / "allin1" / (TRACK_ID + ".json")
        self.stems_env = directory / "out" / "allin1" / (TRACK_ID + ".stems_env.npz")
        self.features = directory / "features" / (TRACK_ID + ".npz")
        self.words = directory / "out" / "words" / (TRACK_ID + ".json")
        self.lyrics = directory / "out" / "lrclib" / (TRACK_ID + ".json")
        self.meta = directory / "meta.json"
        self.vocal_wav = directory / "work" / "vocals16k.wav"
        self.work = directory / "work"


def decode_wav(source, target, rate=44100, channels=2):
    if not shutil.which("ffmpeg"):
        raise AnalyzerMissing("ffmpeg")
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(source), "-vn", "-ac", str(channels),
                    "-ar", str(rate), "-acodec", "pcm_s16le", str(target)], check=True)


def read_mono(path):
    import numpy as np
    with wave.open(str(path)) as stream:
        channels, rate = stream.getnchannels(), stream.getframerate()
        data = np.frombuffer(stream.readframes(stream.getnframes()), dtype=np.int16)
    data = data.reshape(-1, channels).astype(np.float32).mean(1) / 32768.0
    return data, rate


def compute_features(wav_path, out_path):
    """50 fps peak, RMS and low/mid/high band energy (same format as engine/features.py)."""
    import numpy as np
    x, rate = read_mono(wav_path)
    x = x[: len(x) // 2 * 2].reshape(-1, 2).mean(1)
    rate //= 2
    hop = rate // FPS
    frames_count = len(x) // hop
    frames = x[: frames_count * hop].reshape(frames_count, hop)
    peak, rms = np.abs(frames).max(1), np.sqrt((frames ** 2).mean(1))
    nfft = 1024
    padded = np.concatenate([x, np.zeros(nfft, np.float32)])
    window = np.hanning(nfft).astype(np.float32)
    freqs = np.fft.rfftfreq(nfft, 1 / rate)
    lo, mi, hi = (np.zeros(frames_count, np.float32) for _ in range(3))
    for start in range(0, frames_count, 2000):
        end = min(frames_count, start + 2000)
        chunk = np.stack([padded[i * hop:i * hop + nfft] for i in range(start, end)]) * window
        power = np.abs(np.fft.rfft(chunk, axis=1)) ** 2
        lo[start:end] = power[:, freqs < 200].sum(1)
        mi[start:end] = power[:, (freqs >= 200) & (freqs < 2500)].sum(1)
        hi[start:end] = power[:, freqs >= 2500].sum(1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_path, fps=FPS, peak=peak.astype(np.float32), rms=rms.astype(np.float32),
                        lo=lo, mi=mi, hi=hi, dur=len(x) / rate)
    return len(x) / rate


def fit_grid(downbeats):
    """Fit a bar grid robustly to Beat This output, excluding local spurious downbeats."""
    import numpy as np
    if len(downbeats) < 4:
        raise RuntimeError("Beat This found fewer than 4 downbeats; no reliable bar grid for this track.")
    times = np.asarray(downbeats, dtype=float)
    intervals = np.diff(times)
    min_bar_s, max_bar_s = 1.0, 240.0 / 60
    plausible = intervals[(intervals >= min_bar_s) & (intervals <= max_bar_s)]
    if len(plausible) < 3:
        raise RuntimeError("Beat This downbeats are too irregular to fit a reliable bar grid.")
    bar_s = float(np.median(plausible))
    phases = np.mod(times, bar_s)
    tolerance = max(0.08, min(0.14, bar_s * 0.06))
    phase_differences = np.abs(phases[:, None] - phases[None, :])
    phase_differences = np.minimum(phase_differences, bar_s - phase_differences)
    counts = np.sum(phase_differences <= tolerance, axis=1)
    phase = float(phases[int(np.argmax(counts))])
    signed = ((times - phase + bar_s / 2) % bar_s) - bar_s / 2
    inliers = np.abs(signed) <= tolerance
    if int(inliers.sum()) < 4:
        raise RuntimeError("Beat This downbeats do not form a stable bar-grid phase.")
    bars = np.rint((times[inliers] - phase) / bar_s)
    slope, intercept = np.polyfit(bars, times[inliers], 1)
    residual = float(np.median(np.abs(times[inliers] - (slope * bars + intercept))))
    if not min_bar_s - 1e-6 <= slope <= max_bar_s + 1e-6 or residual > tolerance:
        raise RuntimeError("Beat This downbeats do not form a reliable bar grid.")
    return {"source": "beat_this_downbeats", "t1": round(float(intercept), 4), "bar_s": round(float(slope), 5),
            "bpm": round(240.0 / float(slope), 3), "offset_ms": 0.0, "fit": round(residual, 4)}


def _run_beats(source_wav, paths):
    if paths.beats.exists():
        return _read_json(paths.beats)
    if not available("beat_this"):
        raise AnalyzerMissing("beat_this")
    from beat_this.inference import File2Beats
    device = torch_device()
    try:
        beats, downbeats = File2Beats(checkpoint_path="final0", device=device, dbn=False)(str(source_wav))
    except RuntimeError as error:
        if device != "mps" or "out of memory" not in str(error).casefold():
            raise
        import torch
        torch.mps.empty_cache()
        device = "cpu"
        beats, downbeats = File2Beats(checkpoint_path="final0", device=device, dbn=False)(str(source_wav))
    record = {"engine": "beat_this", "checkpoint": "final0", "dbn": False,
              "device": device,
              "beats": [round(float(value), 4) for value in beats],
              "downbeats": [round(float(value), 4) for value in downbeats]}
    _write_json(paths.beats, record)
    return record


def _run_demucs(source_wav, paths):
    """Demucs stems to 50 ms RMS envelopes (+ a 16 kHz vocal stem for WhisperX)."""
    import numpy as np
    if paths.stems_env.exists() and paths.vocal_wav.exists():
        return
    if not available("demucs"):
        raise AnalyzerMissing("demucs")
    out_dir = paths.work / "demix"
    stem_dir = out_dir / "htdemucs" / Path(source_wav).stem
    code = ("import random,sys,numpy as np,torch;random.seed(0);np.random.seed(0);torch.manual_seed(0);"
            "import demucs.separate as d;d.main(sys.argv[1:])")
    if not (stem_dir / "vocals.wav").exists():
        # MPS can stall; give it a bounded time, then redo on the CPU instead of hanging the batch.
        for device in dict.fromkeys((torch_device(), "cpu")):
            try:
                subprocess.run([sys.executable, "-c", code, "--out", str(out_dir), "-n", "htdemucs",
                                "--device", device, str(source_wav)], check=True,
                               timeout=DEMUCS_TIMEOUT_S if device != "cpu" else None)
                break
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
                if device == "cpu":
                    raise
    env = {}
    for name in ("vocals", "drums", "bass", "other"):
        x, rate = read_stem(stem_dir / (name + ".wav"))
        if rate != 44100:
            raise RuntimeError("Demucs stem envelopes require a 44.1 kHz sample rate.")
        count = len(x) // STEM_HOP
        env[name] = np.sqrt((x[:count * STEM_HOP].reshape(count, STEM_HOP) ** 2).mean(1)).astype(np.float32)
        tail = x[count * STEM_HOP:]
        if len(tail):
            env[name] = np.append(env[name], np.sqrt((tail ** 2).mean()))
    paths.stems_env.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(paths.stems_env, hop_s=np.float32(STEM_HOP / 44100), **env)
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(stem_dir / "vocals.wav"), "-ac", "1",
                        "-ar", "16000", str(paths.vocal_wav)], check=True)


def read_stem(path):
    try:
        import soundfile
        x, rate = soundfile.read(str(path), dtype="float32")
        return (x.mean(1) if x.ndim > 1 else x), rate
    except ImportError:
        return read_mono(path)


def vocal_onsets(paths, threshold_ratio=0.15, min_gap_s=1.0):
    """Times where the vocal stem rises from silence; derived from the Demucs vocal envelope."""
    import numpy as np
    env = np.load(paths.stems_env)
    vocals, hop = env["vocals"], float(env["hop_s"])
    level = max(float(np.percentile(vocals, 95)) * threshold_ratio, 1e-5)
    active = vocals > level
    onsets, last = [], -1e9
    for index in range(1, len(active)):
        time_s = index * hop
        if active[index] and not active[index - 1] and time_s - last >= min_gap_s:
            onsets.append(round(time_s, 3))
            last = time_s
    return onsets


def _install_allin1_compat():
    import collections
    import collections.abc
    import numpy as np

    for name in ("MutableSequence", "MutableMapping", "MutableSet", "Sequence", "Mapping", "Set"):
        if not hasattr(collections, name):
            setattr(collections, name, getattr(collections.abc, name))
    for name, value in (("float", float), ("int", int), ("complex", complex)):
        if not hasattr(np, name):
            setattr(np, name, value)

    import natten.functional as functional
    import torch

    def indices_1d(length, kernel_size, dilation, device):
        positions = torch.arange(length, device=device)
        radius = kernel_size // 2
        max_start = max(0, length - 1 - (kernel_size - 1) * dilation)
        starts = (positions - radius * dilation).clamp(0, max_start)
        neighbors = starts[:, None] + torch.arange(kernel_size, device=device)[None, :] * dilation
        relative = (neighbors - positions[:, None]) // dilation + radius
        return neighbors.long(), relative.long()

    def na1d_qk(query, key, rpb, kernel_size, dilation):
        batch, heads, length, width = query.shape
        neighbors, relative = indices_1d(length, kernel_size, dilation, query.device)
        gather = neighbors.reshape(1, 1, length * kernel_size, 1).expand(batch, heads, length * kernel_size, width)
        local_keys = torch.gather(key, 2, gather).reshape(batch, heads, length, kernel_size, width)
        scores = (query.unsqueeze(-2) * local_keys).sum(-1)
        return scores + rpb[:, relative].unsqueeze(0)

    def na1d_av(attention, value, kernel_size, dilation):
        batch, heads, length, width = value.shape
        neighbors, _ = indices_1d(length, kernel_size, dilation, value.device)
        gather = neighbors.reshape(1, 1, length * kernel_size, 1).expand(batch, heads, length * kernel_size, width)
        local_values = torch.gather(value, 2, gather).reshape(batch, heads, length, kernel_size, width)
        return (attention.unsqueeze(-1) * local_values).sum(-2)

    def indices_2d(height, width, kernel_size, dilation, device):
        positions_y = torch.arange(height, device=device)
        positions_x = torch.arange(width, device=device)
        radius = kernel_size // 2
        max_y = max(0, height - 1 - (kernel_size - 1) * dilation)
        max_x = max(0, width - 1 - (kernel_size - 1) * dilation)
        starts_y = (positions_y - radius * dilation).clamp(0, max_y)
        starts_x = (positions_x - radius * dilation).clamp(0, max_x)
        neighbors_y = starts_y[:, None] + torch.arange(kernel_size, device=device)[None, :] * dilation
        neighbors_x = starts_x[:, None] + torch.arange(kernel_size, device=device)[None, :] * dilation
        relative_y = (neighbors_y - positions_y[:, None]) // dilation + radius
        relative_x = (neighbors_x - positions_x[:, None]) // dilation + radius
        linear = (neighbors_y[:, None, :, None] * width + neighbors_x[None, :, None, :]).reshape(
            height * width, kernel_size * kernel_size)
        rel_y = relative_y[:, None, :, None].expand(height, width, kernel_size, kernel_size)
        rel_x = relative_x[None, :, None, :].expand(height, width, kernel_size, kernel_size)
        return linear.long(), rel_y.reshape(height * width, -1).long(), rel_x.reshape(height * width, -1).long()

    def na2d_qk(query, key, rpb, kernel_size, dilation):
        batch, heads, height, width, channels = query.shape
        neighbors, relative_y, relative_x = indices_2d(height, width, kernel_size, dilation, query.device)
        length, count = neighbors.shape
        query_flat = query.reshape(batch, heads, length, channels)
        key_flat = key.reshape(batch, heads, length, channels)
        gather = neighbors.reshape(1, 1, length * count, 1).expand(batch, heads, length * count, channels)
        local_keys = torch.gather(key_flat, 2, gather).reshape(batch, heads, length, count, channels)
        scores = (query_flat.unsqueeze(-2) * local_keys).sum(-1)
        bias = rpb[:, relative_y, relative_x].reshape(heads, length, count)
        return (scores + bias.unsqueeze(0)).reshape(batch, heads, height, width, count)

    def na2d_av(attention, value, kernel_size, dilation):
        batch, heads, height, width, channels = value.shape
        neighbors, _, _ = indices_2d(height, width, kernel_size, dilation, value.device)
        length, count = neighbors.shape
        value_flat = value.reshape(batch, heads, length, channels)
        gather = neighbors.reshape(1, 1, length * count, 1).expand(batch, heads, length * count, channels)
        local_values = torch.gather(value_flat, 2, gather).reshape(batch, heads, length, count, channels)
        result = (attention.reshape(batch, heads, length, count).unsqueeze(-1) * local_values).sum(-2)
        return result.reshape(batch, heads, height, width, channels)

    for name, implementation in (
        ("natten1dqkrpb", na1d_qk), ("natten1dav", na1d_av),
        ("natten2dqkrpb", na2d_qk), ("natten2dav", na2d_av),
    ):
        if not hasattr(functional, name):
            setattr(functional, name, implementation)


def _run_sections(source_wav, paths):
    if paths.sections.exists():
        return _read_json(paths.sections)
    if available("allin1"):
        _install_allin1_compat()
        import allin1
        # Reuses the Demucs stems already separated for this file (vocals step runs first).
        result = allin1.analyze(str(source_wav), out_dir=None, device="cpu", keep_byproducts=True,
                                demix_dir=str(paths.work / "demix"), spec_dir=str(paths.work / "spec"),
                                overwrite=True, multiprocess=False)
        segments = [{"start": s.start, "end": s.end, "label": s.label} for s in result.segments]
        record = {"engine": "allin1", "segments": segments}
        _write_json(paths.sections, record)
        return record
    raise AnalyzerMissing("allin1")


def _run_words(paths):
    if paths.words.exists():
        cached = _read_json(paths.words)
        if cached and cached.get("language_source") == "vocal_window" and not cached.get("alignment_error"):
            return cached
    if not available("whisperx"):
        raise AnalyzerMissing("whisperx")
    if not paths.vocal_wav.exists():
        raise RuntimeError("No vocal stem is available for WhisperX.")
    import wx_one
    record = wx_one.transcribe_local(str(paths.vocal_wav))
    record["language_source"] = "vocal_window"
    _write_json(paths.words, record)
    if record.get("alignment_error"):
        raise RuntimeError("WhisperX word alignment failed: " + record["alignment_error"])
    return record


def _run_lyrics(paths, artist, title):
    if paths.lyrics.exists():
        return _read_json(paths.lyrics)
    if not artist or not title:
        return {"syncedLyrics": None, "reason": "artist and title tags are needed for the LRCLIB lookup"}
    import lrclib_synced
    try:
        found = lrclib_synced.lookup(artist, title)
    except Exception as error:
        return {"syncedLyrics": None, "error": str(error)}
    record = {"artist": artist, "title": title, **(found or {"syncedLyrics": None})}
    _write_json(paths.lyrics, record)
    return record


def analyze(source, artist="", title="", genre="", progress=None, cache_dir=None, config_path=None,
            switches=None, cue_maps=None, experiments=None, gemini=False):
    """Return a v7 review-candidate plan dict. `progress(step, state, detail)` is optional.

    state is one of running, done, skipped, failed. Raises AnalyzerMissing only for analyzers with no fallback.
    """
    source = Path(source)
    report = progress or (lambda *_args: None)
    for needed in ("numpy", "ffmpeg"):
        if (needed == "ffmpeg" and not shutil.which("ffmpeg")) or (needed == "numpy" and not available("numpy")):
            raise AnalyzerMissing(needed)
    digest = file_hash(source)
    paths = Paths((Path(cache_dir) if cache_dir else cache_root()) / digest)
    paths.root.mkdir(parents=True, exist_ok=True)
    notes, started = [], time.time()
    timings = {}

    def step(name, function):
        report(name, "running", "")
        begin = time.time()
        try:
            value = function()
        except AnalyzerMissing as error:
            notes.append(str(error))
            report(name, "skipped", str(error))
            return None
        except Exception as error:
            notes.append(f"{name} failed: {error}")
            report(name, "failed", str(error))
            return None
        timings[name] = round(time.time() - begin, 2)
        report(name, "done", "")
        return value

    decoded = paths.work / "decoded.wav"
    if not paths.features.exists() or not decoded.exists():
        decode_wav(source, decoded)
    if not paths.features.exists():
        compute_features(decoded, paths.features)
    import numpy as np
    feature = np.load(paths.features)
    duration = float(feature["dur"])

    beats = step("beats", lambda: _run_beats(decoded, paths))
    if not beats:
        raise RuntimeError("Beat grid unavailable: " + "; ".join(notes))
    grid = fit_grid(beats["downbeats"])
    import serato_grid
    markers = serato_grid.read_beatgrid(source, on_diagnostic=lambda note: notes.append("beatgrid: " + note))
    if markers:
        grid, grid_note = serato_grid.reconcile(grid, markers)
        notes.append("beatgrid: " + grid_note)

    step("vocals", lambda: _run_demucs(decoded, paths))
    segments_record = step("sections", lambda: _run_sections(decoded, paths))
    segments = (segments_record or {}).get("segments", [])
    analysis = {"segments": segments, "grid": grid, "beats": beats["beats"], "downbeats": beats["downbeats"]}
    if segments:
        _write_json(paths.sections, {**analysis, "engine": segments_record.get("engine")})

    onsets = vocal_onsets(paths) if paths.stems_env.exists() else []
    words_record = step("lyrics", lambda: _run_words(paths)) if paths.vocal_wav.exists() else None
    if not paths.vocal_wav.exists():
        notes.append("lyrics: no vocal stem, so word timing was skipped")
    lyrics_record = _run_lyrics(paths, artist, title)

    stems = None
    hop = 0.05
    if paths.stems_env.exists():
        env = np.load(paths.stems_env)
        stems = {key: env[key].tolist() for key in ("drums", "bass", "vocals") if key in env}
        hop = float(env["hop_s"])
    report("drops", "running", "")
    import rules_v6
    import experiments as experiments_module
    config = rules_v6._load_json(str(config_path or HERE.parent / "config" / "genre_params_v6.json"),
                                 rules_v6.DEFAULT_CONFIG)
    if switches:
        config["switches"] = {**rules_v6.DEFAULT_CONFIG["switches"], **config.get("switches", {}), **switches}
    low_raw = feature["lo"].tolist()
    low_stem_step = experiments_module._resample(low_raw, 1 / float(feature["fps"]), hop).tolist()
    base_kwargs = dict(
        track_id=TRACK_ID, analysis=analysis, plan={"cues": {}}, duration_s=duration, genre=genre,
        words=(words_record or {}).get("words"), synced_lyrics=(lyrics_record or {}).get("syncedLyrics"),
        stems=stems, low_energy=low_stem_step, hop_s=hop, config=config, energy=feature["rms"].tolist(),
        fps=float(feature["fps"]), cue_maps=cue_maps)
    plan = rules_v6.build_plan(**base_kwargs)
    plan["bar_energy"] = experiments_module.bar_energy_table(
        stems, low_raw, base_kwargs["energy"], hop, float(feature["fps"]), grid, duration)
    if gemini:
        import copy
        import gemini_assist

        def rebuild_maps(mode):
            import cue_maps as cue_map_settings
            forced = cue_map_settings.normalize_cue_maps(copy.deepcopy(base_kwargs["cue_maps"]))
            forced["mode"] = mode
            return forced

        def rebuild(mode):
            return rules_v6.build_plan(**{**base_kwargs, "cue_maps": rebuild_maps(mode)})

        report("drops", "running", "Gemini second opinion")
        plan = gemini_assist.assist(
            plan, rebuild, digest, cache_root() / "gemini", artist, title,
            spend_cap=float(config.get("gemini_spend_cap_usd", 3.0)),
            confidence_threshold=float(config.get("gemini_confidence_threshold", 0.6)),
            map_forced=((base_kwargs["cue_maps"] or {}).get("mode") or "AUTO") != "AUTO")
        if (plan.get("gemini") or {}).get("switched_map"):
            base_kwargs["cue_maps"] = rebuild_maps(plan["map_type"])
    enabled = experiments_module.normalize_enabled(experiments)
    context = {**base_kwargs, "low_energy_raw": low_raw, "paths": paths, "decoded_wav": decoded}
    plan["variants"] = experiments_module.run_variants(base_kwargs, context, plan, enabled, rules_v6.build_plan)
    if "bar_energy" not in plan:
        plan["bar_energy"] = experiments_module.bar_energy_table(
            stems, low_raw, base_kwargs["energy"], hop, float(feature["fps"]), grid, duration)
    report("drops", "done", plan.get("map_type", ""))

    report("cues", "running", "")
    degraded = []
    if not segments:
        degraded.append("section labels unavailable (allin1 missing or failed)")
    if not stems:
        degraded.append("stems unavailable (Demucs missing or failed)")
    if not (words_record or {}).get("words"):
        degraded.append("word timings unavailable (WhisperX missing or failed)")
    plan["flags"] = list(dict.fromkeys(plan.get("flags", []) + degraded))
    if degraded and (not segments or not stems):
        for cue in plan.get("cues", {}).values():
            if cue.get("confidence") is not None:
                cue["confidence"] = round(min(float(cue["confidence"]), DEGRADED_CONFIDENCE_CAP), 3)
    elif degraded:
        for cue in plan.get("cues", {}).values():
            if cue.get("confidence") is not None:
                cue["confidence"] = round(min(float(cue["confidence"]), 0.9), 3)
    plan["pipeline"] = {"hash": digest, "device": (beats or {}).get("device", torch_device()),
                        "vocal_onsets": onsets[:50],
                        "step_seconds": timings, "notes": notes, "total_s": round(time.time() - started, 2),
                        "analyzers": environment_report()}
    _write_json(paths.meta, plan["pipeline"])
    shutil.rmtree(paths.work / "demix", ignore_errors=True)
    report("cues", "done", "")
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("audio", nargs="?")
    parser.add_argument("--artist", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--genre", default="")
    parser.add_argument("--out", help="write the plan JSON here")
    parser.add_argument("--cache-dir")
    parser.add_argument("--check", action="store_true", help="print which analyzers are installed")
    args = parser.parse_args()
    if args.check or not args.audio:
        for name, ok in environment_report().items():
            print(f"{'ok     ' if ok else 'MISSING'} {name}" + ("" if ok else "  -> " + INSTALL[name]))
        print("device:", torch_device())
        return
    def show(step_name, state, detail):
        print(f"[{step_name}] {state} {detail}".rstrip(), file=sys.stderr, flush=True)
    try:
        plan = analyze(args.audio, args.artist, args.title, args.genre, show, args.cache_dir)
    except AnalyzerMissing as error:
        raise SystemExit(str(error))
    text = json.dumps(plan, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
