CUE REVIEW (Sound & Service auto-cue, v3)
HISTORICAL PROTOTYPE ONLY. Do not collect new corrections with this server:
its JSON saves are not transactional or retry-safe. Use ../run.sh and the
supported web/ app instead. See ../docs/FOUNDATION.md for migration and recovery.
The original research usage instructions below are retained for provenance.

1. Unzip this folder anywhere (e.g. Desktop).
2. Open Terminal, then:   cd ~/Desktop/cue-review && python3 serve.py
3. Open http://localhost:8765 in Safari/Chrome.
Audio plays read-only from ~/Music/Master Music (set MUSIC_DIR=... to change). Nothing is written to your music or Serato.
Every button press is saved to corrections.json in this folder. Send that file back to tune the rules.
