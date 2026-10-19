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

PYTHON="${PYTHON:-}"
for candidate in python3.12 python3.11 python3.10 python3; do
  if [[ -z "$PYTHON" ]] && command -v "$candidate" >/dev/null; then PYTHON="$candidate"; fi
done
echo "Using $($PYTHON --version) at $(command -v "$PYTHON")"

if ! command -v ffmpeg >/dev/null; then
  if command -v brew >/dev/null; then brew install ffmpeg
  else echo "ffmpeg is required. Install Homebrew (https://brew.sh) and run: brew install ffmpeg"; exit 1; fi
fi

[[ -d .venv ]] || "$PYTHON" -m venv .venv
PIP=".venv/bin/pip"
$PIP install --upgrade pip wheel setuptools

# Core: always needed
$PIP install numpy soundfile mutagen torch torchaudio
$PIP install "https://github.com/CPJKU/beat_this/archive/main.zip"   # beats and downbeats
$PIP install demucs                                                   # stems (vocals, drums, bass)

# Optional analyzers: each failure is reported, and the pipeline falls back (lower confidence).
$PIP install whisperx || echo "WARNING: WhisperX failed. Lyrics will use LRCLIB text and vocal onsets only."
$PIP install allin1 || echo "WARNING: allin1 failed. Section labels are unavailable (cues are capped to 'Needs a look')."
echo "allin1 needs NATTEN. If it is missing, build it: $PIP install natten (a source build; needs Xcode command line tools)."
echo "SongFormer is optional and not installed here. Its MuQ weights are non-commercial; see README."

echo
echo "Analyzer check:"
PYTORCH_ENABLE_MPS_FALLBACK=1 .venv/bin/python engine/analyze.py --check
echo
echo "Start the app with ./run.sh"
