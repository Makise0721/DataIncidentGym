"""Offline review of archived submission-gate refusals (P-1 reader, 2026-09-22).

``review_refusal_events`` re-derives, for every refused ``EVIDENCE_GATE``
event in an archived run, whether the refusal was correct. The inputs are the
run result and scenario contract as loaded from a scoring-inputs bundle; the
evidence set and tool-trace prefix are rebuilt from the events **before** the
refusal, so later-collected evidence can never colour the verdict. The reader
re-derives every claim's recomputability through the same
``derive_claim_recomputability`` the writer used instead of trusting the
stored boolean, and reports disagreements without letting them change the
verdict.

Every refusal gets exactly one of three verdicts:

- ``CORRECT`` — a refusal condition is re-established from the audit alone
  (an unregistered citation on an applicable claim, an unsupported claim, or
  a declared contract gap without a witnessed tool refusal);
- ``FALSE_REFUSAL`` — the gate's own criteria, re-derived, all pass;
- ``INDETERMINABLE`` — the audit is absent, truncated, self-inconsistent, or
  redacted in a way that could still satisfy the kernel contract. It is never
  counted as correct.

The audit's bounded projections (claims, unresolved gaps) are trusted as the
refused submission's own record except where a cap dropped items; that is the
P-1 design boundary (the refused submission itself is not archived).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from data_incident_gym.diagnosis import (
    KERNEL_STRATEGIES,
    AffectedAssetClaim,
    DiagnosisRunResultAny,
    DiagnosisRunResultV3,
    DiagnosisTerminalTraceEvent,
    EvidenceGateTraceEvent,
    EvidenceGateTraceEventV2,
    KernelStateTraceEvent,
    RefusalAudit,
    RejectedDecisionSummaryV2,
    RootCauseClaim,
    ToolTraceEvent,
    ToolTraceEventV2,
    UnresolvedEvidence,
    derive_claim_recomputability,
    refusal_witnessed,
)
from data_incident_gym.diagnostic_contracts import (
    EvidenceGapKind,
    EvidenceGapStatus,
    InvestigationState,
    KernelError,
)
from data_incident_gym.diagnostic_validation import (
    ValidationContext,
    validate_unresolved_declarations,
)
from data_incident_gym.evaluation import (
    APPLICABLE_CLAIM_KINDS_BY_EXPECTED_STATUS,
    claim_supported_by_records,
)
from data_incident_gym.scenarios import ScenarioSpec

REFUSAL_REVIEW_SCHEMA_VERSION = "p1.refusal_review.v1"

BASIS_UNREGISTERED_REF_ESTABLISHED = "UNREGISTERED_REF_ESTABLISHED"
BASIS_UNSUPPORTED_CLAIM_ESTABLISHED = "UNSUPPORTED_CLAIM_ESTABLISHED"
BASIS_ALL_APPLICABLE_CLAIMS_SUPPORTED = "ALL_APPLICABLE_CLAIMS_SUPPORTED"
BASIS_I1_NOT_APPLICABLE = "I1_NOT_APPLICABLE"
BASIS_GAP_WITHOUT_RECEIPT_ESTABLISHED = "GAP_WITHOUT_RECEIPT_ESTABLISHED"
BASIS_ALL_CONTRACT_GAPS_WITNESSED = "ALL_CONTRACT_GAPS_WITNESSED"
BASIS_AUDIT_ABSENT = "AUDIT_ABSENT"
BASIS_KERNEL_CONTRACT_PATHWAY = "KERNEL_CONTRACT_PATHWAY"
BASIS_GATE_INTERNAL_ERROR = "GATE_INTERNAL_ERROR_NOT_REVIEWABLE"
BASIS_CLAIMS_TRUNCATED = "CLAIMS_TRUNCATED"
BASIS_UNRESOLVED_TRUNCATED = "UNRESOLVED_TRUNCATED"
BASIS_COUNT_IDENTITY_VIOLATED = "COUNT_IDENTITY_VIOLATED"
BASIS_AUDIT_EVIDENCE_OUTSIDE_PREFIX = "AUDIT_EVIDENCE_OUTSIDE_PREFIX"
BASIS_SUBJECT_UNAVAILABLE = "SUBJECT_UNAVAILABLE"
BASIS_REASON_CODE_MISMATCH = "REASON_CODE_MISMATCH"
BASIS_APPLICABLE_KINDS_MISMATCH = "APPLICABLE_KINDS_MISMATCH"
BASIS_UNSUPPORTED_REASON_CODE = "UNSUPPORTED_REASON_CODE"
BASIS_KERNEL_FINALIZED_ESTABLISHED = "KERNEL_FINALIZED_ESTABLISHED"
BASIS_DECISION_SCOPE_MISMATCH_ESTABLISHED = "DECISION_SCOPE_MISMATCH_ESTABLISHED"
BASIS_KERNEL_REASON_CODE_MISMATCH = "KERNEL_REASON_CODE_MISMATCH"
BASIS_KERNEL_STATE_AT_REFUSAL_UNAVAILABLE = "KERNEL_STATE_AT_REFUSAL_UNAVAILABLE"
BASIS_KERNEL_UNSUPPORTED_REASON = "UNSUPPORTED_KERNEL_REASON"
BASIS_KERNEL_UNRESOLVED_DUPLICATE = "UNRESOLVED_DECLARATION_DUPLICATE"
BASIS_KERNEL_UNRESOLVED_TRUNCATED = "KERNEL_UNRESOLVED_TRUNCATED"
BASIS_KERNEL_SUBJECT_UNAVAILABLE = "KERNEL_SUBJECT_UNAVAILABLE"
BASIS_KERNEL_GAP_OPEN_ESTABLISHED = "EVIDENCE_GAP_OPEN_ESTABLISHED"
BASIS_KERNEL_UNRESOLVED_ESTABLISHED = "UNRESOLVED_EVIDENCE_UNBOUND_ESTABLISHED"


class RefusalReviewStatus(StrEnum):
    CORRECT = "CORRECT"
    FALSE_REFUSAL = "FALSE_REFUSAL"
    INDETERMINABLE = "INDETERMINABLE"


class RefusalReviewVerdict(BaseModel):
    """One refusal's re-derived verdict, with fixed-code evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.refusal_review.v1"] = REFUSAL_REVIEW_SCHEMA_VERSION
    run_id: StrictStr
    trace_index: Annotated[StrictInt, Field(ge=0)]
    model_request_index: StrictInt | None = None
    reason_code: StrictStr
    status: RefusalReviewStatus
    basis: tuple[StrictStr, ...]
    #: Indices of audited claims whose stored recomputability pair disagrees
    #: with the reader's own derivation. Recorded, never trusted: the verdict
    #: always follows the re-derived pair.
    recomputability_disagreements: tuple[Annotated[StrictInt, Field(ge=0)], ...] = ()


