"""Offline characterization and candidate evaluation for the CONFIRMED gap gate.

Production behaviour is pinned as-is (strict gate: any OPEN or BLOCKED gap
blocks CONFIRMED/NO_INCIDENT). Candidate relaxation rules are evaluated by
pure helpers in this module only; nothing here authorizes or patches
production behaviour. Fixtures are reused from ``test_diagnostic_kernel`` via
direct module loading so the paired cases share the exact accepted inputs.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from data_incident_gym.diagnosis import (
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    KernelStateTraceEvent,
    PolicyIdentity,
    UnresolvedEvidence,
)
from data_incident_gym.diagnostic_contracts import (
    ClaimEvidence,
    ClaimKind,
    EvidenceGapKind,
    EvidenceGapStatus,
    HypothesisAssessment,
    HypothesisVerdict,
    InvestigationIntent,
    InvestigationState,
    KernelDecision,
    KernelError,
    KernelFinalStatus,
)
from data_incident_gym.diagnostic_kernel import DiagnosticKernel
from data_incident_gym.evaluation import (
    ControllerCheckCode,
    _controller_checks,
)


def _load_kernel_fixtures() -> ModuleType:
    module_name = "kernel_gate_assessment_fixtures"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(
        module_name,
        Path(__file__).with_name("test_diagnostic_kernel.py"),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


FIX = _load_kernel_fixtures()


def _extra_blocked_via_tool_failure(kernel: DiagnosticKernel, relation: str) -> None:
    """A real extra investigation failure through prepare/record_tool_failure."""

    prepared = kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id="g_extra_probe",
            gap_kind=EvidenceGapKind.PROFILE_RELATION,
        ),
        tool_name="get_relation_data_profile",
        arguments={"relation_name": relation},
    )
    kernel.record_tool_failure(prepared, "PROFILE_SNAPSHOT_MISMATCH")


def _duplicate_kernel_with_extra_allowance(extra_profile_relation: str) -> DiagnosticKernel:
    """The accepted semantic-duplicate kernel plus one in-scope spare relation.

    The spare relation exists only so an extra probe can fail through the
    real ``record_tool_failure``/OPEN paths; the main-line evidence and
    decision are byte-identical to the accepted CONFIRMED case.
    """

    return DiagnosticKernel.start(
        run_id=FIX.RUN_ID,
        allowed_root_cause_codes=(
            "SOURCE_EXACT_PAYMENT_DUPLICATE",
            "SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
            "LEGITIMATE_SPLIT_PAYMENT",
        ),
        model_request_limit=8,
        tool_call_limit=8,
        observable_schema_relations=("raw_payments",),
        observable_profile_relations=("raw_payments", extra_profile_relation),
        incident_subjects=("seed.jaffle_shop.raw_payments", "raw_payments"),
    )


def _extra_blocked_via_not_allowed(kernel: DiagnosticKernel, relation: str) -> None:
    """A real extra investigation failure through the kernel's blocked path."""

    with pytest.raises(KernelError, match="RELATION_NOT_ALLOWED"):
        kernel.prepare_tool(
            intent=InvestigationIntent(
                gap_id="g_extra_probe",
                gap_kind=EvidenceGapKind.PROFILE_RELATION,
            ),
            tool_name="get_relation_data_profile",
            arguments={"relation_name": relation},
        )


def test_semantic_duplicate_confirmed_blocked_by_one_extra_failed_probe() -> None:
    kernel = FIX._duplicate_kernel()
    records = FIX._duplicate_records()
    FIX._close_duplicate_records(kernel, records)
    _extra_blocked_via_not_allowed(kernel, "raw_orders")

    with pytest.raises(KernelError, match="EVIDENCE_GAP_OPEN"):
        kernel.finalize(FIX._semantic_duplicate_decision(records))

    state = kernel.snapshot(model_requests_used=0)
    assert state.final_status is None
    blocked = [gap for gap in state.gaps if gap.status is EvidenceGapStatus.BLOCKED]
    assert [(gap.gap_id, gap.subject, gap.error_code) for gap in blocked] == [
        ("g_extra_probe", "raw_orders", "RELATION_NOT_ALLOWED")
    ]


