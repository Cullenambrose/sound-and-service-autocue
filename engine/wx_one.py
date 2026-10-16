"""WhisperX word-level transcription of a LOCAL 16 kHz vocal stem (box only)."""
import sys, json, torch, whisperx
torch.set_num_threads(4)
wav, out = sys.argv[1], sys.argv[2]
model = whisperx.load_model("small", "cpu", compute_type="int8"); audio = whisperx.load_audio(wav)
res = model.transcribe(audio, batch_size=4, **({"language": sys.argv[3]} if len(sys.argv) > 3 else {})); lang = res.get("language", "en"); del model
try:
    am, meta = whisperx.load_align_model(language_code=lang, device="cpu"); res = whisperx.align(res["segments"], am, meta, audio, "cpu", return_char_alignments=False)
except Exception as e: print("align failed", e)
words = [w for s in res["segments"] for w in s.get("words", []) if "start" in w]
json.dump({"language": lang, "segments": [{"start": s.get("start"), "end": s.get("end"), "text": s.get("text")} for s in res["segments"]], "words": words}, open(out, "w"))
print(lang, len(words), "words")
