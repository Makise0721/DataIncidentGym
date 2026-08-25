import json
from pathlib import Path

import pytest

from data_incident_gym.incidents import (
    CASE_ID,
    IncidentCaseError,
    load_ground_truth,
)


def test_committed_ground_truth_is_strict_and_canonical(project_root: Path) -> None:
    truth = load_ground_truth(CASE_ID, project_root)

    assert truth.incident_case_id == CASE_ID
    assert truth.root_cause_code == "SOURCE_SCHEMA_COLUMN_RENAMED"
    assert truth.injection.relation == "raw_payments"
    assert truth.injection.from_column == "amount"
    assert truth.injection.to_column == "total_amount"
    assert truth.direct_failure == "model.jaffle_shop.stg_payments"
    assert truth.affected_assets == (
        "model.jaffle_shop.stg_payments",
        "model.jaffle_shop.orders",
        "model.jaffle_shop.customers",
    )
    assert truth.required_evidence_types == (
        "DBT_NODE_ERROR",
        "RELATION_SCHEMA",
        "DBT_LINEAGE",
    )
    assert truth.expected_failure_category == "DBT_MODEL_ERROR"
    assert len(truth.digest()) == 64
    assert truth.to_json().endswith("\n")


def test_unknown_case_is_rejected_before_path_construction(tmp_path: Path) -> None:
    with pytest.raises(IncidentCaseError, match="未知故障案例"):
        load_ground_truth("../../outside", tmp_path)


def test_ground_truth_rejects_contract_drift(
    tmp_path: Path,
    project_root: Path,
) -> None:
    source = project_root / "config/incidents/schema_rename_payment_amount.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["root_cause_code"] = "WRONG_ROOT_CAUSE"
    target = tmp_path / "config/incidents/schema_rename_payment_amount.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(IncidentCaseError, match="Ground Truth 无效"):
        load_ground_truth(CASE_ID, tmp_path)
