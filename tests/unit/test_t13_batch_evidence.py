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
                "resource_type": "model",
                "columns": {name: {} for name in node.get("columns", ())},
                "depends_on": {"nodes": node.get("depends_on", [])},
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
        path.write_text(text, encoding="utf-8")
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

    digests = {
        "baseline": _digest(tmp_path / EVIDENCE_BASELINE_FILENAME),
        "manifest": _digest(target / "manifest.json"),
        "run_results": _digest(target / "run_results.json"),
        "compiled_tree": compiled_tree_digest(compiled_root),
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
            "compiled_code": _compiled_text(CUSTOMERS),
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


def _v2_spec(*, expectation: list[str]):
    payload = json.loads(
        Path("config/scenarios/type_change_payment_amount_drift_b.json").read_text(
            encoding="utf-8"
        )
    )
    contract = payload["observable_evidence_contract"]
    contract["schema_version"] = "observable_evidence.v2"
    # Expectations must stay within the observable schema relations.
    contract["schema_relations"] = ["raw_customers", "raw_orders"]
    contract["expectation_relations"] = expectation
    contract["definition_nodes"] = [CUSTOMERS]
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

    lab._write_runtime_v2(tmp_path, spec, RUN_ID, 1, "b" * 64)

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
        lab._write_runtime_v2(tmp_path, spec, RUN_ID, 1, "b" * 64)

    assert "运行产物自相矛盾" in str(error.value)
