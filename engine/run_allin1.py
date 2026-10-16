"""Run allin1 on CPU on one WAV, timing it, with an audit hook that records every
file opened from inside the installed madmom package (to prove no NC model files load)."""
import sys, os, time, json
madmom_opens, model_opens = [], []
def hook(event, args):
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        p = os.fsdecode(args[0])
        if "madmom" in p and not p.endswith((".py", ".pyc", ".so")):
            madmom_opens.append(p)
        if p.endswith((".pkl", ".npz", ".npy", ".h5", ".hdf5", ".pth", ".pt", ".th", ".ckpt", ".safetensors", ".yaml")):
            model_opens.append(p)
sys.addaudithook(hook)
import torch; torch.set_num_threads(os.cpu_count())
t0 = time.time()
import allin1
t_import = time.time() - t0
wav = sys.argv[1]
out = os.path.join(os.environ["PROOF"], "out", "allin1")
t1 = time.time()
res = allin1.analyze(wav, out_dir=out, device="cpu",
                     demix_dir=os.path.join(out, "demix"), spec_dir=os.path.join(out, "spec"),
                     keep_byproducts=False, overwrite=True)
t_an = time.time() - t1
print(json.dumps({"import_s": round(t_import, 2), "analyze_s_incl_model_load": round(t_an, 2),
                  "bpm": res.bpm, "n_beats": len(res.beats), "n_downbeats": len(res.downbeats),
                  "segments": [(round(s.start, 2), round(s.end, 2), s.label) for s in res.segments]}, indent=1))
print("FILES OPENED FROM madmom (non-code):", madmom_opens)
print("MODEL/CONFIG FILES OPENED:", sorted(set(model_opens)))
