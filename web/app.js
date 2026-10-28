const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const SLOT_COLORS = ["#C02626", "#F8821A", "#0F88CA", "#FAC313", "#CE359E", "#1FAD26", "#1DBEBD", "#6823B6"];
const SLOT_COLOR_IDS = ["red", "orange", "sky", "yellow", "magenta", "green", "teal", "purple"];
let tracks = [], selected = new Set(), activeTrack = null, activeJob = null, soundOn = false, refreshTimer;
let eventSource;
let lastCompletedJob = "";
let sessionToken;
let cueMapSettings = null, cueMapCatalog = null, activeCueMap = "VOCAL";
let reviewSaved = "", reviewSaving = false, reviewLeaveResolve = null;
let eventReviewSaving = false;

async function api(url, options = {}) {
  if (sessionToken === undefined) {
    const sessionResponse = await fetch("/api/session");
    const session = await sessionResponse.json();
    if (!sessionResponse.ok) throw new Error(session.error || "Could not establish local session.");
    sessionToken = session.token;
  }
  options.headers = {...options.headers, "X-AutoCue-Token":sessionToken};
  const response = await fetch(url, options);
  const data = response.headers.get("content-type")?.includes("application/json") ? await response.json() : {};
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
function toast(message) {
  const el = $("#toast"); el.textContent = message; el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 3200);
}
function escapeHtml(value = "") {
  return String(value).replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
}
function displayName(track) { return track.name.replace(/\.[^.]+$/, ""); }
function statusClass(status) {
  return status === "Cues ready" ? "ready" : status === "Needs a look" ? "look" :
    status === "Edited by you" ? "edited" : status === "In Serato" ? "serato" : "original";
}
function renderTracks() {
  const query = $("#search").value.toLowerCase();
  const status = $("#status-filter").value;
  const visible = tracks.filter(track => (!status || track.status === status) &&
    (!query || `${track.name} ${track.relative_path}`.toLowerCase().includes(query)));
  $("#library-count").textContent = tracks.length;
  $("#visible-count").textContent = visible.length;
  const rows = $("#track-rows");
  if (!visible.length) {
    rows.innerHTML = `<tr><td colspan="9" class="empty-state">${tracks.length ? "No tracks match this filter." : "Add audio or choose a crate to get started."}</td></tr>`;
    return;
  }
  rows.innerHTML = visible.map(track => `<tr>
    <td><input type="checkbox" class="track-check" data-hash="${track.hash}" ${selected.has(track.hash) ? "checked" : ""}></td>
    <td><span class="track-name" title="${escapeHtml(track.relative_path)}">${escapeHtml(displayName(track))}</span><span class="track-sub">${escapeHtml(track.relative_path || "Local audio copy")}</span></td>
    <td>${track.bpm ?? "—"}</td><td>${escapeHtml(track.map_type || "—")}</td><td>${escapeHtml(track.genre || "—")}</td>
    <td><div class="cue-dots">${"ABCDEFGH".split("").map((slot, i) => `<span class="cue-dot ${track.cues?.[slot]?.bar == null ? "empty" : ""}" data-cue-color="${escapeHtml(track.cues?.[slot]?.color_id || SLOT_COLOR_IDS[i])}" title="${slot}: ${escapeHtml(track.cues?.[slot]?.name || "")}">${slot}</span>`).join("")}</div></td>
    <td>${track.confidence == null ? "—" : Math.round(track.confidence * 100) + "%"}</td>
    <td><span class="tag ${statusClass(track.status)}">${escapeHtml(track.status)}</span>${track.error ? `<span class="track-sub" title="${escapeHtml(track.error)}">Processing unavailable</span>` : ""}</td>
    <td><button class="link-button review-button" data-hash="${track.hash}">Review</button>${track.cues && !track.write_supported ? `<span class="track-sub">Write not supported yet</span>` : ""}</td></tr>`).join("");
  $$(".track-check").forEach(box => box.addEventListener("change", () => {
    box.checked ? selected.add(box.dataset.hash) : selected.delete(box.dataset.hash);
    updateWriteButton();
  }));
  $$(".review-button").forEach(button => button.addEventListener("click", () => openReview(button.dataset.hash)));
  $("#select-all").checked = visible.length > 0 && visible.every(track => selected.has(track.hash));
  updateWriteButton();
}
function updateWriteButton() {
  const candidates = tracks.filter(track => selected.has(track.hash) && track.reviewed && track.cues &&
    track.source_available && track.write_supported);
  $("#write-reviewed").disabled = candidates.length === 0;
  $("#write-reviewed").textContent = candidates.length ? `Write ${candidates.length} reviewed cue${candidates.length === 1 ? "" : "s"} to Serato` : "Write reviewed cues to Serato";
}
async function reload() {
  try {
    const [library, jobs, backupData] = await Promise.all([api("/api/library"), api("/api/jobs"), api("/api/backups")]);
    tracks = library.tracks;
    renderTracks();
    renderBackups(backupData.backups);
    const job = jobs.jobs.find(item => ["queued", "running", "paused"].includes(item.status));
    if (job) { paintJob(job); startPolling(); }
    else if (activeJob) {
      const done = jobs.jobs.find(item => item.id === activeJob);
      if (done) paintJob(done);
      activeJob = null;
    }
  } catch (error) { toast(error.message); }
}
function paintJob(job) {
  activeJob = job.id;
  const total = JSON.parse(job.track_hashes).length;
  const percent = total ? Math.round(job.completed / total * 100) : 0;
  $("#batch-title").textContent = job.status === "completed" ? "Batch complete" :
    job.status === "paused" ? "Batch paused" : job.status === "running" ? "Processing locally" : "Ready when you are";
  $("#batch-count").textContent = `${job.completed} / ${total}`;
  const elapsed = Math.max(0,Math.floor(Date.now()/1000-job.created_at));
  const eta = job.completed ? Math.floor(elapsed/Math.max(1,job.completed)*(total-job.completed)) : null;
  $("#batch-message").textContent = `${job.message} · elapsed ${formatDuration(elapsed)}${eta == null ? "" : ` · ETA ${formatDuration(eta)}`}`;
  drawProgress(percent);
  drawRunner(percent);
  for (const id of ["job-pause", "job-resume", "job-cancel"]) $("#" + id).hidden = !["queued", "running", "paused"].includes(job.status);
  $("#job-pause").hidden = job.status !== "running";
  $("#job-resume").hidden = job.status !== "paused";
  if (job.status === "completed" && lastCompletedJob !== job.id) {
    lastCompletedJob = job.id; toast("Batch processing finished."); if (soundOn) playChime(); showVictory(job);
  }
    if (job.status !== "completed") $("#victory").hidden = true;
  }
  function drawProgress(percent) {
    const canvas=$("#progress-fill"),context=canvas.getContext("2d"),scale=devicePixelRatio||1;
    const width=canvas.clientWidth,height=canvas.clientHeight;
    canvas.width=width*scale;canvas.height=height*scale;context.scale(scale,scale);
    context.fillStyle="#29333a";context.fillRect(0,0,width,height);
    context.fillStyle="#b7f34a";context.fillRect(0,0,width*Math.max(0,Math.min(100,percent))/100,height);
}
const STEP_LABELS={beats:"Beats",sections:"Sections",vocals:"Vocals",lyrics:"Lyrics",drops:"Drops",cues:"Cues"};
let liveWave={hash:null,peaks:[],duration:0,loading:false},liveKey="";
async function paintLive(live){
  const panel=$("#live");
  if(!live||!live.hash||!Object.keys(live.steps||{}).length){panel.hidden=true;return}
  panel.hidden=false;
  $("#live-name").textContent=live.name;
  const steps=live.steps||{};
  $("#live-chips").innerHTML=Object.keys(STEP_LABELS).map(step=>`<span class="chip ${steps[step]||"waiting"}">${STEP_LABELS[step]}${steps[step]==="skipped"?" (skipped)":""}</span>`).join('<i>›</i>');
  const found=(live.cues_found||[]).length;
  $("#live-detail").textContent=live.detail?live.detail:(steps.cues==="done"?`${found} cues found`:"Analyzing locally");
  if(liveWave.hash!==live.hash){liveWave={hash:live.hash,peaks:[],duration:0,loading:false}}
  if(!liveWave.peaks.length&&!liveWave.loading&&steps.beats){
    liveWave.loading=true;
    try{const data=await api(`/api/waveform/${live.hash}`);liveWave.peaks=data.peaks;liveWave.duration=data.duration}catch(_){}
    liveWave.loading=false;
  }
  const key=JSON.stringify([live.hash,live.cues_found,liveWave.peaks.length]);
  if(key!==liveKey){liveKey=key;drawLiveWave(live)}
}
function drawLiveWave(live){
  const canvas=$("#live-wave"),context=canvas.getContext("2d"),scale=devicePixelRatio||1,width=canvas.clientWidth,height=90;
  canvas.width=width*scale;canvas.height=height*scale;context.scale(scale,scale);context.clearRect(0,0,width,height);
  const peaks=liveWave.peaks;if(!peaks.length)return;
  context.fillStyle="#59666b";
  peaks.forEach((peak,i)=>{const x=i*width/peaks.length,h=Math.max(1,peak*height*.9);context.fillRect(x,(height-h)/2,Math.max(1,width/peaks.length-.5),h)});
  const track=tracks.find(item=>item.hash===live.hash);
  if(track&&track.t1!=null&&track.bar_s&&liveWave.duration){
    "ABCDEFGH".split("").forEach((slot,i)=>{const bar=track.cues?.[slot]?.bar;if(bar==null)return;
      const x=AutoCueTiming.cueTime(bar,track.cues[slot],track)/liveWave.duration*width;context.fillStyle=SLOT_COLORS[i];context.fillRect(x,0,2,height);context.fillText(slot,x+3,10)});
  }
}
function formatDuration(seconds){return `${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,"0")}`}
function showVictory(job){
  const hashes=JSON.parse(job.track_hashes), batch=tracks.filter(track=>hashes.includes(track.hash));
  const cues=batch.reduce((sum,track)=>sum+Object.values(track.cues||{}).filter(cue=>cue.bar!=null).length,0);
  const review=batch.filter(track=>track.status==="Needs a look"||track.error).length;
  $("#victory-title").textContent=`${batch.length} songs · ${cues} cues · ${review} to review`;
  $("#victory-summary").textContent="Your original music files are unchanged.";
  $("#victory").hidden=false;
}
function drawRunner(percent = 0) {
  const canvas = $("#runner"), context = canvas.getContext("2d");
  const scale = devicePixelRatio || 1, width = canvas.clientWidth, height = 72;
  canvas.width = width * scale; canvas.height = height * scale; context.scale(scale, scale);
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#29353a"; context.fillRect(6, 54, width - 12, 2);
  const count = Math.max(5, Math.floor(width / 88));
  for (let i = 0; i < count; i++) {
    const x = 17 + i * (width - 36) / (count - 1), passed = i / (count - 1) <= percent / 100;
    context.fillStyle = passed ? "#b7f34a" : "#59666b";
    context.font = "16px monospace"; context.fillText("♫", x - 5, 44);
    context.fillStyle = "#5d6a6e"; context.fillRect(x - 3, 57, 6, 6);
  }
  const x = 12 + (width - 28) * Math.min(percent, 100) / 100;
  context.fillStyle = "#b7f34a";
  context.fillRect(x, 25, 10, 10); context.fillRect(x - 5, 35, 20, 11);
  context.fillRect(x - 9, 45, 9, 6); context.fillRect(x + 11, 45, 9, 6);
}
function startPolling() {
  if ("EventSource" in window) {
    if (!eventSource) {
      eventSource=new EventSource("/api/events");
      eventSource.onmessage=event=>{try{paintLive(JSON.parse(event.data).live)}catch(_){}reload()};
      eventSource.onerror=()=>{if(!refreshTimer)refreshTimer=setInterval(reload,2000)};
      eventSource.onopen=()=>{if(refreshTimer){clearInterval(refreshTimer);refreshTimer=null}};
    }
  } else if(!refreshTimer) refreshTimer=setInterval(reload,1600);
}
async function addFiles(files) {
  const list = [...files].filter(file => file.name.toLowerCase().endsWith(".crate") || file.type.startsWith("audio/") || /\.(wav|mp3|m4a|flac|aiff?|ogg|opus)$/i.test(file.name));
  if (!list.length) return toast("No supported audio files were found.");
  $("#import-message").textContent = `Copying ${list.length} file${list.length === 1 ? "" : "s"} locally…`;
  let added = 0, existing = 0; const newHashes = [];
  for (const file of list) {
    if (file.name.toLowerCase().endsWith(".crate")) {
      try { const data = await uploadCrate(file); $("#import-message").textContent = `Read crate · ${data.songs} song entries.`; await refreshCrates(); }
      catch (error) { toast(error.message); }
      continue;
    }
    const relative = file.webkitRelativePath || file.name;
    const query = new URLSearchParams({name: file.name, relative});
    const result = await api(`/api/import?${query}`, {method:"POST", headers:{"Content-Type":"application/octet-stream"}, body:file});
    if(result.added){added++;newHashes.push(result.hash)}else existing++;
  }
  $("#import-message").textContent = `${added} added · ${existing} already in library`;
  await reload();
  if (added) await queueTracks(newHashes);
}
async function uploadCrate(file) {
  const query = new URLSearchParams({name:file.name});
  return api(`/api/crates/upload?${query}`, {method:"POST",headers:{"Content-Type":"application/octet-stream"},body:file});
}
async function queueTracks(hashes, force = false) {
  if (!hashes.length) return toast("Select at least one track first.");
  const result = await api("/api/jobs", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({hashes,force})});
  if (result.skipped) toast(`${result.skipped} already analyzed and skipped.`);
  if (result.job_id) { activeJob = result.job_id; startPolling(); await reload(); }
}
function renderBackups(backups) {
  const target = $("#backup-list");
  if (!backups.length) { target.innerHTML = '<div class="empty-state">A verified backup is created automatically before each Serato write.</div>'; return; }
  target.innerHTML = backups.map(item => `<div class="backup-row"><div><strong>${escapeHtml(new Date(item.created_at * 1000).toLocaleString())} · ${item.track_hashes.length} TRACKS</strong><small>${escapeHtml(item.status)} · ${escapeHtml(item.backup_path)}</small></div>${item.status === "written" ? `<button class="button secondary undo-button" data-id="${escapeHtml(item.id)}">Undo this batch</button>` : ""}${["written","undone","verified"].includes(item.status) ? `<button class="button secondary restore-button" data-id="${escapeHtml(item.id)}">Restore everything</button>` : ""}</div>`).join("");
  $$(".undo-button").forEach(button => button.onclick = async () => {
    if (!confirm("Restore the exact original MP3 files from this verified backup? Undo stops if any song has changed since the write.")) return;
    try { await api(`/api/backups/${button.dataset.id}/undo`, {method:"POST"}); toast("Original files restored and hash-verified."); await reload(); }
    catch(error) { toast(error.message); }
  });
  bindRestore();
}
function bindRestore() {
  $$(".restore-button").forEach(button => button.onclick = async () => {
    const typed = prompt("This restores every song file and the whole _Serato_ folder from this backup, replacing anything newer. Serato must be closed.\n\nType RESTORE to continue.");
    if (typed !== "RESTORE") { if (typed !== null) toast("Restore cancelled: confirmation did not match."); return; }
    try { await api(`/api/backups/${button.dataset.id}/restore`, {method:"POST", body:JSON.stringify({confirmation:typed})}); toast("Everything restored from backup."); await reload(); }
    catch(error) { toast(error.message); }
  });
}
function renderCrates(items) {
  const target = $("#crate-list");
  if (!items.length) { target.innerHTML = '<div class="empty-state">No Serato crates found in Music or connected drives.</div>'; return; }
  target.innerHTML = items.map((crate, i) => `<div class="crate-item"><div><button class="crate-name" data-crate="${i}">▸ ${escapeHtml(crate.name)}</button><span class="crate-meta">${crate.songs} songs · ${crate.done} done${crate.missing ? ` · ${crate.missing} missing` : ""}</span></div><span class="crate-count">${escapeHtml(crate.drive || "LOCAL")}</span></div>`).join("");
  $$(".crate-name").forEach(button => button.addEventListener("click", async () => {
    const crate = items[Number(button.dataset.crate)];
    try {
      const data = await api("/api/crates/detail?path=" + encodeURIComponent(crate.path));
      openCrate(crate, data.songs);
    } catch (error) { toast(error.message); }
  }));
}
async function refreshCrates() {
  $("#crate-list").innerHTML = '<div class="empty-state">Scanning known Serato crate folders…</div>';
  try { const data = await api("/api/crates"); renderCrates(data.crates); }
  catch (error) { $("#crate-list").innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`; }
}
async function openReview(hash) {
  const track = tracks.find(track => track.hash === hash);
  if (!track || (activeTrack?.hash === hash && $("#review-dialog").open)) return;
  if (!(await confirmReviewLeave())) return;
  $("#preview-audio").pause();
  activeTrack = track;
  $("#review-title").textContent = displayName(activeTrack);
  $("#preview-audio").src = activeTrack.url;
  $("#cue-edit-list").innerHTML = "ABCDEFGH".split("").map((slot, i) => {
    const cue = activeTrack.cues?.[slot] || {};
    return `<div class="cue-edit" data-row="${slot}"><span class="slot cue-${slot.toLowerCase()}">${slot}</span>
      <div class="cue-edit-main"><div class="cue-edit-title" title="${escapeHtml([cue.reason, ...(cue.flags || [])].filter(Boolean).join(" · "))}"><strong>${escapeHtml(cue.name || slot)}</strong><small>${escapeHtml(cue.meaning ? meaningLabel(cue.meaning) : "Cue slot " + slot)}</small></div>
      <input class="cue-name" data-slot="${slot}" value="${escapeHtml(cue.name || slot)}" aria-label="${slot} cue name">${cue.bar == null && cue.reason ? `<small class="cue-explanation">${escapeHtml(cue.reason)}</small>` : ""}</div>
      <div class="cue-timing"><button data-preview="${slot}" title="Play from cue (P)">▶</button><button data-here="${slot}" title="Set cue to the playhead, snapped to the nearest beat (C)">⌖</button><button data-nudge="-0.25" data-slot="${slot}" title="Nudge one beat earlier">−¼</button>
      <button data-nudge="0.25" data-slot="${slot}" title="Nudge one beat later">+¼</button><button data-nudge="-1" data-slot="${slot}" title="Nudge one bar earlier">−B</button>
      <button data-nudge="1" data-slot="${slot}" title="Nudge one bar later">+B</button><input class="cue-bar" data-slot="${slot}" type="number" min="${firstBar()}" step="0.25" value="${cue.bar ?? ""}" placeholder="Empty" aria-label="${slot} bar"></div></div>`;
  }).join("");
  $("#map-select").value = activeTrack.map_override || "AUTO";
  $("#outro-note").textContent = outroEvidenceText(activeTrack);
  $("#map-reason").textContent = `Using ${activeTrack.map_type}: ${activeTrack.map_choice?.reason || "no reason recorded"}` +
    (activeTrack.map_choice?.vocal_ratio != null && !activeTrack.map_choice.reason.startsWith("vocals in") ? ` · vocals in ${Math.round(activeTrack.map_choice.vocal_ratio * 100)}% of bars` : "");
  const gem = activeTrack.gemini, gemNote = $("#gemini-note");
  gemNote.hidden = !gem || (!gem.used && !gem.error);
  if (!gemNote.hidden) {
    gemNote.textContent = gem.error ? `Gemini unavailable: ${gem.error}` :
      `Gemini: ${gem.map || "no map verdict"}${gem.map_why ? " — " + gem.map_why : ""}` +
      (gem.concerns?.length ? ` · ${gem.concerns.map(item => `${item.cue}: ${item.issue}`).join("; ")}` : "") +
      ` · $${(gem.cost_usd || 0).toFixed(3)}${gem.cached ? " (cached)" : ""}`;
  }
  if (!$("#review-dialog").open) $("#review-dialog").showModal();
  reviewSaved = JSON.stringify(reviewSnapshot());
  updateReviewState();
  updateNavButtons();
  renderVariantPanel(activeTrack);
  $$(".cue-timing button[data-nudge]").forEach(button => button.onclick=()=>nudgeCue(button.dataset.slot,Number(button.dataset.nudge)));
  $$(".cue-timing button[data-preview]").forEach(button => button.onclick=()=>previewCue(button.dataset.preview));
  $$(".cue-timing button[data-here]").forEach(button => button.onclick=()=>cueToPlayhead(button.dataset.here));
  renderEventReviewLoading();
  await Promise.all([drawWaveform(activeTrack), loadEventReview(activeTrack.hash)]);
}
function renderEventReviewLoading() {
  $("#event-review-status").textContent = "Loading annotations…";
  $("#event-label-list").innerHTML = '<div class="empty-state">Loading human event labels…</div>';
}
async function loadEventReview(hash) {
  try {
    const data = await api(`/api/tracks/${hash}/event-review`);
    if (activeTrack?.hash === hash) renderEventReview(data);
  } catch (error) {
    if (activeTrack?.hash === hash) {
      $("#event-review-status").textContent = `Could not load event labels: ${error.message}`;
      $("#event-label-list").innerHTML = '<div class="empty-state">Event labels are unavailable.</div>';
      toast(error.message);
    }
  }
}
function eventLabelName(type) {
  return ({drop:"Drop",build:"Build",breakdown:"Breakdown",chorus:"Chorus",other:"Other"})[type] || type;
}
function renderEventReview(data) {
  if (activeTrack?.hash !== data.track_hash) return;
  const review = data.review || {};
  $("#event-review-state").value = review.state || "pending";
  $("#event-review-note").value = review.note || "";
  const annotations = data.annotations || [];
  $("#event-label-list").innerHTML = annotations.length ? annotations.map(annotation => {
    const certainty = annotation.certainty === "uncertain" ? "uncertain" : "certain";
    const time = Number(annotation.time_s);
    const bar = Number.isFinite(annotation.bar) ? ` · bar ${Number(annotation.bar).toFixed(2)}` : "";
    return `<div class="event-label-row">
      <button class="event-label-time" data-event-seek="${time}" title="Seek to this annotation">${time.toFixed(2)}s${bar}</button>
      <span class="event-label-tag ${certainty}">${escapeHtml(eventLabelName(annotation.event_type))} · ${certainty}</span>
      <span class="event-label-provenance" title="${escapeHtml(annotation.note || "")}">Human · ${escapeHtml(annotation.created_at || "")}${annotation.note ? ` · ${escapeHtml(annotation.note)}` : ""}</span>
      <button class="quiet event-label-retract" data-event-retract="${Number(annotation.id)}" aria-label="Retract this annotation">Retract</button>
    </div>`;
  }).join("") : '<div class="empty-state">No event labels yet. Seek or click the waveform, then label the playhead.</div>';
  $$(".event-label-time").forEach(button => button.onclick = () => {
    const time = Number(button.dataset.eventSeek);
    if (!Number.isFinite(time)) return;
    const audio = $("#preview-audio");
    audio.currentTime = time;
    wave.playhead = time;
    ensureVisible(time);
    drawWave();
    updateReadout(time);
  });
  $$(".event-label-retract").forEach(button => button.onclick = () => retractEventAnnotation(Number(button.dataset.eventRetract)));
  const updated = review.updated_at ? ` · saved ${review.updated_at}` : "";
  $("#event-review-status").textContent = `${annotations.length} active label${annotations.length === 1 ? "" : "s"} · ${review.state || "pending"}${updated}`;
}
async function addEventLabel() {
  if (eventReviewSaving || !activeTrack) return;
  const track = activeTrack;
  const audio = $("#preview-audio"), time = audio.currentTime;
  const duration = wave.duration > 0 ? wave.duration : audio.duration;
  if (!Number.isFinite(duration) || duration <= 0 || !Number.isFinite(time) ||
      time < 0 || time > duration + 0.05) {
    toast("The playhead is outside the available audio.");
    return;
  }
  eventReviewSaving = true;
  $("#add-event-label").disabled = true;
  try {
    await api(`/api/event-annotations/${track.hash}`, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({
        event_type:$("#event-label-type").value,
        certainty:$("#event-label-certainty").value,
        time_s:time, note:$("#event-label-note").value,
      }),
    });
    if (activeTrack?.hash === track.hash) $("#event-label-note").value = "";
    await loadEventReview(track.hash);
    toast("Human event label saved.");
  } catch (error) { toast(error.message); }
  finally { eventReviewSaving = false; $("#add-event-label").disabled = false; }
}
async function saveEventReviewState() {
  if (eventReviewSaving || !activeTrack) return;
  const track = activeTrack;
  const state = $("#event-review-state").value;
  if (state === "skipped" && !confirm("Mark this track skipped? Existing event annotations will be retained.")) return;
  eventReviewSaving = true;
  $("#save-event-review").disabled = true;
  try {
    await api(`/api/event-reviews/${track.hash}`, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({state, note:$("#event-review-note").value}),
    });
    await loadEventReview(track.hash);
    toast(`Event review marked ${state.replace("_", " ")}.`);
  } catch (error) { toast(error.message); }
  finally { eventReviewSaving = false; $("#save-event-review").disabled = false; }
}
async function retractEventAnnotation(id) {
  if (eventReviewSaving || !Number.isInteger(id) || id < 1 ||
      !confirm("Retract this label? It will be excluded from the active review but retained in the export history.")) return;
  const hash = activeTrack?.hash;
  if (!hash) return;
  eventReviewSaving = true;
  try {
    await api(`/api/event-annotations/${id}/retract`, {method:"POST"});
    await loadEventReview(hash);
    toast("Annotation retracted; its provenance remains in export history.");
  } catch (error) { toast(error.message); }
  finally { eventReviewSaving = false; }
}
function outroEvidenceText(track) {
  const evidence = track.outro_vocal_evidence;
  if (!evidence || evidence.status !== "analyzed") {
    return `Instrumental outro unverified: ${evidence?.reason || "vocal evidence unavailable"}. Listen before writing cues.`;
  }
  const boundary = Number(evidence.last_vocal_end_s);
  const note = track.no_vocal_free_outro
    ? "No full detected vocal-free outro bar remains. End-of-song cues, if enabled, are not an instrumental outro."
    : `Last detected vocal activity ends at ${boundary.toFixed(2)}s. No later stem activity above the detection threshold.`;
  return note + " Stem leakage/reverb and missed vocals require listening review." +
    (evidence.ignored_words ? ` ${evidence.ignored_words} invalid or stem-unsupported word timings ignored.` : "");
}
function firstBar() {
  if (activeTrack?.t1 == null || !(activeTrack.bar_s > 0)) return 0;
  return Math.min(0, Math.ceil(timeToBar(0) - 1e-6));
}
let waveDuration = 0;
const VARIANT_COLORS = {baseline:"#d9e2e4", gemini:"#46d9a6", fuzzy_lyrics:"#ffcc33", phrase_changes:"#b58cff", kick_grid:"#4aa8ff", stem_energy:"#ff7ac8", roformer_stems:"#ff9a3c"};
function hiddenVariants() { try { return new Set(JSON.parse(localStorage.getItem("hiddenVariants") || "[]")); } catch { return new Set(); } }
function variantList(track) { return Object.entries(track.variants || {}); }
function renderVariantPanel(track) {
  const entries = variantList(track), panel = $("#variant-panel");
  panel.hidden = entries.length === 0;
  if (!entries.length) return;
  const hidden = hiddenVariants();
  $("#variant-chips").innerHTML = entries.map(([id, variant]) => {
    const ok = variant.status === "ok";
    return `<button class="variant-chip ${ok && !hidden.has(id) ? "on" : "off"}" data-variant="${id}" ${ok ? "" : "disabled"} title="${escapeHtml(variant.detail || "")}">${escapeHtml(variant.label)}${ok ? "" : " · unavailable"}</button>`;
  }).join("");
  $$(".variant-chip:not([disabled])").forEach(chip => chip.onclick = () => {
    const set = hiddenVariants(); const id = chip.dataset.variant;
    set.has(id) ? set.delete(id) : set.add(id);
    localStorage.setItem("hiddenVariants", JSON.stringify([...set]));
    renderVariantPanel(track); drawVariantLanes();
  });
  const visible = entries.filter(([id, variant]) => variant.status === "ok" && !hidden.has(id));
  const baseline = track.variants.baseline?.cues || {};
  const unavailable = entries.filter(([, variant]) => variant.status !== "ok");
  $("#variant-table").innerHTML = `<thead><tr><th>Cue</th>${visible.map(([id, variant]) => `<th><i class="variant-dot" data-variant="${id}"></i>${escapeHtml(variant.label)}</th>`).join("")}</tr></thead><tbody>` +
    "ABCDEFGH".split("").map(slot => `<tr><th>${slot}</th>${visible.map(([id, variant]) => {
      const cue = variant.cues?.[slot] || {}, base = baseline[slot] || {};
      const same = (cue.time_s ?? null) === (base.time_s ?? null) || (cue.time_s != null && base.time_s != null && Math.abs(cue.time_s - base.time_s) <= (track.bar_s || 2) / 8);
      const label = cue.time_s == null ? "empty" : formatDuration(Math.round(cue.time_s)) + ` · bar ${cue.bar}`;
      return `<td class="${same || id === "baseline" ? "" : "differs"}"><button class="variant-use" data-slot="${slot}" data-time="${cue.time_s ?? ""}" ${cue.time_s == null ? "disabled" : ""}>${label}</button></td>`;
    }).join("")}</tr>`).join("") + "</tbody>" +
    (unavailable.length ? `<caption>${unavailable.map(([, variant]) => escapeHtml(variant.label + ": " + variant.detail)).join(" · ")}</caption>` : "");
  $$(".variant-use:not([disabled])").forEach(button => button.onclick = () => {
    const bar = timeToBar(Number(button.dataset.time));
    setCueBar(button.dataset.slot, bar);
    toast(`Cue ${button.dataset.slot} set to bar ${formatBar(bar)}. Save review to keep it.`);
  });
}
function drawVariantLanes() {
  const canvas = $("#variant-lanes"); if (!canvas || !activeTrack || !waveDuration) return;
  const hidden = hiddenVariants(), lanes = variantList(activeTrack).filter(([id, variant]) => variant.status === "ok" && !hidden.has(id));
  const width = canvas.clientWidth || 600, laneHeight = 22, height = Math.max(10, lanes.length * laneHeight), scale = devicePixelRatio || 1;
  canvas.height = height * scale; canvas.width = width * scale;
  const ctx = canvas.getContext("2d"); ctx.scale(scale, scale); ctx.fillStyle = "#0a1012"; ctx.fillRect(0, 0, width, height);
  lanes.forEach(([id, variant], row) => {
    const top = row * laneHeight;
    ctx.fillStyle = row % 2 ? "#0d1417" : "#0a1012"; ctx.fillRect(0, top, width, laneHeight);
    ctx.fillStyle = VARIANT_COLORS[id] || "#aaa";
    for (const slot of "ABCDEFGH") {
      const time = variant.cues?.[slot]?.time_s; if (time == null) continue;
      if (time < wave.viewStart || time > wave.viewStart + wave.viewSpan) continue;
      const x = Math.max(0, Math.min(width - 2, (time - wave.viewStart) / wave.viewSpan * width));
      ctx.fillRect(x - 1, top + 3, 3, laneHeight - 6);
      ctx.font = "10px monospace"; ctx.fillText(slot, x + 4, top + 15);
    }
  });
}
const wave = {hash: null, overview: null, detail: null, duration: 0, error: "",
  viewStart: 0, viewSpan: 1, selected: null, playhead: 0, follow: true, audition: true,
  undo: [], redo: [], committed: {}, drag: null, raf: 0, stopAt: null, ready: false, mini: null};
const WAVE_H = 240, RULER_H = 18, LABEL_ROW = 13, LABEL_ROWS = 4, MINI_H = 36, HIT_PX = 7;
const waveTop = () => RULER_H + LABEL_ROW * LABEL_ROWS + 4;

function cueBarValue(slot) {
  const value = $(`.cue-bar[data-slot="${slot}"]`)?.value;
  return value === "" || value == null || !Number.isFinite(Number(value)) ? null : Number(value);
}
function barToTime(bar) { return AutoCueTiming.barToTime(bar, activeTrack); }
function cueTime(slot) { return AutoCueTiming.cueTime(cueBarValue(slot), activeTrack.cues?.[slot], activeTrack); }
function timeToBar(time) { return AutoCueTiming.timeToBar(time, activeTrack); }
function formatBar(bar) { return String(Number(bar.toFixed(2))); }
function waveSnapshot() { return Object.fromEntries("ABCDEFGH".split("").map(slot => [slot, $(`.cue-bar[data-slot="${slot}"]`)?.value ?? ""])); }
function waveCommit() {
  const now = waveSnapshot();
  if (JSON.stringify(now) === JSON.stringify(wave.committed)) return;
  wave.undo.push(wave.committed); wave.redo = []; wave.committed = now; updateUndoButtons();
}
function waveApply(snapshot) {
  for (const [slot, value] of Object.entries(snapshot)) { const input = $(`.cue-bar[data-slot="${slot}"]`); if (input) input.value = value; }
  wave.committed = waveSnapshot(); updateUndoButtons(); updateReviewState(); drawWave();
}
function waveUndo() { if (!wave.undo.length) return; wave.redo.push(waveSnapshot()); waveApply(wave.undo.pop()); }
function waveRedo() { if (!wave.redo.length) return; wave.undo.push(waveSnapshot()); waveApply(wave.redo.pop()); }
function updateUndoButtons() {
  const undo = $("#wave-undo"), redo = $("#wave-redo");
  if (undo) undo.disabled = !wave.undo.length;
  if (redo) redo.disabled = !wave.redo.length;
}
function setCueBar(slot, bar, {silent = false} = {}) {
  if (reviewSaving) return;
  const input = $(`.cue-bar[data-slot="${slot}"]`); if (!input) return;
  input.value = bar == null ? "" : String(Number(Math.max(firstBar(), bar).toFixed(9)));
  input.dispatchEvent(new Event("input", {bubbles: true}));
  if (!silent) input.dispatchEvent(new Event("change", {bubbles: true}));
}
function nudgeCue(slot, bars) {
  const bar = cueBarValue(slot);
  if (bar == null) return toast("Set a cue bar before nudging.");
  selectCue(slot);
  setCueBar(slot, bar + bars);
  if (wave.audition) auditionCue(slot);
}
function cueToPlayhead(slot) {
  const audio = $("#preview-audio");
  if (!activeTrack?.bar_s) return toast("This track has no timing grid yet.");
  selectCue(slot);
  setCueBar(slot, Math.round(timeToBar(audio.currentTime) * 4) / 4);
}
function startPreviewPlayback() {
  const audio = $("#preview-audio"), source = audio.currentSrc || audio.src;
  audio.play().catch(error => {
    if (error.name === "AbortError" && (audio.paused || (audio.currentSrc || audio.src) !== source)) return;
    toast("Audio playback unavailable: " + error.message);
  });
}
function playFromCue(slot, length) {
  const bar = cueBarValue(slot);
  if (bar == null || activeTrack.t1 == null || activeTrack.bar_s == null) return toast("This track has no timing grid yet.");
  const audio = $("#preview-audio"), time = Math.max(0, cueTime(slot));
  audio.currentTime = time; wave.stopAt = length ? time + length : null; startPreviewPlayback();
  wave.playhead = time; ensureVisible(time); drawWave();
}
function previewCue(slot) { selectCue(slot); playFromCue(slot, null); }
function auditionCue(slot) { playFromCue(slot, Math.min(6, activeTrack.bar_s * 2)); }

function selectCue(slot) {
  wave.selected = slot;
  $$(".cue-edit").forEach(row => row.classList.toggle("selected", row.dataset.row === slot));
  $$("#wave-legend [data-slot]").forEach(item => item.classList.toggle("selected", item.dataset.slot === slot));
  drawWave();
}
function clampView(start, span) {
  const minSpan = Math.min(wave.duration, Math.max(1.5, (activeTrack.bar_s || 2) / 2));
  span = Math.max(minSpan, Math.min(wave.duration, span));
  wave.viewSpan = span; wave.viewStart = Math.max(0, Math.min(wave.duration - span, start));
}
function zoomAt(factor, time) {
  const span = wave.viewSpan * factor, ratio = (time - wave.viewStart) / wave.viewSpan;
  clampView(time - ratio * span, span); drawWave();
}
function ensureVisible(time) {
  if (time < wave.viewStart || time > wave.viewStart + wave.viewSpan) clampView(time - wave.viewSpan * 0.1, wave.viewSpan);
}
function centerOn(time) { clampView(time - wave.viewSpan / 2, wave.viewSpan); drawWave(); }
function zoomToCue(slot) {
  const bar = cueBarValue(slot);
  if (bar == null) return toast(`Cue ${slot} has no bar yet.`);
  clampView(cueTime(slot) - activeTrack.bar_s * 8, activeTrack.bar_s * 16); drawWave();
}
function fitView() { clampView(0, wave.duration); drawWave(); }

async function loadWaveData(track, signal) {
  const overview = wave.hash === track.hash && wave.overview
    ? wave.overview : await api(`/api/waveform/${track.hash}?detail=1`, {signal});
  return {hash: track.hash, duration: overview.duration, overview, detail: null};
}
function refineWave(width) {
  if (wave.viewSpan >= wave.duration - 0.01) {
    clearTimeout(wave.detailTimer); wave.detailController?.abort();
    wave.detailKey = null; wave.detail = null; return;
  }
  const points = Math.max(64, Math.min(4096, Math.ceil(width * 2)));
  const key = `${wave.hash}:${wave.viewStart}:${wave.viewSpan}:${points}`;
  if (wave.detailKey === key) return;
  wave.detailKey = key;
  clearTimeout(wave.detailTimer); wave.detailController?.abort();
  const hash = wave.hash, loadId = wave.loadId, start = wave.viewStart, end = start + wave.viewSpan;
  wave.detailTimer = setTimeout(async () => {
    const controller = new AbortController(); wave.detailController = controller;
    const current = () => wave.loadId === loadId && wave.hash === hash && wave.detailKey === key && $("#review-dialog").open;
    try {
      const data = await api(`/api/waveform/${hash}?detail=1&start=${start}&end=${end}&points=${points}`, {signal: controller.signal});
      if (!current()) return;
      wave.detail = data; drawWave();
    } catch (error) {
      if (current() && error.name !== "AbortError") toast("Waveform zoom detail unavailable: " + error.message);
    }
  }, 60);
}
function waveBands(t0, t1, overviewOnly = false) {
  const detail = wave.detail;
  const data = !overviewOnly && detail && t0 >= detail.start && t1 <= detail.start + detail.columns.length * detail.hop_s
    ? detail : wave.overview;
  const count = data.columns.length, first = Math.max(0, Math.min(count - 1, Math.floor((t0 - data.start) / data.hop_s)));
  const last = Math.max(first + 1, Math.min(count, Math.ceil((t1 - data.start) / data.hop_s)));
  const out = [0, 0, 0, 0, 0];
  for (let index = first; index < last; index++) {
    const column = data.columns[index];
    out[0] = Math.min(out[0], column[0]); out[1] = Math.max(out[1], column[1]);
    for (let band = 2; band < 5; band++) out[band] += column[band] / (last - first);
  }
  return out;
}
function paintWaveColumns(ctx, columns, mid, half) {
  ctx.strokeStyle = "rgba(120,150,160,.28)"; ctx.lineWidth = 1; ctx.beginPath();
  columns.forEach(([lo, hi], x) => { ctx.moveTo(x + 0.5, mid - Math.max(hi, 0.004) * half); ctx.lineTo(x + 0.5, mid - Math.min(lo, -0.004) * half); });
  ctx.stroke();
  [[4, "rgba(235,248,255,.9)", 0.5], [3, "rgba(236,150,48,.9)", 0.8], [2, "rgba(54,112,232,.95)", 1]].forEach(([index, color, gain]) => {
    ctx.strokeStyle = color; ctx.beginPath();
    columns.forEach((column, x) => { const h = Math.max(0.01, column[index] * gain) * half; ctx.moveTo(x + 0.5, mid - h); ctx.lineTo(x + 0.5, mid + h); });
    ctx.stroke();
  });
}
function sizeCanvas(canvas, height) {
  const width = canvas.clientWidth, scale = devicePixelRatio || 1;
  if (canvas.width !== Math.round(width * scale) || canvas.height !== Math.round(height * scale)) {
    canvas.width = Math.round(width * scale); canvas.height = Math.round(height * scale);
  }
  const ctx = canvas.getContext("2d"); ctx.setTransform(scale, 0, 0, scale, 0, 0);
  return [ctx, width];
}
function cueColor(slot) {
  const index = "ABCDEFGH".indexOf(slot);
  return activeTrack.cues?.[slot]?.color_id ? (SLOT_COLORS[SLOT_COLOR_IDS.indexOf(activeTrack.cues[slot].color_id)] || activeTrack.cues[slot].display_color || SLOT_COLORS[index]) : SLOT_COLORS[index];
}
function cueDisplayColor(slot) { return activeTrack.cues?.[slot]?.display_color || cueColor(slot); }

function drawWave() {
  if (!activeTrack || !$("#review-dialog").open) return;
  const canvas = $("#waveform"), [ctx, width] = sizeCanvas(canvas, WAVE_H);
  ctx.fillStyle = "#0a1012"; ctx.fillRect(0, 0, width, WAVE_H);
  if (!wave.ready) {
    const coarse = wave.coarse?.hash === activeTrack.hash ? wave.coarse.peaks : null;
    if (coarse?.length) {
      const mid = WAVE_H / 2, half = WAVE_H / 2 - 4;
      ctx.fillStyle = "#3f6f73";
      for (let x = 0; x < width; x++) {
        const value = coarse[Math.min(coarse.length - 1, Math.floor(x / width * coarse.length))];
        ctx.fillRect(x, mid - value * half, 1, Math.max(1, value * half * 2));
      }
    }
    ctx.fillStyle = "#829198"; ctx.font = "12px monospace";
    ctx.fillText(wave.error || "Loading cached waveform…", 14, 20); return;
  }
  refineWave(width);
  const {viewStart, viewSpan} = wave, top = waveTop(), mid = top + (WAVE_H - top) / 2, half = (WAVE_H - top) / 2 - 2;
  const toX = time => (time - viewStart) / viewSpan * width;
  ctx.fillStyle = "#0d1519"; ctx.fillRect(0, 0, width, top - 2);

  const barS = activeTrack.bar_s, t1 = activeTrack.t1, hasGrid = barS > 0 && t1 != null;
  if (hasGrid) {
    const pxBar = barS / viewSpan * width, first = Math.floor(timeToBar(viewStart)) - 1, last = Math.ceil(timeToBar(viewStart + viewSpan)) + 1;
    const labelEvery = [1, 2, 4, 8, 16, 32, 64].find(step => pxBar * step >= 46) || 64;
    ctx.font = "10px monospace"; ctx.textBaseline = "alphabetic";
    for (let bar = first; bar <= last; bar++) {
      const x = Math.round(toX(barToTime(bar))) + 0.5, phrase = bar % 8 === 0, four = bar % 4 === 0;
      if (pxBar >= 4 || four) {
        ctx.strokeStyle = phrase ? "rgba(160,190,200,.34)" : four ? "rgba(160,190,200,.22)" : "rgba(160,190,200,.12)";
        if (pxBar >= 4 || phrase || four) { ctx.beginPath(); ctx.moveTo(x, RULER_H - 6); ctx.lineTo(x, WAVE_H); ctx.stroke(); }
      }
      if (pxBar / 4 >= 11) {
        ctx.strokeStyle = "rgba(160,190,200,.07)";
        for (let beat = 1; beat < 4; beat++) { const bx = Math.round(toX(barToTime(bar + beat / 4))) + 0.5; ctx.beginPath(); ctx.moveTo(bx, top); ctx.lineTo(bx, WAVE_H); ctx.stroke(); }
      }
      if (bar % labelEvery === 0 && x > 0 && x < width - 14) { ctx.fillStyle = "#8aa0a7"; ctx.fillText(String(bar), x + 3, 11); }
    }
  }

  // Bars in three layers: faint peak outline, then low (blue), mid (amber) and high (white) energy.
  const columns = [];
  for (let x = 0; x < width; x++) columns.push(waveBands(viewStart + x / width * viewSpan, viewStart + (x + 1) / width * viewSpan));
  paintWaveColumns(ctx, columns, mid, half);

  for (const [index, slot] of "ABCDEFGH".split("").entries()) {
    const bar = cueBarValue(slot); if (bar == null || !hasGrid) continue;
    const x = toX(cueTime(slot)); if (x < -4 || x > width + 4) continue;
    const color = cueDisplayColor(slot), selectedCue = wave.selected === slot;
    ctx.strokeStyle = color; ctx.lineWidth = selectedCue ? 2.5 : 1.5;
    ctx.beginPath(); ctx.moveTo(x, RULER_H); ctx.lineTo(x, WAVE_H); ctx.stroke();
    const name = $(`.cue-name[data-slot="${slot}"]`)?.value || activeTrack.cues?.[slot]?.name || slot;
    const text = `${slot} ${name}`; ctx.font = "10px monospace";
    const boxW = Math.min(150, ctx.measureText(text).width + 8), row = index % LABEL_ROWS, y = RULER_H + row * LABEL_ROW;
    const boxX = x + boxW + 2 > width ? x - boxW : x;
    ctx.fillStyle = color; ctx.fillRect(boxX, y, boxW, LABEL_ROW - 1);
    if (selectedCue) { ctx.strokeStyle = "#fff"; ctx.lineWidth = 1; ctx.strokeRect(boxX + 0.5, y + 0.5, boxW - 1, LABEL_ROW - 2); }
    ctx.fillStyle = "#06090a"; ctx.save(); ctx.beginPath(); ctx.rect(boxX, y, boxW, LABEL_ROW); ctx.clip();
    ctx.fillText(text, boxX + 4, y + 10); ctx.restore();
  }

  if (wave.playhead >= viewStart && wave.playhead <= viewStart + viewSpan) {
    const x = Math.round(toX(wave.playhead)) + 0.5;
    ctx.strokeStyle = "#fff"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, WAVE_H); ctx.stroke();
    ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.moveTo(x - 4, 0); ctx.lineTo(x + 4, 0); ctx.lineTo(x, 6); ctx.fill();
  }
  drawMiniMap(); drawVariantLanes();
}
function drawMiniMap() {
  const canvas = $("#wave-mini"); if (!canvas || !wave.ready) return;
  const [ctx, width] = sizeCanvas(canvas, MINI_H);
  ctx.fillStyle = "#0a1012"; ctx.fillRect(0, 0, width, MINI_H);
  if (!wave.mini || wave.mini.width !== Math.floor(width) || wave.mini.hash !== wave.hash) {
    const columns = Math.floor(width), data = [];
    for (let x = 0; x < columns; x++) data.push(waveBands(x / columns * wave.duration, (x + 1) / columns * wave.duration, true));
    wave.mini = {width: columns, hash: wave.hash, data};
  }
  paintWaveColumns(ctx, wave.mini.data, MINI_H / 2, MINI_H / 2 - 2);
  for (const slot of "ABCDEFGH") {
    const bar = cueBarValue(slot); if (bar == null || !activeTrack.bar_s) continue;
    ctx.fillStyle = cueDisplayColor(slot); ctx.fillRect(Math.round(cueTime(slot) / wave.duration * width) - 1, 0, 2, MINI_H);
  }
  const x0 = wave.viewStart / wave.duration * width, w = Math.max(3, wave.viewSpan / wave.duration * width);
  ctx.fillStyle = "rgba(255,255,255,.12)"; ctx.fillRect(x0, 0, w, MINI_H);
  ctx.strokeStyle = "rgba(255,255,255,.6)"; ctx.strokeRect(x0 + 0.5, 0.5, w - 1, MINI_H - 1);
  ctx.fillStyle = "#fff"; ctx.fillRect(Math.round(wave.playhead / wave.duration * width), 0, 1, MINI_H);
}
function updateReadout(time) {
  const el = $("#wave-readout"); if (!el || !wave.ready) return;
  const bar = activeTrack.bar_s ? ` · bar ${formatBar(timeToBar(time))}` : "";
  const minutes = Math.floor(time / 60), seconds = (time % 60).toFixed(2).padStart(5, "0");
  el.textContent = `${minutes}:${seconds}${bar} · ${wave.viewSpan >= wave.duration - 0.01 ? "full track" : (wave.viewSpan / (activeTrack.bar_s || 2)).toFixed(1) + " bars in view"}`;
}
function markerAt(x) {
  if (!activeTrack.bar_s) return null;
  let best = null, bestDistance = HIT_PX + 1;
  for (const slot of "ABCDEFGH") {
    const bar = cueBarValue(slot); if (bar == null) continue;
    const distance = Math.abs((cueTime(slot) - wave.viewStart) / wave.viewSpan * $("#waveform").clientWidth - x);
    if (distance < bestDistance || (distance === bestDistance && slot === wave.selected)) { best = slot; bestDistance = distance; }
  }
  return best;
}
function snapBar(bar, event) {
  if (event.altKey) return Math.round(bar * 20) / 20;
  return event.shiftKey ? Math.round(bar * 4) / 4 : Math.round(bar);
}
function waveFrame() {
  const audio = $("#preview-audio");
  wave.playhead = audio.currentTime;
  if (wave.stopAt != null && audio.currentTime >= wave.stopAt) { wave.stopAt = null; audio.pause(); }
  if (wave.follow && !audio.paused && wave.viewSpan < wave.duration) {
    if (wave.playhead > wave.viewStart + wave.viewSpan * 0.92 || wave.playhead < wave.viewStart) clampView(wave.playhead - wave.viewSpan * 0.08, wave.viewSpan);
  }
  drawWave(); updateReadout(wave.playhead);
  wave.raf = audio.paused ? 0 : requestAnimationFrame(waveFrame);
}
function bindWaveEditor() {
  if (wave.bound) return; wave.bound = true;
  const canvas = $("#waveform"), mini = $("#wave-mini"), audio = $("#preview-audio"), dialog = $("#review-dialog");
  const localX = (event, el = canvas) => event.clientX - el.getBoundingClientRect().left;
  canvas.addEventListener("wheel", event => {
    if (!wave.ready) return; event.preventDefault();
    const width = canvas.clientWidth, time = wave.viewStart + localX(event) / width * wave.viewSpan;
    if (event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
      clampView(wave.viewStart + (event.deltaX || event.deltaY) / width * wave.viewSpan, wave.viewSpan); drawWave();
    } else zoomAt(Math.exp(event.deltaY * (event.ctrlKey ? 0.01 : 0.002)), time);
  }, {passive: false});
  canvas.addEventListener("pointerdown", event => {
    if (!wave.ready || event.button !== 0) return;
    canvas.setPointerCapture(event.pointerId);
    const x = localX(event), slot = markerAt(x);
    if (slot) { selectCue(slot); wave.drag = {slot, startX: x, moved: false}; }
    else wave.drag = {pan: true, startX: x, startView: wave.viewStart, moved: false};
  });
  canvas.addEventListener("pointermove", event => {
    if (!wave.ready) return;
    const x = localX(event), width = canvas.clientWidth, time = wave.viewStart + x / width * wave.viewSpan, drag = wave.drag;
    if (!drag) { canvas.style.cursor = markerAt(x) ? "ew-resize" : "crosshair"; updateReadout(time); return; }
    if (drag.slot) {
      if (!drag.moved && Math.abs(x - drag.startX) <= 2) return;
      drag.moved = true;
      setCueBar(drag.slot, snapBar(timeToBar(time), event), {silent: true}); updateReadout(time);
    } else {
      if (Math.abs(x - drag.startX) > 3) drag.panning = true;
      if (drag.panning) { clampView(drag.startView - (x - drag.startX) / width * wave.viewSpan, wave.viewSpan); drawWave(); canvas.style.cursor = "grabbing"; }
    }
  });
  canvas.addEventListener("pointerup", event => {
    const drag = wave.drag; wave.drag = null; canvas.style.cursor = "crosshair"; if (!drag) return;
    if (drag.slot) {
      if (drag.moved) { $(`.cue-bar[data-slot="${drag.slot}"]`).dispatchEvent(new Event("change", {bubbles: true})); if (wave.audition) auditionCue(drag.slot); }
    } else if (!drag.panning) {
      audio.currentTime = Math.max(0, wave.viewStart + localX(event) / canvas.clientWidth * wave.viewSpan);
      wave.playhead = audio.currentTime; drawWave(); updateReadout(wave.playhead);
    }
  });
  canvas.addEventListener("dblclick", event => { if (!markerAt(localX(event))) fitView(); });

  const miniMove = event => { if (wave.ready) centerOn(localX(event, mini) / mini.clientWidth * wave.duration); };
  mini.addEventListener("pointerdown", event => { mini.setPointerCapture(event.pointerId); wave.miniDrag = true; miniMove(event); });
  mini.addEventListener("pointermove", event => { if (wave.miniDrag) miniMove(event); });
  mini.addEventListener("pointerup", () => { wave.miniDrag = false; });

  audio.addEventListener("play", () => { if (!wave.raf) wave.raf = requestAnimationFrame(waveFrame); });
  audio.addEventListener("pause", () => { wave.stopAt = null; wave.playhead = audio.currentTime; drawWave(); });
  audio.addEventListener("seeked", () => { wave.playhead = audio.currentTime; drawWave(); });
  dialog.addEventListener("close", () => {
    audio.pause(); wave.stopAt = null; invalidateWaveLoad(); updateReviewState();
  });
  window.addEventListener("resize", () => drawWave());

  $("#cue-edit-list").addEventListener("input", event => {
    if (event.target.matches(".cue-bar, .cue-name")) { updateReviewState(); drawWave(); }
  });
  $("#cue-edit-list").addEventListener("change", event => { if (event.target.matches(".cue-bar")) waveCommit(); });
  $("#cue-edit-list").addEventListener("focusin", event => {
    const row = event.target.closest(".cue-edit"); if (row && row.dataset.row !== wave.selected) selectCue(row.dataset.row);
  });
  $("#wave-legend").addEventListener("click", event => {
    const item = event.target.closest("[data-slot]"); if (!item) return;
    selectCue(item.dataset.slot); if (cueBarValue(item.dataset.slot) != null) centerOn(cueTime(item.dataset.slot)); else toast(`Cue ${item.dataset.slot} has no bar yet.`);
  });
  $("#wave-zoom-in").onclick = () => zoomAt(0.5, wave.playhead >= wave.viewStart && wave.playhead <= wave.viewStart + wave.viewSpan ? wave.playhead : wave.viewStart + wave.viewSpan / 2);
  $("#wave-zoom-out").onclick = () => zoomAt(2, wave.viewStart + wave.viewSpan / 2);
  $("#wave-fit").onclick = fitView;
  $("#wave-zoom-bars").onclick = () => { clampView(wave.playhead - activeTrack.bar_s * 4, activeTrack.bar_s * 16); drawWave(); };
  $("#wave-zoom-cue").onclick = () => wave.selected ? zoomToCue(wave.selected) : toast("Select a cue first (click a marker or press 1–8).");
  $("#wave-undo").onclick = waveUndo; $("#wave-redo").onclick = waveRedo;
  $("#wave-follow").onchange = event => { wave.follow = event.target.checked; };
  $("#wave-audition").onchange = event => { wave.audition = event.target.checked; };

  dialog.addEventListener("keydown", event => {
    if (!wave.ready || reviewSaving || reviewLeaveResolve || event.altKey) return;
    const tag = event.target.tagName, typing = tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA";
    const mod = event.metaKey || event.ctrlKey, key = event.key;
    if (mod && key.toLowerCase() === "z" && !(typing && event.target.matches(".cue-name"))) { event.preventDefault(); event.shiftKey ? waveRedo() : waveUndo(); return; }
    if (mod && key.toLowerCase() === "y") { event.preventDefault(); waveRedo(); return; }
    if (mod || typing || tag === "AUDIO") return;
    const beat = activeTrack.bar_s / 4, step = event.shiftKey ? activeTrack.bar_s : beat;
    if (key === " " && tag !== "BUTTON") { event.preventDefault(); audio.paused ? startPreviewPlayback() : audio.pause(); }
    else if (key === "+" || key === "=") zoomAt(0.5, wave.playhead >= wave.viewStart && wave.playhead <= wave.viewStart + wave.viewSpan ? wave.playhead : wave.viewStart + wave.viewSpan / 2);
    else if (key === "-" || key === "_") zoomAt(2, wave.viewStart + wave.viewSpan / 2);
    else if (key === "0") fitView();
    else if (key === "ArrowLeft" || key === "ArrowRight") { event.preventDefault(); audio.currentTime = Math.max(0, Math.min(wave.duration, audio.currentTime + (key === "ArrowLeft" ? -step : step))); wave.playhead = audio.currentTime; ensureVisible(wave.playhead); drawWave(); }
    else if ((key === "[" || key === "]") && wave.selected) { nudgeCue(wave.selected, (key === "[" ? -1 : 1) * (event.shiftKey ? 1 : 0.25)); }
    else if (key === "{" && wave.selected) nudgeCue(wave.selected, -1);
    else if (key === "}" && wave.selected) nudgeCue(wave.selected, 1);
    else if (/^[1-8]$/.test(key)) {
      const slot = "ABCDEFGH"[Number(key) - 1]; selectCue(slot);
      const bar = cueBarValue(slot); bar == null ? toast(`Cue ${slot} has no bar yet. Press C to set it at the playhead.`) : centerOn(cueTime(slot));
    }
    else if (key.toLowerCase() === "p" && wave.selected) previewCue(wave.selected);
    else if (key.toLowerCase() === "c" && wave.selected) cueToPlayhead(wave.selected);
    else if (key.toLowerCase() === "f") { wave.follow = !wave.follow; $("#wave-follow").checked = wave.follow; }
  });
}
function invalidateWaveLoad() {
  wave.loadId = (wave.loadId || 0) + 1;
  wave.controller?.abort();
  wave.detailController?.abort(); clearTimeout(wave.detailTimer); wave.detailKey = null;
}
async function drawWaveform(track) {
  bindWaveEditor();
  invalidateWaveLoad();
  const loadId = wave.loadId, controller = new AbortController();
  wave.controller = controller;
  const current = () => wave.loadId === loadId && activeTrack?.hash === track.hash && $("#review-dialog").open;
  const fresh = wave.hash !== track.hash || !wave.ready;
  if (wave.hash !== track.hash) {
    wave.overview = null; wave.detail = null; wave.coarse = null; wave.duration = 0; waveDuration = 0;
    wave.viewStart = 0; wave.viewSpan = 1; wave.mini = null;
  }
  wave.ready = false; wave.error = ""; wave.selected = null; wave.stopAt = null;
  wave.drag = null; wave.miniDrag = false;
  $("#wave-legend").textContent = ""; $("#wave-readout").textContent = "";
  wave.undo = []; wave.redo = []; wave.committed = waveSnapshot(); updateUndoButtons(); drawWave();
  if (wave.coarse?.hash !== track.hash) {
    api(`/api/waveform/${track.hash}`, {signal: controller.signal}).then(data => {
      if (!current()) return;
      wave.coarse = {hash: track.hash, peaks: data.peaks || []};
      if (!wave.ready) drawWave();
    }).catch(error => {
      if (current() && error.name !== "AbortError") console.warn("Cached waveform unavailable", error);
    });
  }
  let data;
  try { data = await loadWaveData(track, controller.signal); }
  catch (error) {
    if (!current() || error.name === "AbortError") return;
    wave.error = "Waveform preview unavailable: " + error.message; wave.ready = false; drawWave(); return;
  }
  if (!current()) return;
  Object.assign(wave, data, {mini: null});
  wave.ready = true; waveDuration = wave.duration;
  if (fresh) clampView(0, wave.duration);
  wave.playhead = $("#preview-audio").currentTime || 0;
  $("#wave-legend").innerHTML = "ABCDEFGH".split("").map((s, i) => `<span data-slot="${s}" data-cue-color="${escapeHtml(track.cues?.[s]?.color_id || SLOT_COLOR_IDS[i])}" title="Select and center on cue ${s}">${s} · ${escapeHtml(track.cues?.[s]?.name || "")}</span>`).join("");
  drawWave(); updateReadout(wave.playhead);
}
function neighborTrack(step) {
  const index = tracks.findIndex(track => track.hash === activeTrack?.hash);
  return index < 0 ? null : tracks[index + step] || null;
}
function updateNavButtons() {
  $("#prev-track").disabled = !neighborTrack(-1); $("#next-track").disabled = !neighborTrack(1);
  $("#save-next").disabled = !neighborTrack(1);
}
function reviewSnapshot() {
  const cues = {};
  $$(".cue-edit").forEach(row => {
    const slot = $(".cue-name", row).dataset.slot;
    const value = $(".cue-bar", row).value;
    cues[slot] = {name: $(".cue-name", row).value, bar: value === "" ? null : Number(value)};
  });
  return cues;
}
function reviewHasChanges() {
  return $("#review-dialog").open && JSON.stringify(reviewSnapshot()) !== reviewSaved;
}
function updateReviewState() {
  $("#review-state").textContent = reviewSaving ? "Saving review..." :
    reviewHasChanges() ? "Unsaved changes" : "Edits stay local until you save.";
}
function setReviewBusy(busy) {
  reviewSaving = busy;
  $("#review-dialog").inert = busy;
  $("#review-dialog").setAttribute("aria-busy", String(busy));
  updateReviewState();
}
function resolveReviewLeave(choice) {
  const resolve = reviewLeaveResolve;
  reviewLeaveResolve = null;
  $("#unsaved-review-dialog").close();
  resolve?.(choice);
}
async function confirmReviewLeave() {
  if (reviewSaving) { toast("Wait for this review operation to finish."); return false; }
  if (reviewLeaveResolve) return false;
  if (!reviewHasChanges()) return true;
  const choice = await new Promise(resolve => {
    reviewLeaveResolve = resolve;
    $("#unsaved-review-dialog").showModal();
  });
  if (choice === "save") return persistReview();
  return choice === "discard";
}
async function closeReview() {
  if (await confirmReviewLeave()) $("#review-dialog").close();
}
async function persistReview() {
  if (reviewSaving || !activeTrack) return false;
  const track = activeTrack, cues = reviewSnapshot();
  setReviewBusy(true);
  try {
    await api(`/api/tracks/${track.hash}`, {method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({cues,map_type:track.map_type})});
    reviewSaved = JSON.stringify(cues);
    for (const [slot, cue] of Object.entries(cues)) track.cues[slot] = {...track.cues[slot], ...cue};
    track.edited = true; track.reviewed = true;
    selected.add(track.hash); toast("Review saved. Cue edits are local."); await reload();
    return true;
  } catch (error) { toast(error.message); return false; }
  finally { setReviewBusy(false); }
}
async function saveReview(advance = false) {
  const next = advance === true ? neighborTrack(1) : null;
  if (reviewLeaveResolve || !(await persistReview())) return;
  if (next) await openReview(next.hash);
  else $("#review-dialog").close();
}
async function openSettings() {
  const data=await api("/api/settings");$("#gemini-toggle").checked=data.gemini==="true";$("#strictness").value=data.strictness||"standard";
  const status=data.gemini_status||{}, lines=[
    status.configured?`Credential configured (${status.key_source}); access not yet verified.`:"Credential missing. Configure GEMINI_API_KEY locally; never paste it into chat.",
    `Model: ${status.model||"unavailable"}${status.model_source?` (${status.model_source})`:""}.`,
    status.budget_note||"Local spend limit; not a provider billing guarantee.",
  ];
  if(status.spent_usd!=null)lines.push(`Recorded lifetime spend: $${status.spent_usd.toFixed(4)}.`);
  if(status.pending_reconciliation)lines.push("Requests blocked: a previous request needs provider usage reconciliation.");
  if(status.blocked)lines.push(`Requests blocked: ${status.blocked}`);
  if(status.error||status.last_error)lines.push(status.error||status.last_error);
  $("#gemini-status").textContent=lines.join(" ");
  $("#settings-dialog").showModal();
}
function meaningLabel(id) {
  return cueMapCatalog?.meanings?.find(item=>item.id===id)?.label || id.replaceAll("_"," ");
}
function renderCueMapEditor() {
  if (!cueMapSettings || !cueMapCatalog) return;
  const map=cueMapSettings.maps[activeCueMap];
  const description=activeCueMap==="VOCAL"
    ? "For vocal and chorus-led tracks. The last-chorus pad lands on the chorus start by default."
    : "For electronic tracks. Drop cues are placed only when the audio supports a confident drop.";
  $("#cue-map-summary").textContent=description;
  $("#cue-map-mode").value=cueMapSettings.mode;
  for (const key of ["intro","go","exit"]) $(`#mix-${key}`).value=cueMapSettings.mix_lengths[key].join(", ");
  $("#mix-end-fallback").checked=cueMapSettings.end_of_song_fallback;
  $("#cue-map-rows").innerHTML="ABCDEFGH".split("").map(slot=>{
    const spec=map[slot];
    const selectedColor=cueMapCatalog.palette.find(color=>color.id===spec.color)||cueMapCatalog.palette[0];
    const options=cueMapCatalog.palette.map(color=>`<option value="${color.id}" ${color.id===spec.color?"selected":""}>${escapeHtml(color.name)} · ${color.display}</option>`).join("");
    const meanings=cueMapCatalog.meanings.map(item=>`<option value="${item.id}" ${item.id===spec.meaning?"selected":""}>${escapeHtml(item.label)}</option>`).join("");
    const preview=spec.label.replaceAll("{bars}","8").replaceAll("{beats}","32");
    return `<article class="cue-map-row ${spec.enabled?"":"disabled"}" data-cue-slot="${slot}">
      <div class="cue-map-slot" data-cue-color="${selectedColor.id}"><strong>${slot}</strong><small>PAD ${slot}</small></div>
      <div class="cue-map-fields"><label>What should this cue find?<select class="map-meaning" data-slot="${slot}">${meanings}</select></label>
        <label>Serato label<input class="map-label" data-slot="${slot}" maxlength="80" value="${escapeHtml(spec.label)}" placeholder="Example: OUTRO -{bars}">
          <span class="field-hint">Use {beats} for beats or {bars} for bars (END for an end-of-song EXIT). INTRO/GO/EXIT defaults count beats; drop/chorus leads count bars.</span></label></div>
      <div class="cue-map-color"><label>Pad color<select class="map-color" data-slot="${slot}">${options}</select></label>
        <span class="palette-preview" data-cue-color="${selectedColor.id}"><i></i><span>${selectedColor.display} in Serato</span></span>
        <label class="cue-enabled"><input class="map-enabled" data-slot="${slot}" type="checkbox" ${spec.enabled?"checked":""}> Enabled</label></div>
      <div class="cue-map-preview"><span>PREVIEW</span><strong data-cue-color="${selectedColor.id}">${escapeHtml(preview)}</strong></div>
    </article>`;
  }).join("");
  $$(".map-color").forEach(select=>select.onchange=()=>{readCueMapEditor();renderCueMapEditor()});
  $$(".map-enabled").forEach(input=>input.onchange=()=>{readCueMapEditor();renderCueMapEditor()});
  $$(".map-meaning").forEach(select=>select.onchange=()=>{readCueMapEditor();renderCueMapEditor()});
  $$(".map-label").forEach(input=>input.oninput=()=>{
    const preview=$(".cue-map-preview strong",input.closest(".cue-map-row"));
    preview.textContent=input.value.replaceAll("{bars}","8").replaceAll("{beats}","32");
  });
}
async function openCueMaps() {
  const data=await api("/api/settings");
  cueMapSettings=JSON.parse(JSON.stringify(data.cue_maps));
  cueMapCatalog=data.cue_map_catalog;
  cueMapSettings._defaults=data.cue_map_defaults;
  activeCueMap="VOCAL";
  $$(".map-tab").forEach(tab=>{
    tab.classList.toggle("active",tab.dataset.mapTab===activeCueMap);
    tab.setAttribute("aria-selected",String(tab.dataset.mapTab===activeCueMap));
  });
  renderCueMapEditor();
  $("#cue-map-dialog").showModal();
}
function readCueMapEditor() {
  cueMapSettings.end_of_song_fallback=$("#mix-end-fallback").checked;
  cueMapSettings.mix_lengths=Object.fromEntries(["intro","go","exit"].map(key=>[
    key,$(`#mix-${key}`).value.split(",").map(value=>Number(value.trim()))
  ]));
  const map=cueMapSettings.maps[activeCueMap];
  $$(".cue-map-row").forEach(row=>{
    const slot=row.dataset.cueSlot;
    map[slot]={
      meaning:$(`.map-meaning[data-slot="${slot}"]`).value,
      label:$(`.map-label[data-slot="${slot}"]`).value.trim(),
      color:$(`.map-color[data-slot="${slot}"]`).value,
      enabled:$(`.map-enabled[data-slot="${slot}"]`).checked
    };
  });
}
async function saveCueMaps() {
  readCueMapEditor();
  cueMapSettings.mode=$("#cue-map-mode").value;
  const payload={version:cueMapSettings.version,mode:cueMapSettings.mode,maps:cueMapSettings.maps,mix_lengths:cueMapSettings.mix_lengths,end_of_song_fallback:cueMapSettings.end_of_song_fallback};
  await api("/api/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({cue_maps:payload})});
  $("#cue-map-dialog").close();
  toast("Cue maps saved. New analyses will use your settings.");
  loadSettingsLine();
}
function cueMapModeLabel(mode) {
  return mode==="DROP"?"Electronic map":mode==="VOCAL"?"Vocal map":"Auto map";
}
async function writeReviewed() {
  const hashes=tracks.filter(track=>selected.has(track.hash)&&track.reviewed&&track.source_available&&track.write_supported).map(track=>track.hash);
  if(!hashes.length)return;
  const confirmed=confirm(`AutoCue will create and verify a Serato backup before writing cues to ${hashes.length} selected supported music file(s). Serato must be closed. Continue?`);
  if(!confirmed)return;
  try{const result=await api("/api/write",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({hashes})});toast(`Written. Backup: ${result.backup_id}`);await reload();}
  catch(error){toast(error.message);}
}
function openCrate(crate, songs) {
  $("#crate-title").textContent=crate.name;
  $("#crate-songs").innerHTML=songs.map((song,index)=>`<label class="crate-song ${song.missing?"missing":""}">
    <input type="checkbox" class="crate-song-check" value="${escapeHtml(song.path||"")}" ${song.missing?"disabled":index<60?"checked":""}>
    <span>${escapeHtml(song.name)}<small>${song.missing?"Missing file":escapeHtml(song.path)}</small></span></label>`).join("");
  $("#crate-song-count").textContent=`${songs.filter(song=>!song.missing).length} available · ${songs.filter(song=>song.missing).length} missing`;
  $("#crate-select-all").checked=songs.filter(song=>!song.missing).length<=60;
  $("#crate-select-all").onchange=()=>$$(".crate-song-check:not(:disabled)").forEach(box=>box.checked=$("#crate-select-all").checked);
  $("#process-crate").onclick=async()=>{
    const paths=$$(".crate-song-check:checked").map(box=>box.value).filter(Boolean);
    if(!paths.length)return toast("Select at least one available song.");
    try {
      const imported=await api("/api/crates/import",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({crate_path:crate.path,paths})});
      $("#crate-dialog").close();await reload();await queueTracks(imported.items.map(item=>item.hash));toast(`${imported.items.length} selected · ${imported.missing} missing`);
    } catch(error) { toast(error.message); }
  };
  $("#crate-dialog").showModal();
}
function playChime() {
  try {
    const context=new AudioContext(), oscillator=context.createOscillator(), gain=context.createGain();
    oscillator.type="sine";oscillator.frequency.value=880;gain.gain.setValueAtTime(.05,context.currentTime);
    gain.gain.exponentialRampToValueAtTime(.001,context.currentTime+.22);oscillator.connect(gain);gain.connect(context.destination);
    oscillator.start();oscillator.stop(context.currentTime+.22);oscillator.onended=()=>context.close();
  } catch(error) { console.warn("Completion chime unavailable",error); }
}
async function jobAction(action) {
  if(!activeJob)return;
  try{await api(`/api/jobs/${activeJob}/action`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action})});await reload()}
  catch(error){toast(error.message)}
}

