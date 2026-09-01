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
            else (
                ScenarioVerificationStatus.EXPECTED_ANOMALY
                if scenario.direct_failure is None
                else ScenarioVerificationStatus.EXPECTED_FAILURE
            )
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


@pytest.mark.integration
def test_m9_duplicate_profiles_and_recovery(project_root: Path) -> None:
    lab = IncidentLab(Settings(_env_file=None), project_root)
    expected_public = {
        "duplicate_payment_record": (1, 1, ("credit_card", 56)),
        "duplicate_payment_coupon_a": (0, 3, ("coupon", 16)),
        "duplicate_payment_coupon_b": None,
    }

    for case_id, expected in expected_public.items():
        scenario = load_scenario_spec(case_id, project_root)
        baseline = lab.reset(case_id)
        try:
            lab.prepare(case_id)
            run = lab.build(case_id)
            verification = lab.verifier.load_verification(run.run_id)
            assert verification.status is (
                ScenarioVerificationStatus.EXPECTED_FAILURE
                if scenario.direct_failure is not None
                else ScenarioVerificationStatus.EXPECTED_ANOMALY
            )
            profile = load_profile_snapshot(run.artifact_dir / "profile_snapshot.json")
            raw_payments = next(
                (item for item in profile.current if item.relation_name == "raw_payments"),
                None,
            )
            if expected is None:
                assert raw_payments is None
            else:
                assert raw_payments is not None
                key_count = next(
                    item.duplicate_count
                    for item in raw_payments.business_key_duplicates
                    if item.name == "id"
                )
                fingerprint_count = next(
                    item.duplicate_count
                    for item in raw_payments.business_fingerprint_duplicates
                    if item.name == "order_payment_amount"
                )
                payment_method_group = next(
                    item for item in raw_payments.groups if item.name == "payment_method"
                )
                channel_count = next(
                    count
                    for values, count in zip(
                        payment_method_group.values,
                        payment_method_group.counts,
                        strict=True,
                    )
                    if values == (expected[2][0],)
                )
                assert (key_count, fingerprint_count, (expected[2][0], channel_count)) == expected
        finally:
            recovered = lab.restore(case_id)

        assert recovered.state == "HEALTHY"
        assert recovered.fingerprint == baseline.fingerprint


@pytest.mark.integration
def test_m10_orphan_profiles_and_recovery(project_root: Path) -> None:
    lab = IncidentLab(Settings(_env_file=None), project_root)
    expected = {
        "orphan_payment_record": (1, "credit_card", 56, True),
        "orphan_payment_coupon_a": (3, "coupon", 16, True),
        "orphan_payment_coupon_b": (3, "coupon", 16, False),
    }

    for case_id, (violations, channel, channel_count, history_visible) in expected.items():
        baseline = lab.reset(case_id)
        try:
            prepared = lab.prepare(case_id)
            assert prepared.state == "INJECTED"
            run = lab.build(case_id)
            verification = lab.verifier.load_verification(run.run_id)
            assert run.dbt_exit_code == 0
            assert run.verification_status is ScenarioVerificationStatus.EXPECTED_ANOMALY
            assert verification.status is ScenarioVerificationStatus.EXPECTED_ANOMALY
            assert verification.failed_nodes == ()
            assert verification.skipped_nodes == ()

            profile = load_profile_snapshot(run.artifact_dir / "profile_snapshot.json")
            payments = next(
                item for item in profile.current if item.relation_name == "raw_payments"
            )
            relationship = next(
                item
                for item in payments.relationship_violations
                if item.name == "order_id_to_raw_orders_id"
            )
            key = next(
                item for item in payments.business_key_duplicates if item.name == "id"
            )
            fingerprint = next(
                item
                for item in payments.business_fingerprint_duplicates
                if item.name == "order_payment_amount"
            )
            group = next(item for item in payments.groups if item.name == "payment_method")
            observed_channel_count = next(
                count
                for values, count in zip(group.values, group.counts, strict=True)
                if values == (channel,)
            )
            assert (
                payments.row_count,
                relationship.violation_count,
                key.duplicate_count,
                fingerprint.duplicate_count,
                observed_channel_count,
            ) == (113 + violations, violations, 0, 0, channel_count)

            order_profile = next(
                (item for item in profile.current if item.relation_name == "raw_orders"),
                None,
            )
            order_history = next(
                (item for item in profile.history if item.relation_name == "raw_orders"),
                None,
            )
            assert (order_profile is not None) is history_visible
            assert (order_history is not None) is history_visible
            if history_visible:
                series = next(
                    item
                    for item in order_history.histories
                    if item.name == "order_count_by_day"
                )
                assert series.watermark_column == "order_date"
                assert series.watermark_value == "2018-04-09"
        finally:
            recovered = lab.restore(case_id)

        assert recovered.state == "HEALTHY"
        assert recovered.fingerprint == baseline.fingerprint
        assert lab._healthy_relation("raw_payments").row_count == 113
        assert lab._healthy_relation("raw_orders").row_count == 99
        assert "source_batch_note" not in {
            column.name for column in lab._healthy_relation("raw_payments").columns
        }
