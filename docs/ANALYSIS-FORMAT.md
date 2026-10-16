# Per-song analysis format (draft)

AutoCue writes one JSON object per track after analysis. Booth Buddy (and later the Mac prep app) can read the same file without depending on AutoCue’s Python package.

**Status:** draft. Fields may gain names but should stay additive. Version bump `schema_version` on breaking changes.

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
