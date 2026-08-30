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
