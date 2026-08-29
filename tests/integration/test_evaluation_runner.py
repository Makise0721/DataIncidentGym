from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from data_incident_gym.artifacts import ARTIFACT_FILENAMES, ArtifactWriter, TraceEnvelope
from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.diagnosis import Diagnosis
from data_incident_gym.diagnostic_agent import DiagnosisRunner, ModelIdentity
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.diagnostic_kernel import HypothesisVerdict, KernelFinalStatus
from data_incident_gym.evaluation import (
    DeterministicEvaluator,
    EvaluationResult,
    EvaluationStatus,
)
from data_incident_gym.evaluation_runner import EvaluationRunner
from data_incident_gym.evidence import EvidenceRecord
from data_incident_gym.incidents import SUPPORTED_CASE_IDS, TYPE_CHANGE_CASE_ID, load_ground_truth
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


def _intent(
    gap_id: str,
    gap_kind: str,
    *,
    hypothesis_ids: tuple[str, ...] = (),
    new_hypotheses: tuple[dict[str, str], ...] = (),
) -> dict[str, object]:
    return {
        "gap_id": gap_id,
        "gap_kind": gap_kind,
        "hypothesis_ids": hypothesis_ids,
        "new_hypotheses": new_hypotheses,
    }


def _tool_call(
    name: str,
    arguments: dict[str, object],
    tool_call_id: str,
) -> ModelResponse:
    return ModelResponse(
        parts=[ToolCallPart(name, arguments, tool_call_id=tool_call_id)]
    )


