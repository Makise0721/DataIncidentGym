"""Attribution regressions for the two audit findings of 2026-09-11.

1. A historical retry must not promote a currently unattributable failure into
   that historical class (unknown tool after a business or output retry).
2. A mixed response that carries both a kernel-rejectable output and an
   unparseable business call cannot be attributed by the kernel verdict alone,
   in either call order.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
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

RUN_ID = "f" * 32
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


def _unknown_after_retry_model(history: str) -> FunctionModel:
    """Read the run, take one retry of the given kind, then send bad tool names."""

    state = {"step": 0}

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        state["step"] += 1
        step = state["step"]
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
        if step == 2:
            if history == "business":
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "get_dbt_run_results",
                            "}{ not json",
                            tool_call_id="bad-1",
                        )
                    ]
                )
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
                    "unregistered_function",
                    {"relation_name": "raw_payments"},
                    tool_call_id=f"unknown-{step}",
                )
            ]
        )

    return FunctionModel(scripted)


@pytest.mark.asyncio
async def test_unknown_tool_after_business_retry_stays_unknown(tmp_path: Path) -> None:
    """A business call was retried earlier; the current failure is a bad tool
    name, which says nothing about arguments."""

    _write_public_run(tmp_path)
    result, tools = await _run(tmp_path, _unknown_after_retry_model("business"))
    event = _protocol_event(result)

    assert event.error_origin == "UNKNOWN"
    assert event.retry_prompt_targets == ("get_dbt_run_results", "<unknown-field>")
    assert [call.tool_name for call in event.call_shapes] == ["unregistered_function"]
    assert tools.calls == ["get_dbt_run_results"]


@pytest.mark.asyncio
async def test_unknown_tool_after_output_retry_stays_unknown(tmp_path: Path) -> None:
    """The mirror case: an output retry in history must not label the current
    unknown-tool failure as an output failure either."""

    _write_public_run(tmp_path)
    result, tools = await _run(tmp_path, _unknown_after_retry_model("output"))
    event = _protocol_event(result)

    assert event.error_origin == "UNKNOWN"
    assert event.retry_prompt_targets == ("<output>", "<unknown-field>")
    assert [call.tool_name for call in event.call_shapes] == ["unregistered_function"]
    assert tools.calls == ["get_dbt_run_results"]


def _mixed_every_round_model(*, output_first: bool) -> FunctionModel:
    """Every round returns a kernel-rejectable output plus a bad business call."""

    state = {"step": 0}

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        state["step"] += 1
        step = state["step"]
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
        output_call = ToolCallPart(
            agent_info.output_tools[0].name,
            _kernel_invalid_payload(),
            tool_call_id=f"final-{step}",
        )
        bad_business = ToolCallPart(
            "get_dbt_run_results",
            "}{ not json",
            tool_call_id=f"bad-{step}",
        )
        parts = [output_call, bad_business] if output_first else [bad_business, output_call]
        return ModelResponse(parts=parts)

    return FunctionModel(scripted)


@pytest.mark.asyncio
@pytest.mark.parametrize("output_first", (True, False))
async def test_mixed_kernel_and_bad_business_is_unknown_in_both_orders(
    tmp_path: Path,
    output_first: bool,
) -> None:
    """Neither call order lets a kernel verdict name the terminating surface."""

    _write_public_run(tmp_path)
    result, tools = await _run(tmp_path, _mixed_every_round_model(output_first=output_first))
    event = _protocol_event(result)

    assert event.error_origin == "UNKNOWN"
    assert {call.arguments_parse for call in event.call_shapes} == {
        "OBJECT",
        "INVALID_JSON",
    }
    assert "<output>" in event.retry_prompt_targets
    assert "get_dbt_run_results" in event.retry_prompt_targets
    # The kernel really did reject decisions in this run.
    rejected = [
        getattr(item, "reason_code", None)
        for item in result.trace  # type: ignore[attr-defined]
        if getattr(item, "event_type", None) == "EVIDENCE_GATE"
        and not getattr(item, "accepted", True)
    ]
    assert rejected and set(rejected) == {"INSUFFICIENCY_GAP_REQUIRED"}
    assert tools.calls == ["get_dbt_run_results"]


@pytest.mark.asyncio
async def test_pure_kernel_rejection_without_business_call_is_kernel_decision(
    tmp_path: Path,
) -> None:
    """The retained counterpart: a kernel-only rejection still names the kernel,
    so the mixed-case rule did not swallow the precise attribution."""

    _write_public_run(tmp_path)
    state = {"step": 0}

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        state["step"] += 1
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
                    _kernel_invalid_payload(),
                    tool_call_id=f"final-{state['step']}",
                )
            ]
        )

    result, _ = await _run(tmp_path, FunctionModel(scripted))
    event = _protocol_event(result)

    assert event.error_origin == "KERNEL_DECISION"
    assert event.error_loc == ()
    assert event.output_retry_used == 2
    assert [call.arguments_parse for call in event.call_shapes] == ["OBJECT"]


def _mixed_with_typed_business_arguments() -> FunctionModel:
    """Output (kernel-invalid) plus a business call whose JSON is valid but whose
    argument type violates the tool schema."""

    state = {"step": 0}

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        state["step"] += 1
        step = state["step"]
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
                    _kernel_invalid_payload(),
                    tool_call_id=f"final-{step}",
                ),
                ToolCallPart(
                    "get_dbt_run_results",
                    {"run_id": []},
                    tool_call_id=f"badtype-{step}",
                ),
            ]
        )

    return FunctionModel(scripted)


@pytest.mark.asyncio
async def test_kernel_rejection_with_typed_business_arguments_is_unknown(
    tmp_path: Path,
) -> None:
    """Parseable JSON is not proof of valid arguments.

    A business call whose object decodes but breaks the tool's type contract can
    still be the terminating failure, so the kernel verdict cannot own the
    attribution when both call kinds are present.
    """

    _write_public_run(tmp_path)
    result, tools = await _run(tmp_path, _mixed_with_typed_business_arguments())
    event = _protocol_event(result)

    assert event.error_origin == "UNKNOWN"
    assert event.category == "OUTPUT_SCHEMA_REJECTED"
    # Both calls parsed as JSON objects; only the type contract differs.
    assert [call.arguments_parse for call in event.call_shapes] == ["OBJECT", "OBJECT"]
    assert {call.tool_name for call in event.call_shapes} == {
        "final_result",
        "get_dbt_run_results",
    }
    assert "<output>" in event.retry_prompt_targets
    assert "get_dbt_run_results" in event.retry_prompt_targets
    assert tools.calls == ["get_dbt_run_results"]


def _accepted_then_schema_failure_with_business() -> FunctionModel:
    """Round 2 is accepted; round 3 drops a required output field and mixes a
    business call with bad JSON."""

    state = {"step": 0}

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        state["step"] += 1
        step = state["step"]
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
        payload = _base_payload()
        if step > 2:
            del payload["summary"]
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    payload,
                    tool_call_id=f"final-{step}",
                ),
                ToolCallPart(
                    "get_dbt_run_results",
                    "}{ not json",
                    tool_call_id=f"bad-{step}",
                ),
            ]
        )

    return FunctionModel(scripted)


@pytest.mark.asyncio
async def test_accepted_earlier_does_not_excuse_a_current_schema_failure(
    tmp_path: Path,
) -> None:
    """An earlier acceptance is run state, not evidence about this response.

    The current output is missing a required field, so it cannot be excluded as a
    failure source just because an earlier round was accepted.
    """

    _write_public_run(tmp_path)
    result, _ = await _run(tmp_path, _accepted_then_schema_failure_with_business())
    event = _protocol_event(result)

    assert event.error_origin == "UNKNOWN"
    assert event.error_loc == ("summary",)
    assert event.error_kind == ("missing",)
    accepted_gates = [
        getattr(item, "reason_code", None)
        for item in result.trace  # type: ignore[attr-defined]
        if getattr(item, "event_type", None) == "EVIDENCE_GATE"
        and getattr(item, "accepted", False)
    ]
    # The earlier acceptance really happened, and it is still recorded.
    assert "INSUFFICIENT_EVIDENCE" in accepted_gates
