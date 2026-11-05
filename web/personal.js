(() => {
  let outbox, selected = "", current = null, busy = false, exportPreview = null, exportsOffset = 0;
  async function refreshExports(offset = 0) {
    $("#personal-exports").innerHTML = "";
    $("#personal-exports-previous").disabled = true;
    $("#personal-exports-next").disabled = true;
    $("#personal-exports-status").textContent = "Checking saved bundle integrity; source audio is not read.";
    try {
      const result = await api("/api/catalog/personal/exports?offset=" + offset);
      exportsOffset = result.offset;
      $("#personal-exports").innerHTML = result.exports.map(item => {
        if (item.state !== "historical")
          return `<p class="catalog-error">${escapeHtml(item.filename)}: ${escapeHtml(item.error)}</p>`;
        const href = "/api/catalog/personal/exports/download?" + new URLSearchParams({
          dataset_id:datasetId, export_id:item.export_id});
        return `<article class="catalog-song"><h4>${escapeHtml(item.crate.name)} · saved revision ${item.crate.revision}</h4>
          <p class="muted">${item.entries} entries · ${escapeHtml(new Date(item.saved_at * 1000).toLocaleString())} · ${item.size_bytes} bytes · ${escapeHtml(item.export_id)}</p>
          <a class="button secondary" href="${escapeHtml(href)}" download="${escapeHtml(item.filename)}">Download historical ZIP</a>
          <p class="muted">Saved bundle integrity checked. Current source availability has NOT been verified.</p></article>`;
      }).join("") || '<p class="muted">No saved exports on this page.</p>';
      $("#personal-exports-status").textContent = `${result.total} saved bundles · showing ${result.exports.length} from offset ${result.offset}. Unavailable bundles cannot be downloaded.`;
      $("#personal-exports-previous").disabled = result.offset === 0;
      $("#personal-exports-next").disabled = result.offset + result.exports.length >= result.total;
    } catch (error) {
      $("#personal-exports-status").textContent = "Saved exports could not be loaded: " + error.message;
      throw error;
    }
  }
  function clearExport() {
    exportPreview = null;
    $("#personal-export-download").disabled = true;
    $("#personal-export-status").textContent = "";
    $("#personal-export-entries").innerHTML = "";
  }
  const status = (message, failed = false) => {
    $("#personal-status").textContent = message;
    $("#personal-status").classList.toggle("catalog-error", failed);
  };
  async function action(run) {
    if (busy) {
      status("A personal crate action is still in progress.", true);
      throw new Error("A personal crate action is still in progress.");
    }
    busy = true;
    $("#personal-section").setAttribute("aria-busy", "true");
    try { return await run(); }
    catch (error) { status(error.message, true); throw error; }
    finally {
      busy = false;
      $("#personal-section").setAttribute("aria-busy", "false");
      $("#personal-pending").hidden = !outbox?.pending;
    }
  }
  const reportError = error => status(error.message, true);
  const bind = (id, run) => { $(id).onclick = () => action(run).catch(reportError); };
  const refs = entries => entries.map(({id, snapshot_id, song_id}) => ({id, snapshot_id, song_id}));
  function requireCurrent() {
    if (!outbox) throw new Error("Personal crates are not ready; refresh the page.");
    if (!current) throw new Error("Choose or create a personal crate in Arrange your set first.");
    if (outbox.pending) throw new Error("Resolve the pending personal crate save before making another change.");
  }
  async function refresh() {
    clearExport();
    current = null;
    const data = await api("/api/catalog/personal");
    if (!data.crates.some(crate => crate.id === selected)) selected = data.crates[0]?.id || "";
    $("#personal-select").innerHTML = data.crates.map(crate =>
      `<option value="${escapeHtml(crate.id)}" ${crate.id === selected ? "selected" : ""}>${escapeHtml(crate.name)} · ${crate.entries} entries · r${crate.revision}</option>`
    ).join("") || '<option value="">No personal crates</option>';
    current = selected ? await api("/api/catalog/personal/detail?crate_id=" + encodeURIComponent(selected)) : null;
    $("#personal-title").textContent = current ? `${current.name} · revision ${current.revision}` : "Choose or create a personal crate";
    $("#personal-name").value = current?.name || "";
    $("#personal-entries").innerHTML = current?.entries.map((entry, index) =>
      `<article class="catalog-song"><h4>${index + 1}. ${escapeHtml(entry.song.artist)} — ${escapeHtml(entry.song.title)} ${escapeHtml(entry.song.version || "")}</h4>
      <p class="muted">Catalog ${escapeHtml(entry.snapshot_id.slice(0, 10))} / ${escapeHtml(entry.song_id)} · ${escapeHtml(entry.match_state)} · match revision ${entry.decision_revision}${entry.selected_file ? " · " + escapeHtml(entry.selected_file.path) : ""}</p>
      <div class="path-row"><button class="quiet" data-personal-up="${index}" ${index === 0 ? "disabled" : ""}>Move up</button><button class="quiet" data-personal-down="${index}" ${index === current.entries.length - 1 ? "disabled" : ""}>Move down</button><button class="quiet" data-personal-remove="${index}">Remove entry</button></div></article>`
    ).join("") || '<p class="muted">No entries. Browse a catalog crate and add songs to this selected personal crate.</p>';
    for (const [attribute, delta] of [["up", -1], ["down", 1], ["remove", 0]]) {
      $$(`[data-personal-${attribute}]`).forEach(button => {
        button.onclick = () => action(async () => {
          requireCurrent();
          const index = Number(button.dataset[{up:"personalUp", down:"personalDown", remove:"personalRemove"}[attribute]]);
          const entries = refs(current.entries);
          if (delta) [entries[index], entries[index + delta]] = [entries[index + delta], entries[index]];
          else entries.splice(index, 1);
          await save(current.id, current.revision, current.name, entries);
        }).catch(reportError);
      });
    }
  }
  async function sendPending() {
    if (!outbox?.pending) throw new Error("No pending personal crate save.");
    const request = outbox.pending;
    const receipt = await api("/api/catalog/personal/save", {method:"POST",
      headers:{"Content-Type":"application/json"}, body:JSON.stringify(request)});
    outbox.accept(receipt);
    selected = request.crate_id;
    status(`Personal crate save acknowledged at revision ${receipt.revision}. Refreshing current state; an old receipt never restores older order.`);
    await refresh();
  }
  async function save(crateId, revision, name, entries) {
    if (!outbox) throw new Error("Personal crates are not ready; refresh the page.");
    outbox.prepare({crate_id:crateId, base_revision:revision, name, entries});
    $("#personal-pending").hidden = false;
    await sendPending();
  }
  globalThis.PersonalCrates = {
    add: (snapshotId, songId) => action(async () => {
      requireCurrent();
      await save(current.id, current.revision, current.name,
        [...refs(current.entries), {id:crypto.randomUUID(), snapshot_id:snapshotId, song_id:songId}]);
    }),
  };
  bind("#personal-create", async () => {
    const name = $("#personal-new-name").value;
    if (!name.trim()) throw new Error("Enter a personal crate name.");
    await save(crypto.randomUUID(), 0, name, []);
    $("#personal-new-name").value = "";
  });
  bind("#personal-rename", async () => {
    requireCurrent();
    const name = $("#personal-name").value;
    if (!name.trim()) throw new Error("Enter a personal crate name.");
    await save(current.id, current.revision, name, refs(current.entries));
  });
  bind("#personal-refresh", refresh);
  bind("#personal-export-preview", async () => {
    requireCurrent();
    clearExport();
    $("#personal-export-status").textContent = "Reading and hashing reviewed source files; no audio is uploaded.";
    try {
      const request = {dataset_id:datasetId, crate_id:current.id, base_revision:current.revision};
      const result = await api("/api/catalog/personal/export/preview", {
        method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(request)});
      const manifest = result.manifest;
      exportPreview = {...request, preview_id:result.preview_id};
      status("Export preview completed. Review the byte-verification results below.");
      $("#personal-export-status").textContent = `${manifest.requested} requested · ${manifest.eligible} byte-verified · ${manifest.blocked} blocked. ${manifest.ready ? "Ready for reverified export." : "No playlist can be exported. " + manifest.blockers.join(" ")}`;
      $("#personal-export-entries").innerHTML = manifest.entries.map(entry =>
        `<p class="${entry.eligible ? "muted" : "catalog-error"}">${entry.position}. ${escapeHtml(entry.song.artist)} — ${escapeHtml(entry.song.title)} ${escapeHtml(entry.song.version || "")}: ${escapeHtml(entry.reason || "Reviewed bytes verified")} ${escapeHtml(entry.path || "")}</p>`
      ).join("");
      $("#personal-export-download").disabled = !manifest.ready;
    } catch (error) {
      clearExport();
      $("#personal-export-status").textContent = "Verification failed; no export is available.";
      throw error;
    }
  });
  bind("#personal-export-download", async () => {
    requireCurrent();
    if (!exportPreview) throw new Error("Verify and inspect an export preview first.");
    const request = exportPreview;
    clearExport();
    $("#personal-export-status").textContent = "Reverifying bytes and crate/match revisions before publication…";
    try {
      const result = await api("/api/catalog/personal/export/download", {
        method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(request)});
      if (result.preview_id !== request.preview_id || !result.manifest.ready)
        throw new Error("Export acknowledgment did not match the inspected preview.");
      const bytes = Uint8Array.from(atob(result.bundle_base64), character => character.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], {type:"application/zip"}));
      const link = document.createElement("a");
      link.href = url; link.download = result.filename; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      $("#personal-export-status").textContent = "Verified ZIP saved in this app and download requested. Extract it before opening playlist.m3u8. Source files can change afterward; preview again for a new export.";
    } catch (error) {
      $("#personal-export-status").textContent = "Export failed; preview again. No unverified download was requested.";
      throw error;
    }
    await refreshExports();
  });
  bind("#personal-exports-refresh", () => refreshExports());
  bind("#personal-exports-previous", () => refreshExports(Math.max(0, exportsOffset - 20)));
  bind("#personal-exports-next", () => refreshExports(exportsOffset + 20));
  $("#personal-select").onchange = () => action(async () => {
    selected = $("#personal-select").value;
    await refresh();
  }).catch(reportError);
  bind("#personal-retry", sendPending);
  bind("#personal-download", async () => {
    if (!outbox?.pending) throw new Error("No pending personal crate save.");
    const url = URL.createObjectURL(new Blob([JSON.stringify(outbox.pending, null, 2)], {type:"application/json"}));
    const link = document.createElement("a");
    link.href = url; link.download = "autocue-pending-personal-crate.json"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  bind("#personal-discard", async () => {
    if (!confirm("Discard only the browser's pending crate request? A committed change is not undone. Download it first if needed, then reconcile with current server state.")) return;
    outbox.clear();
    status("Browser retry request discarded; server crate changes were not undone.");
    await refresh();
  });
  action(async () => {
    await api("/api/session");
    outbox = new PersonalOutbox(localStorage, datasetId);
    await refresh();
    await refreshExports();
  }).catch(reportError);
})();