def _scripted_diagnosis(
    messages: list[ModelMessage],
    agent_info: AgentInfo,
    *,
    incident_case_id: str,
    initial_run_id: str,
) -> ModelResponse:
    run_results = _returned_records(messages, "get_dbt_run_results")
    node_errors = _returned_records(messages, "get_dbt_node_error")
    lineages = _returned_records(messages, "get_dbt_lineage")
    schemas = _returned_records(messages, "get_relation_schema")
    upstream = tuple(
        record
        for record in lineages
        if getattr(record.content, "direction", None) == "upstream"
    )
    downstream = tuple(
        record
        for record in lineages
        if getattr(record.content, "direction", None) == "downstream"
    )

    if not run_results:
        return _tool_call(
            "get_dbt_run_results",
            {
                "run_id": initial_run_id,
                "intent": _intent("g_failure", "LOCATE_FAILURE"),
            },
            "run-results",
        )
    if not node_errors:
        run_fact = run_results[-1].content
        return _tool_call(
            "get_dbt_node_error",
            {
                "run_id": run_results[-1].run_id,
                "node_id": run_fact.failed_nodes[0],
                "intent": _intent("g_explain", "EXPLAIN_FAILURE"),
            },
            "node-error",
        )
    if not upstream:
        return _tool_call(
            "get_dbt_lineage",
            {
                "node_id": node_errors[-1].content.node_id,
                "direction": "upstream",
                "intent": _intent("g_source", "DISCOVER_SOURCE_RELATION"),
            },
            "upstream-lineage",
        )
    if not schemas:
        seed = next(
            node
            for record in upstream
            for node in record.content.related_nodes
            if node.resource_type == "seed"
        )
        return _tool_call(
            "get_relation_schema",
            {
                "relation_name": seed.name,
                "intent": _intent(
                    "g_schema",
                    "DISCRIMINATE_SCHEMA",
                    hypothesis_ids=("h_rename", "h_type"),
                    new_hypotheses=(
                        {
                            "hypothesis_id": "h_rename",
                            "root_cause_code": "SOURCE_SCHEMA_COLUMN_RENAMED",
                        },
                        {
                            "hypothesis_id": "h_type",
                            "root_cause_code": "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                        },
                    ),
                ),
            },
            "relation-schema",
        )
    if not downstream:
        return _tool_call(
            "get_dbt_lineage",
            {
                "node_id": node_errors[-1].content.node_id,
                "direction": "downstream",
                "intent": _intent(
                    "g_impact",
                    "MAP_IMPACT",
                    hypothesis_ids=("h_rename", "h_type"),
                ),
            },
            "downstream-lineage",
        )

    schema_fact = schemas[-1].content
    if any(
        column.name == "amount" and column.data_type == "text"
        for column in schema_fact.columns
    ):
        selected = ("h_type", "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED")
        refuted = "h_rename"
    else:
        selected = ("h_rename", "SOURCE_SCHEMA_COLUMN_RENAMED")
        refuted = "h_type"
    node_error_id = node_errors[-1].evidence_id
    schema_id = schemas[-1].evidence_id
    lineage_id = downstream[-1].evidence_id
    failed_node_id = node_errors[-1].content.node_id
    assets = (
        failed_node_id,
        *(
            node.node_id
            for node in downstream[-1].content.related_nodes
            if node.resource_type == "model"
        ),
    )
    return _tool_call(
        agent_info.output_tools[0].name,
        {
            "status": "CONFIRMED",
            "incident_case_id": incident_case_id,
            "run_id": initial_run_id,
            "selected_hypothesis_id": selected[0],
            "assessments": (
                {
                    "hypothesis_id": selected[0],
                    "verdict": "SUPPORTED",
                    "evidence_ids": (node_error_id, schema_id),
                },
                {
                    "hypothesis_id": refuted,
                    "verdict": "REFUTED",
                    "evidence_ids": (schema_id,),
                },
            ),
            "claims": (
                {
                    "kind": "ROOT_CAUSE",
                    "value": selected[1],
                    "evidence_ids": (node_error_id, schema_id),
                },
                *(
                    {
                        "kind": "AFFECTED_ASSET",
                        "value": asset,
                        "evidence_ids": (
                            (node_error_id,)
                            if asset == failed_node_id
                            else (lineage_id,)
                        ),
                    }
                    for asset in assets
                ),
            ),
            "summary": "SOURCE_SCHEMA_CHANGE_CONFIRMED",
            "recommended_actions": ("RESTORE_SOURCE_SCHEMA_CONTRACT",),
            "confidence": 0.9,
        },
        "diagnosis",
    )


def _evaluation_runner_with_function_model(project_root: Path, case_id: str) -> EvaluationRunner:
    settings = Settings(_env_file=None)
    diagnostic_settings = DiagnosticSettings(_env_file=None)
    lab = IncidentLab(settings, project_root)

    def diagnosis_factory(run_id: str) -> DiagnosisRunner:
        return DiagnosisRunner.for_run(
            run_id,
            diagnostic_settings,
            project_root,
            model=FunctionModel(
                partial(
                    _scripted_diagnosis,
                    incident_case_id=case_id,
                    initial_run_id=run_id,
                )
            ),
            model_identity=ModelIdentity(
                provider="pydantic-function",
                model="scripted-kernel-model",
            ),
        )

    return EvaluationRunner(
        lab=lab,
        diagnostic_settings=diagnostic_settings,
        diagnosis_factory=diagnosis_factory,
        ground_truth_loader=lambda value: load_ground_truth(value, project_root),
        evaluator=DeterministicEvaluator.evaluate,
        artifact_writer=ArtifactWriter(project_root),
        clock=lambda: datetime.now(UTC),
    )


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", SUPPORTED_CASE_IDS)
async def test_real_lab_function_model_evaluator_and_artifacts_close_one_attempt(
    case_id: str,
) -> None:
    runner = _evaluation_runner_with_function_model(PROJECT_ROOT, case_id)

    result = await runner.run(case_id)

    assert result.status == EvaluationStatus.PASSED
    assert result.evaluation.failed_check_codes == ()
    assert {path.name for path in result.artifact_dir.iterdir()} == set(ARTIFACT_FILENAMES)
    assert EvaluationResult.model_validate_json(
        (result.artifact_dir / "evaluation.json").read_text(encoding="utf-8")
    ).status is EvaluationStatus.PASSED
    diagnosis = Diagnosis.model_validate_json(
        (result.artifact_dir / "diagnosis.json").read_text(encoding="utf-8")
    )
    state = TraceEnvelope.model_validate_json(
        (result.artifact_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    ).event.state
    assert state.final_status is KernelFinalStatus.CONFIRMED
    assert len(state.hypotheses) >= 2
    assert any(
        assessment.verdict is HypothesisVerdict.REFUTED
        for assessment in state.assessments
    )
    if case_id == TYPE_CHANGE_CASE_ID:
        assert diagnosis.root_cause_code == (
            "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"
        )

    lab = IncidentLab(Settings(_env_file=None), PROJECT_ROOT)
    try:
        assert lab.inject(case_id).state == "INJECTED"
    finally:
        assert lab.reset(case_id).state == "HEALTHY"
