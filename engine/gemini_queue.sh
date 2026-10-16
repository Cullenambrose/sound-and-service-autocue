#!/usr/bin/env bash
# Resume Gemini after the free-tier daily quota resets (~4:58 PM PT Oct 15). One song at a time; checkpointed per song.
R=/workspace/research/dj-auto-cues/proof/real; cd $R
while [ $(date +%H%M) -lt 1705 ]; do sleep 60; done
for n in 03 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20; do
  GEM_MODEL=gemini-3-flash-preview python3 tools/gemini_step.py $n >> logs/gemini_queue.txt 2>&1
  grep -q "429" gemini/$n.json && { echo "$n quota hit again $(date +%T), stopping" >> logs/gemini_queue.txt; break; }
  sleep 20
done
python3 tools/build_cue_review.py >> logs/gemini_queue.txt 2>&1   # refresh /workspace/cue-review/data.json
echo "QUEUE DONE $(date +%T)" >> logs/gemini_queue.txt
