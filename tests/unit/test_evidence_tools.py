import json
from pathlib import Path

import pytest

from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evidence import (
    InvalidArtifactError,
    InvalidRunIdError,
    NodeErrorNotFoundError,
    RunContextMismatchError,
    RunNotFoundError,
)
from data_incident_gym.evidence_tools import EvidenceTools

RUN_ID = "0123456789abcdef0123456789abcdef"
OTHER_RUN_ID = "fedcba9876543210fedcba9876543210"
FAILURE_NODE = "model.jaffle_shop.stg_payments"
SUCCESS_NODE = "model.jaffle_shop.seed_payments"
SKIPPED_NODE = "model.jaffle_shop.orders"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_run(tmp_path: Path, run_id: str = RUN_ID) -> Path:
    run_root = tmp_path / ".dig" / "lab" / "runs" / run_id
    _write_json(
        run_root / "metadata.json",
        {
            "schema_version": "m2.run.v1",
            "run_id": run_id,
            "incident_case_id": "schema_rename_payment_amount",
            "dbt_exit_code": 1,
            "ground_truth_digest": "a" * 64,
            "artifacts": {
                "manifest": "dbt/target/manifest.json",
                "run_results": "dbt/target/run_results.json",
                "dbt_log": "dbt/logs/dbt.log",
                "schema": "schema.json",
            },
        },
    )
    _write_json(
        run_root / "schema.json",
        {
            "schema": "analytics",
            "relations": [
                {
                    "name": "raw_payments",
                    "row_count": 113,
                    "columns": [
                        {
                            "name": "id",
                            "data_type": "integer",
                            "nullable": True,
                            "ordinal_position": 1,
                        },
                        {
                            "name": "total_amount",
                            "data_type": "integer",
                            "nullable": True,
                            "ordinal_position": 4,
                        },
                    ],
                }
            ],
            "fingerprint": "b" * 64,
        },
    )
    _write_json(
        run_root / "dbt" / "target" / "run_results.json",
        {
            "metadata": {"generated_at": "2026-08-25T09:00:00Z"},
            "results": [
                {
                    "unique_id": FAILURE_NODE,
                    "status": "error",
                    "message": (
                        'Database Error: column "amount" does not exist\r\n'
                        f"compiled code at {run_root / 'dbt/target/compiled.sql'}\r\n"
                    ),
                },
                {"unique_id": SKIPPED_NODE, "status": "skipped", "message": ""},
                {"unique_id": SUCCESS_NODE, "status": "success", "message": "OK"},
            ],
        },
    )
    nodes = {
        FAILURE_NODE: {"resource_type": "model", "name": "stg_payments"},
        SKIPPED_NODE: {"resource_type": "model", "name": "orders"},
        SUCCESS_NODE: {"resource_type": "seed", "name": "seed_payments"},
        "test.jaffle_shop.not_null_payments": {"resource_type": "test", "name": "not_null"},
    }
    _write_json(
        run_root / "dbt" / "target" / "manifest.json",
        {
            "nodes": nodes,
            "parent_map": {node_id: [] for node_id in nodes},
            "child_map": {node_id: [] for node_id in nodes},
        },
    )
    return run_root


def _tools(tmp_path: Path, run_id: str = RUN_ID) -> EvidenceTools:
    _write_run(tmp_path, run_id)
    return EvidenceTools.for_run(run_id, DiagnosticSettings(_env_file=None), tmp_path)


def test_get_dbt_run_results_returns_failed_and_skipped_nodes(tmp_path: Path) -> None:
    records = _tools(tmp_path).get_dbt_run_results(RUN_ID)

    assert len(records) == 1
    fact = records[0].content
    assert records[0].evidence_type.value == "DBT_RUN_RESULTS"
    assert fact.run_status == "FAILED"
    assert fact.failed_nodes == (FAILURE_NODE,)
    assert fact.skipped_nodes == (SKIPPED_NODE,)


def test_get_dbt_run_results_is_stable_when_called_twice(tmp_path: Path) -> None:
    tools = _tools(tmp_path)

    first = tools.get_dbt_run_results(RUN_ID)[0]
    second = tools.get_dbt_run_results(RUN_ID)[0]

    assert first == second


def test_get_dbt_node_error_returns_normalized_message_without_absolute_path(
    tmp_path: Path,
) -> None:
    tools = _tools(tmp_path)

    records = tools.get_dbt_node_error(RUN_ID, FAILURE_NODE)

    assert len(records) == 1
    message = records[0].content.message
    assert 'column "amount" does not exist' in message
    assert "compiled code at" not in message
    assert "\\" not in message
    assert str(tmp_path) not in message


@pytest.mark.parametrize("node_id", [SUCCESS_NODE, "test.jaffle_shop.not_null_payments", "missing"])
def test_get_dbt_node_error_rejects_successful_or_missing_node(
    tmp_path: Path,
    node_id: str,
) -> None:
    tools = _tools(tmp_path)

    with pytest.raises(NodeErrorNotFoundError):
        tools.get_dbt_node_error(RUN_ID, node_id)


def test_toolset_rejects_invalid_missing_or_mismatched_run(tmp_path: Path) -> None:
    with pytest.raises(InvalidRunIdError):
        EvidenceTools.for_run("not-a-run", DiagnosticSettings(_env_file=None), tmp_path)

    with pytest.raises(RunNotFoundError):
        EvidenceTools.for_run(RUN_ID, DiagnosticSettings(_env_file=None), tmp_path)

    tools = _tools(tmp_path)
    with pytest.raises(RunContextMismatchError):
        tools.get_dbt_run_results(OTHER_RUN_ID)


def test_artifact_reader_rejects_duplicate_keys_and_tampered_mapping(tmp_path: Path) -> None:
    run_root = _write_run(tmp_path)
    metadata_path = run_root / "metadata.json"
    metadata_path.write_text(
        metadata_path.read_text(encoding="utf-8").replace(
            f'"run_id": "{RUN_ID}"',
            f'"run_id": "{RUN_ID}", "run_id": "{RUN_ID}"',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(InvalidArtifactError):
        EvidenceTools.for_run(RUN_ID, DiagnosticSettings(_env_file=None), tmp_path)

    _write_run(tmp_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["artifacts"]["schema"] = "outside.json"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(InvalidArtifactError):
        EvidenceTools.for_run(RUN_ID, DiagnosticSettings(_env_file=None), tmp_path)


def test_artifact_reader_rejects_resolved_path_outside_run_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_root = _write_run(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    original_resolve = Path.resolve

    def resolve(path: Path, strict: bool = False) -> Path:
        resolved = original_resolve(path, strict=strict)
        if path == run_root / "schema.json":
            return outside
        return resolved

    monkeypatch.setattr(Path, "resolve", resolve)

    with pytest.raises(InvalidArtifactError):
        EvidenceTools.for_run(RUN_ID, DiagnosticSettings(_env_file=None), tmp_path)


def test_artifact_reader_rejects_invalid_generated_at_and_duplicate_node_ids(
    tmp_path: Path,
) -> None:
    run_root = _write_run(tmp_path)
    results_path = run_root / "dbt" / "target" / "run_results.json"
    results = json.loads(results_path.read_text(encoding="utf-8"))
    results["metadata"]["generated_at"] = "not-a-timestamp"
    results["results"].append(results["results"][0])
    results_path.write_text(json.dumps(results), encoding="utf-8")

    with pytest.raises(InvalidArtifactError):
        EvidenceTools.for_run(RUN_ID, DiagnosticSettings(_env_file=None), tmp_path)
