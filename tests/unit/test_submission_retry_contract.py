"""How the three output tools share the retry budget, fixed per sequence.

The SDK charges a failed output call to that tool's own budget and shows the
validator a cumulative counter, so the retry allowance is per tool while the
recorded count is cumulative. These tests pin the four sequences the interface
review asked for — sibling success, all candidates failing, a repeated call of
the same tool, and alternating tools — rather than a single "worst case" bound.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from data_incident_gym.diagnosis import DiagnosisStatus, DiagnosticStrategy
from data_incident_gym.diagnostic_agent import DiagnosisRunner, ModelIdentity
from data_incident_gym.evidence import (
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
)

RUN_ID = "c" * 32
# Output tools are sent in a fixed order: abstention, confirmed, health.
_ABSTENTION_TOOL, _CONFIRMED_TOOL, _HEALTH_TOOL = 0, 1, 2
MODEL_BASE_URL = "http://127.0.0.1:11434/v1"
_BINDING = {
    "kernel_hypothesis_ids": ["h_loss", "h_decline"],
    "kernel_new_hypotheses": [
        {"hypothesis_id": "h_loss", "root_cause_code": "SOURCE_PAYMENT_INGESTION_LOSS"},
        {"hypothesis_id": "h_decline", "root_cause_code": "NORMAL_BUSINESS_PAYMENT_DECLINE"},
    ],
}


def _write_public_run(project_root: Path) -> None:
    run_root = project_root / ".dig" / "lab" / "runs" / RUN_ID
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "runtime.json").write_text(
        json.dumps(
            {
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
                "observable_relations": {
                    "schema": ["raw_payments"],
                    "profile": ["raw_payments"],
                    "history": ["raw_payments"],
                },
                "profile_spec_sha256": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    (run_root / "incident_brief.json").write_text(
        json.dumps(
            {
                "schema_version": "incident_brief.v1",
                "signal_code": "DBT_BUILD_FAILED",
                "summary": "The payment build failed.",
                "subjects": ["raw_payments"],
                "logical_observed_at": "2026-09-02T00:00:00+00:00",
                "observations": [],
            }
        ),
        encoding="utf-8",
    )


class _Tools:
    def get_dbt_run_results(self, run_id: str):
        return (
            EvidenceRecord.create(
                run_id=run_id,
                evidence_type=EvidenceType.DBT_RUN_RESULTS,
                source=EvidenceSource.DBT_RUN_RESULTS,
                subject=run_id,
                observed_at=datetime(2026, 9, 2, tzinfo=UTC),
                content=DbtRunResultsFact(
                    kind="DBT_RUN_RESULTS",
                    run_id=run_id,
                    run_status="FAILED",
                    dbt_exit_code=1,
                    failed_nodes=("model.jaffle_shop.stg_payments",),
                    skipped_nodes=(),
                ),
            ),
        )


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        model_base_url=MODEL_BASE_URL,
        model_name="synthetic-model",
        model_api_key="synthetic-key",
    )


def _abstention(*declarations: dict[str, str]) -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "assessments": [],
        "unresolved_evidence": list(declarations),
        "summary": "More evidence is required.",
        "recommended_actions": [],
        "confidence": 0.2,
    }


def _unbound() -> dict[str, str]:
    """A subject no accepted record can bind, so the kernel refuses it."""

    return {"evidence_kind": "INGESTION_WATERMARK", "subject": "raw_orders"}


def _bindable() -> dict[str, str]:
    return {"evidence_kind": "INGESTION_WATERMARK", "subject": "raw_payments"}


def _confirmed_without_root_claim() -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "selected_hypothesis_id": "h_loss",
        "assessments": [],
        "claims": [
            {
                "kind": "AFFECTED_ASSET",
                "value": "model.jaffle_shop.stg_payments",
                "evidence_ids": [],
            }
        ],
        "summary": "A confirmed decision with no root-cause claim.",
        "recommended_actions": [],
        "confidence": 0.9,
    }



@pytest.mark.asyncio
async def test_alternating_tools_across_responses_keep_their_own_budgets(
    tmp_path: Path,
) -> None:
    """Different responses submit through different tools: each refusal is charged
    to the tool that made it, and the run terminates on the tool whose own
    allowance is spent."""

    _write_public_run(tmp_path)
    order = (_ABSTENTION_TOOL, _CONFIRMED_TOOL) * 3

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
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
                        {"run_id": RUN_ID, **_BINDING},
                        tool_call_id="call-0",
                    )
                ]
            )
        round_index = min(len(sent) - 1, len(order) - 1)
        tool = order[round_index]
        payload = _unbound_abstention() if tool == _ABSTENTION_TOOL else _no_root_claim()
        return ModelResponse(
            parts=[_call(agent_info, tool, payload, f"final-{round_index}")]
        )

    result = await _run(tmp_path, FunctionModel(scripted))

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    event = _protocol_events(result)[0]
    assert event.error_origin == "KERNEL_DECISION"
    assert event.tool_name == "final_result_abstention"
    assert [call.tool_name for call in event.call_shapes] == ["final_result_abstention"]
    # Every refusal is charged to the tool that made it, so the two tools take
    # turns until the abstention tool's own allowance is spent.
    assert _gates(result) == [
        (False, "UNRESOLVED_EVIDENCE_UNBOUND"),
        (False, "HYPOTHESIS_ASSESSMENT_INCOMPLETE"),
        (False, "UNRESOLVED_EVIDENCE_UNBOUND"),
        (False, "HYPOTHESIS_ASSESSMENT_INCOMPLETE"),
        (False, "UNRESOLVED_EVIDENCE_UNBOUND"),
        (True, "MODEL_PROTOCOL_ERROR"),
    ]
    assert event.output_retry_used == 4


def _unbound_abstention() -> dict[str, object]:
    return _abstention({"evidence_kind": "INGESTION_WATERMARK", "subject": "raw_orders"})


def _no_root_claim() -> dict[str, object]:
    return _confirmed_without_root_claim()

def _call(
    agent_info: AgentInfo, index: int, payload: dict[str, object], call_id: str
) -> ToolCallPart:
    return ToolCallPart(agent_info.output_tools[index].name, payload, tool_call_id=call_id)


async def _run(tmp_path: Path, model: FunctionModel) -> object:
    runner = DiagnosisRunner.for_run(
        RUN_ID,
        _settings(),
        DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        tmp_path,
        model=model,
        tools=_Tools(),  # type: ignore[arg-type]
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )
    return await runner.diagnose()


def _gates(result: object) -> list[tuple[bool, str]]:
    return [
        (event.accepted, event.reason_code)
        for event in result.trace  # type: ignore[attr-defined]
        if getattr(event, "event_type", None) == "EVIDENCE_GATE"
    ]


def _protocol_events(result: object) -> list[object]:
    return [
        event
        for event in result.trace  # type: ignore[attr-defined]
        if getattr(event, "event_type", None) == "MODEL_PROTOCOL"
    ]


@pytest.mark.asyncio
async def test_sibling_success_sequence(tmp_path: Path) -> None:
    """A refused call and an accepted sibling in one response: the refusal is
    recorded, no retry is charged, and the accepted conclusion stands."""

    _write_public_run(tmp_path)

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
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
                        {"run_id": RUN_ID, **_BINDING},
                        tool_call_id="call-0",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                _call(agent_info, 0, _abstention(_unbound()), "final-1"),
                _call(agent_info, 0, _abstention(_bindable()), "final-2"),
            ]
        )

    result = await _run(tmp_path, FunctionModel(scripted))

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert _protocol_events(result) == []
    assert result.metrics.model_requests == 2
    assert _gates(result) == [
        (False, "UNRESOLVED_EVIDENCE_UNBOUND"),
        (True, "INSUFFICIENT_EVIDENCE"),
    ]
    assert result.diagnosis.unresolved_evidence[0].subject == "raw_payments"


@pytest.mark.asyncio
async def test_all_candidates_failing_in_one_response_charges_each_call(tmp_path: Path) -> None:
    """Every candidate refused: each failed call is charged, the validator sees a
    cumulative count, and the run terminates once a tool's own budget is spent."""

    _write_public_run(tmp_path)

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
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
                        {"run_id": RUN_ID, **_BINDING},
                        tool_call_id="call-0",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                _call(agent_info, 0, _abstention(_unbound()), "final-1"),
                _call(agent_info, 1, _confirmed_without_root_claim(), "final-2"),
            ]
        )

    result = await _run(tmp_path, FunctionModel(scripted))

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    events = _protocol_events(result)
    assert len(events) == 1
    event = events[0]
    assert event.category == "OUTPUT_SCHEMA_REJECTED"
    assert event.error_origin == "KERNEL_DECISION"
    # Both calls in a response are charged, so the counter runs past the per-tool
    # allowance of two before termination.
    assert event.output_retry_used == 4, event.output_retry_used
    assert [call.tool_name for call in event.call_shapes] == [
        "final_result_abstention",
        "final_result_confirmed",
    ]
    assert event.retry_prompt_targets == ("<output>",)
    # The abstention candidate is the one refused last, so it is named.
    assert event.tool_name == "final_result_abstention"
    # Every refused call in the response is charged, so the loop runs three
    # rounds and stops after the abstention tool spends its own allowance; the
    # recorded count is cumulative and therefore exceeds that allowance.
    assert _gates(result) == [
        (False, "UNRESOLVED_EVIDENCE_UNBOUND"),
        (False, "HYPOTHESIS_ASSESSMENT_INCOMPLETE"),
        (False, "UNRESOLVED_EVIDENCE_UNBOUND"),
        (False, "HYPOTHESIS_ASSESSMENT_INCOMPLETE"),
        (False, "UNRESOLVED_EVIDENCE_UNBOUND"),
        (True, "MODEL_PROTOCOL_ERROR"),
    ]


@pytest.mark.asyncio
async def test_repeating_the_same_tool_is_delivered_once(tmp_path: Path) -> None:
    """Two calls of one tool carrying the same valid payload: the call is delivered
    once and the run completes without charging the duplicate."""

    _write_public_run(tmp_path)

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
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
                        {"run_id": RUN_ID, **_BINDING},
                        tool_call_id="call-0",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                _call(agent_info, 0, _abstention(_bindable()), "final-1"),
                _call(agent_info, 0, _abstention(_bindable()), "final-2"),
            ]
        )

    result = await _run(tmp_path, FunctionModel(scripted))

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert result.metrics.model_requests == 2
    assert _gates(result) == [(True, "INSUFFICIENT_EVIDENCE")]
    assert _protocol_events(result) == []
