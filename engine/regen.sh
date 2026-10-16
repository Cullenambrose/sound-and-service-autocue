#!/usr/bin/env bash
# one song at a time, memory-gated: Beat This! then allin1 (Demucs stems kept as 16 kHz vocals)
P=/workspace/research/dj-auto-cues/proof; R=$P/real; source $P/env.sh
mkdir -p $R/out/beat_this $R/out/allin1
gate(){ while [ $(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo) -lt $1 ]; do sleep 15; done; }
for i in $(seq -w 1 20); do
  [ -f $R/out/beat_this/$i.json ] || { gate 1500; $P/venv-allin1/bin/python $R/tools/bt_one.py $R/wav/$i.wav $R/out/beat_this/$i.json > $R/logs/bt2_$i.log 2>&1; }
  [ -f $R/out/allin1/vocals/$i.wav ] || { gate 3000; echo "$i stems $(date +%T)"; $P/venv-allin1/bin/python $R/tools/stems_one.py $R/wav/$i.wav $R/out/allin1 > $R/logs/a1v2_$i.log 2>&1 && echo "$i ok" || echo "$i FAIL"; }
done; echo ALLDONE
