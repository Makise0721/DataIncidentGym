from __future__ import annotations

from collections.abc import Iterable
from functools import partial

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.diagnosis import DiagnosisStatus
from data_incident_gym.diagnostic_agent import DiagnosisRunner
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evidence import EvidenceRecord, EvidenceType
from data_incident_gym.incidents import CASE_ID
from data_incident_gym.lab import IncidentLab


def _returned_records(
    messages: Iterable[ModelMessage], tool_name: str
) -> tuple[EvidenceRecord, ...]:
    returned: list[EvidenceRecord] = []
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolReturnPart) and part.tool_name == tool_name:
                assert part.outcome == "success"
                records = part.content
                assert isinstance(records, tuple)
                returned.extend(records)
    return tuple(returned)


def _scripted_diagnosis(
    messages: list[ModelMessage], agent_info: AgentInfo, *, initial_run_id: str
) -> ModelResponse:
    run_results = _returned_records(messages, "get_dbt_run_results")
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

    if not run_results:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "get_dbt_run_results",
                    {"run_id": initial_run_id},
                    tool_call_id="run-results",
                )
            ]
        )
    if not node_errors:
        run_fact = run_results[-1].content
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "get_dbt_node_error",
                    {"run_id": run_results[-1].run_id, "node_id": run_fact.failed_nodes[0]},
                    tool_call_id="node-error",
                )
            ]
        )
    if not upstream:
        node_id = node_errors[-1].content.node_id
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "get_dbt_lineage",
                    {"node_id": node_id, "direction": "upstream"},
                    tool_call_id="upstream-lineage",
                )
            ]
        )
    if not schemas or not downstream:
        node_id = node_errors[-1].content.node_id
        seed = next(
            node
            for record in upstream
            for node in record.content.related_nodes
            if node.resource_type == "seed"
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "get_relation_schema",
                    {"relation_name": seed.name},
                    tool_call_id="relation-schema",
                ),
                ToolCallPart(
                    "get_dbt_lineage",
                    {"node_id": node_id, "direction": "downstream"},
                    tool_call_id="downstream-lineage",
                ),
            ]
        )

    all_records = run_results + node_errors + schemas + upstream + downstream
    schema_fact = schemas[-1].content
    root_cause_code = f"EVIDENCE_{schema_fact.relation_name.upper()}_SCHEMA_MISMATCH"
    evidence_ids = tuple(
        record.evidence_id
        for record in all_records
        if record.evidence_type in {
            EvidenceType.DBT_NODE_ERROR,
            EvidenceType.RELATION_SCHEMA,
            EvidenceType.DBT_LINEAGE,
        }
    )
    node_name = node_errors[-1].content.node_id.rsplit(".", 1)[-1]
    affected_assets = (node_name,) + tuple(
        node.name
        for record in downstream
        for node in record.content.related_nodes
    )
    return ModelResponse(
        parts=[
            ToolCallPart(
                agent_info.output_tools[0].name,
                {
                    "status": "CONFIRMED",
                    "incident_case_id": CASE_ID,
                    "run_id": node_errors[-1].run_id,
                    "root_cause_code": root_cause_code,
                    "summary": "The collected evidence confirms the incident.",
                    "affected_assets": affected_assets,
                    "evidence_ids": evidence_ids,
                    "recommended_actions": ("Repair the upstream schema contract.",),
                    "confidence": 0.9,
                },
                tool_call_id="diagnosis",
            )
        ]
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_m2_run_and_m3_tools_drive_confirmed_diagnosis() -> None:
    lab = IncidentLab(Settings(_env_file=None), PROJECT_ROOT)
    lab.reset(CASE_ID)
    try:
        lab.inject(CASE_ID)
        run = lab.build(CASE_ID)
        runner = DiagnosisRunner.for_run(
            run.run_id,
            DiagnosticSettings(_env_file=None),
            PROJECT_ROOT,
            model=FunctionModel(partial(_scripted_diagnosis, initial_run_id=run.run_id)),
        )

        result = await runner.diagnose(CASE_ID)

        assert result.diagnosis.status == DiagnosisStatus.CONFIRMED
        inventory = {record.evidence_id: record for record in result.evidence_records}
        cited = tuple(inventory[evidence_id] for evidence_id in result.diagnosis.evidence_ids)
        assert {record.evidence_type for record in cited} >= {
            EvidenceType.DBT_NODE_ERROR,
            EvidenceType.RELATION_SCHEMA,
            EvidenceType.DBT_LINEAGE,
        }
        assert result.diagnosis.evidence_ids
        assert set(result.diagnosis.evidence_ids) <= set(inventory)
        schema_record = next(
            record
            for record in inventory.values()
            if record.evidence_type == EvidenceType.RELATION_SCHEMA
        )
        assert result.diagnosis.root_cause_code == (
            f"EVIDENCE_{schema_record.content.relation_name.upper()}_SCHEMA_MISMATCH"
        )
        assert result.metrics.successful_tool_calls >= 4
        assert {event.tool_name for event in result.trace if event.event_type == "TOOL_CALL"} <= {
            "get_dbt_run_results",
            "get_dbt_node_error",
            "get_relation_schema",
            "get_dbt_lineage",
        }
    finally:
        lab.reset(CASE_ID)
