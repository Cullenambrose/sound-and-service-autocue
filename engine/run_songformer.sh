#!/usr/bin/env bash
# CPU run of SongFormer's own infer.py (debug = single in-process worker).
set -euo pipefail
PROOF=/workspace/research/dj-auto-cues/proof
source $PROOF/env.sh
export SONGFORMER_DEVICE=cpu CUDA_VISIBLE_DEVICES=
cd $PROOF/src/SongFormer/src/SongFormer
export PYTHONPATH=../third_party:.:${PYTHONPATH:-}
rm -f $PROOF/out/songformer/*.json
echo "START $(date "+%Y-%m-%d %H:%M:%S.%3N")"
time $PROOF/venv-songformer/bin/python infer/infer.py -i "$1" -o $PROOF/out/songformer \
  --model SongFormer --checkpoint SongFormer.safetensors --config_path SongFormer.yaml -gn 1 -tn 1 --debug
echo "END $(date "+%Y-%m-%d %H:%M:%S.%3N")"
