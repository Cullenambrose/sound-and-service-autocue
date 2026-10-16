#!/usr/bin/env bash
# heavy per-track steps, one track at a time, RAM-gated. args: worker_index n_workers
P=/workspace/research/dj-auto-cues/proof; S=$P/stress5; source $P/env.sh
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 MALLOC_ARENA_MAX=2 A1_MODEL=harmonix-fold0 PYTHONUNBUFFERED=1
W=$1; N=$2; k=0
for id in $(python3 -c "import json;print(' '.join(sorted(json.load(open('$S/paths.json')))))"); do
  k=$((k+1)); [ $(( (k-1) % N )) -eq $W ] || continue
  f=$(ls $S/in/$id.* 2>/dev/null | head -1)
  while [ -z "$f" ]; do sleep 20; f=$(ls $S/in/$id.* 2>/dev/null | head -1); done   # wait for copy
  [ -f $S/out/allin1/$id.json ] && continue
  while [ $(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo) -lt 3000 ]; do sleep 15; done
  t0=$(date +%s)
  [ -f $S/wav/$id.wav ] || ffmpeg -y -loglevel error -i "$f" -ac 2 -ar 44100 -sample_fmt s16 $S/wav/$id.wav || { echo "$id FAIL decode"; continue; }
  python3 $S/tools/features.py $id >> $S/logs/feat.log 2>&1 || { echo "$id FAIL features"; continue; }
  [ -f $S/out/beat_this/$id.json ] || nice -n 10 $P/venv-allin1/bin/python $P/real/tools/bt_one.py $S/wav/$id.wav $S/out/beat_this/$id.json > $S/logs/bt_$id.log 2>&1 || { echo "$id FAIL beat_this"; continue; }
  nice -n 10 $P/venv-allin1/bin/python $S/tools/allin1_one.py $S/wav/$id.wav $S/out/allin1 > $S/logs/a1_$id.log 2>&1 || { echo "$id FAIL allin1"; continue; }
  rm -rf $S/out/allin1/vocals/$id.wav
  echo "$id ok $(( $(date +%s)-t0 ))s $(date +%T)"
done; echo WORKER $W DONE
