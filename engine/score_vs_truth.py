import json,os,statistics as st
T=json.load(open('truth_tags.json'))
# manual semantic classification of Cullen's cues (slot letter -> category), from his labels/positions
M={
'01':{'A':'INTRO','B':'START','D':'FC','F':'LC','G':'GO','H':'EXIT'},
'02':{'A':'PRE','B':'START','D':'FC','F':'LC','G':'GO','H':'EXIT'},
'03':{'A':'PRE','B':'START','C':'FC','E':'LC','G':'GO','H':'EXIT'},
'04':{'A':'PRE','B':'START','C':'FC','D':'LC','G':'GO','H':'EXIT'},
'05':{'A':'PRE','B':'START','D':'FC','F':'LC','G':'GO','H':'EXIT'},
'06':{'A':'PRE','B':'START','C':'FC','E':'LC','G':'GO','H':'EXIT'},
'07':{'A':'PRE','B':'START','C':'FC','E':'LC','G':'GO','H':'EXIT'},
'08':{'A':'PRE','B':'START','G':'GO','H':'EXIT'},
'09':{'A':'PRE','B':'START','D':'FC','E':'LC','G':'GO','H':'EXIT'},
'10':{'A':'PRE','B':'START','H':'EXIT'},
'11':{'B':'PRE','C':'START','F':'FC','G':'GO','H':'EXIT'},
'12':{'A':'INTRO','C':'FC','G':'GO','H':'EXIT'},
'13':{'A':'PRE','B':'START','C':'FC','G':'GO','H':'EXIT'},
'14':{'A':'PRE','B':'START','G':'GO','H':'EXIT'},
'15':{'A':'PRE','B':'START','D':'FC','G':'GO','H':'EXIT'},
'16':{'A':'PRE','B':'START','C':'FC','G':'GO','H':'EXIT'},
'17':{'A':'INTRO','C':'PRE','D':'START','E':'FC','F':'LC','G':'GO','H':'EXIT'},
'18':{'A':'PRE','B':'START','G':'GO','H':'EXIT'},
'19':{'A':'INTRO','B':'PRE','C':'START','D':'FC','E':'LC','G':'GO','H':'EXIT'},
'20':{'A':'START','C':'FC','F':'LC','G':'GO','H':'EXIT'},
}
# engine slot compared to each truth category
PAIR=[('A','PRE'),('A','INTRO'),('B','START'),('G','FC'),('C','LC'),('H','GO'),('D','EXIT')]
rows=[]
for eng in ['allin1','songformer']:
  for nn in sorted(M):
    p=f'cues/{eng}/{nn}.json'
    if not os.path.exists(p): continue
    ec=json.load(open(p))['cues']
    bpm=T[nn]['serato_beatgrid'][-1]['bpm']; beat=60/bpm
    tc={c['index']:c for c in T[nn]['markers2'] if c['type']=='CUE'}
    truth={cat:tc['ABCDEFGH'.index(s)]['ms']/1000 for s,cat in M[nn].items()}
    for es,cat in PAIR:
      if cat not in truth: continue
      e=ec.get(es,{})
      t=e.get('time_serato') if e.get('status')=='set' else None
      d=None if t is None else (t-truth[cat])/beat
      rows.append(dict(eng=eng,nn=nn,slot=es,cat=cat,truth=truth[cat],eng_t=t,diff_beats=d,conf=e.get('confidence'),reason=e.get('reason','')[:110]))
json.dump(rows,open('score_rows.json','w'),indent=1)
def summ(rs):
  n=len(rs); b=sum(1 for r in rs if r['diff_beats'] is not None and abs(r['diff_beats'])<=1.0); br=sum(1 for r in rs if r['diff_beats'] is not None and abs(r['diff_beats'])<=4.0)
  return n,b,br
for eng in ['allin1','songformer']:
  print('==',eng)
  for es,cat in PAIR:
    rs=[r for r in rows if r['eng']==eng and r['slot']==es and r['cat']==cat]
    n,b,br=summ(rs); 
    if n: print(f'  {es} vs his {cat:5} n={n:2}  beat {b}/{n} ({100*b//n}%)  bar {br}/{n} ({100*br//n}%)')
  rs=[r for r in rows if r['eng']==eng and r['cat']!='INTRO']; n,b,br=summ(rs); print(f'  ALL n={n} beat {b} ({100*b//n}%) bar {br} ({100*br//n}%)')
# head-to-head on tracks both ran
both={r['nn'] for r in rows if r['eng']=='songformer'}
print('== head-to-head tracks',sorted(both))
for eng in ['allin1','songformer']:
  rs=[r for r in rows if r['eng']==eng and r['nn'] in both and r['cat']!='INTRO']; n,b,br=summ(rs); print(f'  {eng}: n={n} beat {b} bar {br}')
print('== confidence buckets (allin1, excl INTRO)')
rs=[r for r in rows if r['eng']=='allin1' and r['cat']!='INTRO' and r['conf'] is not None]
for lo,hi in [(0,.6),(.6,.7),(.7,.8),(.8,.9),(.9,1.01)]:
  s=[r for r in rs if lo<=r['conf']<hi]; n,b,br=summ(s)
  if n: print(f'  {lo}-{hi}: n={n} beat {b} ({100*b//n}%) bar {br} ({100*br//n}%)')
print('== all rows allin1')
for r in rows:
  if r['eng']=='allin1': print(r['nn'],r['slot'],r['cat'],round(r['truth'],2),r['eng_t'] and round(r['eng_t'],2),None if r['diff_beats'] is None else round(r['diff_beats'],1),r['conf'],'|',r['reason'])
