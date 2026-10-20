const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const SLOT_COLORS = ["#1DBEBD", "#1FAD26", "#6823B6", "#CE359E", "#DC1D49", "#FAC313", "#0F88CA", "#F8821A"];
const SLOT_COLOR_IDS = ["teal", "green", "purple", "magenta", "rose", "yellow", "sky", "orange"];
let tracks = [], selected = new Set(), activeTrack = null, activeJob = null, soundOn = false, refreshTimer;
let eventSource;
let lastCompletedJob = "";
let sessionToken;
let cueMapSettings = null, cueMapCatalog = null, activeCueMap = "VOCAL";

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
      const x=(track.t1+bar*track.bar_s)/liveWave.duration*width;context.fillStyle=SLOT_COLORS[i];context.fillRect(x,0,2,height);context.fillText(slot,x+3,10)});
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
  activeTrack = tracks.find(track => track.hash === hash);
  if (!activeTrack) return;
  $("#review-title").textContent = displayName(activeTrack);
  $("#preview-audio").src = activeTrack.url;
  $("#cue-edit-list").innerHTML = "ABCDEFGH".split("").map((slot, i) => {
    const cue = activeTrack.cues?.[slot] || {};
    return `<div class="cue-edit"><span class="slot cue-${slot.toLowerCase()}">${slot}</span>
      <div class="cue-edit-main"><div class="cue-edit-title"><strong>${escapeHtml(cue.name || slot)}</strong><small>${escapeHtml(cue.meaning ? meaningLabel(cue.meaning) : "Cue slot " + slot)}</small></div>
      <input class="cue-name" data-slot="${slot}" value="${escapeHtml(cue.name || slot)}" aria-label="${slot} cue name"></div>
      <div class="cue-timing"><button data-preview="${slot}" title="Play from cue">▶</button><button data-nudge="-0.25" data-slot="${slot}" title="Nudge one beat earlier">−¼</button>
      <button data-nudge="0.25" data-slot="${slot}" title="Nudge one beat later">+¼</button><button data-nudge="-1" data-slot="${slot}" title="Nudge one bar earlier">−B</button>
      <button data-nudge="1" data-slot="${slot}" title="Nudge one bar later">+B</button><input class="cue-bar" data-slot="${slot}" type="number" min="${firstBar()}" step="0.25" value="${cue.bar ?? ""}" placeholder="Empty" aria-label="${slot} bar"></div></div>`;
  }).join("");
  $("#review-dialog").showModal();
  renderVariantPanel(activeTrack);
  $$(".cue-timing button[data-nudge]").forEach(button => button.onclick=()=>nudgeCue(button.dataset.slot,Number(button.dataset.nudge)));
  $$(".cue-timing button[data-preview]").forEach(button => button.onclick=()=>previewCue(button.dataset.preview));
  await drawWaveform(activeTrack);
}
function firstBar() {
  const {t1, bar_s} = activeTrack || {};
  return t1 > 0 && bar_s > 0 ? Math.min(0, Math.ceil(-t1 / bar_s - 1e-6)) : 0;
}
function nudgeCue(slot, bars) {
  const input = $(`.cue-bar[data-slot="${slot}"]`);
  if (input.value === "") return toast("Set a cue bar before nudging.");
  input.value = Math.max(firstBar(), Number(input.value) + bars);
}
function previewCue(slot) {
  const bar = Number($(`.cue-bar[data-slot="${slot}"]`).value);
  if (!Number.isFinite(bar) || activeTrack.t1 == null || activeTrack.bar_s == null) return toast("This track has no timing grid yet.");
  const audio = $("#preview-audio"); audio.currentTime = Math.max(0, activeTrack.t1 + bar * activeTrack.bar_s); audio.play();
}
let waveDuration = 0;
const VARIANT_COLORS = {baseline:"#d9e2e4", fuzzy_lyrics:"#ffcc33", kick_grid:"#4aa8ff", stem_energy:"#ff7ac8", roformer_stems:"#ff9a3c"};
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
    const bar = (Number(button.dataset.time) - activeTrack.t1) / activeTrack.bar_s;
    $(`.cue-bar[data-slot="${button.dataset.slot}"]`).value = (Math.round(bar * 4) / 4).toString();
    toast(`Cue ${button.dataset.slot} set to bar ${Math.round(bar * 4) / 4}. Save review to keep it.`);
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
      const x = Math.max(0, Math.min(width - 2, time / waveDuration * width));
      ctx.fillRect(x - 1, top + 3, 3, laneHeight - 6);
      ctx.font = "10px monospace"; ctx.fillText(slot, x + 4, top + 15);
    }
  });
}
async function drawWaveform(track) {
  const canvas = $("#waveform"), ctx = canvas.getContext("2d"), width = canvas.clientWidth, height = 190;
  const scale = devicePixelRatio || 1; canvas.width = width * scale; canvas.height = height * scale; ctx.scale(scale, scale);
  ctx.fillStyle = "#0a1012"; ctx.fillRect(0,0,width,height);
  try {
    const response = await fetch(track.url), buffer = await response.arrayBuffer();
    const audio = await (new AudioContext()).decodeAudioData(buffer);
    const samples = audio.getChannelData(0), columns = Math.floor(width), step = Math.max(1, Math.floor(samples.length / columns));
    ctx.strokeStyle = "#395b52"; ctx.lineWidth = 1; ctx.beginPath();
    for(let x=0;x<columns;x++){let peak=0;for(let i=x*step;i<Math.min((x+1)*step,samples.length);i+=8)peak=Math.max(peak,Math.abs(samples[i]));const y=peak*(height*.44);ctx.moveTo(x,height/2-y);ctx.lineTo(x,height/2+y)}
    ctx.stroke();
    for(const [i,slot] of "ABCDEFGH".split("").entries()){
      const bar=track.cues?.[slot]?.bar;if(bar==null)continue;
      const seconds=track.t1+bar*track.bar_s, x=Math.max(0,Math.min(width,seconds/audio.duration*width));
      const color=track.cues?.[slot]?.color||SLOT_COLORS[i];
      ctx.strokeStyle=color;ctx.lineWidth=1.5;ctx.beginPath();ctx.moveTo(x,12);ctx.lineTo(x,height-12);ctx.stroke();
      ctx.fillStyle=color;ctx.fillRect(x-4,10,8,8);
    }
    waveDuration = audio.duration; drawVariantLanes();
    $("#wave-legend").innerHTML = "ABCDEFGH".split("").map((s,i)=>`<span data-cue-color="${escapeHtml(track.cues?.[s]?.color_id||SLOT_COLOR_IDS[i])}">${s} · ${escapeHtml(track.cues?.[s]?.name||"")}</span>`).join("");
  } catch { ctx.fillStyle="#829198";ctx.font="12px monospace";ctx.fillText("Waveform preview unavailable for this file format",14,30); }
}
async function saveReview() {
  const cues = {};
  $$(".cue-edit").forEach(row => {
    const slot = $(".cue-name", row).dataset.slot;
    cues[slot] = {name:$(".cue-name",row).value,bar:$(".cue-bar",row).value};
  });
  try {
    await api(`/api/tracks/${activeTrack.hash}`, {method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({cues,map_type:activeTrack.map_type})});
    selected.add(activeTrack.hash);$("#review-dialog").close();toast("Review saved. Cue edits are local.");await reload();
  } catch(error) { toast(error.message); }
}
async function openSettings() {
  const data=await api("/api/settings");$("#gemini-toggle").checked=data.gemini==="true";$("#strictness").value=data.strictness||"standard";$("#settings-dialog").showModal();
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
  $("#cue-map-rows").innerHTML="ABCDEFGH".split("").map(slot=>{
    const spec=map[slot];
    const selectedColor=cueMapCatalog.palette.find(color=>color.id===spec.color)||cueMapCatalog.palette[0];
    const options=cueMapCatalog.palette.map(color=>`<option value="${color.id}" ${color.id===spec.color?"selected":""}>${escapeHtml(color.name)} · ${color.display}</option>`).join("");
    const meanings=cueMapCatalog.meanings.map(item=>`<option value="${item.id}" ${item.id===spec.meaning?"selected":""}>${escapeHtml(item.label)}</option>`).join("");
    const preview=spec.label.replaceAll("{bars}","16");
    return `<article class="cue-map-row ${spec.enabled?"":"disabled"}" data-cue-slot="${slot}">
      <div class="cue-map-slot" data-cue-color="${selectedColor.id}"><strong>${slot}</strong><small>PAD ${slot}</small></div>
      <div class="cue-map-fields"><label>What should this cue find?<select class="map-meaning" data-slot="${slot}">${meanings}</select></label>
        <label>Serato label<input class="map-label" data-slot="${slot}" maxlength="80" value="${escapeHtml(spec.label)}" placeholder="Example: OUTRO -{bars}">
          <span class="field-hint">Use {bars} for an automatic bar count.</span></label></div>
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
    preview.textContent=input.value.replaceAll("{bars}","16");
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
  const payload={version:cueMapSettings.version,mode:cueMapSettings.mode,maps:cueMapSettings.maps};
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
  cueMapSettings={version:defaults.version,mode:defaults.mode,maps:JSON.parse(JSON.stringify(defaults.maps)),_defaults:defaults};
  renderCueMapEditor();
};
$("#cue-map-copy").onclick=()=>{
  readCueMapEditor();
  const other=activeCueMap==="VOCAL"?"DROP":"VOCAL";
  cueMapSettings.maps[other]=JSON.parse(JSON.stringify(cueMapSettings.maps[activeCueMap]));
  toast(`Copied ${activeCueMap==="VOCAL"?"vocal":"electronic"} cue settings to the ${other==="VOCAL"?"vocal":"electronic"} map.`);
};
$("#save-cues").onclick=saveReview;$("#write-reviewed").onclick=writeReviewed;
$("#sound-toggle").onclick=()=>{soundOn=!soundOn;$("#sound-toggle").textContent=`Sound: ${soundOn?"On":"Off"}`};
$("#finder-button").onclick=async()=>{try{await api(`/api/finder/${activeTrack.hash}`,{method:"POST"});toast("Showing original file in Finder.")}catch(error){toast(error.message)}};
$$("[data-close]").forEach(button=>button.onclick=()=>button.closest("dialog").close());
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
  const rows = Object.entries(report.variants || {});
  $("#experiment-report").innerHTML = report.graded_songs ? `<h3>Scored on ${report.graded_songs} graded song${report.graded_songs === 1 ? "" : "s"} (within one beat of your saved cues)</h3>
    <table class="variant-table"><thead><tr><th>Experiment</th><th>Accuracy</th><th>Vocal</th><th>Drop</th><th>Fixes vs baseline</th><th>Breaks vs baseline</th></tr></thead><tbody>` +
    rows.map(([id, row]) => {
      const pct = value => value && value.total ? Math.round(100 * value.right / value.total) + "%" : "–";
      return `<tr><th><i class="variant-dot" data-variant="${id}"></i>${escapeHtml(row.label)}</th><td>${Math.round(100 * row.accuracy)}% <small>(${row.right}/${row.total})</small></td><td>${pct(row.by_map?.VOCAL)}</td><td>${pct(row.by_map?.DROP)}</td><td>${id === "baseline" ? "–" : row.wins}</td><td>${id === "baseline" ? "–" : row.losses}</td></tr>`;
    }).join("") + `</tbody></table>` : `<div class="empty-state">Analyze some songs, then review and save them. Your saved cues become the answer key and this table fills in.</div>`;
  $("#experiments-dialog").showModal();
}
$("#experiments-open").onclick = () => openExperiments().catch(error => toast(error.message));
$("#experiments-save").onclick = async () => {
  const experiments = $$("[data-experiment]:checked").map(box => box.dataset.experiment);
  try { await api("/api/settings", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({experiments})}); $("#experiments-dialog").close(); toast("Experiment choices saved for the next analysis."); }
  catch (error) { toast(error.message); }
};
