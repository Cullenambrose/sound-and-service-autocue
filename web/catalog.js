(() => {
  let outbox, snapshot = "", crate = null, allCrates = [], busy = false, generation = 0, fileOffset = 0;
  const status = (message, failed = false) => {
    $("#catalog-status").textContent = message;
    $("#catalog-status").classList.toggle("catalog-error", failed);
  };
  const request = (url, body) => api(url, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
  function pending() {
    $("#catalog-pending").hidden = !outbox?.pending;
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
      `<div class="crate-item"><div><strong>${escapeHtml(root.path)}</strong><span class="crate-meta">${escapeHtml(root.state)}${root.availability_error || root.error ? " · " + escapeHtml(root.availability_error || root.error) : ""}</span></div><button class="quiet" data-catalog-rescan="${escapeHtml(root.path)}">Rescan</button></div>`
    ).join("") || '<p class="muted">No folders indexed.</p>';
    $$("[data-catalog-rescan]").forEach(button => button.onclick = () => action(() => scan(button.dataset.catalogRescan)));
  }
  async function loadFiles() {
    const data = await api("/api/catalog/files?offset=" + fileOffset);
    $("#catalog-files").innerHTML = data.files.map(file =>
      `<div class="crate-item"><div>${escapeHtml(file.path)}<span class="crate-meta">${escapeHtml(file.state)} · ${escapeHtml(file.metadata.artist || "Unknown artist")} / ${escapeHtml(file.metadata.title || "Unknown title")}${file.error ? " · " + escapeHtml(file.error) : ""}</span></div></div>`
    ).join("") || '<p class="muted">No indexed files.</p>';
    $("#catalog-files-more").hidden = fileOffset + data.files.length >= data.total;
  }
  async function scan(path) {
    status("Indexing the selected folder read-only. No audio is copied or analyzed.");
    const data = await request("/api/catalog/scan", {path});
    status(data.state === "offline" ? `Folder offline; index retained. ${data.error}` :
      `${data.scanned} audio paths indexed · ${data.reused} unchanged · ${data.errors.length} metadata/read errors · ${data.skipped_symlinks.length} symlinks skipped.`,
      data.state === "offline" || data.errors?.length > 0);
    await refreshRoots();
    fileOffset = 0;
    await loadFiles();
    if (crate) await loadSongs();
  }
  async function loadSongs() {
    const current = ++generation, selectedSnapshot = snapshot, selectedCrate = crate;
    const data = await api(`/api/catalog/songs?snapshot_id=${encodeURIComponent(snapshot)}&crate_id=${encodeURIComponent(crate.source_id)}`);
    if (current !== generation || selectedSnapshot !== snapshot || selectedCrate !== crate) return;
    $("#catalog-crate-title").textContent = `${crate.title} · ${crate.available ? data.songs.length + " memberships" : "Source unavailable"}`;
    $("#catalog-songs").innerHTML = data.songs.map((item, index) => {
      const song = item.song;
      return `<article class="catalog-song" data-catalog-search="${escapeHtml(`${song.artist} ${song.title} ${song.version || ""}`.toLowerCase())}">
        <h4>${index + 1}. ${escapeHtml(song.artist)} — ${escapeHtml(song.title)} ${escapeHtml(song.version || "")}</h4>
        <p class="muted">Source ID ${escapeHtml(song.id)} · ${escapeHtml(item.state)}${item.decision ? ` · reviewed revision ${item.revision} · ${escapeHtml(item.decision.note)}` : " · no reviewed file selection"} · BPM ${escapeHtml(song.bpm ?? "unknown")} · Key ${escapeHtml(song.key ?? "unknown")}</p>
        ${item.selected_file ? `<p class="muted">Reviewed file: ${escapeHtml(item.selected_file.path)} · ${escapeHtml(item.selected_file.state)} · SHA-256 ${escapeHtml(item.decision.content_hash)}</p>` : ""}
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
    $$("[data-catalog-missing]").forEach(button => button.onclick = () => choose(Number(button.dataset.catalogMissing), true));
    filterSongs();
  }
  function filterSongs() {
    const query = $("#catalog-song-search").value.toLowerCase();
    $$(".catalog-song").forEach(song => { song.hidden = !song.dataset.catalogSearch.includes(query); });
  }
  async function sendPending() {
    if (!outbox?.pending) throw new Error("No pending decision.");
    const receipt = await request("/api/catalog/decide", outbox.pending);
    outbox.accept(receipt);
    status("Match decision acknowledged and saved. This does not import, analyze or export the file.");
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
    await Promise.all([refreshSnapshots(), refreshRoots()]);
    pending();
  });
})();
