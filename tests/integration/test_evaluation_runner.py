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
from data_incident_gym.diagnosis import DiagnosticStrategy
from data_incident_gym.diagnostic_agent import DiagnosisRunner, ModelIdentity
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evaluation import DeterministicEvaluator, EvaluationStatus
from data_incident_gym.evaluation_runner import EvaluationRunner
from data_incident_gym.evidence import EvidenceRecord
from data_incident_gym.lab import IncidentLab
from data_incident_gym.scenarios import load_scenario_spec

SCENARIO_ID = "schema_type_change_payment_amount"
FAILURE_NODE = "model.jaffle_shop.stg_payments"


def _returned_records(
    messages: Iterable[ModelMessage],
    tool_name: str,
) -> tuple[EvidenceRecord, ...]:
    records: list[EvidenceRecord] = []
    for message in messages:
        for part in message.parts:
            if (
                isinstance(part, ToolReturnPart)
                and part.tool_name == tool_name
                and part.outcome == "success"
                and isinstance(part.content, tuple)
            ):
                records.extend(part.content)
    return tuple(records)


def _tool_call(name: str, arguments: dict[str, object], call_id: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(name, arguments, tool_call_id=call_id)])


def _static_diagnosis(
    messages: list[ModelMessage],
    agent_info: AgentInfo,
    *,
    run_id: str,
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
        return _tool_call("get_dbt_run_results", {"run_id": run_id}, "run-results")
    if not node_errors:
        return _tool_call(
            "get_dbt_node_error",
            {"run_id": run_id, "node_id": FAILURE_NODE},
            "node-error",
        )
    if not upstream:
        return _tool_call(
            "get_dbt_lineage",
            {"node_id": FAILURE_NODE, "direction": "upstream"},
            "upstream",
        )
    if not schemas:
        return _tool_call("get_relation_schema", {"relation_name": "raw_payments"}, "schema")
    if not downstream:
        return _tool_call(
            "get_dbt_lineage",
            {"node_id": FAILURE_NODE, "direction": "downstream"},
            "downstream",
        )

    node_error_id = node_errors[-1].evidence_id
    run_result_id = run_results[-1].evidence_id
    schema_id = schemas[-1].evidence_id
    lineage_id = downstream[-1].evidence_id
    assets = (
        FAILURE_NODE,
        *(
            node.node_id
            for node in downstream[-1].content.related_nodes
            if node.resource_type == "model"
        ),
    )
    payload = {
        "status": "CONFIRMED",
        "run_id": run_id,
        "root_cause_code": "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
        "summary": "The payment amount source type changed.",
        "affected_assets": assets,
        "evidence_ids": [run_result_id, node_error_id, schema_id, lineage_id],
        "claims": [
            {
                "kind": "ROOT_CAUSE",
                "root_cause_code": "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                "evidence_ids": [run_result_id, node_error_id, schema_id],
            },
            *(
                {
                    "kind": "AFFECTED_ASSET",
                    "asset": asset,
                    "evidence_ids": [node_error_id if asset == FAILURE_NODE else lineage_id],
                }
                for asset in assets
            ),
        ],
        "unresolved_evidence": [],
        "recommended_actions": ["Restore the source contract before the next build."],
        "confidence": 0.9,
    }
    return _tool_call(agent_info.output_tools[0].name, payload, "diagnosis")


def _runner(project_root: Path) -> EvaluationRunner:
    settings = Settings(_env_file=None)
    diagnostic_settings = DiagnosticSettings(_env_file=None)

    def diagnosis_factory(run_id: str, strategy: DiagnosticStrategy) -> DiagnosisRunner:
        assert strategy is DiagnosticStrategy.STATIC_SKILL
        return DiagnosisRunner.for_run(
            run_id,
            diagnostic_settings,
            strategy,
            project_root,
            model=FunctionModel(partial(_static_diagnosis, run_id=run_id)),
            model_identity=ModelIdentity(
                provider="pydantic-function",
                model="scripted-static-model",
            ),
        )

    lab = IncidentLab(settings, project_root)
    return EvaluationRunner(
        lab=lab,
        diagnostic_settings=diagnostic_settings,
        diagnosis_factory=diagnosis_factory,
        private_scenario_loader=lambda case_id: load_scenario_spec(case_id, project_root),
        private_verification_loader=lab.verifier.load_verification,
        evaluator=DeterministicEvaluator.evaluate,
        artifact_writer=ArtifactWriter(project_root),
        clock=lambda: datetime.now(UTC),
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_function_model_runner_evaluates_and_writes_static_bundle() -> None:
    result = await _runner(PROJECT_ROOT).run(SCENARIO_ID, DiagnosticStrategy.STATIC_SKILL)

    assert result.status is EvaluationStatus.PASSED
    assert result.evaluation.failed_check_codes == ()
    assert {path.name for path in result.artifact_dir.iterdir()} == set(ARTIFACT_FILENAMES)
    assert result.evaluation.run_id == result.run_id
    assert result.evaluation.incident_case_id == SCENARIO_ID
    assert result.evaluation.status is EvaluationStatus.PASSED
    assert result.evaluation.controller_checks == ()
    assert result.artifact_dir.is_dir()