def _dedup(codes: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(codes))


def _verdict(
    run: DiagnosisRunResultAny,
    trace_index: int,
    reason_code: str,
    status: RefusalReviewStatus,
    basis: tuple[str, ...],
    model_request_index: int | None,
    disagreements: tuple[int, ...] = (),
) -> RefusalReviewVerdict:
    return RefusalReviewVerdict(
        run_id=run.diagnosis.run_id,
        trace_index=trace_index,
        model_request_index=model_request_index,
        reason_code=reason_code,
        status=status,
        basis=basis,
        recomputability_disagreements=disagreements,
    )


def _claim_disagreements(audit: RefusalAudit) -> tuple[int, ...]:
    """Re-derive every claim's recomputability; return where it disagrees."""

    disagreements: list[int] = []
    for index, claim in enumerate(audit.claims):
        derived = derive_claim_recomputability(
            claim.kind,
            claim.known_value,
            claim.unregistered_evidence_refs,
            claim.truncated_evidence_refs,
        )
        if derived != (claim.recomputable, claim.recomputability_reason):
            disagreements.append(index)
    return tuple(disagreements)


def _claims_self_consistent(audit: RefusalAudit) -> bool:
    for claim in audit.claims:
        if (
            claim.total_evidence_refs
            != len(claim.evidence_ids)
            + claim.unregistered_evidence_refs
            + claim.truncated_evidence_refs
        ):
            return False
    return True


