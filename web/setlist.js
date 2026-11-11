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
    return view.saved ? "Crate saved · Export to finish" : "Ready to save";
  }
  function trackRows(items, live = false) {
    return items.map((item, index) => `<tr><td>${String(index + 1).padStart(2, "0")}</td>
      <td><span class="track-title">${escape(item.track.title)}</span><span class="request-sub">${escape(item.track.artist)}</span></td>
      <td>${escape(item.version_label)} <span class="request-sub">${escape(item.track.id)}</span><span class="file-path">${escape(item.track.path)}</span></td>
      <td>${item.reasons.map(reason => `<span class="badge">${escape(reason)}</span>`).join("")}</td>
      ${live ? `<td><button class="secondary" data-action="played" data-file-id="${escape(item.track.id)}">Mark played</button></td>` : ""}</tr>`).join("");
  }
  class FlowClient {
    constructor(fetcher) {
      this.fetcher = fetcher;
      this.token = null;
      this.views = {};
      this.pending = null;
    }
    async request(path, options = {}) {
      const response = await this.fetcher(path, options);
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
      return body;
    }
    async load(mode) {
      if (!this.token) this.token = (await this.request("/api/session")).token;
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
  const exports = {escape, reviewRows, trackRows, prepProgress, FlowClient};
  if (typeof module !== "undefined" && module.exports) module.exports = exports;
  if (!root.document) return;
  const $ = selector => root.document.querySelector(selector);
  const client = new FlowClient(root.fetch.bind(root));
  let mode = "prep", busy = false;
  function guestAccepted() {
    $("#guest-form").reset();
    $("#guest-entry").open = false;
  }
  function paint(view) {
    $("#event-name").textContent = view.event_name;
    if (view.mode === "prep") {
      $("#prep-review").innerHTML = reviewRows(view.needs_review);
      $("#prep-tracks").innerHTML = trackRows(view.recommendations);
      $("#review-count").textContent = view.needs_review.length;
      $("#crate-count").textContent = view.recommendations.length;
      $("#prep-progress").textContent = prepProgress(view);
      $("#prep-progress").classList.toggle("complete", view.prep_done);
      $("#prep-progress").classList.toggle("failed", view.verification.mismatches > 0);
      $("#verify").textContent = view.verification.note;
      $("#save-prep").disabled = !!view.needs_review.length;
      $("#export-prep").disabled = !view.saved;
      $("#download").hidden = view.verification.mismatches !== 0;
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
    const actedOnReview = root.document.activeElement?.matches("[data-key]");
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
        $("#save-prep").disabled = !!client.views.prep.needs_review.length;
        $("#export-prep").disabled = !client.views.prep.saved;
      }
      if (client.pending) {
        root.document.querySelectorAll("button").forEach(button => {button.disabled = true;});
        $("#retry-pending").disabled = false;
        $("#reconcile").disabled = false;
        $("#status").textContent = "Unacknowledged action retained. Retry the identical action or reload to reconcile.";
      }
      $("#recovery").hidden = !client.pending;
      if (!$("#error").hidden) $("#error").focus();
      else if (actedOnReview) {
        const panel = $(mode === "prep" ? "#prep-review" : "#live-requests");
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
  $("#live-tab").addEventListener("click", () => switchMode("live"));
  $("#save-prep").addEventListener("click", () => run(() => client.command("prep", {action:"save"})));
  $("#export-prep").addEventListener("click", () => run(() => client.command("prep", {action:"export"})));
  $("#retry-pending").addEventListener("click", () => run(async () => {
    const guest = client.pending?.action === "guest";
    const view = await client.retry();
    if (guest) guestAccepted();
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
  root.document.addEventListener("click", event => {
    const button = event.target.closest("[data-action]");
    if (button) run(() => client.command(mode, {action:button.dataset.action,
      key:button.dataset.key, file_id:button.dataset.fileId}));
  });
  switchMode("prep");
})(typeof globalThis !== "undefined" ? globalThis : this);
