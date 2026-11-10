# Supported Python worker

## Scope and implementation plan

This follows the transactional review foundation on
`dev/personal-dj-foundation`; it does not replace the cue algorithms.

1. Package only the supported worker and its dependency closure.
2. Keep the existing function/legacy CLI usable; wrap it with a versioned job
   contract and explicit opt-ins for network text lookups and Gemini.
3. Serialize same-track cache use, isolate temporary work and preserve completed
   artifacts on failed writes/cancellation.
4. Prove installation outside the checkout with generated audio and declared
   beats. Keep the `cf38ec3` cue baseline and existing regressions.

`pyproject.toml` owns core dependencies and optional analyzer groups.
`engine/autocue_worker.py` is the worker entry point; `engine/analyze.py` remains
the analysis engine. The wheel deliberately installs a curated list of flat
modules, matching existing imports, and bundles the default genre configuration
as `autocue_config`. It excludes the web server, tests, research scripts, audio,
credentials and model weights. A namespaced package migration is deferred.
Historical experiments and their entry points remain in the checkout.

## Install and smoke test

Python 3.10+ on macOS/Linux; advisory locking currently uses POSIX `fcntl`.
Windows support is not claimed.

```bash
python3 -m venv /path/to/worker-env
/path/to/worker-env/bin/python -m pip install .
/path/to/worker-env/bin/autocue-worker check
/path/to/worker-env/bin/autocue-worker fixture --out /tmp/autocue-fixture-result.json
```

Core dependencies are NumPy, SciPy and Mutagen. The generated fixture uses
44.1 kHz PCM WAV, explicit beats and rules only: no FFmpeg, models, lyrics lookup,
Gemini, original music or tag writes. It checks wiring and degraded cue behavior,
not beat detection or real-world accuracy. Its eight bars are
`A=null, B=0, C=null, D=null, E=null, F=null, G=16, H=24`, at two seconds per bar.
Existing rules place H at the fixture's duration boundary; this milestone does
not retune that behavior or certify export eligibility.

Optional groups describe the current analyzer families:

```bash
/path/to/worker-env/bin/python -m pip install '.[beats]'
# Other separately selectable groups: stems, words, sections
```

Do not install these just to run synthetic tests. Model downloads and full
inference are not part of this milestone's acceptance test. `local` analysis
normally needs FFmpeg plus Beat This; missing optional analyzers retain existing
degraded flags/confidence caps, while unavailable required beats fail explicitly.
Apple Silicon Torch/NATTEN/WhisperX compatibility still requires the existing
`setup.sh` / installation documentation. Extras are dependency declarations,
not proof that every heavy combination works. `check` reports discoverability
and package versions, not model readiness or compatibility.

## Schema-v1 request

`autocue-worker run --request /absolute/request.json --out /absolute/result.json`
reads one job:

```json
{
  "schema_version": 1,
  "job_id": "an-explicit-caller-job-id",
  "profile": "local",
  "source": "/absolute/disposable/song.mp3",
  "cache_dir": "/absolute/disposable/cache",
  "artist": "Synthetic artist",
  "title": "Synthetic title",
  "genre": "pop",
  "experiments": ["baseline"],
  "allow_lyrics": false,
  "gemini": false
}
```

Required: schema version, nonempty job ID (up to 128 characters), source and
profile. Unknown fields/versions fail. All input/cache paths must be absolute
(a leading `~` is expanded). Optional `track_hash` checks expected exact-file
SHA-256 before analysis; source identity is checked again after analysis.
Optional `config_path` replaces the bundled/checkout genre configuration.
Optional `cue_maps` uses the existing versioned cue-map validator; experiment
IDs use the existing experiment registry.

Profiles:

- `local`: current model pipeline. Worker CLI defaults text lookups and Gemini
  **off**. Explicit `allow_lyrics: true` permits artist/title queries to LRCLIB;
  explicit `gemini: true` invokes existing Gemini controls. Model libraries may
  still download weights; this profile is not a network sandbox.
- `prepared`: requires `beats_path` pointing to an object containing `beats`
  and `downbeats` arrays, each with at least four finite, nonnegative, strictly
  increasing seconds, strictly inside decoded duration. Only baseline rules are
  allowed; optional analyzers, lyrics lookup and Gemini are disabled. Non-PCM
  input still requires FFmpeg. These are externally declared observations, not
  model predictions; grid source is `prepared_downbeats` unless an existing
  Serato grid is reconciled by the unchanged engine.

The web app uses `local` through the same function and adapts progress to its
existing live display. It preserves existing web settings, including LRCLIB
text lookup and the saved Gemini toggle; packaging does not silently change
those settings. No AI call was made for implementation or verification.

## Messages, results and failure

Stdout is JSONL; diagnostics go to stderr. Each valid job starts with:

```json
{"schema_version":1,"job_id":"example","sequence":1,"kind":"job","status":"running","data":{"profile":"local"}}
```

Progress messages have `kind: "progress"`, statuses
`running/done/skipped/failed`, and data `{step, detail}`. An optional analyzer
step failure can still yield a degraded plan. Terminal **job** messages have
status `completed`, `failed` or `cancelled`; sequence increases within a job.
Invalid requests rejected before job admission have nonzero exit and stderr,
not a fabricated job receipt. Do not interpret a progress-step status as a
terminal job outcome.

