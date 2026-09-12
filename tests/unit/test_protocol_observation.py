"""Protocol-observation regressions: a terminal protocol failure records which
request produced it, what its calls looked like, and how the failure can be
attributed, so the four failure shapes below stay distinguishable offline.

The four shapes are the ones the v10 smoke batch could not separate:
output validation failure, business tool-argument failure, kernel-rejection
exhaustion, and a mixed response carrying both kinds of call.
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
from data_incident_gym.run_context import IncidentBrief

RUN_ID = "d" * 32
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
    def get_dbt_run_results(self, run_id: str):
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
        "run_id": RUN_ID,
        "assessments": [],
        "unresolved_evidence": [
            {
                "evidence_kind": "INGESTION_WATERMARK",
                "subject": "raw_payments",
            }
        ],
        "summary": "Synthetic decision.",
        "recommended_actions": [],
        "confidence": 0.2,
    }


def _kernel_invalid_payload() -> dict[str, object]:
    """Schema-valid but rejected by the kernel: nothing declared, gaps closed."""

    payload = _base_payload()
    payload["unresolved_evidence"] = []
    return payload


def _unexpected_field_payload() -> dict[str, object]:
    """An abstention that still carries claims: the tool has no such field, so
    the parser rejects it before any decision is built. The reason code is what
    makes the rejection readable, and it is keyed by error type rather than by
    the SDK's wording.
    """

    payload = _base_payload()
    payload["claims"] = [
        {
            "kind": "ROOT_CAUSE",
            "value": "SOURCE_REQUIRED_FIELD_NULL",
            "evidence_ids": ["ev_" + "0" * 64],
        }
    ]
    return payload


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


def _protocol_event(result: object):
    events = [
        event
        for event in result.trace  # type: ignore[attr-defined]
        if getattr(event, "event_type", None) == "MODEL_PROTOCOL"
    ]
    assert len(events) == 1
    return events[0]


def _shape_scripted(mode: str) -> FunctionModel:
    binding = {
        "kernel_hypothesis_ids": ["h_loss", "h_decline"],
        "kernel_new_hypotheses": [
            {"hypothesis_id": "h_loss", "root_cause_code": "SOURCE_PAYMENT_INGESTION_LOSS"},
            {"hypothesis_id": "h_decline", "root_cause_code": "NORMAL_BUSINESS_PAYMENT_DECLINE"},
        ],
    }
    state = {"step": 0}

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
        output_name = agent_info.output_tools[0].name
        if "call-0" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        {"run_id": RUN_ID, **binding},
                        tool_call_id="call-0",
                    )
                ]
            )
        step = state["step"]
        state["step"] = step + 1
        if mode == "output_validation":
            payload = _base_payload()
            del payload["summary"]
            return ModelResponse(
                parts=[ToolCallPart(output_name, payload, tool_call_id=f"final-{step}")]
            )
        if mode == "business_arguments":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        "}{ not json",
                        tool_call_id=f"bad-{step}",
                    )
                ]
            )
        if mode == "unexpected_field":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        output_name,
                        _unexpected_field_payload(),
                        tool_call_id=f"final-{step}",
                    )
                ]
            )
        if mode == "normalization_failure":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        output_name,
                        {**_base_payload(), "summary": "   "},
                        tool_call_id=f"final-{step}",
                    )
                ]
            )
        if mode == "kernel_rejection_exhaustion":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        output_name,
                        _kernel_invalid_payload(),
                        tool_call_id=f"final-{step}",
                    )
                ]
            )
        # mixed: a valid output call next to a business call with bad arguments
        return ModelResponse(
            parts=[
                ToolCallPart(output_name, _base_payload(), tool_call_id=f"final-{step}"),
                ToolCallPart("get_dbt_run_results", "}{ not json", tool_call_id=f"bad-{step}"),
            ]
        )

    return FunctionModel(scripted)


@pytest.mark.asyncio
async def test_output_validation_failure_is_attributed_to_output(tmp_path: Path) -> None:
    """Shape 1: the structured output itself fails schema validation."""

    _write_public_run(tmp_path)
    result = await _run(tmp_path, _shape_scripted("output_validation"))
    event = _protocol_event(result)

    assert event.category == "OUTPUT_SCHEMA_REJECTED"
    assert event.error_origin == "OUTPUT_VALIDATION"
    assert event.error_type == "UNEXPECTED_MODEL_BEHAVIOR"
    assert event.error_loc == ("summary",)
    assert event.error_kind == ("missing",)
    assert event.response_ended_with == "OUTPUT_CALL"
    assert event.retry_prompt_targets == ("<output>",)
    assert [(call.tool_name, call.arguments_parse) for call in event.call_shapes] == [
        ("final_result_abstention", "OBJECT")
    ]
    # The decision never became schema-valid, so the kernel output validator was
    # never reached and no output retry was recorded through it.
    assert event.output_retry_used is None


@pytest.mark.asyncio
async def test_unexpected_field_records_a_classifiable_reason(tmp_path: Path) -> None:
    """The abstention tool has no claim fields, so sending one is a parser-level
    rejection: the location names the field and the reason code comes from the
    type-keyed table. This is what the v14 seq19 case looks like now that the
    illegal combination is no longer expressible."""

    _write_public_run(tmp_path)
    result = await _run(tmp_path, _shape_scripted("unexpected_field"))
    event = _protocol_event(result)

    assert event.category == "OUTPUT_SCHEMA_REJECTED"
    assert event.error_origin == "OUTPUT_VALIDATION"
    assert event.error_loc == ("claims",)
    assert event.error_kind == ("extra_forbidden",)
    assert event.error_reason == ("UNEXPECTED_DECISION_FIELD",)
    # Only the code is recorded: no payload text, no parser message.
    assert all(" " not in reason for reason in event.error_reason)


@pytest.mark.asyncio
async def test_normalization_failure_keeps_its_code_in_the_persisted_event(
    tmp_path: Path,
) -> None:
    """A structurally valid submission can still map onto an invalid decision.
    The failure is recorded with its stable code and the output origin instead of
    being recomputed as UNKNOWN with no reason."""

    _write_public_run(tmp_path)
    result = await _run(tmp_path, _shape_scripted("normalization_failure"))
    event = _protocol_event(result)

    assert event.category == "OUTPUT_SCHEMA_REJECTED"
    assert event.error_origin == "OUTPUT_VALIDATION"
    assert event.error_type == "UNEXPECTED_MODEL_BEHAVIOR"
    assert event.error_reason == ("DECISION_TEXT_BLANK",)
    assert event.error_loc == ()
    assert event.error_kind == ("value_error",)


@pytest.mark.asyncio
async def test_business_argument_failure_is_attributed_to_the_tool(tmp_path: Path) -> None:
    """Shape 2: a business tool call's arguments do not parse."""

    _write_public_run(tmp_path)
    result = await _run(tmp_path, _shape_scripted("business_arguments"))
    event = _protocol_event(result)

    assert event.category == "TOOL_ARGUMENT_REJECTED"
    assert event.error_origin == "BUSINESS_TOOL_ARGUMENTS"
    assert event.tool_name == "get_dbt_run_results"
    assert event.response_ended_with == "BUSINESS_CALL"
    assert event.retry_prompt_targets == ("get_dbt_run_results",)
    assert [(call.tool_name, call.arguments_parse) for call in event.call_shapes] == [
        ("get_dbt_run_results", "INVALID_JSON")
    ]


