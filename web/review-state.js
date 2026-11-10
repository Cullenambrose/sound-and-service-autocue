(function (root) {
  class ReviewDraft {
    constructor(storage, dataset, hash, revision, newId) {
      this.storage = storage;
      this.dataset = dataset;
      this.hash = hash;
      this.newId = newId;
      this.key = `autocue:review:${dataset}:${hash}`;
      const saved = storage.getItem(this.key);
      this.serialized = saved;
      this.record = saved ? JSON.parse(saved) : {base_revision: revision, cues: null, pending: null};
      if (!Number.isInteger(this.record.base_revision) || this.record.base_revision < 0 ||
          (this.record.cues !== null && typeof this.record.cues !== "object")) {
        throw new Error("Stored review draft is invalid. Download browser storage before clearing it.");
      }
    }
    save(cues) {
      this.record.cues = cues;
      this.write(this.record);
    }
    checkOwnership() {
      if (this.storage.getItem(this.key) !== this.serialized) {
        throw new Error("Another tab changed this draft. Download your edits before reopening the saved draft.");
      }
    }
    write(record) {
      this.checkOwnership();
      const serialized = JSON.stringify(record);
      this.storage.setItem(this.key, serialized);
      this.serialized = serialized;
    }
    prepare(cues, mapType) {
      if (!this.record.pending) {
        this.record.pending = {
          request_id: this.newId(), dataset_id: this.dataset,
          base_revision: this.record.base_revision, cues, map_type: mapType,
        };
      }
      this.save(cues);
      return this.record.pending;
    }
    accept(receipt, cues) {
      const pending = this.record.pending;
      if (!pending || receipt.ok !== true || receipt.request_id !== pending.request_id ||
          receipt.dataset_id !== this.dataset || receipt.track_hash !== this.hash ||
          !Number.isInteger(receipt.revision) || receipt.revision < pending.base_revision ||
          !Number.isInteger(receipt.sequence) || receipt.sequence < 1) {
        throw new Error("Save acknowledgment did not match. Draft retained; retry safely.");
      }
      const next = {base_revision: receipt.revision, cues, pending: null};
      this.write(next);
      this.record = next;
    }
    clear() {
      this.checkOwnership();
      this.storage.removeItem(this.key);
      this.serialized = null;
      this.record.cues = null;
      this.record.pending = null;
    }
  }
  root.AutoCueReview = {ReviewDraft};
  if (typeof module !== "undefined") module.exports = root.AutoCueReview;
})(globalThis);
