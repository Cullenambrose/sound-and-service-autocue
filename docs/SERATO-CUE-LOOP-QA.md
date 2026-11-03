# Serato 5 Cue Loop fallback: verification required

## Desired behavior

When no clean 8/4/2/1-bar intro fits:

1. Put START on the first playable BeatGrid downbeat.
2. Find an outro drum loop with no detected vocals or speech throughout it.
3. Assign that later loop to **pad A as a genuine Cue Loop**, before pad B START
   in pad order, although A's audio timestamp is later than B's.
4. Prefer 8 bars / 32 beats, then 4/2/1 bars, and label the actual length.
5. Do not edit the audio or write Track #1.

The user confirmed the first playable downbeat rather than exact file time zero.
This is a distinct cue type and fallback policy, not an ordinary cue shifted late.

## What is verified

Serato's official [Cue Loops guide](https://support.serato.com/hc/en-us/articles/18050017443087-Cue-Loops)
confirms Serato DJ Pro **5.0+** can save a cue and loop on one pad. Enable
**Setup > DJ Preferences > Control Preferences > Enable Cue Loops**. While a
loop is active and the playhead is inside it, create a cue in an empty slot.
It saves the loop start and length together. Triggering that pad jumps and loops.
Editing the live loop does not edit the stored Cue Loop; delete and recreate it.

The [5.0 download notes](https://serato.com/dj/pro/downloads) confirm the feature.
To keep a later-time loop on A and an earlier START on B, disable **Sort Cues and
Loops Chronologically**; Serato's
[cue ordering guide](https://support.serato.com/hc/en-us/articles/233435487)
requires this setting to manually reorder cues.

## Blocker: the persisted link is not verified

Public documentation for the older Markers2 `CUE` and `LOOP` entries does **not**
establish how Serato 5 links a loop to a hot-cue pad. The official guide documents
behavior, not serialization. No verified current public parser was found in the
bounded research. That does not prove a format description cannot exist elsewhere.

**Do not manufacture a CUE plus an unrelated saved LOOP and call it a Cue Loop.**
Reserved bytes, a matching index or an unknown entry name are not evidence of
linkage. The writer is unchanged until real Serato-created samples establish it.
The current planner records a **proposal**, not applied fallback cues. START and
Intro positions remain unchanged; the UI explicitly says nothing was applied.

## Safe fixture workflow on the DJ MacBook Pro

Use only this generated test file, never Happy or another library song. The
generator makes original synthetic percussion; it reads no music library files.
The capture command reads tags but never changes the MP3. It verifies audio
identity against its manifest and refuses to overwrite an existing snapshot.
Quit Serato before every command so its writes have flushed.

From the repository root, with the current branch pulled:

```bash
.venv/bin/python scripts/cue_loop_fixture.py create \
  --directory "$HOME/Music/AutoCue Cue Loop QA"
```

This makes `AutoCue Synthetic Cue Loop QA.mp3` (40 seconds, 120 BPM) and
`fixture.json`. Keep the manifest beside the MP3. Load **that exact file** in
Serato 5.0+, analyze it, set/verify its grid, then create these states one at a
time. Keep the same grid, colors and label in all states.

| State / snapshot label | What to do in Serato |
|---|---|
| `baseline` | Analyze the synthetic file; no cues or saved loops. |
| `plain-cue` | Create one ordinary pad A cue near 16 seconds; loop off. |
| `cue-loop-32` | Delete A. Activate a 32-beat / 8-bar loop starting at the same downbeat near 16 seconds. Move inside it, create pad A. Confirm it retriggers a loop, not just a regular cue. |
| `cue-loop-16` | Delete A. Recreate it as a 16-beat / 4-bar Cue Loop at the same start. |
| `cue-loop-slot-b` | Delete A. Recreate that same 16-beat Cue Loop on B instead. |

After each state, unload the track and quit Serato. Capture it with the matching
label, for example:

```bash
.venv/bin/python scripts/cue_loop_fixture.py snapshot \
  --directory "$HOME/Music/AutoCue Cue Loop QA" --label baseline
```

Repeat with `--label plain-cue`, `cue-loop-32`, `cue-loop-16` and
`cue-loop-slot-b` after their respective Serato edits. Reopen Serato between
states. Confirm the saved Cue Loop still triggers after reloading before capturing.

Share the five snapshot JSON files and `fixture.json`, plus the exact Serato
version and the loop start/end shown by Serato. They contain raw GEOB tag payloads
from this synthetic file, not audio or your library database. If no tag differences
capture the new state, stop: the metadata may live outside GEOB. Do not share your
whole Serato library database; determine a smaller synthetic-only fixture first.

## Implementation gate after capturing

Compare ordinary cue vs Cue Loop, both loop lengths, and pad A vs pad B.
Implement only fields supported by those differences. Add binary fixtures and
round-trip tests that preserve unknown entries and unrelated loops. Then write
only a copy of the synthetic track and confirm in Serato that:

- The intended A pad jumps to the later outro and starts looping immediately.
- B jumps to the first playable downbeat without looping.
- Both cues and loop length persist across unload/reload/restart.
- Pad order survives with chronological sorting disabled.
- Audio payload hashes remain identical and the original has a verified backup.

Only after that gate should the fallback be enabled for reviewed real tracks.
