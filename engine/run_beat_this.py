"""Time Beat This! (CPU, default 'final0' checkpoint, no DBN) on one WAV."""
import sys, time, os, torch
torch.set_num_threads(os.cpu_count())
t0 = time.time()
from beat_this.inference import File2Beats
f2b = File2Beats(checkpoint_path="final0", device="cpu", dbn=False)
t1 = time.time()
beats, downbeats = f2b(sys.argv[1])
t2 = time.time()
import numpy as np
ib = np.diff(beats)
print(f"load_s={t1-t0:.2f} infer_s={t2-t1:.2f} n_beats={len(beats)} n_downbeats={len(downbeats)} "
      f"median_ibi={np.median(ib):.4f}s (~{60/np.median(ib):.1f} BPM) first_downbeats={np.round(downbeats[:4],3).tolist()}")
