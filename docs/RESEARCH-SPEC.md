# RESEARCH-SPEC: the science and prior art behind Cullen's A–H auto-cue tool

*Owner of this section: Grok Bot (science and prior art). PM Bot owns SPEC.md and the build plan.*
*Written Thu Oct 8, 2026, ~1:20 PM PT. Research only: nothing installed, nothing bought, no user machine or music file touched.*
*Extends `README.md` in this folder (the tool comparison). Where this file corrects README, it says so in "Corrections to README" at the end.*

**How to read the numbers.** Every accuracy number here is labeled **FACT** with a source link, or **UNKNOWN** if no published figure exists. Nothing is estimated. Anything labeled **PROPOSAL** is our design choice, not a published result. Benchmarks use different songs and rules, so numbers from different rows are only roughly comparable.

---

## 0. Hard rules (apply to everything below)

1. **Copies only.** Never write to original files, existing Serato crates, `database V2`, `master.sqlite`, or anything in **Wedding Sets**.
2. **New crates only under "NEW 2026-10".** Pilot folder: `~/Music/_CueTest-2026-10/`. Pilot crate: **`NEW 2026-10 – Cue Test`**.
3. **Nothing installed on Cullen's laptop and no spending** (paid APIs, apps, GPUs) without his OK. Analysis runs on the box. Moving copied tracks onto the box is a file transfer that also needs his OK.
4. **Undecided, so the spec must work either way:**
   - **H**: `go` (16 bars before D) **or** empty/spare (the plan's optional second GREEN `8`).
   - **C/G labels**: lyric text **or** fixed text (`last chorus`, `hook`).
   - **Serato version and file types** (3.x vs 4.x vs 5.0 beta; MP3 vs AIFF/FLAC/M4A).
   - **Whether he owns Mixed In Key** and whether its cue export is on.
5. **Skip any file that already has cues** (any `CUE`, `LOOP`, or `FLIP` entry in `Serato Markers2` or `Serato Markers_`). Back up first, and hash the originals before and after (protocol in README).

---

## 1. Bottom line

- **The hard science is done by others; our job is the glue.** Open models already find beats, downbeats, and sections (intro, verse, chorus, outro) on pop. We snap their output to the bar grid, map it to slots A–H, write a review CSV, and only then write Serato cues into copies.
- **Structure engine: run two and let Cullen's tracks decide.** **SongFormer** (2025) beats **allin1** (2023) on every published structure number on the same benchmark (boundaries within ±0.5 s: 0.703 vs 0.596; label accuracy: 0.807 vs 0.740). But SongFormer downloads a **non-commercial (CC BY-NC 4.0)** MuQ checkpoint and does no beat tracking. allin1 is MIT, does beats and sections in one pass, but hasn't been updated since May 2024. **PROPOSAL:** run both in the dry run and keep whichever scores better on Cullen's own 20–30 labeled tracks.
- **Beat/bar grid: prefer Serato's own grid.** Copies of analyzed tracks carry Serato's `Serato BeatGrid` tag, so cues can snap to the same grid Cullen sees. Fallback: **Beat This!** (MIT code and weights; a stronger general beat tracker than allin1 outside pop). Beat This! does **not** clearly beat allin1 on pop downbeats (Harmonix 0.907 vs 0.915, different test setups).
- **Vocals:** a vocal stem (Demucs htdemucs, or the stronger Mel-/BS-RoFormer) helps find "vocal comes in" for B and confirm acapella moments. No published benchmark covers "first vocal onset = DJ start cue" (**UNKNOWN**).
- **Lyrics for C/G labels:** Whisper gets roughly 1 in 3 sung words wrong on a lyrics benchmark (35.5–37.8% WER). Lyric labels must be suggestions Cullen confirms, never auto-written.
- **LLMs/audio models:** good for naming, choosing between two chorus candidates, and explaining a cue in plain English. **Bad at exact timing:** Gemini 2.5 Pro got only 0.423 of boundaries within ±0.5 s (vs 0.703 for SongFormer). Specialist models own the timestamps.
- **Writing cues:** `serato-tools` (Python, MP3/AIFF only) on copies, behind our own safety wrapper. FLAC/M4A later. Serato reads the legacy `Serato Markers_` tag over `Markers2` when both exist, so the wrapper must handle it.
- **Scoring:** per cue, "correct (±0.5 s) / within 1 bar / wrong," measured against Cullen's own hand-set cues on copies. The ship threshold below is a **PROPOSAL**.

---

## 2. State of the art by task

### 2.1 Beat and downbeat tracking (grid for every cue; A's bar count)

| System | What it is | Code / license | Notes |
|---|---|---|---|
| **madmom** (Böck et al., JKU) | Classic RNN + DBN beat/downbeat library; the DBN is used inside many newer systems | [github.com/CPJKU/madmom](https://github.com/CPJKU/madmom). **Code BSD-style; model/data files CC BY-NC-SA 4.0** (per its LICENSE) | allin1 uses madmom's spectrogram and DBN **code** (paper §3.3). The build should confirm it never loads madmom's NC model files. |
| **BeatNet** (Heydari et al., ISMIR 2021) | Online (real-time) beat/downbeat/meter via CRNN + particle filtering | [github.com/mjhydri/BeatNet](https://github.com/mjhydri/BeatNet), CC BY 4.0 | Built for live use; weakest offline numbers here. |
| **allin1** (Kim & Nam, WASPAA 2023) | Beats, downbeats, sections, and labels in one model, using Demucs stems | [github.com/mir-aidj/all-in-one](https://github.com/mir-aidj/all-in-one), MIT; last push May 2024; needs NATTEN | Trained only on Harmonix (pop). Default model `harmonix-all` is an 8-fold ensemble, so **it has seen every Harmonix song**. |
| **Beat This!** (Foscarin, Schlüter, Widmer, ISMIR 2024) | Transformer beat/downbeat tracker, no DBN, trained on 4,556 tracks from 18 datasets/subsets (pop, rock, jazz, classical, Latin, solo instruments) | [github.com/CPJKU/beat_this](https://github.com/CPJKU/beat_this), **MIT code and weights**; active (pushed May 2026) | MIREX 2025 uses it as the baseline. Without DBN it sometimes gives non-periodic beats (paper §4.3). An optional `--dbn` flag exists. |
| **HingeNet / BeatFM** (Ru et al., ICME 2025) | Fine-tuned music foundation models (MusicFM/MERT) | Code availability **UNKNOWN** (not verified) | Slightly higher GTZAN downbeat F1 than Beat This!. |
| **Masked-diffusion Beat This** (Foscarin et al., arXiv Aug 2026) | Beat This! retrained as a masked diffusion model; fixes erratic tempo flips | [github.com/fosfrancesco/md_beat_this](https://github.com/fosfrancesco/md_beat_this) (no license declared); paper notes about S× slower inference | Newest; best no-DBN numbers on GTZAN. Too new to bet v1 on. |

**Plain-language take:** on pop at a steady tempo, downbeats are close to solved (about 91% F1 on Harmonix). Errors cluster in live drums, swing, tempo drift, and odd intros. Cullen's `the 3` label (pickup start) is exactly the kind of case trackers miss, so flag it for review.

**PROPOSAL for v1:** if the copy already has a `Serato BeatGrid` tag (Serato analyzed the original), **snap every cue to Serato's grid**. That way the cues line up with the grid Cullen sees on screen and the CDJ-3000s. Use Beat This! or allin1 downbeats only to (a) choose which downbeat is bar 1 of a phrase and (b) cover tracks with no Serato grid. The `Serato BeatGrid` tag format is documented by Holzhaus and parsed by serato-tools.

### 2.2 Structure segmentation and functional labels (B, C, D, G; E's region)

| System | Labels | Code / license | Notes |
|---|---|---|---|
| **MSAF** (Nieto) | Boundaries plus unnamed clusters (A/B/C), **no "chorus" names** | [github.com/urinieto/msaf](https://github.com/urinieto/msaf), MIT | Classic unsupervised toolkit; useful as a baseline only. Numbers not re-verified here: **UNKNOWN**. |
| **SpecTNT / Harmonic-CNN** (Wang, Hung, Smith, ICASSP 2022) | intro/verse/chorus/bridge/inst/outro/silence | Not public | Defined the 7-label scheme everyone now reports. |
| **allin1** (2023) | intro, verse, chorus, bridge, inst, solo, break, outro, start, end | MIT | One pass with beats, which keeps boundaries bar-aligned. |
| **LinkSeg** (Buisson, McFee, Essid, ISMIR 2024) | 7 labels | Public inference code (per SongFormer paper) | Middle of the pack. |
| **SongFormer** (Hao et al., arXiv Oct 2025) | intro, verse, pre-chorus, chorus, bridge, inst, silence, outro | [github.com/ASLP-lab/SongFormer](https://github.com/ASLP-lab/SongFormer). **Code CC BY 4.0**, but inference loads **`OpenMuQ/MuQ-large-msd-iter`, which is CC BY-NC 4.0** ([HF card](https://huggingface.co/OpenMuQ/MuQ-large-msd-iter)); MusicFM weights MIT | Best published structure numbers. Trained on 14k+ songs (incl. 4,387 Gemini-labeled). 2–4 s/song on an NVIDIA L40. **No beat tracking**: pair it with a grid. |
| **Gemini 2.5 Pro** (as tested by SongFormer authors) | Free-text structure | Paid API | Good labels, **poor boundary timing** (see table). |

**Benchmarks (what the numbers mean):**
- **Harmonix Set**: 912 Western pop songs with beats, downbeats, and section labels ([github.com/urinieto/harmonixset](https://github.com/urinieto/harmonixset)). Closest public data to Cullen's wedding pop.
- **SongFormBench**: 200 re-checked Harmonix songs + 100 Chinese songs, CC BY 4.0 ([HF](https://huggingface.co/datasets/ASLP-lab/SongFormBench)).
- **RWC-Pop**: 100 Japanese pop songs; not in SongFormer's training data.
- **SALAMI**: ~1,400 recordings, mostly two annotators each, CC0 annotations ([DDMAL](https://ddmal.ca/research/salami/annotation/)). No allin1/SongFormer SALAMI numbers published: **UNKNOWN**.
- **Isophonics**: Beatles/Queen etc. annotations, CC BY-NC-SA ([isophonics.net](https://isophonics.net/datasets)).
- **Metrics:** HR.5F / HR3F = F-measure of boundaries within ±0.5 s / ±3 s. ACC = share of time with the right label. At 120 BPM one bar is 2.0 s, so **"within 1 bar" falls between HR.5F and HR3F**.

**Does SongFormer beat allin1? Yes, on every published structure number on the same benchmark** (F14–F19). allin1's own paper reports a higher HR.5F (0.660) because it uses its own 8-fold Harmonix split. On SongFormBench the SongFormer authors measured allin1 at 0.596.

### 2.3 Chorus and hook detection specifically (C = last chorus, G = first chorus, D = after last chorus)

- **Dedicated chorus detector:** Wang, Smith et al., "Supervised chorus detection for popular music" (ICASSP 2021, [arXiv 2103.14253](https://arxiv.org/abs/2103.14253)). Chorus-frame F1 0.692 on Harmonix, about 0.61 across datasets. The open baseline **pychorus** scored 0.466 (F20–F22). Code release: **UNKNOWN**.
- In practice, **allin1 and SongFormer already label "chorus"**, so first/last chorus = first/last segment labeled chorus. ACC (0.740–0.807) covers all labels, not chorus alone.
- **Per-cue accuracy for "first chorus start" or "last chorus start/end": UNKNOWN.** No paper reports it. We have to measure it on Cullen's tracks (§6).
- **Known failure modes (from the papers' own discussion):** a final repeated chorus labeled "outro"; a post-chorus or drop labeled "chorus"; a pre-chorus folded into "verse." SongFormer keeps a pre-chorus label, which helps here.
- **"Hook" vs "chorus":** Cullen's G is "the hook / best part." In most pop that is the first chorus. In dance tracks it may be the drop. **PROPOSAL:** G = first `chorus` segment; if none, the first high-energy `inst`/drop segment; always flagged when the fallback is used.

### 2.4 Vocal/instrumental separation (B refinement, acapella checks, F candidates)

- **HT Demucs** (Meta, ICASSP 2023): fine-tuned model 9.00 dB average SDR, 9.20 dB vocals on MUSDB18-HQ (F23). allin1 already runs it internally. **Repo `facebookresearch/demucs` is archived (read-only) on GitHub**, so expect no fixes.
- **BS-RoFormer** (ByteDance; won SDX'23 music separation): 10.66–12.72 dB vocals depending on size and training data (F24).
- **Mel-RoFormer**: 11.21–11.60 dB vocals in its first paper; up to 13.29 dB in a 2024 follow-up (F25, F26). Community training/inference code: [ZFTurbo/Music-Source-Separation-Training](https://github.com/ZFTurbo/Music-Source-Separation-Training) (MIT; check each checkpoint's own license).
- **Using vocal onset for cues.** A simple loudness gate on the separated vocal is a published, workable voice-activity method for singing (Syed et al. 2025 use it to segment songs for Whisper, [arXiv 2506.15514](https://arxiv.org/abs/2506.15514)). For Cullen: B ("start") is often the first downbeat at or just before the first sustained vocal. The vocal gate gives a candidate, and the grid snaps it. **Accuracy of "first vocal onset → correct B": UNKNOWN.** Measure it.
- **Acapella cues:** for acapellas in his Scratch Samples crate, GREEN goes on "the downbeat where the first phrase starts, which isn't always the first word" (Day 13 note in the plan). Vocal onset + grid gives a candidate; it needs review.
- **F (scratch sample):** a vocal stem can list short, loud, isolated vocal hits as candidates. Choosing a good one is taste. **Leave F blank in v1** (matches PM Bot's plan).

### 2.5 Energy/drop detection, key, tempo

- **Drops (EDM):** Yadati et al. (ISMIR 2014), 100 EDM tracks: drop F1 0.61 with a 3 s window, 0.71 with a 15 s window (F27). Old, small, and EDM-only. Not useful for pop choruses.
- **Mixed In Key energy cues:** MIK says it places "up to 8 automatic Cue Points per track" and calls itself "the world's first and most accurate key finding software," with v11 "at least 10% more accurate than any software that has ever existed" ([mixedinkey.com/learn-more](https://mixedinkey.com/learn-more/)). **These are vendor claims with no published method.** Independent accuracy of MIK cue placement: **UNKNOWN.**
- **Key (independent evals only):**
  - GiantSteps Key (604 EDM tracks; ISMIR 2015, [paper](https://archives.ismir.net/ismir2015/paper/000246.pdf)): rekordbox 71.85% correct, MIK 7 67.22%, KeyFinder 45.36%, Essentia (2015 default) 30.46% (F28). Caveat: the set is biased toward tracks Beatport got wrong, and the versions are from 2015.
  - Korzeniowski & Widmer CNN (ISMIR 2018, [arXiv 1808.05340](https://arxiv.org/abs/1808.05340)): 67.9% correct on GiantSteps, **79.9% on Billboard pop**, 76.3% on Isophonics (F29). madmom ships a related CNN key model, but its model files are non-commercial.
  - Essentia's EDM key profiles (`edma`, `edmm`, `bgate`) exist ([Essentia KeyExtractor docs](https://essentia.upf.edu/reference/std_KeyExtractor.html)); their published accuracy could not be fetched today: **UNKNOWN**.
  - Current MIK 11 / rekordbox 7 / Serato 4 key accuracy on an independent set: **UNKNOWN.**
- **Tempo:** GiantSteps Tempo (664 EDM tracks): best results about 74.5–77.0% within ±4% (rekordbox, Traktor with range set, Böck et al. with range set) (F30).
- **Relevance to cues: low.** Serato already computes BPM and key. **PROPOSAL:** v1 computes no key and uses Serato's BPM/grid when present.

### 2.6 Lyric transcription and alignment (C/G lyric labels)

- **Whisper on sung lyrics** (Jam-ALT benchmark, 79 songs in 4 languages, ISMIR 2024, [arXiv 2408.06370](https://arxiv.org/abs/2408.06370)): WER 37.8% (large-v2) and 35.5% (large-v3). With the language given, v2 drops to 27.9%. **Running HT Demucs vocal separation first made it worse** (44.5% / 48.0%) (F31). Syed et al. 2025 got about 20–23% long-form WER on Jam-ALT with a vocal-gate segmentation trick (F32), and note that Whisper's own timestamps "can be inaccurate."
- **WhisperX** ([github.com/m-bain/whisperX](https://github.com/m-bain/whisperX), BSD-2) adds word timestamps via forced alignment with a speech model. **Published accuracy on singing: UNKNOWN.**
- **Dedicated lyrics-to-audio alignment** (when the lyric text is known, e.g., Cullen types it once): JamendoLyrics average absolute error 0.15–0.22 s for research systems (F33). MIREX 2024 results swing from 0.22 s to 0.65 s on the larger 79-song set (F34).
- **Honest summary:** transcription is too error-prone to label cues unattended. Alignment of *known* lyrics is accurate enough to place a word, but needs the lyric text. **PROPOSAL:** default C/G labels are fixed (`last chorus`, `hook`). In "lyric mode," the tool *suggests* 2–4 words from the transcript at that time, marked `?` in the CSV. Nothing lyric-based is written until Cullen accepts the row.

---

## 3. FACT table (published numbers only)

| ID | Task | System | Dataset / setup | Metric | Value | Source |
|---|---|---|---|---|---|---|
| F1 | Beat | allin1 | Harmonix, 8-fold CV | Beat F1 (±70 ms) | 0.958 | [arXiv 2307.16425, Table 1](https://arxiv.org/abs/2307.16425) |
| F2 | Downbeat | allin1 | Harmonix, 8-fold CV | Downbeat F1 | 0.915 | same |
| F3 | Structure | allin1 | Harmonix, 8-fold CV | HR.5F / PWF / Sf | 0.660 / 0.738 / 0.769 | same |
| F4 | Downbeat | madmom-style TCN (reproduced) | Harmonix | Downbeat F1 | 0.886–0.901 | same |
| F5 | Beat | Beat This! | Harmonix, 8-fold CV | Beat / downbeat F1 | 95.8 / 90.7 | [arXiv 2407.21658, Table 1](https://arxiv.org/abs/2407.21658) |
| F6 | Beat | Beat This! | RWC-Pop, 8-fold CV | Beat / downbeat F1 | 96.1 / 93.7 | same |
| F7 | Beat | Beat This! | GTZAN (test-only) | Beat / downbeat F1 | 89.1 / 78.3 | same, Table 2 |
| F8 | Beat | Beat This! (MIREX 2025 baseline) | GTZAN (999 songs) | Beat F1 | 89.02 | [MIREX 2025](https://music-ir.org/mirex/wiki/2025:Audio_Beat_Tracking_Results) |
| F9 | Beat | BeatNet (online) | GTZAN | Beat / downbeat F1 | 75.44 / 46.49 | [arXiv 2108.03576](https://arxiv.org/abs/2108.03576) |
| F10 | Beat | MusicFM+HingeNet | GTZAN | Beat / downbeat F1 | 89.2 / 79.8 | [arXiv 2508.09788](https://arxiv.org/html/2508.09788v2) |
| F11 | Beat | BeatFM (MusicFM) | GTZAN | Beat / downbeat F1 | 89.1 / 79.6 | [arXiv 2508.09790](https://www.arxiv.org/pdf/2508.09790v1) |
| F12 | Beat | Masked-diffusion Beat This (8 steps) | GTZAN | Beat / downbeat F1; downbeat CMLt | 89.7 / 79.5; 76.4 (vs 67.3 Beat This!) | [arXiv 2608.04624, Table 1](https://arxiv.org/abs/2608.04624) |
| F13 | Beat note | Beat This! | GTZAN | Downbeat F1 when trained only on the older baseline's data | 75.5 | [arXiv 2407.21658, Table 2](https://arxiv.org/abs/2407.21658) |
| F14 | Structure | SongFormer (HX) | SongFormBench-Harmonix (200) | ACC / HR.5F / HR3F | 0.795 / **0.703** / 0.784 | [arXiv 2510.02797, Table II](https://arxiv.org/abs/2510.02797) |
| F15 | Structure | SongFormer (all data) | SongFormBench-Harmonix | ACC / HR.5F / HR3F | **0.807** / 0.696 / 0.780 | same |
| F16 | Structure | allin1 (re-tested) | SongFormBench-Harmonix | ACC / HR.5F / HR3F | 0.740 / 0.596 / 0.730 | same |
| F17 | Structure | LinkSeg-7Labels | SongFormBench-Harmonix | ACC / HR.5F / HR3F | 0.780 / 0.630 / 0.762 | same |
| F18 | Structure | **Gemini 2.5 Pro** | SongFormBench-Harmonix | ACC / HR.5F / HR3F | 0.748 / **0.423** / 0.813 | same; authors also observed Gemini boundary errors "up to 2 s" (§III-A) |
| F19 | Structure | SongFormer vs LinkSeg | RWC-Pop (unseen) | ACC / HR.5F | 0.812 / 0.650 vs 0.747 / 0.648 | same |
| F20 | Chorus | Temporal-HS CNN | Harmonix | Chorus pairwise F1 | 0.692 | [arXiv 2103.14253, Table 1](https://arxiv.org/abs/2103.14253) |
| F21 | Chorus | same, cross-dataset | In-house / SALAMI-pop | F1 | 0.624 / 0.602 | same |
| F22 | Chorus | pychorus (open baseline) | Harmonix | F1 | 0.466 | same |
| F23 | Separation | HT Demucs (fine-tuned) | MUSDB18-HQ | Avg SDR / vocals SDR | 9.00 / 9.20 dB | [arXiv 2211.08553, Table 1](https://arxiv.org/abs/2211.08553) |
| F24 | Separation | BS-RoFormer | MUSDB18-HQ | Vocals SDR | 10.66 (no extra data) / 12.72 (extra data) dB | [arXiv 2309.02612](https://arxiv.org/abs/2309.02612) |
| F25 | Separation | Mel-RoFormer (L=6 / L=9) | MUSDB18-HQ | Vocals SDR | 11.21 / 11.60 dB | [arXiv 2310.01809, Table 1](https://arxiv.org/abs/2310.01809) |
| F26 | Separation | Mel-RoFormer (2024, best) | MUSDB18-HQ | Vocals SDR | 13.29 dB | [arXiv 2409.04702, Table 2](https://arxiv.org/abs/2409.04702) |
| F27 | Drop | Yadati et al. | 100 EDM tracks | Drop F1 @3 s / @15 s | 0.61 / 0.71 | [ISMIR 2014](https://archives.ismir.net/ismir2014/paper/000297.pdf) |
| F28 | Key | rekordbox v3.2.2 / MIK 7 / KeyFinder / Essentia | GiantSteps Key (604) | % correct (MIREX weighted) | 71.85 (79.55) / 67.22 (74.60) / 45.36 (59.30) / 30.46 (44.85) | [ISMIR 2015, Table 5](https://archives.ismir.net/ismir2015/paper/000246.pdf) |
| F29 | Key | Korzeniowski CNN | GiantSteps / Billboard / Isophonics | % correct (weighted) | 67.9 (74.6) / 79.9 (85.1) / 76.3 (82.5) | [arXiv 1808.05340](https://arxiv.org/abs/1808.05340) |
| F30 | Tempo | rekordbox / Traktor 88–175 / Böck 95–190 | GiantSteps Tempo (664) | Accuracy1 (±4%) | 74.5% / 77.0% / 76.5% | [ISMIR 2015, Table 4](https://archives.ismir.net/ismir2015/paper/000246.pdf) |
| F31 | Lyrics ASR | Whisper large-v2 / large-v3 | Jam-ALT (79 songs) | WER (lower = better) | 37.8% / 35.5% (v2 + language: 27.9%; v2/v3 + HT Demucs: 44.5% / 48.0%) | [arXiv 2408.06370, Table 1](https://arxiv.org/abs/2408.06370) |
| F32 | Lyrics ASR | Whisper + vocal-gate segmentation | Jam-ALT, original mix | WER | 23.02% → 20.35% | [arXiv 2506.15514](https://arxiv.org/abs/2506.15514) |
| F33 | Lyric alignment | DSE / DALI-trained systems | JamendoLyrics (20 songs) | Avg abs error | 0.15 s / 0.20–0.23 s | [DAFx 2025 paper](https://dafx.de/paper-archive/2025/DAFx25_paper_15.pdf); [ETH paper table](https://tik-db.ee.ethz.ch/file/45c7793c1a3a476d56f784f74d668d74/) |
| F34 | Lyric alignment | MIREX 2024 entries | Jamendo V1 / V2 | Avg abs error | 0.217–0.547 s / 0.584–0.651 s | [MIREX 2024](https://music-ir.org/mirex/wiki/2024:Lyrics-to-Audio_Alignment_Results) |
| F35 | Audio LLM | Gemini API | docs | Audio tokens per second; timestamp format | 32 tokens/s; refer to times as `MM:SS` | [ai.google.dev/gemini-api/docs/audio](https://ai.google.dev/gemini-api/docs/audio) (updated 2026-09-23) |
| F36 | Speed | SongFormer / allin1 / Gemini 2.5 Pro | per song, NVIDIA L40 | seconds | 2–4 / 9–12 / 30–90 | [arXiv 2510.02797, Table III](https://arxiv.org/abs/2510.02797) |
| F37 | Decoding | allin1 author | MP3 decoders | Timing offset between decoders | 20–40 ms | [allin1 README](https://github.com/mir-aidj/all-in-one#concerning-mp3-files) |

**UNKNOWN (no published number found):** per-cue accuracy for "first chorus," "last chorus," "end of last chorus," "intro end," or "first vocal onset"; allin1/SongFormer on SALAMI; WhisperX word-timing error on singing; MIK 11 / rekordbox 7 key, energy, or cue accuracy on an independent set; Serato's own BPM/key accuracy; any audio LLM's timestamp error in milliseconds; OpenAI `gpt-audio-1.5` music-structure accuracy; Essentia EDM-profile key accuracy (paper not reachable today); CPU-only runtime for allin1/SongFormer.

---

## 4. Datasets: what we can legally use, and Cullen's own ground truth

### 4.1 Public datasets

None of these use Cullen's A–H map. They give *proxies*: intro end ≈ B, first chorus ≈ G, last chorus ≈ C, outro start ≈ D. They are useful for sanity checks; **his own tracks are the real test.**

| Dataset | What it has | License | Audio? | Use for us |
|---|---|---|---|---|
| **Harmonix Set** (912 pop) | Beats, downbeats, sections | Annotations **MIT** ([LICENSE](https://github.com/urinieto/harmonixset/blob/main/LICENSE)); mel spectrograms need a separate license | **Not distributed** (YouTube URLs + DTW alignment notebook) | Best proxy. **Contamination warning:** allin1's default model trained on all of it, and SongFormer on 712 songs of it, so it can't fairly score them. Optional trick: Harmonix songs Cullen already owns could get free structure labels via the alignment notebook (still contaminated for allin1). |
| **SongFormBench** (300) | Expert-revised sections | **CC BY 4.0** ([HF](https://huggingface.co/datasets/ASLP-lab/SongFormBench)) | Annotations, metadata, mels (no raw audio confirmed) | Comparing engines on public data |
| **SALAMI** (~1,400) | Sections, mostly two annotators | **CC0** annotations ([DDMAL](https://ddmal.ca/research/salami/annotation/)) | Partly (Internet Archive portion) | Cross-genre sanity check; no published allin1/SongFormer scores |
| **RWC Music Database** (incl. 100 pop) | Audio + AIST beat/chorus/structure annotations | **CC BY-NC 4.0** (re-released on [Zenodo](https://zenodo.org/records/17177919), 2026-02-01) | Yes | Unseen-by-SongFormer test; **non-commercial only** |
| **Isophonics** | Beatles/Queen etc. structure, key, chords | **CC BY-NC-SA** ([isophonics.net](https://isophonics.net/datasets)) | No (commercial CDs) | Only if he owns the albums; NC |
| **GiantSteps Key / Tempo** | EDM key and BPM | Repo has no declared license | Beatport previews via script | Not needed for cues |
| **JamendoLyrics / Jam-ALT** | Word/line-timed lyrics | Dataset licenses not confirmed today (**UNKNOWN**; Jamendo audio is Creative Commons per track) | Yes | Only if we build lyric mode |

**Licensing note for Cullen's business:** NC (non-commercial) data and weights (RWC, Isophonics, madmom models, MuQ inside SongFormer) are fine for private research. If this tool ever becomes part of Sound & Service or is sold, those parts need replacing or a license. This is a flag, not legal advice.

### 4.2 Ground truth from Cullen's own tracks (the evaluation that matters)

**Who and how long:** Cullen labels; nobody else knows his intent. The plan's Day 3 target is under 2 minutes per track, so **20–30 tracks ≈ 40–60 minutes** total. DJ Bot does the copying and backup.

**Picking tracks (PROPOSAL, 25 tracks):** 8 current pop/dance-pop, 5 hip-hop/R&B, 5 throwback (Motown, disco, live drums), 4 club/EDM, 3 wedding staples (first-dance ballads, entrances). **Prefer tracks Cullen already mapped on Days 3–5**, because his existing cues are free ground truth.

**Two ways to collect labels (both on copies only):**
1. **Already-cued tracks (preferred, zero extra work):** copy the file to `~/Music/_CueTest-2026-10/_groundtruth/` and **read** its `Serato Markers2` (read-only parse, no save) to export slot/time/color/label to CSV. Cullen confirms the CSV matches what he meant. *These copies are for reading; the tool never writes to them, and they never go into a Serato crate.*
2. **Uncued tracks:** in Serato, Cullen sets A–H on the **pilot copies** (inside `NEW 2026-10 – Cue Test` only), then we read them back the same way.

**Measure Cullen's own consistency:** he re-cues 5 of the tracks a week later without looking. The gap between his two passes is the realistic ceiling for any tool.

**CSV schema** (`groundtruth.csv`, one row per track × slot; empty slots get a row with `time_sec` blank so "should be empty" is scored too):

| column | type | example | notes |
|---|---|---|---|
| `track` | text | `Uptown Funk [autocue].mp3` | file name of the copy |
| `file_sha256` | hex | `9f2c…` | hash of the copy at labeling time |
| `slot` | A–H | `C` | |
| `time_sec` | float, 3 dp | `161.234` | blank = slot intentionally empty |
| `bar` | int | `81` | 1-based bar number from the track's first downbeat (Serato grid if present) |
| `label` | text | `last chorus` | exactly as on the pad |
| `color_hex` | hex | `0000CC` | as stored in Markers2 |
| `bpm` | float | `115.0` | from Serato grid/tag |
| `source` | enum | `cullen` / `model:allin1` / `model:songformer` | the predictions file uses the same schema |
| `confidence` | 0–1 | `0.82` | predictions only |
| `notes` | text | `the 3 pickup` | free text |

Minimum required columns, per Chief's brief: `track, slot, time_sec, bar, label`.

---

## 5. Where LLMs and audio models help vs. where specialist models win

| Job | Who should do it | Why |
|---|---|---|
| Beat grid, downbeats, bar counting (A label, H = D − 16 bars) | **Specialist** (Serato grid → Beat This!/allin1) | Needs ±70 ms. LLMs talk in `MM:SS` (F35); no published LLM beat-tracking accuracy |
| Section boundaries (B, C, D, G positions) | **Specialist** (SongFormer/allin1) | Gemini 2.5 Pro HR.5F 0.423 vs SongFormer 0.703 on the same songs (F14, F18) |
| Section *names* when the specialist is unsure (is 2:41 the last chorus or an outro vamp?) | **Audio LLM can help** as a tie-breaker | Gemini's label ACC 0.748 is close to the specialists' (F18), and its ±3 s hit rate is the best (0.813) |
| Choosing between two chorus candidates using lyrics/repetition | **LLM** (text-only is enough if we give it the transcript + segment list) | Reasoning over structure and lyrics, not timing |
| Cue label text (`hook` → `"I wanna dance"`) | **LLM suggests, Cullen confirms** | Sung-lyric WER is 20–48% (F31, F32) |
| Review UI: "I think the last chorus is at 2:41 because…" | **Template first, LLM optional** | The facts (segment label, its confidence, vocals on, 16 bars before the outro) come from the analyzers. A fixed sentence template is cheaper, offline, and can't make things up. An LLM can rephrase it. |
| Writing tags | **Deterministic code only** | Byte-level format; no model in the write path |

**Real models that accept audio (cited docs):**
- **Google Gemini API.** Audio input up to 9.5 hours per prompt, 32 tokens per second, downsampled to 16 Kbps mono. Timestamps are referenced as `MM:SS`. Current docs show model `gemini-3.8-flash` ([docs, updated 2026-09-23](https://ai.google.dev/gemini-api/docs/audio)). **It cannot give sample-accurate or beat-accurate timestamps.** Measured: Gemini 2.5 Pro boundaries were off by up to 2 s (F18). Error figures for newer Gemini versions: **UNKNOWN.**
- **OpenAI `gpt-audio-1.5`.** Accepts WAV/MP3 input via Chat Completions `input_audio` ([guide](https://developers.openai.com/api/docs/guides/audio-chat-completions)). `gpt-4o-audio-preview` was shut down 2026-05-07 ([deprecations](https://developers.openai.com/api/docs/deprecations)). The docs give no timestamp-precision guarantee: **UNKNOWN**; treat it as not timing-capable.
- **Cost/consent:** both are paid APIs and would send copyrighted audio to a third party. **Needs Cullen's OK** (spending rule) and is **not required for v1**.

---

## 6. Serato cue writing: format, libraries, file types, risks

### 6.1 Format (from Holzhaus reverse-engineering docs, MIT)
- **Serato Markers2** ([doc](https://github.com/Holzhaus/serato-tags/blob/main/docs/serato_markers2.md)): MP3/AIFF ID3v2 GEOB frame. 2-byte header `01 01`, then base64 data (linefeed every 72 bytes, padded to at least 470 bytes).
  - Entries: `COLOR` (track color), `BPMLOCK`, `CUE`, `LOOP`, `FLIP`.
  - **`CUE`** = index (0–7 → A–H), position in **milliseconds** (uint32), RGB color, UTF-8 name up to 0x33 bytes including the null (≈50 bytes).
- **Serato Markers_** (legacy, [doc](https://github.com/Holzhaus/serato-tags/blob/main/docs/serato_markers_.md)): first **5 cues** + 9 loops in a fixed 14-entry block with "serato32" encoding; no labels. Holzhaus: **"Serato will prefer information from `Serato Markers_` if it's present."** serato-tools describes Markers_ as "Old, not used in newer versions." **These sources disagree. PROPOSAL:** on our fresh copies, either write a matching Markers_ (cues A–E) or remove it from the copy. Decide on the pilot by loading 1 track in Cullen's actual Serato version.
- **Other containers** ([fileformats doc](https://github.com/Holzhaus/serato-tags/blob/main/docs/fileformats.md)):
  - **FLAC**: Vorbis comment `SERATO_MARKERS_V2` (base64, no padding).
  - **MP4/M4A (AAC/ALAC)**: freeform atom `----:com.serato.dj:markersv2` plus `----:com.serato.dj:markers`. Doc wording: *"In the special case of MP4/M4A files, Serato only reads the first 5 cue points and all loops when these are also in the `markers` field. This field has a fixed length and even when cue points or loops are not in use, they should still be filled."* Practical reading: for M4A, also fill the legacy field or expect at most 5 cues. Verify on the pilot.
  - **Raw .aac**: metadata lives in `_Serato_/Metadata` XML (mac only). **Never touch.**
  - **WAV**: not described in the docs. **UNKNOWN; skip WAV in v1.**
  - **Ogg**: different format. Skip.
- **Proposed cue colors (PROPOSAL; confirm against Cullen's existing cues):** serato-tools' palette names these RGBs ([source](https://github.com/bvandrc/serato-tools/blob/main/src/serato_tools/track_cues_v2.py)):

| Slot | Color | RGB |
|---|---|---|
| A | green | `00CC00` |
| B | yellow | `CCCC00` |
| C | blue | `0000CC` |
| D | purple | `8800CC` |
| E | red | `CC0000` |
| F | orange | `CC4400` |
| G | pink | `CC00CC` |
| H (`go`) | color undecided | — |

  Better still: copy the exact RGB values from one of his hand-cued tracks during ground-truth extraction.

### 6.2 Libraries (verified to exist)
| Library | Language / license | Reads | Writes | Notes |
|---|---|---|---|---|
| **Holzhaus/serato-tags** | Python scripts + docs, MIT; last push Sep 2023 | MP3/AIFF/FLAC/MP4 docs | scripts | Reference docs; serato-tools calls it "no longer maintained" |
| **bvandrc/serato-tools** v4.0.2 | Python ≥3.12; `pyproject` says MIT (no LICENSE file in repo); active (pushed 2026-10-05) | Markers2, Markers_, BeatGrid, Autotags, Overview | **Same, via mutagen, MP3 and `.aiff` only** (the code checks `.mp3` / `.aiff`; `.aif` isn't matched) | Also edits crates, smart crates, `database V2`, and does USB export. **We use only `track_cues_v2` / `track_beatgrid` read + cue write.** `modify_entries()` / `set_track_color()` **delete `Serato Markers_` by default** (`delete_tags_v1=True`). Has a snap-cues-to-beatgrid helper. |
| **Holzhaus/triseratops** | Rust, MPL-2.0; "under heavy development" | Parses Serato tag payloads + database files | Serializer exists, but it **does not read MP3/AIFF/MP4/FLAC containers itself** (needs e.g. the `id3` crate) | Exists; useful as a second parser to cross-check our writes |
| **Mixxx** (C++) | GPL | Imports Serato cues/beatgrids | Writing Serato tags was not shipped as of the 2021 announcement; current status **UNKNOWN** | Its maintainers warn that format bugs "might lead to data loss and potentially even crash Serato" ([Mixxx blog](https://mixxx.org/news/2021-02-08-new-in-2-3-serato-support/)) |
| CuePorter | Python, MIT | MIK cues | Markers2 | Restyles MIK cues; not needed |

### 6.3 Every in-place write risk, and the mitigation
1. **mutagen rewrites the tag of whatever file it's pointed at, in place.** → A hard-coded path allowlist: refuse anything outside `~/Music/_CueTest-2026-10/` and anything without `[autocue]` in the name. Hash originals before and after (README protocol).
2. **A copy inherits the original's cues.** → Skip if any CUE/LOOP/FLIP is in Markers2 *or* Markers_. Log the count. *If MIK already exported cues across his library, many tracks will be skipped; report that rather than overwrite.*
3. **Legacy `Serato Markers_` overrides Markers2** (per Holzhaus), and serato-tools deletes it by default. → An explicit, logged policy (§6.1), tested on 1 copy first.
4. **ID3 version change:** mutagen saves ID3v2.4 by default. If the copy was v2.3, other apps may see changed text frames. → Save with the copy's original version (`v2_version=3` when applicable), then diff all non-Serato frames before/after on the copy.
5. **Decoder offset:** our analysis decodes MP3 → WAV. Decoders disagree by about 20–40 ms (F37), and Mixxx reports that decoder differences shift imported Serato cues by "a few milliseconds" ([Mixxx blog](https://mixxx.org/news/2021-02-08-new-in-2-3-serato-support/)). → Snap to the copy's own `Serato BeatGrid` when present. On the pilot, compare one auto cue against Cullen's hand cue on the same downbeat and set a per-format offset if needed.
6. **Serato's library cache:** Serato 3.x `database V2` and 4.x `master.sqlite` cache cue data. → Write tags **before** the copies are ever added to Serato. Never run library-wide "Rescan ID3 Tags." Never edit DB/crate files (serato-tools can; we don't).
7. **Malformed tag → Serato misbehaves.** → Round-trip check: after writing, re-parse with serato-tools **and** triseratops; byte-compare the cue payload to what we intended; open 1 track in Serato before doing the other 9.
8. **M4A 5-cue limit / FLAC / WAV / .aif unsupported in serato-tools.** → v1 does MP3 (+ `.aiff`) only; other types are skipped and logged until Cullen confirms his formats.
9. **Crates:** never written by code. Cullen or DJ Bot creates `NEW 2026-10 – Cue Test` in Serato by hand and drags in only the pilot folder.
10. **Sidecar backup:** before any write, dump every `Serato *` GEOB frame of the copy to `_meta/<file>.serato-geob.bak` so a copy can be restored exactly.

---

## 7. Evaluation method

**Inputs:** `groundtruth.csv` (Cullen) and `predictions.csv` (model, same schema), keyed by `track` + `slot`. Bar length = 4 beats × 60 / BPM (2.0 s at 120 BPM, 2.4 s at 100 BPM).

**Per cue (slot A–H on each track):**
- **correct**: |pred − truth| ≤ 0.5 s (roughly 1 beat at 120 BPM; after snapping, normally the same downbeat).
- **within 1 bar**: 0.5 s < error ≤ 1 bar.
- **wrong**: error > 1 bar, *or* the model set a slot Cullen left empty, *or* the model left empty a slot Cullen set.
- **label check:** A's bar-count text (`16`/`8`/`4`/`2`/`the 3`) must match exactly. C/G labels are scored only in lyric mode.
- **H:** if H = `go`, score it like any slot. Also report "H correct given D correct" (it's arithmetic, so its errors should only come from D). If H = empty/spare, score "correctly empty" or the spare `8` like A.

**Per engine, reported:**
1. Hit rate within ±0.5 s and within ±1 bar, **per slot** (A–H) and overall, split by genre bucket.
2. The literature metrics HR.5F and HR3F over all section boundaries (via [mir_eval](https://github.com/mir-evaluation/mir_eval)), so we can compare against F14–F19.
3. **Cullen's acceptance review on the pilot copies** (on screen and on the CDJ-3000 pads): each cue marked *correct / within 1 bar (nudge) / wrong*. This is the primary metric.
4. Review time per track vs his ~2 min manual baseline.
5. Safety checks: originals' SHA-256 unchanged, Wedding Sets crate files unchanged, no writes outside the pilot folder. **Any failure = stop.**

**"Good enough to ship a draft" — PROPOSAL (not a published standard):**
- Zero safety violations, **and**
- A, B, D: ≥ 80% *within 1 bar* and ≥ 60% *correct*, **and**
- C, G: ≥ 70% *within 1 bar*, **and**
- E: ≥ 80% accepted by Cullen (a clean 8-bar loop, any reasonable spot), **and**
- median review time ≤ 60 s per track.

If only A/B/D pass, ship a narrower draft (A, B, D, E, H) and leave C/G manual. If A/B/D fail, stop and keep the manual workflow. Calibrate these bars against Cullen's own week-later consistency (§4.2): the tool can't be held to a higher standard than his own re-labeling.

---

## 8. Recommended v1 stack (for PM Bot's SPEC.md)

| Layer | v1 choice | Why | Alternatives / later |
|---|---|---|---|
| Decode | ffmpeg → 44.1 kHz WAV, temp folder on the box | allin1 author's advice (F37) | — |
| Grid | **Serato BeatGrid tag from the copy**; else **Beat This!** (MIT) | Lines up with what Cullen sees; Beat This! generalizes best of the open options | allin1 downbeats; masked-diffusion Beat This later |
| Structure | **Run both allin1 (MIT) and SongFormer (CC BY 4.0 code + CC BY-NC MuQ)**; pick by score on his 20–30 tracks | SongFormer is better on paper (F14–F16); allin1 is fully MIT and outputs beats too | LinkSeg; a vote between engines |
| Vocals | HT Demucs (already inside allin1); Mel-RoFormer if vocal onsets look sloppy | Better vocal SDR (F23–F26) | — |
| Cue mapping | Rules (below), all snapped to downbeats | Deterministic, explainable | — |
| Labels | Fixed text default; lyric mode = WhisperX suggestion marked `?` | Sung WER 20–48% | Alignment if Cullen types lyrics |
| LLM | None in v1. Optional later: Gemini/text-LLM tie-break and explanations, with Cullen's OK | Timing must come from specialists | — |
| Review | `pilot-cues.csv` + a simple local HTML page (waveform, cue markers, one-line reason per cue) | Cullen reviews in about 1 minute | — |
| Write | serato-tools `TrackCuesV2` on **copies only**, behind the §6.3 wrapper | Only maintained Python writer | triseratops cross-check |
| Formats | MP3 (+ `.aiff`) | Library support | FLAC, M4A (with the legacy field) after the pilot |

**Mapping rules (PROPOSAL; every time snapped to a downbeat):**
- **A** = first downbeat of the intro phrase. Label = bars from A to B (16/8/4/2). If B isn't on beat 1 of a bar, label `the 3` (or the beat number) and flag it.
- **B** = first downbeat of the first non-intro section, refined by vocal onset (if vocals start within ±1 bar of the boundary, use the downbeat at or just before the vocal).
- **G** = first `chorus` start. Label `hook` (fixed mode) or suggested lyric (lyric mode).
- **C** = last `chorus` start. Label `last chorus`. If a `bridge` sits right before it, flag "bridge?" for Cullen.
- **D** = end of the last `chorus` (start of the next segment, usually outro). Label `out`.
- **E** = 8-bar loop at A (intro) by default; else the first 8 bars of the outro. Label `loop`. Saved loop vs hot cue is still undecided.
- **F** = blank.
- **H** = config: `go` → D − 16 bars (64 beats), flag if that lands before B. `spare` → A + 8 bars, GREEN, `8`, only if the intro is ≥ 16 bars. `empty` → nothing.
- Each row carries a `reason` string (segment label, model confidence, vocal on/off, bar math) for the review page.

**What still needs Cullen's decisions (from Chief's list):** H mode (and color if `go`); fixed vs lyric labels for C/G; Serato version and file types; whether he owns MIK and whether its cue export is on (that decides how many tracks get skipped for already having cues); OK to copy 20–30 tracks to the box; OK for any paid API.

---

## Corrections to README (better sources found today)
- **serato-tools license:** README said MIT. Its `pyproject.toml` declares MIT, but the repo has **no LICENSE file** (GitHub reports none). It writes **`.mp3` and `.aiff` only** (the `.aif` extension isn't matched in code).
- **SongFormer:** README's numbers stand (HR.5F 0.703). New: its inference downloads **`MuQ-large-msd-iter` (CC BY-NC 4.0)**, and it has **no beat tracking**.
- **Demucs:** the `facebookresearch/demucs` GitHub repo is **archived** (read-only). It still works; just no fixes.
- **Markers_ vs Markers2:** README said Serato prefers Markers_ when present (Holzhaus). serato-tools says Markers_ is unused in newer Serato. These sources disagree; the pilot must test it on Cullen's version.
- **M4A quirk:** README paraphrased; the source's exact wording (quoted in §6.1) is ambiguous. Treat M4A as "verify on pilot."
- **Beat tracker choice:** README listed madmom as allin1's dependency only. New: **Beat This!** (MIT, 2024) and a 2026 masked-diffusion follow-up exist and are stronger general beat trackers. On pop downbeats, allin1's own Harmonix number (0.915) is not beaten by Beat This! (0.907), and the setups differ.

## Sources (all accessed Oct 8, 2026)
Papers: allin1 [2307.16425](https://arxiv.org/abs/2307.16425) · SongFormer [2510.02797](https://arxiv.org/abs/2510.02797) · Beat This! [2407.21658](https://arxiv.org/abs/2407.21658) · Masked diffusion beat tracking [2608.04624](https://arxiv.org/abs/2608.04624) · HingeNet [2508.09788](https://arxiv.org/html/2508.09788v2) · BeatFM [2508.09790](https://www.arxiv.org/pdf/2508.09790v1) · BeatNet [2108.03576](https://arxiv.org/abs/2108.03576) · MIREX 2025 beat [results](https://music-ir.org/mirex/wiki/2025:Audio_Beat_Tracking_Results) · Chorus detection [2103.14253](https://arxiv.org/abs/2103.14253) · HT Demucs [2211.08553](https://arxiv.org/abs/2211.08553) · BS-RoFormer [2309.02612](https://arxiv.org/abs/2309.02612) · Mel-RoFormer [2310.01809](https://arxiv.org/abs/2310.01809), [2409.04702](https://arxiv.org/abs/2409.04702) · Drops [ISMIR 2014](https://archives.ismir.net/ismir2014/paper/000297.pdf) · GiantSteps key/tempo [ISMIR 2015](https://archives.ismir.net/ismir2015/paper/000246.pdf) · Key CNN [1808.05340](https://arxiv.org/abs/1808.05340) · Jam-ALT [2408.06370](https://arxiv.org/abs/2408.06370) · Whisper + separation [2506.15514](https://arxiv.org/abs/2506.15514) · Lyric alignment [DAFx 2025](https://dafx.de/paper-archive/2025/DAFx25_paper_15.pdf), [ETH](https://tik-db.ee.ethz.ch/file/45c7793c1a3a476d56f784f74d668d74/), [MIREX 2024](https://music-ir.org/mirex/wiki/2024:Lyrics-to-Audio_Alignment_Results), [Spotify 2306.07744](https://arxiv.org/abs/2306.07744).
Code: [all-in-one](https://github.com/mir-aidj/all-in-one) · [SongFormer](https://github.com/ASLP-lab/SongFormer) · [MuQ weights](https://huggingface.co/OpenMuQ/MuQ-large-msd-iter) · [beat_this](https://github.com/CPJKU/beat_this) · [md_beat_this](https://github.com/fosfrancesco/md_beat_this) · [BeatNet](https://github.com/mjhydri/BeatNet) · [madmom](https://github.com/CPJKU/madmom) · [msaf](https://github.com/urinieto/msaf) · [demucs](https://github.com/facebookresearch/demucs) · [MSST](https://github.com/ZFTurbo/Music-Source-Separation-Training) · [whisperX](https://github.com/m-bain/whisperX) · [libKeyFinder](https://github.com/mixxxdj/libKeyFinder) (GPL-3.0) · [essentia](https://github.com/MTG/essentia) (AGPL-3.0) · [mir_eval](https://github.com/mir-evaluation/mir_eval).
Serato format: [serato-tags docs](https://github.com/Holzhaus/serato-tags/tree/main/docs) · [serato-tools](https://github.com/bvandrc/serato-tools) · [triseratops](https://github.com/Holzhaus/triseratops) · [Mixxx Serato import](https://mixxx.org/news/2021-02-08-new-in-2-3-serato-support/).
Datasets: [Harmonix](https://github.com/urinieto/harmonixset) · [SongFormBench](https://huggingface.co/datasets/ASLP-lab/SongFormBench) · [SALAMI](https://ddmal.ca/research/salami/annotation/) · [RWC (Zenodo)](https://zenodo.org/records/17177919) · [Isophonics](https://isophonics.net/datasets) · [JamendoLyrics](https://huggingface.co/datasets/jamendolyrics/jamendolyrics) · [Jam-ALT](https://audioshake.github.io/jam-alt/).
APIs/vendors: [Gemini audio](https://ai.google.dev/gemini-api/docs/audio) · [OpenAI audio](https://developers.openai.com/api/docs/guides/audio-chat-completions) · [OpenAI deprecations](https://developers.openai.com/api/docs/deprecations) · [Mixed In Key 11](https://mixedinkey.com/learn-more/).
