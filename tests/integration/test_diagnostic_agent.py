from __future__ import annotations

from collections.abc import Iterable
from functools import partial

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.diagnosis import DiagnosisStatus, DiagnosticStrategy
from data_incident_gym.diagnostic_agent import DiagnosisRunner, ModelIdentity
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evidence import EvidenceRecord
from data_incident_gym.lab import IncidentLab

RUNNING_SCENARIO = "schema_type_change_payment_amount"
FAILURE_NODE = "model.jaffle_shop.stg_payments"


def _returned_records(
    messages: Iterable[ModelMessage],
    tool_name: str,
) -> tuple[EvidenceRecord, ...]:
    returned: list[EvidenceRecord] = []
    for message in messages:
        for part in message.parts:
            if (
                isinstance(part, ToolReturnPart)
                and part.tool_name == tool_name
                and part.outcome == "success"
                and isinstance(part.content, tuple)
            ):
                returned.extend(part.content)
    return tuple(returned)


def _intent(**values: object) -> dict[str, object]:
    binding: dict[str, object] = {
        "kernel_hypothesis_ids": [],
        "kernel_new_hypotheses": [],
    }
    mapping = {
        "hypothesis_ids": "kernel_hypothesis_ids",
        "new_hypotheses": "kernel_new_hypotheses",
    }
    for key, value in values.items():
        binding[mapping.get(key, key)] = value
    return binding


def _tool_call(name: str, arguments: dict[str, object], call_id: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(name, arguments, tool_call_id=call_id)])


def _scripted_diagnosis(
    messages: list[ModelMessage],
    agent_info: AgentInfo,
    *,
    run_id: str,
) -> ModelResponse:
    _run_results = _returned_records(messages, "get_dbt_run_results")
    node_errors = _returned_records(messages, "get_dbt_node_error")
    upstream = _returned_records(messages, "get_dbt_lineage")
    schemas = _returned_records(messages, "get_relation_schema")
    downstream = tuple(
        record
        for record in upstream
        if getattr(record.content, "direction", None) == "downstream"
    )
    upstream = tuple(
        record
        for record in upstream
        if getattr(record.content, "direction", None) == "upstream"
    )

    if not _run_results:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "get_dbt_run_results",
                    {**{"run_id": run_id}, **_intent()},
                    tool_call_id="run-results",
                ),
            ]
        )
    if not node_errors:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "get_dbt_node_error",
                    {
                        **{"run_id": run_id, "node_id": FAILURE_NODE},
                        **_intent(),
                    },
                    tool_call_id="node-error",
                ),
            ]
        )
    if not upstream:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "get_dbt_lineage",
                    {
                        **{"node_id": FAILURE_NODE, "direction": "upstream"},
                        **_intent(),
                    },
                    tool_call_id="upstream",
                ),
            ]
        )
    if not schemas:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "get_relation_schema",
                    {
                        **{"relation_name": "raw_payments"},
                        **_intent(new_hypotheses=[
                                {
                                    "hypothesis_id": "h_rename",
                                    "root_cause_code": "SOURCE_SCHEMA_COLUMN_RENAMED",
                                },
                                {
                                    "hypothesis_id": "h_type",
                                    "root_cause_code": "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                                },
                            ],
                        ),
                    },
                    tool_call_id="schema",
                ),
            ]
        )
    if not downstream:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "get_dbt_lineage",
                    {
                        **{"node_id": FAILURE_NODE, "direction": "downstream"},
                        **_intent(hypothesis_ids=["h_rename", "h_type"],
                        ),
                    },
                    tool_call_id="downstream",
                ),
            ]
        )

    schema = schemas[-1].content
    node_error_id = node_errors[-1].evidence_id
    schema_id = schemas[-1].evidence_id
    lineage_id = downstream[-1].evidence_id
    model_assets = tuple(
        node.node_id
        for node in downstream[-1].content.related_nodes
        if node.resource_type == "model"
    )
    selected = (
        "h_type",
        "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
    ) if any(
        column.name == "amount" and column.data_type == "text" for column in schema.columns
    ) else (
        "h_rename",
        "SOURCE_SCHEMA_COLUMN_RENAMED",
    )
    assessments = [
        {
            "hypothesis_id": "h_rename",
            "verdict": "SUPPORTED" if selected[0] == "h_rename" else "REFUTED",
            "evidence_ids": [schema_id],
        },
        {
            "hypothesis_id": "h_type",
            "verdict": "SUPPORTED" if selected[0] == "h_type" else "REFUTED",
            "evidence_ids": [node_error_id, schema_id],
        },
    ]
    claims = [
        {
            "kind": "ROOT_CAUSE",
            "value": selected[1],
            "evidence_ids": [node_error_id, schema_id],
        },
        {
            "kind": "AFFECTED_ASSET",
            "value": FAILURE_NODE,
            "evidence_ids": [node_error_id],
        },
        *(
            {
                "kind": "AFFECTED_ASSET",
                "value": asset,
                "evidence_ids": [lineage_id],
            }
            for asset in model_assets
        ),
    ]
    payload = {
        "status": "CONFIRMED",
        "run_id": run_id,
        "selected_hypothesis_id": selected[0],
        "assessments": assessments,
        "claims": claims,
        "unresolved_evidence": [],
        "summary": "The payment amount source type changed.",
        "recommended_actions": ["Restore the source contract before the next build."],
        "confidence": 0.9,
    }
    return _tool_call(agent_info.output_tools[0].name, payload, "diagnosis")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_lab_tools_drive_a_kernel_confirmed_diagnosis() -> None:
    lab = IncidentLab(Settings(_env_file=None), PROJECT_ROOT)
    lab.reset(RUNNING_SCENARIO)
    try:
        lab.prepare(RUNNING_SCENARIO)
        run = lab.build(RUNNING_SCENARIO)
        runner = DiagnosisRunner.for_run(
            run.run_id,
            DiagnosticSettings(_env_file=None),
            DiagnosticStrategy.DIAGNOSTIC_KERNEL,
            PROJECT_ROOT,
            model=FunctionModel(partial(_scripted_diagnosis, run_id=run.run_id)),
            model_identity=ModelIdentity(
                provider="pydantic-function",
                model="scripted-kernel-model",
            ),
        )

        result = await runner.diagnose()

        assert result.diagnosis.status is DiagnosisStatus.CONFIRMED
        assert result.kernel_state is not None
        assert result.kernel_state.final_status.value == "CONFIRMED"
        assert result.metrics.successful_tool_calls == 5
        assert result.trace[-2].event_type == "KERNEL_STATE"
        assert result.trace[-1].event_type == "DIAGNOSIS_TERMINAL"
        assert all(
            set(event.arguments) <= {"run_id", "node_id", "direction", "relation_name"}
            for event in result.trace
            if event.event_type == "TOOL_CALL"
        )
    finally:
        assert lab.restore(RUNNING_SCENARIO).state == "HEALTHY"
