import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evidence import (
    InvalidArtifactError,
    InvalidDirectionError,
    InvalidRunIdError,
    NodeErrorNotFoundError,
    NodeNotFoundError,
    ReadOnlyDatabaseError,
    RelationNotAllowedError,
    RelationNotFoundError,
    RunContextMismatchError,
    RunNotFoundError,
    RunStateDriftError,
)
from data_incident_gym.evidence_tools import EvidenceTools

RUN_ID = "0123456789abcdef0123456789abcdef"
OTHER_RUN_ID = "fedcba9876543210fedcba9876543210"
FAILURE_NODE = "model.jaffle_shop.stg_payments"
SUCCESS_NODE = "seed.jaffle_shop.raw_payments"
SKIPPED_NODE = "model.jaffle_shop.orders"
CUSTOMERS_NODE = "model.jaffle_shop.customers"
TEST_NODE = "test.jaffle_shop.not_null_payments"
SOURCE_NODE = "source.jaffle_shop.raw_payments"


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
        CUSTOMERS_NODE: {"resource_type": "model", "name": "customers"},
        SUCCESS_NODE: {"resource_type": "seed", "name": "raw_payments"},
        TEST_NODE: {"resource_type": "test", "name": "not_null"},
    }
    sources = {SOURCE_NODE: {"resource_type": "source", "name": "raw_payments"}}
    _write_json(
        run_root / "dbt" / "target" / "manifest.json",
        {
            "nodes": nodes,
            "sources": sources,
            "metadata": {"generated_at": "2026-08-25T10:00:00Z"},
            "parent_map": {
                FAILURE_NODE: [SUCCESS_NODE],
                SKIPPED_NODE: [FAILURE_NODE],
                CUSTOMERS_NODE: [SKIPPED_NODE],
                SUCCESS_NODE: [],
                TEST_NODE: [],
                SOURCE_NODE: [],
            },
            "child_map": {
                FAILURE_NODE: [SKIPPED_NODE],
                SKIPPED_NODE: [CUSTOMERS_NODE],
                CUSTOMERS_NODE: [],
                SUCCESS_NODE: [FAILURE_NODE],
                TEST_NODE: [],
                SOURCE_NODE: [],
            },
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


def test_get_dbt_lineage_returns_stable_transitive_downstream_models(
    tmp_path: Path,
) -> None:
    tools = _tools(tmp_path)

    records = tools.get_dbt_lineage(FAILURE_NODE, "downstream")

    assert len(records) == 1
    lineage = records[0]
    assert lineage.evidence_type.value == "DBT_LINEAGE"
    assert lineage.source.value == "dbt_artifact:manifest.json"
    assert lineage.observed_at.isoformat() == "2026-08-25T10:00:00+00:00"
    assert tuple(node.node_id for node in lineage.content.related_nodes) == (
        SKIPPED_NODE,
        CUSTOMERS_NODE,
    )
    assert tuple(node.distance for node in lineage.content.related_nodes) == (1, 2)
    assert tuple(node.resource_type for node in lineage.content.related_nodes) == (
        "model",
        "model",
    )
    assert tuple(node.name for node in lineage.content.related_nodes) == (
        "orders",
        "customers",
    )
    assert records == tools.get_dbt_lineage(FAILURE_NODE, "downstream")


def test_get_dbt_lineage_returns_upstream_seed(tmp_path: Path) -> None:
    tools = _tools(tmp_path)

    lineage = tools.get_dbt_lineage(FAILURE_NODE, "upstream")[0].content

    assert tuple(node.node_id for node in lineage.related_nodes) == (SUCCESS_NODE,)
    assert tuple(node.distance for node in lineage.related_nodes) == (1,)


def test_get_dbt_lineage_returns_record_for_valid_leaf_with_no_descendants(
    tmp_path: Path,
) -> None:
    tools = _tools(tmp_path)

    records = tools.get_dbt_lineage(CUSTOMERS_NODE, "downstream")

    assert len(records) == 1
    assert records[0].content.related_nodes == ()


@pytest.mark.parametrize("direction", ["", "UPSTREAM", "downstream ", "upstream\n"])
def test_get_dbt_lineage_rejects_unknown_direction(
    tmp_path: Path,
    direction: str,
) -> None:
    tools = _tools(tmp_path)

    with pytest.raises(InvalidDirectionError):
        tools.get_dbt_lineage(FAILURE_NODE, direction)


def test_get_dbt_lineage_rejects_unknown_node(tmp_path: Path) -> None:
    tools = _tools(tmp_path)

    with pytest.raises(NodeNotFoundError):
        tools.get_dbt_lineage("model.jaffle_shop.missing", "downstream")


@pytest.mark.parametrize("mutation", ["duplicate", "dangling", "cycle"])
def test_get_dbt_lineage_rejects_duplicate_dangling_and_cyclic_edges(
    tmp_path: Path,
    mutation: str,
) -> None:
    run_root = _write_run(tmp_path)
    manifest_path = run_root / "dbt" / "target" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "duplicate":
        manifest["child_map"][FAILURE_NODE].append(SKIPPED_NODE)
    elif mutation == "dangling":
        manifest["child_map"][FAILURE_NODE].append("model.jaffle_shop.missing")
    else:
        manifest["child_map"][FAILURE_NODE].append(TEST_NODE)
        manifest["child_map"][TEST_NODE].append(TEST_NODE)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(InvalidArtifactError):
        tools = EvidenceTools.for_run(RUN_ID, DiagnosticSettings(_env_file=None), tmp_path)
        tools.get_dbt_lineage(FAILURE_NODE, "downstream")


def test_toolset_rejects_invalid_missing_or_mismatched_run(tmp_path: Path) -> None:
    with pytest.raises(InvalidRunIdError):
        EvidenceTools.for_run("not-a-run", DiagnosticSettings(_env_file=None), tmp_path)

    with pytest.raises(RunNotFoundError):
        EvidenceTools.for_run(RUN_ID, DiagnosticSettings(_env_file=None), tmp_path)

    tools = _tools(tmp_path)
    with pytest.raises(RunContextMismatchError):
        tools.get_dbt_run_results(OTHER_RUN_ID)


def test_artifact_reader_does_not_validate_ground_truth_digest(tmp_path: Path) -> None:
    run_root = _write_run(tmp_path)
    metadata_path = run_root / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["ground_truth_digest"] = "not-a-digest"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    EvidenceTools.for_run(RUN_ID, DiagnosticSettings(_env_file=None), tmp_path)


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


class _ReaderCursor:
    def __init__(self, columns: list[tuple[object, ...]] | None = None) -> None:
        self.columns = columns if columns is not None else [("id", "integer", "YES", 1)]
        self.executions: list[tuple[object, object]] = []
        self.last_query = ""

    def __enter__(self) -> "_ReaderCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: object, params: object = None) -> None:
        self.executions.append((query, params))
        self.last_query = str(query)

    def fetchone(self) -> tuple[object, ...] | None:
        if "transaction_read_only" in self.last_query:
            return ("on",)
        if "CURRENT_TIMESTAMP" in self.last_query:
            return (datetime(2026, 8, 25, 12, tzinfo=UTC),)
        return None

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.columns


class _ReaderConnection:
    def __init__(self, cursor: _ReaderCursor) -> None:
        self.cursor_value = cursor

    def __enter__(self) -> "_ReaderConnection":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> _ReaderCursor:
        return self.cursor_value


def _reader_tools(tmp_path: Path, cursor: _ReaderCursor) -> tuple[EvidenceTools, dict[str, object]]:
    _write_run(tmp_path)
    settings = DiagnosticSettings(_env_file=None, postgres_password="TEST_REDACTED_VALUE")
    connect_kwargs: dict[str, object] = {}

    def connect(**kwargs: object) -> _ReaderConnection:
        connect_kwargs.update(kwargs)
        return _ReaderConnection(cursor)

    return (
        EvidenceTools.for_run(RUN_ID, settings, tmp_path, db_connect=connect),
        connect_kwargs,
    )


def test_get_relation_schema_uses_reader_and_returns_live_columns(tmp_path: Path) -> None:
    cursor = _ReaderCursor(
        columns=[
            ("id", "integer", "YES", 1),
            ("total_amount", "integer", "YES", 4),
        ]
    )
    tools, kwargs = _reader_tools(tmp_path, cursor)
    record = tools.get_relation_schema("raw_payments")[0]

    assert record.evidence_type.value == "RELATION_SCHEMA"
    assert record.source.value == "postgres_catalog"
    assert tuple(column.name for column in record.content.columns) == ("id", "total_amount")
    assert kwargs["user"] == "dig_reader"
    assert kwargs["password"] == "TEST_REDACTED_VALUE"
    assert [str(query).strip().upper() for query, _ in cursor.executions] == [
        "SET TRANSACTION READ ONLY",
        "SHOW TRANSACTION_READ_ONLY",
        (
            "SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, ORDINAL_POSITION\n"
            "FROM INFORMATION_SCHEMA.COLUMNS\n"
            "WHERE TABLE_SCHEMA = %S AND TABLE_NAME = %S\n"
            "ORDER BY ORDINAL_POSITION"
        ),
        "SELECT CURRENT_TIMESTAMP",
    ]
    assert cursor.executions[2][1] == ("analytics", "raw_payments")


def test_get_relation_schema_rejects_relation_not_in_run_snapshot(tmp_path: Path) -> None:
    cursor = _ReaderCursor()
    tools, _ = _reader_tools(tmp_path, cursor)

    with pytest.raises(RelationNotAllowedError):
        tools.get_relation_schema("orders")

    assert cursor.executions == []


def test_get_relation_schema_rejects_missing_relation(tmp_path: Path) -> None:
    cursor = _ReaderCursor(columns=[])
    tools, _ = _reader_tools(tmp_path, cursor)

    with pytest.raises(RelationNotFoundError):
        tools.get_relation_schema("raw_payments")


def test_get_relation_schema_rejects_live_schema_different_from_run_snapshot(
    tmp_path: Path,
) -> None:
    cursor = _ReaderCursor(columns=[("id", "bigint", "YES", 1)])
    tools, _ = _reader_tools(tmp_path, cursor)

    with pytest.raises(RunStateDriftError):
        tools.get_relation_schema("raw_payments")


def test_get_relation_schema_redacts_reader_database_errors(tmp_path: Path) -> None:
    cursor = _ReaderCursor()

    def fail_execute(query: object, params: object = None) -> None:
        raise RuntimeError("database failed with TEST_REDACTED_VALUE")

    cursor.execute = fail_execute  # type: ignore[method-assign]
    tools, _ = _reader_tools(tmp_path, cursor)

    with pytest.raises(ReadOnlyDatabaseError) as error:
        tools.get_relation_schema("raw_payments")

    assert "TEST_REDACTED_VALUE" not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