def test_permanent_orphan_confirmed_blocked_by_one_extra_failed_probe() -> None:
    kernel = FIX._orphan_kernel()
    records = FIX._orphan_records()
    FIX._close_orphan_records(kernel, records)
    _extra_blocked_via_not_allowed(kernel, "raw_orders")

    with pytest.raises(KernelError, match="EVIDENCE_GAP_OPEN"):
        kernel.finalize(FIX._orphan_decision(records))


def test_silent_drop_confirmed_blocked_by_one_extra_failed_probe() -> None:
    kernel = FIX._silent_kernel()
    records = FIX._silent_records()
    FIX._close_silent_records(kernel, records)
    _extra_blocked_via_not_allowed(kernel, "raw_customers")

    with pytest.raises(KernelError, match="EVIDENCE_GAP_OPEN"):
        kernel.finalize(FIX._silent_decision(records))


def test_record_tool_failure_path_blocks_confirmed_without_touching_evidence() -> None:
    kernel = _duplicate_kernel_with_extra_allowance("raw_orders")
    records = FIX._duplicate_records()
    FIX._close_duplicate_records(kernel, records)
    inventory_before = kernel.evidence_records
    _extra_blocked_via_tool_failure(kernel, "raw_orders")

    with pytest.raises(KernelError, match="EVIDENCE_GAP_OPEN"):
        kernel.finalize(FIX._semantic_duplicate_decision(records))

    assert kernel.evidence_records == inventory_before
    state = kernel.snapshot(model_requests_used=0)
    blocked = [gap for gap in state.gaps if gap.status is EvidenceGapStatus.BLOCKED]
    assert blocked[0].error_code == "PROFILE_SNAPSHOT_MISMATCH"
    assert blocked[0].tool_name == "get_relation_data_profile"


def test_open_gap_blocks_confirmed_even_with_complete_cited_evidence() -> None:
    kernel = _duplicate_kernel_with_extra_allowance("raw_orders")
    records = FIX._duplicate_records()
    FIX._close_duplicate_records(kernel, records)
    kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id="g_open_probe",
            gap_kind=EvidenceGapKind.PROFILE_RELATION,
        ),
        tool_name="get_relation_data_profile",
        arguments={"relation_name": "raw_orders"},
    )
    assert any(
        gap.status is EvidenceGapStatus.OPEN
        for gap in kernel.snapshot(model_requests_used=0).gaps
    )

    with pytest.raises(KernelError, match="EVIDENCE_GAP_OPEN"):
        kernel.finalize(FIX._semantic_duplicate_decision(records))


def test_no_incident_keeps_strict_gap_gate() -> None:
    kernel, records = FIX._health_kernel(
        logical_observed_at=FIX.datetime(2018, 4, 9, 12, tzinfo=FIX.UTC)
    )
    _extra_blocked_via_not_allowed(kernel, "raw_customers")

    with pytest.raises(KernelError, match="EVIDENCE_GAP_OPEN"):
        kernel.finalize(FIX._health_decision(records))


def test_blocked_gap_keeps_original_attempt_record_for_consumers() -> None:
    kernel = _duplicate_kernel_with_extra_allowance("raw_orders")
    records = FIX._duplicate_records()
    FIX._close_duplicate_records(kernel, records)
    _extra_blocked_via_tool_failure(kernel, "raw_orders")
    with pytest.raises(KernelError, match="EVIDENCE_GAP_OPEN"):
        kernel.finalize(FIX._semantic_duplicate_decision(records))
    state = kernel.snapshot(model_requests_used=0)
    probe = next(gap for gap in state.gaps if gap.gap_id == "g_extra_probe")
    assert probe.status is EvidenceGapStatus.BLOCKED
    assert probe.subject == "raw_orders"
    assert probe.error_code == "PROFILE_SNAPSHOT_MISMATCH"
    assert probe.tool_name == "get_relation_data_profile"


