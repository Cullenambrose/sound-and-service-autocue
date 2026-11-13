(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  let session, state, results = [], proposal = null, savedRule = null, exportRequest = null;
  let outbox = null, busy = false;
  let savedEvidence = null, phaseSerial = 0;
  let aiPlanningDraft = null;
  let batchPreview = null, conversationState = null;
  let editPreview = null;
  let historyPreview = null;
  const json = id => JSON.parse($(id).value);
  const message = (text, failed = false) => {
    $("status").textContent = text;
    $("status").classList.toggle("error", failed);
    if (failed) $("status").focus();
  };
  const node = (tag, text) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    return element;
  };
  function button(text, run) {
    const element = node("button", text);
    element.addEventListener("click", () => action(run));
    return element;
  }
  async function request(url, body) {
    const response = await fetch(url, body === undefined ? {} : {
      method: "POST", headers: {"Content-Type": "application/json", "X-AutoCue-Token": session.token},
      body: JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    return result;
  }
  const post = (endpoint, body) => request("/api/studio/" + endpoint, body);
  async function action(run) {
    if (busy) return message("An action is still running.", true);
    busy = true;
    document.body.setAttribute("aria-busy", "true");
    try { await run(); }
    catch (error) { message(error.message, true); }
    finally { busy = false; document.body.removeAttribute("aria-busy"); }
  }
  function download(name, value, kind = "application/json") {
    const blob = value instanceof Uint8Array ? new Blob([value], {type: kind}) :
      new Blob([JSON.stringify(value, null, 2)], {type: kind});
    const url = URL.createObjectURL(blob);
    const link = node("a");
    link.href = url; link.download = name;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function showPending() { $("pending").hidden = !outbox?.pending; }
  async function retry() {
    if (!outbox?.pending) throw new Error("No pending action.");
    outbox.check();
    const {endpoint, ...body} = outbox.pending;
    const receipt = await request(endpoint, body);
    outbox.accept(receipt);
    if (endpoint === "/api/studio/rules/save") {
      savedRule = {id: receipt.id, revision: receipt.revision};
    }
    if (endpoint === "/api/studio/assistant/chat") {
      await loadConversation(receipt.conversation_id);
    }
    if (endpoint === "/api/studio/batch/approve") {
      batchPreview = null; $("batch-approve").disabled = true;
    }
    if (endpoint === "/api/studio/edit/approve" || endpoint === "/api/studio/edit/undo") {
      editPreview = null; $("edit-approve").disabled = true;
    }
    if (endpoint === "/api/studio/history/approve" || endpoint === "/api/studio/history/undo") {
      historyPreview = null; $("history-approve").disabled = true;
      $("history-preview").textContent = "";
    }
    showPending();
    message("Action acknowledged. Refreshing saved state.");
    await refresh();
  }
  async function durable(url, body) {
    if (!outbox) throw new Error("Studio is not ready.");
    outbox.prepare({endpoint: url, ...body});
    showPending();
    return retry();
  }
  function clearProposal() { proposal = null; $("proposal").hidden = true; }
  function phaseSummary(phases = []) {
    const list = node("ol");
    for (const phase of phases) {
      list.append(node("li", `${phase.label} (${phase.phase}): ${phase.start_s}-${phase.end_s}s; ` +
        `${phase.duration_s}s, target difference ${phase.delta_s}s; ` +
        `positions ${phase.start_position + 1}-${phase.end_position}; energy target ${phase.energy_target ?? "unspecified"}.`));
    }
    return list;
  }
  function transitionSummary(report) {
    const section = node("details");
    section.append(node("summary", "Cue and transition evidence"));
    if (!report) { section.append(node("p", "No transition evidence was recorded for this historical plan.")); return section; }
    const seconds = value => value === null ? "unavailable" : `${value}s`;
    section.append(node("p", `Timing ${report.status}: full files ${seconds(report.full_file_runtime_s)}; ` +
      `reviewed windows ${seconds(report.play_window_subtotal_s)}; ` +
      `estimated elapsed ${seconds(report.estimated_elapsed_s)}.`), node("p", report.note),
      node("pre", JSON.stringify({issues: report.issues, timeline: report.timeline,
        unused_window_ids: report.unused_window_ids, unused_overlap_pairs: report.unused_overlap_pairs}, null, 2)));
    for (const card of report.transitions) {
      section.append(node("h4", `${card.from_id} -> ${card.to_id}`),
        node("p", `BPM ${card.bpm.map(value => value ?? "unknown").join(" -> ")}; ` +
          `Camelot ${card.camelot_keys.map(value => value ?? "unknown").join(" -> ")}; ` +
          `raw tempo difference ${card.tempo_change_percent ?? "unknown"}%; overlap ${card.overlap_s ?? "unspecified"}s.`),
        node("p", card.assumptions), node("p", card.warnings.join("; ")));
    }
    for (const track of report.tracks) {
      const details = node("details");
      details.append(node("summary", `Exact file ${track.file_id}: existing cue analysis`),
        node("pre", JSON.stringify(track, null, 2)));
      section.append(details);
    }
    return section;
  }
  function displayProposal(value) {
    proposal = value;
    $("proposal").hidden = false;
    $("proposal-summary").textContent = `${value.name}: ${value.tracks.length} exact files. Expires ${new Date(value.expires_at * 1000).toLocaleTimeString()}. ${value.note}`;
    $("proposal-tracks").replaceChildren(...value.tracks.map(track =>
      node("li", `${track.artist} - ${track.title} [${track.version || "version unknown"}] | ${track.path} | ${track.content_hash}`)));
    if (value.planning?.alternative.phases) {
      const item = node("li", "Verified full-track phase schedule:");
      item.append(phaseSummary(value.planning.alternative.phases)); $("proposal-tracks").append(item);
    }
    if (value.planning) {
      const item = node("li"); item.append(transitionSummary(value.planning.alternative.transition_evidence));
      $("proposal-tracks").append(item);
    }
    $("proposal").focus();
  }
  async function search() {
    clearProposal();
    const value = await post("search", {rule: json("rule")});
    results = value.tracks;
    $("search-summary").textContent = `${value.total} eligible indexed files; showing ${results.length}. ${value.evidence}`;
    $("results").replaceChildren(...results.map(track => {
      const row = node("tr"), select = node("td"), check = node("input");
      check.type = "checkbox"; check.value = track.id; check.checked = true;
      check.setAttribute("aria-label", `Include ${track.artist} - ${track.title} ${track.version || ""}`);
      select.append(check);
      row.append(select, node("td", `${track.artist} - ${track.title} [${track.version || "unknown version"}]`),
        node("td", `BPM ${track.bpm || "unknown"} / key ${track.key || "unknown"} / energy ${track.energy ?? "unknown"} / ${track.play_count} observed plays`),
        node("td", `${track.id} | ${track.path}`));
      const cues = node("details");
      cues.append(node("summary", "Existing cue evidence"),
        node("pre", JSON.stringify({content_hash: track.content_hash,
          duration_s: track.duration_s, cue_evidence: track.cue_evidence,
          note: "Indexed evidence only. No automatic analysis, audition or source writes."}, null, 2)));
      row.children[2].append(cues);
      const feedback = node("details"), note = node("input");
      note.type = "text"; note.maxLength = 1000;
      note.setAttribute("aria-label", `New private feedback note for ${track.title}`);
      feedback.append(node("summary", `Explicit DJ rating: ${track.dj_rating ?? 0}`),
        node("p", "Personal preference only, never inferred crowd response. New note replaces any previous private note."),
        note);
      for (const [label, rating] of [["Prefer", 1], ["Neutral", 0], ["Avoid softly", -1]]) {
        feedback.append(button(label, async () => {
          await durable("/api/studio/feedback/save", {file_id: track.id,
            base_revision: track.feedback_revision || 0, rating, note: note.value});
          await search();
        }));
      }
      row.children[2].append(feedback);
      return row;
    }));
    for (const id of ["now-playing", "history-file"]) {
      $(id).replaceChildren(...(id === "now-playing" ? [node("option", "No current track")] : []),
        ...results.map(track => {
          const option = node("option", `${track.artist} - ${track.title} [${track.version || "unknown"}]`);
          option.value = track.id; return option;
        }));
      if (id === "now-playing") $(id).firstChild.value = "";
    }
    message("Search complete. Review indexed evidence and exact versions.");
  }
  function useRule(rule) {
    savedRule = rule.id ? rule : null;
    $("name").value = rule.name;
    $("folder").value = rule.folder || "";
    $("rule").value = JSON.stringify(rule.rule, null, 2);
    clearProposal();
  }
  async function refresh() {
    state = await request("/api/studio/state");
    $("ai-model").value = state.ai_settings.model;
    $("ai-status").textContent = (state.capabilities.ai.configured ? `Configured model: ${state.capabilities.ai.model}. ` :
      "No local model configured. The filter builder still works. ") + state.capabilities.ai.note;
    $("rules").replaceChildren(...state.rules.map(rule => {
      const article = node("article");
      article.append(node("strong", `${rule.folder ? rule.folder + " / " : ""}${rule.name} (r${rule.revision})`),
        button("Load and refresh this rule", async () => { useRule(rule); await search(); }));
      return article;
    }));
    $("saved-crates").replaceChildren(...state.crates.map(crate => {
      const article = node("article");
      article.append(node("strong", `${crate.name}: ${crate.entries} entries, r${crate.revision}`),
        button("Draft reviewed rename/reorganization", async () => {
          const detail = await request("/api/catalog/personal/detail?" + new URLSearchParams({crate_id: crate.id}));
          $("edit-operations").value = JSON.stringify([{action: "rename", crate_id: crate.id,
            base_revision: crate.revision, name: crate.name + " (edited)"}], null, 2);
          editPreview = null; $("edit-approve").disabled = true;
          $("edit-output").textContent = JSON.stringify({crate: detail,
            examples: {reorder: {action: "reorder", crate_id: crate.id, base_revision: crate.revision,
              entry_ids: detail.entries.map(entry => entry.id).reverse()},
              merge: {action: "merge", source_id: crate.id, source_revision: crate.revision,
                target_id: "choose-another-crate-id", target_revision: 1, mode: "copy"},
              move: {action: "move", source_id: crate.id, source_revision: crate.revision,
                target_id: "choose-another-crate-id", target_revision: 1,
                entry_ids: detail.entries.slice(0, 1).map(entry => entry.id)},
              remove: {action: "remove", crate_id: crate.id, base_revision: crate.revision,
                entry_ids: detail.entries.slice(0, 1).map(entry => entry.id)}}}, null, 2);
          $("edit-operations").closest("details").open = true; $("edit-operations").focus();
          message("Rename draft and occurrence-based examples loaded. Review/edit the JSON, then preview; nothing changed.");
        }),
        button("Preview verified export", async () => {
          exportRequest = null; $("export-download").disabled = true;
          const body = {dataset_id: session.dataset_id, crate_id: crate.id, base_revision: crate.revision};
          const value = await request("/api/catalog/personal/export/preview", body);
          $("export").hidden = false;
          $("export-summary").textContent = JSON.stringify(value.manifest || value, null, 2);
          // The download endpoint revalidates the preview identity and every file.
          exportRequest = {...body, preview_id: value.preview_id};
          $("export-download").disabled = !(value.manifest?.ready ?? value.ready);
        }));
      if (crate.id.startsWith("studio-")) article.append(button("Inspect saved creation evidence", async () => {
        savedEvidence = await post("crate/evidence", {crate_id: crate.id});
        $("saved-evidence").hidden = false;
        $("evidence-replan").disabled = !savedEvidence.creation.planning;
        $("saved-evidence-note").textContent = `${savedEvidence.creation.name} (current crate revision ${savedEvidence.revision}). ` +
          savedEvidence.note +
          (savedEvidence.current_selection_matches ? " Current selection/order matches the creation journal." :
            " WARNING: current selection/order differs from the creation journal.") +
          (savedEvidence.current_analysis_matches ? " Current analysis matches the journal." :
            " WARNING: cue analysis/review changed; recorded transition evidence is historical. Replan before relying on it.");
        const planning = savedEvidence.creation.planning;
        $("saved-evidence-phases").replaceChildren(...(planning ? [
          node("p", `Recorded full-track runtime: ${planning.alternative.duration_s}s.`),
          phaseSummary(planning.alternative.phases),
          transitionSummary(planning.alternative.transition_evidence),
        ] : [node("p", "Static crate: no whole-set timing policy was certified.")]));
      }));
      return article;
    }));
    $("health").textContent = JSON.stringify(state.health, null, 2);
    renderHistory();
    renderIngestion(state.ingestion);
    $("batch-history").textContent = JSON.stringify(state.batches, null, 2);
    $("edit-history").replaceChildren(...state.edits.map(edit => {
      const article = node("article"), details = node("details");
      details.append(node("summary", `${edit.edit_id}: ${edit.undo ? "undone" : edit.approval ? "approved" : "preview only"}`),
        node("pre", JSON.stringify(edit, null, 2)));
      article.append(details, button("Download before/after recovery journal", () =>
        download("studio-crate-edit-" + edit.edit_id + ".json", edit)));
      if (edit.approval && !edit.undo) article.append(button("Undo this entire edit if unchanged", async () => {
        if (!confirm("Restore every affected crate's previous name/order/memberships? This refuses if newer edits exist; audio is untouched.")) return;
        await durable("/api/studio/edit/undo", {edit_id: edit.edit_id,
          expected_crates: Object.entries(edit.document.before).map(([key, crate]) => {
            const base = edit.approval.crates.find(receipt => receipt.crate_id === key).revision;
            return {crate_id: key, base_revision: base, revision: base + 1, name: crate.name, entries: crate.entries};
          })});
      }));
      return article;
    }));
    $("destinations").replaceChildren(...state.capabilities.destinations.map(item =>
      node("p", `${item.name}: ${item.state}. ${item.note}`)));
    $("gates").replaceChildren(...state.capabilities.release_gates.map(item =>
      node("p", `${item.id}: ${item.state}. ${item.requirement}`)));
    const history = await request("/api/catalog/personal/exports");
    $("exports").replaceChildren(...history.exports.map(item => {
      if (item.state !== "historical") return node("p", `Unavailable export: ${item.error}`);
      const link = node("a", `${item.crate.name}: historical verified ZIP (not current availability)`);
      link.href = "/api/catalog/personal/exports/download?" +
        new URLSearchParams({dataset_id: session.dataset_id, export_id: item.export_id});
      link.download = item.filename;
      const paragraph = node("p"); paragraph.append(link); return paragraph;
    }));
    if (!$("template").options.length || $("template").options.length === 1) {
      for (const template of state.capabilities.templates) {
        const option = node("option", template.name); option.value = template.id; $("template").append(option);
      }
    }
  }
  const bind = (id, run) => $(id).addEventListener("click", () => action(run));
  function renderHistory() {
    historyPreview = null; $("history-approve").disabled = true;
    $("history-preview").textContent = "";
    $("history").replaceChildren(...state.history.map(event => {
      const article = node("article"), label = node("label"), selected = node("input");
      selected.type = "checkbox"; selected.value = event.request_id;
      selected.addEventListener("change", () => {
        historyPreview = null; $("history-approve").disabled = true;
        $("history-preview").textContent = "Selection changed. Preview again before approving.";
      });
      label.append(selected, document.createTextNode(
        ` ${event.played_at} | exact file ${event.file_id} | ${event.note || "no note"} | observation ${event.request_id}`));
      article.append(label);
      return article;
    }));
    $("history-edits").replaceChildren(...(state.history_edits || []).map(edit => {
      const article = node("article"), details = node("details");
      details.append(node("summary", `${edit.edit_id}: ${edit.undo ? "restored" : edit.approval ? "removed from active history" : "preview only"}`),
        node("pre", JSON.stringify(edit, null, 2)));
      article.append(details, button("Download private history recovery journal", () =>
        download("studio-history-" + edit.edit_id + ".json", edit)));
      if (edit.approval && !edit.undo) article.append(button("Undo these history removals", async () => {
        if (!confirm("Restore only these exact observations? This refuses if newer changes affect them. Audio and preferences stay unchanged.")) return;
        await durable("/api/studio/history/undo", {edit_id: edit.edit_id, event_ids: edit.approval.event_ids});
      }));
      return article;
    }));
  }
  function renderIngestion(value) {
    $("monitors").replaceChildren(...value.roots.map(root => {
      const monitor = value.monitors.find(item => item.root_id === root.id);
      const article = node("article"), interval = node("input");
      interval.type = "number"; interval.min = "60"; interval.max = "86400";
      interval.value = monitor?.interval_s || 300;
      interval.setAttribute("aria-label", `Monitor interval seconds for ${root.path}`);
      article.append(node("strong", root.path), node("p",
        `Monitor ${monitor?.enabled ? "enabled" : "disabled"}; interval seconds below. ` +
        `Last job: ${monitor?.last_job || "none"}; next admission: ${
          monitor?.enabled ? new Date(monitor.next_scan * 1000).toLocaleString() : "disabled"}. ` +
        `Error: ${monitor?.error || "none"}. Disabling does not cancel already queued/running scans.`),
      interval, button(monitor?.enabled ? "Disable monitoring" : "Enable monitoring", async () => {
        if (!interval.reportValidity()) throw new Error("Monitor interval must be 60..86400 seconds.");
        await durable("/api/studio/monitor/save", {root_id: root.id, base_revision: monitor?.revision || 0,
          enabled: !monitor?.enabled, interval_s: Number(interval.value)});
      }));
      return article;
    }));
    $("inbox").replaceChildren(node("p", `Inbox counts ${JSON.stringify(value.counts)}. Showing up to ${value.limit}; new evidence first.`),
      ...value.items.map(item => {
        const article = node("article"), metadata = item.metadata;
        article.append(node("strong", `${metadata.artist || "Unknown artist"} - ${metadata.title || "Unknown title"} ` +
          `[${metadata.version || "version unknown"}]`), node("p",
        `${item.relative_path} | ${item.file_id} | status ${item.status}, r${item.revision}; ` +
        `missing ${item.missing_metadata.join(", ") || "none"}; hash group ${item.exact_hash_group_count}; ` +
        `available ${!!item.available}; error ${item.error || "none"}`),
        node("pre", JSON.stringify({metadata, content_hash: item.content_hash}, null, 2)));
        for (const status of ["reviewed", "ignored", "new"]) article.append(button(`Mark ${status}`, () =>
          durable("/api/studio/inbox/review", {file_id: item.file_id, base_revision: item.revision, status})));
        const exclusion = value.exclusions.find(entry => entry.file_id === item.file_id);
        const active = exclusion?.excluded && exclusion.content_hash === item.content_hash;
        if (item.content_hash) article.append(button(active ? "Restore to Studio selection" : "Exclude from Studio selection", async () => {
          if (!confirm(`${active ? "Restore" : "Exclude"} this exact indexed file in Studio only? Original audio and saved crates remain unchanged.`)) return;
          await durable("/api/studio/exclusion/save", {file_id: item.file_id, content_hash: item.content_hash,
            base_revision: exclusion?.revision || 0, excluded: !active, reason: "Explicit inbox review"});
        }));
        return article;
      }));
    $("exclusions").replaceChildren(...value.exclusions.map(item => {
      const article = node("article");
      article.append(node("p", `${item.file_id}: ${item.excluded ? "excluded at recorded hash" : "restored"}, r${item.revision}. ${item.reason}`));
      if (item.excluded && item.current_content_hash) article.append(button("Restore recorded exclusion", () =>
        durable("/api/studio/exclusion/save", {file_id: item.file_id, content_hash: item.current_content_hash,
          base_revision: item.revision, excluded: false, reason: "Explicit recovery"})));
      return article;
    }));
  }
  async function loadConversation(id = $("conversation-id").value) {
    conversationState = await post("assistant/load", {conversation_id: id});
    $("conversation-id").value = id;
    localStorage.setItem("studio-conversation:" + session.dataset_id, id);
    $("conversation").textContent = JSON.stringify(conversationState, null, 2);
    const turns = conversationState.turns;
    const previous = turns.at(-2)?.draft, latest = turns.at(-1)?.draft;
    const changes = {};
    if (latest) for (const section of ["rule", "policy"]) {
      const before = previous?.[section] || {}, after = latest[section] || {};
      changes[section] = [...new Set([...Object.keys(before), ...Object.keys(after)])]
        .filter(key => JSON.stringify(before[key]) !== JSON.stringify(after[key]))
        .map(key => ({field: key, before: before[key] ?? "absent", after: after[key] ?? "removed"}));
    }
    $("chat-changes").textContent = latest ? JSON.stringify({note: "Review actual changes; model output is not semantic proof.", changes}, null, 2) : "";
    $("chat-apply").disabled = !conversationState.turns.length;
  }
  bind("ingest-refresh", async () => {
    await refresh();
    $("scan-status").textContent = JSON.stringify(await request("/api/catalog/scans"), null, 2);
    message("Inbox, monitors and durable scan progress refreshed.");
  });
  bind("batch-preview", async () => {
    batchPreview = null; $("batch-approve").disabled = true;
    batchPreview = await post("batch/preview", {dataset_id: session.dataset_id, crates: json("batch-rules")});
    $("batch-preview-output").textContent = JSON.stringify(batchPreview, null, 2);
    $("batch-approve").disabled = false;
    message("Exact files previewed. Review all crates before atomic approval; no original audio is moved.");
  });
  $("batch-rules").addEventListener("input", () => { batchPreview = null; $("batch-approve").disabled = true; });
  bind("batch-approve", async () => {
    if (!batchPreview) throw new Error("Preview the whole batch first.");
    await durable("/api/studio/batch/approve", {batch_id: batchPreview.batch_id});
  });
  $("edit-operations").addEventListener("input", () => { editPreview = null; $("edit-approve").disabled = true; });
  bind("edit-preview", async () => {
    editPreview = null; $("edit-approve").disabled = true;
    editPreview = await post("edit/preview", {dataset_id: session.dataset_id, operations: json("edit-operations")});
    $("edit-output").textContent = JSON.stringify(editPreview, null, 2);
    $("edit-approve").disabled = false;
    message("Before/after names and exact memberships previewed. Review all changes; existing file evidence is historical.");
  });
  bind("edit-approve", async () => {
    if (!editPreview) throw new Error("Preview existing-crate edits first.");
    await durable("/api/studio/edit/approve", {edit_id: editPreview.edit_id,
      expected_crates: Object.entries(editPreview.after).map(([key, crate]) => ({
        crate_id: key, base_revision: crate.revision, revision: crate.revision + 1,
        name: crate.name, entries: crate.entries,
      }))});
  });
  bind("conversation-load", () => loadConversation());
  bind("ai-configure", () => durable("/api/studio/ai/configure", {
    model: $("ai-model").value.trim(), base_revision: state.ai_settings.revision,
  }));
  bind("conversation-new", () => loadConversation(crypto.randomUUID()));
  bind("chat-send", async () => {
    if (!conversationState || conversationState.id !== $("conversation-id").value) await loadConversation();
    await durable("/api/studio/assistant/chat", {conversation_id: conversationState.id,
      base_revision: conversationState.revision, message: $("chat-message").value,
      rule: json("rule"), share_evidence: $("share-evidence").checked, action: $("chat-action").value});
    message("Assistant turn saved for review only. Inspect the draft and citations; no crate or source file was changed.");
  });
  bind("chat-apply", () => {
    const draft = conversationState?.turns.at(-1)?.draft;
    if (!draft) throw new Error("Load a completed assistant turn first.");
    if (draft.action === "organize") {
      const previous = json("rule");
      const crates = draft.crates.map(crate => ({...crate, rule: {
        ...crate.rule, ...(previous.scope_ids !== undefined ? {scope_ids: previous.scope_ids} : {}),
      }}));
      $("batch-rules").value = JSON.stringify(crates, null, 2);
      batchPreview = null; $("batch-approve").disabled = true;
      $("batch-rules").focus();
    } else if (draft.action === "plan") {
      aiPlanningDraft = draft; $("ai-plan-output").textContent = JSON.stringify(draft, null, 2);
      $("apply-ai-plan").disabled = false; $("apply-ai-plan").focus();
    } else {
      const rule = {...draft.rule}, previous = json("rule");
      if (previous.scope_ids !== undefined) rule.scope_ids = previous.scope_ids;
      useRule({name: draft.name, rule}); $("rule").focus();
    }
    message("Draft copied to the review form only. For a plan use Apply reviewed draft; for a batch preview exact files first.");
  });
  bind("search", search);
  bind("interpret", async () => {
    const value = await post("interpret", {message: $("prompt").value});
    useRule({name: value.intent.name, rule: value.intent.rule});
    message("AI translated the request only. Review/correct the rule, then Search. No library action was taken.");
    $("rule").focus();
  });
  bind("rule-save", () => durable("/api/studio/rules/save", {
    id: savedRule?.id || crypto.randomUUID(), base_revision: savedRule?.revision || 0,
    name: $("name").value, folder: $("folder").value, rule: json("rule"),
  }));
  bind("rule-new", () => { savedRule = null; message("Next smart-rule save will create a new rule."); });
  bind("interpret-plan", async () => {
    aiPlanningDraft = null; $("apply-ai-plan").disabled = true; $("ai-plan-output").textContent = "";
    const value = await post("interpret/plan", {message: $("plan-prompt").value});
    aiPlanningDraft = value.intent;
    $("ai-plan-output").textContent = JSON.stringify(value, null, 2);
    $("apply-ai-plan").disabled = false;
    message("AI translated a draft only. Review every filter, phase and default before applying; nothing was generated or saved.");
  });
  bind("apply-ai-plan", async () => {
    if (!aiPlanningDraft) throw new Error("Translate and review a set brief first.");
    const current = (await post("plan/validate", {policy: json("policy")})).policy;
    if ((current.phases || []).some(phase => phase.must_include.length))
      throw new Error("Existing phases contain exact must-plays. Edit those phases manually; AI did not replace your draft.");
    const policy = {...aiPlanningDraft.policy, must_include: current.must_include,
      exclude: current.exclude, pinned: current.pinned};
    const validated = await post("plan/validate", {policy});
    const rule = {...aiPlanningDraft.rule}, previousRule = json("rule");
    if (previousRule.scope_ids !== undefined) rule.scope_ids = previousRule.scope_ids;
    $("policy").value = JSON.stringify(validated.policy, null, 2);
    $("rule").value = JSON.stringify(rule, null, 2);
    $("name").value = aiPlanningDraft.name;
    $("phase-editor").replaceChildren();
    for (const phase of validated.policy.phases || []) addPhase(phase);
    savedRule = null; clearProposal(); $("plans").replaceChildren();
    aiPlanningDraft = null; $("apply-ai-plan").disabled = true;
    message("Reviewed draft applied. Source scope, event lists and exact-file commitments retained. Generate to test feasibility; no crate was changed.");
  });
  bind("propose", async () => {
    const ids = [...$("results").querySelectorAll("input:checked")].map(check => check.value);
    displayProposal(await post("propose", {dataset_id: session.dataset_id,
      name: $("name").value, rule: json("rule"), file_ids: ids}));
  });
  bind("approve", async () => {
    if (!proposal) throw new Error("Preview an exact-file proposal first.");
    await durable("/api/studio/approve", {proposal_id: proposal.proposal_id});
    clearProposal();
  });
  function addPhase(value = {}) {
    if ($("phase-editor").children.length >= 12) throw new Error("At most 12 phases are supported.");
    const article = node("article"), number = ++phaseSerial;
    const defaults = {phase: "dinner", label: `Phase ${number}`, target_minutes: 10,
      tolerance_minutes: 1, must_include: [], clean_only: false, ...value};
    const fields = [
      ["label", "Label", "text"], ["phase", "Event phase", "select"],
      ["target_minutes", "Target minutes", "number"], ["tolerance_minutes", "Tolerance minutes", "number"],
      ["energy_target", "Energy target (optional, 0-1)", "number"],
      ["bpm_min", "Minimum BPM (optional)", "number"], ["bpm_max", "Maximum BPM (optional)", "number"],
      ["clean_only", "Explicit clean tags required", "checkbox"],
      ["must_include", "Exact must-play file IDs (JSON array)", "textarea"],
    ];
    for (const [field, text, type] of fields) {
      const input = node(type === "select" ? "select" : type === "textarea" ? "textarea" : "input");
      input.id = `phase-${number}-${field}`; input.dataset.field = field;
      if (input.tagName === "INPUT") input.type = type;
      if (type === "select") {
        for (const name of ["ceremony", "cocktail", "dinner", "first_dance", "open_dancing", "generic"]) {
          const option = node("option", name.replaceAll("_", " ")); option.value = name; input.append(option);
        }
      }
      if (type === "number") {
        input.min = field === "target_minutes" ? "0.1" : "0";
        input.max = field === "energy_target" ? "1" : field.startsWith("bpm") ? "1000" : "1440";
        input.step = field === "energy_target" ? "0.01" : "0.1";
        input.required = field === "target_minutes" || field === "tolerance_minutes";
      }
      if (type === "checkbox") input.checked = defaults[field];
      else input.value = field === "must_include" ? JSON.stringify(defaults[field]) : defaults[field] ?? "";
      if (field === "label") { input.required = true; input.maxLength = 80; }
      const label = node("label", `Phase ${number}: ${text}`); label.htmlFor = input.id;
      article.append(label, input);
    }
    article.append(button("Remove this phase and its phase requirements", () => article.remove()));
    $("phase-editor").append(article);
  }
  bind("phase-add", () => addPhase());
  bind("phase-load", async () => {
    const value = await post("plan/validate", {policy: json("policy")});
    $("phase-editor").replaceChildren();
    for (const phase of value.policy.phases || []) addPhase(phase);
    message("Phase editor loaded. Edits take effect only after Apply.");
  });
  bind("phase-apply", async () => {
    const phases = [...$("phase-editor").children].map(article => {
      const phase = {};
      for (const input of article.querySelectorAll("[data-field]")) {
        if (!input.reportValidity()) throw new Error("Correct the highlighted phase field before applying.");
        const field = input.dataset.field;
        if (input.type === "checkbox") phase[field] = input.checked;
        else if (field === "must_include") phase[field] = JSON.parse(input.value);
        else if (input.type === "number") { if (input.value !== "") phase[field] = Number(input.value); }
        else phase[field] = input.value;
      }
      return phase;
    });
    const policy = json("policy");
    if (phases.length) {
      policy.phases = phases;
      policy.target_minutes = phases.reduce((ticks, phase) => ticks + Math.round(phase.target_minutes * 10), 0) / 10;
    } else delete policy.phases;
    const value = await post("plan/validate", {policy});
    $("policy").value = JSON.stringify(value.policy, null, 2);
    clearProposal(); $("plans").replaceChildren();
    message("Phase schedule applied. Global hard constraints retained; generate new alternatives.");
  });
  function planningInput() {
    return {rule: json("rule"), policy: json("policy"), event: json("event"), playback: json("playback")};
  }
  function assertPlanInputs(body) {
    if (JSON.stringify(planningInput()) !== JSON.stringify(body))
      throw new Error("Planning inputs changed. Generate new alternatives before pinning or repairing.");
  }
  function addPins(policy, pins) {
    const combined = [...(policy.pinned || [])];
    for (const pin of pins) {
      if (combined.some(old => (old.position === pin.position && old.file_id !== pin.file_id) ||
          (old.file_id === pin.file_id && old.position !== pin.position)))
        throw new Error("This conflicts with an existing pin. Edit that pin explicitly; no pin was replaced.");
      if (!combined.some(old => old.position === pin.position && old.file_id === pin.file_id)) combined.push(pin);
    }
    return {...policy, pinned: combined};
  }
  async function generatePlan() {
    const body = planningInput();
    clearProposal(); $("plans").replaceChildren();
    const value = await post("plan", body);
    $("plans").replaceChildren(node("pre", JSON.stringify({
      diagnostics: value.diagnostics, unresolved: value.unresolved,
    }, null, 2)));
    for (const [index, alternative] of (value.alternatives || []).entries()) {
      const article = node("article"), list = node("ol");
      list.append(...alternative.tracks.map((track, position) => {
        const item = node("li", `${track.artist} - ${track.title} [${track.version || "unknown"}]`);
        item.append(button(`Pin position ${position + 1}`, () => {
          assertPlanInputs(body);
          $("policy").value = JSON.stringify(addPins(body.policy, [{position, file_id: track.id}]), null, 2);
          clearProposal(); message("Exact position pinned. Regenerate to verify the updated policy.");
        }), button(`Replace position ${position + 1}; keep all other positions`, async () => {
          assertPlanInputs(body);
          const protectedIds = [...(body.policy.must_include || []),
            ...(body.policy.phases || []).flatMap(phase => phase.must_include || []),
            ...(body.policy.pinned || []).map(pin => pin.file_id)];
          if (protectedIds.includes(track.id))
            throw new Error("This file is required or pinned. Change that requirement explicitly before replacing it.");
          const pins = alternative.tracks.flatMap((other, otherPosition) =>
            position === otherPosition ? [] : [{position: otherPosition, file_id: other.id}]);
          const policy = addPins(body.policy, pins);
          policy.exclude = [...new Set([...(policy.exclude || []), track.id])];
          $("policy").value = JSON.stringify(policy, null, 2);
          await generatePlan();
        }));
        return item;
      }));
      article.append(node("h3", `Alternative ${index + 1}`),
        node("p", `${alternative.duration_s} seconds full-track runtime. Not a mixed elapsed-time certification.`),
        phaseSummary(alternative.phases), list,
        transitionSummary(alternative.transition_evidence),
        button("Prepare unreviewed full-file timing draft for this alternative", () => {
          assertPlanInputs(body);
          if (!confirm("Replace the current playback assumptions with unreviewed full-file windows and explicit zero-overlap cuts? Nothing is certified or saved.")) return;
          $("playback").value = JSON.stringify({
            windows: alternative.tracks.map(track => ({file_id: track.id, content_hash: track.content_hash,
              entry_s: 0, exit_s: track.duration_s, reviewed: false})),
            overlaps: alternative.tracks.slice(1).map((track, position) => ({
              from_id: alternative.tracks[position].id, to_id: track.id, seconds: 0,
            })),
          }, null, 2);
          clearProposal();
          message("Unreviewed timing draft prepared. Inspect cues, edit windows/overlaps and review each file before regenerating.");
        }),
        button("Preview this exact plan for approval", async () => {
          assertPlanInputs(body);
          displayProposal(await post("plan/propose", {...body, dataset_id: session.dataset_id,
            name: $("name").value, alternative_index: index}));
        }));
      $("plans").append(article);
    }
    message(value.feasible ? "Constrained set proposals are ready for review." :
      value.diagnostics.status === "infeasible" ? "The supplied hard constraints are infeasible. Review conflicts; nothing was relaxed." :
        "Search budget exhausted without a verified set. This does not prove infeasibility.", !value.feasible);
  }
  bind("plan", generatePlan);
  bind("evidence-download", () => {
    if (!savedEvidence) throw new Error("Inspect a saved crate first.");
    download("studio-creation-evidence.json", savedEvidence);
  });
  bind("evidence-replan", async () => {
    if (!savedEvidence?.creation.planning) throw new Error("Inspect a saved whole-set plan first.");
    const creation = savedEvidence.creation, planning = creation.planning;
    const value = await post("plan/validate", {policy: planning.policy});
    $("policy").value = JSON.stringify(value.policy, null, 2);
    $("rule").value = JSON.stringify(creation.rule, null, 2);
    $("event").value = JSON.stringify(planning.event, null, 2);
    $("playback").value = JSON.stringify(planning.playback || {windows: [], overlaps: []}, null, 2);
    $("name").value = creation.name + " - replanned";
    $("phase-editor").replaceChildren();
    for (const phase of value.policy.phases || []) addPhase(phase);
    savedRule = null; clearProposal(); $("plans").replaceChildren();
    message("Historical inputs loaded into a new unsaved draft. Generate against current evidence; the saved crate is unchanged.");
  });
  bind("live", async () => {
    const value = await post("live", {rule: json("rule"), event: json("event"),
      now_playing: $("now-playing").value || null, played_ids: json("played"), phase: $("phase").value});
    $("live-results").replaceChildren(...value.recommendations.items.map(item =>
      node("li", `${item.track.artist} - ${item.track.title}: ${item.reasons.join("; ")}`)));
    message(value.note);
  });
  bind("record-play", () => {
    if (!$("history-file").value) throw new Error("Search and select a file first.");
    return durable("/api/studio/history/record", {file_id: $("history-file").value,
      played_at: new Date().toISOString(), note: $("history-note").value});
  });
  bind("provider-lookup", async () => {
    const value = await post("providers", {title: $("provider-title").value,
      artist: $("provider-artist").value, version: $("provider-version").value});
    $("providers").replaceChildren(node("p", value.note), node("p", "Search text: " + value.query),
      button("Copy search text", async () => {
        await navigator.clipboard.writeText(value.query);
        message("Search text copied; no provider was contacted.");
      }), ...value.providers.map(provider => {
        const paragraph = node("p"), link = node("a", `${provider.name}: ${provider.link_mode}`);
        link.href = provider.url; link.target = "_blank"; link.rel = "noopener noreferrer";
        paragraph.append(link, node("span", " - " + provider.note)); return paragraph;
      }));
    message("Provider navigation links ready. Nothing has been acquired or fulfilled.");
  });
  bind("export-download", async () => {
    if (!exportRequest) throw new Error("Preview an export first.");
    const value = await request("/api/catalog/personal/export/download", exportRequest);
    const bytes = Uint8Array.from(atob(value.bundle_base64), character => character.charCodeAt(0));
    download(value.filename, bytes, "application/zip");
    message("Verified ZIP delivered to the browser. Confirm the download and test its M3U8 in your DJ software.");
    await refresh();
  });
  bind("refresh", refresh);
  bind("scan", () => durable("/api/catalog/scan", {path: $("root").value}));
  bind("scan-refresh", async () => {
    $("scan-status").textContent = JSON.stringify(await request("/api/catalog/scans"), null, 2);
    await refresh();
  });
  bind("retry", retry);
  bind("download-pending", () => { if (!outbox?.pending) throw new Error("No pending action."); download("studio-pending.json", outbox.pending); });
  bind("discard", async () => {
    if (!confirm("Discard only this browser retry? A committed action is not undone. Download it first if you need to reconcile.")) return;
    outbox.clear(); showPending(); await refresh();
    message("Browser retry discarded. Current saved state was refreshed; no action was undone.");
  });
  bind("diagnostics", () => download("studio-release-readiness.json", state.capabilities));
  bind("backup-download", async () => {
    const value = await request("/api/studio/backup", {});
    const bytes = Uint8Array.from(atob(value.bundle_base64), character => character.charCodeAt(0));
    download(value.filename, bytes, "application/zip");
    message("Private database backup delivered to the browser. Confirm the download. Audio and browser pending actions are not included.");
  });
  bind("history-preview-button", async () => {
    historyPreview = null; $("history-approve").disabled = true;
    const event_ids = Array.from($("history").querySelectorAll("input:checked"), input => input.value);
    if (!event_ids.length) throw new Error("Select exact observed plays to remove.");
    historyPreview = await post("history/preview", {dataset_id: session.dataset_id, event_ids});
    $("history-preview").textContent = JSON.stringify(historyPreview, null, 2);
    $("history-approve").disabled = false;
    message("Review the exact observations below. No active history has changed.");
  });
  bind("history-approve", async () => {
    if (!historyPreview) throw new Error("Preview selected history observations first.");
    if (!confirm("Remove these reviewed observations from active history and rediscovery? Recovery receipts remain; this is not permanent privacy erasure.")) return;
    await durable("/api/studio/history/approve", {edit_id: historyPreview.edit_id, event_ids: historyPreview.event_ids});
  });
  bind("history-download", () => download("studio-active-play-history.json", {
    dataset_id: session.dataset_id, observations: state.history,
    note: "Up to 100 most recent active observations, not a complete playback history or audible-success proof.",
  }));
  $("template").addEventListener("change", () => {
    const template = state?.capabilities.templates.find(item => item.id === $("template").value);
    if (template) { useRule({...template, id: null}); message(template.note); }
  });
  action(async () => {
    session = await request("/api/session");
    outbox = new StudioOutbox(localStorage, session.dataset_id);
    showPending();
    await refresh();
    await loadConversation(localStorage.getItem("studio-conversation:" + session.dataset_id) || crypto.randomUUID());
    message("Local alpha ready. Select/index a folder explicitly or search an existing catalog.");
  });
})();
