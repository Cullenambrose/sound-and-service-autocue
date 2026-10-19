# Sound & Service AutoCue

Library-prep engine that analyzes DJ tracks and suggests Serato cue points (A–H layout) plus two clean 4-bar loop ideas. It is a **prep app**, not a booth player: run it on the library before a gig, review the picks, then (later) write approved cues into Serato.

A native Mac app (Xcode) is planned once the logic is proven. Per-song analysis JSON (see `docs/ANALYSIS-FORMAT.md`) is designed so **Booth Buddy** can read the same results later without coupling the two codebases.

Private repo: `Cullenambrose/sound-and-service-autocue`. Separate from the booking site (`dj-av-booking`) and the iOS app.

## Cue layout (approved)

| Slot | Name | Rule | Auto-set? |
|------|------|------|-----------|
| A | 8BS / 4BS / 2BS / 1BS | 8 bars (32 beats) before START; step down to 4 → 2 → 1; empty only if 1 bar will not fit | yes, conf ≥ 0.85 |
| B | START | first downbeat of the vocal song proper | yes, conf ≥ 0.85 |
| C | 1st Chorus | first chorus ≥4 bars after START | suggestion only |
| D | Last Chorus | last chorus starting before EXIT | suggestion only |
| E, F | — | always empty | — |
| G | GO | 32 beats before EXIT (16 allowed) | yes, conf ≥ 0.85 |
| H | EXIT | bar where the last chorus actually ends | yes, conf ≥ 0.85 |

Saved loops (suggestions only): intro loop 4 bars from A (or first clean beats-only 4 bars before vocals); outro loop 4 bars from GO (or first clean beats-only 4 bars after EXIT). Phrase-aligned (bar 1 of phrase).

Full rules: `docs/cue-logic.md`, `docs/SPEC.md`.

## Layout

```
engine/           Python/shell tools (rules, scorers, Gemini step, learners, stress5/)
review-ui/        Local review page (index.html + serve.py)
web/              Local web library, crate browser, review panel, and backup-gated MP3 writes
docs/             Specs, scores, install notes, Google Serato handover
config/           Learned profiles + genre bucket params (no keys)
```

## How to run (high level)

1. Install analyzers per `docs/INSTALL.md` and `docs/SONGFORMER-ON-MAC.md` (SongFormer needs disk space on the Mac).
2. Set `GEMINI_API_KEY` in the environment when you want AI suggestions on low-confidence cues. Never commit the key.
3. Source `engine/env.sh` (adjust `PROOF` to your checkout) and run the pipeline scripts under `engine/` (e.g. feature extract → `rules_v5.py` / build → review).
4. Start the local web library and review interface:

```bash
./run.sh
```

Open `http://127.0.0.1:8770`. The app keeps its SQLite library, staged audio copies, jobs, and corrections in the ignored `web-data/` directory. It binds only to loopback. Music is not uploaded; dragged/picked files are copied to the local app for analysis and their originals are left in place. Paste a local folder path or use the crate picker to link originals for Finder access and Serato writes.

Install the small Serato-tag dependency once to enable backed-up writes:

```bash
python3 -m pip install -r web/requirements.txt
```

The crate browser reads Serato `.crate` files under `~/Music/_Serato_/Subcrates` and mounted `/Volumes/*/_Serato_/Subcrates` folders without editing them. Serato writes are MP3-only, require a locally linked original, require a saved review, and require Serato to be closed. Each batch first zips discovered `_Serato_` folders and exact original MP3 files to `~/Music/AutoCue Backups/`; the app verifies the archive and hashes before writing. Undo restores the exact originals only when files still match the post-write hashes. The Serato `_Serato_` folders are backed up but are not changed by this app.

**Fresh-audio processing is not yet wired to the web job runner.** The v6 planner consumes cached v5 analysis; the repository has no portable one-file pipeline that turns a newly imported arbitrary file into that cache. Until that integration is added, queued jobs report this limitation in the track row rather than inventing cues. The runner can invoke an explicitly configured `AUTOCUE_PROCESSOR` argv command; each command argument may contain `{input}`, `{output}`, or `{hash}` placeholders, and the processor must write v6 JSON with a `cues` object to `{output}`. This is an integration seam, not a built-in engine command.

