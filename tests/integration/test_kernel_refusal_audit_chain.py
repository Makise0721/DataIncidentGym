"""Real-evidence acceptance for the versioned kernel refusal projection.

A FunctionModel drives a DIAGNOSTIC_KERNEL diagnosis over the real
PostgreSQL/dbt lab: the scripted investigator collects witnessed receipts
through the read-only evidence tools, then submits one deliberately
mis-scoped abstention, so the kernel contract refuses it
(``DECISION_SCOPE_MISMATCH``) as the run's only refused gate event. A
response without any tool call ends the run before another gate event can be
recorded. The evaluation runner archives the failed run and its scoring
inputs; the test strictly reloads the bundle and re-derives the first-error
verdict offline with the P-1 reader.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from data_incident_gym.artifacts import ARTIFACT_FILENAMES, ArtifactWriter
from data_incident_gym.config import Settings
from data_incident_gym.diagnosis import (
    DiagnosisRunResultV3,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    EvidenceGateTraceEventV2,
    KernelStateTraceEvent,
    ToolTraceEventV2,
)
from data_incident_gym.diagnostic_agent import DiagnosisRunner, ModelIdentity
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.diagnostic_contracts import InvestigationState, KernelFinalStatus
from data_incident_gym.evaluation import DeterministicEvaluator, EvaluationStatus
from data_incident_gym.evaluation_inputs import load_evaluation_input_bundle
from data_incident_gym.evaluation_runner import EvaluationRunner
from data_incident_gym.evidence import DbtRunResultsFact, EvidenceRecord
from data_incident_gym.lab import IncidentLab
from data_incident_gym.refusal_review import (
    BASIS_DECISION_SCOPE_MISMATCH_ESTABLISHED,
    RefusalReviewStatus,
    review_refusal_events,
)
from data_incident_gym.scenarios import load_scenario_spec

SCENARIO_ID = "schema_type_change_payment_amount"
# A well-formed run id that can never be this run's id: the mis-scope is the
# kernel contract's own first content check after finalization, so the
# refusal is established before any decision content is judged.
WRONG_RUN_ID = "f" * 32


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


def _with_intent(response: ModelResponse) -> ModelResponse:
    """Kernel controllers read hypothesis intents from tool-call arguments."""

    part = response.parts[0]
    assert isinstance(part, ToolCallPart)
    arguments = part.args
    assert isinstance(arguments, dict)
    merged = {
        **arguments,
        "kernel_hypothesis_ids": [],
        "kernel_new_hypotheses": [],
    }
    return ModelResponse(
        parts=[ToolCallPart(part.tool_name, merged, tool_call_id=part.tool_call_id)]
    )


def _rejection_script(
    messages: list[ModelMessage],
    agent_info: AgentInfo,
    *,
    run_id: str,
) -> ModelResponse:
    output_names = {tool.name for tool in agent_info.output_tools}
    if any(
        isinstance(part, ToolCallPart) and part.tool_name in output_names
        for message in messages
        for part in message.parts
    ):
        # The rejection is on record; produce no tool call so the run
        # terminates before any later gate event can be appended.
        return ModelResponse(parts=[TextPart("the decision stands refused")])

    run_records = _returned_records(messages, "get_dbt_run_results")
    if not run_records:
        return _with_intent(
            _tool_call("get_dbt_run_results", {"run_id": run_id}, "run-results")
        )
    node_errors = _returned_records(messages, "get_dbt_node_error")
    if not node_errors:
        run_fact = run_records[-1].content
        assert isinstance(run_fact, DbtRunResultsFact)
        return _with_intent(
            _tool_call(
                "get_dbt_node_error",
                {"run_id": run_id, "node_id": run_fact.failed_nodes[0]},
                "node-error",
            )
        )
    lineages = _returned_records(messages, "get_dbt_lineage")
    if not lineages:
        node_error = node_errors[-1].content
        return _with_intent(
            _tool_call(
                "get_dbt_lineage",
                {"node_id": node_error.node_id, "direction": "upstream"},
                "upstream-lineage",
            )
        )
    # Kernel strategies expose three output tools in a fixed order: the
    # abstention tool owns INSUFFICIENT_EVIDENCE submissions.
    return _tool_call(
        agent_info.output_tools[0].name,
        {
            "run_id": WRONG_RUN_ID,
            "summary": "Decisive evidence is unavailable, so the cause stays unresolved.",
            "recommended_actions": ["Collect the unavailable decisive evidence."],
            "confidence": 0.2,
        },
        "mis-scoped-abstention",
    )


def _runner(project_root: Path) -> EvaluationRunner:
    settings = Settings(_env_file=None)
    diagnostic_settings = DiagnosticSettings(_env_file=None)

    def diagnosis_factory(
        run_id: str,
        strategy: DiagnosticStrategy,
        submission_policy: object | None = None,
    ) -> DiagnosisRunner:
        assert strategy is DiagnosticStrategy.DIAGNOSTIC_KERNEL
        return DiagnosisRunner.for_run(
            run_id,
            diagnostic_settings,
            strategy,
            project_root,
            model=FunctionModel(partial(_rejection_script, run_id=run_id)),
            model_identity=ModelIdentity(
                provider="pydantic-function",
                model="kernel-refusal-audit-function-model",
            ),
            submission_policy=submission_policy,
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
async def test_kernel_contract_rejection_archives_and_reviews_offline(
    project_root: Path,
) -> None:
    result = await _runner(project_root).run(SCENARIO_ID, DiagnosticStrategy.DIAGNOSTIC_KERNEL)

    assert result.status is EvaluationStatus.FAILED
    assert result.evaluation.status is EvaluationStatus.FAILED
    assert result.scoring_inputs_dir == (
        project_root / ".dig" / "scoring-inputs" / result.run_id
    )
    assert {path.name for path in result.artifact_dir.iterdir()} == set(ARTIFACT_FILENAMES)

    # Strict reload: the bundle's digests and typed kernel state are verified
    # by the loader itself; every later assertion runs on reloaded artifacts.
    bundle = load_evaluation_input_bundle(project_root, result.run_id)
    assert bundle.run_id == result.run_id
    assert bundle.recovery.recovered is True
    run = bundle.diagnosis_run
    assert isinstance(run, DiagnosisRunResultV3)
    assert run.strategy is DiagnosticStrategy.DIAGNOSTIC_KERNEL
    assert run.diagnosis.status is DiagnosisStatus.MODEL_ERROR

    refusals = [
        (index, event)
        for index, event in enumerate(run.trace)
        if isinstance(event, EvidenceGateTraceEventV2) and not event.accepted
    ]
    assert len(refusals) == 1
    refusal_index, refusal = refusals[0]
    assert refusal.reason_code == "DECISION_SCOPE_MISMATCH"
    # A kernel-contract refusal carries the rejected-decision projection, not
    # a submission-gate audit.
    assert refusal.refusal_audit is None
    rejected = refusal.rejected_decision
    assert rejected is not None

    # Audit-required fields must describe exactly the submitted decision.
    assert rejected.schema_version == "p1.rejected_decision.v2"
    assert rejected.status == "INSUFFICIENT_EVIDENCE"
    assert rejected.decision_scope_matches_run is False
    # Four requests completed on the four tool turns; the refusal rides the
    # fifth request, whose processing has not been counted yet.
    assert rejected.model_request_index == 4
    assert rejected.assessments == ()
    assert rejected.claims == ()
    assert rejected.unresolved_evidence == ()
    assert rejected.unresolved_subject_equivalence == ()
    assert rejected.total_assessments == 0
    assert rejected.total_claims == 0
    assert rejected.total_unresolved == 0
    assert rejected.unknown_hypothesis_count == 0
    assert rejected.unknown_claim_count == 0
    assert rejected.unknown_evidence_count == 0
    assert rejected.unknown_subject_count == 0
    assert rejected.truncated is False

    # The refusal was earned on real forensics: witnessed receipts from the
    # read-only evidence tools, resolved in the archived evidence inventory.
    tool_events = tuple(
        event for event in run.trace[:refusal_index] if isinstance(event, ToolTraceEventV2)
    )
    assert len(tool_events) == 3
    assert all(event.error_code is None for event in tool_events)
    assert [event.tool_name for event in tool_events] == [
        "get_dbt_run_results",
        "get_dbt_node_error",
        "get_dbt_lineage",
    ]
    receipt_ids = tuple(
        evidence_id for event in tool_events for evidence_id in event.evidence_ids
    )
    assert receipt_ids
    inventory = {record.evidence_id for record in run.evidence_records}
    assert set(receipt_ids) <= inventory

    # The run ended on the refusal: no tool call after it, the terminal kernel
    # state second to last, and the kernel never finalized a decision.
    assert not any(
        isinstance(event, ToolTraceEventV2) for event in run.trace[refusal_index + 1 : -2]
    )
    assert isinstance(run.trace[-1], DiagnosisTerminalTraceEvent)
    assert run.trace[-1].status is DiagnosisStatus.MODEL_ERROR
    assert isinstance(run.trace[-2], KernelStateTraceEvent)
    kernel_state = InvestigationState.model_validate(run.trace[-2].state)
    assert kernel_state.final_status is KernelFinalStatus.MODEL_ERROR

    # Offline first-error review re-derives the verdict from the archive alone.
    verdicts = review_refusal_events(run, bundle.scenario)
    assert len(verdicts) == 1
    verdict = verdicts[0]
    assert verdict.status is RefusalReviewStatus.CORRECT
    assert verdict.basis == (BASIS_DECISION_SCOPE_MISMATCH_ESTABLISHED,)
    assert verdict.run_id == run.diagnosis.run_id == result.run_id
    assert verdict.trace_index == refusal_index
    assert verdict.reason_code == refusal.reason_code
    assert verdict.model_request_index == rejected.model_request_index
    assert verdict.recomputability_disagreements == ()