def _review_i1(
    audit: RefusalAudit,
    scenario: ScenarioSpec,
    records_at_time: tuple[Any, ...],
) -> tuple[RefusalReviewStatus, tuple[str, ...]]:
    applicable = APPLICABLE_CLAIM_KINDS_BY_EXPECTED_STATUS.get(
        scenario.expected_status, frozenset()
    )
    if set(audit.applicable_claim_kinds) != set(applicable):
        return RefusalReviewStatus.INDETERMINABLE, (BASIS_APPLICABLE_KINDS_MISMATCH,)
    if not applicable:
        # The gate's own first rule: with no applicable claim kinds it never
        # refuses on claim support, so this refusal contradicts the contract.
        return RefusalReviewStatus.FALSE_REFUSAL, (BASIS_I1_NOT_APPLICABLE,)

    violations: list[str] = []
    blockers: list[str] = []
    inventory = {record.evidence_id: record for record in records_at_time}
    for claim in audit.claims:
        if claim.kind not in applicable:
            continue
        if claim.unregistered_evidence_refs > 0:
            violations.append(BASIS_UNREGISTERED_REF_ESTABLISHED)
            continue
        recomputable, reason = derive_claim_recomputability(
            claim.kind,
            claim.known_value,
            claim.unregistered_evidence_refs,
            claim.truncated_evidence_refs,
        )
        if not recomputable:
            blockers.append(f"CLAIM_NOT_RECOMPUTABLE:{reason}")
            continue
        value = claim.known_value
        if value is None or any(item not in inventory for item in claim.evidence_ids):
            blockers.append(BASIS_AUDIT_EVIDENCE_OUTSIDE_PREFIX)
            continue
        if claim.kind == "ROOT_CAUSE":
            rebuilt = RootCauseClaim(
                kind="ROOT_CAUSE", root_cause_code=value, evidence_ids=claim.evidence_ids
            )
        else:
            rebuilt = AffectedAssetClaim(
                kind="AFFECTED_ASSET", asset=value, evidence_ids=claim.evidence_ids
            )
        resolved = tuple(inventory[item] for item in claim.evidence_ids)
        if not claim_supported_by_records(
            scenario, rebuilt, resolved, all_records=records_at_time
        ):
            violations.append(BASIS_UNSUPPORTED_CLAIM_ESTABLISHED)
    if audit.truncated_claim_count > 0:
        blockers.append(BASIS_CLAIMS_TRUNCATED)

    if violations:
        return RefusalReviewStatus.CORRECT, _dedup(violations)
    if blockers:
        return RefusalReviewStatus.INDETERMINABLE, _dedup(blockers)
    return RefusalReviewStatus.FALSE_REFUSAL, (BASIS_ALL_APPLICABLE_CLAIMS_SUPPORTED,)


def _review_i2(
    audit: RefusalAudit,
    scenario: ScenarioSpec,
    trace_prefix: tuple[object, ...],
    diagnosis_run_schema_version: str,
) -> tuple[RefusalReviewStatus, tuple[str, ...]]:
    contract = {
        (gap.gap_kind, gap.subject, gap.reason_code): gap.tool_name
        for gap in scenario.observable_evidence_contract.unresolved_gaps
    }
    violations: list[str] = []
    blockers: list[str] = []
    for item in audit.unresolved_evidence:
        if item.subject is None:
            blockers.append(BASIS_SUBJECT_UNAVAILABLE)
            continue
        tool_name = contract.get((item.evidence_kind, item.subject, item.reason_code))
        if tool_name is None:
            continue
        if not refusal_witnessed(
            trace_prefix,
            tool_name=tool_name,
            target=item.subject,
            code=item.reason_code,
            diagnosis_run_schema_version=diagnosis_run_schema_version,
        ).witnessed:
            violations.append(BASIS_GAP_WITHOUT_RECEIPT_ESTABLISHED)
    if audit.truncated_unresolved_count > 0 and not violations:
        blockers.append(BASIS_UNRESOLVED_TRUNCATED)

    if violations:
        return RefusalReviewStatus.CORRECT, _dedup(violations)
    if blockers:
        return RefusalReviewStatus.INDETERMINABLE, _dedup(blockers)
    return RefusalReviewStatus.FALSE_REFUSAL, (BASIS_ALL_CONTRACT_GAPS_WITNESSED,)


def review_refusal_events(
    run: DiagnosisRunResultAny,
    scenario: ScenarioSpec,
) -> tuple[RefusalReviewVerdict, ...]:
    """Re-derive a verdict for every refused gate event in one archived run."""

    verdicts: list[RefusalReviewVerdict] = []
    for index, event in enumerate(run.trace):
        if not isinstance(event, EvidenceGateTraceEvent) or event.accepted:
            continue
        verdicts.append(_review_event(run, scenario, index, event))
    return tuple(verdicts)