@pytest.mark.asyncio
async def test_kernel_rejection_exhaustion_is_distinguishable(tmp_path: Path) -> None:
    """Shape 3: every decision was schema-valid; only the kernel rejected them.

    This is the shape the v10 smoke batch could not separate from a real schema
    failure, because both recorded an empty ``error_loc``. The origin keeps the
    precise verdict the validator reached: the kernel refused the decision.
    """

    _write_public_run(tmp_path)
    result = await _run(tmp_path, _shape_scripted("kernel_rejection_exhaustion"))
    event = _protocol_event(result)

    assert event.error_loc == ()
    assert event.error_kind == ()
    assert event.error_origin == "KERNEL_DECISION"
    assert event.output_retry_used == 2
    assert event.retry_prompt_targets == ("<output>",)
    assert [call.arguments_parse for call in event.call_shapes] == ["OBJECT"]
    gate_codes = [
        getattr(event_item, "reason_code", None)
        for event_item in result.trace  # type: ignore[attr-defined]
        if getattr(event_item, "event_type", None) == "EVIDENCE_GATE"
        and not getattr(event_item, "accepted", True)
    ]
    # Three kernel rejections: the initial attempt plus two output retries.
    assert gate_codes == ["INSUFFICIENCY_GAP_REQUIRED"] * 3


