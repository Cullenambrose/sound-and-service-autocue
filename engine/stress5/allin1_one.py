"""allin1 on one WAV with a FIXED SEED (seed 0) for Demucs' random shift.
Demucs is run in-process after seeding, then allin1 reuses the stems.
Saves allin1 JSON, activations (npz), and coarse stem RMS envelopes; deletes the stems."""
import sys, os, time, json, random, shutil, subprocess
import numpy as np, torch
SEED=0
random.seed(SEED); np.random.seed(SEED); torch.manual_seed(SEED)
torch.set_num_threads(os.cpu_count())
wav, outdir = sys.argv[1], sys.argv[2]
stem = os.path.splitext(os.path.basename(wav))[0]
work = os.path.join(outdir, "_work_" + stem); os.makedirs(work, exist_ok=True)
demix_dir = os.path.join(work, "demix"); spec_dir = os.path.join(work, "spec")
t0=time.time()
if not os.path.exists(os.path.join(demix_dir, "htdemucs", stem, "vocals.wav")):
    # separate process so Demucs' memory is released before allin1 loads (box RAM is tight)
    import subprocess
    code = ("import random,numpy as np,torch,sys;random.seed(%d);np.random.seed(%d);torch.manual_seed(%d);"
            "torch.set_num_threads(4);import demucs.separate as d;d.main(sys.argv[1:])") % (SEED, SEED, SEED)
    subprocess.run([sys.executable, "-c", code, "--out", demix_dir, "--name", "htdemucs", "--device", "cpu", wav], check=True)
t1=time.time()
import allin1
random.seed(SEED); np.random.seed(SEED); torch.manual_seed(SEED)
res = allin1.analyze(wav, model=os.environ.get("A1_MODEL","harmonix-all"), out_dir=None, device="cpu", demix_dir=demix_dir, spec_dir=spec_dir,
                     include_activations=True, keep_byproducts=True, overwrite=True, multiprocess=False)
t2=time.time()
acts = res.activations or {}
np.savez_compressed(os.path.join(outdir, stem + ".activations.npz"), **{k: np.asarray(v) for k, v in acts.items()})
# coarse stem envelopes (RMS, 50 ms hop) for the B vocal-onset check and E groove check
import soundfile as sf
env = {}
sd = os.path.join(demix_dir, "htdemucs", stem)
hop = 2205
for s in ("vocals", "drums", "bass", "other"):
    x, sr = sf.read(os.path.join(sd, s + ".wav"), dtype="float32")
    if x.ndim > 1: x = x.mean(1)
    n = len(x)//hop
    env[s] = np.sqrt((x[:n*hop].reshape(n, hop)**2).mean(1)).astype(np.float32)
np.savez_compressed(os.path.join(outdir, stem + ".stems_env.npz"), hop_s=np.float32(hop/44100), **env)
out = {"engine":"allin1","model":os.environ.get("A1_MODEL","harmonix-all"),"seed":SEED,"wav":wav,"bpm":res.bpm,
       "beats":res.beats,"downbeats":res.downbeats,"beat_positions":res.beat_positions,
       "segments":[{"start":s.start,"end":s.end,"label":s.label} for s in res.segments],
       "demix_s":round(t1-t0,1),"analyze_s":round(t2-t1,1)}
json.dump(out, open(os.path.join(outdir, stem + ".json"), "w"))
vd=os.path.join(outdir,"vocals"); os.makedirs(vd,exist_ok=True)
subprocess.run(["ffmpeg","-y","-loglevel","error","-i",os.path.join(sd,"vocals.wav"),"-ac","1","-ar","16000",os.path.join(vd,stem+".wav")],check=True)
shutil.rmtree(work, ignore_errors=True)
print(f"demix {t1-t0:.0f}s analyze {t2-t1:.0f}s bpm {res.bpm} segs {len(res.segments)}")
