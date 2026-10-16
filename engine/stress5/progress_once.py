import os,csv,json,time,subprocess
S="/workspace/research/dj-auto-cues/proof/stress5"
man=list(csv.DictReader(open(f"{S}/MANIFEST.csv")))
order=[]; crates={}; seen=set()
for r in man:
  crates.setdefault(r["crate"],[]).append(r["id"])
  if r["crate"] not in seen: order.append(r["crate"]); seen.add(r["crate"])
def done(ids): return sum(1 for i in ids if os.path.exists(f"{S}/out/allin1/{i}.json"))
def pcount(pat):
  try:
    r=subprocess.run(["pgrep","-f",pat], capture_output=True, text=True)
    return len([l for l in r.stdout.splitlines() if l.strip()])
  except Exception: return 0
st={}
if os.path.exists(f"{S}/stats.json"):
  try: st=json.load(open(f"{S}/stats.json"))
  except Exception: st={}
all_done=done([f"{i:03d}" for i in range(1,327)])
post_tail=""
if os.path.exists(f"{S}/logs/post.log"):
  post_tail=open(f"{S}/logs/post.log").read()[-3000:]
out={
  "ts": time.strftime("%Y-%m-%d %H:%M:%S PT"),
  "all_done": all_done, "all_total": 326,
  "crates": {c: {"done": done(v), "total": len(v)} for c,v in ((c,crates[c]) for c in order)},
  "gem_sent": st.get("gem_sent"), "gem_cost": st.get("gem_cost"),
  "A": st.get("A"), "conf": st.get("conf"), "gem_agree": st.get("gem_agree"), "gem_by": st.get("gem_by"),
  "workers": pcount("tools/worker.sh"),
  "supervise": pcount("tools/supervise.sh"),
  "001": os.path.exists(f"{S}/out/allin1/001.json"),
  "all_complete_flag": "ALL DONE" in post_tail,
}
out["analyzers_done"] = all_done >= 326
out["ready"] = all_done >= 326 and (out["all_complete_flag"] or (out["workers"]==0 and out["supervise"]==0))
json.dump(out, open("/tmp/stress5_round.json","w"), indent=1)
print(json.dumps({k:out[k] for k in ("ts","all_done","001","workers","supervise","gem_cost","analyzers_done","ready","crates")}, indent=1))