def test_cross_run_and_unknown_references_stay_rejected_under_candidate_mindset() -> None:
    kernel = _duplicate_kernel_with_extra_allowance("raw_orders")
    records = FIX._duplicate_records()
    FIX._close_duplicate_records(kernel, records)
    run, lineage, _, profile = records
    foreign = profile.model_copy(update={"run_id": "b" * 32})
    prepared = kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id="g_foreign_profile",
            gap_kind=EvidenceGapKind.PROFILE_RELATION,
        ),
        tool_name="get_relation_data_profile",
        arguments={"relation_name": "raw_orders"},
    )
    with pytest.raises(KernelError, match="RUN_CONTEXT_MISMATCH"):
        kernel.record_tool_result(prepared, (foreign,))
    decision = FIX._semantic_duplicate_decision(records)
    with pytest.raises(KernelError, match="EVIDENCE_GAP_OPEN"):
        kernel.finalize(decision)
    clean = FIX._duplicate_kernel()
    clean_records = FIX._duplicate_records()
    FIX._close_duplicate_records(clean, clean_records)
    conflicting = KernelDecision(
        status="CONFIRMED",
        run_id=FIX.RUN_ID,
        selected_hypothesis_id="h_semantic_duplicate",
        assessments=decision.assessments,
        claims=(
            ClaimEvidence(
                kind=ClaimKind.ROOT_CAUSE,
                value="SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
                evidence_ids=("ev_" + "0" * 64,),
            ),
        ),
        summary="claim evidence must stay bound",
        recommended_actions=(),
        confidence=0.9,
    )
    with pytest.raises(KernelError, match="CLAIM_EVIDENCE_UNBOUND"):
        clean.finalize(conflicting)
    assert lineage.evidence_id in {r.evidence_id for r in kernel.evidence_records}
    assert run.evidence_id in {r.evidence_id for r in kernel.evidence_records}


# ---------------------------------------------------------------------------
# Candidate-rule evaluation (offline only; never wired into production).
# ---------------------------------------------------------------------------


def _cited_subjects(state: InvestigationState, decision: KernelDecision) -> set[str]:
    """Subjects touched by evidence cited from any CLOSED gap of the claims."""

    closed_subjects: dict[str, set[str]] = {}
    for gap in state.gaps:
        if gap.status is EvidenceGapStatus.CLOSED:
            closed_subjects.setdefault(gap.subject, set()).update(gap.evidence_ids)
    cited_ids = {
        evidence_id
        for claim in decision.claims
        for evidence_id in claim.evidence_ids
    }
    return {
        subject
        for subject, evidence_ids in closed_subjects.items()
        if evidence_ids & cited_ids
    }


def _candidate_c1_ignorable(state: InvestigationState, decision: KernelDecision) -> list[str]:
    """C1: ignore a BLOCKED gap whose subject was never cited by any claim."""

    cited = _cited_subjects(state, decision)
    return [
        gap.gap_id
        for gap in state.gaps
        if gap.status is EvidenceGapStatus.BLOCKED and gap.subject not in cited
    ]


def test_candidate_c1_cannot_distinguish_dangerous_unavailable_facts() -> None:
    """C1 lets an unqueried discriminating fact pass on public evidence alone.

    In the semantic-duplicate pair the BLOCKED probe on ``raw_orders`` is
    uncited, so C1 would ignore it. Whether that unavailable profile could
    have refuted the structural REFUTED verdict is not decidable from public
    facts: REFUTED verdicts have no content-level validation in kernel or
    evaluator. That makes C1 unsound without private answer knowledge, so the
    candidate is rejected.
    """

    kernel = _duplicate_kernel_with_extra_allowance("raw_orders")
    records = FIX._duplicate_records()
    FIX._close_duplicate_records(kernel, records)
    _extra_blocked_via_tool_failure(kernel, "raw_orders")
    decision = FIX._semantic_duplicate_decision(records)
    state = kernel.snapshot(model_requests_used=0)

    assert any(gap.status is EvidenceGapStatus.BLOCKED for gap in state.gaps)
    assert _candidate_c1_ignorable(state, decision) == ["g_extra_probe"]

    with pytest.raises(KernelError, match="EVIDENCE_GAP_OPEN"):
        kernel.finalize(decision)