@pytest.mark.asyncio
async def test_mixed_response_separates_category_from_origin(tmp_path: Path) -> None:
    """Shape 4: a structured-output call next to a business call with bad JSON.

    Both the output validator and the argument parser were involved in this
    attempt, so the closing response cannot prove which one terminated it. The
    origin stays UNKNOWN while ``category`` keeps its legacy value; keeping both
    fields is what makes that difference visible instead of trusting either one.
    """

    _write_public_run(tmp_path)
    result = await _run(tmp_path, _shape_scripted("mixed"))
    event = _protocol_event(result)

    assert event.category == "OUTPUT_SCHEMA_REJECTED"
    assert event.error_origin == "UNKNOWN"
    assert event.retry_prompt_targets == ("get_dbt_run_results",)
    assert event.response_ended_with == "BUSINESS_CALL"
    shapes = [(call.tool_name, call.arguments_parse) for call in event.call_shapes]
    assert shapes == [
        ("final_result_abstention", "OBJECT"),
        ("get_dbt_run_results", "INVALID_JSON"),
    ]
    # No output-retry budget was spent: the decision itself was schema-valid.
    assert event.output_retry_used == 0


@pytest.mark.asyncio
async def test_output_retry_count_does_not_leak_from_a_previous_round(tmp_path: Path) -> None:
    """Round 2 fails before output validation, so it must not report round 1's
    retry count as its own."""

    _write_public_run(tmp_path)
    binding = {
        "kernel_hypothesis_ids": ["h_loss", "h_decline"],
        "kernel_new_hypotheses": [
            {"hypothesis_id": "h_loss", "root_cause_code": "SOURCE_PAYMENT_INGESTION_LOSS"},
            {"hypothesis_id": "h_decline", "root_cause_code": "NORMAL_BUSINESS_PAYMENT_DECLINE"},
        ],
    }
    state = {"step": 0}

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
        output_name = agent_info.output_tools[0].name
        if "call-0" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        {"run_id": RUN_ID, **binding},
                        tool_call_id="call-0",
                    )
                ]
            )
        step = state["step"]
        state["step"] = step + 1
        if step == 0:
            # Reaches output validation, so this round records a retry count.
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        output_name,
                        _kernel_invalid_payload(),
                        tool_call_id="final-0",
                    )
                ]
            )
        # Fails at business argument parsing: output validation never runs.
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "get_dbt_run_results",
                    "}{ not json",
                    tool_call_id=f"bad-{step}",
                )
            ]
        )

    result = await _run(tmp_path, FunctionModel(scripted))
    event = _protocol_event(result)

    assert event.category == "TOOL_ARGUMENT_REJECTED"
    assert event.error_origin == "BUSINESS_TOOL_ARGUMENTS"
    assert event.output_retry_used is None
    assert all(
        call.arguments_parse == "INVALID_JSON" for call in event.call_shapes
    )


def test_protocol_observation_fields_are_additive_defaults() -> None:
    """Legacy MODEL_PROTOCOL records parse with the observation fields empty."""

    from data_incident_gym.diagnosis import ModelProtocolTraceEvent

    legacy = ModelProtocolTraceEvent.model_validate(
        {
            "event_type": "MODEL_PROTOCOL",
            "stage": "OUTPUT_SCHEMA_VALIDATION",
            "tool_name": "final_result",
            "category": "OUTPUT_SCHEMA_REJECTED",
        }
    )

    assert legacy.model_request_index is None
    assert legacy.output_retry_used is None
    assert legacy.call_shapes == ()
    assert legacy.response_ended_with is None
    assert legacy.error_type is None
    assert legacy.error_origin is None
    assert legacy.retry_prompt_targets == ()


# ------------------------------- candidate attribution and normalization duty

_ABSTENTION_TOOL, _CONFIRMED_TOOL = 0, 1
_BINDING = {
    "kernel_hypothesis_ids": ["h_loss", "h_decline"],
    "kernel_new_hypotheses": [
        {"hypothesis_id": "h_loss", "root_cause_code": "SOURCE_PAYMENT_INGESTION_LOSS"},
        {"hypothesis_id": "h_decline", "root_cause_code": "NORMAL_BUSINESS_PAYMENT_DECLINE"},
    ],
}
_EVIDENCE_ID = "ev_" + "0" * 64


def _abstention(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "run_id": RUN_ID,
        "assessments": [],
        "unresolved_evidence": [
            {"evidence_kind": "INGESTION_WATERMARK", "subject": "raw_payments"}
        ],
        "summary": "The settled boundary is unobservable.",
        "recommended_actions": [],
        "confidence": 0.2,
    }
    payload.update(overrides)
    return payload


