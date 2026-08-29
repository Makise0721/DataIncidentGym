from __future__ import annotations

import hashlib
import json
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

from data_incident_gym.evidence import (
    DbtLineageFact,
    DbtNodeErrorFact,
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceType,
    RelationSchemaFact,
)

_RUN_ID_PATTERN = r"^[0-9a-f]{32}$"
_HYPOTHESIS_ID_PATTERN = r"^h_[a-z0-9_]{1,32}$"
_GAP_ID_PATTERN = r"^g_[a-z0-9_]{1,32}$"
_EVIDENCE_ID_PATTERN = r"^ev_[0-9a-f]{64}$"
_FINGERPRINT_PATTERN = r"^[0-9a-f]{64}$"


class EvidenceGapKind(StrEnum):
    LOCATE_FAILURE = "LOCATE_FAILURE"
    EXPLAIN_FAILURE = "EXPLAIN_FAILURE"
    DISCOVER_SOURCE_RELATION = "DISCOVER_SOURCE_RELATION"
    DISCRIMINATE_SCHEMA = "DISCRIMINATE_SCHEMA"
    MAP_IMPACT = "MAP_IMPACT"


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


class KernelFinalStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    MODEL_ERROR = "MODEL_ERROR"


def _reject_duplicates(values: tuple[object, ...], field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")


class Hypothesis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hypothesis_id: StrictStr = Field(pattern=_HYPOTHESIS_ID_PATTERN)
    root_cause_code: StrictStr = Field(
        pattern=r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$"
    )


class HypothesisAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hypothesis_id: StrictStr = Field(pattern=_HYPOTHESIS_ID_PATTERN)
    verdict: HypothesisVerdict
    evidence_ids: tuple[
        Annotated[StrictStr, Field(pattern=_EVIDENCE_ID_PATTERN)],
        ...,
    ]

    @model_validator(mode="after")
    def reject_duplicate_evidence_ids(self) -> Self:
        _reject_duplicates(self.evidence_ids, "assessment evidence_ids")
        return self


class InvestigationIntent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gap_id: StrictStr = Field(pattern=_GAP_ID_PATTERN)
    gap_kind: EvidenceGapKind
    hypothesis_ids: tuple[
        Annotated[StrictStr, Field(pattern=_HYPOTHESIS_ID_PATTERN)],
        ...,
    ] = ()
    new_hypotheses: tuple[Hypothesis, ...] = ()

    @model_validator(mode="after")
    def reject_duplicate_ids(self) -> Self:
        _reject_duplicates(self.hypothesis_ids, "intent hypothesis_ids")
        _reject_duplicates(
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
    status: EvidenceGapStatus
    evidence_ids: tuple[
        Annotated[StrictStr, Field(pattern=_EVIDENCE_ID_PATTERN)],
        ...,
    ] = ()
    error_code: StrictStr | None = None

    @model_validator(mode="after")
    def reject_duplicate_ids(self) -> Self:
        _reject_duplicates(self.hypothesis_ids, "gap hypothesis_ids")
        _reject_duplicates(self.evidence_ids, "gap evidence_ids")
        return self


class ClaimEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ClaimKind
    value: StrictStr
    evidence_ids: tuple[
        Annotated[StrictStr, Field(pattern=_EVIDENCE_ID_PATTERN)],
        ...,
    ]

    @model_validator(mode="after")
    def reject_duplicate_evidence_ids(self) -> Self:
        _reject_duplicates(self.evidence_ids, "claim evidence_ids")
        return self


class KernelDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["CONFIRMED", "INSUFFICIENT_EVIDENCE"]
    incident_case_id: StrictStr = Field(
        pattern=r"^[a-z][a-z0-9_]{2,63}$"
    )
    run_id: StrictStr = Field(pattern=_RUN_ID_PATTERN)
    selected_hypothesis_id: StrictStr | None
    assessments: tuple[HypothesisAssessment, ...]
    claims: tuple[ClaimEvidence, ...]
    summary: StrictStr
    recommended_actions: tuple[StrictStr, ...]
    confidence: Annotated[StrictFloat, Field(ge=0.0, le=1.0)]

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        _reject_duplicates(
            tuple(item.hypothesis_id for item in self.assessments),
            "assessment hypothesis IDs",
        )
        _reject_duplicates(
            tuple((item.kind, item.value) for item in self.claims),
            "claim kind/value pairs",
        )
        _reject_duplicates(self.recommended_actions, "recommended_actions")
        if not self.summary.strip():
            raise ValueError("summary must not be blank")
        if self.status == "CONFIRMED":
            if self.selected_hypothesis_id is None:
                raise ValueError("CONFIRMED requires selected_hypothesis_id")
            if not self.assessments:
                raise ValueError("CONFIRMED requires assessments")
            if not self.claims:
                raise ValueError("CONFIRMED requires claims")
            if not self.recommended_actions:
                raise ValueError("CONFIRMED requires recommended_actions")
        elif self.selected_hypothesis_id is not None or self.assessments or self.claims:
            raise ValueError(
                "INSUFFICIENT_EVIDENCE requires empty selection, assessments, and claims"
            )
        return self


class PreparedToolCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gap_id: StrictStr
    tool_name: StrictStr
    arguments: dict[StrictStr, StrictStr]
    fingerprint: Annotated[StrictStr, Field(pattern=_FINGERPRINT_PATTERN)]


class InvestigationState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["m6.investigation.v1"]
    incident_case_id: StrictStr
    run_id: StrictStr = Field(pattern=_RUN_ID_PATTERN)
    revision: Annotated[StrictInt, Field(ge=0)]
    allowed_root_cause_codes: tuple[StrictStr, ...]
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

    @model_validator(mode="after")
    def validate_invariants(self) -> Self:
        _reject_duplicates(self.allowed_root_cause_codes, "ontology members")
        _reject_duplicates(
            tuple(item.hypothesis_id for item in self.hypotheses),
            "hypothesis IDs",
        )
        _reject_duplicates(tuple(item.gap_id for item in self.gaps), "gap IDs")
        _reject_duplicates(
            tuple(item.hypothesis_id for item in self.assessments),
            "assessment hypothesis IDs",
        )
        _reject_duplicates(
            tuple((item.kind, item.value) for item in self.claims),
            "claim kind/value pairs",
        )
        _reject_duplicates(self.evidence_inventory, "evidence inventory IDs")
        if self.model_requests_used > self.model_request_limit:
            raise ValueError("model request usage exceeds limit")
        if self.model_requests_remaining != (
            self.model_request_limit - self.model_requests_used
        ):
            raise ValueError("model request remaining count is inconsistent")
        if self.tool_calls_used > self.tool_call_limit:
            raise ValueError("tool call usage exceeds limit")
        if self.tool_calls_remaining != self.tool_call_limit - self.tool_calls_used:
            raise ValueError("tool call remaining count is inconsistent")
        return self


class KernelOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: KernelFinalStatus
    root_cause_code: StrictStr | None
    affected_assets: tuple[StrictStr, ...]
    evidence_ids: tuple[StrictStr, ...]
    summary: StrictStr
    recommended_actions: tuple[StrictStr, ...]
    confidence: Annotated[StrictFloat, Field(ge=0.0, le=1.0)]


class KernelStateTraceEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: Literal["KERNEL_STATE"]
    state: InvestigationState


class KernelError(RuntimeError):
    def __init__(self, code: str, *, fingerprint: str | None = None) -> None:
        self.code = code
        self.fingerprint = fingerprint
        super().__init__(code)
        self.__cause__ = None
        self.__context__ = None


_GAP_TOOL: dict[EvidenceGapKind, tuple[str, str | None]] = {
    EvidenceGapKind.LOCATE_FAILURE: ("get_dbt_run_results", None),
    EvidenceGapKind.EXPLAIN_FAILURE: ("get_dbt_node_error", None),
    EvidenceGapKind.DISCOVER_SOURCE_RELATION: ("get_dbt_lineage", "upstream"),
    EvidenceGapKind.DISCRIMINATE_SCHEMA: ("get_relation_schema", None),
    EvidenceGapKind.MAP_IMPACT: ("get_dbt_lineage", "downstream"),
}

_SAFE_TOOL_ERRORS = {
    "EVIDENCE_TOOL_ERROR",
    "INVALID_ARTIFACT",
    "NODE_ERROR_NOT_FOUND",
    "NODE_NOT_FOUND",
    "READ_ONLY_DATABASE_ERROR",
    "RELATION_NOT_ALLOWED",
    "RELATION_NOT_FOUND",
    "RUN_CONTEXT_MISMATCH",
    "RUN_NOT_FOUND",
    "RUN_STATE_DRIFT",
}

_SAFE_MODEL_ERRORS = {
    "MODEL_DECLINED",
    "MODEL_REQUEST_LIMIT",
    "MODEL_TIMEOUT",
    "MODEL_PROTOCOL_ERROR",
    "MODEL_RUNTIME_ERROR",
}


def _fingerprint(run_id: str, tool_name: str, arguments: dict[str, str]) -> str:
    payload = {
        "arguments": arguments,
        "run_id": run_id,
        "tool_name": tool_name,
    }
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        fallback = {
            "arguments": {
                str(key): type(value).__name__ for key, value in arguments.items()
            }
            if isinstance(arguments, dict)
            else type(arguments).__name__,
            "run_id": type(run_id).__name__,
            "tool_name": type(tool_name).__name__,
        }
        canonical = json.dumps(fallback, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DiagnosticKernel:
    def __init__(
        self,
        *,
        incident_case_id: str,
        run_id: str,
        allowed_root_cause_codes: tuple[str, ...],
        model_request_limit: int,
        tool_call_limit: int,
    ) -> None:
        self._incident_case_id = incident_case_id
        self._run_id = run_id
        self._allowed_root_cause_codes = allowed_root_cause_codes
        self._model_request_limit = model_request_limit
        self._tool_call_limit = tool_call_limit
        self._revision = 0
        self._hypotheses: list[Hypothesis] = []
        self._gaps: list[EvidenceGap] = []
        self._assessments: tuple[HypothesisAssessment, ...] = ()
        self._claims: tuple[ClaimEvidence, ...] = ()
        self._records: list[EvidenceRecord] = []
        self._fingerprints: list[str] = []
        self._prepared_fingerprints: set[str] = set()
        self._prepared_calls: dict[str, PreparedToolCall] = {}
        self._final_status: KernelFinalStatus | None = None
        self._gate_reason: str | None = None

    @classmethod
    def start(
        cls,
        *,
        incident_case_id: str,
        run_id: str,
        allowed_root_cause_codes: tuple[str, ...],
        model_request_limit: int,
        tool_call_limit: int,
    ) -> DiagnosticKernel:
        if len(allowed_root_cause_codes) < 2:
            raise ValueError("Diagnostic Kernel requires at least two ontology members")
        if len(allowed_root_cause_codes) != len(set(allowed_root_cause_codes)):
            raise ValueError("ontology members must be unique")
        if type(model_request_limit) is not int or model_request_limit <= 0:
            raise ValueError("model request limit must be positive")
        if type(tool_call_limit) is not int or tool_call_limit <= 0:
            raise ValueError("tool call limit must be positive")
        return cls(
            incident_case_id=incident_case_id,
            run_id=run_id,
            allowed_root_cause_codes=allowed_root_cause_codes,
            model_request_limit=model_request_limit,
            tool_call_limit=tool_call_limit,
        )

    @property
    def evidence_records(self) -> tuple[EvidenceRecord, ...]:
        return tuple(self._records)

    def snapshot(self, *, model_requests_used: int) -> InvestigationState:
        if type(model_requests_used) is not int or not (
            0 <= model_requests_used <= self._model_request_limit
        ):
            raise ValueError("model request usage exceeds Kernel budget")
        return InvestigationState(
            schema_version="m6.investigation.v1",
            incident_case_id=self._incident_case_id,
            run_id=self._run_id,
            revision=self._revision,
            allowed_root_cause_codes=self._allowed_root_cause_codes,
            hypotheses=tuple(self._hypotheses),
            gaps=tuple(self._gaps),
            assessments=self._assessments,
            claims=self._claims,
            evidence_inventory=tuple(record.evidence_id for record in self._records),
            tool_fingerprints=tuple(self._fingerprints),
            model_request_limit=self._model_request_limit,
            model_requests_used=model_requests_used,
            model_requests_remaining=self._model_request_limit - model_requests_used,
            tool_call_limit=self._tool_call_limit,
            tool_calls_used=len(self._fingerprints),
            tool_calls_remaining=self._tool_call_limit - len(self._fingerprints),
            final_status=self._final_status,
            gate_reason=self._gate_reason,
        )

    def _error(self, code: str, fingerprint: str | None = None) -> None:
        raise KernelError(code, fingerprint=fingerprint) from None

    def _validate_arguments(
        self,
        arguments: object,
        fingerprint: str,
    ) -> dict[str, str]:
        if type(arguments) is not dict or any(
            type(key) is not str or type(value) is not str
            for key, value in arguments.items()
        ):
            self._error("ARGUMENTS_INVALID", fingerprint)
        return dict(arguments)

    def _validate_tool_mapping(
        self,
        intent: InvestigationIntent,
        tool_name: str,
        arguments: dict[str, str],
        fingerprint: str,
    ) -> None:
        expected_tool, expected_direction = _GAP_TOOL[intent.gap_kind]
        if tool_name != expected_tool:
            self._error("GAP_TOOL_MISMATCH", fingerprint)
        if expected_direction is not None and arguments.get("direction") != expected_direction:
            self._error("GAP_TOOL_MISMATCH", fingerprint)

        expected_keys: dict[str, set[str]] = {
            "get_dbt_run_results": {"run_id"},
            "get_dbt_node_error": {"run_id", "node_id"},
            "get_dbt_lineage": {"node_id", "direction"},
            "get_relation_schema": {"relation_name"},
        }
        keys = set(arguments)
        if keys != expected_keys.get(tool_name, set()):
            self._error("ARGUMENTS_INVALID", fingerprint)
        if "run_id" in arguments and arguments["run_id"] != self._run_id:
            self._error("RUN_CONTEXT_MISMATCH", fingerprint)
        if tool_name == "get_dbt_lineage" and arguments.get("direction") not in {
            "upstream",
            "downstream",
        }:
            self._error("ARGUMENTS_INVALID", fingerprint)

    def _known_failed_nodes(self) -> set[str]:
        nodes: set[str] = set()
        for record in self._records:
            if isinstance(record.content, DbtRunResultsFact):
                nodes.update(record.content.failed_nodes)
        return nodes

    def _known_lineage_nodes(self) -> set[str]:
        nodes: set[str] = set()
        for record in self._records:
            if isinstance(record.content, DbtLineageFact):
                nodes.add(record.content.node_id)
                nodes.update(item.node_id for item in record.content.related_nodes)
        return nodes

    def _known_upstream_relations(self) -> set[str]:
        relations: set[str] = set()
        for record in self._records:
            content = record.content
            if not isinstance(content, DbtLineageFact) or content.direction != "upstream":
                continue
            relations.update(
                item.name
                for item in content.related_nodes
                if item.resource_type in {"seed", "source"}
            )
        return relations

    def _validate_argument_provenance(
        self,
        tool_name: str,
        arguments: dict[str, str],
        fingerprint: str,
    ) -> None:
        if tool_name == "get_dbt_node_error" and arguments["node_id"] not in (
            self._known_failed_nodes()
        ):
            self._error("NODE_ARGUMENT_NOT_PROVEN", fingerprint)
        if tool_name == "get_dbt_lineage" and arguments["node_id"] not in (
            self._known_failed_nodes() | self._known_lineage_nodes()
        ):
            self._error("NODE_ARGUMENT_NOT_PROVEN", fingerprint)
        if tool_name == "get_relation_schema" and arguments["relation_name"] not in (
            self._known_upstream_relations()
        ):
            self._error("RELATION_ARGUMENT_NOT_PROVEN", fingerprint)

    def _validate_new_hypotheses(
        self,
        intent: InvestigationIntent,
        fingerprint: str,
    ) -> tuple[Hypothesis, ...]:
        new_hypotheses = tuple(intent.new_hypotheses)
        if not new_hypotheses:
            return ()
        if not any(isinstance(record.content, DbtNodeErrorFact) for record in self._records):
            self._error("HYPOTHESIS_REQUIRES_NODE_ERROR", fingerprint)
        existing_ids = {item.hypothesis_id for item in self._hypotheses}
        for hypothesis in new_hypotheses:
            if hypothesis.hypothesis_id in existing_ids:
                self._error("DUPLICATE_HYPOTHESIS", fingerprint)
            if hypothesis.root_cause_code not in self._allowed_root_cause_codes:
                self._error("ONTOLOGY_CODE_UNKNOWN", fingerprint)
            existing_ids.add(hypothesis.hypothesis_id)
        return new_hypotheses

    def _validate_intent_hypotheses(
        self,
        intent: InvestigationIntent,
        staged_hypotheses: tuple[Hypothesis, ...],
        fingerprint: str,
    ) -> None:
        known_ids = {
            item.hypothesis_id for item in self._hypotheses
        } | {item.hypothesis_id for item in staged_hypotheses}
        if any(item not in known_ids for item in intent.hypothesis_ids):
            self._error("HYPOTHESIS_REFERENCE_UNKNOWN", fingerprint)

    def prepare_tool(
        self,
        *,
        intent: InvestigationIntent,
        tool_name: str,
        arguments: dict[str, str],
    ) -> PreparedToolCall:
        if self._final_status is not None:
            self._error("KERNEL_FINALIZED")
        fingerprint = _fingerprint(self._run_id, tool_name, arguments)
        if len(self._fingerprints) >= self._tool_call_limit:
            self._error("TOOL_CALL_LIMIT", fingerprint)
        self._fingerprints.append(fingerprint)
        self._revision += 1
        if fingerprint in self._prepared_fingerprints:
            self._error("DUPLICATE_TOOL_CALL", fingerprint)

        validated_arguments = self._validate_arguments(arguments, fingerprint)
        self._validate_tool_mapping(intent, tool_name, validated_arguments, fingerprint)
        staged_hypotheses = self._validate_new_hypotheses(intent, fingerprint)
        self._validate_intent_hypotheses(intent, staged_hypotheses, fingerprint)
        self._validate_argument_provenance(tool_name, validated_arguments, fingerprint)

        prepared = PreparedToolCall(
            gap_id=intent.gap_id,
            tool_name=tool_name,
            arguments=validated_arguments,
            fingerprint=fingerprint,
        )
        self._prepared_fingerprints.add(fingerprint)
        self._hypotheses.extend(staged_hypotheses)
        self._gaps.append(
            EvidenceGap(
                gap_id=intent.gap_id,
                gap_kind=intent.gap_kind,
                hypothesis_ids=intent.hypothesis_ids,
                tool_name=tool_name,
                status=EvidenceGapStatus.OPEN,
            )
        )
        self._prepared_calls[fingerprint] = prepared
        return prepared

    def _prepared_gap_index(self, prepared: PreparedToolCall) -> int:
        known = self._prepared_calls.get(prepared.fingerprint)
        if known != prepared:
            self._error("PREPARED_CALL_INVALID", prepared.fingerprint)
        for index in range(len(self._gaps) - 1, -1, -1):
            gap = self._gaps[index]
            if (
                gap.gap_id == prepared.gap_id
                and gap.tool_name == prepared.tool_name
                and gap.status == EvidenceGapStatus.OPEN
            ):
                return index
        self._error("GAP_NOT_OPEN", prepared.fingerprint)
        raise AssertionError("unreachable")

    @staticmethod
    def _expected_record_type(gap: EvidenceGap) -> type[BaseModel]:
        return {
            EvidenceGapKind.LOCATE_FAILURE: DbtRunResultsFact,
            EvidenceGapKind.EXPLAIN_FAILURE: DbtNodeErrorFact,
            EvidenceGapKind.DISCOVER_SOURCE_RELATION: DbtLineageFact,
            EvidenceGapKind.DISCRIMINATE_SCHEMA: RelationSchemaFact,
            EvidenceGapKind.MAP_IMPACT: DbtLineageFact,
        }[gap.gap_kind]

    def _validate_record_compatibility(
        self,
        prepared: PreparedToolCall,
        gap: EvidenceGap,
        records: tuple[EvidenceRecord, ...],
    ) -> None:
        if not records:
            self._error("EVIDENCE_EMPTY", prepared.fingerprint)
        expected_type = self._expected_record_type(gap)
        seen_ids: set[str] = set()
        known = {record.evidence_id: record for record in self._records}
        for record in records:
            if not isinstance(record, EvidenceRecord):
                self._error("EVIDENCE_RECORD_INVALID", prepared.fingerprint)
            if record.run_id != self._run_id:
                self._error("RUN_CONTEXT_MISMATCH", prepared.fingerprint)
            if record.evidence_id in seen_ids:
                self._error("DUPLICATE_EVIDENCE", prepared.fingerprint)
            seen_ids.add(record.evidence_id)
            previous = known.get(record.evidence_id)
            if previous is not None and previous != record:
                self._error("EVIDENCE_ID_CONFLICT", prepared.fingerprint)
            if not isinstance(record.content, expected_type):
                self._error("EVIDENCE_TYPE_MISMATCH", prepared.fingerprint)

            content = record.content
            if isinstance(content, DbtNodeErrorFact) and content.node_id != prepared.arguments[
                "node_id"
            ]:
                self._error("EVIDENCE_SUBJECT_MISMATCH", prepared.fingerprint)
            if isinstance(content, DbtLineageFact) and (
                content.node_id != prepared.arguments["node_id"]
                or content.direction != prepared.arguments["direction"]
            ):
                self._error("EVIDENCE_SUBJECT_MISMATCH", prepared.fingerprint)
            if isinstance(content, RelationSchemaFact) and (
                content.relation_name != prepared.arguments["relation_name"]
            ):
                self._error("EVIDENCE_SUBJECT_MISMATCH", prepared.fingerprint)

    def record_tool_result(
        self,
        prepared: PreparedToolCall,
        records: tuple[EvidenceRecord, ...],
    ) -> tuple[EvidenceRecord, ...]:
        index = self._prepared_gap_index(prepared)
        gap = self._gaps[index]
        records = tuple(records)
        self._validate_record_compatibility(prepared, gap, records)
        known = {record.evidence_id: record for record in self._records}
        new_records = tuple(
            record for record in records if record.evidence_id not in known
        )
        self._records.extend(new_records)
        self._gaps[index] = gap.model_copy(
            update={
                "status": EvidenceGapStatus.CLOSED,
                "evidence_ids": tuple(record.evidence_id for record in records),
            }
        )
        self._prepared_calls.pop(prepared.fingerprint, None)
        self._revision += 1
        return new_records

    def record_tool_failure(
        self,
        prepared: PreparedToolCall,
        error_code: str,
    ) -> None:
        index = self._prepared_gap_index(prepared)
        safe_code = error_code if error_code in _SAFE_TOOL_ERRORS else "EVIDENCE_TOOL_ERROR"
        self._gaps[index] = self._gaps[index].model_copy(
            update={
                "status": EvidenceGapStatus.BLOCKED,
                "error_code": safe_code,
                "evidence_ids": (),
            }
        )
        self._prepared_calls.pop(prepared.fingerprint, None)
        self._revision += 1

    def _require_decision_scope(self, decision: KernelDecision) -> None:
        if (
            decision.incident_case_id != self._incident_case_id
            or decision.run_id != self._run_id
        ):
            self._error("DECISION_SCOPE_MISMATCH")

    def _closed_evidence_ids(self) -> set[str]:
        return {
            evidence_id
            for gap in self._gaps
            if gap.status == EvidenceGapStatus.CLOSED
            for evidence_id in gap.evidence_ids
        }

    def _validate_decision_evidence(
        self,
        decision: KernelDecision,
    ) -> dict[str, EvidenceRecord]:
        inventory = {record.evidence_id: record for record in self._records}
        closed = self._closed_evidence_ids()
        for assessment in decision.assessments:
            for evidence_id in assessment.evidence_ids:
                if evidence_id not in inventory:
                    self._error("ASSESSMENT_EVIDENCE_UNKNOWN")
                if evidence_id not in closed:
                    self._error("ASSESSMENT_EVIDENCE_UNBOUND")
        for claim in decision.claims:
            for evidence_id in claim.evidence_ids:
                if evidence_id not in inventory:
                    self._error("CLAIM_EVIDENCE_UNKNOWN")
                if evidence_id not in closed:
                    self._error("CLAIM_EVIDENCE_UNBOUND")
        return inventory

    def _validate_confirmed_decision(
        self,
        decision: KernelDecision,
    ) -> tuple[dict[str, EvidenceRecord], tuple[str, ...], tuple[str, ...]]:
        claim_keys = tuple((item.kind, item.value) for item in decision.claims)
        if len(claim_keys) != len(set(claim_keys)):
            self._error("DUPLICATE_CLAIM")
        if len(self._hypotheses) < 2:
            self._error("ALTERNATIVE_HYPOTHESIS_REQUIRED")
        if any(
            gap.status in {EvidenceGapStatus.OPEN, EvidenceGapStatus.BLOCKED}
            for gap in self._gaps
        ):
            self._error("EVIDENCE_GAP_OPEN")
        hypothesis_ids = {item.hypothesis_id for item in self._hypotheses}
        assessment_ids = tuple(item.hypothesis_id for item in decision.assessments)
        if set(assessment_ids) != hypothesis_ids or len(assessment_ids) != len(hypothesis_ids):
            self._error("HYPOTHESIS_ASSESSMENT_INCOMPLETE")
        selected_id = decision.selected_hypothesis_id
        assert selected_id is not None
        if selected_id not in hypothesis_ids:
            self._error("SELECTED_HYPOTHESIS_UNKNOWN")
        assessments = {item.hypothesis_id: item for item in decision.assessments}
        if assessments[selected_id].verdict != HypothesisVerdict.SUPPORTED:
            self._error("SELECTED_HYPOTHESIS_NOT_SUPPORTED")
        if not any(
            item.hypothesis_id != selected_id and item.verdict == HypothesisVerdict.REFUTED
            for item in decision.assessments
        ):
            self._error("REFUTED_HYPOTHESIS_REQUIRED")

        inventory = self._validate_decision_evidence(decision)
        root_claims = [item for item in decision.claims if item.kind == ClaimKind.ROOT_CAUSE]
        if len(root_claims) != 1:
            self._error("ROOT_CLAIM_REQUIRED")
        selected = next(item for item in self._hypotheses if item.hypothesis_id == selected_id)
        root_claim = root_claims[0]
        if root_claim.value != selected.root_cause_code:
            self._error("ROOT_CLAIM_MISMATCH")
        root_records = [inventory[item] for item in root_claim.evidence_ids]
        if not any(isinstance(item.content, DbtNodeErrorFact) for item in root_records) or not any(
            isinstance(item.content, RelationSchemaFact) for item in root_records
        ):
            self._error("ROOT_CLAIM_EVIDENCE_INCOMPATIBLE")

        asset_claims = [item for item in decision.claims if item.kind == ClaimKind.AFFECTED_ASSET]
        if not asset_claims:
            self._error("ASSET_CLAIM_REQUIRED")
        for claim in asset_claims:
            records = [inventory[item] for item in claim.evidence_ids]
            directly_supported = any(
                isinstance(record.content, DbtNodeErrorFact)
                and record.content.node_id == claim.value
                for record in records
            )
            downstream_supported = any(
                isinstance(record.content, DbtLineageFact)
                and record.content.direction == "downstream"
                and any(
                    node.node_id == claim.value or node.name == claim.value
                    for node in record.content.related_nodes
                )
                for record in records
            )
            if not directly_supported and not downstream_supported:
                self._error("ASSET_CLAIM_EVIDENCE_INCOMPATIBLE")

        claim_evidence_types = {
            inventory[evidence_id].evidence_type
            for claim in decision.claims
            for evidence_id in claim.evidence_ids
        }
        if not {
            EvidenceType.DBT_NODE_ERROR,
            EvidenceType.RELATION_SCHEMA,
            EvidenceType.DBT_LINEAGE,
        }.issubset(claim_evidence_types):
            self._error("CLAIM_EVIDENCE_TYPES_INCOMPLETE")

        affected_assets: list[str] = []
        evidence_ids: list[str] = []
        for claim in asset_claims:
            if claim.value not in affected_assets:
                affected_assets.append(claim.value)
        for claim in decision.claims:
            for evidence_id in claim.evidence_ids:
                if evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)
        return inventory, tuple(affected_assets), tuple(evidence_ids)

    def finalize(self, decision: KernelDecision) -> KernelOutcome:
        if self._final_status is not None:
            self._error("KERNEL_FINALIZED")
        self._require_decision_scope(decision)
        if decision.status == "INSUFFICIENT_EVIDENCE":
            self._assessments = ()
            self._claims = ()
            self._final_status = KernelFinalStatus.INSUFFICIENT_EVIDENCE
            self._gate_reason = "INSUFFICIENT_EVIDENCE"
            self._revision += 1
            return KernelOutcome(
                status=KernelFinalStatus.INSUFFICIENT_EVIDENCE,
                root_cause_code=None,
                affected_assets=(),
                evidence_ids=(),
                summary=decision.summary,
                recommended_actions=decision.recommended_actions,
                confidence=decision.confidence,
            )

        _, affected_assets, evidence_ids = self._validate_confirmed_decision(decision)
        selected_id = decision.selected_hypothesis_id
        assert selected_id is not None
        selected = next(item for item in self._hypotheses if item.hypothesis_id == selected_id)
        self._assessments = decision.assessments
        self._claims = decision.claims
        self._final_status = KernelFinalStatus.CONFIRMED
        self._gate_reason = "CONFIRMED"
        self._revision += 1
        return KernelOutcome(
            status=KernelFinalStatus.CONFIRMED,
            root_cause_code=selected.root_cause_code,
            affected_assets=affected_assets,
            evidence_ids=evidence_ids,
            summary=decision.summary,
            recommended_actions=decision.recommended_actions,
            confidence=decision.confidence,
        )

    def terminate_model_error(self, reason_code: str) -> KernelOutcome:
        if self._final_status is not None:
            self._error("KERNEL_FINALIZED")
        if reason_code not in _SAFE_MODEL_ERRORS:
            self._error("MODEL_ERROR_REASON_INVALID")
        self._final_status = KernelFinalStatus.MODEL_ERROR
        self._gate_reason = reason_code
        self._revision += 1
        return KernelOutcome(
            status=KernelFinalStatus.MODEL_ERROR,
            root_cause_code=None,
            affected_assets=(),
            evidence_ids=(),
            summary=reason_code,
            recommended_actions=(),
            confidence=0.0,
        )
