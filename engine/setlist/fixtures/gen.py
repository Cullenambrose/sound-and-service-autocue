import json
G=["Pop","Hip Hop","R&B","Rock","Dance / EDM","House","Latin","Country","Throwback","Slow / Ballad"]
lib=[];n=0
def t(title,artist,genre,bpm,version=None,key="8A",dur=210):
    global n;n+=1
    full=f"{title} ({version})" if version else title
    lib.append({"id":f"trk-{n:03d}","file_id":f"file-{n:03d}","path":f"/Demo/Music/{artist} - {full}.mp3",
      "title":full,"artist":artist,"version":version,"genre":genre,"bpm":bpm,"key":key,"duration_s":dur})
# moments
t("Golden Aisle","The Lantern Strings","Slow / Ballad",72,"Intro")
t("Here We Come","Neon Parade","Pop",124,"Clean")
t("Slow Orbit","Maya Vale","Slow / Ballad",68)
t("Slow Orbit","Maya Vale","Slow / Ballad",68,"Short Edit")
t("Porch Light","Dusty Rivers","Country",80)
t("Mama's Waltz","Rosa Linden","Slow / Ballad",90)
t("Sugar Frosting","The Bakers Club","Throwback",116)
t("Last Call Lullaby","Echo Harbor","Slow / Ballad",76,"Extended Mix")
# radio-edit unavailable: only Extended + Clean exist
t("Midnight Signal","Kilo Avenue","Dance / EDM",126,"Extended Mix")
t("Midnight Signal","Kilo Avenue","Dance / EDM",126,"Clean")
# clean vs extended conflict
t("Rooftop Fever","DJ Solano","House",122,"Clean")
t("Rooftop Fever","DJ Solano","House",122,"Extended Mix")
# decoy similar title / same artist
t("Shine On","Velvet Coast","R&B",98,"Clean")
t("Shine On Me","Velvet Coast","R&B",100,"Clean")
t("Shine Only","Brightside Kids","Pop",118)
# title-only ban Hello: two artists
t("Hello","Ada Moon","Slow / Ballad",79)
t("Hello","The Static Hearts","Rock",140,"Clean")
t("Hello Again","Ada Moon","Pop",112)  # must NOT be banned
# do-not-play beats must-play
t("Chicken Shuffle","Barnyard Brass","Throwback",128,"Clean")
t("Cha Cha Line","Floor Captains","Hip Hop",100,"Clean")
t("Loud Ex","Grudge Machine","Rock",150,"Dirty")
# ambiguous identity: same title, two artists, only one has Re-Drum
t("Night Drive","Amber Lane","Pop",116,"Clean")
t("Night Drive","Cobalt Youth","Rock",118,"Re-Drum")
# fillers to reach ~60 across genres
names=["Velocity","Paper Moons","Coastline","Blue Static","Fuego Lento","Back Road","Gold Chain","Satin Hour","Arcade Love","Hands Up","Sunday Drive","Low Tide","Northern Lights","Bassment","Corazon","Two Step","Mixtape 98","Candle Song","Firework City","Deep End","Bounce Back","Mirror Ball","Dust & Denim","Late Bloom","Roller Rink","Ocean Eyes Club","Pulse","Bailando Hoy","Gravel","Velour","After Hours","Horizon","Spinback","Quiet Storm","Carnival","Wildflower","Night Bus","Jukebox Heart","Static Bloom"]
bpms={"Pop":118,"Hip Hop":95,"R&B":92,"Rock":132,"Dance / EDM":128,"House":124,"Latin":102,"Country":110,"Throwback":120,"Slow / Ballad":70}
vers=[None,"Clean","Extended Mix","Short Edit","Intro","Re-Drum"]
for i,nm in enumerate(names):
    g=G[i%10]; t(nm,f"Demo Artist {i+1:02d}",g,bpms[g]+(i%5)-2,vers[i%6])
