# Serato Precision DJ Cue & Loop Engine — Coding Agent Handover

## Overview for Coding Agent
This project is an automated DJ cue point and saved-loop generator for Serato DJ Pro. It combines:
1. **Stem Separation:** Mel-Band RoFormer & Karaoke RoFormer (via `audio-separator`) to isolate clean lead vocals, bass, drums, and instruments.
2. **Beat & Downbeat Tracking:** `Beat This!` (CP-JKU) snapped to low-end kick transients (20-120Hz) and synced to `Serato BeatGrid`.
3. **Lexical Vocal Alignment:** `WhisperX` bottom-up transcription matched against official lyric references (`Genius` / `LRCLIB`) using `RapidFuzz`.
4. **Parametric Cue Routing:** An SQLite blueprint cache + customizable `DJCueProfile` allowing DJs to tweak pre-vocal bars, mix-out warning bars, and mix-out targets.
5. **Serato Integration:** Direct ID3 tag writing of Hot Cues (`CUE`) and Saved Loops (`LOOP`) into `Serato Markers2`.
