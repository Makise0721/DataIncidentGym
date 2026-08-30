from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_incident_gym.config import Settings
from data_incident_gym.lab import IncidentLab
from data_incident_gym.lab_verifier import ScenarioVerificationStatus
from data_incident_gym.scenarios import SUPPORTED_SCENARIO_IDS, load_scenario_spec


@pytest.mark.integration
@pytest.mark.parametrize("case_id", SUPPORTED_SCENARIO_IDS, ids=SUPPORTED_SCENARIO_IDS)
def test_scenario_lab_captures_private_verification_and_recovers(
    project_root: Path,
    case_id: str,
) -> None:
    lab = IncidentLab(Settings(_env_file=None), project_root)
    scenario = load_scenario_spec(case_id, project_root)
    baseline = lab.reset(case_id)

    try:
        prepared = lab.prepare(case_id)
        has_mutation = any(
            mutation.kind != "NO_MUTATION"
            for mutation in scenario.reset_and_injection_contract.mutations
        )
        assert prepared.state == ("INJECTED" if has_mutation else "HEALTHY")

        run = lab.build(case_id)
        verification = lab.verifier.load_verification(run.run_id)

        expected_status = (
            ScenarioVerificationStatus.HEALTHY_CONTROL
            if scenario.expected_status == "NO_INCIDENT"
            else ScenarioVerificationStatus.EXPECTED_FAILURE
        )
        assert run.verification_status is expected_status
        assert verification.status is expected_status
        assert verification.incident_case_id == case_id
        assert verification.run_id == run.run_id
        assert run.dbt_exit_code == verification.dbt_exit_code

        runtime = json.loads(
            (run.artifact_dir / "runtime.json").read_text(encoding="utf-8")
        )
        assert runtime["run_id"] == run.run_id
        assert not any(
            key in runtime
            for key in (
                "scenario_spec_sha256",
                "expected_status",
                "ground_truth_or_acceptable_root_causes",
            )
        )
        for relative_path in (
            "runtime.json",
            "incident_brief.json",
            "schema.json",
            "profile_snapshot.json",
            "dbt/target/manifest.json",
            "dbt/target/run_results.json",
            "dbt/logs/dbt.log",
        ):
            assert (run.artifact_dir / relative_path).is_file()
    finally:
        recovered = lab.restore(case_id)

    assert recovered.state == "HEALTHY"
    assert recovered.fingerprint == baseline.fingerprint
