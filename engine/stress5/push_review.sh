#!/usr/bin/env bash
# Periodic push of cue-review-stress5/data.json to Cullen's Pro. Never touches corrections.json.
set -euo pipefail
SRC=/workspace/cue-review-stress5/data.json
LOG=/workspace/research/dj-auto-cues/proof/stress5/logs/push_review.log
MID=b9ca16b7-51e4-4755-aafc-795829ba68fc
DEST_DIR="${CUE_REVIEW_STRESS5_DIR:-$HOME/Desktop/cue-review-stress5}"
echo "push_review started $(date)" >> "$LOG"
while true; do
  if [ -f "$SRC" ]; then
    SZ=$(wc -c < "$SRC" | tr -d ' ')
    # CopyFromBox lands as ~/data.json — parent agent / this script uses a marker file for the executor to push
    echo "READY_PUSH $SZ $(date -Iseconds)" > /tmp/stress5_push_request
    echo "requested push size=$SZ at $(date)" >> "$LOG"
  fi
  sleep 2700   # 45 min
done
