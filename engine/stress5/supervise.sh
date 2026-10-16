#!/usr/bin/env bash
# refresh review data every 10 min while workers run; then retry failures, lead-vocal pass, final rebuild
S=/workspace/research/dj-auto-cues/proof/stress5; cd $S
while pgrep -f "tools/worker.sh [01] 2" >/dev/null; do python3 tools/post.py --gemini >> logs/post.log 2>&1; sleep 600; done
tools/worker.sh 0 1 >> logs/worker_retry.log 2>&1
tools/lead_s5.sh >> logs/lead.log 2>&1
python3 tools/post.py --gemini >> logs/post.log 2>&1; echo "ALL DONE $(date)" >> logs/post.log
