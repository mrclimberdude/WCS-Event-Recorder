"""Ancestry controls bind exact outcomes; cleanup controls delete only owned copies."""

import json
from pathlib import Path
import subprocess
import sys


ENTRY = Path(__file__).resolve().parents[1] / "scripts" / "release_c_controls.py"


def test_control_builder_holds_source_outcome_and_seed_digests_constant(tmp_path):
    limits = tmp_path / "limits.json"
    limits.write_text(json.dumps({"schema_version": 1, "max_generated_bytes": 128 * 1024**2,
        "minimum_free_bytes": 1, "worker_timeout_seconds": 120, "max_cases": 8, "capture_timeout_seconds": 10}), encoding="utf-8")
    root = tmp_path / "control"
    arguments = [sys.executable, "-B", str(ENTRY), "ancestry", "--profile", "smoke", "--limits", str(limits), "--output", str(root)]
    result = subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8", timeout=150)
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((root / "storage-fixtures.json").read_text(encoding="utf-8"))
    assert manifest["fixed_mock_elapsed_seconds"] == 0.0
    assert [case["axis_value"] for case in manifest["cases"]] == [0, 1, 3]
    identities = {(case["projection"]["source_sha256"], case["projection"]["outcomes_sha256"], case["seed_sha256"])
                  for case in manifest["cases"]}
    assert identities == {tuple(manifest["controls"])}
    assert all(case["projection"]["observations"] == 13 for case in manifest["cases"])
    before = (root / "storage-fixtures.json").read_bytes()
    refused = subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8", timeout=150)
    assert refused.returncode != 0 and (root / "storage-fixtures.json").read_bytes() == before
