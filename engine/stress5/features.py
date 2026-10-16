"""Per-track audio features from the decoded WAV temps (read-only): 50 fps peak + 3-band energy
for the waveform, full-mix RMS for onset / cold-end / fade detection. Output: ../features/<nn>.npz"""
import wave, glob, os, numpy as np
REAL = "/workspace/research/dj-auto-cues/proof/stress5"
os.makedirs(f"{REAL}/features", exist_ok=True)
FPS = 50
for w in sorted([f"{REAL}/wav/{a}.wav" for a in __import__("sys").argv[1:]]):
    nn = os.path.basename(w)[:-4]; out = f"{REAL}/features/{nn}.npz"
    if os.path.exists(out): continue
    with wave.open(w) as f:
        sr = f.getframerate(); x = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16).reshape(-1, 2)
    x = x.astype(np.float32).mean(1) / 32768.0
    x = x[: len(x) // 2 * 2].reshape(-1, 2).mean(1); sr //= 2          # 22050 mono
    hop = sr // FPS; n = len(x) // hop
    fr = x[: n * hop].reshape(n, hop)
    peak = np.abs(fr).max(1); rms = np.sqrt((fr ** 2).mean(1))
    nfft = 1024; pad = np.concatenate([x, np.zeros(nfft)])
    idx = np.arange(n) * hop
    win = np.hanning(nfft).astype(np.float32)
    freqs = np.fft.rfftfreq(nfft, 1 / sr)
    lo, mi, hi = np.zeros(n, np.float32), np.zeros(n, np.float32), np.zeros(n, np.float32)
    for s in range(0, n, 2000):
        e = min(n, s + 2000)
        frames = np.stack([pad[i:i + nfft] for i in idx[s:e]]) * win
        S = np.abs(np.fft.rfft(frames, axis=1)) ** 2
        lo[s:e] = S[:, freqs < 200].sum(1); mi[s:e] = S[:, (freqs >= 200) & (freqs < 2500)].sum(1); hi[s:e] = S[:, freqs >= 2500].sum(1)
    np.savez_compressed(out, fps=FPS, peak=peak.astype(np.float32), rms=rms.astype(np.float32), lo=lo, mi=mi, hi=hi, dur=len(x) / sr)
    print(nn, round(len(x) / sr, 1))
