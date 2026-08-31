from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_incident_gym.config import Settings
from data_incident_gym.lab import IncidentLab
from data_incident_gym.lab_verifier import ScenarioVerificationStatus
from data_incident_gym.profiles import load_profile_snapshot
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


@pytest.mark.integration
def test_m8_profiles_and_fixed_null_targets_recover(
    project_root: Path,
) -> None:
    lab = IncidentLab(Settings(_env_file=None), project_root)
    expected_profiles = {
        "required_null_payment_id": {("raw_payments", "id"): 1},
        "required_null_order_customer_a": {
            ("raw_orders", "user_id"): 1,
            ("raw_customers", "last_name"): 1,
        },
        "required_null_order_customer_b": {
            ("raw_customers", "last_name"): 1,
        },
    }
    expected_restored = {
        ("raw_payments", "id", "order_id", 1): 1,
        ("raw_orders", "user_id", "id", 42): 92,
        ("raw_customers", "last_name", "id", 7): "M.",
    }

    for case_id, expected in expected_profiles.items():
        scenario = load_scenario_spec(case_id, project_root)
        lab.reset(case_id)
        try:
            lab.prepare(case_id)
            run = lab.build(case_id)
            profile = load_profile_snapshot(run.artifact_dir / "profile_snapshot.json")
            observed = {
                (snapshot.relation_name, column.column_name): column.null_count
                for snapshot in profile.current
                for column in snapshot.columns
                if (snapshot.relation_name, column.column_name) in expected
            }
            assert observed == expected
            if case_id == "required_null_order_customer_b":
                assert all(
                    snapshot.relation_name != "raw_orders" for snapshot in profile.current
                )
        finally:
            lab.restore(case_id)

        for mutation in scenario.reset_and_injection_contract.mutations:
            key = (
                mutation.relation,
                mutation.column,
                mutation.selector_column,
                mutation.selector_value,
            )
            assert lab._read_null_target(mutation) == expected_restored[key]
            assert lab._null_count(mutation) == 0
