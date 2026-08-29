from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from data_incident_gym.diagnosis import (
    Diagnosis,
    DiagnosisRunResult,
    ModelProtocolTraceEvent,
    ToolTraceEvent,
)
from data_incident_gym.diagnostic_agent import (
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_SHA256,
    SYSTEM_PROMPT_VERSION,
)
from data_incident_gym.diagnostic_kernel import (
    DiagnosticKernel,
    KernelFinalStatus,
    KernelStateTraceEvent,
)

RUN_ID = "0123456789abcdef0123456789abcdef"
CASE_ID = "schema_rename_payment_amount"
EVIDENCE_ID = "ev_" + "a" * 64
ONTOLOGY = (
    "SOURCE_SCHEMA_COLUMN_RENAMED",
    "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
)


def confirmed_payload() -> dict[str, object]:
    return {
        "status": "CONFIRMED",
        "incident_case_id": CASE_ID,
        "run_id": RUN_ID,
        "root_cause_code": "SOURCE_SCHEMA_COLUMN_RENAMED",
        "summary": "The source column changed while a consumer still references the old name.",
        "affected_assets": ("stg_payments", "orders"),
        "evidence_ids": (EVIDENCE_ID,),
        "recommended_actions": ("Restore the consumer reference.",),
        "confidence": 0.9,
    }


def assert_invalid(changes: dict[str, object]) -> None:
    payload = confirmed_payload()
    payload.update(changes)
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(payload)


def test_confirmed_requires_root_assets_evidence_and_actions() -> None:
    for field in ("root_cause_code", "affected_assets", "evidence_ids", "recommended_actions"):
        payload = confirmed_payload()
        payload[field] = None if field == "root_cause_code" else ()
        with pytest.raises(ValidationError):
            Diagnosis.model_validate(payload)


@pytest.mark.parametrize("status", ["INSUFFICIENT_EVIDENCE", "MODEL_ERROR"])
def test_nonconfirmed_status_rejects_unproven_claims(status: str) -> None:
    payload = confirmed_payload()
    payload.update(
        {
            "status": status,
            "root_cause_code": None,
            "affected_assets": (),
            "summary": "The available evidence does not support a confirmed diagnosis.",
        }
    )
    payload["root_cause_code"] = "SOURCE_SCHEMA_COLUMN_RENAMED"
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(payload)


def test_model_error_accepts_only_fixed_safe_reason_code() -> None:
    payload = confirmed_payload()
    payload.update(
        {
            "status": "MODEL_ERROR",
            "root_cause_code": None,
            "affected_assets": (),
            "summary": "MODEL_RUNTIME_ERROR",
        }
    )
    assert Diagnosis.model_validate(payload).summary == "MODEL_RUNTIME_ERROR"
    payload["summary"] = "MODEL_RUNTIME_ERROR TEST_REDACTED_VALUE"
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(payload)


def test_diagnosis_rejects_extra_fields_duplicate_ids_and_coercion() -> None:
    assert_invalid({"unexpected": "value"})
    assert_invalid({"evidence_ids": (EVIDENCE_ID, EVIDENCE_ID)})
    assert_invalid({"affected_assets": ("orders", "orders")})
    assert_invalid({"recommended_actions": ("same", "same")})
    assert_invalid({"confidence": "0.9"})
    assert_invalid({"confidence": 1})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("incident_case_id", ""),
        ("incident_case_id", "bad case"),
        ("run_id", "not-a-run"),
        ("root_cause_code", "not_upper_snake"),
        ("summary", "   "),
        ("affected_assets", ("orders", " ")),
        ("recommended_actions", (" ",)),
    ],
)
def test_diagnosis_rejects_invalid_strings(field: str, value: object) -> None:
    assert_invalid({field: value})


@pytest.mark.parametrize("confidence", [-0.01, 1.01, float("nan"), float("inf")])
def test_confidence_is_a_finite_float_between_zero_and_one(confidence: float) -> None:
    assert_invalid({"confidence": confidence})


def test_diagnosis_is_frozen() -> None:
    diagnosis = Diagnosis.model_validate(confirmed_payload())
    with pytest.raises(ValidationError):
        diagnosis.status = "MODEL_ERROR"  # type: ignore[misc]


