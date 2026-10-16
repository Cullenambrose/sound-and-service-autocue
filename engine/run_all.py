"""Overnight driver: one engine at a time, one track per process, RAM-gated.
- waits for MemAvailable >= gate before each track
- children get nice 10 + oom_score_adj 1000 (they die first, never another agent's work)
- if MemAvailable drops under FLOOR_MB while a child runs, the child is killed and retried later
- resumable: skips tracks whose output JSON exists
Logs JSONL to ../logs/runs.jsonl"""
import os, sys, time, json, subprocess, signal, threading, glob
REAL = "/workspace/research/dj-auto-cues/proof/real"
PROOF = os.path.dirname(REAL)
VA = f"{PROOF}/venv-allin1/bin/python"
FLOOR_MB = 500
MAX_WAIT_S = 40*60
DEADLINE = time.mktime(time.strptime("2026-10-12 23:30", "%Y-%m-%d %H:%M"))
BASE_GATE = {"beat_this": 900, "songformer": 3300, "allin1": 2200}  # fixed: start only with real headroom (shared box)
LOG = f"{REAL}/logs/runs.jsonl"
env = dict(os.environ, PROOF=PROOF, HF_HOME=f"{PROOF}/cache/hf", TORCH_HOME=f"{PROOF}/cache/torch",
           XDG_CACHE_HOME=f"{PROOF}/cache/xdg", HF_HUB_DISABLE_TELEMETRY="1", HF_HUB_OFFLINE="1",
           TRANSFORMERS_OFFLINE="1", PYTHONUNBUFFERED="1", MALLOC_ARENA_MAX="2")
def avail():
    for l in open("/proc/meminfo"):
        if l.startswith("MemAvailable"): return int(l.split()[1])//1024
def log(d):
    d["ts"] = time.strftime("%Y-%m-%d %H:%M:%S"); open(LOG, "a").write(json.dumps(d)+"\n"); print(json.dumps(d), flush=True)
def pre():
    os.nice(10)
    open("/proc/self/oom_score_adj", "w").write("1000")
def cmd_for(engine, wav, stem):
    out = f"{REAL}/out/{engine}"
    if engine == "beat_this": return [VA, f"{REAL}/tools/bt_one.py", wav, f"{out}/{stem}.json"], f"{out}/{stem}.json"
    if engine == "allin1": return [VA, f"{REAL}/tools/allin1_one.py", wav, out], f"{out}/{stem}.json"
    if engine == "songformer": return ["bash", f"{REAL}/tools/sf_one.sh", wav, out], f"{out}/{stem}.json"
def tree_rss_mb(pid):
    # sum RSS of pid and descendants
    tot = 0; todo = [pid]
    while todo:
        p = todo.pop()
        try:
            tot += int([l for l in open(f"/proc/{p}/status") if l.startswith("VmRSS")][0].split()[1])
            for t in os.listdir(f"/proc/{p}/task"):
                try: todo += [int(c) for c in open(f"/proc/{p}/task/{t}/children").read().split()]
                except Exception: pass
        except Exception: pass
    return tot//1024
def run_one(engine, wav, stem, gate):
    cmd, outjson = cmd_for(engine, wav, stem)
    waited = 0
    while avail() < gate:
        if waited == 0: log({"engine": engine, "track": stem, "event": "waiting_for_ram", "avail_mb": avail(), "gate_mb": gate})
        time.sleep(15); waited += 15
        if waited > MAX_WAIT_S: return {"rc": "ram_timeout"}
    t0 = time.time(); start_avail = avail()
    lf = open(f"{REAL}/logs/{engine}_{stem}.log", "w")
    p = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, env=env, preexec_fn=pre, start_new_session=True)
    st = {"peak": 0, "minavail": 10**9, "killed": False}
    def mon():
        while p.poll() is None:
            st["peak"] = max(st["peak"], tree_rss_mb(p.pid)); a = avail(); st["minavail"] = min(st["minavail"], a)
            if a < FLOOR_MB and not st["killed"]:
                st["killed"] = True; os.killpg(p.pid, signal.SIGKILL)
            time.sleep(0.25)
    th = threading.Thread(target=mon, daemon=True); th.start()
    rc = p.wait(); th.join()
    wall = time.time() - t0
    ok = rc == 0 and os.path.exists(outjson)
    r = {"engine": engine, "track": stem, "rc": rc, "ok": ok, "wall_s": round(wall, 1), "peak_rss_mb": st["peak"],
         "min_avail_mb": st["minavail"], "start_avail_mb": start_avail, "killed_low_ram": st["killed"], "waited_s": waited}
    log(r)
    if ok:
        for c in ([f"{PROOF}/venv-tools/bin/python", f"{REAL}/tools/rules.py"], [f"{PROOF}/venv-tools/bin/python", f"{REAL}/tools/build_review.py"]):
            subprocess.run(c, stdout=open(f"{REAL}/logs/build.log", "a"), stderr=subprocess.STDOUT)
    return r
def main():
    engines = sys.argv[1:] or ["beat_this", "songformer", "allin1"]
    wavs = sorted(glob.glob(f"{REAL}/wav/*.wav"))
    for engine in engines:
        os.makedirs(f"{REAL}/out/{engine}", exist_ok=True)
        gate = BASE_GATE[engine]; te = time.time()
        log({"engine": engine, "event": "engine_start", "avail_mb": avail()})
        tries = {}
        while time.time() < DEADLINE:
            todo = [w for w in wavs if not os.path.exists(cmd_for(engine, w, os.path.basename(w)[:-4])[1]) and tries.get(w, 0) < 6]
            if not todo: break
            for wav in todo:
                if time.time() > DEADLINE: break
                stem = os.path.basename(wav)[:-4]
                r = run_one(engine, wav, stem, gate)
                if r.get("rc") == "ram_timeout":
                    log({"engine": engine, "track": stem, "event": "ram_timeout_skip_for_now"}); continue
                oom = (not r.get("ok")) and (r.get("killed_low_ram") or r.get("min_avail_mb", 10**9) < 800 or r.get("rc") in (-9, 137))
                if oom:
                    # out-of-memory kill (box RAM is shared): not counted as a try; demand more headroom next time
                    gate = BASE_GATE[engine]
                    log({"engine": engine, "track": stem, "event": "oom_retry_later", "new_gate_mb": gate}); time.sleep(60)
                else:
                    tries[wav] = tries.get(wav, 0) + 1
                    if not r.get("ok"): time.sleep(30)
                pass
        log({"engine": engine, "event": "engine_done", "engine_wall_s": round(time.time()-te, 1)})
    log({"event": "all_done"})
    # always (re)build drafts + review page from whatever finished
    for c in (["%s/../venv-tools/bin/python" % REAL, f"{REAL}/tools/rules.py"], ["%s/../venv-tools/bin/python" % REAL, f"{REAL}/tools/build_review.py"]):
        subprocess.run(c, stdout=open(f"{REAL}/logs/build.log", "a"), stderr=subprocess.STDOUT)
    log({"event": "review_built"})
if __name__ == "__main__": main()
