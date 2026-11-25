# Native ingestion inbox status review

Open Library → Detailed workspace → Review indexed folders and ingestion records. Each loaded record offers **Review inbox status…**. The sheet shows its exact file identity, relative path, current status and inbox revision. Choose New, Reviewed or Ignored and press **Save inbox status**; Close makes no change.

These are app-owned triage labels. Reviewed does not certify metadata, cleanliness, musical quality, timing or event suitability. Ignored neither deletes audio nor excludes a file from catalog search or set planning. Existing backend reconciliation returns changed indexed evidence to New. Filters still cover only the existing inbox’s maximum 100 prioritized loaded records.

The native client calls the existing `/api/studio/inbox/review` endpoint with exactly request_id, dataset_id, file_id, base_revision and status. It persists a typed pending request in the private native workflow journal before sending. Every retry uses the same request ID and fields. Receipt validation requires the exact dataset/request/file/status and base revision plus one. No alternate store or backend API is introduced.

An uncertain response remains pending across quit/relaunch. Open Library detail and use **Retry exact inbox status** to reconcile the retained request. No automatic replay occurs. Pending inbox review blocks other native durable approvals/edits, and their pending operations block a new inbox save. Recovery is also visible in planner/crate detail. Only explicit server rejection permits **Discard rejected inbox status**; discarding triggers an evidence refresh before a new review. A transport, receipt or persistence failure must be reconciled rather than replaced with a new request.

Existing journals without the optional inbox field remain readable. Do not switch to an older checkout while an operation is pending: reconcile it with this version first. The journal contains local paths and titles; keep it private and use the redacted support export for diagnostics.

No tags, audio, source folder associations, monitor settings, planner rules, crate memberships or external DJ libraries are changed by inbox review. There is no tag repair, batch triage, full-library pagination, duplicate keeper selection or automatic review. Use temporary generated audio and a separate macOS test account on the DJ laptop; the working Serato library is outside development scope.
