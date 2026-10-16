#!/usr/bin/env bash
P=/workspace/research/dj-auto-cues/proof; R=$P/real; source $P/env.sh
for i in $(seq -w 1 20); do
  while [ ! -f $R/out/allin1/vocals/$i.wav ]; do grep -q "^$i FAIL" $R/logs/regen.txt && continue 2; sleep 20; done
  [ -f $R/out/words/$i.json ] && continue
  while [ $(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo) -lt 2500 ] || pgrep -f demucs.separate >/dev/null && [ $(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo) -lt 5000 ]; do sleep 15; done
  echo "$i wx $(date +%T)"; $P/venv-whisperx/bin/python $R/tools/wx_one.py $R/out/allin1/vocals/$i.wav $R/out/words/$i.json > $R/logs/wx_$i.log 2>&1 && echo "$i ok $(tail -1 $R/logs/wx_$i.log)" || echo "$i FAIL"
done; echo ALLDONE
