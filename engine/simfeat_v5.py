"""v5 change 3 helper: per-bar chroma + MFCC (librosa, venv-allin1) from the decoded WAVs, on the Serato grid.
Usage: simfeat_v5.py <root> (root has wav/, cues/allin1/). Writes <root>/features_v5/<nn>.sim.npz. Skips existing; ~300 MB RAM."""
import sys, os, glob, json, numpy as np, librosa, warnings; warnings.filterwarnings("ignore")
R = sys.argv[1] if len(sys.argv) > 1 else "/workspace/research/dj-auto-cues/proof/real"
os.makedirs(f"{R}/features_v5", exist_ok=True)
for w in sorted(glob.glob(f"{R}/wav/*.wav")):
    nn = os.path.basename(w).split(".")[0].split(" ")[0]; out = f"{R}/features_v5/{nn}.sim.npz"; cj = f"{R}/cues/allin1/{nn}.json"
    if os.path.exists(out) or not os.path.exists(cj): continue
    g = json.load(open(cj))["grid"]; y, sr = librosa.load(w, sr=22050, mono=True); hop = 512
    ch = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop); mf = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20, hop_length=hop)
    t = librosa.frames_to_time(np.arange(ch.shape[1]), sr=sr, hop_length=hop); nb = int((len(y) / sr - g["t1"]) / g["bar_s"]) + 1
    C, M = np.zeros((nb, 12), np.float32), np.zeros((nb, 20), np.float32)
    for j in range(nb):
        m = (t >= g["t1"] + j * g["bar_s"]) & (t < g["t1"] + (j + 1) * g["bar_s"])
        if m.any(): C[j] = ch[:, m].mean(1); M[j] = mf[:, m].mean(1)
    np.savez_compressed(out, chroma=C, mfcc=M); print(nn, nb, flush=True); del y, ch, mf