def _confirmed(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "run_id": RUN_ID,
        "selected_hypothesis_id": "h_loss",
        "assessments": [],
        "claims": [
            {
                "kind": "ROOT_CAUSE",
                "value": "SOURCE_PAYMENT_INGESTION_LOSS",
                "evidence_ids": [_EVIDENCE_ID],
            }
        ],
        "summary": "One root cause is supported.",
        "recommended_actions": [],
        "confidence": 0.9,
    }
    payload.update(overrides)
    return payload


def _round_scripted(*rounds: tuple[tuple[int, dict[str, object]], ...]) -> FunctionModel:
    """One response per request, each carrying the given (tool, payload) calls."""

    state = {"index": -1}

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        state["index"] += 1
        if state["index"] == 0:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        {"run_id": RUN_ID, **_BINDING},
                        tool_call_id="call-0",
                    )
                ]
            )
        position = min(state["index"] - 1, len(rounds) - 1)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[tool].name,
                    payload,
                    tool_call_id=f"final-{position}-{tool}",
                )
                for tool, payload in rounds[position]
            ]
        )

    return FunctionModel(scripted)


def _unbound_abstention() -> dict[str, object]:
    """A subject no accepted record can bind, so the kernel refuses it."""

    return _abstention(
        unresolved_evidence=[{"evidence_kind": "INGESTION_WATERMARK", "subject": "raw_orders"}]
    )


