"""Synthetic unit tests for the cue rules (no audio). Run: venv-tools/bin/python test_rules.py"""
import numpy as np, rules
BPM = 120.0; BAR = 2.0
class FakeGrid:
    def __init__(s): s.bpm=BPM; s.spb=0.5; s.bar=BAR; s.t1=0.0; s.offset=0.0; s.fit=1.0; s.healthy=True; s.ratio=1; s.flags=[]; s.source="serato"; s.phase=0; s.votes=[1,0,0,0]
    bar_of=lambda s,t:(t-s.t1)/s.bar; tbar=lambda s,j:s.t1+j*s.bar; beat_of=lambda s,t:(t-s.t1)/s.spb; tbeat=lambda s,k:s.t1+k*s.spb
class FakeTrack(rules.Track):
    def __init__(s, segs, n_bars, ending="outro", t_on=0.0, vocals_from=None, drums=True, energy=None):
        s.nn="T"; s.fps=50; s.grid=FakeGrid(); s.dur=n_bars*BAR+ (0 if ending=="cold" else 4); n=int(s.dur*s.fps)
        s.db=np.full(n,-6.0); s.ref=0.0; s.peak=np.full(n,0.5); s.t_on=t_on; s.t_off=n_bars*BAR
        if ending=="fade":
            k=int(16*BAR*s.fps); s.db[int(s.t_off*s.fps)-k:int(s.t_off*s.fps)]=np.linspace(-6,-30,k)
        s.db[int(s.t_off*s.fps):]=-80
        if energy:
            for (a,b,v) in energy: s.db[int(a*BAR*s.fps):int(b*BAR*s.fps)]=v
        s.sf=[{"label":l,"start":a*BAR,"end":b*BAR} for l,a,b in segs]; s.a1=None; s.acts=None
        s.stem_hop=0.05; m=int(s.dur/0.05)+1
        v=np.zeros(m); 
        if vocals_from is not None: v[int(vocals_from*BAR/0.05):int(s.t_off/0.05)]=1.0
        v[0]=0.0; v[-1]=1.0  # p95 reference
        d=np.ones(m) if drums else np.zeros(m); d[-1]=1
        s.stems={"vocals":v,"drums":d,"bass":np.ones(m),"other":np.ones(m)}
def run(tr):
    d=rules.draft(tr,"songformer"); return {k:(c["status"],c["label"],c.get("bar") if c.get("bar") is not None else ("beat",c.get("beat"))) for k,c in d["cues"].items()}, d
ok=0; fails=[]
def check(name, got, exp):
    global ok
    for k,v in exp.items():
        if got[k][:len(v)]!=v: fails.append(f"{name}: {k} got {got[k]} expected {v}"); return
    ok+=1
# 1 pop: 16-bar intro, verse, chorus 8, verse, chorus 8, chorus 8 (double last), outro 16
segs=[("intro",0,16),("verse",16,32),("chorus",32,40),("verse",40,56),("chorus",56,64),("chorus",64,72),("outro",72,88)]
g,_=run(FakeTrack(segs,88,vocals_from=16)); check("pop double last chorus", g, {"A":("set","16",0),"B":("set","start",16),"G":("set","hook",32),"C":("set","last 16",56),"D":("set","out",72),"H":("set","go",56),"E":("set","loop",0)})
# 2 32-bar DJ intro (edge 8): A = B-16
segs=[("intro",0,32),("verse",32,48),("chorus",48,56),("verse",56,64),("chorus",64,72),("outro",72,104)]
g,_=run(FakeTrack(segs,104,vocals_from=32)); check("32-bar DJ intro", g, {"A":("set","16",16),"B":("set","start",32),"C":("set","last 8",64),"D":("set","out",72),"H":("set","go",56)})
# 3 short 8-bar intro, A = first downbeat label 8
segs=[("intro",0,8),("verse",8,24),("chorus",24,32),("verse",32,40),("chorus",40,48),("outro",48,56)]
g,_=run(FakeTrack(segs,56,vocals_from=8)); check("8-bar intro", g, {"A":("set","8",0),"B":("set","start",8)})
# 4 pickup: music starts 2 beats before bar 0 -> grid shifted so bar 1 is the first full bar
tr=FakeTrack([("intro",0,5),("verse",5,21),("chorus",21,29),("verse",29,37),("chorus",37,45),("outro",45,53)],53,vocals_from=5,t_on=1.0)
g,_=run(tr); check("pickup intro 'the 3'", g, {"A":("set","the 3"),"B":("set","start",5)})
# 5 hip-hop ends on a verse: D = end of last hook
segs=[("intro",0,8),("verse",8,24),("chorus",24,32),("verse",32,48),("chorus",48,56),("verse",56,72)]
g,_=run(FakeTrack(segs,72,vocals_from=8)); check("hip-hop ends on verse", g, {"C":("set","last 8",48),"D":("set","out",56),"H":("set","go",40)})
# 6 EDM: intro straight into drop, B = G
segs=[("intro",0,16),("chorus",16,32),("inst",32,48),("chorus",48,64),("outro",64,80)]
g,_=run(FakeTrack(segs,80,vocals_from=None)); check("EDM B=G", g, {"B":("set","start",16),"G":("set","hook",16),"C":("set","last 16",48),"D":("set","out",64),"H":("set","go",48)})
# 7 no chorus (instrumental): C,G empty; D at outro
segs=[("intro",0,8),("verse",8,24),("bridge",24,40),("verse",40,56),("outro",56,64)]
g,_=run(FakeTrack(segs,64,vocals_from=None,energy=[(8,56,-8)])); check("no chorus", g, {"C":("empty",),"G":("empty",),"D":("set","out",56)})
# 8 too short for H: go 8
segs=[("intro",0,4),("verse",4,8),("chorus",8,16),("chorus",16,20)]  # merged chorus 8..20, ends at end
segs=[("intro",0,4),("verse",4,12),("chorus",12,16),("verse",16,20),("chorus",20,24),("outro",24,28)]
g,_=run(FakeTrack(segs,28,vocals_from=4)); check("go 12/8", g, {"H":("set","go 12",12)})
# 9 cold ending
segs=[("intro",0,8),("verse",8,24),("chorus",24,32),("verse",32,48),("chorus",48,64)]
g,_=run(FakeTrack(segs,64,ending="cold",vocals_from=8)); check("cold ending", g, {"D":("set","cold")})
# 10 fade ending
g,_=run(FakeTrack(segs,64,ending="fade",vocals_from=8)); check("fade ending", g, {"D":("set","out")})
# 11 hot start (no intro): A empty
segs=[("verse",0,16),("chorus",16,24),("verse",24,40),("chorus",40,48),("outro",48,56)]
g,_=run(FakeTrack(segs,56,vocals_from=0)); check("hot start", g, {"A":("empty",),"B":("set","start",0)})
# 12 vocals in intro -> E goes to outro
segs=[("intro",0,8),("verse",8,24),("chorus",24,32),("verse",32,40),("chorus",40,48),("outro",48,56)]
tr=FakeTrack(segs,56,vocals_from=0); tr.stems["vocals"][int(48*BAR/0.05):]=0; tr.stems["vocals"][-1]=1
g,_=run(tr); check("loop falls back to outro", g, {"E":("set","loop",48)})
# 13 F always empty
check("F empty", g, {"F":("empty",)})
print(f"{ok} passed, {len(fails)} failed"); [print(" FAIL", f) for f in fails]
