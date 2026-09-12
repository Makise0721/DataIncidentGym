"""Shared data contracts for the diagnostic kernel and its validators.

This module owns the kernel's public Pydantic models and error type. It keeps
the exact field shapes and validation logic so the kernel entrypoint can focus
on investigation lifecycle while validators share the same contracts.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from data_incident_gym.diagnosis import KernelStateTraceEvent, NonBlankStr, UnresolvedEvidence

_RUN_ID_PATTERN = r"^[0-9a-f]{32}$"
_HYPOTHESIS_ID_PATTERN = r"^h_[a-z0-9_]{1,32}$"
_GAP_ID_PATTERN = r"^g_[a-z0-9_]{1,32}$"
_EVIDENCE_ID_PATTERN = r"^ev_[0-9a-f]{64}$"
_FINGERPRINT_PATTERN = r"^[0-9a-f]{64}$"
_ROOT_CAUSE_PATTERN = r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$"


class EvidenceGapKind(StrEnum):
    LOCATE_FAILURE = "LOCATE_FAILURE"
    EXPLAIN_FAILURE = "EXPLAIN_FAILURE"
    DISCOVER_SOURCE_RELATION = "DISCOVER_SOURCE_RELATION"
    DISCRIMINATE_SCHEMA = "DISCRIMINATE_SCHEMA"
    MAP_IMPACT = "MAP_IMPACT"
    PROFILE_RELATION = "PROFILE_RELATION"
    COMPARE_HISTORY = "COMPARE_HISTORY"


class EvidenceGapStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    BLOCKED = "BLOCKED"


class HypothesisVerdict(StrEnum):
    SUPPORTED = "SUPPORTED"
    REFUTED = "REFUTED"


class ClaimKind(StrEnum):
    ROOT_CAUSE = "ROOT_CAUSE"
    AFFECTED_ASSET = "AFFECTED_ASSET"
    HEALTH_STATE = "HEALTH_STATE"


class KernelFinalStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NO_INCIDENT = "NO_INCIDENT"
    MODEL_ERROR = "MODEL_ERROR"


def reject_duplicates(values: tuple[object, ...], field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")


# Model-level validation rules, keyed by the exact message each one raises, so a
# rejection can be classified offline without keeping the rejected payload. The
# guard in tests/unit/test_model_rule_reason_codes.py extracts the message
# literals from this module and fails when a rule arrives without a code.
MODEL_RULE_REASONS: dict[str, str] = {
    "assessment evidence_ids must not be empty": "ASSESSMENT_EVIDENCE_EMPTY",
    "assessment evidence_ids must not contain duplicates": "ASSESSMENT_EVIDENCE_DUPLICATED",
    "assessment hypothesis IDs must not contain duplicates": "ASSESSMENT_HYPOTHESES_DUPLICATED",
    "claim evidence_ids must not contain duplicates": "CLAIM_EVIDENCE_DUPLICATED",
    "claim kind/value pairs must not contain duplicates": "CLAIM_VALUES_DUPLICATED",
    "recommended_actions must not contain duplicates": "RECOMMENDED_ACTIONS_DUPLICATED",
    "health claim requires relation/history/bucket/value": "HEALTH_CLAIM_INCOMPLETE",
    "decision text must not be blank": "DECISION_TEXT_BLANK",
    "CONFIRMED requires selected_hypothesis_id": "CONFIRMED_WITHOUT_SELECTION",
    "non-confirmed decision cannot select a hypothesis": "NON_CONFIRMED_SELECTS_HYPOTHESIS",
    "INSUFFICIENT_EVIDENCE cannot contain claims": "ABSTENTION_WITH_CLAIMS",
    "only INSUFFICIENT_EVIDENCE can declare unresolved evidence": "UNRESOLVED_ON_NON_ABSTENTION",
    "NO_INCIDENT can contain only health claims": "NO_INCIDENT_WITH_OTHER_CLAIMS",
}
UNCLASSIFIED_MODEL_RULE = "UNCLASSIFIED_MODEL_RULE"

# Rules the Pydantic parser raises before any of our validators run. They are
# keyed by error type, not by message, so a change in the SDK's wording cannot
# silently turn a known failure into an unclassified one.
MODEL_ERROR_TYPE_REASONS: dict[str, str] = {
    "extra_forbidden": "UNEXPECTED_DECISION_FIELD",
}


_GAP_TOOL: dict[EvidenceGapKind, tuple[str, str | None]] = {
    EvidenceGapKind.LOCATE_FAILURE: ("get_dbt_run_results", None),
    EvidenceGapKind.EXPLAIN_FAILURE: ("get_dbt_node_error", None),
    EvidenceGapKind.DISCOVER_SOURCE_RELATION: ("get_dbt_lineage", "upstream"),
    EvidenceGapKind.DISCRIMINATE_SCHEMA: ("get_relation_schema", None),
    EvidenceGapKind.MAP_IMPACT: ("get_dbt_lineage", "downstream"),
    EvidenceGapKind.PROFILE_RELATION: ("get_relation_data_profile", None),
    EvidenceGapKind.COMPARE_HISTORY: ("get_relation_history", None),
}


def expected_tool_for_gap(gap_kind: EvidenceGapKind) -> tuple[str, str | None]:
    """Return the business tool (and fixed direction, if any) a gap kind maps to."""

    return _GAP_TOOL[gap_kind]


def gap_kind_for_tool(tool_name: str, arguments: dict[str, str]) -> EvidenceGapKind:
    """Derive the gap kind from the business tool call; never guess."""

    for gap_kind, (required_tool, direction) in _GAP_TOOL.items():
        if required_tool == tool_name and (
            direction is None or arguments.get("direction") == direction
        ):
            return gap_kind
    raise KernelError("GAP_TOOL_MISMATCH") from None


class Hypothesis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hypothesis_id: StrictStr = Field(pattern=_HYPOTHESIS_ID_PATTERN)
    root_cause_code: StrictStr = Field(pattern=_ROOT_CAUSE_PATTERN)


class HypothesisAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hypothesis_id: StrictStr = Field(pattern=_HYPOTHESIS_ID_PATTERN)
    verdict: HypothesisVerdict
    evidence_ids: tuple[Annotated[StrictStr, Field(pattern=_EVIDENCE_ID_PATTERN)], ...]

    @model_validator(mode="after")
    def reject_duplicate_evidence_ids(self) -> Self:
        if not self.evidence_ids:
            raise ValueError("assessment evidence_ids must not be empty")
        reject_duplicates(self.evidence_ids, "assessment evidence_ids")
        return self


class InvestigationIntent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.kernel_intent.v1"] = "p1.kernel_intent.v1"
    gap_id: StrictStr = Field(pattern=_GAP_ID_PATTERN)
    gap_kind: EvidenceGapKind
    hypothesis_ids: tuple[
        Annotated[StrictStr, Field(pattern=_HYPOTHESIS_ID_PATTERN)],
        ...,
    ] = ()
    new_hypotheses: tuple[Hypothesis, ...] = ()

    @model_validator(mode="after")
    def reject_duplicate_ids(self) -> Self:
        reject_duplicates(self.hypothesis_ids, "intent hypothesis_ids")
        reject_duplicates(
            tuple(item.hypothesis_id for item in self.new_hypotheses),
            "new hypothesis IDs",
        )
        return self


class EvidenceGap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gap_id: StrictStr = Field(pattern=_GAP_ID_PATTERN)
    gap_kind: EvidenceGapKind
    hypothesis_ids: tuple[
        Annotated[StrictStr, Field(pattern=_HYPOTHESIS_ID_PATTERN)],
        ...,
    ]
    tool_name: StrictStr
    subject: StrictStr
    status: EvidenceGapStatus
    evidence_ids: tuple[
        Annotated[StrictStr, Field(pattern=_EVIDENCE_ID_PATTERN)],
        ...,
    ] = ()
    error_code: StrictStr | None = None

    @model_validator(mode="after")
    def reject_duplicate_ids(self) -> Self:
        reject_duplicates(self.hypothesis_ids, "gap hypothesis_ids")
        reject_duplicates(self.evidence_ids, "gap evidence_ids")
        return self


class ClaimEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ClaimKind
    value: StrictStr
    evidence_ids: tuple[
        Annotated[StrictStr, Field(pattern=_EVIDENCE_ID_PATTERN)],
        ...,
    ]
    relation_name: StrictStr | None = None
    history_name: StrictStr | None = None
    bucket: StrictStr | None = None
    current_value: StrictInt | StrictFloat | None = None

    @model_validator(mode="after")
    def reject_duplicate_evidence_ids(self) -> Self:
        reject_duplicates(self.evidence_ids, "claim evidence_ids")
        if self.kind is ClaimKind.HEALTH_STATE and (
            not self.relation_name
            or not self.history_name
            or not self.bucket
            or self.current_value is None
        ):
            raise ValueError("health claim requires relation/history/bucket/value")
        return self


class KernelDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.kernel_decision.v1"] = "p1.kernel_decision.v1"
    status: Literal["CONFIRMED", "INSUFFICIENT_EVIDENCE", "NO_INCIDENT"]
    run_id: StrictStr = Field(pattern=_RUN_ID_PATTERN)
    selected_hypothesis_id: StrictStr | None = None
    assessments: tuple[HypothesisAssessment, ...] = ()
    claims: tuple[ClaimEvidence, ...] = ()
    unresolved_evidence: tuple[UnresolvedEvidence, ...] = ()
    summary: StrictStr
    recommended_actions: tuple[StrictStr, ...]
    confidence: Annotated[StrictFloat, Field(ge=0.0, le=1.0)]

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        reject_duplicates(
            tuple(item.hypothesis_id for item in self.assessments),
            "assessment hypothesis IDs",
        )
        reject_duplicates(
            tuple((item.kind, item.value) for item in self.claims),
            "claim kind/value pairs",
        )
        reject_duplicates(self.recommended_actions, "recommended_actions")
        if not self.summary.strip() or any(not item.strip() for item in self.recommended_actions):
            raise ValueError("decision text must not be blank")
        if self.status == "CONFIRMED" and self.selected_hypothesis_id is None:
            raise ValueError("CONFIRMED requires selected_hypothesis_id")
        if self.status != "CONFIRMED" and self.selected_hypothesis_id is not None:
            raise ValueError("non-confirmed decision cannot select a hypothesis")
        if self.status == "INSUFFICIENT_EVIDENCE" and self.claims:
            raise ValueError("INSUFFICIENT_EVIDENCE cannot contain claims")
        if self.status != "INSUFFICIENT_EVIDENCE" and self.unresolved_evidence:
            raise ValueError("only INSUFFICIENT_EVIDENCE can declare unresolved evidence")
        if self.status == "NO_INCIDENT" and any(
            item.kind is not ClaimKind.HEALTH_STATE for item in self.claims
        ):
            raise ValueError("NO_INCIDENT can contain only health claims")
        return self


class UnobservedFact(BaseModel):
    """An independent fact this run cannot observe; never a relation receipt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_kind: Literal[
        "INGESTION_WATERMARK",
        "PAYMENT_EVENT_IDENTITY",
        "TRANSFORMATION_DEFINITION",
    ]
    subject: NonBlankStr


