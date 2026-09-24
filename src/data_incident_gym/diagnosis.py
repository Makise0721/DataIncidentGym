from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    AliasChoices,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from data_incident_gym.evidence import (
    EVIDENCE_BATCH_TOOLS,
    TARGETS_REFUSED_CODE,
    EvidenceRecord,
)

RUN_ID_PATTERN = r"^[0-9a-f]{32}$"
EVIDENCE_ID_PATTERN = r"^ev_[0-9a-f]{64}$"
ROOT_CAUSE_CODE_PATTERN = r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$"
_DIGEST_PATTERN = r"^[0-9a-f]{64}$"


def _non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


def _strict_finite_float(value: object) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise ValueError("confidence must be a finite float")
    return value


NonBlankStr = Annotated[StrictStr, AfterValidator(_non_blank)]
RunId = Annotated[NonBlankStr, Field(pattern=RUN_ID_PATTERN)]
EvidenceId = Annotated[NonBlankStr, Field(pattern=EVIDENCE_ID_PATTERN)]
RootCauseCode = Annotated[NonBlankStr, Field(pattern=ROOT_CAUSE_CODE_PATTERN)]
Digest = Annotated[StrictStr, Field(pattern=_DIGEST_PATTERN)]
Confidence = Annotated[
    StrictFloat,
    BeforeValidator(_strict_finite_float),
    Field(ge=0.0, le=1.0),
]


class DiagnosisStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NO_INCIDENT = "NO_INCIDENT"
    MODEL_ERROR = "MODEL_ERROR"


class DiagnosticStrategy(StrEnum):
    STATIC_SKILL = "STATIC_SKILL"
    DIAGNOSTIC_KERNEL = "DIAGNOSTIC_KERNEL"
    NO_TOOL = "NO_TOOL"
    KERNEL_NO_LINEAGE = "KERNEL_NO_LINEAGE"
    KERNEL_NO_SCHEMA = "KERNEL_NO_SCHEMA"
    FIXED_RULE = "FIXED_RULE"
    REFERENCE_ANALYST = "REFERENCE_ANALYST"
    #: T12: a model strategy whose evidence requests are declared as
    #: obligations and validated before execution (``evidence_planner.py``). It
    #: runs through its own runner and stays out of ``MAIN_STRATEGIES`` and the
    #: frozen manifest policies until a new schedule identity is approved.
    EVIDENCE_PLANNER = "EVIDENCE_PLANNER"


MAIN_STRATEGIES = (
    DiagnosticStrategy.STATIC_SKILL,
    DiagnosticStrategy.DIAGNOSTIC_KERNEL,
)
MODEL_STRATEGIES = (
    *MAIN_STRATEGIES,
    DiagnosticStrategy.NO_TOOL,
    DiagnosticStrategy.KERNEL_NO_LINEAGE,
    DiagnosticStrategy.KERNEL_NO_SCHEMA,
    DiagnosticStrategy.EVIDENCE_PLANNER,
)
KERNEL_STRATEGIES = (
    DiagnosticStrategy.DIAGNOSTIC_KERNEL,
    DiagnosticStrategy.KERNEL_NO_LINEAGE,
    DiagnosticStrategy.KERNEL_NO_SCHEMA,
)


class RootCauseClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["ROOT_CAUSE"]
    root_cause_code: RootCauseCode = Field(
        validation_alias=AliasChoices("root_cause_code", "value")
    )
    evidence_ids: tuple[EvidenceId, ...] = Field(min_length=1)

    @property
    def value(self) -> str:
        return self.root_cause_code

    @model_validator(mode="after")
    def reject_duplicates(self) -> RootCauseClaim:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("claim evidence_ids must not contain duplicates")
        return self


class AffectedAssetClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["AFFECTED_ASSET"]
    asset: NonBlankStr = Field(validation_alias=AliasChoices("asset", "value"))
    evidence_ids: tuple[EvidenceId, ...] = Field(min_length=1)

    @property
    def value(self) -> str:
        return self.asset

    @model_validator(mode="after")
    def reject_duplicates(self) -> AffectedAssetClaim:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("claim evidence_ids must not contain duplicates")
        return self


class HealthStateClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["HEALTH_STATE"]
    relation_name: NonBlankStr
    history_name: NonBlankStr
    bucket: NonBlankStr
    current_value: StrictInt | StrictFloat
    evidence_ids: tuple[EvidenceId, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_duplicates(self) -> HealthStateClaim:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("health claim evidence_ids must not contain duplicates")
        return self


DiagnosisClaim = Annotated[
    RootCauseClaim | AffectedAssetClaim | HealthStateClaim,
    Field(discriminator="kind"),
]


# The v1 model-visible gap vocabulary; part of every v1 surface schema.
# T13's two new facts live in ``UnresolvedEvidenceV2`` instead of here: adding
# them to this shared model changed ``Diagnosis.model_json_schema()`` and with
# it the policy identity of several frozen strategies (audit finding on
# ``da5b9a4``). A docstring on this class would show up as a schema description
# and drift the same digests, so the note stays a comment. v1 stays
# byte-identical; v2 surfaces use the separate model.
class UnresolvedEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_kind: Literal[
        "RELATION_SCHEMA",
        "RELATION_DATA_PROFILE",
        "RELATION_HISTORY",
        "INGESTION_WATERMARK",
        "TRANSFORMATION_DEFINITION",
        "PAYMENT_EVENT_IDENTITY",
    ]
    subject: NonBlankStr
    reason_code: Literal["NOT_OBSERVABLE", "RELATION_NOT_ALLOWED"]

    @model_validator(mode="after")
    def validate_reason_code(self) -> UnresolvedEvidence:
        if self.evidence_kind in {"INGESTION_WATERMARK", "PAYMENT_EVENT_IDENTITY"} and (
            self.reason_code != "NOT_OBSERVABLE"
        ):
            raise ValueError(f"{self.evidence_kind} requires NOT_OBSERVABLE")
        return self


class UnresolvedEvidenceV2(BaseModel):
    """The v2 model-visible gap vocabulary (v1 kinds plus the T13 facts).

    Not referenced by ``Diagnosis`` or any v1 surface; it exists so the v2
    strategy contract (slice 4 of the T13 design) can declare expectation and
    definition gaps without touching the frozen v1 schemas.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_kind: Literal[
        "RELATION_SCHEMA",
        "RELATION_DATA_PROFILE",
        "RELATION_HISTORY",
        "INGESTION_WATERMARK",
        "TRANSFORMATION_DEFINITION",
        "PAYMENT_EVENT_IDENTITY",
        "RELATION_SCHEMA_EXPECTATION",
        "DBT_NODE_DEFINITION",
    ]
    subject: NonBlankStr
    reason_code: Literal["NOT_OBSERVABLE", "RELATION_NOT_ALLOWED", "NODE_NOT_ALLOWED"]

    @model_validator(mode="after")
    def validate_reason_code(self) -> UnresolvedEvidenceV2:
        if self.evidence_kind in {"INGESTION_WATERMARK", "PAYMENT_EVENT_IDENTITY"} and (
            self.reason_code != "NOT_OBSERVABLE"
        ):
            raise ValueError(f"{self.evidence_kind} requires NOT_OBSERVABLE")
        return self


_MODEL_ERROR_CODES = {
    "MODEL_DECLINED",
    "MODEL_REQUEST_LIMIT",
    "MODEL_TOOL_CALL_LIMIT",
    "MODEL_TIMEOUT",
    "MODEL_PROTOCOL_ERROR",
    # D2 submission gates: the output-retry budget was spent after gate
    # refusals; the terminal must name the cause instead of a silent accept.
    "MODEL_OUTPUT_RETRY_EXHAUSTED",
    "MODEL_RUNTIME_ERROR",
    "RUN_SETUP_ERROR",
}


class Diagnosis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.diagnosis.v1"] = "p1.diagnosis.v1"
    status: DiagnosisStatus
    run_id: RunId
    root_cause_code: RootCauseCode | None = None
    summary: NonBlankStr
    affected_assets: tuple[NonBlankStr, ...] = ()
    evidence_ids: tuple[EvidenceId, ...] = ()
    claims: tuple[DiagnosisClaim, ...] = ()
    unresolved_evidence: tuple[UnresolvedEvidence, ...] = ()
    recommended_actions: tuple[NonBlankStr, ...] = ()
    confidence: Confidence

    @model_validator(mode="after")
    def validate_contract(self) -> Diagnosis:
        for field_name in (
            "affected_assets",
            "evidence_ids",
            "claims",
            "unresolved_evidence",
            "recommended_actions",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must not contain duplicates")
        claim_keys = tuple(
            (
                claim.kind,
                getattr(claim, "root_cause_code", None),
                getattr(claim, "asset", None),
                getattr(claim, "relation_name", None),
                getattr(claim, "history_name", None),
                getattr(claim, "bucket", None),
            )
            for claim in self.claims
        )
        if len(claim_keys) != len(set(claim_keys)):
            raise ValueError("claims must not contain duplicates")

        root_claims = tuple(claim for claim in self.claims if claim.kind == "ROOT_CAUSE")
        asset_claims = tuple(claim for claim in self.claims if claim.kind == "AFFECTED_ASSET")
        health_claims = tuple(claim for claim in self.claims if claim.kind == "HEALTH_STATE")
        projected_root = root_claims[0].root_cause_code if len(root_claims) == 1 else None
        projected_assets = tuple(claim.asset for claim in asset_claims)
        projected_evidence = tuple(
            dict.fromkeys(
                evidence_id
                for claim in self.claims
                for evidence_id in claim.evidence_ids
            )
        )
        if self.status is DiagnosisStatus.CONFIRMED:
            if len(root_claims) != 1 or self.root_cause_code != projected_root:
                raise ValueError("CONFIRMED requires exactly one projected root cause")
            if not asset_claims or self.affected_assets != projected_assets:
                raise ValueError("CONFIRMED affected assets must project from claims")
            if not self.evidence_ids or not set(projected_evidence).issubset(self.evidence_ids):
                raise ValueError("CONFIRMED evidence_ids must cover claim evidence")
            if self.unresolved_evidence:
                raise ValueError("CONFIRMED cannot contain unresolved evidence")
        elif self.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE:
            if self.root_cause_code is not None or self.affected_assets or self.claims:
                raise ValueError("INSUFFICIENT_EVIDENCE cannot contain claims")
            if not self.unresolved_evidence:
                raise ValueError("INSUFFICIENT_EVIDENCE requires unresolved evidence")
        elif self.status is DiagnosisStatus.NO_INCIDENT:
            if self.root_cause_code is not None or self.affected_assets:
                raise ValueError("NO_INCIDENT cannot contain root or asset claims")
            if not health_claims or len(health_claims) != len(self.claims):
                raise ValueError("NO_INCIDENT requires only health claims")
            if not self.evidence_ids or self.evidence_ids != projected_evidence:
                raise ValueError("NO_INCIDENT evidence_ids must project from health claims")
            if self.unresolved_evidence:
                raise ValueError("NO_INCIDENT cannot contain unresolved evidence")
        elif self.status is DiagnosisStatus.MODEL_ERROR:
            if self.summary not in _MODEL_ERROR_CODES:
                raise ValueError("MODEL_ERROR summary must be a fixed safe reason code")
            if (
                self.root_cause_code is not None
                or self.affected_assets
                or self.claims
                or self.unresolved_evidence
            ):
                raise ValueError("MODEL_ERROR cannot contain business claims")
        return self


class DiagnosisV2(Diagnosis):
    """The v2 model-visible diagnosis contract (T13; wired in slice 4).

    Same shape, status rules and projection checks as ``Diagnosis`` — only the
    unresolved-evidence vocabulary is the v2 one. ``Diagnosis`` itself stays
    byte-identical, so every frozen v1 surface (policy identity, final-diagnosis
    schema digest) keeps matching the sealed manifests.

    ``schema_version`` is the contract's own marker: loaders that revalidate a
    persisted diagnosis pick the class by this field, never by content.
    """

    schema_version: Literal["p1.diagnosis.v2"] = "p1.diagnosis.v2"
    unresolved_evidence: tuple[UnresolvedEvidenceV2, ...] = ()


class TargetRefusal(BaseModel):
    """One explicitly refused batch target and the backend's real code."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target: NonBlankStr
    code: NonBlankStr


class ToolTraceEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: Literal["TOOL_CALL"]
    tool_name: NonBlankStr
    arguments: dict[NonBlankStr, NonBlankStr]
    fingerprint: Digest
    evidence_ids: tuple[EvidenceId, ...]
    error_code: NonBlankStr | None = None
    elapsed_ms: Annotated[StrictInt, Field(ge=0)]
    #: v2 batch tools only: the per-target refusals of an atomic batch refusal,
    #: in request order. Empty for v1 calls and for successful calls; the
    #: call-level ``error_code`` (``TARGETS_REFUSED``) never witnesses a gap.
    target_refusals: tuple[TargetRefusal, ...] = ()

    @model_validator(mode="after")
    def reject_duplicates(self) -> ToolTraceEvent:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence_ids must not contain duplicates")
        targets = [item.target for item in self.target_refusals]
        if len(targets) != len(set(targets)):
            raise ValueError("target_refusals must not repeat a target")
        # An event carrying refusal entries must be an atomic batch refusal:
        # a successful call (or any other code) cannot be a refusal witness.
        if self.target_refusals:
            if self.error_code != TARGETS_REFUSED_CODE:
                raise ValueError(
                    "target_refusals require the TARGETS_REFUSED call-level code"
                )
            if self.evidence_ids:
                raise ValueError("a refused batch call returns no evidence")
        # The summary code belongs to the batch surface only, and an atomic
        # per-target refusal always names at least one refused target.
        if self.error_code == TARGETS_REFUSED_CODE:
            if self.tool_name not in EVIDENCE_BATCH_TOOLS:
                raise ValueError("TARGETS_REFUSED is only valid for batch tools")
            if not self.target_refusals:
                raise ValueError("TARGETS_REFUSED requires at least one refused target")
        return self


class ToolTraceEventV2(ToolTraceEvent):
    """A tool outcome with harness-attested execution provenance.

    Keep the frozen v1 model above unchanged: adding a defaulted provenance
    field there would alter historical run digests and serialized artifacts.
    """

    event_type: Literal["TOOL_CALL_V2"]
    outcome_origin: Literal[
        "CONTROLLER_PRECHECK",
        "EVIDENCE_BACKEND",
        "CONTROLLER_POSTCHECK",
        "PROTOCOL_GATE",
        "TOOL_RUNTIME",
    ]

    @model_validator(mode="after")
    def validate_origin_contract(self) -> ToolTraceEventV2:
        if self.error_code is None:
            if self.outcome_origin != "EVIDENCE_BACKEND":
                raise ValueError("successful tool events must originate at the evidence backend")
        elif self.evidence_ids:
            raise ValueError("failed tool events must not carry successful evidence IDs")
        if self.target_refusals and self.outcome_origin != "EVIDENCE_BACKEND":
            raise ValueError("target_refusals require an evidence-backend outcome")
        if self.error_code == TARGETS_REFUSED_CODE and self.outcome_origin != "EVIDENCE_BACKEND":
            raise ValueError("TARGETS_REFUSED requires an evidence-backend outcome")
        if self.target_refusals and self.tool_name not in EVIDENCE_BATCH_TOOLS:
            raise ValueError("target_refusals are only valid for batch tools")
        return self


#: Trace argument key carrying a batch call's requested targets. The request is
#: recorded comma-joined in request order (identifiers and dbt unique ids never
#: contain commas); an empty request records no key.
_BATCH_REQUEST_KEYS = {
    "get_relation_schema_expectation": "relation_names",
    "get_dbt_node_definition": "node_ids",
}


def _requested_targets(event: ToolTraceEvent) -> tuple[str, ...]:
    key = _BATCH_REQUEST_KEYS.get(event.tool_name)
    if key is None:
        return ()
    raw = event.arguments.get(key, "")
    return tuple(part for part in raw.split(",") if part)


class RefusalWitnessResult(BaseModel):
    """Structured, provenance-aware result shared by all refusal consumers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    witnessed: StrictBool
    outcome_origin: Literal[
        "CONTROLLER_PRECHECK",
        "EVIDENCE_BACKEND",
        "CONTROLLER_POSTCHECK",
        "PROTOCOL_GATE",
        "TOOL_RUNTIME",
        "LEGACY_UNATTRIBUTED",
    ] | None
    event_fingerprint: Digest | None
    trace_sequence: Annotated[StrictInt, Field(ge=1)] | None
    failure_reason: Literal[
        "NO_MATCH",
        "AMBIGUOUS",
        "CODE_MISMATCH",
        "SOURCE_NOT_QUALIFIED",
        "EVIDENCE_PRESENT",
        "TRACE_VERSION_MISMATCH",
    ] | None

    @model_validator(mode="after")
    def validate_result_shape(self) -> RefusalWitnessResult:
        if self.witnessed:
            if (
                self.outcome_origin is None
                or self.event_fingerprint is None
                or self.trace_sequence is None
                or self.failure_reason is not None
            ):
                raise ValueError("a witnessed refusal must identify its source event")
        elif self.failure_reason is None:
            raise ValueError("an unwitnessed result must name its failure reason")
        return self


def _failed_witness(
    reason: Literal[
        "NO_MATCH",
        "AMBIGUOUS",
        "CODE_MISMATCH",
        "SOURCE_NOT_QUALIFIED",
        "EVIDENCE_PRESENT",
        "TRACE_VERSION_MISMATCH",
    ],
    *,
    outcome_origin: Literal[
        "CONTROLLER_PRECHECK",
        "EVIDENCE_BACKEND",
        "CONTROLLER_POSTCHECK",
        "PROTOCOL_GATE",
        "TOOL_RUNTIME",
        "LEGACY_UNATTRIBUTED",
    ] | None = None,
    fingerprint: str | None = None,
    sequence: int | None = None,
) -> RefusalWitnessResult:
    return RefusalWitnessResult(
        witnessed=False,
        outcome_origin=outcome_origin,
        event_fingerprint=fingerprint,
        trace_sequence=sequence,
        failure_reason=reason,
    )


def refusal_witnessed(
    trace_events: Iterable[object],
    *,
    tool_name: str,
    target: str,
    code: str,
    diagnosis_run_schema_version: Literal["p1.diagnosis.v1", "p1.diagnosis_run.v2"],
) -> RefusalWitnessResult:
    """Return the unique refusal receipt under the trusted run schema.

    The v1 branch is frozen, including the T13 batch-tool rule. The v2 branch
    additionally requires harness-attested provenance; versions are selected
    by the aggregate run schema, never by event contents. The iterable must be
    the full trace or a prefix beginning at trace sequence 1; returned sequence
    numbers index every event, including non-tool events.
    """

    indexed = tuple(enumerate(trace_events, start=1))
    tool_events = tuple(
        (i, event)
        for i, event in indexed
        if isinstance(event, ToolTraceEvent) and event.tool_name == tool_name
    )

    if diagnosis_run_schema_version == "p1.diagnosis.v1":
        if any(type(event) is not ToolTraceEvent for _, event in tool_events):
            return _failed_witness("TRACE_VERSION_MISMATCH")
        if tool_name in EVIDENCE_BATCH_TOOLS:
            witnesses = tuple(
                (i, event)
                for i, event in tool_events
                if event.error_code == TARGETS_REFUSED_CODE
                and not event.evidence_ids
                and target in _requested_targets(event)
                and any(
                    item.target == target and item.code == code
                    for item in event.target_refusals
                )
            )
        else:
            witnesses = tuple(
                (i, event)
                for i, event in tool_events
                if event.error_code is not None and target in event.arguments.values()
            )
        if not witnesses:
            return _failed_witness("NO_MATCH")
        if len(witnesses) != 1:
            return _failed_witness("AMBIGUOUS")
        sequence, event = witnesses[0]
        expected_code = (
            TARGETS_REFUSED_CODE if tool_name in EVIDENCE_BATCH_TOOLS else code
        )
        if event.error_code != expected_code:
            return _failed_witness(
                "CODE_MISMATCH",
                outcome_origin="LEGACY_UNATTRIBUTED",
                fingerprint=event.fingerprint,
                sequence=sequence,
            )
        return RefusalWitnessResult(
            witnessed=True,
            outcome_origin="LEGACY_UNATTRIBUTED",
            event_fingerprint=event.fingerprint,
            trace_sequence=sequence,
            failure_reason=None,
        )

    if any(type(event) is not ToolTraceEventV2 for _, event in tool_events):
        return _failed_witness("TRACE_VERSION_MISMATCH")
    if tool_name in EVIDENCE_BATCH_TOOLS:
        refusal_attempts = tuple(
            (i, event)
            for i, event in tool_events
            if event.error_code is not None and target in _requested_targets(event)
        )
        if not refusal_attempts:
            return _failed_witness("NO_MATCH")
        if len(refusal_attempts) != 1:
            return _failed_witness("AMBIGUOUS")
        sequence, event = refusal_attempts[0]
        if event.outcome_origin != "EVIDENCE_BACKEND":
            return _failed_witness(
                "SOURCE_NOT_QUALIFIED",
                outcome_origin=event.outcome_origin,
                fingerprint=event.fingerprint,
                sequence=sequence,
            )
        if event.error_code != TARGETS_REFUSED_CODE:
            return _failed_witness(
                "CODE_MISMATCH",
                outcome_origin=event.outcome_origin,
                fingerprint=event.fingerprint,
                sequence=sequence,
            )
        if event.evidence_ids:
            return _failed_witness(
                "EVIDENCE_PRESENT",
                outcome_origin=event.outcome_origin,
                fingerprint=event.fingerprint,
                sequence=sequence,
            )
        if not any(item.target == target and item.code == code for item in event.target_refusals):
            return _failed_witness(
                "CODE_MISMATCH",
                outcome_origin=event.outcome_origin,
                fingerprint=event.fingerprint,
                sequence=sequence,
            )
        return RefusalWitnessResult(
            witnessed=True,
            outcome_origin=event.outcome_origin,
            event_fingerprint=event.fingerprint,
            trace_sequence=sequence,
            failure_reason=None,
        )
    else:
        witnesses = tuple(
            (i, event)
            for i, event in tool_events
            if event.error_code is not None and target in event.arguments.values()
        )
    if not witnesses:
        return _failed_witness("NO_MATCH")
    if len(witnesses) != 1:
        return _failed_witness("AMBIGUOUS")
    sequence, event = witnesses[0]
    if event.error_code != (TARGETS_REFUSED_CODE if tool_name in EVIDENCE_BATCH_TOOLS else code):
        return _failed_witness(
            "CODE_MISMATCH",
            outcome_origin=event.outcome_origin,
            fingerprint=event.fingerprint,
            sequence=sequence,
        )
    if event.evidence_ids:
        return _failed_witness(
            "EVIDENCE_PRESENT",
            outcome_origin=event.outcome_origin,
            fingerprint=event.fingerprint,
            sequence=sequence,
        )
    if code in {"RELATION_NOT_ALLOWED", "NODE_NOT_ALLOWED"}:
        if event.outcome_origin not in {"CONTROLLER_PRECHECK", "EVIDENCE_BACKEND"}:
            return _failed_witness(
                "SOURCE_NOT_QUALIFIED",
                outcome_origin=event.outcome_origin,
                fingerprint=event.fingerprint,
                sequence=sequence,
            )
    elif event.outcome_origin != "EVIDENCE_BACKEND":
        return _failed_witness(
            "SOURCE_NOT_QUALIFIED",
            outcome_origin=event.outcome_origin,
            fingerprint=event.fingerprint,
            sequence=sequence,
        )
    return RefusalWitnessResult(
        witnessed=True,
        outcome_origin=event.outcome_origin,
        event_fingerprint=event.fingerprint,
        trace_sequence=sequence,
        failure_reason=None,
    )


class RejectedAssessmentSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hypothesis_id: StrictStr
    verdict: Literal["SUPPORTED", "REFUTED"]


class RejectedClaimSummary(BaseModel):
    """One claim as it stood in the refused kernel decision, bounded and lossy.

    ``evidence_ids`` holds the citations that resolved to registered records;
    citations the projection could not resolve are counted once, per decision,
    in ``unknown_evidence_count``. The per-claim counting identity lives on
    the refusal-audit projection (``AuditClaimSummary``), not here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["ROOT_CAUSE", "AFFECTED_ASSET", "HEALTH_STATE"]
    known_value: StrictStr | None = None
    relation_name: StrictStr | None = None
    evidence_ids: tuple[StrictStr, ...] = ()


class AuditClaimSummary(BaseModel):
    """Per-claim audit record for one refusal (``p1.refusal_audit.v1``).

    Every citation the claim carried is accounted for by
    ``total_evidence_refs = len(evidence_ids) + unregistered_evidence_refs
    + truncated_evidence_refs``. ``recomputable`` is decided by deterministic
    projection code and carries a fixed reason code; the offline reader must
    re-derive it rather than trust the boolean.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["ROOT_CAUSE", "AFFECTED_ASSET", "HEALTH_STATE"]
    known_value: StrictStr | None = None
    relation_name: StrictStr | None = None
    evidence_ids: tuple[StrictStr, ...]
    total_evidence_refs: Annotated[StrictInt, Field(ge=0)]
    unregistered_evidence_refs: Annotated[StrictInt, Field(ge=0)]
    truncated_evidence_refs: Annotated[StrictInt, Field(ge=0)]
    recomputable: StrictBool
    recomputability_reason: NonBlankStr


class AuditUnresolvedSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_kind: NonBlankStr
    reason_code: NonBlankStr
    subject: StrictStr | None = None


def derive_claim_recomputability(
    kind: str,
    known_value: str | None,
    unregistered_evidence_refs: int,
    truncated_evidence_refs: int,
) -> tuple[bool, str]:
    """Whether one audited claim's gate verdict can be re-derived offline.

    The single source of this judgement: the audit writer fills the stored
    ``recomputable`` pair from here, and the offline reader re-derives through
    the same function instead of trusting the stored boolean. The decision
    comes from the projection's shape alone — never from the gate's message
    text (which is fixed and generic) and never from whether the refusal was
    convenient:

    - a claim with an unregistered citation is re-derivable from the counts
      alone (the refusal condition is "had an unregistered citation"), without
      rebuilding the claim;
    - a claim whose citation list was capped has an incomplete evidence set;
    - a HEALTH_STATE claim's support rule needs ``history_name``, ``bucket``
      and ``current_value``, which the projection never keeps;
    - a ROOT_CAUSE or AFFECTED_ASSET claim whose value the projection redacted
      cannot be re-judged, because the rule needs the value;
    - anything else keeps its known fields and re-judges normally.
    """

    if unregistered_evidence_refs > 0:
        return True, "RECOMPUTABLE_UNREGISTERED_REF"
    if truncated_evidence_refs > 0:
        return False, "NOT_RECOMPUTABLE_TRUNCATED_REFS"
    if kind == "HEALTH_STATE":
        return False, "NOT_RECOMPUTABLE_HEALTH_CLAIM_FIELDS"
    if known_value is None:
        return False, "NOT_RECOMPUTABLE_REDACTED_CLAIM_VALUE"
    return True, "RECOMPUTABLE_PROJECTED_CLAIM"


class RefusalAudit(BaseModel):
    """The refusal's own inputs, preserved so the verdict can be re-derived.

    Bounded like ``RejectedDecisionSummary``: public identifiers and resolved
    citations only, unknown content reduced to counts. Free text, raw values
    and unregistered identifiers never enter the archive.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.refusal_audit.v1"] = "p1.refusal_audit.v1"
    reason_code: NonBlankStr
    model_request_index: Annotated[StrictInt, Field(ge=0)]
    status: Literal["CONFIRMED", "INSUFFICIENT_EVIDENCE", "NO_INCIDENT"]
    applicable_claim_kinds: tuple[StrictStr, ...]
    claims: tuple[AuditClaimSummary, ...]
    unresolved_evidence: tuple[AuditUnresolvedSummary, ...]
    truncated_claim_count: Annotated[StrictInt, Field(ge=0)]
    truncated_unresolved_count: Annotated[StrictInt, Field(ge=0)]


class RejectedUnresolvedSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_kind: Literal[
        "RELATION_SCHEMA",
        "RELATION_DATA_PROFILE",
        "RELATION_HISTORY",
        "INGESTION_WATERMARK",
        "PAYMENT_EVENT_IDENTITY",
        "TRANSFORMATION_DEFINITION",
    ]
    reason_code: Literal["RELATION_NOT_ALLOWED", "NOT_OBSERVABLE"]
    subject: StrictStr | None = None


class RejectedDecisionSummary(BaseModel):
    """Bounded structural projection of a kernel decision rejected at finalize.

    Only known public identifiers and accepted evidence references are kept;
    raw values, free text and unknown content are reduced to counts. Every array
    and every claim's reference list is capped; items dropped by a cap are not
    inspected and are counted as truncated, never as unknown. Assessments drop
    unregistered hypotheses (`total = len(kept) + unknown + truncated`), while
    claims and unresolved_evidence redact unknown values in place
    (`total = len(kept) + truncated`); one claim's references satisfy
    `total = kept + unknown + truncated`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.rejected_decision.v1"] = "p1.rejected_decision.v1"
    model_request_index: Annotated[StrictInt, Field(ge=0)]
    status: Literal["CONFIRMED", "INSUFFICIENT_EVIDENCE", "NO_INCIDENT"]
    selected_hypothesis_id: StrictStr | None = None
    assessments: tuple[RejectedAssessmentSummary, ...] = ()
    claims: tuple[RejectedClaimSummary, ...] = ()
    unresolved_evidence: tuple[RejectedUnresolvedSummary, ...] = ()
    unknown_hypothesis_count: StrictInt = 0
    unknown_claim_count: StrictInt = 0
    unknown_evidence_count: StrictInt = 0
    unknown_subject_count: StrictInt = 0
    truncated_assessment_count: StrictInt = 0
    truncated_claim_count: StrictInt = 0
    truncated_unresolved_count: StrictInt = 0
    truncated_evidence_count: StrictInt = 0
    total_assessments: StrictInt = 0
    total_claims: StrictInt = 0
    total_unresolved: StrictInt = 0
    truncated: StrictBool = False


class EvidenceGateTraceEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: Literal["EVIDENCE_GATE"]
    reason_code: NonBlankStr
    accepted: StrictBool
    rejected_decision: RejectedDecisionSummary | None = None
    # Additive (P-1): the refusal's own bounded inputs. Absent on v1 archives
    # and whenever the gate refused a submission it could not project, which is
    # reported as indeterminable rather than as a zero count.
    refusal_audit: RefusalAudit | None = None


class ModelCallShape(BaseModel):
    """Safe structural shape of one call inside a model response.

    Only the tool name, whether its arguments parsed as a JSON object, and
    whether it targeted the structured-output tool are recorded. Argument
    values, raw text and provider payloads never reach the trace.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_name: NonBlankStr
    is_output_call: StrictBool
    arguments_parse: Literal["OBJECT", "INVALID_JSON", "EMPTY"]


class ModelProtocolTraceEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: Literal["MODEL_PROTOCOL"]
    stage: Literal[
        "TOOL_ARGUMENT_VALIDATION",
        "OUTPUT_SCHEMA_VALIDATION",
        "OUTPUT_VALIDATION",
        "PROVIDER_RESPONSE",
    ]
    tool_name: NonBlankStr | None
    category: Literal[
        "TOOL_ARGUMENT_REJECTED",
        "OUTPUT_SCHEMA_REJECTED",
        "DECISION_CONTRACT_REJECTED",
        "PREMATURE_FINALIZATION",
        "PROVIDER_PROTOCOL_FAILURE",
    ]
    # Safe schema-rejection diagnostics: field paths (names only) and Pydantic
    # error kinds from the model retry prompt. Raw input values, messages and
    # exception text are never recorded.
    error_loc: tuple[StrictStr, ...] = ()
    error_kind: tuple[StrictStr, ...] = ()
    # Fixed reason codes for model-level rules, which report neither a field
    # location nor a distinguishing kind. Codes only: the validator messages
    # they are derived from never reach the trace.
    error_reason: tuple[StrictStr, ...] = ()
    # Which model request produced the recorded response, and what its calls
    # looked like. This is observation only: it changes no retry, budget or
    # acceptance behaviour.
    model_request_index: Annotated[StrictInt, Field(ge=0)] | None = None
    output_retry_used: Annotated[StrictInt, Field(ge=0)] | None = None
    call_shapes: tuple[ModelCallShape, ...] = ()
    response_ended_with: Literal["OUTPUT_CALL", "BUSINESS_CALL", "TEXT_ONLY", "EMPTY"] | None = None
    error_type: Literal[
        "UNEXPECTED_MODEL_BEHAVIOR",
        "TOOL_RETRY_ERROR",
        "MODEL_API_ERROR",
        "INCOMPLETE_TOOL_CALL",
        "VALUE_ERROR",
        "TYPE_ERROR",
        "OTHER",
    ] | None = None
    error_origin: Literal[
        "OUTPUT_VALIDATION",
        "BUSINESS_TOOL_ARGUMENTS",
        "KERNEL_DECISION",
        "PROVIDER",
        "UNKNOWN",
    ] | None = None
    retry_prompt_targets: tuple[StrictStr, ...] = ()
    # Sanitized transport classification for provider-origin failures, using
    # the doctor probe's fixed category vocabulary but its own `transport=`
    # key: `transport={TIMEOUT|CONNECTION_ERROR|HTTP_<status>|ERROR}`. Only
    # fixed kinds, integer statuses and counters; never exception text,
    # headers, response bodies or credentials. The TIMEOUT arm is defensive —
    # the runner's own TimeoutError catch (MODEL_TIMEOUT) precedes the
    # protocol path. None for validation/protocol failures that are not
    # transport-classifiable and for events recorded before this field
    # existed.
    transport_diagnostic: StrictStr | None = None


class DiagnosisTerminalTraceEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: Literal["DIAGNOSIS_TERMINAL"]
    strategy: DiagnosticStrategy
    status: DiagnosisStatus
    evidence_inventory: tuple[EvidenceId, ...]

    @model_validator(mode="after")
    def reject_duplicates(self) -> DiagnosisTerminalTraceEvent:
        if len(self.evidence_inventory) != len(set(self.evidence_inventory)):
            raise ValueError("evidence_inventory must not contain duplicates")
        return self


class KernelStateTraceEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: Literal["KERNEL_STATE"]
    state: Any


class PlanTraceEvent(BaseModel):
    """One planner event: a declared step, a close, or the obligation state.

    This is the planner's own ledger and is deliberately **not** a
    ``ToolTraceEvent``: a plan verdict is a validation outcome, not an executed
    tool call, and counting one as the other would inflate the tool record.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: Literal["PLAN"] = "PLAN"
    kind: Literal["STEP", "CLOSE", "STATE"]
    accepted: StrictBool = True
    verdict_code: StrictStr | None = None
    obligation_id: StrictStr | None = None
    tool_name: StrictStr | None = None
    plan_refusals_used: Annotated[StrictInt, Field(ge=0)] = 0
    plan_refusal_limit: Annotated[StrictInt, Field(ge=0)] = 0
    tool_calls_used: Annotated[StrictInt, Field(ge=0)] = 0
    #: CLOSE only: what was requested and what it was judged against, so an
    #: archived run distinguishes "satisfied" from "revoked" without the
    #: controller that produced it.
    requested_outcome: StrictStr | None = None
    evidence_ids: tuple[StrictStr, ...] = ()
    reason: StrictStr | None = None
    #: STATE only: every obligation with its final status and citations.
    obligations: tuple[PlanObligationRecord, ...] = ()
    open_obligations: tuple[StrictStr, ...] = ()


class PlanObligationRecord(BaseModel):
    """One obligation's archived state: status plus, when satisfied, its citations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    obligation_id: StrictStr
    status: Literal["OPEN", "SATISFIED", "REVOKED"]
    evidence_kind: StrictStr
    subject: StrictStr
    satisfied_with: tuple[StrictStr, ...] = ()
    close_reason: StrictStr | None = None


TraceEventV1 = Annotated[
    ToolTraceEvent
    | EvidenceGateTraceEvent
    | ModelProtocolTraceEvent
    | KernelStateTraceEvent
    | DiagnosisTerminalTraceEvent
    | PlanTraceEvent,
    Field(discriminator="event_type"),
]

TraceEventV2 = Annotated[
    ToolTraceEventV2
    | EvidenceGateTraceEvent
    | ModelProtocolTraceEvent
    | KernelStateTraceEvent
    | DiagnosisTerminalTraceEvent
    | PlanTraceEvent,
    Field(discriminator="event_type"),
]

# The public pre-v2 name remains the v1 union so existing model fields and
# callers keep their frozen parsing behavior. New generic runtime containers
# should use the explicitly versioned union they own.
TraceEvent = TraceEventV1


class PolicyIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: DiagnosticStrategy
    base_prompt_version: Literal["p1.base.v1"]
    base_prompt_sha256: Digest
    strategy_prompt_version: NonBlankStr
    strategy_prompt_sha256: Digest
    controller_protocol_version: NonBlankStr
    controller_protocol_sha256: Digest
    tool_schema_sha256: Digest


class DiagnosisMetrics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: NonBlankStr
    model: NonBlankStr
    model_requests: Annotated[StrictInt, Field(ge=0)]
    input_tokens: Annotated[StrictInt, Field(ge=0)]
    output_tokens: Annotated[StrictInt, Field(ge=0)]
    tool_call_attempts: Annotated[StrictInt, Field(ge=0)]
    successful_tool_calls: Annotated[StrictInt, Field(ge=0)]
    elapsed_ms: Annotated[StrictInt, Field(ge=0)]


class DiagnosisRunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.diagnosis.v1"] = "p1.diagnosis.v1"
    strategy: DiagnosticStrategy
    policy_identity: PolicyIdentity
    diagnosis: Diagnosis
    evidence_records: tuple[EvidenceRecord, ...]
    trace: tuple[TraceEventV1, ...]
    metrics: DiagnosisMetrics
    kernel_state: Any | None = None

    @property
    def investigation_state(self) -> Any | None:
        return self.kernel_state

    @model_validator(mode="after")
    def validate_contract(self) -> DiagnosisRunResult:
        if self.policy_identity.strategy is not self.strategy:
            raise ValueError("policy identity strategy must match diagnosis strategy")
        if self.schema_version == "p1.diagnosis.v1" and (
            self.policy_identity.controller_protocol_version == "p1.controller.v21"
        ):
            raise ValueError("v2 controller identity requires the v2 diagnosis-run schema")
        if self.schema_version == "p1.diagnosis_run.v2" and (
            self.policy_identity.controller_protocol_version != "p1.controller.v21"
        ):
            raise ValueError("v2 diagnosis-run schema requires the v2 controller identity")
        if self.schema_version == "p1.diagnosis_run.v2" and self.strategy not in {
            DiagnosticStrategy.STATIC_SKILL,
            DiagnosticStrategy.DIAGNOSTIC_KERNEL,
            DiagnosticStrategy.NO_TOOL,
            DiagnosticStrategy.KERNEL_NO_LINEAGE,
            DiagnosticStrategy.KERNEL_NO_SCHEMA,
        }:
            raise ValueError("v2 diagnosis-run schema is reserved for built-in model strategies")
        if self.diagnosis.run_id != self._run_id_from_records():
            raise ValueError("diagnosis run_id must match evidence records")
        evidence_ids = tuple(record.evidence_id for record in self.evidence_records)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_records must not contain duplicate evidence_id values")
        if any(record.run_id != self.diagnosis.run_id for record in self.evidence_records):
            raise ValueError("all evidence records must be run-bound")
        terminal_events = tuple(
            event for event in self.trace if isinstance(event, DiagnosisTerminalTraceEvent)
        )
        if len(terminal_events) != 1 or not self.trace or self.trace[-1] != terminal_events[0]:
            raise ValueError("trace requires one final DIAGNOSIS_TERMINAL event")
        terminal = terminal_events[0]
        if (
            terminal.strategy is not self.strategy
            or terminal.status is not self.diagnosis.status
            or terminal.evidence_inventory != evidence_ids
        ):
            raise ValueError("terminal event does not match diagnosis result")
        kernel_events = tuple(
            event for event in self.trace if isinstance(event, KernelStateTraceEvent)
        )
        if self.strategy in KERNEL_STRATEGIES:
            if self.kernel_state is None or len(kernel_events) != 1:
                raise ValueError("Kernel result requires one Kernel state event")
            if self.trace[-2] != kernel_events[0]:
                raise ValueError("Kernel state must be immediately before terminal")
            state = kernel_events[0].state
            if hasattr(state, "run_id") and state.run_id != self.diagnosis.run_id:
                raise ValueError("Kernel state run_id must match diagnosis")
            if state != self.kernel_state:
                raise ValueError("Kernel state event must equal kernel_state")
        elif kernel_events or self.kernel_state is not None:
            raise ValueError("Static result cannot contain Kernel state")
        tool_events = tuple(event for event in self.trace if isinstance(event, ToolTraceEvent))
        if self.metrics.tool_call_attempts != len(tool_events):
            raise ValueError("metrics tool_call_attempts must match trace")
        successful = sum(event.error_code is None for event in tool_events)
        if self.metrics.successful_tool_calls != successful:
            raise ValueError("metrics successful_tool_calls must match trace")
        return self

    def _run_id_from_records(self) -> str:
        if self.evidence_records:
            return self.evidence_records[0].run_id
        return self.diagnosis.run_id

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class DiagnosisRunResultV2(DiagnosisRunResult):
    """Aggregate run result for the provenance-aware trace contract."""

    schema_version: Literal["p1.diagnosis_run.v2"] = "p1.diagnosis_run.v2"
    trace: tuple[TraceEventV2, ...]


DiagnosisRunResultAny = Annotated[
    DiagnosisRunResult | DiagnosisRunResultV2,
    Field(discriminator="schema_version"),
]
