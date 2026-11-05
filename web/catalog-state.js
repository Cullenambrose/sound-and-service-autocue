(function(root) {
  class MatchOutbox {
    constructor(storage, dataset, createId = () => crypto.randomUUID(), namespace = "match") {
      this.storage = storage;
      this.dataset = dataset;
      this.createId = createId;
      this.label = namespace === "stage" ? "staging request" : namespace === "scan" ? "scan request" : "match";
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
          typeof receipt.job_id !== "string" || !/^[a-f0-9]{32}$/.test(receipt.job_id)) {
        throw new Error("Scan acknowledgment is invalid. The exact request remains available to retry.");
      }
      this.clear();
    }
  }
  if (typeof module !== "undefined" && module.exports) module.exports = {MatchOutbox, StageOutbox, ScanOutbox};
  else Object.assign(root, {MatchOutbox, StageOutbox, ScanOutbox});
})(globalThis);
