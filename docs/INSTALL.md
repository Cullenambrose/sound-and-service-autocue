# Auto-cue proof: install notes (box only, CPU)

Set up Thu Oct 8, 2026, ~2:10–2:25 PM PT. Box only. Nothing was installed on any user computer, nothing was paid for, no music was downloaded, and no Serato tags were written.

Run `source env.sh` first. It points HF_HOME, TORCH_HOME, and XDG_CACHE_HOME at `./cache` so every checkpoint stays inside this folder.

## Layout
| Path | What |
|---|---|
| `venv-allin1/` | Python 3.10.22: torch 2.5.0+cpu, NATTEN 0.17.5+torch250cpu, madmom 0.17.dev0 (git, **no model files**), allin1 1.1.0, demucs 4.1.0, beat-this 1.1.0 |
| `venv-songformer/` | Python 3.10.22: torch 2.4.0+cpu, muq 0.1.0, transformers 4.51.1, x-transformers 2.4.14, msaf 0.1.80, numpy 1.26.4. This is a trimmed copy of SongFormer's requirements.txt, with training/UI packages left out (gradio, wandb, modelscope, triton, …) |
| `src/` | git clones: all-in-one @18e7890, SongFormer @139b2aa (+ submodules MuQ @28847ea, musicfm @b83ebed), beat_this @b95c8ab, madmom @27f032e (models submodule **not** checked out) |
| `src/SongFormer/src/SongFormer/ckpts/` | SongFormer.safetensors (104 MB) + MusicFM pretrained_msd.pt (1.3 GB). md5 matches `ckpts/md5sum.txt` |
| `cache/hf/` | MuQ-large-msd-iter model.safetensors (1.33 GB), allin1 harmonix-all 8 folds (~90 MB), HTDemucs (84 MB) |
| `cache/torch/` | Beat This! `final0` checkpoint (81 MB) |
| `patches/` | The 3 local patches below, plus the madmom stub |
| `run_allin1.py`, `run_songformer.sh`, `run_beat_this.py` | Timing runners |
| `make_synthetic.py`, `synthetic/` | Self-generated 60 s click/noise/tone WAV (no music) |

## Local patches (needed to run on CPU)
1. **NATTEN CPU probe** (`patches/natten_cpu_only.patch`). NATTEN 0.17.5 calls `torch.cuda.get_device_capability()` at import, and that crashes on CPU-only torch. The patch returns 0 when CUDA is absent.
2. **allin1 → NATTEN 0.17 shim** (`patches/allin1_natten017_shim.patch`). allin1 1.1.0 imports the legacy `natten1dqkrpb/natten1dav/...` names. NATTEN 0.17.5 removed them, and it is the oldest CPU wheel still published. The shim maps them to `na1d_qk/na1d_av/na2d_qk/na2d_av`, which use the same heads-first layout. Check: on the synthetic file, allin1 returned 120 BPM and section boundaries at 15.98/31.99/47.99 s, against true changes at 16/32/48 s.
3. **SongFormer device** (`patches/songformer_cpu_device.patch`). `infer.py` hard-codes `cuda:{rank}`. It now reads `SONGFORMER_DEVICE` (set to `cpu` by `run_songformer.sh`).

## madmom model files (CC BY-NC-SA 4.0): not present, not used
- madmom's code is BSD. Its trained models live in a separate git submodule (`CPJKU/madmom_models`) under CC BY-NC-SA 4.0. We cloned madmom **without** that submodule, so no NC model file exists anywhere on the box.
- allin1 imports only madmom **code**: `FramedSignalProcessor`, `Signal`, `ShortTimeFourierTransformProcessor`, `FilteredSpectrogramProcessor`, `LogarithmicSpectrogramProcessor` (spectrogram.py), and `DBNDownBeatTrackingProcessor` (postprocessing/metrical.py). The DBN is an HMM decoder that is fed allin1's own activations, and it loads no model file. madmom's pretrained RNN paths (`RNNDownBeatProcessor`, `from ..models import DOWNBEATS_BLSTM`) are never called.
- `madmom/__init__.py` does `from . import models`, so we installed a stub `madmom/models/__init__.py` (`patches/madmom_models_stub__init__.py`). Any pretrained-model lookup through it raises an error.
- Runtime proof: `run_allin1.py` installs a Python audit hook. Across 2 full runs, the only files opened from madmom were its `dist-info` metadata. The model files loaded were the 8 allin1 `harmonix-fold*.pth` (MIT, HF `taejunkim/allinone`) plus the HTDemucs safetensors.

## MuQ licence note (CC BY-NC 4.0)
SongFormer inference calls `MuQ.from_pretrained("OpenMuQ/MuQ-large-msd-iter")`. Those weights are **CC BY-NC 4.0, non-commercial only** (HF card, and `src/SongFormer/src/third_party/MuQ/LICENSE_weights`). The MuQ code is MIT. SongFormer's code is CC BY 4.0. Its checkpoint repo declares no licence on HF. MusicFM is MIT/Apache-2.0.
Using MuQ is **allowed for this private, non-commercial proof** (Cullen approved the spec on Oct 8, 2026). If the tool is ever used commercially or sold (for example, as part of Sound & Service), SongFormer would need MuQ replaced or a licence. This is a flag, not legal advice.

## Other licences
allin1 code and weights: MIT. Beat This! code and weights: MIT. HTDemucs (via demucs 4.1.0): MIT code; the HF repo `adefossez/HTDemucs` declares no licence tag. NATTEN: MIT.

## Re-run
```bash
cd /workspace/research/dj-auto-cues/proof && source env.sh
venv-allin1/bin/python run_allin1.py synthetic/clicks_noise_60s.wav
venv-allin1/bin/python run_beat_this.py synthetic/clicks_noise_60s.wav
./run_songformer.sh $PWD/synthetic/songformer_input.scp   # .scp = one absolute WAV path per line
```
