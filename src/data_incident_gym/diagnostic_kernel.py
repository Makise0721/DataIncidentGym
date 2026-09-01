from __future__ import annotations

import hashlib
import json
from datetime import datetime
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

from data_incident_gym.diagnosis import (
    KernelStateTraceEvent,
    UnresolvedEvidence,
)
from data_incident_gym.evidence import (
    DbtLineageFact,
    DbtNodeErrorFact,
    DbtRunResultsFact,
    EvidenceRecord,
    RelationDataProfileFact,
    RelationHistoryFact,
    RelationSchemaFact,
)

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


def _reject_duplicates(values: tuple[object, ...], field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")


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
        _reject_duplicates(self.evidence_ids, "assessment evidence_ids")
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
        _reject_duplicates(self.hypothesis_ids, "intent hypothesis_ids")
        _reject_duplicates(
            tuple(item.hypothesis_id for item in self.new_hypotheses),
            "new hypothesis IDs",
        )
        return self


class InvestigationIntentTransport(InvestigationIntent):
    """The exact text part paired with one Kernel business-tool call."""


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
    relation_name: StrictStr | None = None
    history_name: StrictStr | None = None
    bucket: StrictStr | None = None
    current_value: StrictInt | StrictFloat | None = None

    @model_validator(mode="after")
    def reject_duplicate_evidence_ids(self) -> Self:
        _reject_duplicates(self.evidence_ids, "claim evidence_ids")
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
        _reject_duplicates(
            tuple(item.hypothesis_id for item in self.assessments),
            "assessment hypothesis IDs",
        )
        _reject_duplicates(
            tuple((item.kind, item.value) for item in self.claims),
            "claim kind/value pairs",
        )
        _reject_duplicates(self.recommended_actions, "recommended_actions")
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
        _reject_duplicates(self.allowed_root_cause_codes, "ontology members")
        _reject_duplicates(tuple(item.hypothesis_id for item in self.hypotheses), "hypothesis IDs")
        _reject_duplicates(tuple(item.gap_id for item in self.gaps), "gap IDs")
        _reject_duplicates(self.evidence_inventory, "evidence inventory IDs")
        _reject_duplicates(self.tool_fingerprints, "tool fingerprints")
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


_GAP_TOOL: dict[EvidenceGapKind, tuple[str, str | None]] = {
    EvidenceGapKind.LOCATE_FAILURE: ("get_dbt_run_results", None),
    EvidenceGapKind.EXPLAIN_FAILURE: ("get_dbt_node_error", None),
    EvidenceGapKind.DISCOVER_SOURCE_RELATION: ("get_dbt_lineage", "upstream"),
    EvidenceGapKind.DISCRIMINATE_SCHEMA: ("get_relation_schema", None),
    EvidenceGapKind.MAP_IMPACT: ("get_dbt_lineage", "downstream"),
    EvidenceGapKind.PROFILE_RELATION: ("get_relation_data_profile", None),
    EvidenceGapKind.COMPARE_HISTORY: ("get_relation_history", None),
}
_SAFE_TOOL_ERRORS = {
    "EVIDENCE_TOOL_ERROR",
    "INVALID_ARTIFACT",
    "NODE_ERROR_NOT_FOUND",
    "NODE_NOT_FOUND",
    "PROFILE_METRIC_UNAVAILABLE",
    "PROFILE_OUTPUT_LIMIT",
    "PROFILE_SNAPSHOT_MISMATCH",
    "PROFILE_SPEC_INVALID",
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
    "MODEL_TOOL_CALL_LIMIT",
    "MODEL_TIMEOUT",
    "MODEL_PROTOCOL_ERROR",
    "MODEL_RUNTIME_ERROR",
}


def _fingerprint(run_id: str, tool_name: str, arguments: dict[str, str]) -> str:
    canonical = json.dumps(
        {"arguments": arguments, "run_id": run_id, "tool_name": tool_name},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _duplicate_count(
    profile: RelationDataProfileFact,
    collection: str,
    name: str,
) -> int | None:
    facts = getattr(profile.snapshot, collection)
    fact = next((item for item in facts if item.name == name), None)
    return None if fact is None else fact.duplicate_count


def _duplicate_root_supported(
    root_cause_code: str,
    records: list[EvidenceRecord],
    incident_subjects: set[str],
) -> bool:
    runs = [
        record.content
        for record in records
        if isinstance(record.content, DbtRunResultsFact)
    ]
    profiles = [
        record.content
        for record in records
        if isinstance(record.content, RelationDataProfileFact)
        and record.content.relation_name in incident_subjects
    ]
    if len(runs) != 1 or len(profiles) != 1:
        return False
    profile = profiles[0]
    key_count = _duplicate_count(profile, "business_key_duplicates", "id")
    fingerprint_count = _duplicate_count(
        profile,
        "business_fingerprint_duplicates",
        "order_payment_amount",
    )
    payment_method_group = next(
        (item for item in profile.snapshot.groups if item.name == "payment_method"),
        None,
    )
    if root_cause_code == "SOURCE_EXACT_PAYMENT_DUPLICATE":
        return (
            key_count is not None
            and key_count > 0
            and fingerprint_count is not None
            and payment_method_group is not None
        )
    if root_cause_code == "SOURCE_SEMANTIC_PAYMENT_DUPLICATE":
        return (
            runs[0].run_status == "SUCCEEDED"
            and not runs[0].failed_nodes
            and key_count == 0
            and fingerprint_count is not None
            and fingerprint_count > 0
            and payment_method_group is not None
        )
    return False


def _orphan_root_supported(
    records: list[EvidenceRecord],
    incident_subjects: set[str],
) -> bool:
    runs = [
        record.content
        for record in records
        if isinstance(record.content, DbtRunResultsFact)
    ]
    if (
        len(runs) != 1
        or runs[0].run_status != "SUCCEEDED"
        or runs[0].dbt_exit_code != 0
        or runs[0].failed_nodes
        or runs[0].skipped_nodes
    ):
        return False

    profiles = [
        record.content
        for record in records
        if isinstance(record.content, RelationDataProfileFact)
        and record.content.relation_name in incident_subjects
        and any(
            item.name == "order_id_to_raw_orders_id"
            and item.violation_count > 0
            for item in record.content.snapshot.relationship_violations
        )
    ]
    if len(profiles) != 1:
        return False

    histories = [
        record.content
        for record in records
        if isinstance(record.content, RelationHistoryFact)
        and record.content.relation_name in incident_subjects
        and record.content.relation_name != profiles[0].relation_name
    ]
    if len(histories) != 1:
        return False
    series = next(
        (
            item
            for item in histories[0].snapshot.histories
            if item.name == "order_count_by_day"
        ),
        None,
    )
    if (
        series is None
        or not series.points
        or series.watermark_column != "order_date"
        or series.watermark_value is None
    ):
        return False
    try:
        datetime.fromisoformat(series.watermark_value)
    except ValueError:
        return False
    return True


class DiagnosticKernel:
    def __init__(
        self,
        *,
        run_id: str,
        allowed_root_cause_codes: tuple[str, ...],
        model_request_limit: int,
        tool_call_limit: int,
        observable_relations: tuple[str, ...] = (),
        observable_schema_relations: tuple[str, ...] | None = None,
        observable_profile_relations: tuple[str, ...] | None = None,
        observable_history_relations: tuple[str, ...] | None = None,
        incident_subjects: tuple[str, ...] = (),
        health_target_subjects: tuple[str, ...] = (),
    ) -> None:
        self._run_id = run_id
        self._allowed_root_cause_codes = allowed_root_cause_codes
        self._model_request_limit = model_request_limit
        self._tool_call_limit = tool_call_limit
        default_relations = set(observable_relations)
        self._observable_relations_by_tool = {
            "get_relation_schema": set(
                observable_schema_relations
                if observable_schema_relations is not None
                else default_relations
            ),
            "get_relation_data_profile": set(
                observable_profile_relations
                if observable_profile_relations is not None
                else default_relations
            ),
            "get_relation_history": set(
                observable_history_relations
                if observable_history_relations is not None
                else default_relations
            ),
        }
        self._incident_subjects = set(incident_subjects)
        self._health_target_subjects = set(health_target_subjects)
        self._revision = 0
        self._hypotheses: list[Hypothesis] = []
        self._gaps: list[EvidenceGap] = []
        self._assessments: tuple[HypothesisAssessment, ...] = ()
        self._claims: tuple[ClaimEvidence, ...] = ()
        self._records: list[EvidenceRecord] = []
        self._fingerprints: list[str] = []
        self._prepared_calls: dict[str, PreparedToolCall] = {}
        self._final_status: KernelFinalStatus | None = None
        self._gate_reason: str | None = None
        self._selected_hypothesis_id: str | None = None

    @classmethod
    def start(
        cls,
        *,
        run_id: str,
        allowed_root_cause_codes: tuple[str, ...],
        model_request_limit: int,
        tool_call_limit: int,
        observable_relations: tuple[str, ...] = (),
        observable_schema_relations: tuple[str, ...] | None = None,
        observable_profile_relations: tuple[str, ...] | None = None,
        observable_history_relations: tuple[str, ...] | None = None,
        incident_subjects: tuple[str, ...] = (),
        health_target_subjects: tuple[str, ...] = (),
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
            run_id=run_id,
            allowed_root_cause_codes=allowed_root_cause_codes,
            model_request_limit=model_request_limit,
            tool_call_limit=tool_call_limit,
            observable_relations=observable_relations,
            observable_schema_relations=observable_schema_relations,
            observable_profile_relations=observable_profile_relations,
            observable_history_relations=observable_history_relations,
            incident_subjects=incident_subjects,
            health_target_subjects=health_target_subjects,
        )

    @property
    def evidence_records(self) -> tuple[EvidenceRecord, ...]:
        return tuple(self._records)

    def snapshot(self, *, model_requests_used: int) -> InvestigationState:
        if type(model_requests_used) is not int or not 0 <= model_requests_used <= (
            self._model_request_limit
        ):
            raise ValueError("model request usage exceeds Kernel budget")
        return InvestigationState(
            schema_version="p1.investigation.v1",
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
            selected_hypothesis_id=self._selected_hypothesis_id,
        )

    def _error(self, code: str, fingerprint: str | None = None) -> None:
        raise KernelError(code, fingerprint=fingerprint) from None

    def _validate_arguments(self, arguments: object, fingerprint: str) -> dict[str, str]:
        if type(arguments) is not dict or any(
            type(key) is not str or type(value) is not str for key, value in arguments.items()
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
        expected_keys = {
            "get_dbt_run_results": {"run_id"},
            "get_dbt_node_error": {"run_id", "node_id"},
            "get_relation_schema": {"relation_name"},
            "get_dbt_lineage": {"node_id", "direction"},
            "get_relation_data_profile": {"relation_name"},
            "get_relation_history": {"relation_name"},
        }
        if set(arguments) != expected_keys[tool_name]:
            self._error("ARGUMENTS_INVALID", fingerprint)
        if arguments.get("run_id") is not None and arguments["run_id"] != self._run_id:
            self._error("RUN_CONTEXT_MISMATCH", fingerprint)

    def _known_failed_nodes(self) -> set[str]:
        return {
            node
            for record in self._records
            if isinstance(record.content, DbtRunResultsFact)
            for node in record.content.failed_nodes
        }

    def _known_lineage_nodes(self) -> set[str]:
        return {
            node_id
            for record in self._records
            if isinstance(record.content, DbtLineageFact)
            for node_id in (
                record.content.node_id,
                *(item.node_id for item in record.content.related_nodes),
            )
        }

    def _known_relation_names(self) -> set[str]:
        return {
            relation_name
            for record in self._records
            for relation_name in (
                getattr(record.content, "relation_name", None),
                *(
                    node.name
                    for node in getattr(record.content, "related_nodes", ())
                ),
            )
            if isinstance(relation_name, str)
        }

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
            self._known_failed_nodes() | self._known_lineage_nodes() | self._incident_subjects
        ):
            self._error("NODE_ARGUMENT_NOT_PROVEN", fingerprint)
        if (
            tool_name
            in {"get_relation_schema", "get_relation_data_profile", "get_relation_history"}
            and arguments["relation_name"]
            not in self._observable_relations_by_tool[tool_name]
            and arguments["relation_name"] not in self._known_relation_names()
        ):
            self._error("RELATION_ARGUMENT_NOT_PROVEN", fingerprint)

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
        if fingerprint in self._fingerprints:
            self._error("DUPLICATE_TOOL_CALL", fingerprint)
        if any(gap.gap_id == intent.gap_id for gap in self._gaps):
            self._error("DUPLICATE_GAP_ID", fingerprint)
        validated = self._validate_arguments(arguments, fingerprint)
        self._validate_tool_mapping(intent, tool_name, validated, fingerprint)
        existing_ids = {item.hypothesis_id for item in self._hypotheses}
        for hypothesis in intent.new_hypotheses:
            if hypothesis.hypothesis_id in existing_ids:
                self._error("DUPLICATE_HYPOTHESIS", fingerprint)
            if hypothesis.root_cause_code not in self._allowed_root_cause_codes:
                self._error("ONTOLOGY_CODE_UNKNOWN", fingerprint)
            existing_ids.add(hypothesis.hypothesis_id)
        if any(
            hypothesis_id not in existing_ids for hypothesis_id in intent.hypothesis_ids
        ):
            self._error("HYPOTHESIS_REFERENCE_UNKNOWN", fingerprint)
        self._validate_argument_provenance(tool_name, validated, fingerprint)
        prepared = PreparedToolCall(
            gap_id=intent.gap_id,
            tool_name=tool_name,
            arguments=validated,
            fingerprint=fingerprint,
        )
        self._fingerprints.append(fingerprint)
        self._revision += 1
        self._hypotheses.extend(intent.new_hypotheses)
        self._gaps.append(
            EvidenceGap(
                gap_id=intent.gap_id,
                gap_kind=intent.gap_kind,
                hypothesis_ids=intent.hypothesis_ids,
                tool_name=tool_name,
                subject=(
                    validated.get("relation_name")
                    or validated.get("node_id")
                    or validated.get("run_id")
                    or tool_name
                ),
                status=EvidenceGapStatus.OPEN,
            )
        )
        self._prepared_calls[fingerprint] = prepared
        return prepared

    def _prepared_gap_index(self, prepared: PreparedToolCall) -> int:
        if self._prepared_calls.get(prepared.fingerprint) != prepared:
            self._error("PREPARED_CALL_INVALID", prepared.fingerprint)
        for index in range(len(self._gaps) - 1, -1, -1):
            gap = self._gaps[index]
            if gap.gap_id == prepared.gap_id and gap.status is EvidenceGapStatus.OPEN:
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
            EvidenceGapKind.PROFILE_RELATION: RelationDataProfileFact,
            EvidenceGapKind.COMPARE_HISTORY: RelationHistoryFact,
        }[gap.gap_kind]

    def _validate_record_compatibility(
        self,
        prepared: PreparedToolCall,
        gap: EvidenceGap,
        records: tuple[EvidenceRecord, ...],
    ) -> None:
        if not records:
            self._error("EVIDENCE_EMPTY", prepared.fingerprint)
        expected = self._expected_record_type(gap)
        known = {record.evidence_id: record for record in self._records}
        seen: set[str] = set()
        for record in records:
            if not isinstance(record, EvidenceRecord):
                self._error("EVIDENCE_RECORD_INVALID", prepared.fingerprint)
            if record.run_id != self._run_id:
                self._error("RUN_CONTEXT_MISMATCH", prepared.fingerprint)
            if record.evidence_id in seen or (
                record.evidence_id in known and known[record.evidence_id] != record
            ):
                self._error("DUPLICATE_EVIDENCE", prepared.fingerprint)
            seen.add(record.evidence_id)
            if not isinstance(record.content, expected):
                self._error("EVIDENCE_TYPE_MISMATCH", prepared.fingerprint)
            content = record.content
            if (
                isinstance(content, (DbtNodeErrorFact, DbtLineageFact))
                and content.node_id != prepared.arguments.get("node_id")
            ):
                self._error("EVIDENCE_SUBJECT_MISMATCH", prepared.fingerprint)
            if (
                isinstance(content, DbtLineageFact)
                and content.direction != prepared.arguments.get("direction")
            ):
                self._error("EVIDENCE_SUBJECT_MISMATCH", prepared.fingerprint)
            if (
                isinstance(content, RelationSchemaFact)
                and content.relation_name != prepared.arguments.get("relation_name")
            ):
                self._error("EVIDENCE_SUBJECT_MISMATCH", prepared.fingerprint)
            if (
                isinstance(content, (RelationDataProfileFact, RelationHistoryFact))
                and content.relation_name != prepared.arguments.get("relation_name")
            ):
                self._error("EVIDENCE_SUBJECT_MISMATCH", prepared.fingerprint)

    def record_tool_result(
        self,
        prepared: PreparedToolCall,
        records: tuple[EvidenceRecord, ...],
    ) -> tuple[EvidenceRecord, ...]:
        index = self._prepared_gap_index(prepared)
        records = tuple(records)
        self._validate_record_compatibility(prepared, self._gaps[index], records)
        known = {record.evidence_id for record in self._records}
        new_records = tuple(record for record in records if record.evidence_id not in known)
        self._records.extend(new_records)
        self._gaps[index] = self._gaps[index].model_copy(
            update={
                "status": EvidenceGapStatus.CLOSED,
                "evidence_ids": tuple(record.evidence_id for record in records),
            }
        )
        self._prepared_calls.pop(prepared.fingerprint, None)
        self._revision += 1
        return new_records

    def record_tool_failure(self, prepared: PreparedToolCall, error_code: str) -> None:
        index = self._prepared_gap_index(prepared)
        safe_code = error_code if error_code in _SAFE_TOOL_ERRORS else "EVIDENCE_TOOL_ERROR"
        self._gaps[index] = self._gaps[index].model_copy(
            update={"status": EvidenceGapStatus.BLOCKED, "error_code": safe_code}
        )
        self._prepared_calls.pop(prepared.fingerprint, None)
        self._revision += 1

    def _closed_records(self, evidence_ids: tuple[str, ...]) -> dict[str, EvidenceRecord]:
        closed = {
            evidence_id
            for gap in self._gaps
            if gap.status is EvidenceGapStatus.CLOSED
            for evidence_id in gap.evidence_ids
        }
        inventory = {record.evidence_id: record for record in self._records}
        if any(
            evidence_id not in inventory or evidence_id not in closed
            for evidence_id in evidence_ids
        ):
            self._error("CLAIM_EVIDENCE_UNBOUND")
        return inventory

    def _validate_confirmed(self, decision: KernelDecision) -> KernelOutcome:
        if len(self._hypotheses) < 2:
            self._error("ALTERNATIVE_HYPOTHESIS_REQUIRED")
        if any(gap.status is not EvidenceGapStatus.CLOSED for gap in self._gaps):
            self._error("EVIDENCE_GAP_OPEN")
        assessments = {item.hypothesis_id: item for item in decision.assessments}
        if set(assessments) != {item.hypothesis_id for item in self._hypotheses}:
            self._error("HYPOTHESIS_ASSESSMENT_INCOMPLETE")
        self._closed_records(
            tuple(
                evidence_id
                for assessment in decision.assessments
                for evidence_id in assessment.evidence_ids
            )
        )
        selected_id = decision.selected_hypothesis_id
        assert selected_id is not None
        selected = next(item for item in self._hypotheses if item.hypothesis_id == selected_id)
        if assessments[selected_id].verdict is not HypothesisVerdict.SUPPORTED:
            self._error("SELECTED_HYPOTHESIS_NOT_SUPPORTED")
        if not any(
            item.hypothesis_id != selected_id and item.verdict is HypothesisVerdict.REFUTED
            for item in decision.assessments
        ):
            self._error("REFUTED_HYPOTHESIS_REQUIRED")
        inventory = self._closed_records(
            tuple(evidence_id for claim in decision.claims for evidence_id in claim.evidence_ids)
        )
        root_claims = tuple(item for item in decision.claims if item.kind is ClaimKind.ROOT_CAUSE)
        asset_claims = tuple(
            item for item in decision.claims if item.kind is ClaimKind.AFFECTED_ASSET
        )
        if len(root_claims) != 1 or not asset_claims:
            self._error("CLAIMS_INCOMPLETE")
        root_claim = root_claims[0]
        if root_claim.value != selected.root_cause_code:
            self._error("ROOT_CLAIM_MISMATCH")
        root_records = [inventory[evidence_id] for evidence_id in root_claim.evidence_ids]
        node_errors = tuple(
            record.content
            for record in root_records
            if isinstance(record.content, DbtNodeErrorFact)
        )
        has_node_error = bool(node_errors)
        has_relation_fact = any(
            isinstance(record.content, (RelationSchemaFact, RelationDataProfileFact))
            for record in root_records
        )
        duplicate_root = root_claim.value in {
            "SOURCE_EXACT_PAYMENT_DUPLICATE",
            "SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
        }
        orphan_root = root_claim.value == "SOURCE_PERMANENT_ORPHAN_PAYMENT"
        if orphan_root:
            if not _orphan_root_supported(root_records, self._incident_subjects):
                self._error("ROOT_CLAIM_EVIDENCE_INCOMPATIBLE")
        elif duplicate_root:
            if not _duplicate_root_supported(
                root_claim.value,
                root_records,
                self._incident_subjects,
            ):
                self._error("ROOT_CLAIM_EVIDENCE_INCOMPATIBLE")
            if root_claim.value == "SOURCE_EXACT_PAYMENT_DUPLICATE":
                if self._incident_subjects and not any(
                    error.node_id in self._incident_subjects for error in node_errors
                ):
                    self._error("ROOT_CLAIM_EVIDENCE_INCOMPATIBLE")
                upstream_relations = {
                    node.name
                    for record in self._records
                    if isinstance(record.content, DbtLineageFact)
                    and record.content.direction == "upstream"
                    and record.content.node_id in {error.node_id for error in node_errors}
                    for node in record.content.related_nodes
                }
                if not has_node_error or not has_relation_fact or not any(
                    getattr(record.content, "relation_name", None) in upstream_relations
                    for record in root_records
                    if isinstance(record.content, (RelationSchemaFact, RelationDataProfileFact))
                ):
                    self._error("ROOT_CLAIM_EVIDENCE_INCOMPATIBLE")
        else:
            if self._incident_subjects and not any(
                error.node_id in self._incident_subjects for error in node_errors
            ):
                self._error("ROOT_CLAIM_EVIDENCE_INCOMPATIBLE")
            upstream_relations = {
                node.name
                for record in self._records
                if isinstance(record.content, DbtLineageFact)
                and record.content.direction == "upstream"
                and record.content.node_id in {error.node_id for error in node_errors}
                for node in record.content.related_nodes
            }
            if not has_node_error or not has_relation_fact or not any(
                getattr(record.content, "relation_name", None) in upstream_relations
                for record in root_records
                if isinstance(record.content, (RelationSchemaFact, RelationDataProfileFact))
            ):
                self._error("ROOT_CLAIM_EVIDENCE_INCOMPATIBLE")
        for claim in asset_claims:
            records = [inventory[evidence_id] for evidence_id in claim.evidence_ids]
            if not any(
                isinstance(record.content, DbtNodeErrorFact)
                and record.content.node_id == claim.value
                for record in records
            ) and not any(
                isinstance(record.content, DbtLineageFact)
                and record.content.direction == "downstream"
                and any(
                    node.node_id == claim.value or node.name == claim.value
                    for node in record.content.related_nodes
                )
                for record in records
            ) and not any(
                isinstance(record.content, DbtLineageFact)
                and record.content.direction == "upstream"
                and record.content.node_id in {error.node_id for error in node_errors}
                and any(
                    node.node_id == claim.value
                    and node.resource_type == "model"
                    and node.distance == 1
                    for node in record.content.related_nodes
                )
                for record in records
            ):
                self._error("ASSET_CLAIM_EVIDENCE_INCOMPATIBLE")
        evidence_ids = tuple(
            dict.fromkeys(
                evidence_id for claim in decision.claims for evidence_id in claim.evidence_ids
            )
        )
        self._assessments = decision.assessments
        self._claims = decision.claims
        self._selected_hypothesis_id = selected_id
        self._final_status = KernelFinalStatus.CONFIRMED
        self._gate_reason = "CONFIRMED"
        self._revision += 1
        return KernelOutcome(
            status=KernelFinalStatus.CONFIRMED,
            root_cause_code=selected.root_cause_code,
            affected_assets=tuple(claim.value for claim in asset_claims),
            evidence_ids=evidence_ids,
            summary=decision.summary,
            recommended_actions=decision.recommended_actions,
            confidence=decision.confidence,
        )

    def _validate_health(self, decision: KernelDecision) -> KernelOutcome:
        if any(gap.status is not EvidenceGapStatus.CLOSED for gap in self._gaps):
            self._error("EVIDENCE_GAP_OPEN")
        run_records = [
            record for record in self._records if isinstance(record.content, DbtRunResultsFact)
        ]
        if not run_records or any(
            record.content.run_status != "SUCCEEDED" or record.content.failed_nodes
            for record in run_records
        ):
            self._error("HEALTH_RUN_NOT_PROVEN")
        health_claims = tuple(
            item for item in decision.claims if item.kind is ClaimKind.HEALTH_STATE
        )
        if not health_claims or len(health_claims) != len(decision.claims):
            self._error("HEALTH_CLAIM_REQUIRED")
        inventory = self._closed_records(
            tuple(evidence_id for claim in health_claims for evidence_id in claim.evidence_ids)
        )
        for claim in health_claims:
            records = [inventory[evidence_id] for evidence_id in claim.evidence_ids]
            profile = next(
                (
                    record.content
                    for record in records
                    if isinstance(record.content, RelationDataProfileFact)
                    and record.content.relation_name == claim.relation_name
                ),
                None,
            )
            history = next(
                (
                    record.content
                    for record in records
                    if isinstance(record.content, RelationHistoryFact)
                    and record.content.relation_name == claim.relation_name
                ),
                None,
            )
            if profile is None or history is None:
                self._error("HEALTH_EVIDENCE_INCOMPATIBLE")
            history_series = next(
                (
                    series
                    for series in history.snapshot.histories
                    if series.name == claim.history_name
                ),
                None,
            )
            if history_series is None:
                self._error("HEALTH_HISTORY_NOT_DECLARED")
            if history_series.watermark_column is not None:
                if history_series.watermark_value is None:
                    self._error("HEALTH_WATERMARK_NOT_PROVEN")
                if history_series.sla_seconds is not None:
                    try:
                        watermark = datetime.fromisoformat(history_series.watermark_value)
                        observed_at = next(
                            record.observed_at
                            for record in records
                            if record.content == history
                        )
                        lag = (observed_at - watermark).total_seconds()
                    except (TypeError, ValueError):
                        self._error("HEALTH_WATERMARK_INVALID")
                    if lag < 0 or lag > history_series.sla_seconds:
                        self._error("HEALTH_SLA_NOT_SATISFIED")
            if self._health_target_subjects and (
                f"{claim.relation_name}/{claim.history_name}/{claim.bucket}"
                not in self._health_target_subjects
            ):
                self._error("HEALTH_POINT_NOT_ALERT_TARGET")
            current = next(
                (
                    point
                    for series in (history_series,)
                    for point in series.points
                    if point.bucket == claim.bucket
                ),
                None,
            )
            if current is None or current.value != claim.current_value:
                self._error("HEALTH_POINT_MISMATCH")
            if (
                history.snapshot.relation_name != profile.snapshot.relation_name
                or history.snapshot.relation_name != claim.relation_name
            ):
                self._error("HEALTH_RELATION_MISMATCH")
            prior = [
                point.value
                for series in history.snapshot.histories
                if series.name == claim.history_name
                for point in series.points
                if point.periodic_key == current.periodic_key and point.bucket < current.bucket
            ]
            if len(prior) < 4 or not min(prior) <= current.value <= max(prior):
                self._error("HEALTH_RANGE_NOT_PROVEN")
        evidence_ids = tuple(
            dict.fromkeys(
                evidence_id for claim in health_claims for evidence_id in claim.evidence_ids
            )
        )
        self._claims = decision.claims
        self._final_status = KernelFinalStatus.NO_INCIDENT
        self._gate_reason = "NO_INCIDENT"
        self._revision += 1
        return KernelOutcome(
            status=KernelFinalStatus.NO_INCIDENT,
            root_cause_code=None,
            affected_assets=(),
            evidence_ids=evidence_ids,
            summary=decision.summary,
            recommended_actions=decision.recommended_actions,
            confidence=decision.confidence,
        )

    def _validate_unresolved_declarations(
        self,
        declarations: tuple[UnresolvedEvidence, ...],
    ) -> None:
        _reject_duplicates(
            tuple(
                (item.evidence_kind, item.subject, item.reason_code)
                for item in declarations
            ),
            "unresolved evidence declarations",
        )
        blocked_schema = {
            (gap.subject, gap.error_code)
            for gap in self._gaps
            if (
                gap.gap_kind is EvidenceGapKind.DISCRIMINATE_SCHEMA
                and gap.status is EvidenceGapStatus.BLOCKED
            )
        }
        blocked_profiles = {
            (gap.subject, gap.error_code)
            for gap in self._gaps
            if (
                gap.gap_kind is EvidenceGapKind.PROFILE_RELATION
                and gap.status is EvidenceGapStatus.BLOCKED
            )
        }
        blocked_histories = {
            (gap.subject, gap.error_code)
            for gap in self._gaps
            if (
                gap.gap_kind is EvidenceGapKind.COMPARE_HISTORY
                and gap.status is EvidenceGapStatus.BLOCKED
            )
        }
        known_subjects = {
            node_id
            for record in self._records
            for node_id in (
                getattr(record.content, "node_id", None),
                *(
                    item.node_id
                    for item in getattr(record.content, "related_nodes", ())
                ),
            )
            if isinstance(node_id, str)
        }
        known_subjects.update(
            node_id
            for record in self._records
            if isinstance(record.content, DbtRunResultsFact)
            for node_id in (*record.content.failed_nodes, *record.content.skipped_nodes)
        )
        for item in declarations:
            if item.evidence_kind == "RELATION_SCHEMA":
                if (item.subject, item.reason_code) not in blocked_schema:
                    self._error("UNRESOLVED_EVIDENCE_UNBOUND")
            elif item.evidence_kind == "RELATION_DATA_PROFILE":
                if (item.subject, item.reason_code) not in blocked_profiles:
                    self._error("UNRESOLVED_EVIDENCE_UNBOUND")
            elif item.evidence_kind == "RELATION_HISTORY":
                if (item.subject, item.reason_code) not in blocked_histories:
                    self._error("UNRESOLVED_EVIDENCE_UNBOUND")
            elif item.evidence_kind in {"INGESTION_WATERMARK", "PAYMENT_EVENT_IDENTITY"}:
                if (
                    item.reason_code != "NOT_OBSERVABLE"
                    or item.subject not in self._incident_subjects
                ):
                    self._error("UNRESOLVED_EVIDENCE_UNBOUND")
            elif item.subject not in known_subjects:
                self._error("UNRESOLVED_EVIDENCE_UNBOUND")

    def finalize(self, decision: KernelDecision) -> KernelOutcome:
        if self._final_status is not None:
            self._error("KERNEL_FINALIZED")
        if decision.run_id != self._run_id:
            self._error("DECISION_SCOPE_MISMATCH")
        if decision.status == "CONFIRMED":
            return self._validate_confirmed(decision)
        if decision.status == "NO_INCIDENT":
            return self._validate_health(decision)
        if len(self._hypotheses) < 2:
            self._error("ALTERNATIVE_HYPOTHESIS_REQUIRED")
        if not any(
            gap.status in {EvidenceGapStatus.OPEN, EvidenceGapStatus.BLOCKED}
            for gap in self._gaps
        ) and not decision.unresolved_evidence:
            self._error("INSUFFICIENCY_GAP_REQUIRED")
        self._validate_unresolved_declarations(decision.unresolved_evidence)
        self._claims = ()
        self._final_status = KernelFinalStatus.INSUFFICIENT_EVIDENCE
        self._gate_reason = "INSUFFICIENT_EVIDENCE"
        self._revision += 1
        derived_unresolved = tuple(
            UnresolvedEvidence(
                evidence_kind=(
                    "RELATION_DATA_PROFILE"
                    if gap.gap_kind is EvidenceGapKind.PROFILE_RELATION
                    else (
                        "RELATION_HISTORY"
                        if gap.gap_kind is EvidenceGapKind.COMPARE_HISTORY
                        else "RELATION_SCHEMA"
                    )
                ),
                subject=gap.subject,
                reason_code="RELATION_NOT_ALLOWED"
                if gap.status is EvidenceGapStatus.BLOCKED
                else "NOT_OBSERVABLE",
            )
            for gap in self._gaps
            if gap.status in {EvidenceGapStatus.OPEN, EvidenceGapStatus.BLOCKED}
            and gap.gap_kind
            in {
                EvidenceGapKind.DISCRIMINATE_SCHEMA,
                EvidenceGapKind.PROFILE_RELATION,
                EvidenceGapKind.COMPARE_HISTORY,
            }
        )
        unresolved = tuple(dict.fromkeys((*derived_unresolved, *decision.unresolved_evidence)))
        return KernelOutcome(
            status=KernelFinalStatus.INSUFFICIENT_EVIDENCE,
            root_cause_code=None,
            affected_assets=(),
            evidence_ids=tuple(record.evidence_id for record in self._records),
            unresolved_evidence=unresolved,
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


__all__ = [
    "ClaimEvidence",
    "ClaimKind",
    "DiagnosticKernel",
    "EvidenceGap",
    "EvidenceGapKind",
    "EvidenceGapStatus",
    "Hypothesis",
    "HypothesisAssessment",
    "HypothesisVerdict",
    "InvestigationIntent",
    "InvestigationIntentTransport",
    "InvestigationState",
    "KernelDecision",
    "KernelError",
    "KernelFinalStatus",
    "KernelOutcome",
    "KernelStateTraceEvent",
    "PreparedToolCall",
]
