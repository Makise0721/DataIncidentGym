import json
import subprocess
from pathlib import Path

import pytest

from data_incident_gym.config import Settings
from data_incident_gym.incidents import SUPPORTED_CASE_IDS
from data_incident_gym.lab import IncidentLab


@pytest.mark.e2e
@pytest.mark.parametrize("case_id", SUPPORTED_CASE_IDS, ids=SUPPORTED_CASE_IDS)
def test_incident_is_reproducible_across_ten_runs(
    project_root: Path,
    case_id: str,
) -> None:
    lab = IncidentLab(Settings(_env_file=None), project_root)
    initial = lab.reset(case_id)
    stable_results: list[str] = []
    run_dirs: list[Path] = []

    try:
        for run_number in range(1, 11):
            reset = lab.reset(case_id)
            injection = lab.inject(case_id)
            run = lab.build(case_id)
            projection = {
                "case_id": case_id,
                "failed_nodes": run.verification.failed_nodes,
                "affected_assets": run.verification.affected_assets,
                "error_category": run.verification.error_category,
                "schema_fingerprint": run.verification.schema_fingerprint,
                "ground_truth_digest": run.verification.ground_truth_digest,
            }
            stable_results.append(json.dumps(projection, sort_keys=True))
            run_dirs.append(run.artifact_dir)
            print(
                f"incident run {run_number}/10: "
                f"run_id={run.run_id} projection={projection}"
            )
            assert reset.fingerprint == initial.fingerprint
            assert injection.fingerprint != initial.fingerprint
    finally:
        recovered = lab.reset(case_id)

    assert len(set(stable_results)) == 1
    assert len({path.name for path in run_dirs}) == 10
    assert all(path.is_dir() for path in run_dirs)
    assert recovered.fingerprint == initial.fingerprint
    submodule = subprocess.run(
        ["git", "-C", str(project_root / "third_party/jaffle_shop"), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert submodule.stdout.strip() == ""