# g-10 control: same bare title, two artists, no version text (appended to keep earlier ids stable)
t("Moon Harbor","Iris Dunn","Pop",112)
t("Moon Harbor","The Tidewater Band","Country",108)
assert all(x["genre"] in G for x in lib)
R=lambda i,ti,a,v=None,**k:{"id":i,"title":ti,"artist":a,"version":v,**k}
ev={"inquiry_id":"DEMO-INQ-0001","event_date":"2026-06-20","event_name":"Demo Wedding — Synthetic Fixture",
 "client_name":"Demo Couple (synthetic)","venue":"Demo Barn Venue (fictional)","locked":False,
 "planner":{"status":"draft","song_cues":[]},
 "event_timelines":{"items":[
  {"name":"Processional","kind":"processional","start_time":"16:00","end_time":"16:05","duration_minutes":5,"song":R("tl-1","Golden Aisle","The Lantern Strings","Intro")},
  {"name":"Grand Entrance","kind":"entrance","start_time":"17:30","duration_minutes":3,"song":R("tl-2","Here We Come","Neon Parade","Clean")},
  {"name":"First Dance","kind":"songCue","start_time":"17:35","duration_minutes":4,"song":R("tl-3","Slow Orbit","Maya Vale","Short Edit"),"songCue":{"fade":8,"seconds":150}},
  {"name":"Fade Note","kind":"songCue","songCue":{"version":"Extended","seconds":12}},
  {"name":"Father-Daughter Dance","kind":"parent_dance","start_time":"17:45","duration_minutes":4,"song":R("tl-4","Porch Light","Dusty Rivers")},
  {"name":"Mother-Son Dance","kind":"parent_dance","start_time":"17:50","duration_minutes":4,"song":R("tl-5","Mama's Waltz","Rosa Linden")},
  {"name":"Cake Cutting","kind":"cake","start_time":"19:00","duration_minutes":5,"song":R("tl-6","Sugar Frosting","The Bakers Club")},
  {"name":"Last Dance","kind":"last_dance","start_time":"22:55","duration_minutes":5,"song":R("tl-7","Last Call Lullaby","Echo Harbor","Extended Mix")}]},
 "music_lists":{
  "must_play":[R("mp-1","Velocity","Demo Artist 01"),R("mp-2","Paper Moons","Demo Artist 02","Clean"),R("mp-3","Hands Up","Demo Artist 10"),
   R("mp-4","Corazon","Demo Artist 15"),R("mp-5","Two Step","Demo Artist 16"),R("mp-6","Mixtape 98","Demo Artist 17"),
   R("mp-7","Chicken Shuffle","Barnyard Brass"),R("mp-8","Shine On","Velvet Coast","Clean")],
  "play_if_possible":[R("pip-1","Rooftop Fever","DJ Solano","Extended Mix"),R("pip-2","Mirror Ball","Demo Artist 22"),
   R("pip-3","Roller Rink","Demo Artist 25"),R("pip-4","Bailando Hoy","Demo Artist 28"),R("pip-5","Not In Library","Ghost Band")],
  "do_not_play":[{"id":"dnp-1","title":"Hello"},R("dnp-2","Chicken Shuffle","Barnyard Brass"),R("dnp-3","Cha Cha Line","Floor Captains"),R("dnp-4","Loud Ex","Grudge Machine")],
  "special_moments":[]}}
guests=[R("g-1","Midnight Signal","Kilo Avenue","Radio Edit"),R("g-2","Rooftop Fever","DJ Solano","Clean"),
 R("g-3","Shine On Me","Velvet Coast"),R("g-4","Hello","Ada Moon"),R("g-5","Hello Again","Ada Moon"),R("g-6","Arcade Love","Demo Artist 09"),
 R("g-7","Midnight Signal","Kilo Avenue","Clean"),R("g-8","Cha Cha Line","Floor Captains","Clean"),{"id":"g-9","title":"Night Drive","version":"Re-Drum"},{"id":"g-10","title":"Moon Harbor"}]
ev["_demo_live_inputs"]={"_note":"Live-only; pass as context_from_event(event, guest_requests=...). Parser ignores this key.","guest_requests":guests}
json.dump(ev,open("mock-wedding-event.json","w"),indent=2,ensure_ascii=False)
json.dump({"_note":"Synthetic tracks only; no real files.","tracks":lib},open("mock-library.json","w"),indent=2,ensure_ascii=False)
import random,copy
ng=copy.deepcopy(ev);ng.pop("_demo_live_inputs")
json.dump(ng,open("mock-event-no-guests.json","w"),indent=2,ensure_ascii=False)
sh=list(lib);random.Random(42).shuffle(sh)
json.dump({"_note":"Same tracks as mock-library.json, random.Random(42).shuffle","tracks":sh},open("mock-library-shuffled.json","w"),indent=2,ensure_ascii=False)
print(len(lib))
