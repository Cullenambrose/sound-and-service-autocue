"""Utilities for aligning LRCLIB synced lyrics to local WhisperX word times."""
import math
import re
import unicodedata
from collections import Counter

TIMED_LINE = re.compile(r"^\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]\s*(.*)$")


def normalize(text):
    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    return " ".join(re.findall(r"[^\W_']+", normalized, flags=re.UNICODE))


def parse_synced_lyrics(text):
    lines = []
    for line in (text or "").splitlines():
        match = TIMED_LINE.match(line.strip())
        if not match:
            continue
        minute, second, fraction, lyric = match.groups()
        if not normalize(lyric):
            continue
        fraction_s = int(fraction or 0) / (10 ** len(fraction or "1"))
        timestamp = int(minute) * 60 + int(second) + fraction_s
        lines.append({"time_s": timestamp, "text": lyric.strip(), "normalized": normalize(lyric)})
    return lines


def _word_tokens(words):
    tokens = []
    for word in words or []:
        token = normalize(word.get("word") or word.get("text"))
        if token and word.get("start") is not None:
            tokens.append((token, float(word["start"]), float(word.get("end", word["start"]))))
    return tokens


def _line_match(line_tokens, transcript_tokens, start_at=0):
    if not line_tokens:
        return None
    # Allow missed or hallucinated words, but require a majority of lyric tokens.
    best = None
    minimum = max(1, math.ceil(len(line_tokens) * 0.65))
    for start in range(start_at, len(transcript_tokens)):
        matched = 0
        first = None
        cursor = start
        for token in line_tokens:
            found = next((i for i in range(cursor, min(len(transcript_tokens), cursor + 3))
                          if transcript_tokens[i][0] == token), None)
            if found is not None:
                if first is None:
                    first = found
                matched += 1
                cursor = found + 1
        if matched >= minimum and (best is None or matched > best[0]):
            best = (matched, first)
    return None if best is None else transcript_tokens[best[1]][1]


def _linear_fit(points):
    if len(points) < 2:
        return 1.0, points[0][1] - points[0][0] if points else 0.0
    x_mean = sum(x for x, _ in points) / len(points)
    y_mean = sum(y for _, y in points) / len(points)
    denom = sum((x - x_mean) ** 2 for x, _ in points)
    scale = sum((x - x_mean) * (y - y_mean) for x, y in points) / denom if denom else 1.0
    return scale, y_mean - scale * x_mean


def find_repeated_choruses(lines, bar_s):
    counts = Counter(line["normalized"] for line in lines)
    repeated = [line for line in lines if counts[line["normalized"]] >= 2]
    if len(repeated) < 2:
        return []
    max_gap = max(8.0, 4 * bar_s)
    clusters = []
    for line in repeated:
        if not clusters or line["time_s"] - clusters[-1][-1]["time_s"] > max_gap:
            clusters.append([line])
        else:
            clusters[-1].append(line)
    spans = []
    for cluster in clusters:
        distinct = len({line["normalized"] for line in cluster})
        if distinct < 2:
            continue
        spans.append({
            "start_s": cluster[0]["time_s"],
            "end_s": cluster[-1]["time_s"] + max(1.5, bar_s),
            "confidence": min(0.9, 0.55 + distinct * 0.05),
            "source": "synced_lyrics_repeated_lines",
        })
    return spans


def align_synced_lyrics(synced_text, words, bar_s, max_residual_s=2.5):
    lines = parse_synced_lyrics(synced_text)
    transcript = _word_tokens(words)
    if not lines or not transcript:
        return {"status": "rejected", "reason": "synced lyrics or word timings are unavailable",
                "offset_s": None, "tempo_ratio": None, "matched_lines": 0, "coverage": 0.0, "choruses": []}

    points = []
    cursor = 0
    for line in lines:
        matched_time = _line_match(line["normalized"].split(), transcript, cursor)
        if matched_time is not None:
            matched_index = next((i for i in range(cursor, len(transcript))
                                  if transcript[i][1] == matched_time), None)
            points.append((line["time_s"], matched_time))
            if matched_index is not None:
                cursor = matched_index + 1
    scale, offset = _linear_fit(points)
    residuals = [abs(y - (scale * x + offset)) for x, y in points]
    median_residual = sorted(residuals)[len(residuals) // 2] if residuals else float("inf")
    coverage = len(points) / len(lines)
    accepted = len(points) >= 2 and coverage >= 0.2 and 0.9 <= scale <= 1.1 and median_residual <= max_residual_s
    if not accepted:
        return {
            "status": "rejected",
            "reason": "poor lyrics-to-transcript alignment",
            "offset_s": round(offset, 3) if points else None,
            "tempo_ratio": round(scale, 5) if points else None,
            "matched_lines": len(points),
            "coverage": round(coverage, 3),
            "median_residual_s": None if not residuals else round(median_residual, 3),
            "choruses": [],
        }

    choruses = find_repeated_choruses(lines, bar_s)
    for span in choruses:
        span["start_s"] = round(scale * span["start_s"] + offset, 3)
        span["end_s"] = round(scale * span["end_s"] + offset, 3)
        span["confidence"] = round(span["confidence"] * min(1.0, coverage / 0.5), 2)
    return {
        "status": "aligned",
        "reason": "synced lyric lines matched WhisperX word timings",
        "offset_s": round(offset, 3),
        "tempo_ratio": round(scale, 5),
        "matched_lines": len(points),
        "coverage": round(coverage, 3),
        "median_residual_s": round(median_residual, 3),
        "choruses": choruses,
    }
