#!/usr/bin/env bash
# Installs the AutoCue analyzers on an Apple Silicon Mac into ./.venv (nothing system-wide).
# Re-run safely: finished steps are skipped. Check the result with: .venv/bin/python engine/analyze.py --check
#
# Approximate disk use:  Python packages ~3.5 GB (PyTorch ~0.9 GB)
# First-run model downloads (cached afterwards):
#   Beat This   ~80 MB     Demucs htdemucs ~80 MB     WhisperX small + aligner ~0.6 GB
#   all-in-one (sections) ~0.2 GB     SongFormer (optional) ~1.5 GB incl. MuQ weights
# Total: roughly 5-6 GB without SongFormer.
set -euo pipefail
cd "$(dirname "$0")"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "Note: this script targets Apple Silicon macOS. Continuing anyway; MPS will not be used."
fi

# Beat This needs Python 3.10+. Use one if present, otherwise fetch a private 3.11 with uv (nothing system-wide).
PYTHON=""
for candidate in python3.12 python3.11 python3.10; do
  if [[ -z "$PYTHON" ]] && command -v "$candidate" >/dev/null; then PYTHON="$candidate"; fi
done
if [[ ! -d .venv ]]; then
  if [[ -n "$PYTHON" ]]; then
    "$PYTHON" -m venv .venv
  else
    python3 -m pip install --user --quiet uv
    python3 -m uv venv --python 3.11 .venv
  fi
fi
.venv/bin/python --version
.venv/bin/python -m pip --version >/dev/null 2>&1 || .venv/bin/python -m ensurepip --upgrade >/dev/null 2>&1 || python3 -m uv pip install --python .venv/bin/python pip

if ! command -v ffmpeg >/dev/null; then
  if command -v brew >/dev/null; then brew install ffmpeg
  else
    # No Homebrew: use the static ffmpeg bundled with imageio-ffmpeg, linked inside .venv only.
    .venv/bin/python -m pip install --quiet imageio-ffmpeg
    ln -sf "$(.venv/bin/python -c 'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())')" .venv/bin/ffmpeg
  fi
fi

PIP=".venv/bin/python -m pip"
$PIP install --upgrade pip wheel setuptools

# Core: always needed
$PIP install numpy soundfile mutagen torch torchaudio
$PIP install "https://github.com/CPJKU/beat_this/archive/main.zip"   # beats and downbeats
$PIP install demucs                                                   # stems (vocals, drums, bass)

# Optional analyzers: each failure is reported, and the pipeline falls back (lower confidence).
$PIP install whisperx || echo "WARNING: WhisperX failed. Lyrics will use LRCLIB text and vocal onsets only."
$PIP install allin1 || echo "WARNING: allin1 failed. Section labels are unavailable (cues are capped to 'Needs a look')."

echo "SongFormer is optional and not installed here. Its MuQ weights are non-commercial; see README."

echo
echo "Analyzer check:"
PATH="$PWD/.venv/bin:$PATH" PYTORCH_ENABLE_MPS_FALLBACK=1 .venv/bin/python engine/analyze.py --check
echo
echo "Start the app with ./run.sh"