def _review_event(
    run: DiagnosisRunResultAny,
    scenario: ScenarioSpec,
    index: int,
    event: EvidenceGateTraceEvent,
) -> RefusalReviewVerdict:
    audit = event.refusal_audit
    if event.rejected_decision is not None:
        if (
            isinstance(run, DiagnosisRunResultV3)
            and isinstance(event, EvidenceGateTraceEventV2)
            and isinstance(event.rejected_decision, RejectedDecisionSummaryV2)
        ):
            return _review_kernel_rejection(
                run, scenario, index, event, event.rejected_decision
            )
        # The kernel-contract pathway archives a rejected_decision payload
        # whose criteria belong to the kernel's own contract; re-deriving
        # those is a separate pathway, not this gate review.
        return _verdict(
            run, index, event.reason_code,
            RefusalReviewStatus.INDETERMINABLE, (BASIS_KERNEL_CONTRACT_PATHWAY,), None,
        )
    if audit is None:
        return _verdict(
            run, index, event.reason_code,
            RefusalReviewStatus.INDETERMINABLE, (BASIS_AUDIT_ABSENT,), None,
        )
    disagreements = _claim_disagreements(audit)
    if audit.reason_code != event.reason_code:
        return _verdict(
            run, index, event.reason_code,
            RefusalReviewStatus.INDETERMINABLE, (BASIS_REASON_CODE_MISMATCH,),
            audit.model_request_index, disagreements,
        )
    if not _claims_self_consistent(audit):
        return _verdict(
            run, index, event.reason_code,
            RefusalReviewStatus.INDETERMINABLE, (BASIS_COUNT_IDENTITY_VIOLATED,),
            audit.model_request_index, disagreements,
        )
    if audit.reason_code == "GATE_INTERNAL_ERROR":
        return _verdict(
            run, index, event.reason_code,
            RefusalReviewStatus.INDETERMINABLE, (BASIS_GATE_INTERNAL_ERROR,),
            audit.model_request_index, disagreements,
        )

    trace_prefix = tuple(run.trace[:index])
    tool_prefix = tuple(
        item for item in trace_prefix if isinstance(item, ToolTraceEvent)
    )
    received = {item for event_tool in tool_prefix for item in event_tool.evidence_ids}
    records_at_time = tuple(
        record for record in run.evidence_records if record.evidence_id in received
    )

    if audit.reason_code == "CLAIM_SUPPORT_REQUIRED":
        status, basis = _review_i1(audit, scenario, records_at_time)
    elif audit.reason_code == "GAP_RECEIPT_REQUIRED":
        status, basis = _review_i2(audit, scenario, trace_prefix, run.schema_version)
    else:
        status, basis = RefusalReviewStatus.INDETERMINABLE, (BASIS_UNSUPPORTED_REASON_CODE,)
    return _verdict(
        run, index, event.reason_code, status, basis,
        audit.model_request_index, disagreements,
    )


def _kernel_state_at_refusal(
    run: DiagnosisRunResultV3,
    index: int,
) -> InvestigationState | None:
    """Return the final state only when the trace proves it matches refusal time."""

    state_events = tuple(
        (position, event.state)
        for position, event in enumerate(run.trace)
        if isinstance(event, KernelStateTraceEvent)
    )
    if len(state_events) != 1:
        return None
    state_index, raw_state = state_events[0]
    if state_index <= index or run.trace[-2] != run.trace[state_index]:
        return None
    if any(isinstance(event, ToolTraceEvent) for event in run.trace[index + 1 : state_index]):
        return None
    try:
        state = InvestigationState.model_validate(raw_state)
    except Exception:
        return None
    record_ids = tuple(record.evidence_id for record in run.evidence_records)
    receipt_ids = tuple(
        evidence_id
        for event in run.trace[:index]
        if isinstance(event, ToolTraceEvent)
        and event.error_code is None
        for evidence_id in event.evidence_ids
    )
    prefix_tool_calls = sum(
        isinstance(event, ToolTraceEvent) for event in run.trace[:index]
    )
    if (
        state.run_id != run.diagnosis.run_id
        or state.evidence_inventory != record_ids
        or state.evidence_inventory != receipt_ids
        or state.tool_calls_used != prefix_tool_calls
    ):
        return None
    tool_events = tuple(event for event in run.trace if isinstance(event, ToolTraceEvent))
    if any(type(event) is not ToolTraceEventV2 for event in tool_events):
        return None
    return state


