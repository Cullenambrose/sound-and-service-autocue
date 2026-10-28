# Per-song analysis format (draft)

AutoCue writes one JSON object per track after analysis. Booth Buddy (and later the Mac prep app) can read the same file without depending on AutoCue’s Python package.

**Status:** draft. Fields may gain names but should stay additive. Version bump `schema_version` on breaking changes. The v6 cue-plan contract is schema version 2; the original sample below documents the earlier v1 analysis object.

The October 28 arithmetic/label update adds `phrasing_version: "v6.2"` to new
plans without changing schema version 2, the `v7` planner-family version, or the
`v6.1-pop-vocal` pad-layout identifier. Pop/Vocal labels now spell out bars and
beats; known old templates migrate only on settings normalization/reanalysis.
Stored plans and already-written tags are not silently converted. Human review
answers retain `time_s` and `unsnapped` so vocal pickups are not scored against
their structural bar. See [the phrasing audit](./PM%20BOT%20READ%20ME.md).

## File naming

Suggested: `<artist> - <title>.analysis.json` next to the audio, or a content-addressed store keyed by `file_hash` / `audio_sha256`. Paths to audio are optional and machine-local; consumers should prefer `audio_sha256` + library index.

## Schema (draft)

```json
{
  "schema_version": 1,
  "analyzed_at": "2026-10-15T21:00:00-07:00",
  "engine": {
    "name": "sound-and-service-autocue",
    "rules_version": "v5",
    "analyzers": ["beat_this", "allin1", "songformer?", "demucs?", "whisperx?"]
  },
  "identity": {
    "title": "string",
    "artist": "string",
    "audio_sha256": "hex",
    "duration_s": 0.0,
    "source_path": null
  },
  "bpm": {
    "value": 128.0,
    "confidence": 0.0,
    "stable": true,
    "drift_ratio": 0.0
  },
  "grid": {
    "beat_times_s": [0.0],
    "downbeat_times_s": [0.0],
    "bar_times_s": [0.0],
    "meter": "4/4"
  },
  "sections": [
    {
      "label": "intro|verse|chorus|bridge|outro|other",
      "start_s": 0.0,
      "end_s": 0.0,
      "start_bar": 0,
      "confidence": 0.0,
      "source": "allin1|songformer|consensus"
    }
  ],
  "cues": {
    "A": { "name": "8BS", "time_s": 0.0, "bar": 0, "confidence": 0.0, "auto_set": true, "status": "auto|suggest|empty|review" },
    "B": { "name": "START", "time_s": 0.0, "bar": 0, "confidence": 0.0, "auto_set": true, "status": "auto|suggest|empty|review" },
    "C": { "name": "1st Chorus", "time_s": 0.0, "bar": 0, "confidence": 0.0, "auto_set": false, "status": "suggest|empty|review" },
    "D": { "name": "Last Chorus", "time_s": 0.0, "bar": 0, "confidence": 0.0, "auto_set": false, "status": "suggest|empty|review" },
    "E": null,
    "F": null,
    "G": { "name": "GO", "time_s": 0.0, "bar": 0, "confidence": 0.0, "auto_set": true, "status": "auto|suggest|empty|review" },
    "H": { "name": "EXIT", "time_s": 0.0, "bar": 0, "confidence": 0.0, "auto_set": true, "status": "auto|suggest|empty|review" }
  },
  "loops": {
    "intro": { "start_s": 0.0, "length_bars": 4, "confidence": 0.0, "clean": true, "phrase_aligned": true },
    "outro": { "start_s": 0.0, "length_bars": 4, "confidence": 0.0, "clean": true, "phrase_aligned": true }
  },
  "energy": {
    "rms_envelope": null,
    "drop_candidates_s": [],
    "notes": "optional compact summary; full envelopes may live in a sidecar"
  },
  "vocals": {
    "first_vocal_s": null,
    "cold_start": false,
    "chorus_lyric_fingerprint": null
  },
  "genre_bucket": "pop|hiphop_rnb|house_edm|latin|other",
  "confidence": {
    "overall": 0.0,
    "needs_review": true,
    "flags": ["tempo_drift", "cold_start", "low_chorus_agreement"]
  },
  "gemini": {
    "used": false,
    "model": null,
    "suggestions": {}
  },
  "review": {
    "marked_at": null,
    "overrides": {}
  }
}
```

## Booth Buddy use (later)

- Filter crates by `genre_bucket`, BPM, and `confidence.overall`.
- Prefer tracks with solid `B`/`H` auto-set for open-format mixing.
- Never require Gemini at play time; prep-time suggestions are enough.

## Non-goals for v1

- Embedding raw stems or full Whisper transcripts in this file.
- Writing Serato GEOB tags from Booth Buddy (AutoCue / Mac prep app owns writes).

## v6 cue-plan fields

The v6 planner writes a separate plan per song with `schema_version: 2` and `engine.rules_version: "v6"`; it does not replace the v5 analysis or write Serato tags. The plan contains:

```json
{
  "track_id": "001",
  "version": "v6",
  "map_type": "DROP",
  "genre_bucket": "house_edm",
  "genre_phrase_bars": 32,
  "cue_order": ["A", "B", "C", "D", "E", "F", "G", "H"],
  "drops": [{"bar": 64, "time_s": 128.0, "confidence": 0.8, "reason": "…"}],
  "breakdowns": [{"bar": 56, "confidence": 0.8, "reason": "…"}],
  "lyric_choruses": [{"start_bar": 32, "end_bar": 40, "confidence": 0.8, "source": "aligned_synced_lyrics"}],
  "flags": [],
  "cues": {
    "A": {"name": "INTRO -16", "bar": 0, "time_s": 0.0, "confidence": 0.7, "reason": "…"},
    "B": {"name": "START", "bar": 16, "time_s": 32.0, "confidence": 0.82, "reason": "…"}
  }
}
```