class DecisionClaim(BaseModel):
    """A root-cause or affected-asset claim: no health fields, no health kind."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["ROOT_CAUSE", "AFFECTED_ASSET"]
    value: StrictStr
    evidence_ids: tuple[
        Annotated[StrictStr, Field(pattern=_EVIDENCE_ID_PATTERN)],
        ...,
    ]
    relation_name: StrictStr | None = None

    @model_validator(mode="after")
    def reject_duplicate_evidence_ids(self) -> Self:
        reject_duplicates(self.evidence_ids, "claim evidence_ids")
        return self


class HealthClaim(BaseModel):
    """A health claim; the claim kind and the value are fixed, not submitted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    relation_name: NonBlankStr
    history_name: NonBlankStr
    bucket: NonBlankStr
    current_value: StrictInt | StrictFloat
    evidence_ids: tuple[
        Annotated[StrictStr, Field(pattern=_EVIDENCE_ID_PATTERN)],
        ...,
    ]

    @model_validator(mode="after")
    def reject_duplicate_evidence_ids(self) -> Self:
        reject_duplicates(self.evidence_ids, "claim evidence_ids")
        return self


class DecisionSubmission(BaseModel):
    """Fields every submission carries; the terminal status comes from the tool."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.kernel_decision.v1"] = "p1.kernel_decision.v1"
    run_id: StrictStr = Field(pattern=_RUN_ID_PATTERN)
    assessments: tuple[HypothesisAssessment, ...] = ()
    summary: StrictStr
    recommended_actions: tuple[StrictStr, ...]
    confidence: Annotated[StrictFloat, Field(ge=0.0, le=1.0)]


class AbstentionSubmission(DecisionSubmission):
    unresolved_evidence: tuple[UnobservedFact, ...] = ()


class ConfirmedSubmission(DecisionSubmission):
    selected_hypothesis_id: StrictStr
    claims: tuple[DecisionClaim, ...] = Field(min_length=1)


class HealthSubmission(DecisionSubmission):
    claims: tuple[HealthClaim, ...] = Field(min_length=1)


Submission = AbstentionSubmission | ConfirmedSubmission | HealthSubmission


def to_kernel_decision(submission: object) -> KernelDecision:
    """Map one submission onto the internal decision.

    Pure mapping: every submitted field is carried over unchanged, and only the
    constants the submitting tool already fixes are filled in. It performs no
    I/O and touches no run state; a payload that violates a decision-level rule
    raises ``ValidationError`` and is classified by the caller.
    """

    if isinstance(submission, ConfirmedSubmission):
        return KernelDecision(
            schema_version=submission.schema_version,
            status="CONFIRMED",
            run_id=submission.run_id,
            selected_hypothesis_id=submission.selected_hypothesis_id,
            assessments=submission.assessments,
            claims=tuple(
                ClaimEvidence(
                    kind=ClaimKind(claim.kind),
                    value=claim.value,
                    evidence_ids=claim.evidence_ids,
                    relation_name=claim.relation_name,
                )
                for claim in submission.claims
            ),
            summary=submission.summary,
            recommended_actions=submission.recommended_actions,
            confidence=submission.confidence,
        )
    if isinstance(submission, HealthSubmission):
        return KernelDecision(
            schema_version=submission.schema_version,
            status="NO_INCIDENT",
            run_id=submission.run_id,
            assessments=submission.assessments,
            claims=tuple(
                ClaimEvidence(
                    kind=ClaimKind.HEALTH_STATE,
                    # The value is not part of the model's submission and is not
                    # read by the kernel or the evaluator for health claims; it
                    # is filled from the relation to keep the artifact's shape.
                    value=claim.relation_name,
                    evidence_ids=claim.evidence_ids,
                    relation_name=claim.relation_name,
                    history_name=claim.history_name,
                    bucket=claim.bucket,
                    current_value=claim.current_value,
                )
                for claim in submission.claims
            ),
            summary=submission.summary,
            recommended_actions=submission.recommended_actions,
            confidence=submission.confidence,
        )
    if isinstance(submission, AbstentionSubmission):
        return KernelDecision(
            schema_version=submission.schema_version,
            status="INSUFFICIENT_EVIDENCE",
            run_id=submission.run_id,
            assessments=submission.assessments,
            unresolved_evidence=tuple(
                UnresolvedEvidence(
                    evidence_kind=fact.evidence_kind,
                    subject=fact.subject,
                    reason_code="NOT_OBSERVABLE",
                )
                for fact in submission.unresolved_evidence
            ),
            summary=submission.summary,
            recommended_actions=submission.recommended_actions,
            confidence=submission.confidence,
        )
    raise TypeError("submission type is not a decision submission")


class PreparedToolCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gap_id: StrictStr = Field(pattern=_GAP_ID_PATTERN)
    tool_name: StrictStr
    arguments: dict[StrictStr, StrictStr]
    fingerprint: StrictStr = Field(pattern=_FINGERPRINT_PATTERN)


class InvestigationState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.investigation.v1"]
    run_id: StrictStr = Field(pattern=_RUN_ID_PATTERN)
    revision: Annotated[StrictInt, Field(ge=0)]
    allowed_root_cause_codes: tuple[StrictStr, ...]
    lineage_node_candidates: tuple[StrictStr, ...] = ()
    hypotheses: tuple[Hypothesis, ...]
    gaps: tuple[EvidenceGap, ...]
    assessments: tuple[HypothesisAssessment, ...]
    claims: tuple[ClaimEvidence, ...]
    evidence_inventory: tuple[StrictStr, ...]
    tool_fingerprints: tuple[StrictStr, ...]
    model_request_limit: Annotated[StrictInt, Field(gt=0)]
    model_requests_used: Annotated[StrictInt, Field(ge=0)]
    model_requests_remaining: Annotated[StrictInt, Field(ge=0)]
    tool_call_limit: Annotated[StrictInt, Field(gt=0)]
    tool_calls_used: Annotated[StrictInt, Field(ge=0)]
    tool_calls_remaining: Annotated[StrictInt, Field(ge=0)]
    final_status: KernelFinalStatus | None
    gate_reason: StrictStr | None
    selected_hypothesis_id: StrictStr | None

    @model_validator(mode="after")
    def validate_invariants(self) -> Self:
        reject_duplicates(self.allowed_root_cause_codes, "ontology members")
        reject_duplicates(self.lineage_node_candidates, "lineage node candidates")
        reject_duplicates(tuple(item.hypothesis_id for item in self.hypotheses), "hypothesis IDs")
        reject_duplicates(tuple(item.gap_id for item in self.gaps), "gap IDs")
        reject_duplicates(self.evidence_inventory, "evidence inventory IDs")
        reject_duplicates(self.tool_fingerprints, "tool fingerprints")
        if self.model_requests_used > self.model_request_limit:
            raise ValueError("model request usage exceeds limit")
        if self.model_requests_remaining != self.model_request_limit - self.model_requests_used:
            raise ValueError("model request remaining count is inconsistent")
        if self.tool_calls_used > self.tool_call_limit:
            raise ValueError("tool call usage exceeds limit")
        if self.tool_calls_remaining != self.tool_call_limit - self.tool_calls_used:
            raise ValueError("tool call remaining count is inconsistent")
        if self.selected_hypothesis_id is not None and self.selected_hypothesis_id not in {
            item.hypothesis_id for item in self.hypotheses
        }:
            raise ValueError("selected hypothesis must be registered")
        return self


class KernelOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: KernelFinalStatus
    root_cause_code: StrictStr | None
    affected_assets: tuple[StrictStr, ...]
    evidence_ids: tuple[StrictStr, ...]
    unresolved_evidence: tuple[UnresolvedEvidence, ...] = ()
    summary: StrictStr
    recommended_actions: tuple[StrictStr, ...]
    confidence: Annotated[StrictFloat, Field(ge=0.0, le=1.0)]


class KernelError(RuntimeError):
    def __init__(self, code: str, *, fingerprint: str | None = None) -> None:
        self.code = code
        self.fingerprint = fingerprint
        super().__init__(code)
        self.__cause__ = None
        self.__context__ = None


__all__ = [
    "AbstentionSubmission",
    "ClaimEvidence",
    "ClaimKind",
    "ConfirmedSubmission",
    "DecisionClaim",
    "DecisionSubmission",
    "EvidenceGap",
    "EvidenceGapKind",
    "EvidenceGapStatus",
    "HealthClaim",
    "HealthSubmission",
    "Hypothesis",
    "HypothesisAssessment",
    "HypothesisVerdict",
    "InvestigationIntent",
    "InvestigationState",
    "KernelDecision",
    "KernelError",
    "KernelFinalStatus",
    "KernelOutcome",
    "KernelStateTraceEvent",
    "MODEL_ERROR_TYPE_REASONS",
    "PreparedToolCall",
    "Submission",
    "UnobservedFact",
    "expected_tool_for_gap",
    "gap_kind_for_tool",
    "reject_duplicates",
    "to_kernel_decision",
]
