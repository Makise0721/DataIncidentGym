"""T13 slice 1: the v2 batch evidence tools over synthetic run artifacts.

Offline on purpose: the tools read run-bound files (baseline snapshot, manifest,
run_results, compiled SQL) behind the frozen batch protocol, so a fabricated run
root exercises every rule — atomic refusal with mixed codes, dedupe/order/limit,
per-target UNKNOWN, run membership and content agreement — without a database.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_incident_gym.evidence import (
    BatchTargetsRefusedError,
    BatchTooLargeError,
    DbtNodeDefinitionFact,
    EvidenceIntegrityError,
    EvidenceType,
    RelationSchemaExpectationFact,
    TargetsEmptyError,
)
from data_incident_gym.evidence_batch import (
    BATCH_TARGET_LIMIT,
    MAX_COMPILED_SQL_BYTES,
    BatchEvidenceTools,
)
from data_incident_gym.lab import IncidentExecutionError
from data_incident_gym.run_context import (
    EVIDENCE_BASELINE_FILENAME,
    RUNTIME_V2_SCHEMA_VERSION,
    ObservableRunContext,
    RunContextError,
    _validate_runtime,
    compiled_tree_digest,
)
from data_incident_gym.strategy_adapter import ToolRequest

RUN_ID = "d" * 32
CUSTOMERS = "model.jaffle_shop.customers"
STG_CUSTOMERS = "model.jaffle_shop.stg_customers"
STG_ORDERS = "model.jaffle_shop.stg_orders"
STG_PAYMENTS = "model.jaffle_shop.stg_payments"
INVOCATION = "2fc86c24-abc8-47ec-9111-47979b836e01"
GENERATED_AT = "2026-09-18T04:45:51.689404Z"
BASELINE_FINGERPRINT = "e5" + "0" * 62


def _compiled_text(node: str) -> str:
    return f"select * from {node.split('.')[-1]}"


def _run_root(
    tmp_path: Path,
    *,
    nodes: dict[str, dict],
    parent_map: dict[str, list[str]],
    results: list[dict],
    compiled_files: dict[str, str],
    baseline_relations: list[dict] | None = None,
    published_invocation: str = INVOCATION,
) -> tuple[Path, dict]:
    """Write a run root's files and return it with their measured digests."""

    target = tmp_path / "dbt" / "target"
    compiled_root = target / "compiled"
    compiled_root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "metadata": {"invocation_id": published_invocation},
        "nodes": {
            node_id: {
                "resource_type": node.get("resource_type", "model"),
                "columns": {name: {} for name in node.get("columns", ())},
                "depends_on": {"nodes": node.get("depends_on", [])},
                **({"name": node["name"]} if node.get("name") is not None else {}),
                **(
                    {"relation_name": node["relation_name"]}
                    if node.get("relation_name") is not None
                    else {}
                ),
                **(
                    {"compiled_code": node["manifest_text"]}
                    if node.get("manifest_text") is not None
                    else {}
                ),
                **(
                    {"compiled_path": str((compiled_root / node["file"]).resolve())}
                    if node.get("file") is not None
                    else {}
                ),
            }
            for node_id, node in nodes.items()
        },
        "sources": {},
        "parent_map": parent_map,
        "child_map": {node_id: [] for node_id in nodes},
    }
    run_results = {
        "metadata": {"invocation_id": published_invocation},
        "results": results,
    }
    (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (target / "run_results.json").write_text(json.dumps(run_results), encoding="utf-8")
    for name, text in compiled_files.items():
        path = compiled_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        # Bytes, not text mode: the fixture must control line endings exactly,
        # as dbt does (it writes CRLF compiled files on Windows).
        path.write_bytes(text.encode("utf-8"))
    baseline_payload = {
        "schema_version": "p1.evidence_baseline.v1",
        "generated_at": GENERATED_AT,
        "baseline_fingerprint": BASELINE_FINGERPRINT,
        "relations": baseline_relations
        if baseline_relations is not None
        else [
            {
                "name": "raw_customers",
                "columns": [
                    {
                        "name": "id",
                        "expected_data_type": "integer",
                        "expected_nullable": True,
                        "ordinal_position": 1,
                    }
                ],
            }
        ],
    }
    (tmp_path / EVIDENCE_BASELINE_FILENAME).write_text(
        json.dumps(baseline_payload), encoding="utf-8"
    )
    def _digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _node_text(node: dict) -> str | None:
        by_source = (
            node.get("run_results_text"),
            node.get("file_text"),
            node.get("manifest_text"),
        )
        for value in by_source:
            if value is not None:
                return value
        return None

    node_definitions = {}
    for node_id, node in nodes.items():
        text_value = _node_text(node)
        if text_value is None:
            continue
        node_definitions[node_id] = {
            "sha256": hashlib.sha256(text_value.encode("utf-8")).hexdigest(),
            "redacted": False,
        }
    digests = {
        "baseline": _digest(tmp_path / EVIDENCE_BASELINE_FILENAME),
        "manifest": _digest(target / "manifest.json"),
        "run_results": _digest(target / "run_results.json"),
        "compiled_tree": compiled_tree_digest(compiled_root),
        "node_definitions": node_definitions,
    }
    return tmp_path, digests


def _context(
    run_root: Path,
    digests: dict,
    *,
    expectation: tuple[str, ...] = ("raw_customers",),
    definition: tuple[str, ...] = (CUSTOMERS,),
    is_v2: bool = True,
) -> ObservableRunContext:
    if is_v2:
        runtime = {
            "schema_version": RUNTIME_V2_SCHEMA_VERSION,
            "run_id": RUN_ID,
            "dbt_exit_code": 1,
            "artifacts": {
                "manifest": "dbt/target/manifest.json",
                "run_results": "dbt/target/run_results.json",
                "dbt_log": "dbt/logs/dbt.log",
                "schema": "schema.json",
                "profile_snapshot": "profile_snapshot.json",
                "incident_brief": "incident_brief.json",
            },
            "observable_relations": {
                "schema": ["raw_customers", "raw_orders"],
                "profile": [],
                "history": [],
                "expectation": list(expectation),
            },
            "observable_nodes": {"definition": list(definition)},
            "evidence_baseline": {
                "path": EVIDENCE_BASELINE_FILENAME,
                "baseline_fingerprint": BASELINE_FINGERPRINT,
                "sha256": digests["baseline"],
            },
            "build_provenance": {
                "dbt_invocation_id": INVOCATION,
                "artifact_sha256": {
                    "manifest": digests["manifest"],
                    "run_results": digests["run_results"],
                    "compiled_tree": digests["compiled_tree"],
                },
                "node_definitions": digests["node_definitions"],
            },
            "profile_spec_sha256": "b" * 64,
        }
        runtime = _validate_runtime(runtime, RUN_ID)
    else:
        runtime = {
            "schema_version": "p1.runtime.v1",
            "run_id": RUN_ID,
            "dbt_exit_code": 1,
            "artifacts": {
                "manifest": "dbt/target/manifest.json",
                "run_results": "dbt/target/run_results.json",
                "dbt_log": "dbt/logs/dbt.log",
                "schema": "schema.json",
                "profile_snapshot": "profile_snapshot.json",
                "incident_brief": "incident_brief.json",
            },
            "observable_relations": {
                "schema": ["raw_customers"],
                "profile": [],
                "history": [],
            },
            "profile_spec_sha256": "b" * 64,
        }
    brief = SimpleNamespace()
    return ObservableRunContext(RUN_ID, run_root, runtime, brief)  # type: ignore[arg-type]


def _tools(run_root: Path, digests: dict, **context_kwargs) -> BatchEvidenceTools:
    context = _context(run_root, digests, **context_kwargs)
    target = run_root / "dbt" / "target"
    artifacts = SimpleNamespace(
        context=context,
        run_root=run_root,
        manifest=json.loads((target / "manifest.json").read_text(encoding="utf-8")),
        run_results=json.loads((target / "run_results.json").read_text(encoding="utf-8")),
        manifest_generated_at=datetime(2026, 9, 18, tzinfo=UTC),
        read_json=lambda path: json.loads(Path(path).read_text(encoding="utf-8")),
    )
    return BatchEvidenceTools(RUN_ID, artifacts)


def _node_graph() -> tuple[dict, dict, list, dict]:
    nodes = {
        CUSTOMERS: {"depends_on": [STG_CUSTOMERS, STG_PAYMENTS]},
        STG_CUSTOMERS: {"depends_on": ["seed.jaffle_shop.raw_customers"]},
        STG_ORDERS: {"depends_on": ["seed.jaffle_shop.raw_orders"]},
        STG_PAYMENTS: {"depends_on": ["seed.jaffle_shop.raw_payments"]},
    }
    parent_map = {
        CUSTOMERS: [STG_CUSTOMERS, STG_PAYMENTS],
        STG_CUSTOMERS: ["seed.jaffle_shop.raw_customers"],
        STG_ORDERS: ["seed.jaffle_shop.raw_orders"],
        STG_PAYMENTS: ["seed.jaffle_shop.raw_payments"],
    }
    results = [{"unique_id": CUSTOMERS, "status": "error", "message": "boom"}]
    return nodes, parent_map, results, {}


# -- run context v2 validation ----------------------------------------------


def test_v1_runtime_rejects_the_v2_fields(tmp_path: Path) -> None:
    payload = {
        "schema_version": "p1.runtime.v1",
        "run_id": RUN_ID,
        "dbt_exit_code": 0,
        "artifacts": {
            "manifest": "dbt/target/manifest.json",
            "run_results": "dbt/target/run_results.json",
            "dbt_log": "dbt/logs/dbt.log",
            "schema": "schema.json",
            "profile_snapshot": "profile_snapshot.json",
            "incident_brief": "incident_brief.json",
        },
        "observable_relations": {"schema": [], "profile": [], "history": []},
        "profile_spec_sha256": "b" * 64,
        "observable_nodes": {"definition": []},
    }
    with pytest.raises(RunContextError):
        _validate_runtime(payload, RUN_ID)


def test_v2_runtime_requires_the_expectation_subset() -> None:
    payload = {
        "schema_version": RUNTIME_V2_SCHEMA_VERSION,
        "run_id": RUN_ID,
        "dbt_exit_code": 0,
        "artifacts": {
            "manifest": "dbt/target/manifest.json",
            "run_results": "dbt/target/run_results.json",
            "dbt_log": "dbt/logs/dbt.log",
            "schema": "schema.json",
            "profile_snapshot": "profile_snapshot.json",
            "incident_brief": "incident_brief.json",
        },
        "observable_relations": {
            "schema": ["raw_customers"],
            "profile": [],
            "history": [],
            "expectation": ["raw_orders"],
        },
        "observable_nodes": {"definition": []},
        "evidence_baseline": {
            "path": EVIDENCE_BASELINE_FILENAME,
            "baseline_fingerprint": BASELINE_FINGERPRINT,
            "sha256": "a" * 64,
        },
        "build_provenance": {
            "dbt_invocation_id": INVOCATION,
            "artifact_sha256": {
                "manifest": "a" * 64,
                "run_results": "a" * 64,
                "compiled_tree": "a" * 64,
            },
            "node_definitions": {},
        },
        "profile_spec_sha256": "b" * 64,
    }
    with pytest.raises(RunContextError):
        _validate_runtime(payload, RUN_ID)


# -- E1: expectations --------------------------------------------------------


def test_expectations_return_one_fact_per_target_in_request_order(tmp_path: Path) -> None:
    nodes, parent_map, results, files = _node_graph()
    run_root, digests = _run_root(
        tmp_path, nodes=nodes, parent_map=parent_map, results=results, compiled_files=files,
        baseline_relations=[
            {
                "name": "raw_customers",
                "columns": [
                    {"name": "id", "expected_data_type": "integer",
                     "expected_nullable": True, "ordinal_position": 1},
                    {"name": "first_name", "expected_data_type": "text",
                     "expected_nullable": True, "ordinal_position": 2},
                ],
            }
        ],
    )
    tools = _tools(run_root, digests, expectation=("raw_customers", "raw_orders"))

    records = tools.get_relation_schema_expectation("raw_customers,raw_orders,raw_customers")

    assert [record.subject for record in records] == ["raw_customers", "raw_orders"]
    assert all(
        record.evidence_type is EvidenceType.RELATION_SCHEMA_EXPECTATION for record in records
    )
    known = records[0].content
    assert isinstance(known, RelationSchemaExpectationFact)
    assert known.known is True
    assert known.baseline_fingerprint == BASELINE_FINGERPRINT
    assert [column.name for column in known.columns] == ["id", "first_name"]
    unknown = records[1].content
    assert isinstance(unknown, RelationSchemaExpectationFact)
    assert unknown.known is False
    assert unknown.columns == ()


def test_expectations_refuse_atomically_with_per_target_codes(tmp_path: Path) -> None:
    nodes, parent_map, results, files = _node_graph()
    run_root, digests = _run_root(
        tmp_path, nodes=nodes, parent_map=parent_map, results=results, compiled_files=files
    )
    tools = _tools(run_root, digests, expectation=("raw_customers",))

    with pytest.raises(BatchTargetsRefusedError) as error:
        tools.get_relation_schema_expectation("raw_customers,raw_orders")

    assert error.value.target_refusals == (("raw_orders", "RELATION_NOT_ALLOWED"),)


def test_expectations_are_refused_entirely_on_a_v1_run(tmp_path: Path) -> None:
    nodes, parent_map, results, files = _node_graph()
    run_root, digests = _run_root(
        tmp_path, nodes=nodes, parent_map=parent_map, results=results, compiled_files=files
    )
    tools = _tools(run_root, digests, is_v2=False)

    with pytest.raises(BatchTargetsRefusedError) as error:
        tools.get_relation_schema_expectation("raw_customers")

    assert error.value.target_refusals == (("raw_customers", "RELATION_NOT_ALLOWED"),)


def test_batch_limits_and_empty_requests_are_frozen(tmp_path: Path) -> None:
    nodes, parent_map, results, files = _node_graph()
    run_root, digests = _run_root(
        tmp_path, nodes=nodes, parent_map=parent_map, results=results, compiled_files=files
    )
    tools = _tools(run_root, digests)

    with pytest.raises(TargetsEmptyError):
        tools.get_relation_schema_expectation("")
    with pytest.raises(TargetsEmptyError):
        tools.get_relation_schema_expectation(",,")
    too_many = ",".join(f"raw_{index}" for index in range(BATCH_TARGET_LIMIT + 1))
    with pytest.raises(BatchTooLargeError):
        tools.get_relation_schema_expectation(too_many)


def test_a_tampered_baseline_snapshot_is_an_integrity_error(tmp_path: Path) -> None:
    nodes, parent_map, results, files = _node_graph()
    run_root, digests = _run_root(
        tmp_path, nodes=nodes, parent_map=parent_map, results=results, compiled_files=files
    )
    tools = _tools(run_root, digests)
    payload = json.loads((run_root / EVIDENCE_BASELINE_FILENAME).read_text(encoding="utf-8"))
    payload["baseline_fingerprint"] = "f" * 64
    (run_root / EVIDENCE_BASELINE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvidenceIntegrityError):
        tools.get_relation_schema_expectation("raw_customers")


# -- E2: node definitions ----------------------------------------------------


def test_definitions_resolve_each_source_precedence_case(tmp_path: Path) -> None:
    nodes = {
        CUSTOMERS: {
            "depends_on": [STG_CUSTOMERS, STG_ORDERS],
            "columns": ("customer_id",),
            "manifest_text": _compiled_text(CUSTOMERS),
            "file": "customers.sql",
        },
        STG_CUSTOMERS: {
            "depends_on": ["seed.jaffle_shop.raw_customers"],
            "manifest_text": _compiled_text(STG_CUSTOMERS),
        },
        STG_ORDERS: {"depends_on": ["seed.jaffle_shop.raw_orders"]},
    }
    parent_map = {
        CUSTOMERS: [STG_CUSTOMERS, STG_ORDERS],
        STG_CUSTOMERS: ["seed.jaffle_shop.raw_customers"],
        STG_ORDERS: ["seed.jaffle_shop.raw_orders"],
    }
    results = [
        {
            "unique_id": CUSTOMERS,
            "status": "error",
            "compiled_code": nodes[CUSTOMERS].get("run_results_text")
            or nodes[CUSTOMERS].get("manifest_text")
            or nodes[CUSTOMERS].get("file_text"),
        }
    ]
    files = {"customers.sql": _compiled_text(CUSTOMERS)}
    run_root, digests = _run_root(
        tmp_path, nodes=nodes, parent_map=parent_map, results=results, compiled_files=files
    )
    tools = _tools(
        run_root, digests, definition=(CUSTOMERS, STG_CUSTOMERS, STG_ORDERS)
    )

    records = tools.get_dbt_node_definition(f"{CUSTOMERS},{STG_CUSTOMERS},{STG_ORDERS}")

    assert [record.subject for record in records] == [CUSTOMERS, STG_CUSTOMERS, STG_ORDERS]
    first = records[0].content
    assert isinstance(first, DbtNodeDefinitionFact)
    assert first.known is True and first.complete is True
    assert first.compiled_sql == _compiled_text(CUSTOMERS)
    assert first.declared_columns == ("customer_id",)
    assert first.depends_on == (STG_CUSTOMERS, STG_ORDERS)
    # Manifest-only fallback still yields the text.
    second = records[1].content
    assert isinstance(second, DbtNodeDefinitionFact)
    assert second.compiled_sql == _compiled_text(STG_CUSTOMERS)
    # No compiled text anywhere: an explicit unknown, never complete.
    third = records[2].content
    assert isinstance(third, DbtNodeDefinitionFact)
    assert third.known is False and third.complete is False
    assert third.compiled_sql is None


def test_definitions_refuse_mixed_codes_in_one_batch(tmp_path: Path) -> None:
    nodes, parent_map, results, files = _node_graph()
    # STG_PAYMENTS exists in the manifest but is not an ancestor of the failed
    # node; UNKNOWN_NODE is granted by the contract but absent from the run.
    parent_map[CUSTOMERS] = [STG_CUSTOMERS]
    run_root, digests = _run_root(
        tmp_path, nodes=nodes, parent_map=parent_map, results=results, compiled_files=files
    )
    tools = _tools(
        run_root,
        digests,
        definition=(STG_PAYMENTS, "model.jaffle_shop.unknown"),
    )

    with pytest.raises(BatchTargetsRefusedError) as error:
        tools.get_dbt_node_definition(f"{STG_PAYMENTS},model.jaffle_shop.unknown")

    assert error.value.target_refusals == (
        (STG_PAYMENTS, "NODE_NOT_ALLOWED"),
        ("model.jaffle_shop.unknown", "NODE_NOT_FOUND"),
    )


def test_an_ungranted_definition_is_refused_mixed_with_granted_targets(
    tmp_path: Path,
) -> None:
    nodes, parent_map, results, files = _node_graph()
    run_root, digests = _run_root(
        tmp_path, nodes=nodes, parent_map=parent_map, results=results, compiled_files=files
    )
    tools = _tools(run_root, digests, definition=(CUSTOMERS,))

    with pytest.raises(BatchTargetsRefusedError) as error:
        tools.get_dbt_node_definition(f"{CUSTOMERS},{STG_CUSTOMERS}")

    assert error.value.target_refusals == ((STG_CUSTOMERS, "NODE_NOT_ALLOWED"),)


def test_definitions_reject_artifacts_from_another_build(tmp_path: Path) -> None:
    nodes, parent_map, results, files = _node_graph()
    nodes[CUSTOMERS] = {
        **nodes[CUSTOMERS],
        "manifest_text": _compiled_text(CUSTOMERS),
        "file": "customers.sql",
    }
    files = {"customers.sql": _compiled_text(CUSTOMERS)}
    results = [
        {"unique_id": CUSTOMERS, "status": "error", "compiled_code": _compiled_text(CUSTOMERS)}
    ]
    run_root, digests = _run_root(
        tmp_path, nodes=nodes, parent_map=parent_map, results=results, compiled_files=files
    )
    tools = _tools(run_root, digests)
    # Swap in another build's manifest: digests no longer match the record.
    target = run_root / "dbt" / "target"
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    manifest["metadata"]["invocation_id"] = "another-invocation"
    (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(EvidenceIntegrityError):
        tools.get_dbt_node_definition(CUSTOMERS)


def test_definitions_reject_invocation_mismatch(tmp_path: Path) -> None:
    nodes, parent_map, results, files = _node_graph()
    nodes[CUSTOMERS] = {
        **nodes[CUSTOMERS],
        "manifest_text": _compiled_text(CUSTOMERS),
        "file": "customers.sql",
    }
    files = {"customers.sql": _compiled_text(CUSTOMERS)}
    results = [
        {"unique_id": CUSTOMERS, "status": "error", "compiled_code": _compiled_text(CUSTOMERS)}
    ]
    run_root, digests = _run_root(
        tmp_path, nodes=nodes, parent_map=parent_map, results=results, compiled_files=files,
        published_invocation="another-invocation",
    )
    tools = _tools(run_root, digests)

    with pytest.raises(EvidenceIntegrityError):
        tools.get_dbt_node_definition(CUSTOMERS)


def test_definitions_reject_disagreeing_sources(tmp_path: Path) -> None:
    nodes, parent_map, results, files = _node_graph()
    nodes[CUSTOMERS] = {
        **nodes[CUSTOMERS],
        "manifest_text": _compiled_text(CUSTOMERS),
        "file": "customers.sql",
    }
    files = {"customers.sql": "select * from something_else"}
    results = [
        {"unique_id": CUSTOMERS, "status": "error", "compiled_code": _compiled_text(CUSTOMERS)}
    ]
    run_root, digests = _run_root(
        tmp_path, nodes=nodes, parent_map=parent_map, results=results, compiled_files=files
    )
    tools = _tools(run_root, digests)

    with pytest.raises(EvidenceIntegrityError):
        tools.get_dbt_node_definition(CUSTOMERS)


def test_oversized_definitions_are_truncated_and_never_complete(tmp_path: Path) -> None:
    big = "-- " + "x" * (MAX_COMPILED_SQL_BYTES + 100)
    nodes = {CUSTOMERS: {"depends_on": [], "manifest_text": big}}
    parent_map = {CUSTOMERS: []}
    results = [
        {"unique_id": CUSTOMERS, "status": "error", "compiled_code": big}
    ]
    run_root, digests = _run_root(
        tmp_path, nodes=nodes, parent_map=parent_map, results=results, compiled_files={}
    )
    tools = _tools(run_root, digests)

    content = tools.get_dbt_node_definition(CUSTOMERS)[0].content

    assert isinstance(content, DbtNodeDefinitionFact)
    assert content.known is True
    assert content.complete is False
    assert content.compiled_sql is not None
    assert len(content.compiled_sql.encode("utf-8")) == MAX_COMPILED_SQL_BYTES
    assert content.compiled_sql_sha256 == hashlib.sha256(big.encode("utf-8")).hexdigest()


# -- session wiring: refusals reach the receipt with per-target detail -------


class _BatchBackend:
    """Minimal backend exposing only the two v2 batch tools."""

    def __init__(self, *, refusals: tuple[tuple[str, str], ...] = ()) -> None:
        self.refusals = refusals

    def get_relation_schema_expectation(self, relation_names: str):
        raise BatchTargetsRefusedError(
            "refused", target_refusals=self.refusals
        )

    def get_dbt_node_definition(self, node_ids: str):
        raise AssertionError("not used")


def test_a_batch_refusal_reaches_the_receipt_with_its_per_target_detail() -> None:
    from tests.unit.test_strategy_adapter import PROTOCOL_TOOL_ALLOWLIST, _request, _session

    v2_allowlist = frozenset(
        {*PROTOCOL_TOOL_ALLOWLIST, "get_relation_schema_expectation", "get_dbt_node_definition"}
    )
    session = _session(
        _BatchBackend(refusals=(("raw_orders", "RELATION_NOT_ALLOWED"),)),
        allowlist=v2_allowlist,
    )

    receipt = session.call_tool(
        _request("get_relation_schema_expectation", relation_names="raw_customers,raw_orders")
    )

    assert receipt.accepted is False
    assert receipt.error is not None and receipt.error.code == "TARGETS_REFUSED"
    assert [(item.target, item.code) for item in receipt.target_refusals] == [
        ("raw_orders", "RELATION_NOT_ALLOWED")
    ]


def test_a_v1_session_never_grants_the_v2_tools() -> None:
    from tests.unit.test_strategy_adapter import _request, _session

    session = _session()

    receipt = session.call_tool(
        _request("get_relation_schema_expectation", relation_names="raw_customers")
    )

    assert receipt.accepted is False
    assert receipt.error is not None and receipt.error.code == "TOOL_NOT_ALLOWLISTED"
    assert receipt.target_refusals == ()


def test_batch_arguments_must_use_the_frozen_string_encoding() -> None:
    from tests.unit.test_strategy_adapter import PROTOCOL_TOOL_ALLOWLIST, _session

    v2_allowlist = frozenset(
        {*PROTOCOL_TOOL_ALLOWLIST, "get_relation_schema_expectation"}
    )
    session = _session(_BatchBackend(), allowlist=v2_allowlist)

    receipt = session.call_tool(
        ToolRequest(
            request_id="req-list",
            tool_name="get_relation_schema_expectation",
            arguments={"relation_names": ["raw_customers"]},
        )
    )

    assert receipt.accepted is False
    assert receipt.error is not None and receipt.error.code == "TOOL_ARGUMENT_INVALID"


# -- lab writer: snapshot cutting and run binding (offline) ------------------


def _lab(tmp_path: Path):
    from data_incident_gym.config import Settings
    from data_incident_gym.lab import IncidentLab

    return IncidentLab(Settings(_env_file=None), tmp_path)


def _v2_spec(*, expectation: list[str], definition: list[str] | None = None):
    payload = json.loads(
        Path("config/scenarios/type_change_payment_amount_drift_b.json").read_text(
            encoding="utf-8"
        )
    )
    if definition is None:
        definition = [CUSTOMERS]
    contract = payload["observable_evidence_contract"]
    contract["schema_version"] = "observable_evidence.v2"
    # Expectations must stay within the observable schema relations.
    contract["schema_relations"] = ["raw_customers", "raw_orders"]
    contract["expectation_relations"] = expectation
    contract["definition_nodes"] = definition
    from data_incident_gym.scenarios import ScenarioSpec

    return ScenarioSpec.model_validate(payload)


def _write_trusted_baseline(project_root: Path) -> str:
    from data_incident_gym.baseline import (
        ColumnSummary,
        RelationSummary,
        make_baseline_summary,
    )

    relations = (
        RelationSummary(
            name="raw_customers",
            row_count=100,
            columns=(
                ColumnSummary("id", "integer", True, 1),
                ColumnSummary("first_name", "text", True, 2),
            ),
        ),
        RelationSummary(
            name="raw_orders",
            row_count=99,
            columns=(ColumnSummary("id", "integer", True, 1),),
        ),
    )
    summary = make_baseline_summary("analytics", relations)
    dig = project_root / ".dig"
    dig.mkdir(parents=True, exist_ok=True)
    (dig / "baseline-summary.json").write_text(summary.to_json(), encoding="utf-8")
    return summary.fingerprint


def test_the_evidence_baseline_snapshot_is_cut_and_bound(tmp_path: Path) -> None:
    fingerprint = _write_trusted_baseline(tmp_path)
    spec = _v2_spec(expectation=["raw_customers"])

    _lab(tmp_path)._write_evidence_baseline(tmp_path, spec)

    payload = json.loads(
        (tmp_path / EVIDENCE_BASELINE_FILENAME).read_text(encoding="utf-8")
    )
    assert payload["baseline_fingerprint"] == fingerprint
    assert [relation["name"] for relation in payload["relations"]] == ["raw_customers"]
    assert payload["relations"][0]["columns"][0] == {
        "name": "id",
        "expected_data_type": "integer",
        "expected_nullable": True,
        "ordinal_position": 1,
    }


def test_a_tampered_trusted_baseline_is_refused_at_build(tmp_path: Path) -> None:
    _write_trusted_baseline(tmp_path)
    path = tmp_path / ".dig" / "baseline-summary.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["fingerprint"] = "f" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Exception) as error:
        _lab(tmp_path)._write_evidence_baseline(tmp_path, _v2_spec(expectation=[]))

    assert "健康基线指纹不一致" in str(error.value)


def test_the_v2_runtime_records_build_provenance_over_final_bytes(tmp_path: Path) -> None:
    _write_trusted_baseline(tmp_path)
    spec = _v2_spec(expectation=["raw_customers"])
    lab = _lab(tmp_path)
    lab._write_evidence_baseline(tmp_path, spec)
    _run_root(
        tmp_path,
        nodes={CUSTOMERS: {"depends_on": [], "manifest_text": "select 1"}},
        parent_map={CUSTOMERS: []},
        results=[{"unique_id": CUSTOMERS, "status": "error", "compiled_code": "select 1"}],
        compiled_files={},
    )

    lab._write_runtime_v2(
        tmp_path,
        spec,
        RUN_ID,
        1,
        "b" * 64,
        original_texts=lab._definition_texts(tmp_path),
    )

    runtime = json.loads((tmp_path / "runtime.json").read_text(encoding="utf-8"))
    assert runtime["schema_version"] == RUNTIME_V2_SCHEMA_VERSION
    assert runtime["observable_relations"]["expectation"] == ["raw_customers"]
    assert runtime["observable_nodes"]["definition"] == [CUSTOMERS]
    provenance = runtime["build_provenance"]
    assert provenance["dbt_invocation_id"] == INVOCATION
    assert provenance["artifact_sha256"]["manifest"] == hashlib.sha256(
        (tmp_path / "dbt" / "target" / "manifest.json").read_bytes()
    ).hexdigest()
    assert (
        runtime["evidence_baseline"]["sha256"]
        == hashlib.sha256((tmp_path / EVIDENCE_BASELINE_FILENAME).read_bytes()).hexdigest()
    )
    # The record must survive its own validator, i.e. it is a valid run context.
    _validate_runtime(runtime, RUN_ID)


def test_the_v2_runtime_refuses_artifacts_that_disagree(tmp_path: Path) -> None:
    _write_trusted_baseline(tmp_path)
    spec = _v2_spec(expectation=[])
    lab = _lab(tmp_path)
    lab._write_evidence_baseline(tmp_path, spec)
    _run_root(
        tmp_path,
        nodes={CUSTOMERS: {"depends_on": [], "manifest_text": "select 1"}},
        parent_map={CUSTOMERS: []},
        results=[{"unique_id": CUSTOMERS, "status": "error", "compiled_code": "select 2"}],
        compiled_files={},
    )

    with pytest.raises(Exception) as error:
        lab._write_runtime_v2(
        tmp_path,
        spec,
        RUN_ID,
        1,
        "b" * 64,
        original_texts=lab._definition_texts(tmp_path),
    )

    assert "运行产物自相矛盾" in str(error.value)

# -- audit round: redaction change and byte-exact truncation -----------------

SENTINEL = "AUDIT_SECRET_SENTINEL"


def _artifacts(run_root: Path, context: ObservableRunContext) -> SimpleNamespace:
    target = run_root / "dbt" / "target"
    return SimpleNamespace(
        context=context,
        run_root=run_root,
        manifest=json.loads((target / "manifest.json").read_text(encoding="utf-8")),
        run_results=json.loads((target / "run_results.json").read_text(encoding="utf-8")),
        manifest_generated_at=datetime(2026, 9, 18, tzinfo=UTC),
        read_json=lambda path: json.loads(Path(path).read_text(encoding="utf-8")),
    )


def test_a_redaction_changed_definition_is_never_complete(tmp_path: Path) -> None:
    """Audit reproduction: a password-bearing definition was redacted in the
    archived copies yet still reported ``complete=True``."""

    from data_incident_gym.config import PROJECT_ROOT, Settings
    from data_incident_gym.lab import IncidentLab

    settings = Settings(_env_file=None, postgres_password=SENTINEL)
    original = f"select '{SENTINEL}' as customer_id"
    nodes = {
        CUSTOMERS: {
            "depends_on": [STG_CUSTOMERS],
            "manifest_text": original,
            "file": "customers.sql",
        },
        STG_CUSTOMERS: {"depends_on": [], "manifest_text": _compiled_text(STG_CUSTOMERS)},
    }
    parent_map = {CUSTOMERS: [STG_CUSTOMERS], STG_CUSTOMERS: []}
    results = [{"unique_id": CUSTOMERS, "status": "error", "compiled_code": original}]
    run_root, _ = _run_root(
        tmp_path,
        nodes=nodes,
        parent_map=parent_map,
        results=results,
        compiled_files={"customers.sql": original},
    )
    lab = IncidentLab(settings, PROJECT_ROOT)

    original_texts = lab._definition_texts(run_root)
    lab._redact_compiled_tree(run_root)
    for artifact in (
        run_root / "dbt" / "target" / "manifest.json",
        run_root / "dbt" / "target" / "run_results.json",
    ):
        lab._redact_file(artifact)
    spec = _v2_spec(expectation=[])
    lab._write_runtime_v2(
        run_root, spec, RUN_ID, 1, "b" * 64, original_texts=original_texts
    )

    compiled_file = run_root / "dbt" / "target" / "compiled" / "customers.sql"
    assert SENTINEL not in compiled_file.read_text(encoding="utf-8")
    runtime = json.loads((run_root / "runtime.json").read_text(encoding="utf-8"))
    definitions = runtime["build_provenance"]["node_definitions"]
    assert definitions[CUSTOMERS]["redacted"] is True
    assert definitions[STG_CUSTOMERS]["redacted"] is False

    def _digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    context = _context(
        run_root,
        {
            "baseline": _digest(run_root / EVIDENCE_BASELINE_FILENAME),
            "manifest": _digest(run_root / "dbt" / "target" / "manifest.json"),
            "run_results": _digest(run_root / "dbt" / "target" / "run_results.json"),
            "compiled_tree": compiled_tree_digest(run_root / "dbt" / "target" / "compiled"),
            "node_definitions": definitions,
        },
        definition=(CUSTOMERS, STG_CUSTOMERS),
    )
    tools = BatchEvidenceTools(RUN_ID, _artifacts(run_root, context))

    contents = tools.get_dbt_node_definition(f"{CUSTOMERS},{STG_CUSTOMERS}")

    redacted = contents[0].content
    assert isinstance(redacted, DbtNodeDefinitionFact)
    assert redacted.known is True
    assert redacted.complete is False
    assert redacted.compiled_sql is not None and "***" in redacted.compiled_sql
    untouched = contents[1].content
    assert isinstance(untouched, DbtNodeDefinitionFact)
    assert untouched.complete is True


def test_oversized_non_ascii_definitions_stay_within_the_byte_cap(
    tmp_path: Path,
) -> None:
    """Audit reproduction: a character slice returned ~49 KB for a 16 KiB cap."""

    comment = "注释" * 100  # 600 UTF-8 bytes per repetition
    big = "-- " + comment * 100 + "select 1"
    assert len(big.encode("utf-8")) > MAX_COMPILED_SQL_BYTES
    nodes = {
        CUSTOMERS: {
            "depends_on": [],
            "manifest_text": big,
            "file": "customers.sql",
        }
    }
    run_root, digests = _run_root(
        tmp_path,
        nodes=nodes,
        parent_map={CUSTOMERS: []},
        results=[{"unique_id": CUSTOMERS, "status": "error", "compiled_code": big}],
        compiled_files={"customers.sql": big},
    )
    tools = _tools(run_root, digests, definition=(CUSTOMERS,))

    content = tools.get_dbt_node_definition(CUSTOMERS)[0].content

    assert isinstance(content, DbtNodeDefinitionFact)
    assert content.complete is False
    assert content.compiled_sql is not None
    returned = content.compiled_sql
    assert len(returned.encode("utf-8")) <= MAX_COMPILED_SQL_BYTES
    assert returned == returned.encode("utf-8").decode("utf-8")
    assert returned.endswith("\ufffd") is False


# -- audit round 2: the build record and the public whitelist are separate --


def _written_run(tmp_path: Path, spec, nodes: dict, parent_map: dict, results: list, files):
    """Write a run, produce the v2 runtime, and load it back through the
    validator — the exact write→load path of a real build."""

    from data_incident_gym.config import PROJECT_ROOT, Settings
    from data_incident_gym.lab import IncidentLab

    run_root, _ = _run_root(
        tmp_path,
        nodes=nodes,
        parent_map=parent_map,
        results=results,
        compiled_files=files,
    )
    lab = IncidentLab(Settings(_env_file=None), PROJECT_ROOT)
    lab._write_runtime_v2(
        run_root,
        spec,
        RUN_ID,
        1,
        "b" * 64,
        original_texts=lab._definition_texts(run_root),
    )
    runtime = _validate_runtime(
        json.loads((run_root / "runtime.json").read_text(encoding="utf-8")), RUN_ID
    )
    context = ObservableRunContext(
        RUN_ID, run_root, runtime, SimpleNamespace()  # type: ignore[arg-type]
    )
    return run_root, BatchEvidenceTools(RUN_ID, _artifacts(run_root, context))


def test_the_build_record_may_cover_nodes_outside_the_public_whitelist(
    tmp_path: Path,
) -> None:
    """Audit reproduction: two compiled nodes, only one granted, used to fail
    the runtime's own validation."""

    nodes = {
        CUSTOMERS: {
            "depends_on": [STG_CUSTOMERS],
            "manifest_text": _compiled_text(CUSTOMERS),
            "file": "customers.sql",
        },
        STG_CUSTOMERS: {"depends_on": [], "manifest_text": _compiled_text(STG_CUSTOMERS)},
    }
    parent_map = {CUSTOMERS: [STG_CUSTOMERS], STG_CUSTOMERS: []}
    results = [
        {"unique_id": CUSTOMERS, "status": "error", "compiled_code": _compiled_text(CUSTOMERS)}
    ]
    run_root, tools = _written_run(
        tmp_path,
        _v2_spec(expectation=[], definition=[CUSTOMERS]),
        nodes,
        parent_map,
        results,
        {"customers.sql": _compiled_text(CUSTOMERS)},
    )

    runtime = json.loads((run_root / "runtime.json").read_text(encoding="utf-8"))
    # The record covers both compiled nodes; the whitelist grants one.
    assert set(runtime["build_provenance"]["node_definitions"]) == {
        CUSTOMERS,
        STG_CUSTOMERS,
    }
    assert runtime["observable_nodes"]["definition"] == [CUSTOMERS]

    granted = tools.get_dbt_node_definition(CUSTOMERS)[0].content
    assert isinstance(granted, DbtNodeDefinitionFact)
    assert granted.complete is True
    with pytest.raises(BatchTargetsRefusedError) as error:
        tools.get_dbt_node_definition(STG_CUSTOMERS)
    assert error.value.target_refusals == ((STG_CUSTOMERS, "NODE_NOT_ALLOWED"),)


def test_an_empty_definition_whitelist_still_produces_a_valid_runtime(
    tmp_path: Path,
) -> None:
    """Audit reproduction: the B-variant shape (nothing granted) used to fail
    the runtime's own validation."""

    nodes = {
        CUSTOMERS: {
            "depends_on": [],
            "manifest_text": _compiled_text(CUSTOMERS),
            "file": "customers.sql",
        }
    }
    results = [
        {"unique_id": CUSTOMERS, "status": "error", "compiled_code": _compiled_text(CUSTOMERS)}
    ]
    run_root, tools = _written_run(
        tmp_path,
        _v2_spec(expectation=[], definition=[]),
        nodes,
        {CUSTOMERS: []},
        results,
        {"customers.sql": _compiled_text(CUSTOMERS)},
    )

    runtime = json.loads((run_root / "runtime.json").read_text(encoding="utf-8"))
    assert runtime["observable_nodes"]["definition"] == []
    assert set(runtime["build_provenance"]["node_definitions"]) == {CUSTOMERS}

    with pytest.raises(BatchTargetsRefusedError) as error:
        tools.get_dbt_node_definition(CUSTOMERS)
    assert error.value.target_refusals == ((CUSTOMERS, "NODE_NOT_ALLOWED"),)


# -- dry run finding: dbt writes CRLF compiled files on Windows --------------


CRLF_BODY = "with source as (\n    select * from seed\n)\n\nselect * from source\n"


def test_a_crlf_compiled_file_agrees_with_its_lf_json_copies(tmp_path: Path) -> None:
    """Dry-run reproduction: dbt writes the compiled file with CRLF while the
    manifest and run_results copies of the same SQL keep LF. The raw string
    comparison refused the whole build as self-contradictory."""

    lf = CRLF_BODY
    crlf = lf.replace("\n", "\r\n")
    run_root, _ = _run_root(
        tmp_path,
        nodes={CUSTOMERS: {"depends_on": [], "manifest_text": lf, "file": "customers.sql"}},
        parent_map={CUSTOMERS: []},
        results=[{"unique_id": CUSTOMERS, "status": "error", "compiled_code": lf}],
        compiled_files={"customers.sql": crlf},
    )
    lab = _lab(tmp_path)

    texts = lab._definition_texts(run_root)

    # Precedence picks run_results, and the file is not treated as a conflict.
    assert texts[CUSTOMERS] == lf


def test_a_genuinely_different_compiled_file_is_still_refused(tmp_path: Path) -> None:
    """The canonical comparison must not weaken the integrity check."""

    run_root, _ = _run_root(
        tmp_path,
        nodes={
            CUSTOMERS: {
                "depends_on": [],
                "manifest_text": CRLF_BODY,
                "file": "customers.sql",
            }
        },
        parent_map={CUSTOMERS: []},
        results=[
            {"unique_id": CUSTOMERS, "status": "error", "compiled_code": CRLF_BODY}
        ],
        compiled_files={"customers.sql": CRLF_BODY + "\n-- trailing difference\n"},
    )

    with pytest.raises(IncidentExecutionError) as error:
        _lab(tmp_path)._definition_texts(run_root)

    assert "运行产物自相矛盾" in str(error.value)


def test_a_crlf_run_loads_and_serves_its_definition(tmp_path: Path) -> None:
    """Write→load: a CRLF compiled file must survive the whole v2 path and the
    served definition must be the canonical (run_results) text."""

    _write_trusted_baseline(tmp_path)
    spec = _v2_spec(expectation=[])
    lab = _lab(tmp_path)
    lab._write_evidence_baseline(tmp_path, spec)
    lf = CRLF_BODY
    run_root, _ = _run_root(
        tmp_path,
        nodes={CUSTOMERS: {"depends_on": [], "manifest_text": lf, "file": "customers.sql"}},
        parent_map={CUSTOMERS: []},
        results=[{"unique_id": CUSTOMERS, "status": "error", "compiled_code": lf}],
        compiled_files={"customers.sql": lf.replace("\n", "\r\n")},
    )
    lab._write_runtime_v2(
        tmp_path,
        spec,
        RUN_ID,
        1,
        "b" * 64,
        original_texts=lab._definition_texts(run_root),
    )
    runtime = _validate_runtime(
        json.loads((tmp_path / "runtime.json").read_text(encoding="utf-8")), RUN_ID
    )
    context = ObservableRunContext(
        RUN_ID, tmp_path, runtime, SimpleNamespace()  # type: ignore[arg-type]
    )
    tools = BatchEvidenceTools(RUN_ID, _artifacts(tmp_path, context))

    content = tools.get_dbt_node_definition(CUSTOMERS)[0].content

    assert isinstance(content, DbtNodeDefinitionFact)
    assert content.known is True
    assert content.complete is True
    assert content.compiled_sql == lf


# -- slice 4: the public identity bridge (design §4.1 wiring requirement) ----


def test_the_definition_fact_carries_the_public_identity_bridge(tmp_path: Path) -> None:
    """E2 publishes the node name and the identity its SQL writes, taken from
    the manifest — so a reader origin can be matched to a whitelisted relation
    explicitly, never by name similarity."""

    run_root, digests = _run_root(
        tmp_path,
        nodes={
            CUSTOMERS: {
                "depends_on": ["seed.jaffle_shop.raw_customers"],
                "manifest_text": "select 1",
                "file": "customers.sql",
                "name": "customers",
                "relation_name": '"data_incident_gym"."analytics"."customers"',
            }
        },
        parent_map={CUSTOMERS: ["seed.jaffle_shop.raw_customers"]},
        results=[{"unique_id": CUSTOMERS, "status": "error", "compiled_code": "select 1"}],
        compiled_files={"customers.sql": "select 1"},
    )
    tools = _tools(run_root, digests, definition=(CUSTOMERS,))

    content = tools.get_dbt_node_definition(CUSTOMERS)[0].content

    assert isinstance(content, DbtNodeDefinitionFact)
    assert content.name == "customers"
    assert content.relation_identity == "data_incident_gym.analytics.customers"


def test_a_quoted_relation_name_keeps_its_identity_in_the_fact(tmp_path: Path) -> None:
    """The identity the fact publishes is the one the reader reports: quoting
    and case are preserved, so distinct identifiers never collapse."""

    run_root, digests = _run_root(
        tmp_path,
        nodes={
            CUSTOMERS: {
                "depends_on": [],
                "manifest_text": "select 1",
                "file": "customers.sql",
                "name": "customers",
                "relation_name": '"analytics"."STG_CUSTOMERS"',
            }
        },
        parent_map={CUSTOMERS: []},
        results=[{"unique_id": CUSTOMERS, "status": "error", "compiled_code": "select 1"}],
        compiled_files={"customers.sql": "select 1"},
    )
    tools = _tools(run_root, digests, definition=(CUSTOMERS,))

    content = tools.get_dbt_node_definition(CUSTOMERS)[0].content

    assert isinstance(content, DbtNodeDefinitionFact)
    assert content.relation_identity == 'analytics."STG_CUSTOMERS"'


def test_a_test_node_carries_no_relation_identity(tmp_path: Path) -> None:
    """Tests have no relation of their own: the bridge must stay empty rather
    than name something that does not exist."""

    run_root, digests = _run_root(
        tmp_path,
        nodes={CUSTOMERS: {"depends_on": [], "manifest_text": "select 1"}},
        parent_map={CUSTOMERS: []},
        results=[{"unique_id": CUSTOMERS, "status": "error", "compiled_code": "select 1"}],
        compiled_files={},
    )
    tools = _tools(run_root, digests, definition=(CUSTOMERS,))

    content = tools.get_dbt_node_definition(CUSTOMERS)[0].content

    assert isinstance(content, DbtNodeDefinitionFact)
    assert content.name is None
    assert content.relation_identity is None


def test_the_expectation_fact_carries_the_bridge_when_the_snapshot_has_it(
    tmp_path: Path,
) -> None:
    run_root, digests = _run_root(
        tmp_path,
        nodes={CUSTOMERS: {"depends_on": [], "manifest_text": "select 1"}},
        parent_map={CUSTOMERS: []},
        results=[{"unique_id": CUSTOMERS, "status": "error", "compiled_code": "select 1"}],
        compiled_files={},
        baseline_relations=[
            {
                "name": "raw_customers",
                "relation_identity": "data_incident_gym.analytics.raw_customers",
                "resource_type": "seed",
                "columns": [
                    {
                        "name": "id",
                        "expected_data_type": "integer",
                        "expected_nullable": True,
                        "ordinal_position": 1,
                    }
                ],
            }
        ],
    )
    tools = _tools(run_root, digests, expectation=("raw_customers",), definition=())

    content = tools.get_relation_schema_expectation("raw_customers")[0].content

    assert isinstance(content, RelationSchemaExpectationFact)
    assert content.relation_identity == "data_incident_gym.analytics.raw_customers"
    assert content.resource_type == "seed"


def test_a_snapshot_without_the_bridge_stays_readable(tmp_path: Path) -> None:
    """Backward compatibility: a snapshot written before the bridge existed
    loads, and its facts carry no identity (callers must then abstain)."""

    run_root, digests = _run_root(
        tmp_path,
        nodes={CUSTOMERS: {"depends_on": [], "manifest_text": "select 1"}},
        parent_map={CUSTOMERS: []},
        results=[{"unique_id": CUSTOMERS, "status": "error", "compiled_code": "select 1"}],
        compiled_files={},
    )
    tools = _tools(run_root, digests, expectation=("raw_customers",), definition=())

    content = tools.get_relation_schema_expectation("raw_customers")[0].content

    assert isinstance(content, RelationSchemaExpectationFact)
    assert content.known is True
    assert content.relation_identity is None
    assert content.resource_type is None


def test_the_build_writes_the_bridge_into_the_snapshot(tmp_path: Path) -> None:
    """The writer takes the bridge from this run's manifest (public metadata)."""

    _write_trusted_baseline(tmp_path)
    spec = _v2_spec(expectation=["raw_customers", "raw_orders"])
    _run_root(
        tmp_path,
        nodes={
            "seed.jaffle_shop.raw_customers": {
                "depends_on": [],
                "manifest_text": None,
                "resource_type": "seed",
                "name": "raw_customers",
                "relation_name": '"data_incident_gym"."analytics"."raw_customers"',
            },
            "seed.jaffle_shop.raw_orders": {
                "depends_on": [],
                "manifest_text": None,
                "resource_type": "seed",
                "name": "raw_orders",
                "relation_name": '"data_incident_gym"."analytics"."raw_orders"',
            },
        },
        parent_map={},
        results=[],
        compiled_files={},
    )

    _lab(tmp_path)._write_evidence_baseline(tmp_path, spec)

    payload = json.loads((tmp_path / EVIDENCE_BASELINE_FILENAME).read_text(encoding="utf-8"))
    entries = {entry["name"]: entry for entry in payload["relations"]}
    assert entries["raw_customers"]["relation_identity"] == (
        "data_incident_gym.analytics.raw_customers"
    )
    assert entries["raw_customers"]["resource_type"] == "seed"
    assert entries["raw_orders"]["relation_identity"] == (
        "data_incident_gym.analytics.raw_orders"
    )
