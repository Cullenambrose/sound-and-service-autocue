"""Synthetic whole-set planner contracts, including independent exhaustive oracles."""
import copy
import itertools
import json
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from setlist import planning
from setlist.inputs import song_identity


def track(file_id, duration_s=60, **fields):
    return {"id": file_id, "title": f"Song {file_id}", "artist": "Artist",
            "version": "Clean", "path": f"/synthetic/{file_id}.mp3",
            "duration_s": duration_s, **fields}


def policy(**fields):
    return {"target_minutes": 2, "tolerance_minutes": 0, "max_tracks": 4, **fields}


def oracle(records, rules):
    """Deliberately simple exhaustive integer-duration feasibility oracle."""
    required = set(rules.get("must_include", []))
    excluded = set(rules.get("exclude", []))
    lower = (rules["target_minutes"] - rules["tolerance_minutes"]) * 60
    upper = (rules["target_minutes"] + rules["tolerance_minutes"]) * 60
    feasible = set()
    for count in range(1, min(rules["max_tracks"], len(records)) + 1):
        for sequence in itertools.permutations(records, count):
            ids = tuple(record["id"] for record in sequence)
            if required - set(ids) or set(ids) & excluded:
                continue
            if len({song_identity(row["title"], row["artist"]) for row in sequence}) != count:
                continue
            if any(pin["position"] >= count or ids[pin["position"]] != pin["file_id"]
                   for pin in rules.get("pinned", [])):
                continue
            durations = [row.get("duration_s") for row in sequence]
            if any(type(value) not in (int, float) or value <= 0 for value in durations):
                continue
            if lower <= sum(durations) <= upper:
                feasible.add(ids)
    return feasible


def phase_oracle(records, rules):
    """Enumerate sequences and every nonempty contiguous phase partition."""
    sequences = oracle(records, {**rules, "must_include": list(set(
        rules.get("must_include", []) + [
            file_id for phase in rules["phases"] for file_id in phase.get("must_include", [])
        ]))})
    by_id = {row["id"]: row for row in records}
    valid = set()
    for sequence in sequences:
        for cuts in itertools.combinations(range(1, len(sequence)), len(rules["phases"]) - 1):
            starts = (0,) + cuts
            ends = cuts + (len(sequence),)
            for phase, start, end in zip(rules["phases"], starts, ends):
                group = [by_id[file_id] for file_id in sequence[start:end]]
                duration = sum(row["duration_s"] for row in group)
                if (not set(phase.get("must_include", [])) <= set(sequence[start:end])
                        or abs(duration - phase["target_minutes"] * 60) >
                        phase["tolerance_minutes"] * 60):
                    break
                if any(
                    (phase.get("clean_only", False) and row["version"] != "Clean")
                    or ("bpm_min" in phase and
                        (row.get("bpm") is None or row["bpm"] < phase["bpm_min"]))
                    or ("bpm_max" in phase and
                        (row.get("bpm") is None or row["bpm"] > phase["bpm_max"]))
                    for row in group
                ):
                    break
            else:
                valid.add((sequence, ends))
    return valid


