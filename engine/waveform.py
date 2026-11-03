"""Versioned local waveform pyramid; zoom requests never decode audio after the first build."""
import fcntl
import math
import os
import subprocess
import tempfile
import wave
from pathlib import Path

VERSION = 1
RATE = 22050
BUCKET = 128


def build(source, directory):
    import numpy as np
    from scipy.signal import lfilter
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"waveform-v{VERSION}.npz"
    with (directory / "waveform.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.is_file():
            return target, True
        with tempfile.TemporaryDirectory(prefix="waveform-", dir=directory) as work:
            decoded = Path(work) / "mono.wav"
            try:
                subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(source),
                                "-vn", "-ac", "1", "-ar", str(RATE), "-acodec", "pcm_s16le", str(decoded)],
                               check=True, capture_output=True, timeout=300)
            except FileNotFoundError:
                raise RuntimeError("Waveform generation requires ffmpeg.") from None
            except subprocess.TimeoutExpired:
                raise RuntimeError("Waveform decoding exceeded the five-minute timeout.") from None
            except subprocess.CalledProcessError:
                raise ValueError("Audio could not be decoded for waveform editing.") from None
            try:
                with wave.open(str(decoded)) as audio:
                    samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32) / 32768
            except (wave.Error, EOFError):
                raise ValueError("Decoded audio is not a valid PCM waveform.") from None
            if not len(samples):
                raise ValueError("Audio has no samples for waveform editing.")
            duration = len(samples) / RATE
            count = math.ceil(len(samples) / BUCKET)
            padded = np.pad(samples, (0, count * BUCKET - len(samples)))
            frames = padded.reshape(-1, BUCKET)
            weights = np.full(count, BUCKET, dtype=np.float64)
            weights[-1] = len(samples) - (count - 1) * BUCKET
            level = np.zeros((count, 6), dtype=np.float32)
            level[:, 0], level[:, 1], level[:, 5] = frames.min(1), frames.max(1), weights
            low_alpha, mid_alpha = (1 - math.exp(-2 * math.pi * cutoff / RATE) for cutoff in (200, 2500))
            low = lfilter([low_alpha], [1, -(1 - low_alpha)], samples).astype(np.float32)
            mid = lfilter([mid_alpha], [1, -(1 - mid_alpha)], samples).astype(np.float32)
            for index, band in enumerate((low, mid - low, samples - mid), 2):
                energy = np.pad(band * band, (0, count * BUCKET - len(samples))).reshape(-1, BUCKET).sum(1)
                level[:, index] = energy / weights
            norm = np.maximum(np.sqrt(np.quantile(level[:, 2:5], 0.98, axis=0)), 1e-9)
            arrays = {"duration": duration, "norm": norm, "version": VERSION}
            index = 0
            while True:
                arrays[f"level_{index}"] = level
                if len(level) <= 1:
                    break
                padded = np.pad(level, ((0, (-len(level)) % 4), (0, 0)))
                groups = padded.reshape(-1, 4, 6)
                combined = np.zeros((len(groups), 6), dtype=np.float32)
                combined[:, 0], combined[:, 1] = groups[:, :, 0].min(1), groups[:, :, 1].max(1)
                combined[:, 5] = groups[:, :, 5].sum(1)
                combined[:, 2:5] = (groups[:, :, 2:5] * groups[:, :, 5:6]).sum(1) / combined[:, 5:6]
                level, index = combined, index + 1
            temporary = Path(work) / "pyramid.npz"
            np.savez_compressed(temporary, **arrays)
            os.replace(temporary, target)
    return target, False


def query(source, directory, start=0, end=None, points=1600):
    import numpy as np
    if not math.isfinite(start) or start < 0 or type(points) is not int or not 64 <= points <= 4096:
        raise ValueError("Waveform start must be nonnegative and points must be 64-4096.")
    if end is not None and (not math.isfinite(end) or end <= start):
        raise ValueError("Waveform end must be finite and after start.")
    path, cached = build(source, directory)
    with np.load(path, allow_pickle=False) as archive:
        duration = float(archive["duration"])
        if start >= duration:
            raise ValueError("Waveform start is beyond the end of the track.")
        end = min(duration, end if end is not None else duration)
        hop, index = BUCKET / RATE, 0
        while (end - start) / hop > points and f"level_{index + 1}" in archive:
            hop, index = hop * 4, index + 1
        level = archive[f"level_{index}"]
        first, last = int(start / hop), min(len(level), math.ceil(end / hop))
        selected = level[first:last, :5].copy()
        selected[:, 2:5] = np.minimum(1, np.sqrt(selected[:, 2:5]) / archive["norm"])
        if not np.isfinite(selected).all() or not math.isfinite(duration) or duration <= 0:
            raise ValueError("Cached waveform is invalid; rebuild its versioned cache.")
        return {"version": VERSION, "duration": duration, "start": first * hop, "hop_s": hop,
                "level": index, "cached": cached, "columns": np.round(selected, 5).tolist()}
