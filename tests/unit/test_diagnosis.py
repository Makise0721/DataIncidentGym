from __future__ import annotations

import pytest
from pydantic import ValidationError

from data_incident_gym.diagnosis import Diagnosis, DiagnosisRunResult

RUN_ID = "0123456789abcdef0123456789abcdef"
EVIDENCE_ID = "ev_" + "a" * 64


def confirmed_payload() -> dict[str, object]:
    return {
        "status": "CONFIRMED",
        "incident_case_id": "schema_rename_payment_amount",
        "run_id": RUN_ID,
        "root_cause_code": "SOURCE_SCHEMA_COLUMN_RENAMED",
        "summary": (
            "The source column changed while a downstream model still references the old name."
        ),
        "affected_assets": ("stg_payments", "orders"),
        "evidence_ids": (EVIDENCE_ID,),
        "recommended_actions": ("Restore the consumer reference or update the source contract.",),
        "confidence": 0.9,
    }


def nonconfirmed_payload(status: str) -> dict[str, object]:
    payload = confirmed_payload()
    payload.update(
        {
            "status": status,
            "root_cause_code": None,
            "affected_assets": (),
            "summary": "The available evidence does not support a confirmed diagnosis.",
        }
    )
    return payload


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
    payload = nonconfirmed_payload(status)
    payload["root_cause_code"] = "SOURCE_SCHEMA_COLUMN_RENAMED"
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(payload)


def test_model_error_accepts_only_fixed_safe_reason_code() -> None:
    payload = nonconfirmed_payload("MODEL_ERROR")
    payload["summary"] = "MODEL_RUNTIME_ERROR"
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


def test_diagnosis_is_frozen_and_run_result_has_exact_contract() -> None:
    diagnosis = Diagnosis.model_validate(confirmed_payload())
    with pytest.raises(ValidationError):
        diagnosis.status = "MODEL_ERROR"  # type: ignore[misc]

    result = DiagnosisRunResult.model_validate(
        {
            "diagnosis": diagnosis,
            "evidence_records": (),
            "trace": (),
            "metrics": {
                "provider": "openai-compatible",
                "model": "gemma4:e4b",
                "model_requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "tool_call_attempts": 0,
                "successful_tool_calls": 0,
                "elapsed_ms": 0,
            },
        }
    )
    assert set(result.model_dump()) == {"diagnosis", "evidence_records", "trace", "metrics"}
    with pytest.raises(ValidationError):
        DiagnosisRunResult.model_validate({**result.model_dump(), "unexpected": "value"})