def test_kernel_decision_is_the_only_model_output_contract() -> None:
    from data_incident_gym.diagnostic_kernel import (
        ClaimEvidence,
        ClaimKind,
        HypothesisAssessment,
        HypothesisVerdict,
        KernelDecision,
    )

    decision = KernelDecision(
        status="CONFIRMED",
        incident_case_id=CASE_ID,
        run_id=RUN_ID,
        selected_hypothesis_id="h_root",
        assessments=(
            HypothesisAssessment(
                hypothesis_id="h_root",
                verdict=HypothesisVerdict.SUPPORTED,
                evidence_ids=(EVIDENCE_ID,),
            ),
        ),
        claims=(
            ClaimEvidence(
                kind=ClaimKind.ROOT_CAUSE,
                value="SOURCE_SCHEMA_COLUMN_RENAMED",
                evidence_ids=(EVIDENCE_ID,),
            ),
        ),
        summary="A structured decision with explicit claim evidence.",
        recommended_actions=("Keep the source contract aligned.",),
        confidence=0.8,
    )
    assert set(decision.model_dump()) == {
        "status",
        "incident_case_id",
        "run_id",
        "selected_hypothesis_id",
        "assessments",
        "claims",
        "summary",
        "recommended_actions",
        "confidence",
    }
    with pytest.raises(ValidationError):
        decision.status = "INSUFFICIENT_EVIDENCE"  # type: ignore[misc]


def test_kernel_decision_rejects_controller_fields_and_bad_status_shape() -> None:
    from data_incident_gym.diagnostic_kernel import KernelDecision

    base = {
        "status": "INSUFFICIENT_EVIDENCE",
        "incident_case_id": CASE_ID,
        "run_id": RUN_ID,
        "selected_hypothesis_id": None,
        "assessments": (),
        "claims": (),
        "summary": "More evidence is required.",
        "recommended_actions": ("Collect source evidence.",),
        "confidence": 0.2,
    }
    with pytest.raises(ValidationError):
        KernelDecision.model_validate({**base, "affected_assets": ()})
    with pytest.raises(ValidationError):
        KernelDecision.model_validate(
            {**base, "selected_hypothesis_id": "h_root", "claims": ()}
        )


def test_kernel_decision_rejects_blank_recommended_actions() -> None:
    from data_incident_gym.diagnostic_kernel import KernelDecision

    with pytest.raises(ValidationError, match="recommended_actions"):
        KernelDecision.model_validate(
            {
                "status": "INSUFFICIENT_EVIDENCE",
                "incident_case_id": CASE_ID,
                "run_id": RUN_ID,
                "selected_hypothesis_id": None,
                "assessments": (),
                "claims": (),
                "summary": "More evidence is required.",
                "recommended_actions": ("",),
                "confidence": 0.2,
            }
        )


def test_diagnosis_run_result_contains_terminal_kernel_state() -> None:
    kernel = DiagnosticKernel.start(
        incident_case_id=CASE_ID,
        run_id=RUN_ID,
        allowed_root_cause_codes=ONTOLOGY,
        model_request_limit=8,
        tool_call_limit=8,
    )
    state = kernel.snapshot(model_requests_used=0).model_copy(
        update={
            "final_status": KernelFinalStatus.INSUFFICIENT_EVIDENCE,
            "gate_reason": "INSUFFICIENT_EVIDENCE",
        }
    )
    diagnosis = Diagnosis.model_validate(
        {
            "status": "INSUFFICIENT_EVIDENCE",
            "incident_case_id": CASE_ID,
            "run_id": RUN_ID,
            "root_cause_code": None,
            "summary": "The available evidence is insufficient.",
            "affected_assets": (),
            "evidence_ids": (),
            "recommended_actions": ("Collect additional evidence.",),
            "confidence": 0.2,
        }
    )
    result = DiagnosisRunResult.model_validate(
        {
            "diagnosis": diagnosis,
            "evidence_records": (),
            "trace": (KernelStateTraceEvent(event_type="KERNEL_STATE", state=state),),
            "investigation_state": state,
            "metrics": {
                "provider": "pydantic-function",
                "model": "scripted-kernel-model",
                "model_requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "tool_call_attempts": 0,
                "successful_tool_calls": 0,
                "elapsed_ms": 0,
            },
        }
    )
    assert result.investigation_state == result.trace[-1].state
    assert set(result.model_dump()) == {
        "diagnosis",
        "evidence_records",
        "trace",
        "investigation_state",
        "metrics",
    }


