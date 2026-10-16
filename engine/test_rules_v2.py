import rules_v2 as r
r.PROFILE["use_lyrics"] = False  # unit tests run on synthetic grids only
G = {"t1": 0.0, "bar_s": 2.0, "offset_ms": 0}
flat = lambda j: -5.0
def mk(B=8, D=40, conf=0.95, ch=(8, 16, 32)):
    segs = [{"cls": "intro", "j0": 0, "j1": B}] + [{"cls": "chorus", "j0": j, "j1": j + 8} for j in ch]
    return {"engine": "t", "grid": G, "segments": segs, "cues": {"B": {"status": "set", "bar": B, "confidence": conf}, "D": {"status": "set", "bar": D, "confidence": conf}}}
def run(d, lead=16, e=flat):
    r.bar_energy = lambda nn, g: (e, 300.0); return r.build(d, "xx", lead)["cues"]
def test_layout():
    c = run(mk(B=12), 16); assert c["A"]["bar"] == 4 and c["A"]["name"] == "8BS" and c["G"]["bar"] == c["H"]["bar"] - 8
    assert c["E"]["status"] == c["F"]["status"] == "empty"
def test_a_rule():
    c = run(mk(B=8)); assert c["A"]["name"] == "8BS" and c["A"]["bar"] == 0
    c = run(mk(B=6)); assert c["A"]["name"] == "4BS" and c["A"]["bar"] == 2
    g = {"t1": 0.5, "bar_s": 2.0, "offset_ms": 0}
    c = run({**mk(B=7), "grid": g}); assert c["A"]["name"] == "4BS" and c["A"]["bar"] == 3
    r.PROFILE["min_start_bar"] = 3
    try:
        c = run(mk(B=3)); assert c["A"]["name"] == "2BS" and c["A"]["bar"] == 1
        c = run(mk(B=1)); assert c["A"]["name"] == "1BS"
        c = run({**mk(B=1), "grid": {"t1": -0.5, "bar_s": 2.0, "offset_ms": 0}}); assert c["A"]["status"] == "empty" and c["A"]["flags"]
    finally: r.PROFILE["min_start_bar"] = 8
def test_cd_never_auto():
    c = run(mk()); assert c["C"]["status"] == "suggest" and c["D"]["status"] == "suggest" and c["B"]["status"] == "set"
def test_first_chorus_not_on_start():
    c = run(mk(B=8, ch=(8, 16, 32))); assert c["C"]["bar"] == 16 and c["D"]["bar"] == 32
def test_no_intro():
    c = run(mk(B=0)); assert c["B"]["bar"] == 8 and c["B"]["status"] == "review"
def test_exit_tail_bar():
    e = lambda j: -5.0 if j <= 40 else -12.0
    assert run(mk(D=40), e=e)["H"]["bar"] == 41
    e2 = lambda j: -5.0 if j < 40 else -12.0
    assert run(mk(D=40), e=e2)["H"]["bar"] == 40
def test_go_16_option():
    c = r.build(mk(), "xx", 16, 16)["cues"]; assert c["G"]["bar"] == c["H"]["bar"] - 4

def test_loops_suggest_only():
    r._stems = lambda nn: None
    d = r.build(mk(B=12), "xx", 16)
    lp = {l["loop"]: l for l in d["loops"]}
    assert lp["intro"]["bar"] == 4 and lp["intro"]["status"] == "suggest" and lp["intro"]["beats"] == 16
    assert all(l["status"] in ("suggest", "empty") for l in d["loops"])
