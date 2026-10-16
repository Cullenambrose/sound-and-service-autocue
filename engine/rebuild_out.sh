#!/usr/bin/env bash
# out/ was lost (folder restored without it at 11:38). Rebuild stems -> words -> LRCLIB, one song at a time, checkpointed.
P=/workspace/research/dj-auto-cues/proof; R=$P/real; source $P/env.sh; mkdir -p $R/out/allin1 $R/out/words $R/out/lrclib
while pgrep -f lead_all.sh >/dev/null; do sleep 20; done
for i in $(seq -w 1 20); do
  while [ $(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo) -lt 3000 ]; do sleep 15; done
  [ -f $R/out/allin1/vocals/$i.wav ] || $P/venv-allin1/bin/python $R/tools/stems_one.py $R/wav/$i.wav $R/out/allin1 > $R/logs/stems_$i.log 2>&1
  L=en; [ $i = 05 ] && L=es
  [ -f $R/out/words/$i.json ] || $P/venv-whisperx/bin/python $R/tools/wx_one.py $R/out/allin1/vocals/$i.wav $R/out/words/$i.json $L > $R/logs/wx_$i.log 2>&1
  echo "$i $(date +%T) $(tail -1 $R/logs/wx_$i.log)"
done
python3 $R/tools/lrclib.py > $R/logs/lrclib.txt 2>&1; echo ALLDONE
