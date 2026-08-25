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
                "artifacts": {
                    "manifest": "dbt/target/manifest.json",
                    "run_results": "dbt/target/run_results.json",
                    "dbt_log": "dbt/logs/dbt.log",
                    "schema": "schema.json",
                },
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
    (run_root / "dbt/stdout.log").write_text("", encoding="utf-8")
    (run_root / "dbt/stderr.log").write_text("", encoding="utf-8")
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


def test_verifier_accepts_manifest_and_run_result_extensions(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    manifest_path = run_root / "dbt/target/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["future_manifest_extension"] = {"version": 2}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    results_path = run_root / "dbt/target/run_results.json"
    results = json.loads(results_path.read_text(encoding="utf-8"))
    results["future_run_results_extension"] = ["kept"]
    results["results"][0]["future_result_extension"] = True
    results_path.write_text(json.dumps(results), encoding="utf-8")

    result = IncidentVerifier(tmp_path).verify(RUN_ID)

    assert result.status == "EXPECTED_FAILURE"


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


def test_verifier_rejects_tampered_metadata_artifact_mapping(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    metadata_path = run_root / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["artifacts"]["schema"] = "outside.json"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(LabVerificationError, match="artifacts"):
        IncidentVerifier(tmp_path).verify(RUN_ID)


def test_verifier_rejects_unknown_metadata_field(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    metadata_path = run_root / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["untrusted"] = "TEST_REDACTED_VALUE"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(LabVerificationError, match="字段集合"):
        IncidentVerifier(tmp_path).verify(RUN_ID)


def test_verifier_rejects_duplicate_json_keys(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    metadata_path = run_root / "metadata.json"
    metadata = metadata_path.read_text(encoding="utf-8")
    metadata_path.write_text(
        metadata.replace(
            f'"run_id": "{RUN_ID}"',
            f'"run_id": "{RUN_ID}", "run_id": "{RUN_ID}"',
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(LabVerificationError, match="读取验证产物"):
        IncidentVerifier(tmp_path).verify(RUN_ID)


def test_verifier_rejects_duplicate_run_result_ids(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    results_path = run_root / "dbt/target/run_results.json"
    results = json.loads(results_path.read_text(encoding="utf-8"))
    results["results"].append(
        {"unique_id": "model.jaffle_shop.orders", "status": "success"}
    )
    results_path.write_text(json.dumps(results), encoding="utf-8")

    with pytest.raises(LabVerificationError, match="unique_id 重复"):
        IncidentVerifier(tmp_path).verify(RUN_ID)


def test_verifier_rejects_duplicate_manifest_child_ids(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    manifest_path = run_root / "dbt/target/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["child_map"]["model.jaffle_shop.stg_payments"].append(
        "model.jaffle_shop.orders"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(LabVerificationError, match="节点重复"):
        IncidentVerifier(tmp_path).verify(RUN_ID)


def test_verifier_rejects_cyclic_manifest_lineage(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    manifest_path = run_root / "dbt/target/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["child_map"]["model.jaffle_shop.orders"].append(
        "model.jaffle_shop.stg_payments"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(LabVerificationError, match="存在循环"):
        IncidentVerifier(tmp_path).verify(RUN_ID)


def test_verifier_rejects_cross_branch_manifest_cycle(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    manifest_path = run_root / "dbt/target/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["child_map"]["model.jaffle_shop.orders"].append(
        "model.jaffle_shop.customers"
    )
    manifest["child_map"]["model.jaffle_shop.customers"].append(
        "model.jaffle_shop.orders"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(LabVerificationError, match="存在循环"):
        IncidentVerifier(tmp_path).verify(RUN_ID)


def test_verifier_rejects_missing_manifest_child_map_entry(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    manifest_path = run_root / "dbt/target/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["child_map"]["model.jaffle_shop.customers"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(LabVerificationError, match="缺少节点"):
        IncidentVerifier(tmp_path).verify(RUN_ID)


@pytest.mark.parametrize("mutation", ["cycle", "duplicate", "missing"])
def test_verifier_rejects_invalid_unrelated_manifest_graph(
    tmp_path: Path,
    project_root: Path,
    mutation: str,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    manifest_path = run_root / "dbt/target/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["nodes"]["model.jaffle_shop.unrelated"] = {"resource_type": "model"}
    if mutation == "cycle":
        manifest["nodes"]["model.jaffle_shop.unrelated_2"] = {"resource_type": "model"}
        manifest["child_map"]["model.jaffle_shop.unrelated"] = [
            "model.jaffle_shop.unrelated_2"
        ]
        manifest["child_map"]["model.jaffle_shop.unrelated_2"] = [
            "model.jaffle_shop.unrelated"
        ]
    elif mutation == "duplicate":
        manifest["child_map"]["model.jaffle_shop.unrelated"] = [
            "model.jaffle_shop.orders",
            "model.jaffle_shop.orders",
        ]
    else:
        pass
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(LabVerificationError, match="(循环|重复|不完整|缺少)"):
        IncidentVerifier(tmp_path).verify(RUN_ID)


def test_verifier_rejects_unrelated_non_model_cycle(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    manifest_path = run_root / "dbt/target/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["nodes"]["test.jaffle_shop.unrelated"] = {"resource_type": "test"}
    manifest["child_map"]["test.jaffle_shop.unrelated"] = [
        "test.jaffle_shop.unrelated"
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(LabVerificationError, match="存在循环"):
        IncidentVerifier(tmp_path).verify(RUN_ID)


def test_verifier_rejects_run_result_node_missing_from_manifest(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    results_path = run_root / "dbt/target/run_results.json"
    results = json.loads(results_path.read_text(encoding="utf-8"))
    results["results"].append(
        {"unique_id": "model.jaffle_shop.unknown", "status": "skipped"}
    )
    results_path.write_text(json.dumps(results), encoding="utf-8")

    with pytest.raises(LabVerificationError, match="节点不存在"):
        IncidentVerifier(tmp_path).verify(RUN_ID)


def test_verifier_rejects_duplicate_committed_ground_truth_key(
    tmp_path: Path,
    project_root: Path,
) -> None:
    _write_valid_run(tmp_path, project_root)
    config_path = tmp_path / "config/incidents/schema_rename_payment_amount.json"
    config = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        config.replace(
            '"root_cause_code": "SOURCE_SCHEMA_COLUMN_RENAMED"',
            '"root_cause_code": "SOURCE_SCHEMA_COLUMN_RENAMED", '
            '"root_cause_code": "SOURCE_SCHEMA_COLUMN_RENAMED"',
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(LabVerificationError, match="Ground Truth 快照无效"):
        IncidentVerifier(tmp_path).verify(RUN_ID)


def test_verifier_rejects_unknown_schema_field(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    schema_path = run_root / "schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["TEST_REDACTED_VALUE"] = "TEST_REDACTED_VALUE"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")

    with pytest.raises(LabVerificationError, match="Schema 字段集合"):
        IncidentVerifier(tmp_path).verify(RUN_ID)


@pytest.mark.parametrize("capture", ["stdout.log", "stderr.log"])
def test_verifier_rejects_missing_process_capture(
    tmp_path: Path,
    project_root: Path,
    capture: str,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    (run_root / "dbt" / capture).unlink()

    with pytest.raises(LabVerificationError, match="缺少"):
        IncidentVerifier(tmp_path).verify(RUN_ID)


def test_verifier_rejects_non_model_failure_node(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    results_path = run_root / "dbt/target/run_results.json"
    results = json.loads(results_path.read_text(encoding="utf-8"))
    results["results"].append(
        {"unique_id": "test.jaffle_shop.some_test", "status": "error"}
    )
    results_path.write_text(json.dumps(results), encoding="utf-8")

    with pytest.raises(LabVerificationError, match="直接失败节点"):
        IncidentVerifier(tmp_path).verify(RUN_ID)


def test_verifier_rejects_non_strict_schema_metadata(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    schema_path = run_root / "schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["relations"][0]["columns"][0]["nullable"] = 1
    schema_path.write_text(json.dumps(schema), encoding="utf-8")

    with pytest.raises(LabVerificationError, match="类型"):
        IncidentVerifier(tmp_path).verify(RUN_ID)


def test_verifier_rejects_non_integer_row_count(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    schema_path = run_root / "schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["relations"][0]["row_count"] = 113.0
    schema_path.write_text(json.dumps(schema), encoding="utf-8")

    with pytest.raises(LabVerificationError, match="行数"):
        IncidentVerifier(tmp_path).verify(RUN_ID)


def test_verifier_rejects_malformed_success_result(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    results_path = run_root / "dbt/target/run_results.json"
    results = json.loads(results_path.read_text(encoding="utf-8"))
    results["results"].append({"status": "success"})
    results_path.write_text(json.dumps(results), encoding="utf-8")

    with pytest.raises(LabVerificationError, match="结果项无效"):
        IncidentVerifier(tmp_path).verify(RUN_ID)


def test_verifier_wraps_invalid_utf8_without_exception_context(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    (run_root / "metadata.json").write_bytes(b"\xff")

    with pytest.raises(LabVerificationError) as error:
        IncidentVerifier(tmp_path).verify(RUN_ID)

    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_verifier_rejects_invalid_run_id(tmp_path: Path) -> None:
    with pytest.raises(LabVerificationError, match="run_id"):
        IncidentVerifier(tmp_path).verify("../../outside")
