"""Generated click/tone audio and declared beat observations; never a model accuracy test."""
import json
from pathlib import Path
import wave


def fixture_request(root: Path):
    import numpy as np
    root.mkdir(parents=True, exist_ok=True)
    source, beats_path = root / "synthetic.wav", root / "beats.json"
    rate, seconds = 44100, 48
    times = np.arange(rate * seconds) / rate
    audio = 0.03 * np.sin(2 * np.pi * 220 * times)
    pulse_times = np.arange(2205) / rate
    pulse = 0.5 * np.sin(2 * np.pi * 60 * pulse_times) * np.exp(-pulse_times * 50)
    for beat in np.arange(0, seconds, 0.5):
        start = int(beat * rate)
        audio[start:start + len(pulse)] += pulse
    with wave.open(str(source), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes((audio * 32767).astype("<i2").tobytes())
    beats_path.write_text(json.dumps({
        "beats": [i * 0.5 for i in range(seconds * 2)],
        "downbeats": [i * 2 for i in range(seconds // 2)],
    }), encoding="utf-8")
    return {"schema_version": 1, "job_id": "synthetic-fixture", "source": str(source),
            "profile": "prepared", "beats_path": str(beats_path), "cache_dir": str(root / "cache"),
            "genre": "pop", "experiments": ["baseline"]}
