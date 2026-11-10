(() => {
  let outbox, stageOutbox, scanOutbox, scanPoll, snapshot = "", crate = null, allCrates = [], busy = false, generation = 0, fileOffset = 0;
  const scanStates = new Map(), activeScans = new Set(["queued", "running", "cancelling"]);
  const status = (message, failed = false) => {
    $("#catalog-status").textContent = message;
    $("#catalog-status").classList.toggle("catalog-error", failed);
  };
  const request = (url, body) => api(url, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
  function pending() {
    $("#catalog-pending").hidden = !outbox?.pending;
    $("#catalog-stage-pending").hidden = !stageOutbox?.pending;
    $("#catalog-scan-pending").hidden = !scanOutbox?.pending;
  }
  async function action(run) {
    if (busy) return;
    busy = true;
    $("#catalog-section").setAttribute("aria-busy", "true");
    try { await run(); }
    catch (error) { status(error.message, true); }
    finally {
      busy = false;
      $("#catalog-section").setAttribute("aria-busy", "false");
      pending();
    }
  }
  function renderCrates() {
    const query = $("#catalog-search").value.toLowerCase();
    $("#catalog-crates").innerHTML = allCrates.filter(item => item.title.toLowerCase().includes(query)).map(item =>
      `<div class="crate-item"><button class="link-button" data-catalog-crate="${escapeHtml(item.source_id)}">${escapeHtml(item.title)}</button><span class="crate-meta">${item.available ? `${item.songs} songs` : "Source unavailable"} · ID ${escapeHtml(item.source_id)}</span></div>`
    ).join("") || '<p class="muted">No catalog crates found.</p>';
    $$("[data-catalog-crate]").forEach(button => button.onclick = () => action(async () => {
      crate = allCrates.find(item => item.source_id === button.dataset.catalogCrate);
      await loadSongs();
    }));
  }
  async function refreshSnapshots() {
    const data = await api("/api/catalog/snapshots");
    if (!data.snapshots.some(item => item.id === snapshot)) snapshot = data.snapshots[0]?.id || "";
    $("#catalog-snapshot").innerHTML = data.snapshots.map(item =>
      `<option value="${item.id}" ${item.id === snapshot ? "selected" : ""}>${escapeHtml(item.source)} · ${item.id.slice(0, 10)}</option>`
    ).join("") || '<option value="">No snapshots imported</option>';
    await loadCrates();
  }
  async function loadCrates() {
    crate = null;
    generation++;
    $("#catalog-songs").replaceChildren();
    $("#catalog-crate-title").textContent = "Choose a catalog crate";
    const data = await api("/api/catalog/crates?snapshot_id=" + encodeURIComponent(snapshot));
    allCrates = data.crates;
    renderCrates();
  }
  async function refreshRoots() {
    const data = await api("/api/catalog/roots");
    $("#catalog-roots").innerHTML = data.roots.map(root =>
      `<div class="crate-item"><div><strong>${escapeHtml(root.path)}</strong><span class="crate-meta">${escapeHtml(root.state)}${root.availability_error || root.error ? " · " + escapeHtml(root.availability_error || root.error) : ""}</span>
      ${root.state === "offline" ? `<label>Replacement folder<input data-catalog-replacement="${root.id}" placeholder="/absolute/path/to/replacement/music"></label><button class="quiet" data-catalog-reassociate="${root.id}">Verify and reassociate</button>` : ""}</div><button class="quiet" data-catalog-rescan="${escapeHtml(root.path)}">Rescan</button></div>`
    ).join("") || '<p class="muted">No folders indexed.</p>';
    $$("[data-catalog-rescan]").forEach(button => button.onclick = () => action(() => scan(button.dataset.catalogRescan)));
    $$("[data-catalog-reassociate]").forEach(button => button.onclick = () => action(async () => {
      const root = data.roots.find(item => item.id === button.dataset.catalogReassociate);
      const path = $(`[data-catalog-replacement="${root.id}"]`).value;
      if (!path.trim()) throw new Error("Enter the replacement folder's absolute path.");
      if (!confirm(`Verify and reassociate offline folder ${root.path} to ${path}? Every previously available indexed file must exist at the same relative path with identical bytes. All files will be rehashed; missing, changed or previously errored files block reassociation. Matches keep their IDs; original-file Serato write links are NOT moved. No files or tags are written.`)) return;
      scanOutbox.prepare({path, root_id:root.id, base_revision:root.scan_revision});
      pending();
      await sendScanPending();
    }));
  }
  async function loadFiles() {
    const data = await api("/api/catalog/files?offset=" + fileOffset);
    $("#catalog-files").innerHTML = data.files.map(file =>
      `<div class="crate-item"><div>${escapeHtml(file.path)}<span class="crate-meta">${escapeHtml(file.state)} · ${escapeHtml(file.metadata.artist || "Unknown artist")} / ${escapeHtml(file.metadata.title || "Unknown title")}${file.error ? " · " + escapeHtml(file.error) : ""}</span></div></div>`
    ).join("") || '<p class="muted">No indexed files.</p>';
    $("#catalog-files-more").hidden = fileOffset + data.files.length >= data.total;
  }
  async function scan(path) {
    scanOutbox.prepare({path});
    pending();
    await sendScanPending();
  }
  async function sendScanPending() {
    if (!scanOutbox?.pending) throw new Error("No pending scan request.");
    const receipt = await request("/api/catalog/scan", scanOutbox.pending);
    scanOutbox.accept(receipt);
    status("Scan submission acknowledged. Inspect current job status below; retrying submission never reruns a finished scan.");
    await refreshScanJobs();
  }
  function refreshScanJobs() {
    if (scanPoll) return scanPoll;
    scanPoll = (async () => {
      const {jobs} = await api("/api/catalog/scans");
      $("#catalog-scans").innerHTML = jobs.map(job => {
        const progress = job.progress, result = job.result;
        const detail = result ? `${result.scanned} audio paths · ${result.reused} unchanged · ${result.errors.length} metadata/read errors · ${result.skipped_symlinks.length} symlinks skipped` :
          `${progress.phase} · ${progress.scanned} audio paths encountered · ${progress.reused} unchanged · ${progress.errors} errors${progress.current_path ? " · " + progress.current_path : ""}`;
        return `<div class="crate-item"><div><strong>${escapeHtml(job.path)}</strong><span class="crate-meta">${job.operation === "reassociate" ? "Reassociation · " : ""}${escapeHtml(job.status)} · ${escapeHtml(detail)}${result?.reassociated ? ` · ${result.verified_files} indexed files verified; folder reassociated` : ""}</span>${job.error ? `<span class="catalog-error">${escapeHtml(job.error)}</span>` : ""}
          ${result?.errors.length ? `<details><summary>First ${Math.min(10, result.errors.length)} errors (all retained in indexed files)</summary>${result.errors.slice(0, 10).map(error => `<p>${escapeHtml(error.path)}: ${escapeHtml(error.error)}</p>`).join("")}</details>` : ""}</div>
          ${activeScans.has(job.status) ? `<button class="quiet" data-catalog-scan-cancel="${job.id}" ${job.status === "cancelling" ? "disabled" : ""}>Cancel scan</button>` : ""}</div>`;
      }).join("") || '<p class="muted">No scan jobs yet.</p>';
      $$("[data-catalog-scan-cancel]").forEach(button => button.onclick = () => action(async () => {
        const reply = await request("/api/catalog/scans/cancel", {dataset_id:datasetId, job_id:button.dataset.catalogScanCancel});
        status(`Scan cancellation acknowledged: ${reply.job.status}. A completed publication cannot be undone by cancellation.`);
        await refreshScanJobs();
      }));
      const finished = jobs.some(job => !activeScans.has(job.status) &&
        (!scanStates.has(job.id) || activeScans.has(scanStates.get(job.id))));
      $("#catalog-scan-status").textContent = jobs.some(job => activeScans.has(job.status)) ?
        "Scanning read-only in the background. Cancellation waits for current filesystem/tag I/O; no partial index is published." :
        "No active scans. Failed, cancelled and interrupted jobs require a new explicit Index folder action.";
      $("#catalog-scan-status").classList.remove("catalog-error");
      if (finished) {
        await refreshRoots();
        fileOffset = 0;
        await loadFiles();
        if (crate) await loadSongs();
      }
      scanStates.clear();
      jobs.forEach(job => scanStates.set(job.id, job.status));
    })().finally(() => { scanPoll = null; });
    return scanPoll;
  }
  async function pollScans() {
    try { await refreshScanJobs(); }
    catch (error) {
      $("#catalog-scan-status").textContent = `Scan status unavailable: ${error.message}. Retrying automatically; do not assume completion.`;
      $("#catalog-scan-status").classList.add("catalog-error");
    }
    setTimeout(pollScans, 1500);
  }
  async function loadSongs() {
    const current = ++generation, selectedSnapshot = snapshot, selectedCrate = crate;
    const data = await api(`/api/catalog/songs?snapshot_id=${encodeURIComponent(snapshot)}&crate_id=${encodeURIComponent(crate.source_id)}`);
    if (current !== generation || selectedSnapshot !== snapshot || selectedCrate !== crate) return;
    $("#catalog-crate-title").textContent = `${crate.title} · ${crate.available ? data.songs.length + " memberships" : "Source unavailable"}`;
    $("#catalog-songs").innerHTML = data.songs.map((item, index) => {
      const song = item.song;
      return `<article class="catalog-song" data-catalog-search="${escapeHtml(`${song.artist} ${song.title} ${song.version || ""}`.toLowerCase())}">
        <h4>${index + 1}. ${escapeHtml(song.artist || "Unknown artist")} — ${escapeHtml(song.title || "Unknown title")} ${escapeHtml(song.version || "")}</h4>
        ${!song.artist.trim() || !song.title.trim() ? '<p class="catalog-error">Source artist or title is missing. Nothing is inferred; incomplete metadata cannot establish tag agreement.</p>' : ""}
        <p class="muted">Source ID ${escapeHtml(song.id)} · ${escapeHtml(item.state)}${item.decision ? ` · reviewed revision ${item.revision} · ${escapeHtml(item.decision.note)}` : " · no reviewed file selection"} · BPM ${escapeHtml(song.bpm ?? "unknown")} · Key ${escapeHtml(song.key ?? "unknown")}</p>
        ${item.selected_file ? `<p class="muted">Reviewed file: ${escapeHtml(item.selected_file.path)} · ${escapeHtml(item.selected_file.state)} · SHA-256 ${escapeHtml(item.decision.content_hash)}</p>` : ""}
        <div class="path-row">
          <button class="button secondary" data-catalog-personal="${index}">Add to selected personal crate</button>
          <button class="button secondary" data-catalog-stage="${index}" ${item.state === "exact" ? "" : "disabled"}>Stage reviewed copy</button>
          ${item.staged_track?.state === "available" ? `
            <button class="button secondary" data-catalog-process="${index}">Analyze staged copy</button>
            <button class="button secondary" data-catalog-review="${index}" ${item.staged_track.has_plan ? "" : "disabled"}>Review cues</button>` : ""}
        </div>
        <p class="muted">${item.staged_track ? `Staged copy: ${escapeHtml(item.staged_track.state)} · ${escapeHtml(item.staged_track.status || "not in analysis library")}${item.staged_track.error ? " · " + escapeHtml(item.staged_track.error) : ""}` : "Not staged for cue review."} Staging does not start analysis or link the original for Serato writes.</p>
        <label>File candidate <select data-catalog-choice="${index}"><option value="">Choose deliberately; nothing is selected automatically</option>${item.candidates.map((file, i) =>
          `<option value="${i}" ${file.state === "available" ? "" : "disabled"}>${escapeHtml(file.path)} · ${escapeHtml(file.state)} · ${file.metadata_equal ? "tags agree (review required)" : "VERSION CONFLICT"}</option>`).join("")}</select></label>
        <p class="muted">Matching tags do not prove the recording/edit. Listen and verify elsewhere before accepting. A version conflict requires an explicit override and note.</p>
        <label><input type="checkbox" data-catalog-override="${index}"> I intentionally accept a metadata/version conflict</label>
        <div class="path-row"><input data-catalog-note="${index}" aria-label="Review note for ${escapeHtml(song.title)}" placeholder="Review note (required for version conflict)">
        <button class="button secondary" data-catalog-accept="${index}">Accept reviewed file</button><button class="quiet" data-catalog-missing="${index}">Mark missing / clear match</button></div>
      </article>`;
    }).join("") || '<p class="muted">No songs in this source crate.</p>';
    function choose(index, missing) {
      return action(async () => {
        if (stageOutbox?.pending) throw new Error("Resolve the pending staging request before changing a match.");
        const item = data.songs[index], value = $(`[data-catalog-choice="${index}"]`).value;
        if (!missing && value === "") throw new Error("Choose a candidate before accepting a reviewed file.");
        const file = missing ? null : item.candidates[Number(value)];
        const override = $(`[data-catalog-override="${index}"]`).checked;
        const note = $(`[data-catalog-note="${index}"]`).value;
        if (file && !file.metadata_equal && (!override || !note.trim())) {
          throw new Error("Version/metadata conflict requires the explicit override checkbox and a review note.");
        }
        outbox.prepare({snapshot_id:selectedSnapshot, song_id:item.song.id, base_revision:item.revision,
          file_id:file?.id ?? null, content_hash:file?.content_hash ?? null,
          override, note});
        pending();
        await sendPending();
      });
    }
    $$("[data-catalog-accept]").forEach(button => button.onclick = () => choose(Number(button.dataset.catalogAccept), false));
    $$("[data-catalog-personal]").forEach(button => button.onclick = () => action(async () => {
      await PersonalCrates.add(selectedSnapshot, data.songs[Number(button.dataset.catalogPersonal)].song.id);
      status("Personal crate addition acknowledged. See Arrange your set for the latest saved order.");
    }));
    $$("[data-catalog-missing]").forEach(button => button.onclick = () => choose(Number(button.dataset.catalogMissing), true));
    $$("[data-catalog-stage]").forEach(button => button.onclick = () => action(async () => {
      if (outbox?.pending) throw new Error("Resolve the pending match decision before staging.");
      const item = data.songs[Number(button.dataset.catalogStage)];
      stageOutbox.prepare({snapshot_id:selectedSnapshot, song_id:item.song.id,
        decision_revision:item.revision, file_id:item.decision.file_id, content_hash:item.decision.content_hash});
      pending();
      await sendStagePending();
    }));
    $$("[data-catalog-process]").forEach(button => button.onclick = () => action(async () => {
      const item = data.songs[Number(button.dataset.catalogProcess)];
      if (!confirm("Analyze this app-local copy with your current analysis settings (including the saved Gemini toggle)? Models may download weights; LRCLIB may query artist/title. No original tags are written. Already analyzed tracks are not forced to rerun.")) return;
      await queueTracks([item.staged_track.hash]);
      status("Analysis request submitted. Follow batch progress, then refresh match and cue status.");
      await loadSongs();
    }));
    $$("[data-catalog-review]").forEach(button => button.onclick = () => action(async () => {
      const hash = data.songs[Number(button.dataset.catalogReview)].staged_track.hash;
      const library = await api("/api/library");
      tracks = library.tracks;
      renderTracks();
      if (!tracks.some(track => track.hash === hash)) throw new Error("Staged track was removed. Refresh match and cue status.");
      await openReview(hash);
    }));
    filterSongs();
  }
  function filterSongs() {
    const query = $("#catalog-song-search").value.toLowerCase();
    $$("#catalog-songs .catalog-song").forEach(song => { song.hidden = !song.dataset.catalogSearch.includes(query); });
  }
  async function sendPending() {
    if (stageOutbox?.pending) throw new Error("Resolve the pending staging request before changing a match.");
    if (!outbox?.pending) throw new Error("No pending decision.");
    const receipt = await request("/api/catalog/decide", outbox.pending);
    outbox.accept(receipt);
    status("Match decision acknowledged and saved. This does not import, analyze or export the file.");
    if (crate) await loadSongs();
  }
  async function sendStagePending() {
    if (!stageOutbox?.pending) throw new Error("No pending staging request.");
    const receipt = await request("/api/catalog/stage", stageOutbox.pending);
    stageOutbox.accept(receipt);
    status("Staging acknowledged. Inspect current staged status below; this receipt does not start analysis or undo later track removal.");
    await reload();
    if (crate) await loadSongs();
  }
  $("#catalog-upload").onchange = () => action(async () => {
    const file = $("#catalog-upload").files[0];
    if (!file) return;
    if (file.size > 64 * 1024 * 1024) throw new Error("Catalog snapshot exceeds 64 MB.");
    status("Validating and importing snapshot; malformed rows reject the entire import.");
    const receipt = await request("/api/catalog/import", JSON.parse(await file.text()));
    snapshot = receipt.snapshot_id;
    await refreshSnapshots();
    status(`${receipt.added ? "Imported" : "Already imported"} · ${receipt.crates} crates · ${receipt.songs} source songs · ${receipt.memberships} ordered memberships.`);
    $("#catalog-upload").value = "";
  });
  $("#catalog-snapshot").onchange = () => action(async () => {
    snapshot = $("#catalog-snapshot").value;
    await loadCrates();
  });
  $("#catalog-search").oninput = renderCrates;
  $("#catalog-song-search").oninput = filterSongs;
  $("#catalog-scan").onclick = () => action(() => scan($("#catalog-folder").value));
  $("#catalog-files-refresh").onclick = () => action(async () => { fileOffset = 0; await loadFiles(); });
  $("#catalog-files-more").onclick = () => action(async () => { fileOffset += 100; await loadFiles(); });
  $("#catalog-retry").onclick = () => action(sendPending);
  $("#catalog-scan-retry").onclick = () => action(sendScanPending);
  $("#catalog-scan-download").onclick = () => action(async () => {
    if (!scanOutbox?.pending) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(scanOutbox.pending, null, 2)], {type:"application/json"}));
    const link = document.createElement("a");
    link.href = url; link.download = "autocue-pending-scan.json"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  $("#catalog-scan-discard").onclick = () => action(async () => {
    if (!confirm("Discard only this browser's scan submission retry? An admitted job may still be running. Inspect scan jobs and cancel it separately if needed.")) return;
    scanOutbox.clear();
    status("Browser scan request discarded. This did not cancel a server job.");
    await refreshScanJobs();
  });
  $("#catalog-matches-refresh").onclick = () => action(async () => {
    if (crate) await loadSongs();
    else status("Choose a crate to refresh its match and cue status.");
  });
  $("#catalog-stage-retry").onclick = () => action(sendStagePending);
  $("#catalog-stage-download").onclick = () => action(async () => {
    if (!stageOutbox?.pending) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(stageOutbox.pending, null, 2)], {type:"application/json"}));
    const link = document.createElement("a");
    link.href = url; link.download = "autocue-pending-staging.json"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  $("#catalog-stage-discard").onclick = () => action(async () => {
    if (!confirm("Discard only this browser's staging retry request? This does not undo a committed copy. Download it first if needed.")) return;
    stageOutbox.clear();
    status("Pending staging request discarded. Refresh the crate to inspect committed state.");
    if (crate) await loadSongs();
  });
  $("#catalog-download").onclick = () => action(async () => {
    if (!outbox?.pending) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(outbox.pending, null, 2)], {type:"application/json"}));
    const link = document.createElement("a");
    link.href = url; link.download = "autocue-pending-match.json"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  $("#catalog-discard").onclick = () => action(async () => {
    if (!confirm("Discard only this browser's retry request? A decision already committed by the server is not undone. Download it first if needed.")) return;
    outbox.clear();
    status("Pending browser request discarded; inspect the latest saved decision before editing.");
    if (crate) await loadSongs();
  });
  action(async () => {
    await api("/api/session");
    outbox = new MatchOutbox(localStorage, datasetId);
    stageOutbox = new StageOutbox(localStorage, datasetId);
    scanOutbox = new ScanOutbox(localStorage, datasetId);
    await Promise.all([refreshSnapshots(), refreshRoots()]);
    pending();
    pollScans();
  });
})();