Corrections stay local in `web-data/corrections.json` (the standalone review UI's `corrections.json` is also gitignored). Feed the marks back through the learner scripts to tune the profile in `config/`.

## Ground truth

**Do not** treat the old Serato Markers2 on the 20-song test set as Cullen’s hand cues — those lined up with Mixed In Key energy cues. Scoring and learning use **Cullen’s review-page marks** (`corrections.json` → `learner_marks.py` → `config/learned_profile_marks.json`). See `docs/STATUS.md`.


## v5 (switchable improvements)

`engine/rules_v5.py` post-processes v4 (`v4_go32`); each change can be turned off with `--off a,b` or tested alone with `--only a`. Details and ablation: `docs/SCORE-v5-2026-10-15.md`.

| # | Change | What it does |
|---|--------|----------------|
| 1 | `loops_phrase` | Intro/outro loops only on phrase-aligned bars (4k from START/EXIT) + clean-beat stem check |
| 2 | `exit_phrase` | EXIT snaps to 8- then 4-bar grid from START; G = EXIT − 8 bars; flag if no nearby boundary |
| 3 | `chorus_verify` | Keep C/D only if section recurs (chroma/MFCC or lyric line); else low-conf suggestion |
| 4 | `drop_robust` | EXIT drop needs drums+bass down for a whole bar (not a one-frame dip) |
| 5 | `tempo_check` | Flag tempo drift / multi-BPM grids; rebuild bar times from detected beats when unstable |
| 6 | `cold_start` | Vocals+drums in bar 1 → deliberate cold start (A empty + flag); vocals-only → a cappella intro |
| 7 | `consensus` | Bump or cut START/EXIT confidence when allin1 / SongFormer / stems / energy agree or disagree |
| 8 | `genre_params` | Bucket (pop / hiphop_rnb / house_edm / latin / other); params in `config/genre_params_v5.json`; tune with `learner_marks.py --buckets` |

**Score vs Cullen’s marks (18 cues / 4 songs):** v4 and v5 are tied at **9/18**. v5 auto-sets fewer cues and adds review flags. **v4 stays the default** until there are ~50+ stress-crate marks, then re-score and decide. Roadmap: `docs/ROADMAP.md`.

## Status

See `docs/STATUS.md` for current scores, the v5 ablation, and the 5-crate stress run.

## v6 (separate, switchable plan)

`engine/rules_v6.py` reads cached v5 analysis and writes a separate eight-cue DROP or VOCAL plan. The switch defaults and genre phrase lengths are in `config/genre_params_v6.json`. It does not change v5 defaults or write to Serato/audio. Synced LRCLIB lookups and WhisperX word timings are cacheable from a manifest; Gemini is an explicit opt-in (`engine/gemini_v6.py --enable`) and is disabled by default. Review data can be built beside v5 with `engine/build_v6_review.py`; usage and scoring are in `docs/ANALYSIS-FORMAT.md`.


## Fresh-audio pipeline (new songs, no cached analysis)

`engine/analyze.py` takes one audio file and produces a v6 plan with no manual steps. The web app calls it for every imported song, so a dropped MP3 goes straight to **Cues ready** or **Needs a look**.

Steps (the chips on the page): Beats (Beat This) › Sections (all-in-one) › Vocals (Demucs stems) › Lyrics (WhisperX word timing + LRCLIB synced lyrics) › Drops (kick/bass energy) › Cues. Results are cached by content hash in `~/Library/Application Support/AutoCue/cache/<hash>/` using the v5 cache layout, so nothing is analyzed twice (override with `AUTOCUE_CACHE_DIR`).

- **Install:** `./setup.sh` (Apple Silicon; creates `.venv`, uses MPS where supported). Check what is present with `.venv/bin/python engine/analyze.py --check`. It needs Python 3.10+ (macOS's built-in 3.9 is too old for Beat This) and ffmpeg.
- The installer also pins NumPy 2.1–2.2 and SciPy 1.14.1: SciPy 1.15's PROPACK binary fails to load on this macOS 27 runtime. `madmom` is built with Cython; allin1's older NATTEN and NumPy APIs are adapted at runtime, so no system Python packages are patched.
- **Disk / downloads:** about 3.5 GB of packages, plus first-run models: Beat This ~80 MB, Demucs ~80 MB, WhisperX ~0.6 GB, all-in-one ~0.2 GB. Roughly 5-6 GB total without SongFormer.
- **Missing analyzers:** the page and CLI name the analyzer and the install command. Without the beat model there is no bar grid, so the song fails with that message. Without Demucs, all-in-one, or WhisperX the step is skipped, the plan is flagged, and cue confidence is capped (0.55 without sections or stems, so the song lands in "Needs a look"; 0.9 without word timings). Cues are never invented.
- When no outro section is labeled, a sustained energy or vocal fade in the last 32 bars can supply lower-confidence **OUTRO** and **GO -32** cues; otherwise those cues remain blank. The C/D fallback labels follow the selected map, and legacy `CHORUS` labels are replaced rather than written.
- **Speed:** a 3-minute synthetic click track on this Apple Silicon Mac (MPS) took about 9 s for beats and about 73 s for Demucs, roughly 87 s total before all-in-one and WhisperX. A real 3:39 song required about 116 s for beats, 83 s for Demucs, 140 s for WhisperX (including model load), and 121 s for all-in-one sections on the first run; later runs reuse the hash cache. These are one-song observations, not a performance guarantee. Batches run one song at a time. Demucs on MPS is given a time limit (`AUTOCUE_DEMUCS_TIMEOUT`, default 600 s) and then retried on the CPU.
- Beat This retries on CPU if MPS reports out-of-memory; it does not disable PyTorch's MPS memory limit. Downbeat fitting rejects local false/duplicate detections before building the beat grid.
- **SongFormer license:** SongFormer's MuQ weights are licensed for non-commercial use only. They are optional and not installed by `setup.sh`; the pipeline runs fully without them, and a sellable version must keep them off.

## Algorithm experiments (compare ideas on your own songs)

Every analysis keeps the baseline v6 plan plus one plan per switched-on experiment, in `plan.variants`
(see `engine/experiments.py`): low-band timing fix, fuzzy lyric matching, kick-snapped grid, stem-energy
sections, and optional RoFormer vocals (slow; `pip install audio-separator`). Per-bar stem energies are saved
in `plan.bar_energy`. In the review panel, toggle variant lanes and click a variant's time to use it. Save your
review; saved cues are the answer key. **Experiments** in the top bar scores each variant (within one beat),
split by VOCAL and DROP, with fixes and breaks vs baseline, and exports everything as JSON. Variants only exist for
songs analyzed after an experiment is on, so re-analyze older songs. `rapidfuzz` is used for fuzzy matching when
installed, with a `difflib` fallback. Genius lyrics are not included: they need an API key and have no timestamps.

## Serato writes

Writes need a saved review, Serato closed, and a verified backup of the `_Serato_` folder and each song's original file. Supported formats: MP3, AIFF (ID3 `Serato Markers2`), FLAC (`SERATO_MARKERS_V2`) and M4A (`----:com.serato.dj:markersv2`). Other formats show "Write not supported yet". Foreign marker entries (loops, cues 9+) are preserved and the audio data is hash-checked before and after. **Undo this batch** restores one write; **Restore everything** (typed `RESTORE`) restores the song files and the `_Serato_` folder from a backup. FLAC and M4A writing follows the published Serato tag layout and is verified by read-back and audio-hash checks, but has not been opened in Serato itself.

Real `.crate` copies can be placed in `tests/fixtures/crates/` (gitignored); the tests parse them read-only when present.
