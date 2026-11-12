(function (root) {
  "use strict";
  const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({
    "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;",
  }[char]));
  function reviewRows(rows, items = []) {
    if (!rows.length) return '<p class="empty">All clear. No decisions waiting.</p>';
    const files = new Map(items.map(item => [item.track.id, item]));
    return rows.map(row => {
      const selected = row.fulfilled ? files.get(row.selected_file_id) : null;
      const state = row.fulfilled ? "matched" : row.decision === "dismiss" ? "dismissed" : row.status;
      return `<article class="review-row ${escape(state)}">
      <div class="row-head"><div><span class="request-title">${escape(row.request.title)}</span>
      <span class="request-sub">${escape(row.request.artist || "Artist not specified")} ${row.request.version ? " / " + escape(row.request.version) : ""}</span>
      ${row.request.metadata?.first_name ? `<span class="request-sub">From ${escape(row.request.metadata.first_name)}</span>` : ""}</div>
      <span class="badge ${row.fulfilled ? "success" : state === "blocked" ? "blocked" : state === "dismissed" ? "neutral" : "review"}">${escape(state.replaceAll("_", " "))}</span></div>
      ${row.note ? `<p class="review-note">${escape(row.note)}</p>` : ""}
      ${selected ? `<div class="resolved-file"><strong>${escape(selected.track.title)}</strong>
        <span class="request-sub">${escape(selected.track.artist)} / ${escape(selected.version_label)}</span>
        <span class="file-path">${escape(selected.track.path)}</span></div>` : ""}
      ${row.selected_file_id ? `<span class="request-sub">Selected file: ${escape(row.selected_file_id)}</span>` : ""}
      ${row.needs_review ? `<div class="candidates">${row.candidates.map(track => `<div class="candidate">
        <strong>${escape(track.title)}</strong><span class="request-sub">${escape(track.artist)} / ${escape(track.version_label)}</span>
        <span class="file-path">${escape(track.path)}</span>
        ${row.can_confirm ? `<button class="confirm" data-action="confirm" data-key="${escape(row.key)}" data-file-id="${escape(track.id)}"
          aria-label="${escape(row.status === "version_unavailable" ? `Use ${track.version_label} instead for ${row.request.title}` : `Confirm ${track.title} by ${track.artist}, ${track.version_label}`)}">${escape(row.status === "version_unavailable" ? `Use ${track.version_label} instead` : "Confirm")}</button>` : ""}</div>`).join("")}</div>
        <button class="dismiss" data-action="dismiss" data-key="${escape(row.key)}" aria-label="Dismiss request: ${escape(row.request.title)}">Dismiss request</button>` : ""}</article>`;
    }).join("");
  }
  function prepProgress(view) {
    if (view.prep_done) return "Saved · Exported · Verified: 0 mismatches";
    if (view.verification.mismatches > 0) return `Verification failed: ${view.verification.mismatches} mismatches`;
    if (view.needs_review.length) return `${view.needs_review.length} ${view.needs_review.length === 1 ? "decision" : "decisions"} before saving`;
    if (view.out_of_pool?.length) return `${view.out_of_pool.length} matched requests outside source · Change build mode`;
    if ((view.prep_mode || "seed") === "seed" && view.seed_rows?.some(row => row.status !== "available")) return "Resolve imported entries before saving";
    if (view.saved && view.saved_current === false) return "Working set changed · Save again";
    return view.saved ? "Crate saved · Export to finish" : "Ready to save";
  }
  function trackRows(items, live = false) {
    return items.map((item, index) => `<tr><td>${String(index + 1).padStart(2, "0")}</td>
      <td><span class="track-title">${escape(item.track.title)}</span><span class="request-sub">${escape(item.track.artist)}</span></td>
      <td>${escape(item.version_label)} <span class="request-sub">${escape(item.track.id)}</span><span class="file-path">${escape(item.track.path)}</span>
      <details><summary>Mix metadata</summary><span class="request-sub">BPM: ${escape(item.track.bpm ?? "Unknown")} / Key: ${escape(item.track.key || "Unknown")} / Energy: ${escape(item.track.energy ?? "Unknown")}</span></details></td>
      <td>${item.reasons.map(reason => `<span class="badge">${escape(reason)}</span>`).join("")}</td>
      ${live ? `<td><button class="secondary" data-action="played" data-file-id="${escape(item.track.id)}">Mark played</button></td>` : ""}</tr>`).join("");
  }
  function seedRows(rows, editable = false) {
    return rows.map((row, index) => `<li class="review-row">
      <div class="row-head"><strong>${index + 1}. ${escape(row.title || "Metadata unavailable")}</strong>
      <span class="badge ${row.status === "available" ? "success" : "review"}">${escape(row.status.replaceAll("_", " "))}</span></div>
      <span class="request-sub">${escape(row.artist || "Unknown artist")}</span><span class="file-path">${escape(row.path)}</span>
      ${row.replacement ? `<p class="review-note">Reconnected by DJ. Original reference retained above.</p><strong>${escape(row.replacement.title)}</strong><span class="file-path">${escape(row.replacement.path)}</span>` : ""}
      ${editable && row.status === "missing_library_file" ? `<p class="muted">${escape(row.reconnect_status === "matched" && !row.reconnect_candidates?.length
        ? "Matching file is excluded or another event version has priority."
        : row.reconnect_status === "ambiguous" ? "Imported title is ambiguous. Remove this membership or review its source; no replacement guessed."
        : row.reconnect_status === "missing_metadata" ? "Source metadata is incomplete; no replacement guessed."
        : "Original file unavailable. Acquisition never reconnects it automatically.")}</p>
        ${(row.reconnect_candidates || []).map(track => `<div class="candidate"><strong>${escape(track.title)}</strong>
          <span class="request-sub">${escape(track.artist)} / ${escape(track.version_label)}</span><span class="file-path">${escape(track.path)}</span>
          <button class="confirm" data-action="seed_reconnect" data-entry-id="${escape(row.id)}" data-file-id="${escape(track.id)}"
            aria-label="Reconnect file for entry ${index + 1}: ${escape(track.title)}">Reconnect file</button></div>`).join("")}` : ""}
      ${editable ? `<div class="seed-actions">
        ${index ? `<button class="secondary" data-action="seed_move" data-entry-id="${escape(row.id)}" data-direction="up" aria-label="Move up: entry ${index + 1}">Move up</button>` : ""}
        ${index < rows.length - 1 ? `<button class="secondary" data-action="seed_move" data-entry-id="${escape(row.id)}" data-direction="down" aria-label="Move down: entry ${index + 1}">Move down</button>` : ""}
        <button class="dismiss" data-action="seed_remove" data-entry-id="${escape(row.id)}" aria-label="Remove from set: entry ${index + 1}">Remove from set</button>
      </div><form class="membership-plan" data-plan-entry="${escape(row.id)}">
        <label for="phase-${escape(row.id)}">Phase for entry ${index + 1}</label>
        <select id="phase-${escape(row.id)}" name="phase">${["unassigned","ceremony","dinner","first_dance","dancing"].map(phase =>
          `<option value="${phase}" ${phase === (row.planning?.phase || "unassigned") ? "selected" : ""}>${phase.replaceAll("_"," ")}</option>`).join("")}</select>
        <label><input type="checkbox" name="pinned" ${row.planning?.pinned ? "checked" : ""}> Pin position for entry ${index + 1}</label>
        <button type="submit" class="secondary">Save phase / pin</button>
      </form>` : ""}</li>`).join("");
  }
  function planningRows(planning) {
    return Object.entries(planning).map(([phase,row]) => `<p><strong>${escape(phase.replaceAll("_"," "))}</strong>:
      ${row.entries} entries · ${(row.known_seconds / 60).toFixed(1)} known min · ${row.unknown} unknown durations${row.target_minutes == null ? "" : ` · target ${row.target_minutes} min${row.unknown ? " (total unknown)" : ` · ${(row.known_seconds / 60 - row.target_minutes).toFixed(1)} min vs target`}`}</p>`).join("");
  }
  function eligibleRows(items, query = "", source = null) {
    const filter = query.trim().toLocaleLowerCase();
    const visible = items.filter(item => [item.track.title, item.track.artist, item.version_label]
      .some(value => String(value ?? "").toLocaleLowerCase().includes(filter)));
    return {count:visible.length, html:visible.map(item => {
      const added = source?.order.includes("added:" + item.track.id);
      const unavailable = !source || added;
      return `<article class="eligible-track"><strong>${escape(item.track.title)}</strong>
        <span class="request-sub">${escape(item.track.artist)} / ${escape(item.version_label)}</span>
        ${item.reasons.map(reason => `<span class="badge">${escape(reason)}</span>`).join("")}
        <details><summary>Exact file</summary><span class="file-path">${escape(item.track.path)}</span>
        <span class="request-sub">${escape(item.track.id)}</span></details>
        <button class="secondary" data-action="seed_add" data-file-id="${escape(item.track.id)}"
          ${unavailable ? 'disabled data-unavailable="true"' : ""}
          aria-label="${escape(added ? `Added to set: ${item.track.title}` : `Add to set: ${item.track.title}, ${item.version_label}`)}">${added ? "Added to set" : "Add to set"}</button></article>`;
    }).join("") || '<p class="empty">No eligible tracks contain that text. This filter does not resolve requests or search record pools.</p>'};
  }
  function providerRows(providers, query = "") {
    const filter = query.trim().toLocaleLowerCase();
    const visible = providers.filter(provider => provider.name.toLocaleLowerCase().includes(filter));
    return {count:visible.length, html:visible.map(provider => {
      const search = provider.link_mode === "search" || provider.link_mode === "search_login";
      const label = provider.link_mode === "search_login" ? "Search link / sign-in required"
        : search ? "Direct search" : "Copy text, then search";
      return `<div><a href="${escape(provider.url)}" target="_blank" rel="noopener noreferrer">${search ? "Search" : "Open"} ${escape(provider.name)} <span class="request-sub">${escape(provider.kind)} / ${label}</span></a><p class="muted">${escape(provider.note)}</p>${search ? `<a href="${escape(provider.homepage_url)}" target="_blank" rel="noopener noreferrer">Open ${escape(provider.name)} homepage instead</a>` : ""}</div>`;
    }).join("") || '<p class="empty">No providers contain that name.</p>'};
  }
  function acquisitionRows(rows) {
    const labels = {not_started:"Not started", searching:"Searching", acquired:"Acquired externally; not verified"};
    return rows.map(row => `<article class="review-row">
      <h3>${escape(row.title)}</h3><span class="request-sub">${escape(row.artist || "Artist not specified")} ${escape(row.version || "")}</span>
      ${row.source_label ? `<span class="request-sub">${escape(row.source_label)}</span>` : ""}
      <p class="review-note">${escape(labels[row.progress])}</p>
      <label for="lookup-${escape(row.key)}">Search text (select and copy)</label>
      <input id="lookup-${escape(row.key)}" readonly value="${escape(row.query)}">
      <button class="secondary" data-copy-query="${escape(row.key)}">Copy search text</button>
      <details><summary>Choose a provider (${row.providers.length})</summary>
      <label for="provider-filter-${escape(row.key)}">Filter providers by name</label>
      <input id="provider-filter-${escape(row.key)}" type="search" data-provider-filter="${escape(row.key)}" aria-controls="provider-links-${escape(row.key)}" maxlength="100">
      <p class="muted" role="status" data-provider-count="${escape(row.key)}">${row.providers.length} providers. Search links and copy/open fallbacks are labeled below.</p>
      <div id="provider-links-${escape(row.key)}" class="provider-destinations">${providerRows(row.providers).html}</div></details>
      <p class="muted">Only a link click sends the search text to that provider. No account connection or availability check. Verify the exact version there.</p>
      <div class="seed-actions">${Object.entries(labels).map(([progress, label]) => `<button class="secondary"
        data-action="acquisition_progress" data-key="${escape(row.key)}" data-progress="${progress}"
        aria-pressed="${row.progress === progress}">${escape(label)}</button>`).join("")}</div>
      <p class="muted">Still unresolved. Acquisition progress is not a playable file or fulfillment.</p>
    </article>`).join("") || '<p class="empty">No missing-song lookups waiting.</p>';
  }
  async function copySearchText(text, clipboard) {
    if (!clipboard?.writeText) throw new Error("Clipboard unavailable. Select and copy the search text manually.");
    await clipboard.writeText(text);
  }
  class FlowClient {
    constructor(fetcher) {
      this.fetcher = fetcher;
      this.token = null;
      this.session = null;
      this.views = {};
      this.pending = null;
    }
    async request(path, options = {}) {
      const prefix = root.location?.pathname.startsWith("/setlist/") ? "/setlist" : "";
      const response = await this.fetcher(prefix + path, options);
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
      return body;
    }
    async load(mode) {
      if (!this.token) {
        this.session = await this.request("/api/session");
        this.token = this.session.token;
      }
      this.views[mode] = await this.request(`/api/${mode}`);
      return this.views[mode];
    }
    async command(mode, fields) {
      if (this.pending) throw new Error("Resolve the pending action before making another change.");
      this.pending = {mode, ...fields, base_revision:this.views[mode].revision,
        request_id:root.crypto.randomUUID()};
      return this.retry();
    }
    async retry() {
      if (!this.pending) throw new Error("No action is waiting for acknowledgment.");
      const mode = this.pending.mode;
      const result = await this.request("/api/command", {
        method:"POST", headers:{"Content-Type":"application/json", "X-Setlist-Token":this.token},
        body:JSON.stringify(this.pending),
      });
      this.pending = null;
      this.views[mode] = result;
      return result;
    }
  }
  const exports = {escape, reviewRows, trackRows, seedRows, eligibleRows, providerRows, acquisitionRows, copySearchText, planningRows, prepProgress, FlowClient};
  if (typeof module !== "undefined" && module.exports) module.exports = exports;
  if (!root.document) return;
  const $ = selector => root.document.querySelector(selector);
  let acquisitionPreview = null;
  let portablePreview = null, portableGeneration = 0, sequencePreview = null;
  function invalidatePortable() {
    portableGeneration++;
    portablePreview = null;
    $("#portable-preview").hidden = true;
  }
  let acquisitionGeneration = 0;
  function invalidateAcquisition() {
    acquisitionGeneration++;
    acquisitionPreview = null;
    $("#acquisition-preview").hidden = true;
  }
  function acquisitionAccepted() {
    $("#acquisition-form").reset();
    invalidateAcquisition();
  }
  const client = new FlowClient(root.fetch.bind(root));
  let mode = "prep", busy = false;
  let importPreview = null;
  let importGeneration = 0;
  function invalidatePreview() {
    importGeneration += 1;
    importPreview = null;
    $("#serato-preview").hidden = true;
  }
  function blockedPrep(view) {
    return !!view.needs_review.length || !!view.out_of_pool?.length
      || (view.prep_mode || "seed") === "seed" && view.seed_rows?.some(row => row.status !== "available");
  }
  function guestAccepted() {
    $("#guest-form").reset();
    $("#guest-entry").open = false;
  }
  function paintEligible(view) {
    const result = eligibleRows(view.recommendations, $("#eligible-search").value,
      view.prep_mode === "seed" ? view.serato_source : null);
    $("#eligible-tracks").innerHTML = result.html;
    $("#eligible-count").textContent = `${result.count} of ${view.recommendations.length} eligible tracks`;
    if (busy || client.pending) $("#eligible-tracks").querySelectorAll("button")
      .forEach(button => {button.disabled = true;});
  }
  let pilotConfigured = false;
  function configurePilot() {
    if (!client.session?.read_only_pilot || pilotConfigured) return;
    pilotConfigured = true;
    root.document.title = "Booth Buddy · Read-only Prep pilot";
    $(".demo-label").textContent = "READ-ONLY PILOT / SELECTED FILES ONLY";
    $("#workspace-scope").textContent = "Explicitly selected files · Read-only tags and availability · No library scan";
    $("#serato-help").textContent = "Select a static .crate and matching database snapshot, at most 32 MiB each. Only verified files in the launch selection can be available. Unselected paths are not opened. Source bytes stay untouched.";
    $("#serato-sample").hidden = true;
    $("#portable-import").hidden = true;
    $("#prep-mode option[value='library']").textContent = "Use selected verified library";
    $("#acquisition-title").textContent = "Verify a selected acquired file";
    $("#acquisition-help").textContent = "Only paths explicitly permitted at launch are available here. Preview reads file tags and hashes without modifying the file. The shared engine checks song and requested version; availability is checked again on acceptance. Licensing and audio quality are not certified.";
    $("#acquisition-file").hidden = true;
    $("#acquisition-file").required = false;
    $("#acquisition-file-label").hidden = true;
    $("#selected-acquisition").hidden = false;
    $("#acquisition-path").required = true;
    $("#acquisition-path").replaceChildren(...client.session.acquisition_paths.map(path => {
      const option = root.document.createElement("option");
      option.value = path;
      option.textContent = path;
      return option;
    }));
    $("#acquisition-sample").hidden = true;
    $("#acquisition-accept").textContent = "Accept verified file";
    $(".boundary-note").textContent = "Read-only pilot. No music, tag or Serato writes. Saved Prep stays separate.";
  }
  function paint(view) {
    configurePilot();
    const libraryLabel = client.session?.read_only_pilot ? "selected verified" : "synthetic";
    $("#event-name").textContent = view.event_name;
    if (view.mode === "prep") {
      $("#prep-review").innerHTML = reviewRows(view.needs_review);
      $("#prep-tracks").innerHTML = trackRows(view.crate_entries || view.recommendations);
      $("#prep-order-note").textContent = view.prep_mode === "source"
        ? "Shared-engine order within the imported source. Outside-source requirements stay separate."
        : view.prep_mode === "library"
        ? `Shared-engine order across the ${libraryLabel} library. Imported order is retained separately.`
        : view.serato_source
        ? "Your working set order, with matched event requests and cues appended. Every row keeps its exact file."
        : "Ordered by the shared setlist engine. Every row keeps its original file.";
      $("#review-count").textContent = view.needs_review.length;
      $("#decision-total").textContent = view.needs_review.length;
      $("#crate-count").textContent = (view.crate_entries || view.recommendations).length;
      $("#prep-progress").textContent = prepProgress(view);
      $("#prep-progress").classList.toggle("complete", view.prep_done);
      $("#prep-progress").classList.toggle("failed", view.verification.mismatches > 0);
      $("#prep-mode").value = view.prep_mode;
      $("#scope-note").textContent = view.prep_mode === "source"
        ? "Only eligible exact files from the imported source. Outside-source event requests stay visible below."
        : view.prep_mode === "library" ? `Full ${libraryLabel} library in shared-engine order; imported order is retained separately.`
        : "Imported membership order, followed by matched event requests. Missing source entries must be resolved.";
      $("#out-of-pool").innerHTML = (view.out_of_pool || []).map(row =>
        `<p>${escape(row.request.title)}: matched in library, outside selected source. Change build mode to include it.</p>`).join("");
      $("#phase-totals").innerHTML = planningRows(view.planning);
      $("#library-counts").textContent = `${view.library_overview.tracks} ${libraryLabel} records / ${view.library_overview.song_identities} songs / ${view.library_overview.multiple_file_songs} songs with multiple files.`;
      $("#duplicate-report").innerHTML = view.library_overview.groups.map(group => `<details><summary>${escape(group[0].title)} (${group.length} files)</summary>${group.map(track => `<p>${escape(track.title)} / ${escape(track.artist)}<span class="file-path">${escape(track.path)}</span></p>`).join("")}</details>`).join("") || '<p class="empty">No multiple-file song groups.</p>';
      $("#planning-panel").hidden = !view.serato_source || view.prep_mode !== "seed";
      if (sequencePreview && sequencePreview.revision !== view.revision) {
        sequencePreview = null;
        $("#sequence-result").hidden = true;
      }
      $("#verify").textContent = view.verification.note;
      $("#save-prep").disabled = blockedPrep(view);
      $("#export-prep").disabled = !view.saved;
      $("#download").hidden = view.verification.mismatches !== 0;
      $("#seed-workspace").hidden = !view.serato_source || view.prep_mode !== "seed";
      $("#edit-seed").hidden = !view.serato_source || view.prep_mode !== "seed";
      $("#eligible-help").textContent = view.prep_mode !== "seed"
        ? "Shared-engine recommendations in the selected scope. Switch to playlist-order mode to edit memberships."
        : view.serato_source
        ? "Shared-engine recommendations, not your whole library. Add an exact file to your working set."
        : "Shared-engine recommendations, not your whole library. Import a starting set to add files.";
      $("#seed-memberships").innerHTML = seedRows(view.seed_rows || [], true);
      $("#seed-source").textContent = view.serato_source
        ? `${view.serato_source.crate_name} · ${view.seed_rows.length} working memberships · original order retained until you edit` : "";
      $("#seed-add-file").replaceChildren(...view.recommendations.map(item => {
        const option = root.document.createElement("option");
        option.value = item.track.id;
        option.textContent = `${item.track.title} — ${item.track.artist} / ${item.version_label}`;
        return option;
      }));
      paintEligible(view);
      $("#acquisition-count").textContent = `(${view.acquisition_queue.length})`;
      $("#acquisition-queue").innerHTML = acquisitionRows(view.acquisition_queue);
      $("#acquisition-intake").hidden = !view.acquisition_queue.length;
      const selectedRequest = $("#acquisition-request").value;
      $("#acquisition-request").replaceChildren(...view.acquisition_queue.map(row => {
        const option = root.document.createElement("option");
        option.value = row.key;
        option.textContent = `${row.source_label ? row.source_label + ": " : ""}${row.title} / ${row.artist || "Artist not specified"} / ${row.version || "Version as requested"}`;
        return option;
      }));
      if (view.acquisition_queue.some(row => row.key === selectedRequest)) $("#acquisition-request").value = selectedRequest;
      if (acquisitionPreview && acquisitionPreview.base_revision !== view.revision) invalidateAcquisition();
    } else {
      const guests = view.requests.filter(row => row.category === "guest_request");
      $("#live-requests").innerHTML = reviewRows(guests, view.recommendations);
      $("#guest-count").textContent = guests.length;
      $("#live-tracks").innerHTML = trackRows(view.recommendations, true);
    }
  }
  async function run(operation) {
    if (busy) return;
    busy = true;
    const actedOnReview = root.document.activeElement?.matches("[data-key], [data-entry-id]");
    const returnFocus = root.document.activeElement;
    $("main").setAttribute("aria-busy", "true");
    $("#error").hidden = true;
    root.document.querySelectorAll("button").forEach(button => {button.disabled = true;});
    try {
      paint(await operation());
      $("#status").textContent = mode === "prep" ? prepProgress(client.views.prep) : "Requests checked. Saved Prep is unchanged.";
    } catch (error) {
      $("#error").textContent = error.message + (client.pending ? " Retry the pending action or reload to reconcile." : "");
      $("#error").hidden = false;
    } finally {
      busy = false;
      $("main").setAttribute("aria-busy", "false");
      root.document.querySelectorAll("button").forEach(button => {button.disabled = false;});
      if (mode === "prep" && client.views.prep) {
        $("#save-prep").disabled = blockedPrep(client.views.prep);
        $("#export-prep").disabled = !client.views.prep.saved;
        $("#seed-add").disabled = !client.views.prep.recommendations.length;
        $("#acquisition-sample").disabled = !client.views.prep.acquisition_queue.length;
      }
      root.document.querySelectorAll("[data-unavailable]").forEach(button => {button.disabled = true;});
      if (client.pending) {
        root.document.querySelectorAll("button").forEach(button => {button.disabled = true;});
        $("#retry-pending").disabled = false;
        $("#reconcile").disabled = false;
        $("#status").textContent = "Unacknowledged action retained. Retry the identical action or reload to reconcile.";
      }
      $("#recovery").hidden = !client.pending;
      if (!$("#error").hidden) $("#error").focus();
      else if (returnFocus?.id === "serato-accept") {
        $("#seed-workspace").open = true;
        $("#seed-memberships").focus();
      }
      else if (returnFocus?.dataset.action === "seed_add") $("#eligible-search").focus();
      else if (returnFocus?.id === "portable-accept") $("#seed-memberships").focus();
      else if (returnFocus?.id === "sequence-accept") $("#sequence-preview").focus();
      else if (returnFocus?.id === "acquisition-accept" || returnFocus?.dataset.action === "acquisition_progress") {
        $("#prep-decisions").focus();
      }
      else if (actedOnReview) {
        const panel = $(returnFocus?.dataset.entryId ? "#seed-memberships" : mode === "prep" ? "#prep-review" : "#live-requests");
        (panel.querySelector("button") || panel).focus();
      } else if (returnFocus?.isConnected && !returnFocus.disabled) {
        const closedDisclosure = returnFocus.closest("details:not([open])");
        if (closedDisclosure) closedDisclosure.querySelector("summary").focus();
        else if (returnFocus.getClientRects().length) returnFocus.focus();
        else if (mode === "live") $("#guest-entry summary").focus();
      }
    }
  }
  function switchMode(next) {
    if (client.pending) {
      $("#error").textContent = "Resolve the pending action before switching screens.";
      $("#error").hidden = false;
      return;
    }
    mode = next;
    $("#prep-screen").hidden = mode !== "prep";
    $("#live-screen").hidden = mode !== "live";
    $("#prep-tab").setAttribute("aria-current", mode === "prep" ? "page" : "false");
    $("#live-tab").setAttribute("aria-current", mode === "live" ? "page" : "false");
    return run(() => client.load(mode));
  }
  $("#prep-tab").addEventListener("click", () => switchMode("prep"));
  $("#edit-seed").addEventListener("click", () => {
    $("#seed-workspace").open = true;
    $("#seed-memberships").focus();
  });
  $("#eligible-search").addEventListener("input", () => {
    if (client.views.prep) paintEligible(client.views.prep);
  });
  $("#live-tab").addEventListener("click", () => switchMode("live"));
  $("#save-prep").addEventListener("click", () => run(() => client.command("prep", {action:"save"})));
  $("#export-prep").addEventListener("click", () => run(() => client.command("prep", {action:"export"})));
  $("#retry-pending").addEventListener("click", () => run(async () => {
    const guest = client.pending?.action === "guest";
    const acquisition = client.pending?.action === "acquisition_accept";
    const view = await client.retry();
    if (guest) guestAccepted();
    if (acquisition) acquisitionAccepted();
    return view;
  }));
  $("#reconcile").addEventListener("click", () => {
    client.pending = null;
    run(() => client.load(mode));
  });
  $("#guest-form").addEventListener("submit", event => {
    event.preventDefault();
    run(async () => {
      const view = await client.command("live", {action:"guest", first_name:$("#guest-name").value,
        email:$("#guest-email").value, song:$("#guest-song").value});
      guestAccepted();
      return view;
    });
  });
  async function previewSerato(upload, generation) {
    const preview = await client.request("/api/serato/preview", {
      method:"POST", headers:{"Content-Type":"application/json", "X-Setlist-Token":client.token},
      body:JSON.stringify(upload),
    });
    if (generation !== importGeneration) throw new Error("Selected snapshots changed. Preview them again.");
    importPreview = {upload, preview};
    $("#serato-preview").hidden = false;
    $("#serato-preview-summary").textContent = `${preview.crate_name}: ${preview.entries.length} memberships. ${preview.note}`;
    $("#serato-preview-rows").innerHTML = seedRows(preview.entries);
    return client.views.prep;
  }
  function readSnapshot(input) {
    const file = input.files[0];
    const limit = client.session?.max_snapshot_bytes || 512 * 1024;
    if (!file || !file.size || file.size > limit) return Promise.reject(new Error(`Select a nonempty snapshot no larger than ${limit / 1024} KiB.`));
    return new Promise((resolve, reject) => {
      const reader = new root.FileReader();
      reader.onload = () => resolve(reader.result.split(",")[1]);
      reader.onerror = () => reject(new Error("Could not read the selected snapshot."));
      reader.readAsDataURL(file);
    });
  }
  $("#serato-form").addEventListener("submit", event => {
    event.preventDefault();
    if (busy) return;
    invalidatePreview();
    const generation = importGeneration;
    run(async () => previewSerato({
      crate_name:$("#serato-crate").files[0]?.name,
      crate_base64:await readSnapshot($("#serato-crate")),
      database_base64:await readSnapshot($("#serato-database")),
      volume_root:$("#serato-volume").value,
    }, generation));
  });
  $("#serato-form").addEventListener("input", invalidatePreview);
  $("#serato-form").addEventListener("change", invalidatePreview);
  $("#serato-sample").addEventListener("click", () => {
    if (busy) return;
    invalidatePreview();
    const generation = importGeneration;
    run(async () => previewSerato(await client.request("/api/serato/demo"), generation));
  });
  $("#serato-accept").addEventListener("click", () => run(async () => {
    if (!importPreview) throw new Error("Preview the selected snapshots first.");
    const view = await client.command("prep", {action:"serato_import",
      upload:importPreview.upload, preview_id:importPreview.preview.id});
    invalidatePreview();
    $("#seed-memberships").focus();
    return view;
  }));
  $("#seed-add").addEventListener("click", () => run(() => client.command("prep",
    {action:"seed_add", file_id:$("#seed-add-file").value})));
  $("#apply-mode").addEventListener("click", () => run(() => client.command("prep",
    {action:"prep_mode", value:$("#prep-mode").value})));
  $("#target-form").addEventListener("submit", event => {
    event.preventDefault();
    run(() => client.command("prep", {action:"phase_target",phase:$("#target-phase").value,
      minutes:Number($("#target-minutes").value)}));
  });
  root.document.addEventListener("submit", event => {
    const form = event.target.closest("[data-plan-entry]");
    if (!form) return;
    event.preventDefault();
    run(() => client.command("prep", {action:"seed_plan",entry_id:form.dataset.planEntry,
      phase:form.elements.phase.value,pinned:form.elements.pinned.checked}));
  });
  $("#sequence-preview").addEventListener("click", () => run(async () => {
    const preview = await client.request("/api/sequence/preview", {
      method:"POST", headers:{"Content-Type":"application/json","X-Setlist-Token":client.token},
      body:JSON.stringify({base_revision:client.views.prep.revision})});
    sequencePreview = {...preview, revision:client.views.prep.revision};
    $("#sequence-note").textContent = preview.note;
    $("#sequence-rows").innerHTML = preview.rows.map(row => `<li>${escape(row.title)} · ${escape(row.planning.phase.replaceAll("_"," "))}${row.planning.pinned ? " · pinned" : ""}</li>`).join("");
    $("#sequence-result").hidden = false;
    $("#sequence-result").focus();
    return client.views.prep;
  }));
  $("#sequence-accept").addEventListener("click", () => run(() => {
    if (!sequencePreview || sequencePreview.revision !== client.views.prep.revision) throw new Error("Preview the current sequence first.");
    return client.command("prep",{action:"sequence_accept",preview_id:sequencePreview.id});
  }));
  $("#portable-form").addEventListener("input",invalidatePortable);
  $("#portable-form").addEventListener("change",invalidatePortable);
  $("#portable-form").addEventListener("submit", event => {
    event.preventDefault();
    if (busy) return;
    invalidatePortable();
    const generation = portableGeneration;
    run(async () => {
      const file = $("#portable-file").files[0];
      if (!file || !file.size || file.size > 512 * 1024) throw new Error("Select synthetic text no larger than 512 KiB.");
      const upload = {format:$("#portable-format").value,name:$("#portable-name").value,
        crate_id:$("#portable-crate").value,text:await file.text()};
      return previewPortable(upload,generation);
    });
  });
  $("#portable-accept").addEventListener("click", () => run(async () => {
    if (!portablePreview) throw new Error("Preview the selected portable input first.");
    const view = await client.command("prep",{action:"portable_import",...portablePreview});
    invalidatePortable();
    $("#seed-workspace").open = true;
    return view;
  }));
  async function previewPortable(upload,generation) {
    const preview = await client.request("/api/portable/preview", {
      method:"POST",headers:{"Content-Type":"application/json","X-Setlist-Token":client.token},
      body:JSON.stringify(upload)});
    if (generation !== portableGeneration) throw new Error("Selected portable input changed. Preview again.");
    portablePreview = {upload,preview_id:preview.id};
    $("#portable-note").textContent = `${preview.entries.length} memberships. ${preview.note}`;
    $("#portable-rows").innerHTML = seedRows(preview.entries);
    $("#portable-preview").hidden = false;
    return client.views.prep;
  }
  async function previewAcquisition(track, generation) {
    const body = {key:$("#acquisition-request").value, track, base_revision:client.views.prep.revision};
    const preview = await client.request("/api/acquisition/preview", {
      method:"POST", headers:{"Content-Type":"application/json", "X-Setlist-Token":client.token},
      body:JSON.stringify(body),
    });
    if (generation !== acquisitionGeneration) throw new Error("Selected metadata changed. Preview it again.");
    acquisitionPreview = {...body, preview_id:preview.id};
    $("#acquisition-preview-note").textContent = `${preview.status.replaceAll("_", " ")}. ${preview.note} ${preview.selected_file_id ? "Selected file: " + preview.selected_file_id + "." : "Nothing fulfilled."} ${preview.note_scope}`;
    $("#acquisition-preview-track").textContent = JSON.stringify(preview.track, null, 2);
    $("#acquisition-preview-changes").innerHTML = preview.changes.map(change =>
      `<li>${escape(change.title)}: ${escape(change.before.replaceAll("_", " "))} &rarr; ${escape(change.after.replaceAll("_", " "))}${change.selected_file_id ? ` / ${escape(change.selected_file_id)}` : ""}</li>`
    ).join("") || "<li>No request resolution changed.</li>";
    $("#acquisition-preview").hidden = false;
    $("#acquisition-preview").focus();
    return client.views.prep;
  }
  $("#acquisition-form").addEventListener("input", invalidateAcquisition);
  $("#acquisition-form").addEventListener("change", invalidateAcquisition);
  $("#acquisition-form").addEventListener("submit", event => {
    event.preventDefault();
    if (busy) return;
    invalidateAcquisition();
    const generation = acquisitionGeneration;
    run(async () => {
      if (client.session?.read_only_pilot) {
        return previewAcquisition({path:$("#acquisition-path").value}, generation);
      }
      const file = $("#acquisition-file").files[0];
      if (!file || !file.size || file.size > 8192) throw new Error("Select a nonempty synthetic JSON file no larger than 8 KiB.");
      return previewAcquisition(JSON.parse(await file.text()), generation);
    });
  });
  $("#acquisition-sample").addEventListener("click", () => {
    if (busy) return;
    invalidateAcquisition();
    const generation = acquisitionGeneration;
    run(() => {
      const row = client.views.prep.acquisition_queue.find(row => row.key === $("#acquisition-request").value);
      if (!row) throw new Error("Choose a pending acquisition request.");
      return previewAcquisition({id:`acquired-${row.key}`, title:row.title,
        artist:row.artist || "Synthetic Artist", version:row.version || "",
        path:`/Demo/Acquired/${row.key}.mp3`}, generation);
    });
  });
  $("#acquisition-accept").addEventListener("click", () => run(async () => {
    if (!acquisitionPreview) throw new Error("Preview the selected metadata first.");
    const {base_revision, ...fields} = acquisitionPreview;
    if (base_revision !== client.views.prep.revision) throw new Error("Session changed. Preview again.");
    const view = await client.command("prep", {action:"acquisition_accept", ...fields});
    acquisitionAccepted();
    return view;
  }));
  root.document.addEventListener("input", event => {
    const filter = event.target.closest("[data-provider-filter]");
    if (!filter) return;
    const row = client.views.prep?.acquisition_queue.find(row => row.key === filter.dataset.providerFilter);
    if (!row) return;
    const result = providerRows(row.providers, filter.value);
    root.document.getElementById("provider-links-" + row.key).innerHTML = result.html;
    filter.parentElement.querySelector("[data-provider-count]").textContent =
      `${result.count} of ${row.providers.length} providers. Filtering does not search their catalogs.`;
  });
  root.document.addEventListener("click", event => {
    const copy = event.target.closest("[data-copy-query]");
    if (copy) {
      const input = root.document.getElementById("lookup-" + copy.dataset.copyQuery);
      copySearchText(input.value, root.navigator.clipboard).then(() => {
        $("#error").hidden = true;
        $("#status").textContent = "Search text copied. Open a provider and paste it into their search.";
      }).catch(() => {
        input.focus();
        input.select();
        $("#error").textContent = "Could not copy to clipboard. The search text is selected; copy it manually.";
        $("#error").hidden = false;
      });
      return;
    }
    const button = event.target.closest("[data-action]");
    if (button) run(() => client.command(mode, {action:button.dataset.action,
      key:button.dataset.key, file_id:button.dataset.fileId,
      entry_id:button.dataset.entryId, direction:button.dataset.direction, progress:button.dataset.progress}));
  });
  switchMode("prep").then(() => {
    const params = new URLSearchParams(root.location.search);
    if (root.location.pathname.startsWith("/setlist/") && params.has("personal")) run(async () => {
      const response = await root.fetch("/api/catalog/personal/prep?" + new URLSearchParams({crate_id:params.get("personal")}));
      const document = await response.json();
      if (!response.ok) throw new Error(document.error || "Personal-set handoff failed.");
      const crate = document.crates[0];
      if (String(crate.revision) !== params.get("revision")) throw new Error("Personal set changed. Refresh its main-app revision before previewing.");
      invalidatePortable();
      $("#portable-import").open = true;
      return previewPortable({format:"catalog",name:crate.title,crate_id:crate.id,
        text:JSON.stringify(document)},portableGeneration);
    });
  });
})(typeof globalThis !== "undefined" ? globalThis : this);