def test_c1_ignores_the_blocked_probe_and_leaves_no_unresolved_declaration() -> None:
    """C1's verdict on the paired case, and what the model would have to emit.

    This is the candidate-side observation only: C1 marks the BLOCKED probe
    ignorable because its subject was never cited. Whether ignoring it is
    safe is decided in ``test_evaluator_gap_gate_rejects_a_relaxed_
    confirmed_terminal_state`` (real evaluator invocation) and by the
    REFUTED structural-validity fact pinned in this module.
    """

    kernel = FIX._duplicate_kernel()
    records = FIX._duplicate_records()
    FIX._close_duplicate_records(kernel, records)
    _extra_blocked_via_not_allowed(kernel, "raw_orders")
    decision = FIX._semantic_duplicate_decision(records)
    state = kernel.snapshot(model_requests_used=0)

    assert _candidate_c1_ignorable(state, decision) == ["g_extra_probe"]
    assert any(gap.status is EvidenceGapStatus.BLOCKED for gap in state.gaps)
    unresolved_probe = UnresolvedEvidence(
        evidence_kind="RELATION_DATA_PROFILE",
        subject="raw_orders",
        reason_code="RELATION_NOT_ALLOWED",
    )
    assert unresolved_probe.subject not in {
        item.subject for item in decision.unresolved_evidence
    }


def test_candidate_c1_rejects_structural_refuted_shortcut_for_open_gaps() -> None:
    """OPEN gaps stay blocking for any candidate; C1 only ever sees BLOCKED."""

    kernel = _duplicate_kernel_with_extra_allowance("raw_orders")
    records = FIX._duplicate_records()
    FIX._close_duplicate_records(kernel, records)
    kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id="g_open_probe",
            gap_kind=EvidenceGapKind.PROFILE_RELATION,
        ),
        tool_name="get_relation_data_profile",
        arguments={"relation_name": "raw_orders"},
    )
    decision = FIX._semantic_duplicate_decision(records)
    state = kernel.snapshot(model_requests_used=0)

    assert any(gap.status is EvidenceGapStatus.OPEN for gap in state.gaps)
    assert _candidate_c1_ignorable(state, decision) == []
    with pytest.raises(KernelError, match="EVIDENCE_GAP_OPEN"):
        kernel.finalize(decision)
    assert state.final_status is None


def test_assessments_are_structural_not_content_validated() -> None:
    """REFUTED passes with any bound closed evidence; no semantic check exists.

    This is the load-bearing fact against relaxation: a BLOCKED probe may be
    exactly the query that would have overturned a REFUTED verdict, and
    neither kernel nor evaluator can tell from public facts.
    """

    kernel = FIX._duplicate_kernel()
    records = FIX._duplicate_records()
    FIX._close_duplicate_records(kernel, records)
    run, lineage, schema, profile = records
    decision = FIX._semantic_duplicate_decision(records)
    thinnest_refuted = KernelDecision(
        status="CONFIRMED",
        run_id=FIX.RUN_ID,
        selected_hypothesis_id="h_semantic_duplicate",
        assessments=(
            HypothesisAssessment(
                hypothesis_id="h_semantic_duplicate",
                verdict=HypothesisVerdict.SUPPORTED,
                evidence_ids=(profile.evidence_id,),
            ),
            HypothesisAssessment(
                hypothesis_id="h_legitimate_split",
                verdict=HypothesisVerdict.REFUTED,
                evidence_ids=(schema.evidence_id,),
            ),
        ),
        claims=decision.claims,
        summary=decision.summary,
        recommended_actions=(),
        confidence=decision.confidence,
    )

    outcome = kernel.finalize(thinnest_refuted)
    assert outcome.status is KernelFinalStatus.CONFIRMED
    assert kernel.snapshot(model_requests_used=0).selected_hypothesis_id == (
        "h_semantic_duplicate"
    )


