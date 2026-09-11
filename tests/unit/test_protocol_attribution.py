"""Protocol-attribution regressions for the request-isolation and origin fixes.

Five groups, each driven through a real DiagnosisRunner + FunctionModel:

1. provider failure isolation (round 2 raises; round 1's shape must not appear)
2. first-request provider failure (no fabricated response)
3. kernel rejection followed by a provider failure (no leakage)
4. unregistered tool name (tool-choice failure is not an argument failure)
5. cross-surface history (a previously retried surface never overrides the
   current response's contrary evidence)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from data_incident_gym.diagnosis import DiagnosticStrategy
from data_incident_gym.diagnostic_agent import DiagnosisRunner, ModelIdentity
from data_incident_gym.evidence import (
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
)
from data_incident_gym.run_context import IncidentBrief

RUN_ID = "e" * 32
MODEL_BASE_URL = "http://127.0.0.1:11434/v1"


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
    brief = IncidentBrief(
        schema_version="incident_brief.v1",
        signal_code="DBT_BUILD_FAILED",
        summary="A pipeline evidence review is requested.",
        subjects=("raw_payments",),
        logical_observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        observations=(),
    )
    (run_root / "incident_brief.json").write_text(brief.model_dump_json(), encoding="utf-8")


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        model_base_url=MODEL_BASE_URL,
        model_name="synthetic-model",
        model_api_key=SimpleNamespace(get_secret_value=lambda: "synthetic-key"),
    )


class _Tools:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_dbt_run_results(self, run_id: str):
        self.calls.append("get_dbt_run_results")
        return (
            EvidenceRecord.create(
                run_id=run_id,
                evidence_type=EvidenceType.DBT_RUN_RESULTS,
                source=EvidenceSource.DBT_RUN_RESULTS,
                subject=run_id,
                observed_at=datetime(2026, 8, 30, tzinfo=UTC),
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


def _base_payload() -> dict[str, object]:
    return {
        "schema_version": "p1.kernel_decision.v1",
        "status": "INSUFFICIENT_EVIDENCE",
        "run_id": RUN_ID,
        "selected_hypothesis_id": None,
        "assessments": [],
        "claims": [],
        "unresolved_evidence": [
            {
                "evidence_kind": "INGESTION_WATERMARK",
                "subject": "raw_payments",
                "reason_code": "NOT_OBSERVABLE",
            }
        ],
        "summary": "Synthetic decision.",
        "recommended_actions": [],
        "confidence": 0.2,
    }


def _kernel_invalid_payload() -> dict[str, object]:
    payload = _base_payload()
    payload["unresolved_evidence"] = []
    return payload


def _binding() -> dict[str, object]:
    return {
        "kernel_hypothesis_ids": ["h_loss", "h_decline"],
        "kernel_new_hypotheses": [
            {"hypothesis_id": "h_loss", "root_cause_code": "SOURCE_PAYMENT_INGESTION_LOSS"},
            {"hypothesis_id": "h_decline", "root_cause_code": "NORMAL_BUSINESS_PAYMENT_DECLINE"},
        ],
    }


async def _run(tmp_path: Path, model: FunctionModel) -> tuple[object, _Tools]:
    tools = _Tools()
    runner = DiagnosisRunner.for_run(
        RUN_ID,
        _settings(),
        DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        tmp_path,
        model=model,
        tools=tools,  # type: ignore[arg-type]
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )
    return await runner.diagnose(), tools


def _protocol_event(result: object):
    events = [
        event
        for event in result.trace  # type: ignore[attr-defined]
        if getattr(event, "event_type", None) == "MODEL_PROTOCOL"
    ]
    assert len(events) == 1
    return events[0]


def _provider_error() -> ModelHTTPError:
    return ModelHTTPError(status_code=500, model_name="synthetic-model", body=None)


@pytest.mark.asyncio
async def test_provider_failure_does_not_inherit_the_previous_response(
    tmp_path: Path,
) -> None:
    """Round 1 returns a business call; round 2 raises. The event must carry
    round 2's attempt index and no response of its own."""

    _write_public_run(tmp_path)
    state = {"calls": 0}

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        state["calls"] += 1
        if state["calls"] == 2:
            raise _provider_error()
        if "call-0" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        {"run_id": RUN_ID, **_binding()},
                        tool_call_id="call-0",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    _base_payload(),
                    tool_call_id="final",
                )
            ]
        )

    result, tools = await _run(tmp_path, FunctionModel(scripted))
    event = _protocol_event(result)

    assert result.diagnosis.status is not None
    assert event.category == "PROVIDER_PROTOCOL_FAILURE"
    assert event.error_origin == "PROVIDER"
    assert event.model_request_index == 2
    assert event.call_shapes == ()
    assert event.response_ended_with is None
    assert event.output_retry_used is None
    # The first round's business call really did run, so this is not an
    # artifact of the request never happening.
    assert tools.calls == ["get_dbt_run_results"]