def _kernel_verdict(
    run: DiagnosisRunResultV3,
    index: int,
    event: EvidenceGateTraceEventV2,
    summary: RejectedDecisionSummaryV2,
    status: RefusalReviewStatus,
    basis: str,
) -> RefusalReviewVerdict:
    return _verdict(
        run,
        index,
        event.reason_code,
        status,
        (basis,),
        summary.model_request_index,
    )


def _review_kernel_rejection(
    run: DiagnosisRunResultV3,
    scenario: ScenarioSpec,
    index: int,
    event: EvidenceGateTraceEventV2,
    summary: RejectedDecisionSummaryV2,
) -> RefusalReviewVerdict:
    """Replay the implemented kernel-finalize prefix in original first-error order."""

    if run.strategy not in KERNEL_STRATEGIES:
        return _kernel_verdict(
            run, index, event, summary, RefusalReviewStatus.INDETERMINABLE,
            BASIS_KERNEL_UNSUPPORTED_REASON,
        )
    state = _kernel_state_at_refusal(run, index)
    if state is None:
        return _kernel_verdict(
            run, index, event, summary, RefusalReviewStatus.INDETERMINABLE,
            BASIS_KERNEL_STATE_AT_REFUSAL_UNAVAILABLE,
        )

    accepted_before = any(
        isinstance(candidate, EvidenceGateTraceEventV2) and candidate.accepted
        for candidate in run.trace[:index]
    )
    accepted_after = any(
        isinstance(candidate, EvidenceGateTraceEventV2) and candidate.accepted
        for candidate in run.trace[index + 1 : -2]
        if not isinstance(candidate, DiagnosisTerminalTraceEvent)
        and not isinstance(candidate, KernelStateTraceEvent)
    )
    if accepted_before:
        expected_finalized = True
    elif accepted_after or state.final_status is None:
        expected_finalized = False
    else:
        return _kernel_verdict(
            run, index, event, summary, RefusalReviewStatus.INDETERMINABLE,
            BASIS_KERNEL_STATE_AT_REFUSAL_UNAVAILABLE,
        )
    if expected_finalized:
        status = (
            RefusalReviewStatus.CORRECT
            if event.reason_code == "KERNEL_FINALIZED"
            else RefusalReviewStatus.FALSE_REFUSAL
        )
        return _kernel_verdict(
            run, index, event, summary, status,
            BASIS_KERNEL_FINALIZED_ESTABLISHED
            if status is RefusalReviewStatus.CORRECT
            else BASIS_KERNEL_REASON_CODE_MISMATCH,
        )
    if event.reason_code == "KERNEL_FINALIZED":
        return _kernel_verdict(
            run, index, event, summary, RefusalReviewStatus.FALSE_REFUSAL,
            BASIS_KERNEL_REASON_CODE_MISMATCH,
        )

    if not summary.decision_scope_matches_run:
        status = (
            RefusalReviewStatus.CORRECT
            if event.reason_code == "DECISION_SCOPE_MISMATCH"
            else RefusalReviewStatus.FALSE_REFUSAL
        )
        return _kernel_verdict(
            run, index, event, summary, status,
            BASIS_DECISION_SCOPE_MISMATCH_ESTABLISHED
            if status is RefusalReviewStatus.CORRECT
            else BASIS_KERNEL_REASON_CODE_MISMATCH,
        )
    if event.reason_code == "DECISION_SCOPE_MISMATCH":
        return _kernel_verdict(
            run, index, event, summary, RefusalReviewStatus.FALSE_REFUSAL,
            BASIS_KERNEL_REASON_CODE_MISMATCH,
        )

    open_gap = any(
        gap.status in {EvidenceGapStatus.OPEN, EvidenceGapStatus.BLOCKED}
        for gap in state.gaps
    )
    if event.reason_code == "EVIDENCE_GAP_OPEN":
        if summary.status == "CONFIRMED" and len(state.hypotheses) < 2:
            return _kernel_verdict(
                run, index, event, summary, RefusalReviewStatus.FALSE_REFUSAL,
                BASIS_KERNEL_REASON_CODE_MISMATCH,
            )
        expected_gap_error = (
            summary.status in {"CONFIRMED", "NO_INCIDENT"} and open_gap
        )
        status = (
            RefusalReviewStatus.CORRECT
            if expected_gap_error
            else RefusalReviewStatus.FALSE_REFUSAL
        )
        return _kernel_verdict(
            run, index, event, summary, status,
            BASIS_KERNEL_GAP_OPEN_ESTABLISHED
            if expected_gap_error
            else BASIS_KERNEL_REASON_CODE_MISMATCH,
        )

    if event.reason_code == "UNRESOLVED_EVIDENCE_UNBOUND":
        if summary.status != "INSUFFICIENT_EVIDENCE":
            return _kernel_verdict(
                run, index, event, summary, RefusalReviewStatus.FALSE_REFUSAL,
                BASIS_KERNEL_REASON_CODE_MISMATCH,
            )
        if len(state.hypotheses) < 2 or (
            not open_gap and summary.total_unresolved == 0
        ):
            return _kernel_verdict(
                run, index, event, summary, RefusalReviewStatus.FALSE_REFUSAL,
                BASIS_KERNEL_REASON_CODE_MISMATCH,
            )
        keys = tuple(
            (item.evidence_kind, subject_id, item.reason_code)
            for item, subject_id in zip(
                summary.unresolved_evidence,
                summary.unresolved_subject_equivalence,
                strict=True,
            )
        )
        if len(keys) != len(set(keys)):
            return _kernel_verdict(
                run, index, event, summary, RefusalReviewStatus.FALSE_REFUSAL,
                BASIS_KERNEL_UNRESOLVED_DUPLICATE,
            )
        if summary.truncated_unresolved_count:
            return _kernel_verdict(
                run, index, event, summary, RefusalReviewStatus.INDETERMINABLE,
                BASIS_KERNEL_UNRESOLVED_TRUNCATED,
            )
        visible = tuple(
            UnresolvedEvidence(
                evidence_kind=item.evidence_kind,
                subject=item.subject,
                reason_code=item.reason_code,
            )
            for item in summary.unresolved_evidence
            if item.subject is not None
        )
        blocked = {
            EvidenceGapKind.DISCRIMINATE_SCHEMA: frozenset(
                (gap.subject, gap.error_code)
                for gap in state.gaps
                if gap.gap_kind is EvidenceGapKind.DISCRIMINATE_SCHEMA
                and gap.status is EvidenceGapStatus.BLOCKED
            ),
            EvidenceGapKind.PROFILE_RELATION: frozenset(
                (gap.subject, gap.error_code)
                for gap in state.gaps
                if gap.gap_kind is EvidenceGapKind.PROFILE_RELATION
                and gap.status is EvidenceGapStatus.BLOCKED
            ),
            EvidenceGapKind.COMPARE_HISTORY: frozenset(
                (gap.subject, gap.error_code)
                for gap in state.gaps
                if gap.gap_kind is EvidenceGapKind.COMPARE_HISTORY
                and gap.status is EvidenceGapStatus.BLOCKED
            ),
        }
        try:
            validate_unresolved_declarations(
                ValidationContext(
                    incident_subjects=frozenset(scenario.incident_brief.subjects),
                    health_target_subjects=frozenset(),
                    incident_logical_observed_at=None,
                    incident_observations=(),
                    all_records=run.evidence_records,
                ),
                visible,
                blocked_schema=blocked[EvidenceGapKind.DISCRIMINATE_SCHEMA],
                blocked_profiles=blocked[EvidenceGapKind.PROFILE_RELATION],
                blocked_histories=blocked[EvidenceGapKind.COMPARE_HISTORY],
            )
        except KernelError as error:
            if error.code != "UNRESOLVED_EVIDENCE_UNBOUND":
                return _kernel_verdict(
                    run, index, event, summary, RefusalReviewStatus.INDETERMINABLE,
                    BASIS_KERNEL_UNSUPPORTED_REASON,
                )
            if any(item.subject is None for item in summary.unresolved_evidence):
                return _kernel_verdict(
                    run, index, event, summary, RefusalReviewStatus.CORRECT,
                    BASIS_KERNEL_UNRESOLVED_ESTABLISHED,
                )
            return _kernel_verdict(
                run, index, event, summary, RefusalReviewStatus.CORRECT,
                BASIS_KERNEL_UNRESOLVED_ESTABLISHED,
            )
        except Exception:
            return _kernel_verdict(
                run, index, event, summary, RefusalReviewStatus.INDETERMINABLE,
                BASIS_KERNEL_UNSUPPORTED_REASON,
            )
        redacted = tuple(
            item for item in summary.unresolved_evidence if item.subject is None
        )
        redacted_proven_unbound = False
        for item in redacted:
            if item.evidence_kind == "RELATION_SCHEMA":
                candidates = blocked[EvidenceGapKind.DISCRIMINATE_SCHEMA]
            elif item.evidence_kind == "RELATION_DATA_PROFILE":
                candidates = blocked[EvidenceGapKind.PROFILE_RELATION]
            elif item.evidence_kind == "RELATION_HISTORY":
                candidates = blocked[EvidenceGapKind.COMPARE_HISTORY]
            else:
                # Other validator branches bind subjects only through
                # incident_subjects or known evidence node IDs. The projection
                # always exposes those sets, so a redacted subject cannot bind.
                redacted_proven_unbound = True
                break
            if not any(reason == item.reason_code for _, reason in candidates):
                # Relation declarations bind only to a blocked gap with the
                # same reason code. Without any such gap, every hidden subject
                # is provably unbound; with one, redaction may conceal a match.
                redacted_proven_unbound = True
                break
        if redacted_proven_unbound:
            return _kernel_verdict(
                run, index, event, summary, RefusalReviewStatus.CORRECT,
                BASIS_KERNEL_UNRESOLVED_ESTABLISHED,
            )
        if redacted:
            return _kernel_verdict(
                run, index, event, summary, RefusalReviewStatus.INDETERMINABLE,
                BASIS_KERNEL_SUBJECT_UNAVAILABLE,
            )
        return _kernel_verdict(
            run, index, event, summary, RefusalReviewStatus.FALSE_REFUSAL,
            BASIS_KERNEL_REASON_CODE_MISMATCH,
        )

    return _kernel_verdict(
        run, index, event, summary, RefusalReviewStatus.INDETERMINABLE,
        BASIS_KERNEL_UNSUPPORTED_REASON,
    )


