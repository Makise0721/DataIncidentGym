from __future__ import annotations

from pathlib import Path

import pytest

from data_incident_gym.config import Settings
from data_incident_gym.lab_verifier import (
    IncidentVerifier,
    LabVerificationError,
    ScenarioVerification,
    ScenarioVerificationStatus,
)

RUN_ID = "a" * 32


def _verification(case_id: str = "schema_type_change_payment_amount") -> ScenarioVerification:
    return ScenarioVerification(
        status=ScenarioVerificationStatus.EXPECTED_FAILURE,
        incident_case_id=case_id,
        run_id=RUN_ID,
        dbt_exit_code=1,
        failed_nodes=("model.jaffle_shop.stg_payments",),
        skipped_nodes=(),
        affected_assets=(
            "model.jaffle_shop.customers",
            "model.jaffle_shop.orders",
            "model.jaffle_shop.stg_payments",
        ),
        schema_fingerprint="a" * 64,
        profile_spec_sha256="b" * 64,
    )


def test_private_verification_round_trips_without_database_access(tmp_path: Path) -> None:
    path = tmp_path / ".dig" / "lab" / "private" / RUN_ID / "verification.json"
    path.parent.mkdir(parents=True)
    path.write_text(_verification().to_json(), encoding="utf-8")

    loaded = IncidentVerifier(tmp_path, settings=Settings(_env_file=None)).load_verification(RUN_ID)

    assert loaded == _verification()


def test_private_verification_rejects_duplicate_or_cross_run_payload(tmp_path: Path) -> None:
    path = tmp_path / ".dig" / "lab" / "private" / RUN_ID / "verification.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"status":"EXPECTED_FAILURE","status":"EXPECTED_FAILURE",'
        f'"incident_case_id":"schema_type_change_payment_amount","run_id":"{RUN_ID}",'
        '"dbt_exit_code":1,"failed_nodes":[],"skipped_nodes":[],"affected_assets":[],'
        '"schema_fingerprint":"' + "a" * 64 + '","profile_spec_sha256":"' + "b" * 64 + '"}',
        encoding="utf-8",
    )

    with pytest.raises(LabVerificationError):
        IncidentVerifier(tmp_path, settings=Settings(_env_file=None)).load_verification(RUN_ID)


def test_scenario_verification_requires_sorted_unique_observations() -> None:
    with pytest.raises(ValueError, match="unique and sorted"):
        ScenarioVerification(
            status=ScenarioVerificationStatus.EXPECTED_FAILURE,
            incident_case_id="schema_type_change_payment_amount",
            run_id=RUN_ID,
            dbt_exit_code=1,
            failed_nodes=("z", "a"),
            skipped_nodes=(),
            affected_assets=(),
            schema_fingerprint="a" * 64,
            profile_spec_sha256="b" * 64,
        )


def test_run_results_treat_a_failed_test_as_a_failed_node(tmp_path: Path) -> None:
    test_id = "test.jaffle_shop.not_null_orders_customer_id.c5f02694af"
    path = tmp_path / "run_results.json"
    path.write_text(
        '{"results":[{"unique_id":"'
        + test_id
        + '","status":"fail"}]}',
        encoding="utf-8",
    )
    manifest = {"nodes": {test_id: {"resource_type": "test"}}}

    failed, skipped = IncidentVerifier._read_run_results(path, manifest)

    assert failed == (test_id,)
    assert skipped == ()


def test_failed_test_maps_only_to_its_distance_one_model() -> None:
    test_id = "test.jaffle_shop.not_null_orders_customer_id.c5f02694af"
    model_id = "model.jaffle_shop.orders"
    seed_id = "source.jaffle_shop.raw_orders"
    manifest = {
        "nodes": {
            test_id: {"resource_type": "test"},
            model_id: {"resource_type": "model"},
            seed_id: {"resource_type": "seed"},
        },
        "parent_map": {
            test_id: [model_id],
            model_id: [seed_id],
            seed_id: [],
        },
        "child_map": {
            test_id: [],
            model_id: [test_id],
            seed_id: [model_id],
        },
    }

    assert IncidentVerifier._affected_models(manifest, test_id) == {model_id}
