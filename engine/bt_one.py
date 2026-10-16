"""Beat This! on one WAV -> JSON (beats, downbeats). Read-only on input."""
import sys, os, time, json, torch
torch.set_num_threads(os.cpu_count())
t0=time.time()
from beat_this.inference import File2Beats
f2b = File2Beats(checkpoint_path="final0", device="cpu", dbn=False)
t1=time.time()
wav, out = sys.argv[1], sys.argv[2]
b, d = f2b(wav)
t2=time.time()
json.dump({"engine":"beat_this","checkpoint":"final0","dbn":False,"wav":wav,
  "beats":[round(float(x),4) for x in b],"downbeats":[round(float(x),4) for x in d],
  "load_s":round(t1-t0,2),"infer_s":round(t2-t1,2)}, open(out,"w"))
print(f"load {t1-t0:.1f}s infer {t2-t1:.1f}s beats {len(b)} downbeats {len(d)}")
