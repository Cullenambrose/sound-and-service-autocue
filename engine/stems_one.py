"""Demucs (htdemucs, seed 0) on one WAV -> 16 kHz mono vocal stem + coarse stem RMS envelopes (50 ms)."""
import sys, os, shutil, subprocess
import numpy as np, soundfile as sf
wav, outdir = sys.argv[1], sys.argv[2]; stem = os.path.splitext(os.path.basename(wav))[0]
demix = os.path.join(outdir, "_work", "demix"); sd = os.path.join(demix, "htdemucs", stem)
if not os.path.exists(os.path.join(sd, "vocals.wav")):
    code = ("import random,numpy as np,torch,sys;random.seed(0);np.random.seed(0);torch.manual_seed(0);"
            "torch.set_num_threads(4);import demucs.separate as d;d.main(sys.argv[1:])")
    subprocess.run([sys.executable, "-c", code, "--out", demix, "--name", "htdemucs", "--device", "cpu", wav], check=True)
hop = 2205; env = {}
for s in ("vocals", "drums", "bass", "other"):
    x, sr = sf.read(os.path.join(sd, s + ".wav"), dtype="float32"); x = x.mean(1) if x.ndim > 1 else x; n = len(x) // hop
    env[s] = np.sqrt((x[:n*hop].reshape(n, hop)**2).mean(1)).astype(np.float32)
np.savez_compressed(os.path.join(outdir, stem + ".stems_env.npz"), hop_s=np.float32(hop/44100), **env)
vd = os.path.join(outdir, "vocals"); os.makedirs(vd, exist_ok=True)
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", os.path.join(sd, "vocals.wav"), "-ac", "1", "-ar", "16000", os.path.join(vd, stem + ".wav")], check=True)
shutil.rmtree(sd, ignore_errors=True); print("ok")
