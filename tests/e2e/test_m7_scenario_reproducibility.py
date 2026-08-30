from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from data_incident_gym.config import Settings
from data_incident_gym.lab import IncidentLab
from data_incident_gym.scenarios import P1_M7_SCENARIO_IDS, load_scenario_spec


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _projection(lab: IncidentLab, case_id: str, run_id: str) -> str:
    run_root = lab.project_root / ".dig" / "lab" / "runs" / run_id
    private_root = lab.project_root / ".dig" / "lab" / "private" / run_id
    verification = lab.verifier.load_verification(run_id)
    runtime = json.loads((run_root / "runtime.json").read_text(encoding="utf-8"))
    runtime.pop("run_id")
    scenario_snapshot = json.loads(
        (private_root / "scenario_snapshot.json").read_text(encoding="utf-8")
    )
    return json.dumps(
        {
            "case_id": case_id,
            "status": verification.status.value,
            "dbt_exit_code": verification.dbt_exit_code,
            "failed_nodes": verification.failed_nodes,
            "skipped_nodes": verification.skipped_nodes,
            "affected_assets": verification.affected_assets,
            "schema_fingerprint": verification.schema_fingerprint,
            "schema_digest": _digest(run_root / "schema.json"),
            "profile_snapshot_digest": _digest(run_root / "profile_snapshot.json"),
            "profile_spec_sha256": verification.profile_spec_sha256,
            "runtime": runtime,
            "incident_brief_digest": _digest(run_root / "incident_brief.json"),
            "scenario_spec_sha256": scenario_snapshot["scenario_spec_sha256"],
        },
        sort_keys=True,
    )


@pytest.mark.e2e
@pytest.mark.parametrize("case_id", P1_M7_SCENARIO_IDS, ids=P1_M7_SCENARIO_IDS)
def test_m7_scenarios_reproduce_across_ten_cycles(
    project_root: Path,
    case_id: str,
) -> None:
    lab = IncidentLab(Settings(_env_file=None), project_root)
    scenario = load_scenario_spec(case_id, project_root)
    initial = lab.reset(case_id)
    projections: list[str] = []
    run_ids: list[str] = []

    try:
        for cycle in range(1, 11):
            reset = lab.reset(case_id)
            prepared = lab.prepare(case_id)
            run = lab.build(case_id)
            projections.append(_projection(lab, case_id, run.run_id))
            run_ids.append(run.run_id)
            print(
                f"M7 scenario {case_id} cycle {cycle}/10: "
                f"run_id={run.run_id} status={run.verification_status.value}"
            )

            assert reset.fingerprint == initial.fingerprint
            if scenario.expected_status == "NO_INCIDENT":
                assert run.dbt_exit_code == 0
                assert prepared.fingerprint == initial.fingerprint
            else:
                assert run.dbt_exit_code != 0
                assert prepared.fingerprint != initial.fingerprint
    finally:
        recovered = lab.restore(case_id)

    assert len(set(projections)) == 1
    assert len(run_ids) == len(set(run_ids)) == 10
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