Successful result shape:

```json
{
  "schema_version": 1,
  "job_id": "example",
  "status": "completed",
  "track_hash": "exact-file-sha256",
  "plan": {"cues": {}, "provenance": {}}
}
```

The real plan retains all existing cue/grid/variant fields. Provenance includes
schema/engine version, exact-file identity, profile, effective settings hash
(including metadata), prepared-beats hash when applicable, Python and installed
dependency versions. It does **not** fingerprint model weights, previously
cached analyzer artifacts, or mutable remote lyric/AI responses. It is not a
complete reproducibility claim for model-backed runs. Timing fields also vary.

The CLI fsyncs a unique sibling temporary file and atomically replaces the
requested JSON before announcing completion (including `result_path`). Output
must be distinct from source/request/beat/config/project inputs. Write failures
never announce success. Previous successful output remains on failure, so
callers must match job ID/track hash and terminal acknowledgment, not merely
check that an output file exists. Exit codes: 0 completed, 1 failed, 130
cooperatively cancelled. There is no durable job-ID receipt journal in this CLI:
rerunning a request recomputes/reuses caches rather than replaying a receipt.

The Python `run()` function returns the envelope and calls the message callback;
its completion means a plan was returned, **not** that the caller persisted it.
The web server remains responsible for SQLite persistence.

## Cache and cancellation boundaries

- Local caches retain `<cache>/<exact-file-hash>/...` for compatibility.
  Prepared observations use
  `<cache>/prepared-v1/<beats-hash>/<exact-file-hash>/...`, so synthetic features
  cannot pollute normal model analysis.
- `analysis.lock` serializes writers for the same cache/track using `flock`.
  Other tracks can run independently. Historical scripts that bypass this
  worker do not participate in locking; do not run them against active caches.
- Each job owns a unique `job-*` directory; normal completion, failure and
  cooperative cancellation clean only that directory. Completed cache features
  and stems use atomic NPZ replacement. Decoded vocal output is promoted from
  the workspace only after FFmpeg succeeds.
- `vocals16k.wav` now persists at the track-cache root. Older shared `work/`
  directories are not removed automatically; an old cached vocal may need one
  extraction into the new location. Full temporary stems are not retained.
- Kill/power loss can leave abandoned job/temp files. There is no automatic
  scavenger or multi-file transactional cache snapshot. Old damaged cache files
  are not repaired by this change. Preserve evidence and remove only the
  identified disposable track cache if regeneration is needed.
- Cancellation is checked while waiting for locks and at analysis boundaries,
  before returning a plan and before CLI/web persistence. SIGINT/SIGTERM set a
  cooperative flag; long inference/subprocess calls can delay response.
  This is **not immediate process-tree termination**. An accepted cancellation
  before persistence preserves the previous web plan/review state and does not
  count the active track completed. A request after commit cannot undo a save.
- The legacy `AUTOCUE_PROCESSOR` subprocess cannot be interrupted mid-call here;
  cancellation is checked before storing its returned result. The web batch
  loop stops subsequent tracks. Pausing remains between tracks.

## Verification and resume

```bash
python -m pip install -r requirements-test.txt
python -m unittest discover -s engine -p 'test_*.py'
python -m unittest web.test_server
node --test web/test_*.cjs

# Use a separate environment for a non-editable install:
python -m pip wheel --no-deps . --wheel-dir /tmp/autocue-wheels
/path/to/clean-env/bin/python -m pip install /tmp/autocue-wheels/*.whl
/path/to/clean-env/bin/python scripts/verify_worker_install.py
```

The installation verifier rejects checkout/editable imports and invokes the
installed console script twice from a temporary directory, with project/AI
environment overrides removed. It checks sequential JSONL, result identity,
degraded confidence, exact cue bars and repeatable provenance. CI adds this
wheel gate on Python 3.10/3.12 without optional models/credentials.

Regression tests cover isolated cache reuse, source identity changes,
same-track serialization, independent tracks, lock-wait cancellation,
interrupted writes, terminal-message accuracy and preservation of reviewed web
tracks on cancellation. The `cf38ec3` rules baseline remains unchanged.

Final local verification: **345 engine, 51 web and 25 Node cases passed**.
A separate clean Python 3.10 environment installed the built wheel with only
core dependencies, passed the outside-checkout fixture verifier, and passed all
Python suites (three optional model-compatibility checks skipped).

Not verified: a fresh full-model installation, real music accuracy/listening,
actual Serato round trips, immediate process termination, Windows, physical
power loss, or hosted CI (nothing was pushed). No original music or production
database was changed.

Next: synthetic catalog snapshots plus read-only selected-folder indexing and
explicit exact/ambiguous/missing version matches. Keep catalog entities separate
from physical file hashes. Do not add AI or tag-writing/export changes until
those operations are reliable.

Update: that first catalog slice is implemented; see the [catalog handoff](CATALOG.md)
for its demo and remaining real-export/job-infrastructure acceptance gates.
