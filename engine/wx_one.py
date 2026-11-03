"""WhisperX word-level transcription of LOCAL vocal stems (box only)."""
import argparse
import csv
import json
import os
import sys
import threading
from collections import OrderedDict
import torch

# PyTorch >= 2.6 defaults to weights_only=True, which rejects the pyannote VAD checkpoint that
# WhisperX bundles. This module only loads those trusted model files.
_torch_load = torch.load
def _trusted_load(*args, **kwargs):
    kwargs["weights_only"] = False
    return _torch_load(*args, **kwargs)
torch.load = _trusted_load
import whisperx

torch.set_num_threads(4)

_model = None
_aligners = OrderedDict()
_transcription_lock = threading.Lock()


def transcribe_local(wav, language=None):
    global _model
    with _transcription_lock:
        if _model is None:
            _model = whisperx.load_model("small", "cpu", compute_type="int8")
        record = transcribe(wav, _model, language, _aligners)
        lang = record["language"]
        if lang in _aligners:
            _aligners.move_to_end(lang)
        while len(_aligners) > 2:
            _aligners.popitem(last=False)
        return record


def vocal_window(audio, rate=16000, seconds=30):
    """The loudest `seconds`-long stretch of a vocal stem; the first 30s are often an instrumental intro."""
    per_second = [float((audio[i:i + rate] ** 2).mean()) for i in range(0, max(1, len(audio) - rate + 1), rate)]
    if len(per_second) <= seconds:
        return audio
    totals = [sum(per_second[i:i + seconds]) for i in range(len(per_second) - seconds + 1)]
    start = totals.index(max(totals)) * rate
    return audio[start:start + seconds * rate]


def transcribe(wav, model, language=None, aligner_cache=None):
    audio = whisperx.load_audio(wav)
    if not language:
        language = model.transcribe(vocal_window(audio), batch_size=4).get("language")
    options = {"language": language} if language else {}
    result = model.transcribe(audio, batch_size=4, **options)
    lang = result.get("language", language or "en")
    align_error = None
    aligner_cache = {} if aligner_cache is None else aligner_cache
    aligned = aligner_cache.get(lang)
    if aligned is None:
        try:
            aligned = whisperx.load_align_model(language_code=lang, device="cpu")
            aligner_cache[lang] = aligned
        except Exception as error:
            align_error = str(error)
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
            with open(output, encoding="utf-8") as cached:
                record = json.load(cached)
            if not record.get("alignment_error"):
                counts["skipped"] += 1
                continue
        if not os.path.isfile(audio):
            counts["missing_audio"] += 1
            print(f"{source_id}: missing vocal stem: {audio}", file=sys.stderr, flush=True)
            continue
        pending.append((source_id, audio, output))
    if not pending:
        return counts
    for source_id, audio, output in pending:
        record = transcribe_local(audio, language)
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
    record = transcribe_local(args.wav, language)
    _save_record(args.out, record)
    if record["alignment_error"]:
        print("word alignment failed: " + record["alignment_error"], file=sys.stderr)
    print(record["language"], len(record["words"]), "timed words")


if __name__ == "__main__":
    main()
