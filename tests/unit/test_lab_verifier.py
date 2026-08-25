import json
from pathlib import Path

import pytest

from data_incident_gym.baseline import (
    ColumnSummary,
    RelationSummary,
    make_baseline_summary,
)
from data_incident_gym.incidents import CASE_ID, load_ground_truth
from data_incident_gym.lab_verifier import IncidentVerifier, LabVerificationError

RUN_ID = "0123456789abcdef0123456789abcdef"


def _write_valid_run(tmp_path: Path, project_root: Path) -> Path:
    truth = load_ground_truth(CASE_ID, project_root)
    config_path = tmp_path / "config/incidents/schema_rename_payment_amount.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(truth.to_json(), encoding="utf-8")

    run_root = tmp_path / ".dig/lab/runs" / RUN_ID
    target = run_root / "dbt/target"
    logs = run_root / "dbt/logs"
    target.mkdir(parents=True)
    logs.mkdir(parents=True)
    (run_root / "ground_truth.json").write_text(truth.to_json(), encoding="utf-8")
    (run_root / "metadata.json").write_text(
        json.dumps(
            {
                "schema_version": "m2.run.v1",
                "run_id": RUN_ID,
                "incident_case_id": CASE_ID,
                "dbt_exit_code": 1,
                "ground_truth_digest": truth.digest(),
            }
        ),
        encoding="utf-8",
    )
    relation = RelationSummary(
        name="raw_payments",
        row_count=113,
        columns=(
            ColumnSummary("id", "integer", True, 1),
            ColumnSummary("order_id", "integer", True, 2),
            ColumnSummary("payment_method", "text", True, 3),
            ColumnSummary("total_amount", "integer", True, 4),
        ),
    )
    schema = make_baseline_summary("analytics", (relation,))
    (run_root / "schema.json").write_text(schema.to_json(), encoding="utf-8")
    (target / "run_results.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "unique_id": "model.jaffle_shop.stg_payments",
                        "status": "error",
                    },
                    {"unique_id": "model.jaffle_shop.orders", "status": "skipped"},
                    {"unique_id": "model.jaffle_shop.customers", "status": "skipped"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (target / "manifest.json").write_text(
        json.dumps(
            {
                "nodes": {
                    "model.jaffle_shop.stg_payments": {"resource_type": "model"},
                    "model.jaffle_shop.orders": {"resource_type": "model"},
                    "model.jaffle_shop.customers": {"resource_type": "model"},
                },
                "child_map": {
                    "model.jaffle_shop.stg_payments": [
                        "model.jaffle_shop.orders",
                        "model.jaffle_shop.customers",
                    ],
                    "model.jaffle_shop.orders": [],
                    "model.jaffle_shop.customers": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (logs / "dbt.log").write_text("Database Error", encoding="utf-8")
    return run_root


def test_verifier_accepts_expected_failure_and_writes_stable_result(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)

    result = IncidentVerifier(tmp_path).verify(RUN_ID)

    assert result.status == "EXPECTED_FAILURE"
    assert result.failed_nodes == ("model.jaffle_shop.stg_payments",)
    assert result.affected_assets == (
        "model.jaffle_shop.stg_payments",
        "model.jaffle_shop.orders",
        "model.jaffle_shop.customers",
    )
    assert result.error_category == "DBT_MODEL_ERROR"
    assert (run_root / "verification.json").read_text(encoding="utf-8") == (
        result.to_json()
    )


def test_verifier_rejects_wrong_failed_node(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    results_path = run_root / "dbt/target/run_results.json"
    results_path.write_text(
        json.dumps(
            {
                "results": [
                    {"unique_id": "model.jaffle_shop.orders", "status": "error"}
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(LabVerificationError, match="直接失败节点"):
        IncidentVerifier(tmp_path).verify(RUN_ID)


def test_verifier_rejects_unexpected_dbt_success(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    metadata_path = run_root / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["dbt_exit_code"] = 0
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(LabVerificationError, match="dbt 意外成功"):
        IncidentVerifier(tmp_path).verify(RUN_ID)


def test_verifier_rejects_invalid_run_id(tmp_path: Path) -> None:
    with pytest.raises(LabVerificationError, match="run_id"):
        IncidentVerifier(tmp_path).verify("../../outside")