def test_run_result_requires_one_identical_terminal_kernel_snapshot() -> None:
    kernel = DiagnosticKernel.start(
        incident_case_id=CASE_ID,
        run_id=RUN_ID,
        allowed_root_cause_codes=ONTOLOGY,
        model_request_limit=8,
        tool_call_limit=8,
    )
    state = kernel.snapshot(model_requests_used=0)
    diagnosis = Diagnosis.model_validate(
        {
            "status": "INSUFFICIENT_EVIDENCE",
            "incident_case_id": CASE_ID,
            "run_id": RUN_ID,
            "root_cause_code": None,
            "summary": "The available evidence is insufficient.",
            "affected_assets": (),
            "evidence_ids": (),
            "recommended_actions": ("Collect additional evidence.",),
            "confidence": 0.2,
        }
    )
    base = {
        "diagnosis": diagnosis,
        "evidence_records": (),
        "trace": (KernelStateTraceEvent(event_type="KERNEL_STATE", state=state),),
        "investigation_state": state,
        "metrics": {
            "provider": "pydantic-function",
            "model": "scripted-kernel-model",
            "model_requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "tool_call_attempts": 0,
            "successful_tool_calls": 0,
            "elapsed_ms": 0,
        },
    }
    missing_terminal = {**base, "trace": ()}
    with pytest.raises(ValidationError, match="terminal Kernel state"):
        DiagnosisRunResult.model_validate(missing_terminal)

    wrong_state = state.model_copy(update={"revision": state.revision + 1})
    non_identical_terminal = {
        **base,
        "trace": (KernelStateTraceEvent(event_type="KERNEL_STATE", state=wrong_state),),
    }
    with pytest.raises(ValidationError, match="terminal Kernel state must equal"):
        DiagnosisRunResult.model_validate(non_identical_terminal)


def test_run_result_rejects_inventory_or_identity_drift() -> None:
    kernel = DiagnosticKernel.start(
        incident_case_id=CASE_ID,
        run_id=RUN_ID,
        allowed_root_cause_codes=ONTOLOGY,
        model_request_limit=8,
        tool_call_limit=8,
    )
    state = kernel.snapshot(model_requests_used=0)
    diagnosis = Diagnosis.model_validate(
        {
            "status": "INSUFFICIENT_EVIDENCE",
            "incident_case_id": CASE_ID,
            "run_id": RUN_ID,
            "root_cause_code": None,
            "summary": "The available evidence is insufficient.",
            "affected_assets": (),
            "evidence_ids": (),
            "recommended_actions": ("Collect additional evidence.",),
            "confidence": 0.2,
        }
    )
    wrong_state = state.model_copy(update={"evidence_inventory": (EVIDENCE_ID,)})
    with pytest.raises(ValidationError, match="Kernel evidence inventory"):
        DiagnosisRunResult.model_validate(
            {
                "diagnosis": diagnosis,
                "evidence_records": (),
                "trace": (
                    KernelStateTraceEvent(event_type="KERNEL_STATE", state=wrong_state),
                ),
                "investigation_state": wrong_state,
                "metrics": {
                    "provider": "pydantic-function",
                    "model": "scripted-kernel-model",
                    "model_requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "tool_call_attempts": 0,
                    "successful_tool_calls": 0,
                    "elapsed_ms": 0,
                },
            }
        )


def test_tool_trace_event_requires_nonnegative_strict_elapsed_ms() -> None:
    event = ToolTraceEvent(
        event_type="TOOL_CALL",
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
        fingerprint="b" * 64,
        evidence_ids=(),
        elapsed_ms=0,
    )
    assert event.elapsed_ms == 0
    with pytest.raises(ValidationError):
        ToolTraceEvent.model_validate({**event.model_dump(), "elapsed_ms": -1})
    with pytest.raises(ValidationError):
        ToolTraceEvent.model_validate({**event.model_dump(), "elapsed_ms": 1.0})


def test_model_protocol_trace_event_is_safe_and_strict() -> None:
    event = ModelProtocolTraceEvent(
        event_type="MODEL_PROTOCOL",
        stage="TOOL_ARGUMENT_VALIDATION",
        tool_name="get_dbt_run_results",
        category="DECISION_CONTRACT_REJECTED",
    )
    assert ModelProtocolTraceEvent.model_validate_json(event.model_dump_json()) == event
    with pytest.raises(ValidationError):
        ModelProtocolTraceEvent.model_validate({**event.model_dump(), "unexpected": "value"})
    with pytest.raises(ValidationError):
        ModelProtocolTraceEvent.model_validate({**event.model_dump(), "tool_name": " "})


def test_diagnosis_prompt_is_m6_versioned_hashed_and_case_agnostic() -> None:
    assert SYSTEM_PROMPT_VERSION == "m6.diagnosis.v1"
    assert hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest() == SYSTEM_PROMPT_SHA256
    assert "InvestigationIntent" in SYSTEM_PROMPT
    assert "SOURCE_SCHEMA_COLUMN_RENAMED" in SYSTEM_PROMPT
    assert "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED" in SYSTEM_PROMPT
    assert "at least two candidate hypotheses" in SYSTEM_PROMPT
    assert "Ground Truth" not in SYSTEM_PROMPT
    assert "schema_rename_payment_amount" not in SYSTEM_PROMPT
    assert "schema_type_change_payment_amount" not in SYSTEM_PROMPT
