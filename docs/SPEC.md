# Auto hot-cue tool for Cullen Ambrose: final spec and build plan

*Revised Thu Oct 8, 2026 (PT).*
*Status: **Approved by Cullen at 2:08 PM PT.** Phase 1 (free proof, box only) may start. No tag writes, no laptop installs, no spend. He still picks the 20–30 tracks.*

## For Cullen

1. The tool drafts your A–H cues (same slot, color, and label as your cue-logic sheet). You still check every track.
2. H is `go`: on the outgoing song, exactly 16 bars before Purple. Color is locked: H is bright green (you see #1FAD26). The intro cue A is dark green (you see #006838).
3. Step 1 is a cheap proof. Two free analyzers (allin1 and SongFormer) run on copies of 20–30 songs you've already cued, and the one that scores better is kept.
4. Only if that proof passes do we build the top-tier version: stronger models that cross-check each other.
5. Every cue gets a confidence score. If the tool isn't sure, it leaves that slot empty and flags it. A wrong cue is worse than a missing one.
6. Scoring is per cue, for every slot A–H: correct, within 1 bar, wrong, or correctly left empty.
7. Pink lyric labels are only suggestions you confirm, and F (scratch) stays yours. Expect 30–60 seconds of review per track.
8. Copies only, in `~/Music/_CueTest-2026-10/`. Your originals, Wedding Sets, and Serato library are never written.
9. The proof costs $0 in software. Nothing gets installed on your laptop, and nothing gets bought (apps, GPU, paid AI) without your OK.
10. You approved this on Oct 8 at 2:08 PM PT. Colors are locked. Blue is `last 16` or `last 8`, Purple uses `cold` on cold endings, and the bail-out loop is 4 or 8 bars.

---

## 0. Sources and status

| Source | Status | Used for |
|---|---|---|
| `/workspace/dj-tutor/cue-logic.md` (DJ Tutor, Oct 8 2026) | Read fully. **Source of truth for all cue logic.** It supersedes the cue table in `30-day-plan.md` (incl. "H = spare") | Every placement and label rule in §3 |
| `/workspace/research/dj-auto-cues/RESEARCH-SPEC.md` (Research Bot, Oct 8 2026, ~1:20 PM PT) | **Present. Read fully and folded in.** Marked **[RS]** | Model choices, licenses, published numbers (its F-IDs), writer risks, eval method, proposed ship bar |
| `/workspace/research/dj-auto-cues/README.md` (feasibility study, Oct 8 2026) | Read. **Safety rules carried over unchanged.** Marked **[README]** | Safety protocol, backup/hash script, tool comparison |
| `/workspace/dj-tutor/30-day-plan.md` | Read | Day 3 hand-mapped set (scheduled Sat Oct 10, 2026) |
| `/workspace/dj-tutor/cue-card/README.md` | Read | Serato stored/displayed color hex values |

**Stale note in RS:** RESEARCH-SPEC §0 and §8 still list H as undecided (`go` vs spare). That's out of date. **Cullen agreed H = `go`** (cue-logic.md). This spec supports only `go`, and the spare mode is removed.

**Number labels:** every number is either from a cited source ([RS] F-ID or [README]) or measured on the box today ([measured]). Anything else is labeled **PROPOSAL**, **estimate**, or **UNKNOWN**. No benchmark numbers were invented.

---

## 1. Goal

Draft Cullen's A–H hot cues, following `cue-logic.md` exactly, as a **reviewable first draft**. Cullen accepts, nudges, or blanks each cue. Only accepted cues are written, and only to copies. When the tool isn't confident, it **leaves the slot empty and flags it** (cue-logic edge case 9).

**Not a goal:** hands-off cueing, rewriting his library, picking scratch samples (F), using transcribed lyrics as truth, or replacing Serato's grid.

**What accuracy to expect [RS]:**
- Boundaries within ±0.5 s on SongFormBench-Harmonix: **SongFormer 0.703, allin1 0.596**. Label accuracy (ACC): **0.807 vs 0.740** (arXiv 2510.02797; F14–F16).
- Downbeat F1 on Harmonix: allin1 0.915 (F2), Beat This! 0.907 (F5). Different setups.
- **Per-cue accuracy for "first chorus", "last chorus", "end of last chorus", "intro end", or "first vocal onset" is UNKNOWN.** No paper reports it, so we measure it on his tracks (§6).

---

## 2. Non-negotiable safety rules

These apply to every phase and every build agent. The code enforces them, and they're also written here. **A build that can break any of them is a failed build.**

1. **Never write to original files**, `database V2`, `master.sqlite`, or any existing Serato crate file. **Never touch Wedding Sets** (crates, tracks, or their files).
2. **New crates only under `NEW 2026-10`.** Pilot crate: **`NEW 2026-10 – Cue Test`**. The tool never creates or edits crate files. A person creates crates in Serato's UI.
3. **Copies only, in `~/Music/_CueTest-2026-10/`, with an `[autocue]` filename suffix.** The tool refuses any path outside that folder. It resolves symlinks and `..` first. It also refuses any file whose name lacks `[autocue]`. Ground-truth copies live in `~/Music/_CueTest-2026-10/_groundtruth/` and are **read-only** to the tool: never written, never put in a crate [RS §4.2].
4. **Skip any file that already has cues:** any `CUE`, `LOOP`, or `FLIP` entry in `Serato Markers2` **or** legacy `Serato Markers_` (and the FLAC/M4A equivalents) means skip and log [RS]. Never merge with or overwrite existing cues.
5. **Back up first, with Serato closed:** `~/Music/_Serato_`, and on Serato 4, `~/Library/Application Support/Serato/Library`. Also back up the `_Serato_` folder on any external music drive. A backup on a different disk is preferred [README].
6. **SHA-256 the originals before and after.** They must stay byte-identical, with sizes and mtimes unchanged (the README's "Exact setup and byte-identical check").
7. **Nothing installed on Cullen's laptop, and no spending** (MIK, Lexicon, rekordbox, cloud GPU, paid AI APIs) without his explicit OK.
8. **Do not buy or use MIK, Lexicon, or rekordbox for this.** MIK cue export and Lexicon Full Sync rewrite or delete library data [README].
9. No library-wide Rescan ID3 Tags, no Analyze with "Set BPM/Beatgrid" outside the test crate, and **never import or call the serato-tools crate, smart-crate, database, or USB-export code** [README, RS §6.2].
10. **Dry run by default.** Writing needs an explicit write flag **plus** the hash of the CSV Cullen approved. If the CSV changed after approval, the write is refused.
11. **Write before import:** tags are written only while Serato is closed and only before the copies are added to Serato [README, RS §6.3].
12. **Moving copies to the box, or to any third party (cloud GPU, AI API), is a file transfer that needs Cullen's OK.** Box copies are deleted after each phase unless he says to keep them. **No copyrighted audio goes to any third-party API without his OK** [RS §5].
13. **No model in the write path.** Tags are written by deterministic code only [RS §5].

---

## 3. Cue logic (encoded from `cue-logic.md`)

**AGREED** items are fixed in code. **PROPOSED** items are config settings: the default picked here matches DJ Tutor's recommendation, and **Cullen can change any of them** before or after the proof.

### 3.1 Prerequisites (AGREED, apply to every cue)

- **Grid:** snap every cue to the **`Serato BeatGrid` already on the copy** [RS]. That's the same grid Cullen sees on screen and on the CDJ-3000s. **Fallback grid only** (no Serato grid on the copy): **Beat This!** (MIT code and weights) [RS]. Cues placed on a fallback grid get a `grid:fallback` flag and lower confidence.
- **Bars and phrases:** 1 bar = 4 beats. Every cue except pickups (A) sits on beat 1 of a bar. Structural cues (A, B, C, D, G, H) sit on phrase lines.
  - **PROPOSAL:** a phrase line is a downbeat a multiple of 4 bars from B (for H, counted back from D). A structural cue off that grid stays where the music says but gets flagged.
  - **Exception:** F is a transient, not a bar start. The prerequisite text doesn't carve it out; that's a wording gap for DJ Tutor, and it doesn't matter because F is never auto-written.
- **Track-level stop flags** (all cues for the track get lower confidence and the track is flagged): no Serato grid and a fallback grid that's not steady, tempo drift, or a meter other than 4/4.

### 3.2 Section mapping (which segment counts as "the chorus")

- **"Chorus block"** = the chorus in pop, the **hook** in hip-hop (edge case 3), the **drop** in EDM (edge case 4). Engines label these `chorus`.
  - If the engine finds no `chorus` but a high-energy `inst`/drop segment follows the intro, it's used as the drop and flagged [RS §2.3].
- **Consecutive chorus segments are merged into one block.** That handles the double last chorus (edge case 6).
- **No chorus block at all** (edge case 5): C and G stay empty.
- **Known engine failure modes [RS §2.3]** get flagged:
  - a final repeated chorus labeled `outro`
  - a post-chorus or drop labeled `chorus`
  - a pre-chorus folded into `verse` (SongFormer has a pre-chorus label, which helps)

### 3.3 Slot rules

| Slot | Color (status) | Placement rule | Label rule | Status per cue-logic |
|---|---|---|---|---|
| **A** | Green | Intro length L = bars from the intro's first downbeat to B. **L ≥ 16:** A = exactly 16 bars before B (also for 32-bar DJ intros, with no extra cue; edge case 8). **L < 16:** A = the intro's first downbeat. **Pickup intro** (starts mid-bar): A sits on its first beat. **Never move A to fake a 16-bar intro** (edge case 2) | L ≥ 16: `16`. L < 16: the real count (`8`, `4`, `2`, `1`; a non-standard count like `12` is written as the number and flagged). Pickup: `the N` (the beat it starts on, e.g. `the 3`) | AGREED (pickup label from Spinelli) |
| **B** | Yellow | First beat of the song proper: the start of the first non-intro segment (EDM: the end of the DJ intro). **Refinement:** if a sustained vocal starts within ±1 bar of that boundary, use the downbeat at or just before the vocal [RS §8]. **This is the only place vocal separation is used** | `start` | AGREED |
| **C** | Blue | First beat of the **last chorus block** (last hook in hip-hop, even if the song ends on a verse; last drop in EDM). Double last chorus: the start of the final block (edge cases 3, 4, 6) | **Default (PROPOSED): `last N`**, N = bars in that block, counting the whole block for a double chorus. Normally `last 16` / `last 8`; other lengths are written as the real count and flagged. **Fallback mode: `last chorus`** | Placement AGREED. Label PROPOSED |
| **D** | Purple | Beat right after the last chorus block ends (outro start). **Hip-hop ending on a verse:** end of the last hook, so mixing out skips that verse. **DJ edits:** end of the last chorus, not the end of the DJ outro. **Cold ending:** the beat where the music stops. **Fade-out ending:** the phrase line where the fade begins. **No chorus:** outro start, or the last clean phrase line if there's no outro (flagged) | `out`. Cold ending: **`cold` (PROPOSED, default ON)** | AGREED (`cold` PROPOSED) |
| **E** | Red | Bail-out loop: a clean, steady 4- or 8-bar section, ideally drums + groove with no vocals, starting on a phrase line. **Locked Oct 8:** 4 or 8 bars, whichever fits. Try the intro first (from A, if the intro has room and A isn't a pickup), then the outro (from D, if the outro has room and the ending isn't `cold`). Neither fits: leave empty + flag. **"No vocals" is checked by Cullen at review** (separation is only used for B) | `loop` | AGREED. Length locked as 4 or 8 |
| **E saved loop** | Red | **Default (PROPOSED): ON.** Also write a Serato saved loop (Markers2 `LOOP` entry) with the same start and length. Loop end = start + N bars on the grid. Saved-loop slot **1 by default** (config). Serato 5 Cue Loops aren't used (5.0 is beta, not for gigs [README]) | `loop` | AGREED (it's in the rule text). Writer support has to be verified (§4.4) |
| **F** | Orange | **Never auto-written; always empty in the output.** Cullen sets it by hand on the transient of one clean one-shot, or leaves it empty if there's none | (his) `snare` / `hey` / short lyric | AGREED |
| **G** | Pink | First beat of the **first** chorus block (first hook / first drop). **EDM:** if the intro goes straight into the first drop, B and G sit on the same beat and **both are set** (edge case 4). **Single chorus block:** C and G land on the same beat; both are set and flagged `single chorus` (PROPOSAL; cue-logic doesn't cover this) | **Written default: `hook`.** The lyric label (first 2–3 words of the hook, **max 10 characters**, trimmed at a word boundary) is **a WhisperX suggestion only**, marked `?` in the review, and written only if Cullen accepts or types it | AGREED (10-char limit PROPOSED, default ON) |
| **H** | **Bright green (AGREED)**, stored `00CC00`, displays `#1FAD26` | On the outgoing track, **exactly 16 bars before D**. **Too short** (D − 16 bars falls before G, or inside the intro before B): H = the furthest phrase line still after G (after B if there's no G), stepping back from D in 4-bar steps (PROPOSAL). If fewer than 4 bars fit, or D is empty or low-confidence: leave empty + flag. **Last chorus exactly 16 bars:** H and C sit on the same beat; both are set (edge case 1) | `go`. Too short: `go N`, N = bars left to D (e.g. `go 8`) | AGREED as `go`, bright green. Edge cases PROPOSED |

### 3.4 Labels and the 10-character limit (PROPOSED, default ON)

- Every written label must be 10 characters or fewer (Serato truncates on screen; cue-logic note 1).
- **Conflict to flag to Cullen:** the fallback C label `last chorus` is **11 characters**, one over the limit. In fallback mode the tool writes it as cue-logic says, with a warning. Cullen picks a shorter fallback if he wants one.
- Old `30-day-plan.md` labels that cue-logic dropped (C = `bridge`) aren't generated.

### 3.5 Leave empty and flag (edge case 9, PROPOSED, default ON)

- Each cue gets a confidence score from 0 to 1 (§4.3). **Below its slot's threshold, the slot is left empty** and the row is flagged `needs review: <reason>`.
- **Dependent cues inherit uncertainty:** H depends on D, and A's label depends on B. If the parent cue is empty, the dependent one is empty too.
- Thresholds are config. Defaults get calibrated on the labeled set in Phase 1 (the PROPOSAL: pick the lowest threshold that keeps that slot's "wrong" rate at or under 10%). Turning this off isn't recommended: a wrong cue is worse than a missing one.

### 3.6 Mixing rules (AGREED; used for checks, not cue placement)

- The basic mix (`go` → Green → lock 8 / blend 8 → Yellow lands on Purple) and the floor rules (Blue jumps, Pink starts, Red bail-outs) need no extra cues.
- The tool uses them as **consistency checks** in the review: H = D − 16 bars; C and G before D; A before B; and Blue's jump target on a phrase line.
- Edge case 2 (incoming intro shorter than 16 bars) is a mixing technique. Its only effect on the code is "never move A."

### 3.7 Colors written to the file (RGB stored in Markers2)

| Slot | Color | Default RGB | Source |
|---|---|---|---|
| A | Dark green (AGREED, Oct 8) | `44CC00` stored, displays as `#006838` in Serato DJ Pro | Holzhaus serato-tags palette index 6 |
| B | Yellow | `CCCC00` | agree |
| C | Blue | `0000CC` | agree |
| D | Purple | `8800CC` | agree |
| E | Red | `CC0000` | agree |
| F | Orange | not written (F is never auto-set) | Sources disagree: cue card `CC8800`, serato-tools palette `CC4400` [RS] |
| G | Pink | `CC00CC` | agree |
| H | Bright green (AGREED, Oct 8) | `00CC00` stored, displays as `#1FAD26` in Serato DJ Pro | Holzhaus serato-tags palette index 7. White is dropped. |

**Better default [RS]:** during ground-truth extraction, copy the exact RGB values from Cullen's own hand-cued tracks, and use those over the table above.

---

## 4. Architecture

Two tiers: a **cheap proof engine** (Phases 1–2) and a **top-tier engine** (Phase 4). Both share the guard, grid snap, rules engine (§3), confidence scoring, review CSV, and writer. The top tier only replaces the analysis layer, and only if it beats the proof engine on the same labeled tracks.

```
copies (Mac test folder) ──transfer (OK'd)──▶ box staging (copies only)
  ├─ 1. Guard: path allowlist, [autocue], skip if CUE/LOOP/FLIP, SHA-256
  ├─ 2. Read Serato BeatGrid from the copy (fallback: Beat This!)
  ├─ 3. Decode to 44.1 kHz WAV (temp) with ffmpeg   ← MP3 decoders differ by 20–40 ms [RS F37]
  ├─ 4. Analysis (tier-dependent, §4.1 / §4.2)
  ├─ 5. Rules engine (§3) + per-cue confidence (§4.3) + one-line reason per cue
  ├─ 6. pilot-cues.csv ──▶ Cullen's review (CSV, later the HTML page) ──▶ approved CSV (hashed)
  └─ 7. Writer: serato-tools, accepted rows only, sidecar backup, round-trip check
         ──transfer back (OK'd)──▶ ~/Music/_CueTest-2026-10/ ──▶ crate NEW 2026-10 – Cue Test (made by hand)
```

### 4.1 Proof engine (Phases 1–2, $0 software)

| Layer | Choice | Notes |
|---|---|---|
| Grid | Serato BeatGrid on the copy; else **Beat This!** (MIT) | [RS §2.1] |
| Structure | **Run both allin1 and SongFormer in the dry run**, and keep whichever scores better on 20–30 of Cullen's labeled tracks | SongFormer: 0.703 boundaries within 0.5 s and 0.807 ACC, vs allin1's 0.596 and 0.740 (arXiv 2510.02797) [RS]. **allin1:** fully MIT, also outputs beats, last updated May 2024. **SongFormer:** code CC BY 4.0, but it loads **`MuQ-large-msd-iter`, CC BY-NC 4.0 (non-commercial only)**. No beat tracking, so it relies on the grid |
| Vocals (B refinement only) | HT Demucs (already inside allin1) | `facebookresearch/demucs` is archived (no fixes) [RS] |
| Lyrics | Not in the proof | Pink label = `hook` |
| LLM | None | [RS §8] |

### 4.2 Top-tier engine (Phase 4, only after the proof passes and Cullen OKs it)

Cullen asked for the strongest models. These are the strongest options **Research Bot found**, each still validated against his tracks. If one doesn't beat the proof engine on a slot, it isn't used for that slot.

| Job | Top-tier choice | Status / what's unknown |
|---|---|---|
| Grid | Still **Serato's grid first**. Fallback: Beat This!. Candidate: masked-diffusion Beat This (arXiv Aug 2026), best no-DBN GTZAN numbers (F12) | md_beat_this has **no declared license** and is slower at inference [RS; RS gives the slowdown factor as a garbled "S×", so the figure is UNKNOWN]. Not used until its license is confirmed. HingeNet/BeatFM code availability **UNKNOWN** |
| Structure | **Engine vote:** allin1 + SongFormer, plus LinkSeg (F17: 0.630 HR.5F) as a third voter. The boundary is taken where engines agree within 1 bar, and disagreement lowers confidence | LinkSeg license **UNKNOWN**. Whether a vote beats the best single engine on his tracks is **UNKNOWN** (measured in Phase 4) |
| Vocal separation (**B only**) | **Mel-RoFormer** (up to 13.29 dB vocal SDR, F26) or **BS-RoFormer** (10.66–12.72 dB, F24), vs HT Demucs 9.20 dB (F23). Via ZFTurbo MSST (MIT code) | **Each checkpoint's license UNKNOWN.** Whether a better SDR means better B cues is **UNKNOWN** |
| Pink lyric suggestion | **WhisperX** (BSD-2) on the mix | **Suggestion only, never ground truth, never auto-written.** Whisper's sung-word error rate is about 36% (35.5–37.8% WER on Jam-ALT, F31). **Separating vocals first made it worse** (44.5–48.0%) [RS]. WhisperX's word-timing accuracy on singing: **UNKNOWN** |
| Ambiguous section labels | **Optional text-only LLM tie-break** (§4.3) | Paid API: **needs Cullen's OK.** Model choice and cost **UNKNOWN** |
| Not used | Audio LLMs for timing (Gemini 2.5 Pro: 0.423 boundaries within 0.5 s, errors up to 2 s, F18); MSAF (no chorus names); key detection | [RS §2, §5] |

### 4.3 Per-cue confidence and the LLM step

**Confidence (0–1, in every CSV row, with a one-line `reason`):** a weighted mix of these signals (weights are a PROPOSAL, fitted in Phase 1):
1. **Grid:** Serato grid (high) or fallback (lower). Tempo drift or a non-4/4 flag lowers it.
2. **Engine agreement:** in the proof, allin1 and SongFormer agree on the boundary within 1 bar and on the label. In the top tier, the vote margin.
3. **Engine-reported probability**, where an engine exposes it. Whether SongFormer and LinkSeg expose usable per-boundary probabilities is **UNKNOWN**.
4. **Rule consistency:** sits on a phrase line; standard A count (16/8/4/2/1); chorus block length 8 or 16; D − 16 fits after G.
5. **Vocal onset agreement** (B only).

**LLM tie-break (top tier only, optional, off by default):**
- **When:** only if the engines disagree on which block is the last chorus or hook, or on whether a segment is a chorus vs outro vamp.
- **Input:** text only: segment list with bar counts and each engine's labels, vocal on/off, and repeated-line hints. No audio is sent.
- **Output:** one of the candidate block IDs, or `unsure`. **It never outputs a time**, and `unsure` → leave empty + flag.
- **Not used** for timing, the grid, A/B/D/E/H math, or writing. Each call is logged with its candidate set and choice. Expected share of tracks that need it: **UNKNOWN**.

### 4.4 Writer (copies only)

- **Library:** `serato-tools` (Python 3.12+, `TrackCuesV2` cue write and `track_beatgrid` read only).
  - Writes **MP3 and `.aiff` only** (`.aif` isn't matched in code) [RS].
  - License: its `pyproject` says MIT, but there's **no LICENSE file** in the repo [RS].
- **Folder lock, skip-if-cued, sidecar backup:** before any write, every `Serato *` GEOB frame of the copy goes to `_meta/<file>.serato-geob.bak`. Then SHA-256 of the originals (§2).
- **Legacy `Serato Markers_`: the sources disagree** [RS]. Holzhaus says Serato prefers it when it's present; serato-tools calls it unused in newer versions and deletes it by default.
  - **Phase 2 must test one copy on Cullen's actual Serato version** before the other 9: either write a matching Markers_ (cues A–E) or remove it from the copy. The policy is logged.
- **ID3 version:** save with the copy's original ID3 version (e.g. v2.3), then diff all non-Serato frames before and after [RS].
- **Round-trip check:** re-parse with serato-tools **and** triseratops, byte-compare the payload to the intended cues, and open 1 track in Serato before doing the rest [RS].
- **The one-copy test (Phase 2) also verifies:**
  - how H's stored `00CC00` displays as bright green `#1FAD26`, and A's stored `44CC00` as dark green `#006838`, on screen and on the CDJ-3000 pads
  - that E's saved `LOOP` entry shows up (serato-tools' LOOP write support: **verify**)
  - label truncation
  - decoder offset: compare one auto cue against Cullen's hand cue on the same downbeat, and set a per-format offset if needed [RS]
- **Formats:** proof = MP3 + `.aiff`. FLAC, M4A (needs the legacy `markers` field or shows at most 5 cues; verify), and WAV/Ogg/raw AAC are skipped and logged [RS].
- The writer never touches beatgrid, BPM, key, overview, crates, or the DB.

### 4.5 Review

- **`pilot-cues.csv`:** the RS schema (`track, file_sha256, slot, time_sec, bar, label, color_hex, bpm, source, confidence, notes`) plus `reason, flags, suggested_label, decision (accept / nudge / blank), nudged_time_sec`. Empty slots get a row too.
- **Phase 3 page:** one local HTML file (waveform, A–H markers, play-from-cue, accept / nudge ±1 beat or ±1 bar / blank, label edit, and the one-line reason per cue). It opens in his browser with nothing installed and exports the same CSV. **Target: under 60 s per track.**
- The final check is always in Serato and on the CDJ-3000 pads.

---

## 5. Where it runs

| Option | Facts | Verdict |
|---|---|---|
| **Shared Linux box** | **[measured Oct 8 2026]** 8 CPU cores, 15 GB RAM (about 8 GB in use by other work at check time), about 104 GB free disk, **no NVIDIA GPU visible** | **Proof runs here, CPU-only.** CPU runtime for allin1 and SongFormer is **UNKNOWN** [RS]. The first Phase 1 task times one track |
| **Cloud GPU** | Published GPU speed, NVIDIA L40, per song: **SongFormer 2–4 s, allin1 9–12 s** (F36) [RS] | **Optional.** Likely needed for the top tier if CPU is too slow. **Cost unknown, needs a quote.** Needs Cullen's OK for both the spend and the transfer |
| **Cullen's Mac** | Chip/OS unknown | **Not used.** Only the README's built-in `cp` / `shasum` / `stat` commands run there, with his OK and Serato closed |

**Box staging folder (PROPOSAL):** a box-side mirror of `_CueTest-2026-10` holding copies, WAV temps, and `_meta/` only. The exact path is fixed in Phase 1, with the same guard.

---

## 6. Evaluation plan (scored against cue-logic.md, read never write)

**Labeled set:** **20–30 of Cullen's own cued tracks** [RS].
- **Day 3's 10 hand-mapped tracks are the core**, but **they may not exist until Sat Oct 10, 2026.**
- Add his Day 4–5 tracks, or tracks he cues on pilot copies inside `NEW 2026-10 – Cue Test` [RS §4.2].
- Suggested mix (RS PROPOSAL, 25 tracks): 8 pop/dance-pop, 5 hip-hop/R&B, 5 throwback, 4 club/EDM, 3 wedding staples.
- His labeling time: about 40–60 min total (estimate from the Day 3 target of under 2 min per track) [RS].

**Ground truth:**
1. Copy the tracks to `_groundtruth/`. A read-only parse of `Serato Markers2` produces `groundtruth.csv`.
2. **Cullen confirms the CSV matches what he meant under cue-logic.md.**
   - His Day 3 cues were set under the older 30-day plan (H = spare, C may be `bridge`, A at the top of a long intro).
   - Where a rule is pure arithmetic (A = his B − 16 bars when the intro is 16+ bars; H = his D − 16 bars), the expected cue is derived from his other cues.
   - Rows that follow the old plan are marked `not comparable` rather than counted as wrong.
3. **Consistency ceiling:** he re-cues 5 tracks a week later. The tool isn't held to a higher bar than his own repeat [RS].

**Per-cue categories (every slot A–H on every track):**

| Category | Definition |
|---|---|
| **Correct** | Within 0.5 s of his cue (normally the same downbeat after snapping) **and** the label matches where labels are scored |
| **Within 1 bar** | 0.5 s < error ≤ 1 bar (a nudge) |
| **Wrong** | Error > 1 bar, a scored label that doesn't match, **or** the tool set a slot he left empty |
| **Correctly left empty** | Both empty (e.g. no chorus → C and G empty; F with no clean one-shot) |
| **Missed (left empty)** | Tool empty, he set it. Reported separately, and **not counted as accepted.** Cue-logic ranks it less bad than wrong; RS counts it as wrong, so both views are reported |

**Label scoring:**
- A: exact (`16` / `8` / … / `the 3`).
- C: `last N` with N matching his block length (in default mode).
- D: `out` vs `cold`.
- H: `go` / `go N`.
- G: lyric text not scored; whether he accepted the WhisperX suggestion is reported.
- F: tool is always empty, so it's reported but excluded from totals.

**Reported per engine (allin1, SongFormer; later the top tier):**
- **Acceptance rate per slot A–H:**
  - strict = (correct + correctly left empty) / N
  - usable = strict + within 1 bar
- Wrong rate and missed rate per slot. Also "H correct given D correct" (H is arithmetic on D).
- Split by genre bucket.
- mir_eval HR.5F / HR3F, for comparison with the published numbers [RS].
- Does confidence catch the errors (wrong-cue rate above vs below the threshold)?
- Median signed offset in ms (decoder check), runtime per track, and Cullen's review time.
- Safety checks: originals' SHA-256 unchanged, Wedding Sets crate files unchanged, no writes outside the pilot folder. **Any failure = stop.**

**Ship bar (Research Bot's PROPOSAL, not a published standard; Cullen can change it):**
- Zero safety violations, **and**
- A, B, D: ≥ 80% within 1 bar **and** ≥ 60% correct (within 0.5 s), **and**
- C, G: ≥ 70% within 1 bar, **and**
- E: ≥ 80% accepted by Cullen, **and**
- median review time ≤ 60 s per track.
- **If only A/B/D pass:** ship a narrower draft (A, B, D, E, H) and leave C/G manual. **If A/B/D fail:** stop and keep the manual workflow [RS].
- **This spec adds (PROPOSAL):** no slot over 10% wrong.

---

## 7. Phases and milestones

Every phase ends with a short report to Cullen, and **the next phase needs his explicit OK.** Build agents: **grok-4.7 at the highest reasoning setting.**

| Phase | What | Writes tags? | Gate to the next phase |
|---|---|---|---|
| **0. Spec approval** | Chief reviews, then Cullen approves this spec and answers the DECISIONS (or keeps the defaults) | No | Cullen's approval |
| **1. Cheap proof: dry run (box, CPU, $0)** | Box only: set up allin1, SongFormer, and Beat This!, and time 1 track. Build the guard, grid snap, rules engine (§3), and confidence scoring. With his OK: back up + hash + copy the 20–30 labeled tracks (read-only) and **10 uncued pilot tracks**, then transfer to the box. Run both engines report-only, score them (§6), keep the better one, calibrate the thresholds, and produce **`pilot-cues.csv`** for the 10 pilot tracks | **No.** Copies, CSVs, and logs only | Engine passes the ship bar on the labeled set. Cullen signs off on the CSV. Originals re-hash OK |
| **2. Cheap proof: write** | **1 copy first** on his actual Serato version: Markers_ policy, bright-green H vs dark-green A, saved loop, labels, decoder offset. Then the other 9 copies. Serato closed, before import. Transfer back; crate made by hand; check on screen and on the CDJ-3000 pads | Yes, 10 copies only | Cullen's per-cue scores. Originals byte-identical. Wedding Sets crate files hash the same as the backup |
| **3. Review page** | The HTML review page (§4.5) replaces hand-editing the CSV. His accept/nudge/blank choices become more labeled data | No (it exports a CSV) | Median review ≤ 60 s per track |
| **4. Top-tier pipeline** | Research Bot first confirms the §9 unknowns. Build §4.2 (engine vote, Mel-/BS-RoFormer for B, WhisperX Pink suggestions, optional LLM tie-break). Re-score on the **same** labeled set | Same as Phase 2, copies only | Adopted **only per slot where it beats the proof engine without raising the wrong rate.** Any GPU or API spend needs his OK and a quote first |
| **5. Repeatable batches** | Only after the ship bar is passed. New batches of copies, a new crate per batch under `NEW 2026-10`, same hash checks | Yes, copies only | **His OK per batch.** Batch size PROPOSAL: 25 or fewer |

**Rollback (any phase) [README]:** remove the tracks from the crate/library (remove from library, not delete from disk), delete the copy folder, or restore the `_Serato_` / Library backup. Delete the box copies. A single copy can also be restored from its sidecar backup.

---

## 8. Cost estimates

| Item | Cost |
|---|---|
| Proof software | **$0.** allin1 (MIT), Beat This! (MIT), SongFormer (CC BY 4.0 code + **CC BY-NC MuQ**; fine for private use), serato-tools (MIT per pyproject) [RS] |
| **Licensing flag** | If this ever becomes part of Sound & Service or is sold, the non-commercial parts (SongFormer's MuQ, madmom model files) need replacing or a license. **The build must confirm allin1 never loads madmom's NC model files** [RS]. A flag, not legal advice |
| Box compute | No extra purchase. CPU runtime **UNKNOWN** |
| Cloud GPU (top tier) | **Unknown, needs a quote.** Only with his OK |
| LLM tie-break API (top tier) | **Unknown.** Off by default; only with his OK |
| MIK / Lexicon / rekordbox | **$0. Not bought, not used** |
| Build time | Proof (Phases 1–2): **estimate 1–2 days [README]**, now with two engines, so likely the upper end (estimate). Phases 3–4: **UNKNOWN** |
| Build agents (grok-4.7, highest reasoning) | **UNKNOWN** (no pricing in the sources) |
| Cullen's time | About 40–60 min to label 20–30 tracks (once, estimate [RS]); 30–60 s review per track; one Serato + CDJ check per batch; 5 re-cues for the consistency check |

---

## 9. DECISIONS (open)

**H is no longer a decision:** `go` is AGREED, and its color is locked as bright green (stored `00CC00`, displays `#1FAD26`). A is dark green (stored `44CC00`, displays `#006838`). Source: Holzhaus serato-tags color table, confirmed Oct 8.

| # | Decision | Default the tool uses (Cullen can change) |
|---|---|---|
| D1 | H color | **Closed Oct 8.** Bright green, stored `00CC00`. A is dark green, stored `44CC00`. |
| D2 | C label mode | **Closed Oct 8, 2:08 PM PT.** `last 16` or `last 8`. Not `last chorus`. |
| D3 | `cold` on Purple for cold endings | **Closed Oct 8, 2:08 PM PT.** ON. |
| D4 | E: loop length | **Closed Oct 8, 2:08 PM PT.** 4 or 8 bars, whichever fits the clean section. Intro first, then outro. Saved loop ON in slot 1. |
| D5 | Pink 10-character max (PROPOSED); lyric suggestions on or off | 10-char limit ON; `hook` written; WhisperX suggestions only in the top tier |
| D6 | Edge cases 1–9 (all PROPOSED in cue-logic) | All ON as written in §3 |
| D7 | Confidence thresholds / leave-empty | ON; thresholds calibrated in Phase 1 |
| D8 | Serato version (3.x / 4.x / 5.0 beta) and file types (MP3, AIFF, M4A, FLAC) | MP3 + `.aiff` only; other types skipped and logged |
| D9 | MIK: installed? cue export or overwrite on? | Out of scope to use. If he has it, DJ Bot confirms export/overwrite is OFF. Tracks it already cued get skipped |
| D10 | OK to copy 20–30 + 10 tracks to the box (and back) | No transfer without his OK. Delete after the phase |
| D11 | Ship bar (§6) | Research Bot's proposal + no slot over 10% wrong |
| D12 | Top tier: cloud GPU and LLM tie-break spend | Off until he OKs a quote |
| D13 | Long-term: autocue copies sit next to the originals in Serato | Out of scope for Phases 1–5 |
| D14 | Colors: copy RGB from his own hand cues vs the §3.7 table | Copy from his cues when available |

---

## 10. Build agent checklist (after approval only)

1. Confirm Phase 0 approval is on record and the D1–D14 answers (or defaults) are in the run config.
2. Nothing runs on Cullen's laptop except the README's built-in copy/hash commands, with his OK and Serato closed.
3. **Guard tests come first,** on throwaway synthetic files on the box. Prove the tool refuses:
   - a path outside the folder, and a symlink pointing out
   - a missing `[autocue]`, and any write into `_groundtruth/`
   - a file with Markers2 or Markers_ CUE/LOOP/FLIP entries
   - an unsupported type
   - a write without an approved CSV hash
4. Unit tests for **every §3 rule and edge case**:
   - intro ≥ 16 / < 16 / pickup
   - `last 16` / `last 8` / odd length; double last chorus
   - hip-hop ending on a verse
   - EDM with B = G; no chorus
   - `cold` and fade endings
   - `go` / `go 8` / H empty
   - E intro, then outro, then empty
   - 10-char limit; leave empty below the threshold
5. Prove by grep that the serato-tools crate/DB/USB modules are never imported, and that allin1 doesn't load madmom NC model files.
6. Every report includes the before/after SHA-256 results for the originals.

---

## 11. Research follow-ups (RESEARCH-SPEC.md folded in; these are still UNKNOWN)

Research Bot must answer these before Phase 4 (and the CPU-runtime item during Phase 1). Don't fill any of them with guesses.

**Licenses:**
- each Mel-/BS-RoFormer checkpoint
- LinkSeg
- md_beat_this (none declared)
- serato-tools (no LICENSE file)
- JamendoLyrics / Jam-ALT, if lyric work needs them

**CPU vs GPU:**
- CPU runtime per track for allin1, SongFormer, Beat This!, Mel-RoFormer, and WhisperX on an 8-core, 15 GB box (or measured in Phase 1)
- whether SongFormer runs CPU-only at all

**Accuracy:**
- per-cue accuracy (first/last chorus, intro end, outro start, vocal onset)
- WhisperX word timing on singing
- whether an engine vote beats SongFormer alone
- whether better vocal SDR improves B
- audio-LLM timestamp error for newer models

**Behavior:**
- Markers_ vs Markers2 precedence on Cullen's Serato version (Phase 2 test)
- serato-tools LOOP write support
- how Serato and the CDJ show the two greens side by side (A dark, H bright)

**Cost:**
- cloud GPU quote for the top tier
- LLM tie-break API cost
- build-agent cost

---

## Sources

- `/workspace/dj-tutor/cue-logic.md`: cue rules (source of truth).
- `/workspace/research/dj-auto-cues/RESEARCH-SPEC.md`: F-IDs, licenses, writer risks, eval method, ship bar. Key papers: SongFormer [arXiv 2510.02797](https://arxiv.org/abs/2510.02797), allin1 [2307.16425](https://arxiv.org/abs/2307.16425), Beat This! [2407.21658](https://arxiv.org/abs/2407.21658), Jam-ALT [2408.06370](https://arxiv.org/abs/2408.06370), HT Demucs [2211.08553](https://arxiv.org/abs/2211.08553), BS-RoFormer [2309.02612](https://arxiv.org/abs/2309.02612), Mel-RoFormer [2409.04702](https://arxiv.org/abs/2409.04702).
- Code: [all-in-one](https://github.com/mir-aidj/all-in-one) · [SongFormer](https://github.com/ASLP-lab/SongFormer) · [MuQ weights (CC BY-NC)](https://huggingface.co/OpenMuQ/MuQ-large-msd-iter) · [beat_this](https://github.com/CPJKU/beat_this) · [whisperX](https://github.com/m-bain/whisperX) · [MSST](https://github.com/ZFTurbo/Music-Source-Separation-Training) · [serato-tools](https://github.com/bvandrc/serato-tools) · [triseratops](https://github.com/Holzhaus/triseratops) · [serato-tags docs](https://github.com/Holzhaus/serato-tags/tree/main/docs) · [mir_eval](https://github.com/mir-evaluation/mir_eval).
- `/workspace/research/dj-auto-cues/README.md`: safety protocol, backup/hash script, tool comparison, MIK/Lexicon risks.
- `/workspace/dj-tutor/cue-card/README.md`: Serato color values (community reverse-engineered).

## Cue layout v2 (supersedes the A–H meanings above; approved by Cullen Oct 15, 2026)
A = 8BS (8 bars before START, falling back to 4BS, 2BS, 1BS), B = START, C = 1st Chorus (suggest only), D = Last Chorus (suggest only), G = GO (exit_lead_beats before EXIT, default 32, 16 allowed), H = EXIT, E/F empty. Auto-set only A/B/G/H at confidence ≥ 0.85. Full rules: cue-logic.md.