class WholeSetPlannerTests(unittest.TestCase):
    def assert_contract(self, result, records, rules):
        self.assertEqual(result["feasible"], bool(result["alternatives"]))
        json.dumps(result, allow_nan=False)
        originals = {row["id"]: row for row in records}
        for alternative in result["alternatives"]:
            sequence = alternative["tracks"]
            ids = [row["id"] for row in sequence]
            self.assertTrue(1 <= len(sequence) <= rules["max_tracks"])
            self.assertEqual(len(set(ids)), len(ids))
            self.assertEqual(len({song_identity(row["title"], row["artist"])
                                  for row in sequence}), len(sequence))
            self.assertTrue(set(rules.get("must_include", [])) <= set(ids))
            self.assertFalse(set(rules.get("exclude", [])) & set(ids))
            for pin in rules.get("pinned", []):
                self.assertEqual(ids[pin["position"]], pin["file_id"])
            for row in sequence:
                self.assertEqual(row, originals[row["id"]])
            duration = sum(row["duration_s"] for row in sequence)
            self.assertAlmostEqual(alternative["duration_s"], duration)
            self.assertAlmostEqual(alternative["delta_s"], duration - rules["target_minutes"] * 60)
            self.assertLessEqual(abs(duration - rules["target_minutes"] * 60),
                                 rules["tolerance_minutes"] * 60 + 1e-10)
            self.assertEqual(len(alternative["track_reasons"]), len(sequence))

    def test_phase_partitions_agree_with_exhaustive_oracle(self):
        for durations in ((30, 30, 60, 60), (40, 50, 70, 80), (60, 60, 60, 60)):
            records = [track(index, seconds, bpm=80 + index * 20)
                       for index, seconds in enumerate(durations)]
            for tolerance in (0, 0.5):
                for extra in ({}, {"must_include": [0]},
                              {"bpm_max": 100}, {"must_include": [3], "bpm_min": 140}):
                    rules = policy(
                        target_minutes=3, tolerance_minutes=0.5, phases=[
                            {"phase": "dinner", "target_minutes": 1,
                             "tolerance_minutes": tolerance, **extra},
                            {"phase": "open_dancing", "target_minutes": 2,
                             "tolerance_minutes": tolerance},
                        ])
                    expected = phase_oracle(records, rules)
                    result = planning.plan_set(records, rules)
                    with self.subTest(durations=durations, tolerance=tolerance, extra=extra):
                        self.assertEqual(result["feasible"], bool(expected))
                        self.assertTrue(result["diagnostics"]["search_complete"])
                        self.assert_contract(result, records, rules)
                        for alternative in result["alternatives"]:
                            self.assertIn((
                                tuple(row["id"] for row in alternative["tracks"]),
                                tuple(phase["end_position"] for phase in alternative["phases"])),
                                expected)
                            elapsed = 0
                            for phase in alternative["phases"]:
                                self.assertEqual(phase["start_s"], elapsed)
                                self.assertEqual(phase["file_ids"], [
                                    row["id"] for row in alternative["tracks"][
                                        phase["start_position"]:phase["end_position"]]])
                                elapsed = phase["end_s"]
                            self.assertEqual(elapsed, alternative["duration_s"])

    def test_phase_energy_arc_changes_order_and_preserves_exact_moment(self):
        records = [track("high", energy=0.9), track("low", energy=0.1)]
        rules = policy(max_tracks=2, phases=[
            {"phase": "dinner", "label": "Warm up", "target_minutes": 1,
             "tolerance_minutes": 0, "energy_target": 0.1},
            {"phase": "open_dancing", "label": "Peak", "target_minutes": 1,
             "tolerance_minutes": 0, "energy_target": 0.9},
        ])
        original = copy.deepcopy((records, rules))
        result = planning.plan_set(records, rules)
        self.assertEqual([row["id"] for row in result["alternatives"][0]["tracks"]], ["low", "high"])
        self.assertEqual([phase["energy_target"] for phase in result["alternatives"][0]["phases"]],
                         [0.1, 0.9])
        self.assertEqual(result, planning.plan_set(reversed(records), rules))
        self.assertEqual((records, rules), original)
        rules["phases"][0]["must_include"] = ["high"]
        result = planning.plan_set(records, rules)
        self.assertTrue(result["feasible"])
        self.assertTrue(all(alt["phases"][0]["file_ids"] == ["high"]
                            for alt in result["alternatives"]))
        rules["pinned"] = [{"position": 1, "file_id": "high"}]
        self.assertFalse(planning.plan_set(records, rules)["feasible"])

    def test_three_phase_oracle_pins_edits_and_cross_phase_no_repeat(self):
        records = [track("short", 30, title="Alpha (Short Edit)"),
                   track("long", 60, title="Alpha (Extended Mix)"),
                   track("b", 30), track("c", 30), track("d", 60)]
        for extra in ({}, {"pinned": [{"position": 1, "file_id": "c"}]},
                      {"must_include": ["short"], "exclude": ["b"]},
                      {"must_include": ["short", "long"]}):
            rules = policy(target_minutes=2, phases=[
                {"phase": "cocktail", "target_minutes": 0.5, "tolerance_minutes": 0},
                {"phase": "dinner", "target_minutes": 0.5, "tolerance_minutes": 0},
                {"phase": "open_dancing", "target_minutes": 1, "tolerance_minutes": 0},
            ], **extra)
            expected = phase_oracle(records, rules)
            result = planning.plan_set(records, rules)
            with self.subTest(extra=extra):
                self.assertEqual(result["feasible"], bool(expected))
                for alternative in result["alternatives"]:
                    self.assertIn((
                        tuple(row["id"] for row in alternative["tracks"]),
                        tuple(phase["end_position"] for phase in alternative["phases"])), expected)

    def test_phase_timing_uses_decimal_exact_boundaries(self):
        records = [track("a", 0.1), track("b", 0.2), track("c", 0.3)]
        rules = policy(target_minutes=0.01, max_tracks=3, phases=[
            {"phase": "dinner", "target_minutes": 0.005, "tolerance_minutes": 0,
             "must_include": ["a", "b"]},
            {"phase": "open_dancing", "target_minutes": 0.005, "tolerance_minutes": 0,
             "must_include": ["c"]},
        ])
        result = planning.plan_set(records, rules)
        self.assertTrue(result["feasible"])
        self.assertEqual([phase["duration_s"] for phase in result["alternatives"][0]["phases"]], [0.3, 0.3])
        self.assertEqual(result["alternatives"][0]["duration_s"], 0.6)
        records[2]["duration_s"] = 0.30000000000000004
        self.assertFalse(planning.plan_set(records, rules)["feasible"])

    def test_phase_constraints_never_relax_global_rules_and_unknowns(self):
        records = [track("clean", title="Alpha", bpm=90),
                   track("dirty", title="Beta", version="Dirty", bpm=120),
                   track("unknown", title="Gamma", bpm=None)]
        rules = policy(max_tracks=2, phases=[
            {"phase": "dinner", "target_minutes": 1, "tolerance_minutes": 0,
             "bpm_max": 95, "clean_only": True},
            {"phase": "open_dancing", "target_minutes": 1, "tolerance_minutes": 0,
             "must_include": ["dirty"], "bpm_min": 110},
        ])
        self.assertTrue(planning.plan_set(records, rules)["feasible"])
        for extra in ({"clean_only": True}, {"bpm_max": 100}, {"exclude": ["dirty"]}):
            self.assertFalse(planning.plan_set(records, rules | extra)["feasible"])
        rules["phases"][1]["must_include"] = ["unknown"]
        result = planning.plan_set(records, rules)
        self.assertFalse(result["feasible"])
        self.assertIn("phase_required_file_ineligible", [item["code"] for item in result["unresolved"]])

    def test_phase_conflicts_nonempty_timing_and_budget_evidence(self):
        records = [track(index) for index in range(3)]
        phases = [{"phase": "dinner", "target_minutes": 1, "tolerance_minutes": 0,
                   "must_include": [0]},
                  {"phase": "first_dance", "target_minutes": 1, "tolerance_minutes": 0,
                   "must_include": [0]}]
        result = planning.plan_set(records, policy(phases=phases))
        self.assertEqual(result["unresolved"][0]["code"], "file_required_in_multiple_phases")
        phases[1]["must_include"] = [1]
        self.assertTrue(planning.plan_set(records, policy(phases=phases))["feasible"])
        with patch.object(planning, "MAX_EXPANSIONS", 0):
            result = planning.plan_set(records, policy(phases=phases))
        self.assertEqual(result["diagnostics"]["status"], "search_exhausted")
        self.assertFalse(planning.plan_set(records, policy(max_tracks=1, phases=phases))["feasible"])
        # A globally correct length cannot excuse an empty or out-of-budget phase.
        self.assertFalse(planning.plan_set([track(0, 120)], policy(phases=phases))["feasible"])
        phases[0]["tolerance_minutes"] = 1
        phases[1]["tolerance_minutes"] = 1
        self.assertFalse(planning.plan_set([track(0, 120)], policy(phases=phases))["feasible"])

    def test_strict_phase_policy_validation_and_normalization(self):
        phase = {"phase": "dinner", "target_minutes": 2, "tolerance_minutes": 0}
        invalid = [None, [], {}, [None], [phase] * 13, [phase | {"phase": []}],
                   [phase | {"phase": "unknown"}], [phase | {"label": ""}],
                   [phase | {"label": "x" * 81}], [phase | {"label": False}],
                   [phase | {"target_minutes": 1}], [phase | {"clean_only": 1}],
                   [phase | {"bpm_min": 120, "bpm_max": 100}],
                   [phase | {"must_include": [True]}], [phase | {"pinned": []}],
                   [phase | {"max_tracks": 2}], [phase | {"phases": [phase]}]]
        for phases in invalid:
            with self.subTest(phases=phases), self.assertRaises(ValueError):
                planning.validate_policy(policy(phases=phases))
        normalized = planning.validate_policy(policy(phases=[phase | {"label": " Dinner "}]))
        self.assertEqual(normalized["phases"][0]["label"], "Dinner")
        self.assertEqual(planning.validate_policy(normalized), normalized)

    def test_exhaustive_small_oracle_feasibility_and_returned_sequences(self):
        for durations in ((30, 40, 50, 60), (25, 45, 70, 90), (50, 50, 50, 50)):
            records = [track(index, duration) for index, duration in enumerate(durations)]
            for target in (1, 2, 3, 5):
                for extra in (
                    {}, {"must_include": [0]},
                    {"must_include": [0], "exclude": [1]},
                    {"pinned": [{"position": 1, "file_id": 2}]},
                    {"must_include": [3], "pinned": [{"position": 0, "file_id": 0}]},
                ):
                    rules = policy(target_minutes=target, **extra)
                    expected = oracle(records, rules)
                    with self.subTest(durations=durations, rules=rules):
                        result = planning.plan_set(records, rules)
                        self.assertEqual(result["feasible"], bool(expected))
                        self.assert_contract(result, records, rules)
                        for alternative in result["alternatives"]:
                            self.assertIn(tuple(row["id"] for row in alternative["tracks"]), expected)
                        if not expected:
                            self.assertEqual(result["diagnostics"]["status"], "infeasible")
                            self.assertTrue(result["diagnostics"]["search_complete"])

    def test_small_oracle_different_edits_and_incompatible_musts(self):
        records = [track("short", 60, title="Track (Short Edit)"),
                   track("long", 120, title="Track (Extended Mix)"),
                   track("other", 60)]
        for extra in ({}, {"must_include": ["short"]}, {"must_include": ["long"]},
                      {"must_include": ["short", "long"]},
                      {"pinned": [{"position": 1, "file_id": "long"}]}):
            rules = policy(**extra)
            expected = oracle(records, rules)
            result = planning.plan_set(records, rules)
            self.assertEqual(result["feasible"], bool(expected))
            self.assert_contract(result, records, rules)
        result = planning.plan_set(records, policy(must_include=["short", "long"]))
        self.assertEqual(result["unresolved"][0]["code"], "required_song_repeated")

    def test_inclusive_exact_duration_tolerance_boundaries(self):
        for duration, feasible in ((89.999, False), (90, True), (120, True),
                                   (150, True), (150.001, False)):
            rules = policy(tolerance_minutes=0.5, max_tracks=1)
            result = planning.plan_set([track("a", duration)], rules)
            self.assertEqual(result["feasible"], feasible)
        for durations in ((0.1, 0.2, 0.3), (0.3, 0.3), (0.6,)):
            records = [track(index, duration) for index, duration in enumerate(durations)]
            result = planning.plan_set(
                records, policy(target_minutes=0.01, must_include=list(range(len(records)))))
            self.assertTrue(result["feasible"])
            self.assertEqual(result["alternatives"][0]["duration_s"], 0.6)
            self.assertEqual(result["alternatives"][0]["delta_s"], 0)
        result = planning.plan_set(
            [track("a", 0.6), track("b", 1e-100)],
            policy(target_minutes=0.01, must_include=["a", "b"]))
        self.assertFalse(result["feasible"])

    def test_no_empty_set_even_if_tolerance_includes_zero(self):
        result = planning.plan_set([], policy(tolerance_minutes=3))
        self.assertFalse(result["feasible"])
        self.assertEqual(result["diagnostics"]["status"], "infeasible")

    def test_exact_pins_required_files_and_exclusion_conflicts(self):
        records = [track("a"), track("b"), track("c")]
        rules = policy(must_include=["b"], exclude=["c"],
                       pinned=[{"position": 1, "file_id": "a"}])
        result = planning.plan_set(records, rules)
        self.assertTrue(result["feasible"])
        self.assertEqual([row["id"] for row in result["alternatives"][0]["tracks"]], ["b", "a"])
        cases = (
            ({"must_include": ["missing"]}, "required_file_missing"),
            ({"must_include": ["a"], "exclude": ["a"]}, "required_file_ineligible"),
            ({"pinned": [{"position": 0, "file_id": "a"}], "exclude": ["a"]},
             "required_file_ineligible"),
            ({"pinned": [{"position": 0, "file_id": "a"},
                         {"position": 0, "file_id": "b"}]}, "pin_position_conflict"),
            ({"pinned": [{"position": 0, "file_id": "a"},
                         {"position": 1, "file_id": "a"}]}, "file_pinned_twice"),
            ({"max_tracks": 1, "must_include": ["a", "b"]}, "required_capacity"),
            ({"pinned": [{"position": 3, "file_id": "a"}]}, "duration_or_capacity_impossible"),
        )
        for extra, code in cases:
            with self.subTest(extra=extra):
                result = planning.plan_set(records, policy(**extra))
                self.assertFalse(result["feasible"])
                self.assertIn(code, [row["code"] for row in result["unresolved"]])
                self.assertTrue(result["diagnostics"]["search_complete"])

    def test_clean_requires_explicit_evidence_and_dirty_always_wins(self):
        records = [
            track("clean", version="Clean Intro"),
            track("title-clean", title="Good (Clean)", version=None),
            track("unknown", version=None),
            track("radio", version="Radio Edit"),
            track("dirty", title="Bad (Dirty)", version="Clean"),
            track("explicit", title="Bad (Clean)", version="Explicit"),
            track("both", version="Clean Dirty"),
            track("claim", version=None, is_clean=True),
        ]
        result = planning.plan_set(records, policy(clean_only=True))
        self.assertTrue(result["feasible"])
        for alternative in result["alternatives"]:
            self.assertEqual({row["id"] for row in alternative["tracks"]},
                             {"clean", "title-clean"})
        for row in records[2:]:
            result = planning.plan_set(records, policy(clean_only=True, must_include=[row["id"]]))
            self.assertFalse(result["feasible"])
            self.assertEqual(result["unresolved"][0]["code"], "required_file_ineligible")

    def test_unknown_durations_never_satisfy_target(self):
        for value in (None, 0, -1, True, "120"):
            result = planning.plan_set([track("unknown", value), track("known", 60)], policy())
            self.assertFalse(result["feasible"])
            self.assertEqual(result["diagnostics"]["filtered"], {"unknown_duration": 1})
        result = planning.plan_set(
            [track("unknown", None), track("known", 120)], policy(must_include=["unknown"]))
        self.assertFalse(result["feasible"])
        self.assertEqual(result["unresolved"][0]["reason"], "unknown_duration")
        for value in (float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                planning.plan_set([track("unknown", value)], policy())

    def test_strict_bpm_unknowns_and_inclusive_bounds_no_double_time(self):
        records = [track("low", bpm=100), track("high", bpm=120),
                   track("half", bpm=55), track("outside", bpm=120.01),
                   track("unknown"), track("bad", bpm=True), track("string", bpm="110")]
        rules = policy(bpm_min=100, bpm_max=120)
        result = planning.plan_set(records, rules)
        self.assertTrue(result["feasible"])
        for alternative in result["alternatives"]:
            self.assertEqual({row["id"] for row in alternative["tracks"]}, {"low", "high"})
        for row in records[2:]:
            result = planning.plan_set(records, policy(
                bpm_min=100, bpm_max=120, must_include=[row["id"]]))
            self.assertFalse(result["feasible"])
        result = planning.plan_set([track("unknown", 120)], policy())
        self.assertTrue(result["feasible"])

    def test_all_excluded_versions_cannot_sneak_back_in(self):
        records = [track("a", title="One (Clean)"), track("b", title="One (Dirty)"),
                   track("c", 120, title="Another")]
        rules = policy(exclude=["a", "b"])
        result = planning.plan_set(records, rules)
        self.assertTrue(result["feasible"])
        self.assertEqual([row["id"] for row in result["alternatives"][0]["tracks"]], ["c"])
        self.assertFalse(planning.plan_set(records, policy(exclude=["a", "b", "c"]))["feasible"])

    def test_song_identity_uses_matcher_normalization_and_feature_handling(self):
        records = [track("a", title="Deja-Vu (Clean)", artist="Main feat. Guest"),
                   track("b", title="deja vu (Intro)", artist="MAIN")]
        result = planning.plan_set(records, policy())
        self.assertFalse(result["feasible"])
        self.assertFalse(planning.plan_set(
            records, policy(must_include=["a", "b"]))["feasible"])

    def test_determinism_input_and_policy_order_no_mutation_exact_metadata(self):
        records = [track(index, key="8A", bpm=110 + index,
                         energy=0.2 * index, opaque={"tags": ["original"]})
                   for index in range(5)]
        rules = policy(energy_target=0.8, must_include=[1, 2], exclude=[3, 4],
                       pinned=[{"position": 0, "file_id": 1}])
        originals = copy.deepcopy((records, rules))
        expected = planning.plan_set(records, rules)
        rng = random.Random(17)
        for _ in range(12):
            rng.shuffle(records)
            rules["must_include"].reverse()
            rules["exclude"].reverse()
            self.assertEqual(planning.plan_set(records, rules), expected)
        self.assertEqual(sorted(records, key=lambda row: row["id"]), originals[0])
        self.assertEqual(rules, originals[1])
        self.assert_contract(expected, records, rules)

    def test_shared_scoring_explains_energy_key_and_continuity(self):
        records = [track(index, bpm=110, key="8A", genre="Pop", energy=0.8)
                   for index in range(3)]
        with patch.object(planning, "score_candidate", wraps=planning.score_candidate) as scorer:
            result = planning.plan_set(records, policy(energy_target=0.8))
            self.assertTrue(scorer.called)
        reasons = [reason for entry in result["alternatives"][0]["track_reasons"]
                   for reason in entry["reasons"]]
        self.assertIn("Energy fits phase target", reasons)
        self.assertIn("Camelot-compatible key", reasons)
        self.assertIn("Genre continuity: Pop", reasons)

    def test_version_preferences_reuse_matcher_but_do_not_collapse_edits(self):
        records = [track("dirty", 120, title="Same", version="Dirty"),
                   track("clean", 120, title="Same", version="Clean")]
        with patch.object(planning, "rank_candidates", wraps=planning.rank_candidates) as ranker:
            result = planning.plan_set(records, policy(max_tracks=1))
            self.assertTrue(ranker.called)
        self.assertEqual({alt["tracks"][0]["id"] for alt in result["alternatives"]},
                         {"dirty", "clean"})
        result = planning.plan_set(records, policy(max_tracks=1, must_include=["dirty"]))
        self.assertEqual(result["alternatives"][0]["tracks"][0]["version"], "Dirty")

    def test_exact_id_types(self):
        records = [track(1), track("1")]
        result = planning.plan_set(records, policy(must_include=[1, "1"]))
        # Both records' title defaults are identical: no-repeat is song-level.
        self.assertFalse(result["feasible"])
        records[1]["title"] = "Different"
        result = planning.plan_set(records, policy(must_include=[1, "1"]))
        self.assertTrue(result["feasible"])

    def test_approximate_beam_failure_is_not_proof_of_infeasibility(self):
        records = [track("a", 80), track("b", 60), track("c", 60)]
        with patch.object(planning, "BEAM_WIDTH", 1):
            # 80s is nearer target, but only the discarded 60s branches can finish.
            rules = policy(max_tracks=2)
            self.assertTrue(oracle(records, rules))
            result = planning.plan_set(records, rules)
        self.assertFalse(result["feasible"])
        self.assertEqual(result["diagnostics"]["status"], "search_exhausted")
        self.assertFalse(result["diagnostics"]["search_complete"])
        self.assertIn("beam_width", result["diagnostics"]["truncated_by"])

    def test_candidate_limit_and_budget_failures_are_explicit(self):
        records = [track("a", 80), track("b", 60), track("c", 60)]
        with patch.object(planning, "MAX_CANDIDATES", 1):
            result = planning.plan_set(records, policy())
        self.assertFalse(result["feasible"])
        self.assertEqual(result["diagnostics"]["status"], "search_exhausted")
        self.assertIn("candidate_limit", result["diagnostics"]["truncated_by"])
        with patch.object(planning, "MAX_EXPANSIONS", 0):
            result = planning.plan_set(records, policy())
        self.assertFalse(result["feasible"])
        self.assertEqual(result["diagnostics"]["expansions"], 0)
        self.assertIn("expansion_limit", result["diagnostics"]["truncated_by"])

    def test_required_files_survive_candidate_cap(self):
        records = [track(index) for index in range(8)]
        with patch.object(planning, "MAX_CANDIDATES", 3):
            result = planning.plan_set(records, policy(
                must_include=[6, 7], pinned=[{"position": 1, "file_id": 7}]))
        self.assertTrue(result["feasible"])
        self.assertEqual({row["id"] for row in result["alternatives"][0]["tracks"]}, {6, 7})
        self.assertIn("candidate_limit", result["diagnostics"]["truncated_by"])
        with patch.object(planning, "MAX_CANDIDATES", 3):
            self.assertEqual(planning.plan_set(records, policy()),
                             planning.plan_set(reversed(records), policy()))

    def test_feasible_result_can_still_have_incomplete_search(self):
        records = [track(index, 60) for index in range(5)]
        with patch.object(planning, "BEAM_WIDTH", 1):
            result = planning.plan_set(records, policy())
        self.assertTrue(result["feasible"])
        self.assertFalse(result["diagnostics"]["search_complete"])
        self.assertEqual(result["diagnostics"]["status"], "feasible")

    def test_invalid_policy_types_ranges_unknown_fields(self):
        invalid = [
            {"target_minutes": 0}, {"target_minutes": -1}, {"target_minutes": True},
            {"target_minutes": "2"}, {"target_minutes": float("nan")},
            {"target_minutes": 10 ** 1000},
            {"target_minutes": float("inf")}, {"target_minutes": 1441},
            {"tolerance_minutes": -1}, {"tolerance_minutes": False},
            {"max_tracks": True}, {"max_tracks": 0}, {"max_tracks": 61}, {"max_tracks": 2.0},
            {"bpm_min": None}, {"bpm_min": 0}, {"bpm_min": 130, "bpm_max": 120},
            {"bpm_max": True}, {"bpm_max": float("nan")},
            {"energy_target": -0.1}, {"energy_target": 1.1}, {"energy_target": True},
            {"energy_target": float("nan")}, {"clean_only": 1},
            {"must_include": "a"}, {"must_include": [True]}, {"must_include": [""]},
            {"must_include": ["a", "a"]}, {"exclude": [None]}, {"exclude": ["a", "a"]},
            {"pinned": {}}, {"pinned": [{"position": True, "file_id": "a"}]},
            {"pinned": [{"position": -1, "file_id": "a"}]},
            {"pinned": [{"position": 4, "file_id": "a"}]},
            {"pinned": [{"position": 0, "file_id": "a", "extra": 1}]},
            {"pinned": [{"position": 0}]}, {"pinned": [{"position": 0, "file_id": False}]},
            {"allow_repeats": True}, {"event": {}}, {"beam_width": 1},
        ]
        for extra in invalid:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                planning.validate_policy(policy(**extra))
        for invalid_value in (None, [], {}, {"target_minutes": 2}):
            with self.assertRaises(ValueError):
                planning.validate_policy(invalid_value)
        self.assertTrue(planning.validate_policy(policy(energy_target=0))["energy_target"] == 0)

    def test_invalid_or_unbounded_inputs_fail_clearly(self):
        invalid = [
            [track("a"), track("a")], [track(True)], [track("a", title="")],
            [track("a", artist=None)], [track("a", path="")], [track("a", version=3)],
            [track("a", opaque=object())], [{"id": "a"}], [None],
        ]
        for records in invalid:
            with self.subTest(records=records), self.assertRaises(ValueError):
                planning.plan_set(records, policy())
        with self.assertRaises(ValueError):
            planning.plan_set(None, policy())
        with patch.object(planning, "MAX_INPUT_TRACKS", 2), self.assertRaises(ValueError):
            planning.plan_set((track(index) for index in range(3)), policy())

    def test_full_sixty_track_limit_with_fixed_pins(self):
        records = [track(index, 60) for index in range(60)]
        rules = policy(target_minutes=60, max_tracks=60,
                       pinned=[{"position": index, "file_id": index} for index in range(60)])
        result = planning.plan_set(records, rules)
        self.assertTrue(result["feasible"])
        self.assert_contract(result, records, rules)

    def test_full_sixty_track_unpinned_search_reserves_horizon_budget(self):
        records = [track(index, 60) for index in range(120)]
        rules = policy(target_minutes=60, max_tracks=60,
                       must_include=[119], exclude=[0],
                       pinned=[{"position": 59, "file_id": 119}])
        result = planning.plan_set(records, rules)
        self.assertTrue(result["feasible"])
        self.assert_contract(result, records, rules)
        self.assertEqual(len(result["alternatives"][0]["tracks"]), 60)
        self.assertLess(result["diagnostics"]["limits"]["beam_width"], planning.BEAM_WIDTH)
        self.assertLessEqual(result["diagnostics"]["expansions"], planning.MAX_EXPANSIONS)
        self.assertNotIn("expansion_limit", result["diagnostics"]["truncated_by"])
        self.assertIn("beam_width", result["diagnostics"]["truncated_by"])
        self.assertFalse(result["diagnostics"]["search_complete"])

    def test_long_multiphase_search_reserves_both_boundary_branches(self):
        records = [track(index, 60) for index in range(120)]
        rules = policy(target_minutes=60, max_tracks=60, phases=[
            {"phase": "cocktail", "target_minutes": 20, "tolerance_minutes": 0},
            {"phase": "dinner", "target_minutes": 20, "tolerance_minutes": 0},
            {"phase": "open_dancing", "target_minutes": 20, "tolerance_minutes": 0,
             "must_include": [119]},
        ])
        result = planning.plan_set(records, rules)
        self.assertTrue(result["feasible"])
        self.assert_contract(result, records, rules)
        self.assertEqual([phase["duration_s"] for phase in result["alternatives"][0]["phases"]],
                         [1200, 1200, 1200])
        self.assertIn(119, result["alternatives"][0]["phases"][-1]["file_ids"])
        self.assertNotIn("expansion_limit", result["diagnostics"]["truncated_by"])
        self.assertLessEqual(result["diagnostics"]["expansions"], planning.MAX_EXPANSIONS)


if __name__ == "__main__":
    unittest.main()
