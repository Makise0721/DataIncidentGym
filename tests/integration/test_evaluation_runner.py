from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from data_incident_gym.artifacts import ARTIFACT_FILENAMES, ArtifactWriter
from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.diagnostic_agent import DiagnosisRunner
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evaluation import (
    DeterministicEvaluator,
    EvaluationResult,
    EvaluationStatus,
)
from data_incident_gym.evaluation_runner import EvaluationRunner
from data_incident_gym.evidence import EvidenceRecord
from data_incident_gym.incidents import CASE_ID, load_ground_truth
from data_incident_gym.lab import IncidentLab


def _returned_records(
    messages: Iterable[ModelMessage], tool_name: str
) -> tuple[EvidenceRecord, ...]:
    returned: list[EvidenceRecord] = []
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolReturnPart) and part.tool_name == tool_name:
                assert part.outcome == "success"
                assert isinstance(part.content, tuple)
                returned.extend(part.content)
    return tuple(returned)


def _scripted_diagnosis(
    messages: list[ModelMessage], agent_info: AgentInfo, *, initial_run_id: str
) -> ModelResponse:
    run_results = _returned_records(messages, "get_dbt_run_results")
    node_errors = _returned_records(messages, "get_dbt_node_error")
    lineages = _returned_records(messages, "get_dbt_lineage")
    schemas = _returned_records(messages, "get_relation_schema")
    downstream = tuple(
        record
        for record in lineages
        if getattr(record.content, "direction", None) == "downstream"
    )
    upstream = tuple(
        record
        for record in lineages
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
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "get_dbt_lineage",
                    {"node_id": node_errors[-1].content.node_id, "direction": "upstream"},
                    tool_call_id="upstream-lineage",
                )
            ]
        )
    if not schemas or not downstream:
        seed = next(
            node
            for record in upstream
            for node in record.content.related_nodes
            if node.resource_type == "seed"
        )
        node_id = node_errors[-1].content.node_id
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

    return ModelResponse(
        parts=[
            ToolCallPart(
                agent_info.output_tools[0].name,
                {
                    "status": "CONFIRMED",
                    "incident_case_id": CASE_ID,
                    "run_id": node_errors[-1].run_id,
                    "root_cause_code": "SOURCE_SCHEMA_COLUMN_RENAMED",
                    "summary": "The collected evidence confirms the source schema change.",
                    "recommended_actions": ("Restore the source contract before the next build.",),
                    "confidence": 0.9,
                },
                tool_call_id="diagnosis",
            )
        ]
    )


def _evaluation_runner_with_function_model(project_root: Path) -> EvaluationRunner:
    settings = Settings(_env_file=None)
    diagnostic_settings = DiagnosticSettings(_env_file=None)
    lab = IncidentLab(settings, project_root)

    def diagnosis_factory(run_id: str) -> DiagnosisRunner:
        return DiagnosisRunner.for_run(
            run_id,
            diagnostic_settings,
            project_root,
            model=FunctionModel(partial(_scripted_diagnosis, initial_run_id=run_id)),
        )

    return EvaluationRunner(
        lab=lab,
        diagnostic_settings=diagnostic_settings,
        diagnosis_factory=diagnosis_factory,
        ground_truth_loader=lambda case_id: load_ground_truth(case_id, project_root),
        evaluator=DeterministicEvaluator.evaluate,
        artifact_writer=ArtifactWriter(project_root),
        clock=lambda: datetime.now(UTC),
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_lab_function_model_evaluator_and_artifacts_close_one_attempt() -> None:
    runner = _evaluation_runner_with_function_model(PROJECT_ROOT)

    result = await runner.run(CASE_ID)

    assert result.status == EvaluationStatus.PASSED
    assert result.evaluation.failed_check_codes == ()
    assert {path.name for path in result.artifact_dir.iterdir()} == set(ARTIFACT_FILENAMES)
    assert EvaluationResult.model_validate_json(
        (result.artifact_dir / "evaluation.json").read_text(encoding="utf-8")
    ).status is EvaluationStatus.PASSED

    lab = IncidentLab(Settings(_env_file=None), PROJECT_ROOT)
    try:
        assert lab.inject(CASE_ID).state == "INJECTED"
    finally:
        assert lab.reset(CASE_ID).state == "HEALTHY"