__all__ = [
    "BASIS_ALL_APPLICABLE_CLAIMS_SUPPORTED",
    "BASIS_ALL_CONTRACT_GAPS_WITNESSED",
    "BASIS_AUDIT_ABSENT",
    "BASIS_AUDIT_EVIDENCE_OUTSIDE_PREFIX",
    "BASIS_CLAIMS_TRUNCATED",
    "BASIS_COUNT_IDENTITY_VIOLATED",
    "BASIS_DECISION_SCOPE_MISMATCH_ESTABLISHED",
    "BASIS_GATE_INTERNAL_ERROR",
    "BASIS_I1_NOT_APPLICABLE",
    "BASIS_KERNEL_CONTRACT_PATHWAY",
    "BASIS_KERNEL_FINALIZED_ESTABLISHED",
    "BASIS_KERNEL_GAP_OPEN_ESTABLISHED",
    "BASIS_KERNEL_REASON_CODE_MISMATCH",
    "BASIS_KERNEL_STATE_AT_REFUSAL_UNAVAILABLE",
    "BASIS_KERNEL_SUBJECT_UNAVAILABLE",
    "BASIS_KERNEL_UNRESOLVED_DUPLICATE",
    "BASIS_KERNEL_UNRESOLVED_ESTABLISHED",
    "BASIS_KERNEL_UNRESOLVED_TRUNCATED",
    "BASIS_KERNEL_UNSUPPORTED_REASON",
    "BASIS_REASON_CODE_MISMATCH",
    "BASIS_SUBJECT_UNAVAILABLE",
    "BASIS_UNREGISTERED_REF_ESTABLISHED",
    "BASIS_UNSUPPORTED_CLAIM_ESTABLISHED",
    "BASIS_UNSUPPORTED_REASON_CODE",
    "BASIS_UNRESOLVED_TRUNCATED",
    "RefusalReviewStatus",
    "RefusalReviewVerdict",
    "review_refusal_events",
]
