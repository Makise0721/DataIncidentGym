"""A model-level rejection must be classifiable offline.

``error_loc`` stays empty and ``error_kind`` stays ``value_error`` when a
model-level validator rejects a whole payload, so those two fields cannot tell a
duplicated entry from a blank text or a status/field conflict. That is exactly
the p1-formal-v14 seq19 failure: the rejection was recorded but not
interpretable. The validators' own messages are now mapped to fixed reason
codes, and these tests keep that mapping complete and leak-free.

The rule inventory is extracted from the contract source rather than
hand-listed, so a new model-level validator without a reason code fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "data_incident_gym"
CONTRACTS_SOURCE = PACKAGE / "diagnostic_contracts.py"

_EXPECTED_RULE_COUNT = 13


def _class_body(text: str, name: str) -> str:
    """Source of one class, up to the next top-level class."""

    after = text.split(f"class {name}", 1)[1]
    return after.split("\nclass ", 1)[0]


def _reject_duplicates_fields(body: str) -> set[str]:
    """Field names passed to ``reject_duplicates`` inside one class body.

    The calls span several lines and nest parentheses, so the quoted field name
    is read by balancing parentheses rather than by a single-line pattern.
    """

    fields: set[str] = set()
    start = 0
    while True:
        index = body.find("reject_duplicates(", start)
        if index < 0:
            return fields
        index += len("reject_duplicates(")
        depth = 1
        end = index
        while end < len(body) and depth:
            if body[end] == "(":
                depth += 1
            elif body[end] == ")":
                depth -= 1
            end += 1
        call = body[index:end]
        quoted = re.findall(r'"([^"]+)"', call)
        if quoted:
            fields.add(quoted[-1])
        start = end


def _rule_messages() -> set[str]:
    """Messages a KernelDecision-level validator can raise, read from source.

    Covers the decision model and the two nested models it validates, because
    any of them can reject a submitted payload. ``reject_duplicates`` raises
    ``f"{field_name} must not contain duplicates"``, so its call sites are
    expanded with the literal field name they pass.
    """

    text = CONTRACTS_SOURCE.read_text(encoding="utf-8")
    messages: set[str] = set()
    for class_name in ("KernelDecision", "HypothesisAssessment", "ClaimEvidence"):
        body = _class_body(text, class_name)
        messages.update(re.findall(r'raise ValueError\(\s*"([^"]+)"', body))
        messages.update(
            f"{field} must not contain duplicates"
            for field in _reject_duplicates_fields(body)
        )
    return messages


def _reasons_for(payload: dict[str, object]) -> tuple[str, ...]:
    from data_incident_gym.diagnostic_agent import _model_rule_reasons
    from data_incident_gym.diagnostic_contracts import KernelDecision

    with pytest.raises(ValidationError) as error:
        KernelDecision.model_validate(payload)
    return _model_rule_reasons(error.value)


def _decision(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "p1.kernel_decision.v1",
        "status": "CONFIRMED",
        "run_id": "a" * 32,
        "selected_hypothesis_id": "h_one",
        "assessments": [
            {
                "hypothesis_id": "h_one",
                "verdict": "SUPPORTED",
                "evidence_ids": ["ev_" + "0" * 64],
            }
        ],
        "claims": [
            {
                "kind": "ROOT_CAUSE",
                "value": "SOURCE_REQUIRED_FIELD_NULL",
                "evidence_ids": ["ev_" + "0" * 64],
            }
        ],
        "summary": "A short diagnosis.",
        "recommended_actions": ["Act."],
        "confidence": 0.5,
    }
    payload.update(overrides)
    return payload


def test_the_rule_inventory_is_not_vacuous() -> None:
    messages = _rule_messages()

    assert len(messages) >= _EXPECTED_RULE_COUNT, sorted(messages)
    assert "decision text must not be blank" in messages
    assert "claim kind/value pairs must not contain duplicates" in messages


def test_every_model_level_rule_has_its_own_reason_code() -> None:
    from data_incident_gym.diagnostic_contracts import MODEL_RULE_REASONS

    missing = sorted(_rule_messages() - set(MODEL_RULE_REASONS))

    assert missing == [], missing
    codes = list(MODEL_RULE_REASONS.values())
    assert len(codes) == len(set(codes)), "reason codes must stay distinguishable"


def test_duplicate_and_blank_and_status_rules_are_distinguishable() -> None:
    """The three families that ``value_error`` alone cannot separate."""

    duplicated = _reasons_for(
        _decision(
            claims=[
                {
                    "kind": "ROOT_CAUSE",
                    "value": "SOURCE_REQUIRED_FIELD_NULL",
                    "evidence_ids": ["ev_" + "0" * 64],
                },
                {
                    "kind": "ROOT_CAUSE",
                    "value": "SOURCE_REQUIRED_FIELD_NULL",
                    "evidence_ids": ["ev_" + "0" * 64],
                },
            ]
        )
    )
    blank = _reasons_for(_decision(summary="   ", recommended_actions=["Act."]))
    status_conflict = _reasons_for(
        _decision(status="INSUFFICIENT_EVIDENCE", selected_hypothesis_id="h_one")
    )

    assert "CLAIM_VALUES_DUPLICATED" in duplicated
    assert blank == ("DECISION_TEXT_BLANK",)
    assert status_conflict == ("NON_CONFIRMED_SELECTS_HYPOTHESIS",)
    assert set(duplicated) != set(blank) != set(status_conflict)


def test_an_unmapped_rule_is_reported_as_unclassified() -> None:
    """A model-level message the mapping does not know must not be dropped and
    must not leak: it becomes the fixed placeholder instead. This is the case a
    future validator would hit before its code is added here."""

    from data_incident_gym.diagnostic_agent import _model_rule_reasons
    from data_incident_gym.diagnostic_contracts import UNCLASSIFIED_MODEL_RULE

    class _UnmappedError:
        @staticmethod
        def errors() -> list[dict[str, object]]:
            return [
                {
                    "type": "value_error",
                    "msg": "Value error, a new rule with a raw value id-12345",
                    "loc": (),
                }
            ]

    reasons = _model_rule_reasons(_UnmappedError())  # type: ignore[arg-type]

    assert reasons == (UNCLASSIFIED_MODEL_RULE,)
    assert "id-12345" not in "".join(reasons)


def test_reason_codes_never_carry_payload_text() -> None:
    """Codes only: no field values, no validator messages, no evidence IDs."""

    from data_incident_gym.diagnostic_contracts import MODEL_RULE_REASONS

    forbidden = ("ev_" + "0" * 64, "Value error", "source-1", "raw_payments")
    for message, code in MODEL_RULE_REASONS.items():
        assert message not in code, code
        assert not any(token in code for token in forbidden), code
        assert code.isupper(), code
