(function(root) {
  class MatchOutbox {
    constructor(storage, dataset, createId = () => crypto.randomUUID(), namespace = "match") {
      this.storage = storage;
      this.dataset = dataset;
      this.createId = createId;
      this.label = namespace === "stage" ? "staging request" : namespace === "scan" ? "scan request" :
        namespace === "personal" ? "personal crate request" : namespace === "studio" ? "Studio action" : "match";
      this.key = `autocue:catalog-${namespace}:v1:${dataset}`;
      this.saved = storage.getItem(this.key);
      this.pending = this.saved ? JSON.parse(this.saved) : null;
      if (this.pending && this.pending.dataset_id !== dataset) throw new Error(`Pending ${this.label} dataset is invalid.`);
    }
    check() {
      if (this.storage.getItem(this.key) !== this.saved) throw new Error(`Another tab changed the pending ${this.label}. Reload before continuing.`);
    }
    prepare(fields) {
      this.check();
      if (this.pending) throw new Error(`Retry or explicitly discard the pending ${this.label} before making another decision.`);
      const request = {...fields, dataset_id:this.dataset, request_id:this.createId()};
      const encoded = JSON.stringify(request);
      this.storage.setItem(this.key, encoded);
      this.saved = encoded;
      this.pending = request;
      return request;
    }
    accept(receipt) {
      const request = this.pending;
      if (!request || receipt?.ok !== true ||
          ["request_id", "dataset_id", "snapshot_id", "song_id", "file_id"].some(key => receipt[key] !== request[key]) ||
          receipt.content_hash !== (request.file_id === null ? null : request.content_hash) ||
          receipt.revision !== request.base_revision + 1) {
        throw new Error("Match acknowledgment is invalid. The exact request remains available to retry.");
      }
      this.clear();
    }
    clear() {
      this.check();
      this.storage.removeItem(this.key);
      this.saved = null;
      this.pending = null;
    }
  }
  class StageOutbox extends MatchOutbox {
    constructor(storage, dataset, createId = () => crypto.randomUUID()) {
      super(storage, dataset, createId, "stage");
    }
    accept(receipt) {
      const request = this.pending;
      if (!request || receipt?.ok !== true ||
          ["request_id", "dataset_id", "snapshot_id", "song_id", "file_id", "content_hash",
            "decision_revision"].some(key => receipt[key] !== request[key]) ||
          receipt.track_hash !== request.content_hash || typeof receipt.added !== "boolean") {
        throw new Error("Staging acknowledgment is invalid. The exact request remains available to retry.");
      }
      this.clear();
    }
  }
  class ScanOutbox extends MatchOutbox {
    constructor(storage, dataset, createId = () => crypto.randomUUID()) {
      super(storage, dataset, createId, "scan");
    }
    accept(receipt) {
      const request = this.pending;
      if (!request || receipt?.ok !== true ||
          ["request_id", "dataset_id", "path"].some(key => receipt[key] !== request[key]) ||
          (receipt.root_id !== request.root_id || receipt.base_revision !== request.base_revision) ||
          typeof receipt.job_id !== "string" || !/^[a-f0-9]{32}$/.test(receipt.job_id)) {
        throw new Error("Scan acknowledgment is invalid. The exact request remains available to retry.");
      }
      this.clear();
    }
  }
  class PersonalOutbox extends MatchOutbox {
    constructor(storage, dataset, createId = () => crypto.randomUUID()) {
      super(storage, dataset, createId, "personal");
    }
    accept(receipt) {
      const request = this.pending;
      if (!request || receipt?.ok !== true ||
          ["request_id", "dataset_id", "crate_id", "base_revision", "name"].some(key => receipt[key] !== request[key]) ||
          receipt.revision !== request.base_revision + 1 ||
          !Array.isArray(receipt.entries) || receipt.entries.length !== request.entries.length ||
          receipt.entries.some((entry, index) => !entry ||
            ["id", "snapshot_id", "song_id"].some(key => entry[key] !== request.entries[index][key]))) {
        throw new Error("Personal crate acknowledgment is invalid. The exact request remains available to retry.");
      }
      this.clear();
    }
  }
  class StudioOutbox extends MatchOutbox {
    constructor(storage, dataset, createId = () => crypto.randomUUID()) {
      super(storage, dataset, createId, "studio");
      if (this.pending) this.validate(this.pending);
    }
    validate(request) {
      if (!request || !["/api/studio/approve", "/api/studio/rules/save",
        "/api/studio/history/record", "/api/catalog/scan", "/api/studio/batch/approve",
        "/api/studio/inbox/review", "/api/studio/monitor/save", "/api/studio/exclusion/save",
        "/api/studio/feedback/save", "/api/studio/assistant/chat", "/api/studio/ai/configure",
        "/api/studio/edit/approve", "/api/studio/edit/undo",
        "/api/studio/history/approve", "/api/studio/history/undo"].includes(request.endpoint) ||
        typeof request.request_id !== "string" || !request.request_id) {
        throw new Error("Retained Studio action is invalid. Preserve browser storage before manual recovery.");
      }
    }
    accept(receipt) {
      const request = this.pending;
      if (request?.endpoint === "/api/catalog/scan") return ScanOutbox.prototype.accept.call(this, receipt);
      let valid = !!request && receipt?.ok === true &&
        receipt.request_id === request.request_id && receipt.dataset_id === request.dataset_id;
      if (request?.endpoint === "/api/studio/approve") {
        valid = valid && receipt.proposal_id === request.proposal_id &&
          receipt.crate_id === "studio-" + request.proposal_id && receipt.revision === 1 &&
          Number.isInteger(receipt.entries) && receipt.entries > 0;
      } else if (request?.endpoint === "/api/studio/rules/save") {
        valid = valid && receipt.id === request.id && receipt.revision === request.base_revision + 1;
      } else if (request?.endpoint === "/api/studio/history/record") {
        valid = valid && receipt.file_id === request.file_id &&
          Number.isFinite(Date.parse(request.played_at)) &&
          Date.parse(receipt.played_at) === Date.parse(request.played_at);
      } else if (["/api/studio/history/approve", "/api/studio/history/undo"].includes(request?.endpoint)) {
        valid = valid && receipt.edit_id === request.edit_id &&
          receipt.operation === (request.endpoint.endsWith("/undo") ? "undo" : "approve") &&
          Array.isArray(request.event_ids) && request.event_ids.length > 0 && request.event_ids.length <= 500 &&
          new Set(request.event_ids).size === request.event_ids.length &&
          request.event_ids.every(id => typeof id === "string" && id.length > 0) &&
          Array.isArray(receipt.event_ids) && receipt.event_ids.length === request.event_ids.length &&
          receipt.event_ids.every((id, index) => id === request.event_ids[index]);
      } else if (request?.endpoint === "/api/studio/batch/approve") {
        valid = valid && receipt.batch_id === request.batch_id && Array.isArray(receipt.crates) &&
          receipt.crates.length > 0 && receipt.crates.length <= 8 && receipt.crates.every(crate =>
            crate.ok === true && crate.dataset_id === request.dataset_id &&
            crate.crate_id === "studio-" + crate.proposal_id && crate.revision === 1 &&
            Number.isInteger(crate.entries) && crate.entries > 0) &&
          receipt.entries === receipt.crates.reduce((sum, crate) => sum + crate.entries, 0);
      } else if (["/api/studio/edit/approve", "/api/studio/edit/undo"].includes(request?.endpoint)) {
        valid = valid && receipt.edit_id === request.edit_id &&
          receipt.operation === (request.endpoint.endsWith("/undo") ? "undo" : "approve") &&
          Array.isArray(receipt.crates) && receipt.crates.length > 0 && receipt.crates.length <= 8 &&
          new Set(receipt.crates.map(crate => crate.crate_id)).size === receipt.crates.length &&
          receipt.crates.every(crate => typeof crate.crate_id === "string" &&
            Number.isInteger(crate.base_revision) && crate.base_revision > 0 &&
            crate.revision === crate.base_revision + 1 && typeof crate.name === "string" &&
            Array.isArray(crate.entries) && crate.entries.every(entry =>
              typeof entry.id === "string" && typeof entry.snapshot_id === "string" && typeof entry.song_id === "string"));
        if (request.expected_crates) valid = valid && Array.isArray(request.expected_crates) &&
          receipt.crates.length === request.expected_crates.length && receipt.crates.every((crate, index) => {
            const expected = request.expected_crates[index];
            return ["crate_id", "base_revision", "revision", "name"].every(key => crate[key] === expected[key]) &&
              crate.entries.length === expected.entries.length && crate.entries.every((entry, position) =>
                ["id", "snapshot_id", "song_id"].every(key => entry[key] === expected.entries[position][key]));
          });
      } else if (request?.endpoint === "/api/studio/ai/configure") {
        valid = valid && receipt.model === request.model && receipt.revision === request.base_revision + 1;
      } else if (request?.endpoint === "/api/studio/assistant/chat") {
        valid = valid && receipt.conversation_id === request.conversation_id &&
          receipt.revision === request.base_revision + 1 && receipt.turn?.message === request.message &&
          typeof receipt.turn?.draft?.action === "string";
      } else if (["/api/studio/inbox/review", "/api/studio/exclusion/save",
        "/api/studio/feedback/save", "/api/studio/monitor/save"].includes(request?.endpoint)) {
        const field = request.endpoint.endsWith("monitor/save") ? "root_id" : "file_id";
        const state = request.endpoint.endsWith("monitor/save") ? "enabled" :
          request.endpoint.endsWith("inbox/review") ? "status" :
            request.endpoint.endsWith("feedback/save") ? "rating" : "excluded";
        valid = valid && receipt[field] === request[field] && receipt[state] === request[state] &&
          receipt.revision === request.base_revision + 1;
      } else valid = false;
      if (!valid) throw new Error("Studio acknowledgment is invalid. The exact action remains available to retry.");
      this.clear();
    }
  }
  if (typeof module !== "undefined" && module.exports) module.exports = {MatchOutbox, StageOutbox, ScanOutbox, PersonalOutbox, StudioOutbox};
  else Object.assign(root, {MatchOutbox, StageOutbox, ScanOutbox, PersonalOutbox, StudioOutbox});
})(globalThis);