_NORMALIZATION_FAILURES = {
    "DECISION_TEXT_BLANK": (_ABSTENTION_TOOL, _abstention(summary="   ")),
    "RECOMMENDED_ACTIONS_DUPLICATED": (
        _ABSTENTION_TOOL,
        _abstention(recommended_actions=["Act.", "Act."]),
    ),
    "ASSESSMENT_HYPOTHESES_DUPLICATED": (
        _ABSTENTION_TOOL,
        _abstention(
            assessments=[
                {"hypothesis_id": "h_loss", "verdict": "SUPPORTED", "evidence_ids": [_EVIDENCE_ID]},
                {"hypothesis_id": "h_loss", "verdict": "REFUTED", "evidence_ids": [_EVIDENCE_ID]},
            ]
        ),
    ),
    "CLAIM_VALUES_DUPLICATED": (
        _CONFIRMED_TOOL,
        _confirmed(
            claims=[
                {
                    "kind": "ROOT_CAUSE",
                    "value": "SOURCE_PAYMENT_INGESTION_LOSS",
                    "evidence_ids": [_EVIDENCE_ID],
                },
                {
                    "kind": "ROOT_CAUSE",
                    "value": "SOURCE_PAYMENT_INGESTION_LOSS",
                    "evidence_ids": [_EVIDENCE_ID],
                },
            ]
        ),
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("code", sorted(_NORMALIZATION_FAILURES))
async def test_each_normalization_failure_keeps_its_code_in_the_event(
    tmp_path: Path, code: str
) -> None:
    """A structurally valid submission whose decision is refused is recorded with
    its own code, the tool it came from, and the output origin."""

    _write_public_run(tmp_path)
    tool, payload = _NORMALIZATION_FAILURES[code]
    result = await _run(tmp_path, _round_scripted(((tool, payload),)))

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    event = _protocol_event(result)
    assert event.category == "OUTPUT_SCHEMA_REJECTED"
    assert event.error_origin == "OUTPUT_VALIDATION"
    assert event.error_reason == (code,)
    assert event.tool_name == (
        "final_result_confirmed" if tool == _CONFIRMED_TOOL else "final_result_abstention"
    )


@pytest.mark.asyncio
async def test_a_normalization_failure_does_not_survive_a_later_success(tmp_path: Path) -> None:
    """The refusal is answered by a valid submission in the next request, so the
    run completes and no protocol failure is recorded."""

    _write_public_run(tmp_path)
    result = await _run(
        tmp_path,
        _round_scripted(
            ((_ABSTENTION_TOOL, _abstention(summary="   ")),),
            ((_ABSTENTION_TOOL, _abstention()),),
        ),
    )

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert [
        event
        for event in result.trace
        if getattr(event, "event_type", None) == "MODEL_PROTOCOL"
    ] == []


@pytest.mark.asyncio
async def test_a_later_kernel_verdict_does_not_inherit_earlier_details(tmp_path: Path) -> None:
    """A kernel rejection in a later request owns the attribution, so the earlier
    normalization details are not paired with it."""

    _write_public_run(tmp_path)
    result = await _run(
        tmp_path,
        _round_scripted(
            ((_ABSTENTION_TOOL, _abstention(summary="   ")),),
            ((_ABSTENTION_TOOL, _unbound_abstention()),),
        ),
    )

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    event = _protocol_event(result)
    assert event.error_origin == "KERNEL_DECISION"
    assert event.error_reason == ()
    assert event.error_loc == ()
    assert event.tool_name == "final_result_abstention"


@pytest.mark.asyncio
async def test_a_kernel_verdict_does_not_own_a_later_candidate_failure(tmp_path: Path) -> None:
    """The audited sequence: the confirmed tool's retries are spent first, then one
    response carries an unbound abstention followed by another blank confirmed.
    The kernel verdict is refused before the normalization failure, so the event
    must be the latter's — never the kernel verdict carrying its details."""

    _write_public_run(tmp_path)
    result = await _run(
        tmp_path,
        _round_scripted(
            ((_CONFIRMED_TOOL, _confirmed(summary="   ")),),
            ((_CONFIRMED_TOOL, _confirmed(summary="   ")),),
            (
                (_ABSTENTION_TOOL, _unbound_abstention()),
                (_CONFIRMED_TOOL, _confirmed(summary="   ")),
            ),
        ),
    )

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    event = _protocol_event(result)
    assert event.error_origin == "OUTPUT_VALIDATION"
    assert event.error_reason == ("DECISION_TEXT_BLANK",)
    assert event.tool_name == "final_result_confirmed"


@pytest.mark.asyncio
@pytest.mark.parametrize("kernel_first", [True, False])
async def test_two_candidates_in_one_response_do_not_share_attribution(
    tmp_path: Path, kernel_first: bool
) -> None:
    """When one candidate is refused by the kernel and another fails
    normalization, the event names the candidate that ended the request."""

    _write_public_run(tmp_path)
    # The two candidates come through different tools, so a request-level marker
    # could otherwise let one tool's verdict name the other tool's failure.
    kernel_candidate = (_ABSTENTION_TOOL, _unbound_abstention())
    blank_candidate = (_CONFIRMED_TOOL, _confirmed(summary="   "))
    rounds = (
        (kernel_candidate, blank_candidate)
        if kernel_first
        else (blank_candidate, kernel_candidate)
    )
    result = await _run(tmp_path, _round_scripted(rounds))

    event = _protocol_event(result)
    if kernel_first:
        # The abstention candidate is refused last, so its verdict names the
        # failure and the confirmed candidate's details are not attached to it.
        assert event.error_origin == "KERNEL_DECISION"
        assert event.error_reason == ()
        assert event.tool_name == "final_result_abstention"
    else:
        # Here the blank confirmed candidate is refused last; the triple stays
        # consistent with that candidate.
        assert event.error_origin == "OUTPUT_VALIDATION"
        assert event.error_reason == ("DECISION_TEXT_BLANK",)
        assert event.tool_name == "final_result_confirmed"
    # Either way the origin, the details and the tool come from one candidate.
    assert (event.error_reason == ()) is (event.error_origin == "KERNEL_DECISION")


@pytest.mark.asyncio
async def test_a_pre_validated_candidate_does_not_own_a_later_candidates_failure(
    tmp_path: Path,
) -> None:
    """The audited sequence: two schema-level candidates in one response, where
    the SDK ends the request on the second. Neither is located by the response
    alone, so the event keeps the call shapes and states nothing else."""

    _write_public_run(tmp_path)
    missing_selection = {
        key: value
        for key, value in _confirmed().items()
        if key != "selected_hypothesis_id"
    }
    result = await _run(
        tmp_path,
        _round_scripted(
            ((_CONFIRMED_TOOL, missing_selection),),
            ((_CONFIRMED_TOOL, missing_selection),),
            (
                (_ABSTENTION_TOOL, _abstention(claims=[])),
                (_CONFIRMED_TOOL, missing_selection),
            ),
        ),
    )

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    event = _protocol_event(result)
    assert event.category == "OUTPUT_SCHEMA_REJECTED"
    # The first candidate's pre-validation details must not speak for a request
    # that ended on the second one.
    assert event.error_origin == "UNKNOWN"
    assert event.error_loc == ()
    assert event.error_kind == ()
    assert event.error_reason == ()
    assert event.tool_name is None
    assert [call.tool_name for call in event.call_shapes] == [
        "final_result_abstention",
        "final_result_confirmed",
    ]
