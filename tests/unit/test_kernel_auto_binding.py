"""Regression tests for the controller-managed kernel gap binding protocol.

The controller derives the gap kind from the business tool, allocates
``g_auto_N`` identifiers per run, and no longer exposes ``kernel_gap_id`` or
``kernel_gap_kind`` in the model-visible tool schemas. These tests pin that
protocol without contacting a model service.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from data_incident_gym.diagnosis import (
    DiagnosisStatus,
    DiagnosticStrategy,
)
from data_incident_gym.diagnostic_agent import (
    TOOL_NAMES,
    DiagnosisRunner,
    ModelIdentity,
    _kernel_state_summary,
    policy_surface_for_strategy,
)
from data_incident_gym.diagnostic_contracts import (
    EvidenceGapKind,
    InvestigationIntent,
    KernelError,
    gap_kind_for_tool,
)
from data_incident_gym.diagnostic_kernel import DiagnosticKernel
from data_incident_gym.evidence import (
    DbtLineageFact,
    DbtLineageNode,
    DbtNodeErrorFact,
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
    RelationSchemaColumn,
    RelationSchemaFact,
)
from data_incident_gym.run_context import IncidentBrief

RUN_ID = "a" * 32
MODEL_BASE_URL = "http://127.0.0.1:11434/v1"


def test_gap_kind_for_tool_maps_all_seven_tool_shapes() -> None:
    assert gap_kind_for_tool("get_dbt_run_results", {"run_id": RUN_ID}) is (
        EvidenceGapKind.LOCATE_FAILURE
    )
    assert gap_kind_for_tool(
        "get_dbt_node_error",
        {"run_id": RUN_ID, "node_id": "model.jaffle_shop.stg_payments"},
    ) is EvidenceGapKind.EXPLAIN_FAILURE
    assert gap_kind_for_tool(
        "get_dbt_lineage",
        {"node_id": "model.jaffle_shop.stg_payments", "direction": "upstream"},
    ) is EvidenceGapKind.DISCOVER_SOURCE_RELATION
    assert gap_kind_for_tool(
        "get_dbt_lineage",
        {"node_id": "model.jaffle_shop.stg_payments", "direction": "downstream"},
    ) is EvidenceGapKind.MAP_IMPACT
    assert gap_kind_for_tool("get_relation_schema", {"relation_name": "raw_payments"}) is (
        EvidenceGapKind.DISCRIMINATE_SCHEMA
    )
    assert gap_kind_for_tool(
        "get_relation_data_profile",
        {"relation_name": "raw_payments"},
    ) is EvidenceGapKind.PROFILE_RELATION
    assert gap_kind_for_tool(
        "get_relation_history",
        {"relation_name": "raw_orders"},
    ) is EvidenceGapKind.COMPARE_HISTORY


def test_gap_kind_for_tool_rejects_unknown_tool_and_missing_direction() -> None:
    with pytest.raises(KernelError, match="GAP_TOOL_MISMATCH"):
        gap_kind_for_tool("get_free_sql", {})
    with pytest.raises(KernelError, match="GAP_TOOL_MISMATCH"):
        gap_kind_for_tool("get_dbt_lineage", {"node_id": "model.jaffle_shop.stg_payments"})
    with pytest.raises(KernelError, match="GAP_TOOL_MISMATCH"):
        gap_kind_for_tool(
            "get_dbt_lineage",
            {"node_id": "model.jaffle_shop.stg_payments", "direction": "sideways"},
        )


def test_kernel_tool_schemas_drop_auto_fields_and_keep_hypothesis_arguments() -> None:
    surface = policy_surface_for_strategy(DiagnosticStrategy.DIAGNOSTIC_KERNEL)
    schemas = {
        item["name"]: item["parameters"] for item in surface.tool_schema_payload
    }

    assert set(schemas) == set(TOOL_NAMES)
    business_arguments = {
        "get_dbt_run_results": {"run_id"},
        "get_dbt_node_error": {"run_id", "node_id"},
        "get_relation_schema": {"relation_name"},
        "get_dbt_lineage": {"node_id", "direction"},
        "get_relation_data_profile": {"relation_name"},
        "get_relation_history": {"relation_name"},
    }
    for tool_name, parameters in schemas.items():
        properties = set(parameters["properties"])
        required = set(parameters["required"])
        assert "kernel_gap_id" not in properties, tool_name
        assert "kernel_gap_kind" not in properties, tool_name
        assert "kernel_gap_id" not in required, tool_name
        assert "kernel_gap_kind" not in required, tool_name
        assert "kernel_hypothesis_ids" in properties, tool_name
        assert "kernel_new_hypotheses" in properties, tool_name
        assert business_arguments[tool_name] <= required, tool_name
        assert required <= business_arguments[tool_name], tool_name


def _write_public_context(project_root: Path) -> None:
    run_root = project_root / ".dig" / "lab" / "runs" / RUN_ID
    run_root.mkdir(parents=True)
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
            "schema": ["raw_payments"],
            "profile": ["raw_payments"],
            "history": ["raw_orders"],
        },
        "profile_spec_sha256": "b" * 64,
    }
    (run_root / "runtime.json").write_text(json.dumps(runtime), encoding="utf-8")
    brief = IncidentBrief(
        schema_version="incident_brief.v1",
        signal_code="DBT_BUILD_FAILED",
        summary="A dbt model build failed.",
        subjects=("model.jaffle_shop.stg_payments",),
        logical_observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        observations=(),
    )
    (run_root / "incident_brief.json").write_text(brief.model_dump_json(), encoding="utf-8")


class _KernelEvidenceTools:
    def __init__(self) -> None:
        self._records = _kernel_evidence()

    def get_dbt_run_results(self, _run_id: str):
        return (self._records[0],)

    def get_dbt_node_error(self, _run_id: str, _node_id: str):
        return (self._records[1],)

    def get_dbt_lineage(self, _node_id: str, direction: str):
        return (self._records[2 if direction == "upstream" else 4],)

    def get_relation_schema(self, _relation_name: str):
        return (self._records[3],)

    def get_relation_data_profile(self, _relation_name: str):
        return ()

    def get_relation_history(self, _relation_name: str):
        return ()


def _kernel_evidence() -> tuple[EvidenceRecord, ...]:
    observed_at = datetime(2026, 8, 30, tzinfo=UTC)
    run_results = EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_RUN_RESULTS,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject=RUN_ID,
        observed_at=observed_at,
        content=DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=RUN_ID,
            run_status="FAILED",
            dbt_exit_code=1,
            failed_nodes=("model.jaffle_shop.stg_payments",),
            skipped_nodes=("model.jaffle_shop.orders", "model.jaffle_shop.customers"),
        ),
    )
    node_error = EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_NODE_ERROR,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject="model.jaffle_shop.stg_payments",
        observed_at=observed_at,
        content=DbtNodeErrorFact(
            kind="DBT_NODE_ERROR",
            run_id=RUN_ID,
            node_id="model.jaffle_shop.stg_payments",
            resource_type="model",
            status="error",
            message='column "amount" has type text',
        ),
    )
    upstream = EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_LINEAGE,
        source=EvidenceSource.DBT_MANIFEST,
        subject="model.jaffle_shop.stg_payments",
        observed_at=observed_at,
        content=DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=RUN_ID,
            node_id="model.jaffle_shop.stg_payments",
            direction="upstream",
            related_nodes=(
                DbtLineageNode(
                    node_id="seed.jaffle_shop.raw_payments",
                    resource_type="seed",
                    name="raw_payments",
                    distance=1,
                ),
            ),
        ),
    )
    schema = EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.RELATION_SCHEMA,
        source=EvidenceSource.POSTGRES_CATALOG,
        subject="analytics.raw_payments",
        observed_at=observed_at,
        content=RelationSchemaFact(
            kind="RELATION_SCHEMA",
            run_id=RUN_ID,
            schema_name="analytics",
            relation_name="raw_payments",
            columns=(
                RelationSchemaColumn(
                    name="amount",
                    data_type="text",
                    nullable=True,
                    ordinal_position=4,
                ),
            ),
        ),
    )
    downstream = EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_LINEAGE,
        source=EvidenceSource.DBT_MANIFEST,
        subject="model.jaffle_shop.stg_payments",
        observed_at=observed_at,
        content=DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=RUN_ID,
            node_id="model.jaffle_shop.stg_payments",
            direction="downstream",
            related_nodes=(
                DbtLineageNode(
                    node_id="model.jaffle_shop.orders",
                    resource_type="model",
                    name="orders",
                    distance=1,
                ),
                DbtLineageNode(
                    node_id="model.jaffle_shop.customers",
                    resource_type="model",
                    name="customers",
                    distance=2,
                ),
            ),
        ),
    )
    return run_results, node_error, upstream, schema, downstream


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        model_base_url=MODEL_BASE_URL,
        model_name="synthetic-model",
        model_api_key=SimpleNamespace(get_secret_value=lambda: "synthetic-key"),
    )


def _kernel_runner(project_root: Path, model: FunctionModel) -> DiagnosisRunner:
    return DiagnosisRunner.for_run(
        RUN_ID,
        _settings(),
        DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        project_root,
        model=model,
        tools=_KernelEvidenceTools(),
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )


def _hypotheses_payload() -> dict[str, object]:
    return {
        "kernel_hypothesis_ids": ["h_rename", "h_type"],
        "kernel_new_hypotheses": [
            {"hypothesis_id": "h_rename", "root_cause_code": "SOURCE_SCHEMA_COLUMN_RENAMED"},
            {
                "hypothesis_id": "h_type",
                "root_cause_code": "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
            },
        ],
    }


def _insufficient_payload() -> dict[str, object]:
    return {
        "schema_version": "p1.kernel_decision.v1",
        "status": "INSUFFICIENT_EVIDENCE",
        "run_id": RUN_ID,
        "unresolved_evidence": [],
        "summary": "More evidence is required.",
        "recommended_actions": [],
        "confidence": 0.2,
    }


@pytest.mark.asyncio
async def test_auto_gap_ids_are_sequential_per_response_and_reset_per_run(
    tmp_path: Path,
) -> None:
    def scripted(
        messages: list[ModelMessage],
        agent_info: AgentInfo,
    ) -> ModelResponse:
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        if "call-0" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        {**{"run_id": RUN_ID}, **_hypotheses_payload()},
                        tool_call_id="call-0",
                    ),
                    ToolCallPart(
                        "get_dbt_node_error",
                        {
                            "run_id": RUN_ID,
                            "node_id": "model.jaffle_shop.stg_payments",
                            "kernel_hypothesis_ids": ["h_rename", "h_type"],
                        },
                        tool_call_id="call-1",
                    ),
                ]
            )
        if "call-2" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_relation_data_profile",
                        {
                            "relation_name": "raw_payments",
                            "kernel_hypothesis_ids": ["h_rename", "h_type"],
                        },
                        tool_call_id="call-2",
                    ),
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    _insufficient_payload(),
                    tool_call_id="final",
                )
            ]
        )

    _write_public_context(tmp_path)
    result = await _kernel_runner(tmp_path, FunctionModel(scripted)).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert [(gap.gap_id, gap.gap_kind.value) for gap in result.kernel_state.gaps] == [
        ("g_auto_1", "LOCATE_FAILURE"),
        ("g_auto_2", "EXPLAIN_FAILURE"),
        ("g_auto_3", "PROFILE_RELATION"),
    ]
    assert [gap.tool_name for gap in result.kernel_state.gaps] == [
        "get_dbt_run_results",
        "get_dbt_node_error",
        "get_relation_data_profile",
    ]
    assert [gap.subject for gap in result.kernel_state.gaps] == [
        RUN_ID,
        "model.jaffle_shop.stg_payments",
        "raw_payments",
    ]

    second_root = tmp_path / "second-run"
    second_root.mkdir()
    _write_public_context(second_root)
    second = await _kernel_runner(second_root, FunctionModel(scripted)).diagnose()
    assert [gap.gap_id for gap in second.kernel_state.gaps] == ["g_auto_1", "g_auto_2", "g_auto_3"]


@pytest.mark.asyncio
async def test_auto_gap_numbering_does_not_rewind_after_kernel_rejection(
    tmp_path: Path,
) -> None:
    def scripted(
        messages: list[ModelMessage],
        agent_info: AgentInfo,
    ) -> ModelResponse:
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        if "call-0" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        {**{"run_id": RUN_ID}, **_hypotheses_payload()},
                        tool_call_id="call-0",
                    ),
                    ToolCallPart(
                        "get_relation_schema",
                        {"relation_name": "raw_orders"},
                        tool_call_id="call-1",
                    ),
                ]
            )
        if "call-2" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_node_error",
                        {
                            "run_id": RUN_ID,
                            "node_id": "model.jaffle_shop.stg_payments",
                            "kernel_hypothesis_ids": ["h_rename", "h_type"],
                        },
                        tool_call_id="call-2",
                    ),
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    _insufficient_payload(),
                    tool_call_id="final",
                )
            ]
        )

    _write_public_context(tmp_path)
    result = await _kernel_runner(tmp_path, FunctionModel(scripted)).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert [
        (gap.gap_id, gap.status.value, gap.error_code) for gap in result.kernel_state.gaps
    ] == [
        ("g_auto_1", "CLOSED", None),
        ("g_auto_2", "BLOCKED", "RELATION_NOT_ALLOWED"),
        ("g_auto_3", "CLOSED", None),
    ]


@pytest.mark.asyncio
async def test_model_supplied_auto_fields_are_rejected_without_kernel_binding(
    tmp_path: Path,
) -> None:
    legacy_arguments = {
        "run_id": RUN_ID,
        "kernel_gap_id": "g_locate",
        "kernel_gap_kind": "LOCATE_FAILURE",
    }

    def scripted(
        messages: list[ModelMessage],
        agent_info: AgentInfo,
    ) -> ModelResponse:
        tool_returns = sum(
            isinstance(part, ToolReturnPart)
            for message in messages
            for part in message.parts
        )
        if tool_returns == 0:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        legacy_arguments,
                        tool_call_id="call-legacy",
                    ),
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    _insufficient_payload(),
                    tool_call_id="final",
                )
            ]
        )

    _write_public_context(tmp_path)
    result = await _kernel_runner(tmp_path, FunctionModel(scripted)).diagnose()

    assert result.kernel_state.gaps == ()
    assert result.metrics.successful_tool_calls == 0
    assert all(event.event_type != "TOOL_CALL" for event in result.trace)


def test_ledger_text_and_kernel_rejection_agree_on_relation_allowlist() -> None:
    """Evidence-returned relations outside a tool's allowlist stay unqueryable.

    The ledger must not offer relations that merely appeared in accepted
    evidence: the kernel rejects them with ``RELATION_NOT_ALLOWED`` and the
    text must say exactly that, without an evidence-returned exception.
    """

    kernel = DiagnosticKernel.start(
        run_id=RUN_ID,
        allowed_root_cause_codes=(
            "SOURCE_SCHEMA_COLUMN_RENAMED",
            "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
        ),
        model_request_limit=8,
        tool_call_limit=8,
        observable_schema_relations=("raw_payments",),
        incident_subjects=("model.jaffle_shop.stg_payments",),
    )
    observed_at = datetime(2026, 8, 30, tzinfo=UTC)
    lineage = EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_LINEAGE,
        source=EvidenceSource.DBT_MANIFEST,
        subject="model.jaffle_shop.stg_payments",
        observed_at=observed_at,
        content=DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=RUN_ID,
            node_id="model.jaffle_shop.stg_payments",
            direction="upstream",
            related_nodes=(
                DbtLineageNode(
                    node_id="seed.jaffle_shop.raw_orders",
                    resource_type="seed",
                    name="raw_orders",
                    distance=1,
                ),
            ),
        ),
    )
    prepared = kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id="g_auto_1",
            gap_kind=EvidenceGapKind.DISCOVER_SOURCE_RELATION,
        ),
        tool_name="get_dbt_lineage",
        arguments={
            "node_id": "model.jaffle_shop.stg_payments",
            "direction": "upstream",
        },
    )
    kernel.record_tool_result(prepared, (lineage,))

    summary = _kernel_state_summary(kernel, kernel.snapshot(model_requests_used=0))

    assert "already returned by accepted evidence" not in summary
    assert "relations returned by evidence do not extend it" in summary
    assert "raw_orders" not in summary
    assert "raw_payments" in summary

    with pytest.raises(KernelError, match="RELATION_NOT_ALLOWED"):
        kernel.prepare_tool(
            intent=InvestigationIntent(
                gap_id="g_auto_2",
                gap_kind=EvidenceGapKind.DISCRIMINATE_SCHEMA,
            ),
            tool_name="get_relation_schema",
            arguments={"relation_name": "raw_orders"},
        )
