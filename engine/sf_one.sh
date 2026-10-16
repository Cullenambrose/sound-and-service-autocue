#!/usr/bin/env bash
# SongFormer on one WAV -> out/songformer/<stem>.json
set -euo pipefail
PROOF=/workspace/research/dj-auto-cues/proof
source $PROOF/env.sh
export SONGFORMER_DEVICE=cpu CUDA_VISIBLE_DEVICES=
OUT=$2
SCP=$(mktemp /tmp/sf_XXXX.scp); echo "$1" > $SCP
cd $PROOF/src/SongFormer/src/SongFormer
export PYTHONPATH=../third_party:.:${PYTHONPATH:-}
$PROOF/venv-songformer/bin/python infer/infer.py -i "$SCP" -o "$OUT" \
  --model SongFormer --checkpoint SongFormer.safetensors --config_path SongFormer.yaml -gn 1 -tn 1 --debug
rm -f $SCP
