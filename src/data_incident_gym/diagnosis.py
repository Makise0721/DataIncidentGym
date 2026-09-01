from __future__ import annotations

import hashlib
import json
import math
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

from data_incident_gym.evidence import EvidenceRecord

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


_MODEL_ERROR_CODES = {
    "MODEL_DECLINED",
    "MODEL_REQUEST_LIMIT",
    "MODEL_TOOL_CALL_LIMIT",
    "MODEL_TIMEOUT",
    "MODEL_PROTOCOL_ERROR",
    "MODEL_RUNTIME_ERROR",
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


class ToolTraceEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: Literal["TOOL_CALL"]
    tool_name: NonBlankStr
    arguments: dict[NonBlankStr, NonBlankStr]
    fingerprint: Digest
    evidence_ids: tuple[EvidenceId, ...]
    error_code: NonBlankStr | None = None
    elapsed_ms: Annotated[StrictInt, Field(ge=0)]

    @model_validator(mode="after")
    def reject_duplicates(self) -> ToolTraceEvent:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence_ids must not contain duplicates")
        return self


class EvidenceGateTraceEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: Literal["EVIDENCE_GATE"]
    reason_code: NonBlankStr
    accepted: StrictBool


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


TraceEvent = Annotated[
    ToolTraceEvent
    | EvidenceGateTraceEvent
    | ModelProtocolTraceEvent
    | KernelStateTraceEvent
    | DiagnosisTerminalTraceEvent,
    Field(discriminator="event_type"),
]


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
    trace: tuple[TraceEvent, ...]
    metrics: DiagnosisMetrics
    kernel_state: Any | None = None

    @property
    def investigation_state(self) -> Any | None:
        return self.kernel_state

    @model_validator(mode="after")
    def validate_contract(self) -> DiagnosisRunResult:
        if self.policy_identity.strategy is not self.strategy:
            raise ValueError("policy identity strategy must match diagnosis strategy")
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
        if self.strategy is DiagnosticStrategy.DIAGNOSTIC_KERNEL:
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