Every cue includes a confidence, reason, review flags, and candidate bars. The plan contains all eight slots A–H in order, plus the resolved cue meaning, label, and Serato palette color for each cue. The settings are user-editable in the web app and apply to new analyses; re-analyze a track to apply changed settings to an existing plan.

Default VOCAL map (v6.1 pad layout with v6.2 explicit-unit labels):
A `Intro 8 bars (32 beats)` (red `#CC0000`, 8/4/2/1 bars before START's structural
anchor, not necessarily Bar 0), B `START` (orange `#CC8800`, exact unsnapped vocal
onset; `start_anchor_bar` preserves the structural bar), C empty,
D `GO 8 bars (32 beats) > EXIT` (yellow `#CCCC00`),
E `Mix 4 bars (16 beats) > EXIT` (magenta `#CC00CC`),
F `EXIT 1st Chorus` (green `#00CC00`),
G `GO 8 bars (32 beats) > OUT` (teal `#00CCCC`), H `OUT` (purple `#8800CC`).
Countdowns can shorten to configured smaller windows; their labels report the
actual distance. OUT and chorus boundaries remain heuristic evidence requiring
listening, not certified clean-window endpoints.

Plans carry `cue_spec: "v6.1-pop-vocal"`, `phrasing_version: "v6.2"`,
`start_onset`, `exit_evidence` and `out_detection`. `pop_v61` gates the onset/exit/OUT
algorithms; selecting the older layout is a separate cue-map setting.
`cue_maps.legacy_v7_vocal_map()` and the DROP layout retain their earlier
configurable meanings. In those layouts `INTRO-{beats}` and `GO-{beats}` count
beats, but `EXIT -32` and `-32 DROP` offsets count bars. Never infer units from
the number alone. Custom meanings, labels, colors and disabled pads are supported.
AUTO selects between maps using existing evidence; forcing DROP does not invent
drops when none were detected.

Serato writes (MP3/AIFF) update `GEOB:Serato Markers2` and `GEOB:Serato Markers_` together so Serato never sees two disagreeing cue sets. Markers2 is `01 01` + 72-column base64 of the inner payload (`01 01`, `COLOR`, `CUE`s in pad order, preserved loops/flips, `BPMLOCK`, `00`) + `00`. Existing hot cues on pads 1–8, including Mixed In Key `Energy N` cues, are replaced; cues 9+ and loops are kept. The audio stream SHA-256 (bytes after the ID3v2 tag) must be identical before and after every write.

The palette stores Serato's metadata RGB value and uses its corresponding Pro/Lite display color for the UI. The mapping is based on the reverse-engineered [Triseratops Serato color reference](https://github.com/Holzhaus/triseratops/blob/8e92aae1794c4f02a2405eb88ea72f251b077f0c/src/tag/color.rs); it is not an official Serato specification. `lyric_choruses` contains only accepted synced-lyric matches; `chorus_spans` also records audio-section fallbacks. Transcripts, synced lyric cache, stems, and Gemini cache remain outside Git.

Build and review steps are local-only:

```bash
python3 engine/lrclib_synced.py --manifest /path/to/stress5/MANIFEST.csv --cache-dir /path/to/stress5/out/lrclib_synced
python3 engine/wx_one.py --manifest /path/to/stress5/MANIFEST.csv --vocal-dir /path/to/stress5/out/allin1/vocals --words-dir /path/to/stress5/out/words
python3 engine/rules_v5.py --root /path/to/stress5 --out /path/to/stress5/cues_v2/allin1/v5_go32
python3 engine/rules_v6.py --root /path/to/stress5 --manifest /path/to/stress5/MANIFEST.csv --out /path/to/stress5/cues_v6 --lyrics-dir /path/to/stress5/out/lrclib_synced --words-dir /path/to/stress5/out/words --genre-tags /path/to/stress5/genre_tags.json
python3 engine/build_v6_review.py --root /path/to/stress5 --manifest /path/to/stress5/MANIFEST.csv --v5-dir /path/to/stress5/cues_v2/allin1/v5_go32 --v6-dir /path/to/stress5/cues_v6 --features-dir /path/to/stress5/features --out /path/to/cue-review-v6
```

Serve that data without copying it into Git:

```bash
DATA_FILE=/path/to/cue-review-v6/data.json CORRECTIONS_FILE=/path/to/cue-review-v6/corrections.json MUSIC_DIR=/path/to/music PORT=8766 python3 review-ui/serve.py
```

For switch ablations, use `rules_v6.py --off SWITCH` or `--only SWITCH` to build each plan directory, then pass the extra directories as repeatable `--variant NAME=DIR` arguments to `engine/score_v6.py --v6-dir … --v5-dir … --marks /path/to/corrections.json`. The report is generated under `docs/SCORE-v6-<date>.md`; no meaningful score can be produced until review marks are available.

Gemini remains off by default. To use the text-only candidate tie-breaker, first set `switches.gemini_tiebreaker` to `true` in `config/genre_params_v6.json`, then run `engine/gemini_v6.py --enable` with a local cache directory and `GEMINI_API_KEY`. It writes a complete plan directory, caches each response, sends no audio or transcript text, and respects the configured confidence threshold and spend cap (at most $3).
