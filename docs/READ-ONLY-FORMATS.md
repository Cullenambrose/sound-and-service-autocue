# Read-only selected audio formats

`SelectedFiles` inspects only canonical, explicitly listed regular files. It
does not enumerate folders or modify the selected media. Inspection reads the
file's tags, hashes its contents, and compares file identity and timestamps
before and after the read; a file that changes while being inspected is
rejected. Later validation also fails closed when the recorded fingerprint no
longer matches.

The generated format test covers WAV, MP3, FLAC, AIFF, M4A, Ogg/Opus (`.ogg`),
and Opus (`.opus`). It uses short synthetic tones and generated tags only; it
does not inspect or write real media. The test uses an already-installed
`ffmpeg` executable and skips the matrix if `ffmpeg` is unavailable. It does
not install encoders or other dependencies.

The test verifies title, artist, duration, BPM, and Unicode tags through
`SelectedFiles`. Version tags are read for WAV, MP3, FLAC, AIFF, Ogg/Opus, and
Opus; the current Mutagen easy-tag mapping does not expose a version field for
M4A, so M4A is checked for its title and artist and returns an empty version.
An empty version is allowed. Title and artist must be nonempty strings;
version may be empty. BPM, when present, and duration, when known, must be
finite and nonnegative. An unknown duration (`null`) is allowed.

File metadata and content hashes do not replace the identity/timestamp
fingerprint: timestamp changes, including `ctime`, invalidate a selection even
when the media payload appears unchanged. Select the file again after any
such change.