# ---------------------------------------------------------------------------
# Review fixes: paired near-miss cases with missing key evidence, and a real
# evaluator invocation for the independence conflict.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "broken",
    ("duplicate_profile", "orphan_watermark", "silent_comparison"),
)
def test_missing_key_evidence_is_rejected_by_domain_rules_under_the_same_blocked_gap(
    broken: str,
) -> None:
    """Paired near-miss: same BLOCKED gap, key evidence degraded.

    Each case pairs with its accepted CONFIRMED control from above. With the
    key piece degraded, the domain rule -- not the gap gate -- rejects the
    finalize. This shows a relaxation rule cannot hide missing evidence
    behind "uncited/unnecessary": the content checks evaluate exactly what
    the model cites, so omitting or degrading evidence stays rejected
    independently of any gap-gate change.
    """

    kernel = _duplicate_kernel_with_extra_allowance("raw_orders")
    records = FIX._duplicate_records()
    if broken == "duplicate_profile":
        records = FIX._duplicate_records(fingerprint_duplicates=0)
        FIX._close_duplicate_records(kernel, records)
        _extra_blocked_via_tool_failure(kernel, "raw_orders")
        expected_code = "ROOT_CLAIM_EVIDENCE_INCOMPATIBLE"
        decision = FIX._semantic_duplicate_decision(records)
    elif broken == "orphan_watermark":
        kernel = FIX._orphan_kernel()
        records = FIX._orphan_records(watermark=None)
        FIX._close_orphan_records(kernel, records)
        _extra_blocked_via_not_allowed(kernel, "raw_orders")
        expected_code = "ROOT_CLAIM_EVIDENCE_INCOMPATIBLE"
        decision = FIX._orphan_decision(records)
    else:
        kernel = FIX._silent_kernel(
            observations=(
                (
                    "CURRENT_PERIOD_COUNT",
                    "raw_payments/payment_count_by_order_date/2018-04-07",
                    "2",
                ),
                (
                    "EXPECTED_PERIOD_COUNT",
                    "raw_payments/payment_count_by_order_date/2018-04-07",
                    "2",
                ),
                ("CURRENT_RELATION_COUNT", "raw_payments", "112"),
                ("SETTLED_PAYMENT_WINDOW_END", "raw_orders", "2018-04-07"),
            ),
        )
        records = FIX._silent_records()
        FIX._close_silent_records(kernel, records)
        _extra_blocked_via_not_allowed(kernel, "raw_customers")
        expected_code = "ROOT_CLAIM_EVIDENCE_INCOMPATIBLE"
        decision = FIX._silent_decision(records)

    with pytest.raises(KernelError, match="EVIDENCE_GAP_OPEN"):
        kernel.finalize(decision)

    # The gap gate is not what protects these cases: strip the blocked gap
    # from the picture by re-running the same degraded evidence on a fresh
    # kernel without the extra probe; the domain rule still refuses.
    if broken == "duplicate_profile":
        fresh = FIX._duplicate_kernel()
        fresh_records = FIX._duplicate_records(fingerprint_duplicates=0)
        FIX._close_duplicate_records(fresh, fresh_records)
        with pytest.raises(KernelError, match=expected_code):
            fresh.finalize(FIX._semantic_duplicate_decision(fresh_records))
    elif broken == "orphan_watermark":
        fresh = FIX._orphan_kernel()
        fresh_records = FIX._orphan_records(watermark=None)
        FIX._close_orphan_records(fresh, fresh_records)
        with pytest.raises(KernelError, match=expected_code):
            fresh.finalize(FIX._orphan_decision(fresh_records))
    else:
        fresh = FIX._silent_kernel(
            observations=(
                (
                    "CURRENT_PERIOD_COUNT",
                    "raw_payments/payment_count_by_order_date/2018-04-07",
                    "2",
                ),
                (
                    "EXPECTED_PERIOD_COUNT",
                    "raw_payments/payment_count_by_order_date/2018-04-07",
                    "2",
                ),
                ("CURRENT_RELATION_COUNT", "raw_payments", "112"),
                ("SETTLED_PAYMENT_WINDOW_END", "raw_orders", "2018-04-07"),
            ),
        )
        fresh_records = FIX._silent_records()
        FIX._close_silent_records(fresh, fresh_records)
        with pytest.raises(KernelError, match=expected_code):
            fresh.finalize(FIX._silent_decision(fresh_records))


