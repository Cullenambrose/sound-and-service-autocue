"""WhisperX word-level transcription of LOCAL vocal stems (box only)."""
import argparse
import csv
import json
import os
import sys
import torch
import whisperx

torch.set_num_threads(4)


def transcribe(wav, model, language=None, aligner_cache=None):
    audio = whisperx.load_audio(wav)
    options = {"language": language} if language else {}
    result = model.transcribe(audio, batch_size=4, **options)
    lang = result.get("language", language or "en")
    align_error = None
    if lang not in aligner_cache:
        try:
            aligner_cache[lang] = whisperx.load_align_model(language_code=lang, device="cpu")
        except Exception as error:
            align_error = str(error)
            aligner_cache[lang] = None
    aligned = aligner_cache[lang]
    if aligned is None and align_error is None:
        align_error = "word alignment model is unavailable for language " + lang
    if aligned is not None:
        align_model, metadata = aligned
        try:
            result = whisperx.align(result["segments"], align_model, metadata, audio, "cpu",
                                    return_char_alignments=False)
        except Exception as error:
            align_error = str(error)
    words = [word for segment in result["segments"] for word in segment.get("words", [])
             if word.get("start") is not None]
    return {"language": lang, "words": words, "alignment_error": align_error}


def _save_record(path, record):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as target:
        json.dump(record, target, ensure_ascii=False)
    os.replace(temporary, path)


def transcribe_manifest(manifest, vocal_dir, words_dir, language=None):
    with open(manifest, newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        fieldnames = set(reader.fieldnames or [])
        id_column = "id" if "id" in fieldnames else "n" if "n" in fieldnames else None
        if id_column is None:
            raise ValueError("manifest must have an 'id' or 'n' column")
        rows = list(reader)
    os.makedirs(words_dir, exist_ok=True)
    counts = {"transcribed": 0, "skipped": 0, "missing_audio": 0}
    pending = []
    for row in rows:
        track_id = (row.get(id_column) or "").strip()
        if not track_id:
            raise ValueError("manifest contains a row with an empty track ID")
        if os.path.basename(track_id) != track_id or track_id in (".", ".."):
            raise ValueError("manifest track IDs must be plain file-name components")
        source_id = track_id.zfill(2)
        output = os.path.join(words_dir, source_id + ".json")
        audio = os.path.join(vocal_dir, source_id + ".wav")
        if os.path.exists(output):
            counts["skipped"] += 1
            continue
        if not os.path.isfile(audio):
            counts["missing_audio"] += 1
            print(f"{source_id}: missing vocal stem: {audio}", file=sys.stderr, flush=True)
            continue
        pending.append((source_id, audio, output))
    if not pending:
        return counts
    model = whisperx.load_model("small", "cpu", compute_type="int8")
    aligner_cache = {}
    for source_id, audio, output in pending:
        record = transcribe(audio, model, language, aligner_cache)
        _save_record(output, record)
        counts["transcribed"] += 1
        print(f"{source_id}: {len(record['words'])} timed words", flush=True)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", nargs="?")
    parser.add_argument("out", nargs="?")
    parser.add_argument("language", nargs="?")
    parser.add_argument("--manifest")
    parser.add_argument("--vocal-dir")
    parser.add_argument("--words-dir")
    parser.add_argument("--language", dest="language_option")
    args = parser.parse_args()
    language = args.language_option or args.language
    if args.manifest:
        if not args.vocal_dir or not args.words_dir:
            parser.error("--manifest requires --vocal-dir and --words-dir")
        counts = transcribe_manifest(args.manifest, args.vocal_dir, args.words_dir, language)
        print(json.dumps(counts, sort_keys=True))
        if counts["missing_audio"]:
            raise SystemExit(1)
        return
    if not args.wav or not args.out:
        parser.error("provide WAV and output paths, or use --manifest")
    model = whisperx.load_model("small", "cpu", compute_type="int8")
    record = transcribe(args.wav, model, language, {})
    _save_record(args.out, record)
    if record["alignment_error"]:
        print("word alignment failed: " + record["alignment_error"], file=sys.stderr)
    print(record["language"], len(record["words"]), "timed words")


if __name__ == "__main__":
    main()