$("#browse-files").onclick=()=>$("#file-picker").click();
$("#browse-folder").onclick=()=>$("#folder-picker").click();
$("#file-picker").onchange=event=>addFiles(event.target.files).catch(error=>toast(error.message));
$("#folder-picker").onchange=event=>addFiles(event.target.files).catch(error=>toast(error.message));
$("#dropzone").addEventListener("dragover",event=>{event.preventDefault();$("#dropzone").classList.add("dragging")});
$("#dropzone").addEventListener("dragleave",()=>$("#dropzone").classList.remove("dragging"));
$("#dropzone").addEventListener("drop",event=>{event.preventDefault();$("#dropzone").classList.remove("dragging");addFiles(event.dataTransfer.files).catch(error=>toast(error.message))});
$("#path-add").onclick=async()=>{try{const data=await api("/api/import-path",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:$("#path-input").value})});await reload();await queueTracks(data.items.map(item=>item.hash));toast(`${data.items.length} tracks added · ${data.skipped} skipped`)}catch(error){toast(error.message)}};
$("#process-selected").onclick=()=>queueTracks([...selected]).catch(error=>toast(error.message));
$("#reanalyze-selected").onclick=()=>{if(confirm("Re-analyze selected tracks even if they already have results?"))queueTracks([...selected],true).catch(error=>toast(error.message))};
const removeTracks=async(body,message)=>{if(!confirm(message))return;try{const data=await api("/api/tracks/remove",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});body.hashes?.forEach(hash=>selected.delete(hash));if(body.all)selected.clear();await reload();toast(`Removed ${data.removed} track${data.removed===1?"":"s"} from the library.`)}catch(error){toast(error.message)}};
$("#remove-selected").onclick=()=>{if(!selected.size)return toast("Select tracks to remove first.");removeTracks({hashes:[...selected]},`Remove ${selected.size} selected track(s), including their local cue reviews and event labels? Original files are not touched.`)};
$("#clear-library").onclick=()=>removeTracks({all:true},"Clear the whole library? Analysis results, cue reviews, and event labels will be lost. Your original files are not touched.");
$("#map-select").onchange=async event=>{
  const choice=event.target.value,track=activeTrack;
  if(!track||choice===(track.map_override||"AUTO"))return;
  event.target.value=track.map_override||"AUTO";
  if(!(await confirmReviewLeave()))return;
  const warning=track.edited?" Your saved cue edits for this song will be replaced.":"";
  if(!confirm(`Re-derive this song's cues using the ${choice==="AUTO"?"automatic":choice} map?${warning} It reuses the earlier analysis, so it only takes a moment.`)){event.target.value=track.map_override||"AUTO";return}
  setReviewBusy(true);
  try{const result=await api(`/api/tracks/${track.hash}/map`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({map_type:choice})});
    $("#review-dialog").close();if(result.job_id){activeJob=result.job_id;startPolling()}await reload();toast("Re-deriving cues with the new map…")}
  catch(error){event.target.value=track.map_override||"AUTO";toast(error.message)}
  finally{setReviewBusy(false)}
};
$("#start-review").onclick=()=>{$("#status-filter").value="Needs a look";renderTracks();document.querySelector(".library-section").scrollIntoView({behavior:"smooth"})};
$("#select-all").onchange=()=>{const visible=tracks.filter(track=>!$("#status-filter").value||track.status===$("#status-filter").value);visible.forEach(track=>$("#select-all").checked?selected.add(track.hash):selected.delete(track.hash));renderTracks()};
$("#search").oninput=renderTracks;$("#status-filter").onchange=renderTracks;
$("#crates-refresh").onclick=refreshCrates;
$("#job-pause").onclick=()=>jobAction("pause");$("#job-resume").onclick=()=>jobAction("resume");$("#job-cancel").onclick=()=>jobAction("cancel");
$("#settings-open").onclick=$("#settings-inline").onclick=()=>openSettings().catch(error=>toast(error.message));
$("#settings-save").onclick=async()=>{try{await api("/api/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({gemini:$("#gemini-toggle").checked,strictness:$("#strictness").value})});$("#settings-dialog").close();$("#using-settings").textContent=`Using: Auto map · Gemini tie-break ${$("#gemini-toggle").checked?"on":"off"} · Review ${$("#strictness").value}`}catch(error){toast(error.message)}};
$("#cue-maps-open").onclick=()=>openCueMaps().catch(error=>toast(error.message));
$$(".map-tab").forEach(tab=>tab.onclick=()=>{
  readCueMapEditor();activeCueMap=tab.dataset.mapTab;
  $$(".map-tab").forEach(item=>{
    item.classList.toggle("active",item===tab);
    item.setAttribute("aria-selected",String(item===tab));
  });
  renderCueMapEditor();
});
$("#cue-map-save").onclick=()=>saveCueMaps().catch(error=>toast(error.message));
$("#cue-map-defaults").onclick=()=>{
  if(!confirm("Restore both cue maps to their recommended defaults? Your custom meanings, labels, and colors will be replaced."))return;
  const defaults=cueMapSettings._defaults;
  cueMapSettings={version:defaults.version,mode:defaults.mode,maps:JSON.parse(JSON.stringify(defaults.maps)),mix_lengths:JSON.parse(JSON.stringify(defaults.mix_lengths)),end_of_song_fallback:defaults.end_of_song_fallback,_defaults:defaults};
  renderCueMapEditor();
};
$("#cue-map-copy").onclick=()=>{
  readCueMapEditor();
  const other=activeCueMap==="VOCAL"?"DROP":"VOCAL";
  cueMapSettings.maps[other]=JSON.parse(JSON.stringify(cueMapSettings.maps[activeCueMap]));
  toast(`Copied ${activeCueMap==="VOCAL"?"vocal":"electronic"} cue settings to the ${other==="VOCAL"?"vocal":"electronic"} map.`);
};
$("#save-cues").onclick=()=>saveReview();$("#save-next").onclick=()=>saveReview(true);$("#next-track").onclick=()=>{const t=neighborTrack(1);if(t)openReview(t.hash)};$("#prev-track").onclick=()=>{const t=neighborTrack(-1);if(t)openReview(t.hash)};$("#write-reviewed").onclick=writeReviewed;
$("#add-event-label").onclick=()=>addEventLabel();$("#save-event-review").onclick=()=>saveEventReviewState();
$("#sound-toggle").onclick=()=>{soundOn=!soundOn;$("#sound-toggle").textContent=`Sound: ${soundOn?"On":"Off"}`};
$("#finder-button").onclick=async()=>{try{await api(`/api/finder/${activeTrack.hash}`,{method:"POST"});toast("Showing original file in Finder.")}catch(error){toast(error.message)}};
$$("[data-close]").forEach(button=>button.onclick=()=>{
  const dialog=button.closest("dialog");
  if(dialog.id==="review-dialog")closeReview();
  else dialog.close();
});
$$("[data-review-leave]").forEach(button=>button.onclick=()=>resolveReviewLeave(button.dataset.reviewLeave));
$("#unsaved-review-dialog").addEventListener("cancel",event=>{event.preventDefault();resolveReviewLeave("cancel")});
$("#review-dialog").addEventListener("cancel",event=>{event.preventDefault();closeReview()});
window.addEventListener("beforeunload",event=>{
  if(reviewSaving||eventReviewSaving||reviewHasChanges()){event.preventDefault();event.returnValue=""}
});
drawRunner();drawProgress(0);reload();refreshCrates();loadSettingsLine();loadCapabilities();
async function loadSettingsLine(){try{const data=await api("/api/settings");$("#using-settings").textContent=`Using: ${cueMapModeLabel(data.cue_maps?.mode||"AUTO")} · Gemini tie-break ${data.gemini==="true"?"on":"off"} · Review ${data.strictness||"standard"}`}catch(error){toast(error.message)}}
async function loadCapabilities(){
  try {
    const status=await api("/api/status");
    if(!status.processor_configured)$("#import-message").textContent="Analysis engine not wired for fresh audio yet. Imported files stay local; jobs will report this limitation.";
    if(!status.write_dependency_available)$("#write-reviewed").title="Install web/requirements.txt to enable Serato tag writes.";
    if(!status.write_platform_supported)$("#write-reviewed").title="Serato writes are supported only on macOS.";
  } catch(error){toast(error.message)}
}

async function openExperiments() {
  const [settings, report] = await Promise.all([api("/api/settings"), api("/api/experiments/report")]);
  const enabled = new Set(settings.experiments || []);
  $("#experiment-list").innerHTML = settings.experiment_catalog.map(item => `<label class="setting-row"><span><strong><i class="variant-dot" data-variant="${item.id}"></i>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.summary)}${item.heavy ? " Slow." : ""}</small></span><input type="checkbox" data-experiment="${item.id}" ${enabled.has(item.id) ? "checked" : ""} ${item.id === "baseline" ? "disabled" : ""}></label>`).join("");
  $("#experiment-report").innerHTML = renderScoreReport(report);
  $("#experiments-dialog").showModal();
}
function renderScoreReport(report) {
  const rows = Object.entries(report.variants || {});
  const pct = value => value && value.total ? Math.round(100 * value.right / value.total) + "%" : "–";
  const positionedPct = value => pct({right:value?.positioned_right || 0,total:value?.positioned_total || 0});
  const notes = `<p class="muted">Only saved human targets count. Omitted targets stay unknown; explicit empty targets are shown separately. Different cue meanings/maps are excluded from timing comparisons.</p>` +
    `<p class="muted">${report.skipped?.length || 0} tracks skipped; ${report.unavailable_predictions?.length || 0} unavailable predictions. Export the report for per-cue errors and exclusions.</p>`;
  if (!report.graded_songs || !rows.length) return `<div class="empty-state">No scorable reviewed targets with saved automatic predictions yet. Analyze, review and save songs first. No accuracy estimate is available.</div>` + notes;
  return `<h3>Scored on ${report.graded_songs} reviewed song${report.graded_songs === 1 ? "" : "s"} (within one beat)</h3>` + notes +
    `<table class="variant-table"><thead><tr><th>Experiment</th><th>Positioned targets</th><th>Explicit empty targets</th><th>Vocal positioned</th><th>Drop positioned</th><th>Map correct</th><th>Fixes vs rules</th><th>Breaks vs rules</th></tr></thead><tbody>` +
    rows.map(([id, row]) => {
      return `<tr><th><i class="variant-dot" data-variant="${escapeHtml(id)}"></i>${escapeHtml(row.label)}</th><td>${pct({right:row.positioned_right,total:row.positioned_total})} <small>(${row.positioned_right}/${row.positioned_total})</small></td><td>${row.empty_right}/${row.empty_total}</td><td>${positionedPct(row.by_map?.VOCAL)}</td><td>${positionedPct(row.by_map?.DROP)}</td><td>${pct(report.map_comparison?.[id])}</td><td>${id === "baseline" ? "–" : row.wins}</td><td>${id === "baseline" ? "–" : row.losses}</td></tr>`;
    }).join("") + `</tbody></table><p class="muted">Recorded new Gemini cost on these saved runs: $${Number(report.gemini_cost?.new_cost_usd || 0).toFixed(4)}; ${report.gemini_cost?.unknown_cost_tracks || 0} tracks with unknown cost. Not lifetime/provider billing.</p>`;
}
$("#experiments-open").onclick = () => openExperiments().catch(error => toast(error.message));
$("#experiments-save").onclick = async () => {
  const experiments = $$("[data-experiment]:checked").map(box => box.dataset.experiment);
  try { await api("/api/settings", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({experiments})}); $("#experiments-dialog").close(); toast("Experiment choices saved for the next analysis."); }
  catch (error) { toast(error.message); }
};
