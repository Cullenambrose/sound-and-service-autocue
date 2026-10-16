# Plan: run SongFormer on Cullen's MacBook Pro (not installed yet; needs his OK)

Why: on the box SongFormer needs ~6–7 GB free RAM and was killed twice (other bots share it). His MacBook Pro is an
M1 Pro, 16 GB RAM, macOS 26.5.2, Homebrew Python 3.12 — enough RAM when the Mac is otherwise idle.

**Blocker to raise first: his disk has only ~20 GB free (98% full).** This needs ~4.5 GB. OK, but tight.

## What gets installed (all inside one folder, `~/AutoCue/songformer`, deletable with one `rm -rf`)
| Item | Size |
|---|---|
| Python 3.10 venv (torch 2.4 arm64, transformers 4.51, muq, msaf, librosa…) | ~1.3 GB |
| SongFormer code @139b2aa + MuQ/MusicFM submodules | ~60 MB |
| SongFormer.safetensors | 104 MB |
| MusicFM pretrained_msd.pt | 1.3 GB |
| MuQ-large-msd-iter (Hugging Face, downloaded on first run) | 1.33 GB |
| **Total** | **~4.2–4.5 GB** |
Nothing goes into system Python, nothing touches ~/Music or Serato. Licence note: MuQ weights are CC BY-NC (fine for this private proof, not for a commercial product).

## Steps (Terminal, ~20–30 min one-time, mostly downloads)
```bash
mkdir -p ~/AutoCue && cd ~/AutoCue
brew install uv ffmpeg                         # uv ~30 MB; skip ffmpeg if `ffmpeg -version` works
git clone https://github.com/ASLP-lab/SongFormer.git songformer && cd songformer && git checkout 139b2aa && git submodule update --init --recursive
uv venv -p 3.10 .venv && source .venv/bin/activate
uv pip install torch==2.4.0 torchaudio==2.4.0 transformers==4.51.1 x-transformers==2.4.14 muq==0.1.0 msaf==0.1.80 numpy==1.26.4 librosa==0.11.0 soundfile safetensors omegaconf loguru einops
# checkpoints (same files + md5 as the box; ckpts/md5sum.txt)
# either download per the SongFormer README, or I copy them from the box with CopyFromBox (1.4 GB, 2 files)
patch -p1 < songformer_cpu_device.patch        # I supply this file: reads SONGFORMER_DEVICE instead of hard-coded cuda
export HF_HOME=~/AutoCue/hf SONGFORMER_DEVICE=mps   # fall back to cpu if mps errors
```

## Run (reads songs, writes JSON only)
```bash
cd ~/AutoCue/songformer/src/SongFormer && source ~/AutoCue/songformer/.venv/bin/activate
export PYTHONPATH=../third_party:. HF_HOME=~/AutoCue/hf SONGFORMER_DEVICE=mps
ls ~/Music/"Master Music"/{the 20 test files}.mp3 > ~/AutoCue/test20.scp     # I generate this list exactly
python infer/infer.py -i ~/AutoCue/test20.scp -o ~/AutoCue/songformer_out --model SongFormer \
  --checkpoint SongFormer.safetensors --config_path SongFormer.yaml -gn 1 -tn 1 --debug
```
Times: box CPU did a 60 s clip in 43 s warm / 90 s cold. Estimate on M1 Pro: ~1–2 min per song on CPU, faster on MPS;
**~20–40 min for all 20** (first run adds model load). Peak RAM ~5–6 GB: close Serato/Chrome while it runs.
Songs are only read. Output: `~/AutoCue/songformer_out/<name>.json` (~5 KB each) — I copy those to the box with CopyToBox and
score them; nothing is written back to his library.

## Undo
`rm -rf ~/AutoCue` (and `brew uninstall uv` if wanted).

## v5 integration (Oct 15, 2026) — what happens with the output
- Nothing is installed yet. Disk need unchanged: **~4.5 GB in `~/AutoCue`, plus ~1 GB headroom for pip/HF caches during install
  → have at least 6 GB free first** (Pro had ~20 GB free, 98% full). Peak RAM 5–6 GB while running; close Serato/Chrome.
- Engine v5 (`proof/real/tools/rules_v5.py`, change 7 "consensus") already reads SongFormer when present, from either:
  1. `proof/<run>/cues/songformer/<nn>.json` (bar segments made by rules.py), or
  2. `proof/<run>/out/songformer/<nn>.json` — the **raw** `infer.py` output (list of `{start, end, label}` in seconds).
  So after the Mac run: copy `~/AutoCue/songformer_out/<name>.json` to the box (CopyToBox), rename each to the track
  number (`01.json` … for the 20-song set, `001.json` … for stress5) under `out/songformer/`, then rerun
  `venv-tools/bin/python real/tools/rules_v5.py [--root …]`. SongFormer then becomes a 4th/5th boundary source next to
  allin1, stems and energy; START/EXIT confidence = base +0.05 when every source agrees, −0.3×(share disagreeing) otherwise.
- For stress5 (326 songs) the .scp list is `stress5/paths.json` values; ~1–2 min/song on M1 Pro → ~6–10 h; run overnight.
