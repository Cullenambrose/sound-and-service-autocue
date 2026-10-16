from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple, Literal
import numpy as np

MixOutTarget = Literal["last_chorus_end", "first_chorus_end", "second_chorus_end", "outro_start"]
ChorusFirstMode = Literal["smart_merge_verse1", "strict_slots"]

@dataclass
class DJCueProfile:
profile_name: str = "Open-Format Pro"
pre_vocal_bars: int = 4
pre_mix_out_bars: int = 4
mix_out_target: MixOutTarget = "last_chorus_end"
chorus_first_mode: ChorusFirstMode = "smart_merge_verse1"
snap_vocal_to_downbeat: bool = True
include_outro_type_in_label: bool = True
intro_loop_bars: int = 4
outro_loop_bars: int = 4
vocal_tease_loop_bars: int = 4
confidence_review_threshold: float = 0.92

@dataclass
class BarStemEnergy:
bar_idx: int
lead_vocals_db: float
drums_db: float
bass_db: float
other_db: float

@dataclass
class ClassifiedSegment:
phrase_idx: int
role: str
start_downbeat_idx: int
end_downbeat_idx: int
first_word_sec: float
confidence: float
text_preview: str = ""

@dataclass
class TrackBlueprint:
file_hash: str
file_path: str
artist: str
title: str
bpm: float
downbeats: List[float]
bar_energies: List[BarStemEnergy]
segments: List[ClassifiedSegment]
instrumental_drops: List[int]
overall_confidence: float
review_reason: str = ""

@dataclass
class SeratoCuePoint:
slot: int
name: str
position_ms: int
downbeat_idx: int
color_rgb: Tuple[int, int, int]
confidence: float

@dataclass
class SeratoSavedLoop:
slot: int
name: str
start_ms: int
end_ms: int
start_downbeat_idx: int
end_downbeat_idx: int
color_rgb: Tuple[int, int, int]
locked: bool = False

class ParametricCueEngine:
@staticmethod
def classify_mix_out_character(mix_out_db_idx: int, bar_energies: List[BarStemEnergy], window_bars: int = 4):
    if not bar_energies or mix_out_db_idx >= len(bar_energies):
        return "COLD END", (153, 153, 153)
    window = bar_energies[mix_out_db_idx : min(len(bar_energies), mix_out_db_idx + window_bars)]
    voc_db = float(np.mean([b.lead_vocals_db for b in window]))
    drm_db = float(np.mean([b.drums_db for b in window]))
    bas_db = float(np.mean([b.bass_db for b in window]))
    oth_db = float(np.mean([b.other_db for b in window]))

    if max(voc_db, drm_db, bas_db, oth_db) < -30.0: return "COLD END", (153, 153, 153)
    if voc_db > -24.0 and drm_db < -28.0 and bas_db < -28.0: return "ACAP OUT", (0, 255, 255)
    if drm_db > -20.0 and voc_db < -28.0 and oth_db < -25.0: return "DRUM OUT", (0, 204, 102)
    if drm_db > -22.0 and voc_db < -27.0: return "INST OUT", (0, 204, 102)
    return "VOC+BEAT", (51, 204, 204)

@classmethod
def generate_cues_and_loops(cls, blueprint: TrackBlueprint, profile: DJCueProfile):
    downbeats = blueprint.downbeats
    if not downbeats: return [], [], "UNKNOWN"
    cues, loops = {}, {}

    def make_cue(slot, name, db_idx, color, conf, exact_sec=None):
        clamped_idx = max(0, min(len(downbeats) - 1, db_idx))
        sec = exact_sec if exact_sec is not None else downbeats[clamped_idx]
        cues[slot] = SeratoCuePoint(slot, name, int(round(sec * 1000)), clamped_idx, color, round(conf, 2))

    def make_loop(slot, name, start_idx, bars_len, color):
        if bars_len <= 0 or start_idx >= len(downbeats) - 1: return
        s_idx = max(0, min(len(downbeats) - 2, start_idx))
        e_idx = min(len(downbeats) - 1, s_idx + bars_len)
        if e_idx <= s_idx: return
        loops[slot] = SeratoSavedLoop(slot, name, int(round(downbeats[s_idx] * 1000)), int(round(downbeats[e_idx] * 1000)), s_idx, e_idx, color)

    make_cue(0, "INTRO", 0, (0, 102, 255), 0.99)
    if profile.intro_loop_bars > 0:
        make_loop(0, f"INTRO {profile.intro_loop_bars}B LOOP", 0, profile.intro_loop_bars, (0, 102, 255))

    full_choruses = [s for s in blueprint.segments if s.role == "CHORUS_FULL"]
    substantive_vocals = [s for s in blueprint.segments if s.role in ("VOCAL_TEASE", "VERSE", "PRE_CHORUS", "CHORUS_FULL", "BUILDUP_LOOP")]

    if substantive_vocals:
        first_voc = substantive_vocals[0]
        voc_idx = first_voc.start_downbeat_idx
        if profile.pre_vocal_bars > 0:
            make_cue(1, f"{profile.pre_vocal_bars}B PRE-VOC", max(0, voc_idx - profile.pre_vocal_bars), (255, 204, 0), first_voc.confidence)
        make_cue(2, "VOCAL START", voc_idx, (255, 128, 0), first_voc.confidence)
        if full_choruses:
            make_cue(3, "CHORUS 1", full_choruses[0].start_downbeat_idx, (255, 0, 0), full_choruses[0].confidence)

    if len(full_choruses) > 1:
        make_cue(4, "LAST CHORUS", full_choruses[-1].start_downbeat_idx, (204, 0, 255), full_choruses[-1].confidence)

    mix_out_idx = full_choruses[-1].end_downbeat_idx if full_choruses else max(0, len(downbeats) - 8)
    outro_type, outro_color = cls.classify_mix_out_character(mix_out_idx, blueprint.bar_energies)
    
    if profile.pre_mix_out_bars > 0:
        make_cue(5, f"{profile.pre_mix_out_bars}B MIX OUT", max(0, mix_out_idx - profile.pre_mix_out_bars), (255, 204, 0), blueprint.overall_confidence)
    make_cue(6, f"OUTRO ({outro_type})", mix_out_idx, outro_color, blueprint.overall_confidence)

    if profile.outro_loop_bars > 0 and outro_type != "COLD END":
        make_loop(1, f"OUTRO {profile.outro_loop_bars}B", mix_out_idx, profile.outro_loop_bars, outro_color)

    return [cues[k] for k in sorted(cues.keys())], [loops[k] for k in sorted(loops.keys())], outro_type
