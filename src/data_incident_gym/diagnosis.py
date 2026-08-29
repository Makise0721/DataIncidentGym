from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
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

from data_incident_gym.diagnostic_kernel import InvestigationState, KernelStateTraceEvent
from data_incident_gym.evidence import EvidenceRecord

RUN_ID_PATTERN = r"^[0-9a-f]{32}$"
EVIDENCE_ID_PATTERN = r"^ev_[0-9a-f]{64}$"
CASE_ID_PATTERN = r"^[a-z][a-z0-9_]{2,63}$"
ROOT_CAUSE_CODE_PATTERN = r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$"


def _non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


def _strict_finite_float(value: object) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise ValueError("confidence must be a finite float")
    return value


NonBlankStr = Annotated[StrictStr, AfterValidator(_non_blank)]
CaseId = Annotated[NonBlankStr, Field(pattern=CASE_ID_PATTERN)]
RunId = Annotated[NonBlankStr, Field(pattern=RUN_ID_PATTERN)]
EvidenceId = Annotated[NonBlankStr, Field(pattern=EVIDENCE_ID_PATTERN)]
RootCauseCode = Annotated[NonBlankStr, Field(pattern=ROOT_CAUSE_CODE_PATTERN)]
Confidence = Annotated[StrictFloat, BeforeValidator(_strict_finite_float), Field(ge=0.0, le=1.0)]


class DiagnosisStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    MODEL_ERROR = "MODEL_ERROR"


class Diagnosis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DiagnosisStatus
    incident_case_id: CaseId
    run_id: RunId
    root_cause_code: RootCauseCode | None
    summary: NonBlankStr
    affected_assets: tuple[NonBlankStr, ...]
    evidence_ids: tuple[EvidenceId, ...]
    recommended_actions: tuple[NonBlankStr, ...]
    confidence: Confidence

    @model_validator(mode="after")
    def validate_contract(self) -> Diagnosis:
        for field_name in ("affected_assets", "evidence_ids", "recommended_actions"):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must not contain duplicates")

        if self.status == DiagnosisStatus.CONFIRMED:
            if self.root_cause_code is None:
                raise ValueError("CONFIRMED requires root_cause_code")
            if not self.affected_assets:
                raise ValueError("CONFIRMED requires affected_assets")
            if not self.evidence_ids:
                raise ValueError("CONFIRMED requires evidence_ids")
            if not self.recommended_actions:
                raise ValueError("CONFIRMED requires recommended_actions")
        elif self.root_cause_code is not None or self.affected_assets:
            raise ValueError("non-confirmed diagnosis cannot contain unproven claims")

        if self.status == DiagnosisStatus.MODEL_ERROR and self.summary not in {
            "MODEL_DECLINED",
            "MODEL_REQUEST_LIMIT",
            "MODEL_TIMEOUT",
            "MODEL_PROTOCOL_ERROR",
            "MODEL_RUNTIME_ERROR",
        }:
            raise ValueError("MODEL_ERROR summary must be a fixed safe reason code")
        return self


class ToolTraceEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: Literal["TOOL_CALL"]
    tool_name: NonBlankStr
    arguments: dict[NonBlankStr, NonBlankStr]
    fingerprint: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    evidence_ids: tuple[EvidenceId, ...]
    error_code: NonBlankStr | None = None
    elapsed_ms: Annotated[StrictInt, Field(ge=0)]

    @model_validator(mode="after")
    def reject_duplicate_evidence_ids(self) -> ToolTraceEvent:
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
        "PREMATURE_FINALIZATION",
        "PROVIDER_PROTOCOL_FAILURE",
    ]


TraceEvent = Annotated[
    ToolTraceEvent
    | EvidenceGateTraceEvent
    | ModelProtocolTraceEvent
    | KernelStateTraceEvent,
    Field(discriminator="event_type"),
]


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

    diagnosis: Diagnosis
    evidence_records: tuple[EvidenceRecord, ...]
    trace: tuple[TraceEvent, ...]
    investigation_state: InvestigationState
    metrics: DiagnosisMetrics

    @model_validator(mode="after")
    def reject_duplicate_evidence_records(self) -> DiagnosisRunResult:
        evidence_ids = tuple(record.evidence_id for record in self.evidence_records)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_records must not contain duplicate evidence_id values")
        terminal_events = tuple(
            event for event in self.trace if isinstance(event, KernelStateTraceEvent)
        )
        if len(terminal_events) != 1 or not self.trace or self.trace[-1] != terminal_events[0]:
            raise ValueError("trace requires one terminal Kernel state")
        if terminal_events[0].state != self.investigation_state:
            raise ValueError("terminal Kernel state must equal investigation_state")
        if self.investigation_state.incident_case_id != self.diagnosis.incident_case_id:
            raise ValueError("Kernel case must match diagnosis")
        if self.investigation_state.run_id != self.diagnosis.run_id:
            raise ValueError("Kernel run must match diagnosis")
        if self.investigation_state.evidence_inventory != evidence_ids:
            raise ValueError("Kernel evidence inventory must match records exactly")
        if self.investigation_state.model_requests_used != self.metrics.model_requests:
            raise ValueError("Kernel model budget must match metrics")
        if self.investigation_state.tool_calls_used != self.metrics.tool_call_attempts:
            raise ValueError("Kernel tool budget must match metrics")
        if self.investigation_state.final_status is None:
            raise ValueError("run result requires terminal Kernel status")
        if self.investigation_state.final_status.value != self.diagnosis.status.value:
            raise ValueError("Kernel final status must match diagnosis")
        return self
