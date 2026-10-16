#!/usr/bin/env bash
S=/workspace/research/dj-auto-cues/proof/stress5
LOG=$S/logs/monitor.log
SRC=/workspace/cue-review-stress5/data.json
echo "monitor start $(date)" >> "$LOG"
FIRST4_REPORTED=0
LAST_PUSH=0
while true; do
  NOW=$(date +%s)
  # first4: ids 001-106 that appear in non-disco crates — use unique first4 set
  python3 - <<'PY' > /tmp/stress5_progress.json
import os,csv,json,time
S="/workspace/research/dj-auto-cues/proof/stress5"
man=list(csv.DictReader(open(f"{S}/MANIFEST.csv")))
first4=sorted({r["id"] for r in man if r["crate"]!="Disco Dance Party"})
crates={}
for r in man: crates.setdefault(r["crate"],set()).add(r["id"])
def done(ids):
  return sum(1 for i in ids if os.path.exists(f"{S}/out/allin1/{i}.json"))
out={
  "ts": time.strftime("%Y-%m-%d %H:%M:%S PT"),
  "first4_done": done(first4), "first4_total": len(first4),
  "all_done": done([f"{i:03d}" for i in range(1,327)]), "all_total": 326,
  "by_crate": {c: {"done": done(ids), "total": len(ids)} for c,ids in crates.items()},
  "workers": os.popen("pgrep -c -f 'tools/worker.sh [01] 2'").read().strip(),
  "gem_cost": json.load(open(f"{S}/stats.json")).get("gem_cost") if os.path.exists(f"{S}/stats.json") else None,
  "gem_sent": json.load(open(f"{S}/stats.json")).get("gem_sent") if os.path.exists(f"{S}/stats.json") else None,
}
# missing first4
out["first4_missing"]=[i for i in first4 if not os.path.exists(f"{S}/out/allin1/{i}.json")][:20]
out["first4_complete"]= out["first4_done"]==out["first4_total"]
json.dump(out, open("/tmp/stress5_progress.json","w"), indent=1)
print(json.dumps(out))
PY
  # request push every 45 min
  if [ -f "$SRC" ] && [ $((NOW - LAST_PUSH)) -ge 2700 ]; then
    SZ=$(wc -c < "$SRC" | tr -d ' ')
    echo "PUSH_NEEDED $SZ $(date -Iseconds)" > /tmp/stress5_push_request
    echo "push requested size=$SZ $(date)" >> "$LOG"
    LAST_PUSH=$NOW
  fi
  # milestone flag
  if python3 -c "import json;d=json.load(open('/tmp/stress5_progress.json'));raise SystemExit(0 if d['first4_complete'] else 1)"; then
    if [ "$FIRST4_REPORTED" -eq 0 ]; then
      echo "FIRST4_COMPLETE $(date)" > /tmp/stress5_first4_done
      echo "first4 complete $(date)" >> "$LOG"
      FIRST4_REPORTED=1
    fi
  fi
  # all done?
  if ! pgrep -f "tools/worker.sh [01] 2" >/dev/null; then
    # wait for supervise to finish post
    if grep -q "ALL DONE" "$S/logs/post.log" 2>/dev/null; then
      echo "ALL_COMPLETE $(date)" > /tmp/stress5_all_done
      SZ=$(wc -c < "$SRC" | tr -d ' ')
      echo "PUSH_NEEDED $SZ $(date -Iseconds) FINAL" > /tmp/stress5_push_request
      echo "all complete $(date)" >> "$LOG"
      break
    fi
  fi
  sleep 60
done