def _confirmed_run_result(
    state: InvestigationState,
    *,
    root_cause_code: str = "SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
) -> DiagnosisRunResult:
    evidence_id = "ev_" + "a" * 64
    diagnosis = Diagnosis(
        status=DiagnosisStatus.CONFIRMED,
        run_id=state.run_id,
        root_cause_code=root_cause_code,
        summary="The payment aggregate contains repeated business fingerprints.",
        evidence_ids=[evidence_id],
        affected_assets=["model.jaffle_shop.stg_payments"],
        claims=[
            {
                "kind": "ROOT_CAUSE",
                "root_cause_code": root_cause_code,
                "evidence_ids": [evidence_id],
            },
            {
                "kind": "AFFECTED_ASSET",
                "asset": "model.jaffle_shop.stg_payments",
                "evidence_ids": [evidence_id],
            },
        ],
        confidence=0.9,
    )
    return DiagnosisRunResult(
        strategy=DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        policy_identity=PolicyIdentity(
            strategy=DiagnosticStrategy.DIAGNOSTIC_KERNEL,
            base_prompt_version="p1.base.v1",
            base_prompt_sha256="0" * 64,
            strategy_prompt_version="p1.kernel.v9",
            strategy_prompt_sha256="1" * 64,
            controller_protocol_version="p1.controller.v8",
            controller_protocol_sha256="2" * 64,
            tool_schema_sha256="3" * 64,
        ),
        diagnosis=diagnosis,
        evidence_records=(),
        trace=(
            KernelStateTraceEvent(
                event_type="KERNEL_STATE",
                state=state,
            ),
            DiagnosisTerminalTraceEvent(
                event_type="DIAGNOSIS_TERMINAL",
                strategy=DiagnosticStrategy.DIAGNOSTIC_KERNEL,
                status=DiagnosisStatus.CONFIRMED,
                evidence_inventory=(),
            ),
        ),
        metrics=DiagnosisMetrics(
            provider="synthetic",
            model="synthetic-model",
            model_requests=1,
            input_tokens=0,
            output_tokens=0,
            tool_call_attempts=0,
            successful_tool_calls=0,
            elapsed_ms=1,
        ),
        kernel_state=state,
    )


def test_evaluator_gap_gate_rejects_a_relaxed_confirmed_terminal_state() -> None:
    """Real evaluator invocation: a C1-relaxed terminal state is rejected.

    The kernel refuses to produce a CONFIRMED terminal state with a BLOCKED
    gap, so the relaxed state is constructed by relabeling the blocked
    investigation exactly as a relaxation rule would emit it. Feeding it to
    the production ``_controller_checks`` shows the independent
    KERNEL_EVIDENCE_GAP_GATE fails; the clean terminal state passes.
    """

    kernel = _duplicate_kernel_with_extra_allowance("raw_orders")
    records = FIX._duplicate_records()
    FIX._close_duplicate_records(kernel, records)
    _extra_blocked_via_tool_failure(kernel, "raw_orders")
    blocked_snapshot = kernel.snapshot(model_requests_used=0)
    relaxed_state = blocked_snapshot.model_copy(
        update={
            "final_status": KernelFinalStatus.CONFIRMED,
            "gate_reason": "CONFIRMED",
            "selected_hypothesis_id": "h_semantic_duplicate",
        }
    )

    relaxed_checks = _controller_checks(_confirmed_run_result(relaxed_state))
    relaxed_gap_check = next(
        check
        for check in relaxed_checks
        if check.code is ControllerCheckCode.KERNEL_EVIDENCE_GAP_GATE
    )
    assert relaxed_gap_check.passed is False
    assert relaxed_gap_check.actual == ("INVALID",)

    clean_kernel = FIX._duplicate_kernel()
    clean_records = FIX._duplicate_records()
    FIX._close_duplicate_records(clean_kernel, clean_records)
    clean_kernel.finalize(FIX._semantic_duplicate_decision(clean_records))
    clean_checks = _controller_checks(
        _confirmed_run_result(clean_kernel.snapshot(model_requests_used=0))
    )
    clean_gap_check = next(
        check
        for check in clean_checks
        if check.code is ControllerCheckCode.KERNEL_EVIDENCE_GAP_GATE
    )
    assert clean_gap_check.passed is True
