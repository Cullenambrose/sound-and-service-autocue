# Pinned contracts (copies, do not edit here)

The master copies live in the booking site repo, `cullenambrose/dj-av-booking`, under
`docs/contracts/`. The human-readable spec is `docs/contracts/event-sync-v1.md` there.

| File | Contract | Pinned from |
|---|---|---|
| `event-sync-v1.schema.json` | Event sync 1.0.0 | dj-av-booking `5ea83fd` (branch `claude/sync-contract-agents-md-15otma`; re-pin to `main` once merged) |
| `fixtures/events-response.example.json` | Sample feed that must parse | same |
| `fixtures/matcher-cases.json` | Shared same-song cases | same |

To change a contract, open a PR in dj-av-booking first, then update these files byte for byte
in a linked PR here and change the "Pinned from" line. A check that the copies match the site
(`diff` against a fetched copy) belongs in CI once the app is on `main`.
