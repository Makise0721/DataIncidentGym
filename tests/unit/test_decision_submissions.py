"""The three model-facing submissions and their single normalization entry.

Each submission carries only what its terminal status can express, so a
mutually exclusive combination is not expressible; ``to_kernel_decision`` is a
pure mapping that fills only the constants the submitting tool already fixes.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from data_incident_gym.diagnostic_contracts import (
    MODEL_ERROR_TYPE_REASONS,
    MODEL_RULE_REASONS,
    AbstentionSubmission,
    ClaimEvidence,
    ClaimKind,
    ConfirmedSubmission,
    HealthSubmission,
    HypothesisAssessment,
    HypothesisVerdict,
    KernelDecision,
    to_kernel_decision,
)

RUN_ID = "a" * 32
EVIDENCE_ID = "ev_" + "b" * 64
OTHER_EVIDENCE_ID = "ev_" + "c" * 64
BASE = {
    "run_id": RUN_ID,
    "summary": "A short diagnosis.",
    "recommended_actions": (),
    "confidence": 0.5,
}


def _assessment(hypothesis_id: str = "h_loss") -> dict[str, object]:
    return {
        "hypothesis_id": hypothesis_id,
        "verdict": "SUPPORTED",
        "evidence_ids": [EVIDENCE_ID],
    }


# --------------------------------------------------------------- field surface


def test_each_submission_exposes_only_its_own_terminal_fields() -> None:
    abstention = AbstentionSubmission.model_json_schema()["properties"]
    confirmed = ConfirmedSubmission.model_json_schema()["properties"]
    health = HealthSubmission.model_json_schema()["properties"]

    assert "claims" not in abstention
    assert "selected_hypothesis_id" not in abstention
    assert "status" not in abstention
    assert "unresolved_evidence" not in confirmed
    assert "status" not in confirmed
    assert "unresolved_evidence" not in health
    assert "selected_hypothesis_id" not in health
    assert "unresolved_evidence" in abstention
    assert {"claims", "selected_hypothesis_id"} <= set(confirmed)
    assert "claims" in health


def test_claims_are_required_and_carry_at_least_one_item() -> None:
    for model, field in ((ConfirmedSubmission, "claims"), (HealthSubmission, "claims")):
        schema = model.model_json_schema()
        assert field in schema["required"]
        assert schema["properties"][field]["minItems"] == 1

    with pytest.raises(ValidationError) as error:
        ConfirmedSubmission(**BASE, selected_hypothesis_id="h_loss", claims=())

    assert any(item["type"] == "too_short" for item in error.value.errors())


def test_an_abstention_cannot_express_claims_or_a_selection() -> None:
    with pytest.raises(ValidationError) as error:
        AbstentionSubmission(**BASE, claims=[])

    assert error.value.errors()[0]["type"] == "extra_forbidden"
    assert error.value.errors()[0]["loc"] == ("claims",)

    with pytest.raises(ValidationError) as selection:
        AbstentionSubmission(**BASE, selected_hypothesis_id="h_loss")

    assert selection.value.errors()[0]["loc"] == ("selected_hypothesis_id",)


def test_missing_required_fields_report_their_location() -> None:
    with pytest.raises(ValidationError) as error:
        ConfirmedSubmission(**BASE, claims=[])  # type: ignore[call-arg]

    missing = {(item["type"], item["loc"]) for item in error.value.errors()}
    assert ("missing", ("selected_hypothesis_id",)) in missing


def test_cross_tool_payloads_are_rejected() -> None:
    abstention_payload = {
        **BASE,
        "unresolved_evidence": [{"evidence_kind": "INGESTION_WATERMARK", "subject": "raw_orders"}],
    }
    health_payload = {
        **BASE,
        "claims": [
            {
                "relation_name": "raw_payments",
                "history_name": "payment_count_by_order_date",
                "bucket": "2018-03-23",
                "current_value": 3,
                "evidence_ids": [EVIDENCE_ID],
            }
        ],
    }

    with pytest.raises(ValidationError):
        ConfirmedSubmission(**abstention_payload, selected_hypothesis_id="h_loss", claims=[])
    with pytest.raises(ValidationError):
        ConfirmedSubmission(**health_payload, selected_hypothesis_id="h_loss")
    with pytest.raises(ValidationError):
        AbstentionSubmission(**health_payload)
    with pytest.raises(ValidationError):
        HealthSubmission(**confirmed_payload())


def confirmed_payload() -> dict[str, object]:
    return {
        **BASE,
        "selected_hypothesis_id": "h_loss",
        "claims": [
            {
                "kind": "ROOT_CAUSE",
                "value": "SOURCE_PAYMENT_INGESTION_LOSS",
                "evidence_ids": [EVIDENCE_ID],
            }
        ],
    }


# ---------------------------------------------------------------- normalization


def test_abstention_normalization_fills_the_fixed_reason_code() -> None:
    submission = AbstentionSubmission(
        **{**BASE, "assessments": [_assessment()]},
        unresolved_evidence=[
            {"evidence_kind": "INGESTION_WATERMARK", "subject": "raw_orders"}
        ],
    )

    decision = to_kernel_decision(submission)

    assert decision == KernelDecision(
        status="INSUFFICIENT_EVIDENCE",
        run_id=RUN_ID,
        assessments=(
            HypothesisAssessment(
                hypothesis_id="h_loss",
                verdict=HypothesisVerdict.SUPPORTED,
                evidence_ids=(EVIDENCE_ID,),
            ),
        ),
        unresolved_evidence=(
            {
                "evidence_kind": "INGESTION_WATERMARK",
                "subject": "raw_orders",
                "reason_code": "NOT_OBSERVABLE",
            },
        ),
        summary="A short diagnosis.",
        recommended_actions=(),
        confidence=0.5,
    )
    assert decision.claims == ()
    assert decision.selected_hypothesis_id is None


def test_confirmed_normalization_carries_claims_and_the_selection() -> None:
    decision = to_kernel_decision(ConfirmedSubmission(**confirmed_payload()))

    assert decision.status == "CONFIRMED"
    assert decision.selected_hypothesis_id == "h_loss"
    assert decision.claims == (
        ClaimEvidence(
            kind=ClaimKind.ROOT_CAUSE,
            value="SOURCE_PAYMENT_INGESTION_LOSS",
            evidence_ids=(EVIDENCE_ID,),
            relation_name=None,
        ),
    )
    assert decision.unresolved_evidence == ()


def test_health_normalization_fills_the_claim_kind_and_value() -> None:
    """The value is not submitted and is not read for health claims; it is
    filled from the relation so the artifact keeps its shape."""

    submission = HealthSubmission(
        **BASE,
        claims=[
            {
                "relation_name": "raw_payments",
                "history_name": "payment_count_by_order_date",
                "bucket": "2018-03-23",
                "current_value": 3,
                "evidence_ids": [EVIDENCE_ID, OTHER_EVIDENCE_ID],
            }
        ],
    )

    decision = to_kernel_decision(submission)

    assert decision.status == "NO_INCIDENT"
    assert decision.claims == (
        ClaimEvidence(
            kind=ClaimKind.HEALTH_STATE,
            value="raw_payments",
            evidence_ids=(EVIDENCE_ID, OTHER_EVIDENCE_ID),
            relation_name="raw_payments",
            history_name="payment_count_by_order_date",
            bucket="2018-03-23",
            current_value=3,
        ),
    )


def test_normalization_rejects_a_payload_that_is_not_a_submission() -> None:
    with pytest.raises(TypeError):
        to_kernel_decision(KernelDecision(status="INSUFFICIENT_EVIDENCE", **BASE))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        to_kernel_decision({"status": "CONFIRMED"})


# ------------------------------------------------------- decision-level residue


def test_decision_level_rules_remain_reachable_at_normalization() -> None:
    """Four rules cannot be expressed by the submissions, so they surface when
    the mapped decision is built; each one keeps its stable reason code."""

    cases = {
        "decision text must not be blank": AbstentionSubmission(
            **{**BASE, "summary": "   "}
        ),
        "recommended_actions must not contain duplicates": AbstentionSubmission(
            **{**BASE, "recommended_actions": ["Act.", "Act."]}
        ),
        "assessment hypothesis IDs must not contain duplicates": AbstentionSubmission(
            **{
                **BASE,
                "assessments": [_assessment(), _assessment()],
            }
        ),
        "claim kind/value pairs must not contain duplicates": ConfirmedSubmission(
            **{
                **BASE,
                "selected_hypothesis_id": "h_loss",
                "claims": [
                    {
                        "kind": "ROOT_CAUSE",
                        "value": "SOURCE_PAYMENT_INGESTION_LOSS",
                        "evidence_ids": [EVIDENCE_ID],
                    },
                    {
                        "kind": "ROOT_CAUSE",
                        "value": "SOURCE_PAYMENT_INGESTION_LOSS",
                        "evidence_ids": [OTHER_EVIDENCE_ID],
                    },
                ],
            }
        ),
    }

    for message, submission in cases.items():
        with pytest.raises(ValidationError) as error:
            to_kernel_decision(submission)
        messages = {item["msg"].removeprefix("Value error, ") for item in error.value.errors()}
        assert message in messages
        assert message in MODEL_RULE_REASONS, message


def test_parser_level_rules_have_their_own_type_keyed_code() -> None:
    codes = [*MODEL_RULE_REASONS.values(), *MODEL_ERROR_TYPE_REASONS.values()]

    assert MODEL_ERROR_TYPE_REASONS == {"extra_forbidden": "UNEXPECTED_DECISION_FIELD"}
    assert len(codes) == len(set(codes)), "reason codes must stay distinguishable"
    assert set(MODEL_RULE_REASONS) & set(MODEL_ERROR_TYPE_REASONS) == set()
