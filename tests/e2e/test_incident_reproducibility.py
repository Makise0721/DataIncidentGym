from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from data_incident_gym.config import Settings
from data_incident_gym.lab import IncidentLab

P0_SCENARIO_ID = "schema_rename_payment_amount"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.e2e
def test_p0_rename_regression_reproduces_across_ten_runs(project_root: Path) -> None:
    lab = IncidentLab(Settings(_env_file=None), project_root)
    initial = lab.reset(P0_SCENARIO_ID)
    projections: list[str] = []
    run_dirs: list[Path] = []

    try:
        for cycle in range(1, 11):
            reset = lab.reset(P0_SCENARIO_ID)
            prepared = lab.prepare(P0_SCENARIO_ID)
            run = lab.build(P0_SCENARIO_ID)
            verification = lab.verifier.load_verification(run.run_id)
            projections.append(
                json.dumps(
                    {
                        "status": verification.status.value,
                        "dbt_exit_code": verification.dbt_exit_code,
                        "failed_nodes": verification.failed_nodes,
                        "affected_assets": verification.affected_assets,
                        "schema_fingerprint": verification.schema_fingerprint,
                        "schema_digest": _digest(run.artifact_dir / "schema.json"),
                        "profile_digest": _digest(run.artifact_dir / "profile_snapshot.json"),
                    },
                    sort_keys=True,
                )
            )
            run_dirs.append(run.artifact_dir)
            print(f"P0 rename cycle {cycle}/10: run_id={run.run_id}")
            assert reset.fingerprint == initial.fingerprint
            assert prepared.fingerprint != initial.fingerprint
            assert run.dbt_exit_code != 0
    finally:
        recovered = lab.restore(P0_SCENARIO_ID)

    assert len(set(projections)) == 1
    assert len({path.name for path in run_dirs}) == 10
    assert recovered.fingerprint == initial.fingerprint
    submodule = subprocess.run(
        [
            "git",
            "-C",
            str(project_root / "third_party/jaffle_shop"),
            "status",
            "--short",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert submodule.stdout.strip() == ""
