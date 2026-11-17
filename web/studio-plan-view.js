"use strict";

const StudioPlanView = (() => {
  const FIELD_LABELS = {
    q: "Text query",
    artist: "Artist",
    genre: "Genre",
    bpm_min: "Minimum BPM",
    bpm_max: "Maximum BPM",
    energy_min: "Minimum indexed energy",
    energy_max: "Maximum indexed energy",
    clean_only: "Tagged-clean required",
    never_played: "Never played",
    limit: "Maximum search results",
    sort: "Search order",
    energy_target: "Preferred energy",
    use_feedback: "Use explicit DJ feedback",
  };

  function planDraftSummary(payload) {
    const intent = payload?.intent;
    if (!intent || typeof intent !== "object" || !intent.policy || !intent.rule ||
        typeof intent.policy !== "object" || typeof intent.rule !== "object") {
      throw new Error("The set brief draft is incomplete.");
    }
    const {policy, rule} = intent;
    if ((policy.phases !== undefined && !Array.isArray(policy.phases)) ||
        !Array.isArray(payload.constraint_evidence ?? []) ||
        !Array.isArray(intent.unsupported ?? [])) {
      throw new Error("The set brief draft has invalid review evidence.");
    }
    if (![policy.target_minutes, policy.tolerance_minutes].every(Number.isFinite) ||
        !Number.isInteger(policy.max_tracks)) {
      throw new Error("The set brief draft has invalid timing values.");
    }
    const overview = `${policy.target_minutes} minutes, +/-${policy.tolerance_minutes} minutes, ` +
      `up to ${policy.max_tracks} tracks.`;
    const format = value => typeof value === "boolean" ? (value ? "yes" : "no") : value;
    const filters = [
      ...Object.entries(rule).map(([field, value]) => `Library filter - ${FIELD_LABELS[field] || field}: ${format(value)}`),
      ...Object.entries(policy).filter(([field]) => Object.hasOwn(FIELD_LABELS, field))
        .map(([field, value]) => `Set preference - ${FIELD_LABELS[field]}: ${format(value)}`),
    ];
    const phases = (policy.phases || []).map(phase => {
      if (!phase || typeof phase !== "object" || typeof phase.phase !== "string" ||
          !Number.isFinite(phase.target_minutes) || !Number.isFinite(phase.tolerance_minutes)) {
        throw new Error("The set brief contains an invalid phase.");
      }
      const label = phase.label || phase.phase.replaceAll("_", " ");
      const options = Object.entries(phase)
        .filter(([field]) => Object.hasOwn(FIELD_LABELS, field))
        .map(([field, value]) => `${FIELD_LABELS[field]} ${format(value)}`);
      return `${label}: ${phase.target_minutes} minutes (+/-${phase.tolerance_minutes} minutes)` +
        (options.length ? `; ${options.join(", ")}` : "");
    });
    const evidence = (payload.constraint_evidence || []).map(item => {
      if (!item || typeof item.quote !== "string" || typeof item.field !== "string") {
        throw new Error("The set brief contains invalid source-quote evidence.");
      }
      const label = FIELD_LABELS[item.field] || item.field.replaceAll("_", " ");
      const location = Number.isInteger(item.phase_index) ? ` in phase ${item.phase_index + 1}` :
        item.scope === "policy" ? " in set policy" : " in library filters";
      return `"${item.quote}" interpreted as ${label}${location}: ${format(item.value)}`;
    });
    return {
      overview,
      filters,
      phases,
      evidence,
      unsupported: intent.unsupported || [],
      caveat: "Source quotes are not proof that the request was fully understood. Compare this read-back with your brief before applying it.",
      note: typeof payload.note === "string" ? payload.note : "",
    };
  }

  function compareObservedSet(alternative, observations) {
    if (!alternative || !Array.isArray(alternative.tracks) || !Array.isArray(observations)) {
      throw new Error("A planned set and explicit play observations are required.");
    }
    const plannedIds = new Set();
    for (const track of alternative.tracks) {
      if (!track || typeof track.id !== "string" || plannedIds.has(track.id)) {
        throw new Error("The planned set contains invalid or duplicate exact file IDs.");
      }
      plannedIds.add(track.id);
    }
    const eventIds = new Set();
    const ordered = observations.map(event => {
      if (!event || typeof event.request_id !== "string" || !event.request_id ||
          typeof event.file_id !== "string" || !event.file_id ||
          typeof event.played_at !== "string" || !Number.isFinite(Date.parse(event.played_at)) ||
          eventIds.has(event.request_id)) {
        throw new Error("Selected play observations must have unique IDs, exact files and valid times.");
      }
      eventIds.add(event.request_id);
      return {request_id: event.request_id, file_id: event.file_id, played_at: event.played_at,
        timestamp: Date.parse(event.played_at)};
    }).sort((left, right) => left.timestamp - right.timestamp ||
      (left.request_id < right.request_id ? -1 : left.request_id > right.request_id ? 1 : 0));
    const counts = new Map();
    for (const event of ordered) counts.set(event.file_id, (counts.get(event.file_id) || 0) + 1);
    return {
      planned_tracks: alternative.tracks.map(track => ({
        track, selected_play_count: counts.get(track.id) || 0,
      })),
      actual_order: ordered.map(({timestamp, ...event}) => event),
      outside_plan: ordered.filter(event => !plannedIds.has(event.file_id))
        .map(({timestamp, ...event}) => event),
      not_observed: alternative.tracks.filter(track => !counts.has(track.id)),
      repeats: [...counts.entries()].filter(([, count]) => count > 1)
        .map(([file_id, count]) => ({file_id, count})),
    };
  }

  function trackReasons(alternative) {
    if (!alternative || !Array.isArray(alternative.tracks) ||
        !Array.isArray(alternative.track_reasons) ||
        alternative.track_reasons.length !== alternative.tracks.length) {
      throw new Error("Planner track explanations do not match the selected set.");
    }
    const byId = new Map();
    for (const entry of alternative.track_reasons) {
      if (!entry || !Array.isArray(entry.reasons) ||
          entry.reasons.some(reason => typeof reason !== "string" || !reason.trim()) ||
          byId.has(entry.file_id)) {
        throw new Error("Planner returned invalid per-track explanations.");
      }
      byId.set(entry.file_id, entry.reasons);
    }
    const seen = new Set();
    return alternative.tracks.map(track => {
      if (!track || seen.has(track.id) || !byId.has(track.id)) {
        throw new Error("Planner track explanations do not match the selected files.");
      }
      seen.add(track.id);
      return {file_id: track.id, reasons: byId.get(track.id)};
    });
  }

  function musicMetadataSummary(track) {
    if (!track || typeof track !== "object") throw new Error("Track metadata evidence is missing.");
    const sourceLabel = (field, value) => {
      if (value === null || value === undefined || value === "") return "unknown";
      const source = track.music_metadata_evidence?.[field];
      if (source === "local_file_tag") return "local file tag; not independently verified";
      if (source === "unverified") return "source unverified";
      return "source not recorded";
    };
    return `BPM ${track.bpm ?? "unknown"} (${sourceLabel("bpm", track.bpm)}); ` +
      `key ${track.key || "unknown"} (${sourceLabel("key", track.key)})`;
  }

  return {compareObservedSet, musicMetadataSummary, planDraftSummary, trackReasons};
})();

if (typeof module !== "undefined" && module.exports) module.exports = StudioPlanView;
else window.StudioPlanView = StudioPlanView;
