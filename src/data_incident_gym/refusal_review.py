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
- ``INDETERMINABLE`` — the audit is absent, truncated, redacted or
  self-inconsistent. Indeterminable is never counted as correct.

The audit's bounded projections (claims, unresolved gaps) are trusted as the
refused submission's own record except where a cap dropped items; that is the
P-1 design boundary (the refused submission itself is not archived).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from data_incident_gym.diagnosis import (
    AffectedAssetClaim,
    DiagnosisRunResult,
    EvidenceGateTraceEvent,
    RefusalAudit,
    RootCauseClaim,
    ToolTraceEvent,
    derive_claim_recomputability,
    refusal_witnessed,
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
    run: DiagnosisRunResult,
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
    tool_prefix: tuple[ToolTraceEvent, ...],
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
            tool_prefix,
            tool_name=tool_name,
            target=item.subject,
            code=item.reason_code,
        ):
            violations.append(BASIS_GAP_WITHOUT_RECEIPT_ESTABLISHED)
    if audit.truncated_unresolved_count > 0 and not violations:
        blockers.append(BASIS_UNRESOLVED_TRUNCATED)

    if violations:
        return RefusalReviewStatus.CORRECT, _dedup(violations)
    if blockers:
        return RefusalReviewStatus.INDETERMINABLE, _dedup(blockers)
    return RefusalReviewStatus.FALSE_REFUSAL, (BASIS_ALL_CONTRACT_GAPS_WITNESSED,)


def review_refusal_events(
    run: DiagnosisRunResult,
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
    run: DiagnosisRunResult,
    scenario: ScenarioSpec,
    index: int,
    event: EvidenceGateTraceEvent,
) -> RefusalReviewVerdict:
    audit = event.refusal_audit
    if event.rejected_decision is not None:
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

    tool_prefix = tuple(
        item for item in run.trace[:index] if isinstance(item, ToolTraceEvent)
    )
    received = {item for event_tool in tool_prefix for item in event_tool.evidence_ids}
    records_at_time = tuple(
        record for record in run.evidence_records if record.evidence_id in received
    )

    if audit.reason_code == "CLAIM_SUPPORT_REQUIRED":
        status, basis = _review_i1(audit, scenario, records_at_time)
    elif audit.reason_code == "GAP_RECEIPT_REQUIRED":
        status, basis = _review_i2(audit, scenario, tool_prefix)
    else:
        status, basis = RefusalReviewStatus.INDETERMINABLE, (BASIS_UNSUPPORTED_REASON_CODE,)
    return _verdict(
        run, index, event.reason_code, status, basis,
        audit.model_request_index, disagreements,
    )


__all__ = [
    "BASIS_ALL_APPLICABLE_CLAIMS_SUPPORTED",
    "BASIS_ALL_CONTRACT_GAPS_WITNESSED",
    "BASIS_AUDIT_ABSENT",
    "BASIS_AUDIT_EVIDENCE_OUTSIDE_PREFIX",
    "BASIS_CLAIMS_TRUNCATED",
    "BASIS_COUNT_IDENTITY_VIOLATED",
    "BASIS_GATE_INTERNAL_ERROR",
    "BASIS_I1_NOT_APPLICABLE",
    "BASIS_KERNEL_CONTRACT_PATHWAY",
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
