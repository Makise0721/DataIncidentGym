from __future__ import annotations

from types import SimpleNamespace

from data_incident_gym.scenarios import load_scenario_spec
from data_incident_gym.submission_policy import (
    CLAIM_SUPPORT_REQUIRED,
    GAP_RECEIPT_REQUIRED,
    GATE_INTERNAL_ERROR,
    SubmissionPolicy,
)

_INSUFFICIENCY_CASE = "schema_type_change_order_customer_b"
_CONFIRMED_CASE = "schema_type_change_order_customer_a"


def _insufficiency_policy() -> SubmissionPolicy:
    return SubmissionPolicy(load_scenario_spec(_INSUFFICIENCY_CASE))


def _confirmed_policy() -> SubmissionPolicy:
    return SubmissionPolicy(load_scenario_spec(_CONFIRMED_CASE))


def _claim(kind: str, evidence_ids: tuple[str, ...]) -> SimpleNamespace:
    return SimpleNamespace(kind=kind, evidence_ids=evidence_ids)


def _declared(evidence_kind: str, subject: str, reason_code: str) -> SimpleNamespace:
    return SimpleNamespace(
        evidence_kind=evidence_kind, subject=subject, reason_code=reason_code
    )


def test_i1_refuses_claims_with_unresolvable_citations() -> None:
    policy = _confirmed_policy()
    submission = SimpleNamespace(
        claims=(_claim("ROOT_CAUSE", ("ev_" + "a" * 64,)),),
        unresolved_evidence=(),
    )

    refusal = policy.check(submission, records=(), trace=())

    assert refusal is not None
    assert refusal.code == CLAIM_SUPPORT_REQUIRED
    assert "cite evidence" in refusal.message


def test_i1_is_vacuous_for_insufficiency_contracts() -> None:
    policy = _insufficiency_policy()
    submission = SimpleNamespace(
        claims=(_claim("ROOT_CAUSE", ()),),
        unresolved_evidence=(),
    )

    assert policy.check(submission, records=(), trace=()) is None


def test_i2_refuses_declared_tool_gap_without_a_recorded_refusal() -> None:
    policy = _insufficiency_policy()
    submission = SimpleNamespace(
        claims=(),
        unresolved_evidence=(
            _declared("RELATION_SCHEMA", "raw_orders", "RELATION_NOT_ALLOWED"),
        ),
    )

    refusal = policy.check(submission, records=(), trace=())

    assert refusal is not None
    assert refusal.code == GAP_RECEIPT_REQUIRED
    assert "delete" not in refusal.message


def test_i2_allows_non_tool_and_unmatched_declarations() -> None:
    policy = _insufficiency_policy()
    submission = SimpleNamespace(
        claims=(),
        unresolved_evidence=(
            _declared(
                "TRANSFORMATION_DEFINITION",
                "model.jaffle_shop.stg_orders",
                "NOT_OBSERVABLE",
            ),
            _declared("SOME_KIND", "some_subject", "SOME_CODE"),
        ),
    )

    assert policy.check(submission, records=(), trace=()) is None


def test_gate_fails_closed_when_it_cannot_evaluate() -> None:
    policy = _confirmed_policy()

    class Exploding:
        @property
        def claims(self):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    refusal = policy.check(Exploding(), records=(), trace=())

    assert refusal is not None
    assert refusal.code == GATE_INTERNAL_ERROR


def test_kernel_decision_shaped_submissions_use_the_same_gates() -> None:
    policy = _insufficiency_policy()
    kernel_like = SimpleNamespace(
        status="INSUFFICIENT_EVIDENCE",
        claims=(),
        unresolved_evidence=(
            _declared("RELATION_SCHEMA", "raw_orders", "RELATION_NOT_ALLOWED"),
        ),
    )

    refusal = policy.check(kernel_like, records=(), trace=())

    assert refusal is not None
    assert refusal.code == GAP_RECEIPT_REQUIRED
