# Native client-request intake

In **Set planner → Import client requests**, paste text or choose a UTF-8 text file. Select the input format and default category, then select **Preview requests**.

Supported formats:

- **One title per line**: each nonblank line is a literal title. Artist/title separators are not guessed. Review and fill in artists and versions in the preview.
- **CSV with headers** or **TSV with headers**: `title` is required; optional columns are `artist`, `version`, `category`. Headers are case-insensitive. Quoted fields, escaped quotes, embedded newlines, CRLF and a UTF-8 BOM are supported. Unknown/duplicate columns and malformed quoting or row widths are refused rather than discarded.
- Category values are exactly `must_play`, `do_not_play`, `play_if_possible`. Blank category cells use the chosen default. Unsupported categories are refused.

Example CSV:

```csv
title,artist,version,category
Synthetic first dance,Invented Artist,Radio,must_play
Synthetic exclusion,Invented Artist,,do_not_play
Synthetic optional,Another Artist,Live,play_if_possible
```

The preview is editable: title, artist, exact version and category can change; individual rows can be removed. Source record numbers refer to logical CSV records, not physical lines inside quoted multiline fields. Requests retain order within each destination category and repeated requests are retained. Nothing is matched or appended automatically.

**Add reviewed requests to event draft** appends to the current event's request lists and preserves existing requests, special moments, client playlists and other event fields. Invalid existing lists are refused. **Discard import preview** changes no event data. Changing source text, format or default category invalidates the preview. Unapplied import previews are temporary view state; applying updates the shared event draft for this app session, not a durable booking record or a saved crate.

After applying, use **Generate alternatives**. The existing backend resolver and planner remain responsible for matching, exact-version requirements, missing must-plays and exclusion conflicts. An imported request is not a resolved request, a purchased song or proof of playback. No new matcher, backend endpoint or source-media operation is introduced.

Limits: 64 KiB UTF-8 input/file, 1–200 reviewed requests per import, and at most 1 KiB per title/artist/version field. Blank titles, unsupported categories and control characters other than tabs/newlines are refused. The file chooser reads at most 65,537 bytes before enforcing the limit; it does not load an arbitrarily large file. This milestone does not include column mapping for arbitrary vendor files, OCR, Spotify/provider access, bulk ambiguity-resolution UI or durable event storage.
