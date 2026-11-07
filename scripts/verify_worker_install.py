"""Verify the installed wheel and console command without importing checkout modules."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import autocue_worker
import setlist


def main():
    checkout = Path(__file__).resolve().parents[1]
    if Path(autocue_worker.__file__).resolve().is_relative_to(checkout):
        raise RuntimeError("Verify a non-editable wheel installation, not checkout/PYTHONPATH imports.")
    if Path(setlist.__file__).resolve().is_relative_to(checkout):
        raise RuntimeError("The installed wheel must include the setlist package.")
    command = Path(sys.executable).parent / "autocue-worker"
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("AUTOCUE_", "GEMINI_", "GEM_")) and key != "PYTHONPATH"}
    with tempfile.TemporaryDirectory(prefix="autocue-installed-test-") as directory:
        root = Path(directory)
        results = []
        for index in range(2):
            output = root / f"result-{index}.json"
            finished = subprocess.run([str(command), "fixture", "--out", str(output)],
                                      cwd=root, env=environment, capture_output=True, text=True, timeout=60)
            if finished.returncode:
                raise RuntimeError(finished.stderr or finished.stdout)
            messages = [json.loads(line) for line in finished.stdout.splitlines()]
            result = json.loads(output.read_text(encoding="utf-8"))
            assert messages[-1]["status"] == result["status"] == "completed"
            assert [event["sequence"] for event in messages] == list(range(1, len(messages) + 1))
            assert all(event["schema_version"] == 1 for event in messages)
            assert result["track_hash"] == "75b2c9cb9d87557fc97fd99a1c9ab7d3f3d52ae4734b4031024c4e18d96df065"
            assert result["plan"]["grid"]["source"] == "prepared_downbeats"
            assert result["plan"]["grid"]["bar_s"] == 2
            assert result["plan"]["provenance"]["profile"] == "prepared"
            assert set(result["plan"]["cues"]) == set("ABCDEFGH")
            assert max(cue["confidence"] or 0 for cue in result["plan"]["cues"].values()) <= 0.55
            assert {slot: cue["bar"] for slot, cue in result["plan"]["cues"].items()} == dict(
                zip("ABCDEFGH", [None, 0, None, None, None, None, 16, 24]))
            results.append(result)
        assert results[0]["plan"]["cues"] == results[1]["plan"]["cues"]
        assert results[0]["plan"]["provenance"] == results[1]["plan"]["provenance"]
    print("Installed worker: deterministic offline fixture and JSONL contract verified.")


if __name__ == "__main__":
    main()
