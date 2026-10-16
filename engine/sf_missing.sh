#!/usr/bin/env bash
R=/workspace/research/dj-auto-cues/proof/real
for n in 02 03 06 08 09 12 13 14 16 17 18 19 20; do
  [ -f $R/out/songformer/$n.json ] && continue
  while [ $(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo) -lt 3500 ]; do sleep 20; done
  echo "$n start $(date +%T) avail=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)MB"
  bash $R/tools/sf_one.sh $R/wav/$n.wav $R/out/songformer > $R/logs/sf_$n.log 2>&1 && echo "$n ok $(date +%T)" || echo "$n FAIL $(date +%T)"
done; echo ALLDONE