@pytest.mark.asyncio
async def test_first_request_provider_failure_reports_attempt_one(tmp_path: Path) -> None:
    _write_public_run(tmp_path)

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        raise _provider_error()

    result, tools = await _run(tmp_path, FunctionModel(scripted))
    event = _protocol_event(result)

    assert event.category == "PROVIDER_PROTOCOL_FAILURE"
    assert event.error_origin == "PROVIDER"
    assert event.model_request_index == 1
    assert event.call_shapes == ()
    assert event.response_ended_with is None
    assert tools.calls == []


@pytest.mark.asyncio
async def test_kernel_rejection_then_provider_failure_does_not_leak(tmp_path: Path) -> None:
    """A rejection in an earlier attempt must not surface as the origin, nor
    leave its validator retry count on the failing attempt."""

    _write_public_run(tmp_path)
    state = {"calls": 0}

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        state["calls"] += 1
        if "call-0" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        {"run_id": RUN_ID, **_binding()},
                        tool_call_id="call-0",
                    )
                ]
            )
        if state["calls"] == 3:
            raise _provider_error()
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    _kernel_invalid_payload(),
                    tool_call_id=f"final-{state['calls']}",
                )
            ]
        )

    result, _ = await _run(tmp_path, FunctionModel(scripted))
    event = _protocol_event(result)

    assert event.category == "PROVIDER_PROTOCOL_FAILURE"
    assert event.error_origin == "PROVIDER"
    assert event.model_request_index == 3
    assert event.output_retry_used is None
    assert event.call_shapes == ()
    # The earlier rejection is still visible in the trace, just not as this
    # failure's origin.
    rejected_gates = [
        getattr(item, "reason_code", None)
        for item in result.trace  # type: ignore[attr-defined]
        if getattr(item, "event_type", None) == "EVIDENCE_GATE"
        and not getattr(item, "accepted", True)
    ]
    assert rejected_gates == ["INSUFFICIENCY_GAP_REQUIRED"]


@pytest.mark.asyncio
async def test_unregistered_tool_name_is_not_an_argument_failure(tmp_path: Path) -> None:
    """A wrong or hallucinated tool name proves nothing about arguments."""

    _write_public_run(tmp_path)

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "get_relation_forecast",
                    {"relation_name": "raw_payments"},
                    tool_call_id="unknown-1",
                )
            ]
        )

    result, tools = await _run(tmp_path, FunctionModel(scripted))
    event = _protocol_event(result)

    assert event.error_origin == "UNKNOWN"
    assert event.retry_prompt_targets == ("<unknown-field>",)
    assert event.model_request_index == 2
    assert tools.calls == []


@pytest.mark.asyncio
async def test_business_retry_history_does_not_override_current_output_failure(
    tmp_path: Path,
) -> None:
    """A business retry happened earlier, but the closing response is a pure
    output call whose schema fails: the current evidence wins."""

    _write_public_run(tmp_path)
    state = {"calls": 0}

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        state["calls"] += 1
        if "call-0" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        {"run_id": RUN_ID, **_binding()},
                        tool_call_id="call-0",
                    )
                ]
            )
        if state["calls"] == 2:
            # Business call with unparseable arguments: creates a business retry.
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        "}{ not json",
                        tool_call_id="bad-1",
                    )
                ]
            )
        # Closing response: a single output call missing a required field.
        payload = _base_payload()
        del payload["summary"]
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    payload,
                    tool_call_id=f"final-{state['calls']}",
                )
            ]
        )

    result, _ = await _run(tmp_path, FunctionModel(scripted))
    event = _protocol_event(result)

    assert event.error_origin == "OUTPUT_VALIDATION"
    assert event.error_loc == ("summary",)
    assert event.error_kind == ("missing",)
    assert "<output>" in event.retry_prompt_targets
    assert "get_dbt_run_results" in event.retry_prompt_targets
    assert event.response_ended_with == "OUTPUT_CALL"


@pytest.mark.asyncio
async def test_output_retry_history_does_not_override_current_business_failure(
    tmp_path: Path,
) -> None:
    """The mirror case: an output retry happened earlier, but the closing
    response is an unparseable business call."""

    _write_public_run(tmp_path)
    state = {"calls": 0}

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        state["calls"] += 1
        if "call-0" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        {"run_id": RUN_ID, **_binding()},
                        tool_call_id="call-0",
                    )
                ]
            )
        if state["calls"] == 2:
            payload = _base_payload()
            del payload["summary"]
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        agent_info.output_tools[0].name,
                        payload,
                        tool_call_id="final-1",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "get_dbt_run_results",
                    "}{ not json",
                    tool_call_id=f"bad-{state['calls']}",
                )
            ]
        )

    result, _ = await _run(tmp_path, FunctionModel(scripted))
    event = _protocol_event(result)

    assert event.error_origin == "BUSINESS_TOOL_ARGUMENTS"
    assert event.response_ended_with == "BUSINESS_CALL"
    assert [call.arguments_parse for call in event.call_shapes] == ["INVALID_JSON"]
    assert "<output>" in event.retry_prompt_targets
    assert "get_dbt_run_results" in event.retry_prompt_targets
