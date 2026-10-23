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
        if int(second) >= 60 or not normalize(lyric):
            continue
        fraction_s = int(fraction or 0) / (10 ** len(fraction or "1"))
        timestamp = int(minute) * 60 + int(second) + fraction_s
        lines.append({"time_s": timestamp, "text": lyric.strip(), "normalized": normalize(lyric)})
    return sorted(lines, key=lambda line: line["time_s"])


def _word_tokens(words):
    tokens = []
    ignored = 0
    for word in words or []:
        if not isinstance(word, dict):
            ignored += 1
            continue
        start, end = word.get("start"), word.get("end")
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start):
            ignored += 1
            continue
        for token in normalize(word.get("word") or word.get("text")).split():
            tokens.append((token, start, end))
    return sorted(tokens, key=lambda token: token[1]), ignored


def _similar(left, right):
    if left == right:
        return True
    if min(len(left), len(right)) < 4:
        return False
    try:
        from rapidfuzz import fuzz
        return fuzz.ratio(left, right) >= 80
    except ImportError:
        from difflib import SequenceMatcher
        return SequenceMatcher(None, left, right).ratio() >= 0.8


def _line_matches(line_tokens, transcript_tokens, fuzzy=False):
    same = _similar if fuzzy else (lambda left, right: left == right)
    if len(line_tokens) < 3 or len(set(line_tokens)) < 2:
        return []
    matches = {}
    minimum = max(3, math.ceil(len(line_tokens) * 0.65))
    for start in range(len(transcript_tokens)):
        if not any(same(transcript_tokens[start][0], token) for token in line_tokens[:2]):
            continue
        indices = []
        cursor = start
        for token in line_tokens:
            found = next((i for i in range(cursor, min(len(transcript_tokens), cursor + 3))
                          if transcript_tokens[i][1] - transcript_tokens[start][1] <= max(12, len(line_tokens) * 1.5)
                          and same(transcript_tokens[i][0], token)), None)
            if found is not None:
                indices.append(found)
                cursor = found + 1
        if len(indices) >= minimum:
            first, last = indices[0], indices[-1]
            matches[(first, last)] = {
                "first_token": first, "last_token": last, "matched_tokens": len(indices),
                "start_s": transcript_tokens[first][1], "end_s": transcript_tokens[last][2],
            }
    # Do not count overlapping partial matches as separate repetitions.
    selected = []
    for match in sorted(matches.values(), key=lambda item: (-item["matched_tokens"], item["first_token"])):
        if not any(match["first_token"] <= old["last_token"] and old["first_token"] <= match["last_token"]
                   for old in selected):
            selected.append(match)
    return sorted(selected, key=lambda item: item["start_s"])


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
        if (not clusters or line.get("section") != clusters[-1][-1].get("section")
                or line["time_s"] - clusters[-1][-1]["time_s"] > max_gap):
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
            "end_s": cluster[-1].get("end_s", cluster[-1]["time_s"] + max(1.5, bar_s)),
            "confidence": min(0.9, 0.55 + distinct * 0.05),
            "source": "locally_matched_repeated_lyrics",
            "section": cluster[0].get("section"),
        })
    return spans


def align_synced_lyrics(synced_text, words, bar_s, max_residual_s=2.5, fuzzy=False):
    if not math.isfinite(bar_s) or bar_s <= 0 or not math.isfinite(max_residual_s) or max_residual_s < 0:
        raise ValueError("Lyric alignment requires a positive bar duration and nonnegative residual limit.")
    lines = parse_synced_lyrics(synced_text)
    transcript, ignored = _word_tokens(words)
    rejected = {"status": "rejected", "reason": "poor lyrics-to-transcript alignment",
                "offset_s": None, "tempo_ratio": None, "matched_lines": 0,
                "coverage": 0.0, "choruses": [], "sections": [], "matches": [],
                "ignored_word_timings": ignored, "version": 2}
    if not lines or not transcript:
        return {**rejected, "reason": "synced lyrics or valid word timings are unavailable"}

    candidates, occurrences = [], {}
    for index, line in enumerate(lines):
        text = line["normalized"]
        if text not in occurrences:
            occurrences[text] = _line_matches(text.split(), transcript, fuzzy)
        candidates.extend({**match, "line": index, "source_s": line["time_s"], "normalized": text}
                          for match in occurrences[text])
        if len(candidates) > 2048:
            return {**rejected, "reason": "lyrics alignment is too ambiguous (candidate limit exceeded)"}
    candidates.sort(key=lambda item: (item["start_s"], item["line"]))

    def fit(path):
        points = [(item["source_s"], item["start_s"]) for item in path]
        scale, offset = _linear_fit(points)
        residuals = [abs(y - (scale * x + offset)) for x, y in points]
        return scale, offset, max(residuals, default=0)

    paths = []
    for index, candidate in enumerate(candidates):
        best = [candidate]
        for previous in range(index):
            old = candidates[previous]
            source_gap = candidate["source_s"] - old["source_s"]
            audio_gap = candidate["start_s"] - old["start_s"]
            if (not 1 <= candidate["line"] - old["line"] <= 3 or source_gap <= 0
                    or candidate["first_token"] <= old["last_token"]
                    or candidate["start_s"] < old["end_s"]
                    or not 0.9 <= audio_gap / source_gap <= 1.1):
                continue
            trial = paths[previous] + [candidate]
            scale, _, residual = fit(trial)
            if 0.9 <= scale <= 1.1 and residual <= max_residual_s and len(trial) > len(best):
                best = trial
        paths.append(best)
    sections = []
    for path in sorted(paths, key=lambda path: (-len(path), path[0]["start_s"], path[0]["line"])):
        if len(path) < 2 or len({item["normalized"] for item in path}) < 2:
            continue
        if any(path[0]["first_token"] <= old[-1]["last_token"]
               and old[0]["first_token"] <= path[-1]["last_token"] for old in sections):
            continue
        sections.append(path)
    sections.sort(key=lambda path: path[0]["start_s"])
    matches = [{**item, "section": index} for index, path in enumerate(sections) for item in path]
    coverage = len({item["line"] for item in matches}) / len(lines)
    if len(matches) < 2 or coverage < 0.2:
        return {**rejected, "matched_lines": len(matches), "coverage": round(coverage, 3)}
    scale, offset, residual = fit(matches)
    global_fit = 0.9 <= scale <= 1.1 and residual <= max_residual_s
    choruses = find_repeated_choruses([
        {**item, "time_s": item["start_s"]} for item in matches], bar_s)
    return {
        "status": "aligned", "version": 2,
        "mode": "global" if global_fit else "sectionwise",
        "reason": "ordered local lyric matches; unsupported spans are not extrapolated",
        "offset_s": round(offset, 3) if global_fit else None,
        "tempo_ratio": round(scale, 5) if global_fit else None,
        "matched_lines": len(matches),
        "coverage": round(coverage, 3),
        "max_residual_s": round(residual, 3) if global_fit else None,
        "ignored_word_timings": ignored,
        "matches": matches,
        "sections": [
            {"start_s": path[0]["start_s"], "end_s": path[-1]["end_s"],
             "source_start_s": path[0]["source_s"], "source_end_s": path[-1]["source_s"],
             "tempo_ratio": round(fit(path)[0], 5), "offset_s": round(fit(path)[1], 3),
             "max_residual_s": round(fit(path)[2], 3), "matched_lines": len(path)}
            for path in sections],
        "choruses": choruses,
    }
