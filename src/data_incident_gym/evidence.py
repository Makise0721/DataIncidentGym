from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, NoReturn

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

from data_incident_gym.profiles import RelationHistorySnapshot, RelationProfileSnapshot

RUN_ID_PATTERN = r"^[0-9a-f]{32}$"


class EvidenceType(StrEnum):
    DBT_RUN_RESULTS = "DBT_RUN_RESULTS"
    DBT_NODE_ERROR = "DBT_NODE_ERROR"
    RELATION_SCHEMA = "RELATION_SCHEMA"
    DBT_LINEAGE = "DBT_LINEAGE"
    RELATION_DATA_PROFILE = "RELATION_DATA_PROFILE"
    RELATION_HISTORY = "RELATION_HISTORY"
    # T13 v2 facts; only the batch tools of the v2 surface produce them.
    RELATION_SCHEMA_EXPECTATION = "RELATION_SCHEMA_EXPECTATION"
    DBT_NODE_DEFINITION = "DBT_NODE_DEFINITION"


class EvidenceSource(StrEnum):
    DBT_RUN_RESULTS = "dbt_artifact:run_results.json"
    DBT_MANIFEST = "dbt_artifact:manifest.json"
    POSTGRES_CATALOG = "postgres_catalog"
    POSTGRES_PROFILE_SNAPSHOT = "postgres_profile_snapshot"
    #: The run-bound copy of the trusted healthy baseline (T13 E1).
    RUN_BASELINE = "run_artifact:baseline_evidence.json"


class RelationSchemaColumn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: StrictStr
    data_type: StrictStr
    nullable: StrictBool
    ordinal_position: StrictInt


class DbtRunResultsFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["DBT_RUN_RESULTS"]
    run_id: StrictStr = Field(pattern=RUN_ID_PATTERN)
    run_status: Literal["FAILED", "SUCCEEDED"]
    dbt_exit_code: StrictInt
    failed_nodes: tuple[StrictStr, ...]
    skipped_nodes: tuple[StrictStr, ...]


class DbtNodeErrorFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["DBT_NODE_ERROR"]
    run_id: StrictStr = Field(pattern=RUN_ID_PATTERN)
    node_id: StrictStr
    resource_type: StrictStr
    status: StrictStr
    message: StrictStr


class RelationSchemaFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["RELATION_SCHEMA"]
    run_id: StrictStr = Field(pattern=RUN_ID_PATTERN)
    schema_name: StrictStr
    relation_name: StrictStr
    columns: tuple[RelationSchemaColumn, ...]


class DbtLineageNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: StrictStr
    resource_type: StrictStr
    name: StrictStr
    distance: StrictInt


class DbtLineageFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["DBT_LINEAGE"]
    run_id: StrictStr = Field(pattern=RUN_ID_PATTERN)
    node_id: StrictStr
    direction: Literal["upstream", "downstream"]
    related_nodes: tuple[DbtLineageNode, ...]


class RelationDataProfileFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["RELATION_DATA_PROFILE"]
    run_id: StrictStr = Field(pattern=RUN_ID_PATTERN)
    relation_name: StrictStr
    profile_spec_version: Literal["profile_spec.v1"]
    profile_spec_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot: RelationProfileSnapshot


class ExpectedColumn(BaseModel):
    """One baseline expectation row: what the healthy contract recorded."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: StrictStr
    expected_data_type: StrictStr
    expected_nullable: StrictBool
    ordinal_position: StrictInt


class RelationSchemaExpectationFact(BaseModel):
    """The trusted baseline's column expectations for one relation (T13 E1).

    A fact only: it reports what the healthy baseline recorded, never whether
    the current observation deviates from it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["RELATION_SCHEMA_EXPECTATION"]
    run_id: StrictStr = Field(pattern=RUN_ID_PATTERN)
    relation_name: StrictStr
    baseline_fingerprint: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    known: StrictBool
    columns: tuple[ExpectedColumn, ...] = ()


class DbtNodeDefinitionFact(BaseModel):
    """One run-bound node definition: what the model actually executed (T13 E2).

    ``complete`` is False when the text exceeds the size cap (the text is then
    truncated): a partial definition must never be used for column mapping.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["DBT_NODE_DEFINITION"]
    run_id: StrictStr = Field(pattern=RUN_ID_PATTERN)
    node_id: StrictStr
    known: StrictBool
    resource_type: StrictStr | None = None
    declared_columns: tuple[StrictStr, ...] = ()
    depends_on: tuple[StrictStr, ...] = ()
    compiled_sql_sha256: StrictStr | None = None
    compiled_sql: StrictStr | None = None
    complete: StrictBool = True


class RelationHistoryFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["RELATION_HISTORY"]
    run_id: StrictStr = Field(pattern=RUN_ID_PATTERN)
    relation_name: StrictStr
    profile_spec_version: Literal["profile_spec.v1"]
    profile_spec_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot: RelationHistorySnapshot


SchemaColumn = RelationSchemaColumn
LineageNode = DbtLineageNode

type EvidenceContent = Annotated[
    DbtRunResultsFact
    | DbtNodeErrorFact
    | RelationSchemaFact
    | DbtLineageFact
    | RelationDataProfileFact
    | RelationHistoryFact
    | RelationSchemaExpectationFact
    | DbtNodeDefinitionFact,
    Field(discriminator="kind"),
]


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _strict_datetime_input(value: object) -> object:
    if not isinstance(value, (str, datetime)):
        raise ValueError("observed_at must be an ISO string or datetime")
    return value


_CONTENT_TYPES: dict[EvidenceType, type[BaseModel]] = {
    EvidenceType.DBT_RUN_RESULTS: DbtRunResultsFact,
    EvidenceType.DBT_NODE_ERROR: DbtNodeErrorFact,
    EvidenceType.RELATION_SCHEMA: RelationSchemaFact,
    EvidenceType.DBT_LINEAGE: DbtLineageFact,
    EvidenceType.RELATION_DATA_PROFILE: RelationDataProfileFact,
    EvidenceType.RELATION_HISTORY: RelationHistoryFact,
    EvidenceType.RELATION_SCHEMA_EXPECTATION: RelationSchemaExpectationFact,
    EvidenceType.DBT_NODE_DEFINITION: DbtNodeDefinitionFact,
}

_SOURCE_TYPES: dict[EvidenceType, EvidenceSource] = {
    EvidenceType.DBT_RUN_RESULTS: EvidenceSource.DBT_RUN_RESULTS,
    EvidenceType.DBT_NODE_ERROR: EvidenceSource.DBT_RUN_RESULTS,
    EvidenceType.RELATION_SCHEMA: EvidenceSource.POSTGRES_CATALOG,
    EvidenceType.DBT_LINEAGE: EvidenceSource.DBT_MANIFEST,
    EvidenceType.RELATION_DATA_PROFILE: EvidenceSource.POSTGRES_PROFILE_SNAPSHOT,
    EvidenceType.RELATION_HISTORY: EvidenceSource.POSTGRES_PROFILE_SNAPSHOT,
    EvidenceType.RELATION_SCHEMA_EXPECTATION: EvidenceSource.RUN_BASELINE,
    EvidenceType.DBT_NODE_DEFINITION: EvidenceSource.DBT_MANIFEST,
}

#: The frozen T13 batch tool surface (``p1.evidence_tools.v2``). Witness rules
#: dispatch on this identity — never on whether an event happens to carry
#: refusal entries, so a v1 tool can never switch rules through the new field.
EVIDENCE_BATCH_TOOLS = (
    "get_relation_schema_expectation",
    "get_dbt_node_definition",
)

#: Call-level summary code of an atomic batch refusal. It never witnesses a
#: gap: the per-target ``target_refusals`` entries carry the real codes.
TARGETS_REFUSED_CODE = "TARGETS_REFUSED"


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: StrictStr = Field(pattern=RUN_ID_PATTERN)
    evidence_id: StrictStr = Field(pattern=r"^ev_[0-9a-f]{64}$")
    evidence_type: EvidenceType
    source: EvidenceSource
    subject: StrictStr
    observed_at: Annotated[datetime, BeforeValidator(_strict_datetime_input)]
    content: EvidenceContent
    content_digest: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        evidence_type: EvidenceType,
        source: EvidenceSource,
        subject: str,
        observed_at: datetime,
        content: EvidenceContent,
    ) -> EvidenceRecord:
        content_payload = content.model_dump(mode="json")
        content_digest = hashlib.sha256(_canonical_bytes(content_payload)).hexdigest()
        identity = {
            "content_digest": content_digest,
            "evidence_type": evidence_type.value,
            "run_id": run_id,
            "source": source.value,
            "subject": subject,
        }
        evidence_id = "ev_" + hashlib.sha256(_canonical_bytes(identity)).hexdigest()
        return cls(
            run_id=run_id,
            evidence_id=evidence_id,
            evidence_type=evidence_type,
            source=source,
            subject=subject,
            observed_at=observed_at,
            content=content,
            content_digest=content_digest,
        )

    @model_validator(mode="after")
    def validate_contract(self) -> EvidenceRecord:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.content.run_id != self.run_id:
            raise ValueError("content run_id must match record run_id")
        expected_content_type = _CONTENT_TYPES[self.evidence_type]
        if not isinstance(self.content, expected_content_type):
            raise ValueError("evidence_type does not match content kind")
        if self.source != _SOURCE_TYPES[self.evidence_type]:
            raise ValueError("source does not match evidence_type")

        content_payload = self.content.model_dump(mode="json")
        expected_digest = hashlib.sha256(_canonical_bytes(content_payload)).hexdigest()
        if self.content_digest != expected_digest:
            raise ValueError("content_digest does not match content")
        identity = {
            "content_digest": expected_digest,
            "evidence_type": self.evidence_type.value,
            "run_id": self.run_id,
            "source": self.source.value,
            "subject": self.subject,
        }
        expected_id = "ev_" + hashlib.sha256(_canonical_bytes(identity)).hexdigest()
        if self.evidence_id != expected_id:
            raise ValueError("evidence_id does not match record identity")
        return self


def _safe_message(message: object) -> str:
    text = str(message)
    text = re.sub(
        r"(?i)\b(?:password|passwd|secret|token)\s*[:=]\s*[^\s,;]+",
        "[redacted credential]",
        text,
    )
    text = re.sub(
        r"(?i)\b(?:select|insert|update|delete|alter|create|drop|grant|revoke)\b.*",
        "[redacted SQL]",
        text,
    )
    text = re.sub(
        r'(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\|/)[^\r\n,;:()\[\]]*?\.[^\r\n\s,;:()\[\]]+',
        "[redacted path]",
        text,
    )
    text = re.sub(
        r'(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\|/)[^\r\n\s]*',
        "[redacted path]",
        text,
    )
    text = re.sub(
        r"(\[redacted path\])(?:[ \t]+[^\r\n\s,;:()\[\]]*[\\/][^\r\n\s,;:()\[\]]*)+",
        r"\1",
        text,
    )
    return text


class EvidenceToolError(RuntimeError):
    code = "EVIDENCE_TOOL_ERROR"

    def __init__(self, message: object = "Evidence tool failed") -> None:
        super().__init__(_safe_message(message))
        self.__cause__ = None
        self.__context__ = None


ERROR_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def safe_error_code(error: BaseException) -> str:
    """The error's stable code, or a generic fallback for anything else.

    Shared by every layer that must record a refusal without leaking message
    text: the runner's trace, the strategy-protocol receipts and the kernel.
    """

    code = getattr(error, "code", None)
    return (
        code
        if isinstance(code, str) and ERROR_CODE_PATTERN.fullmatch(code)
        else "EVIDENCE_TOOL_ERROR"
    )


class InvalidRunIdError(EvidenceToolError):
    code = "INVALID_RUN_ID"


class RunNotFoundError(EvidenceToolError):
    code = "RUN_NOT_FOUND"


class RunContextMismatchError(EvidenceToolError):
    code = "RUN_CONTEXT_MISMATCH"


class InvalidArtifactError(EvidenceToolError):
    code = "INVALID_ARTIFACT"


class NodeNotFoundError(EvidenceToolError):
    code = "NODE_NOT_FOUND"


class NodeErrorNotFoundError(EvidenceToolError):
    code = "NODE_ERROR_NOT_FOUND"


class InvalidDirectionError(EvidenceToolError):
    code = "INVALID_DIRECTION"


class RelationNotAllowedError(EvidenceToolError):
    code = "RELATION_NOT_ALLOWED"


class RelationNotFoundError(EvidenceToolError):
    code = "RELATION_NOT_FOUND"


class RunStateDriftError(EvidenceToolError):
    code = "RUN_STATE_DRIFT"


class ReadOnlyDatabaseError(EvidenceToolError):
    code = "READ_ONLY_DATABASE_ERROR"


class ProfileSpecInvalidError(EvidenceToolError):
    code = "PROFILE_SPEC_INVALID"


class ProfileMetricUnavailableError(EvidenceToolError):
    code = "PROFILE_METRIC_UNAVAILABLE"


class ProfileSnapshotMismatchError(EvidenceToolError):
    code = "PROFILE_SNAPSHOT_MISMATCH"


class ProfileOutputLimitError(EvidenceToolError):
    code = "PROFILE_OUTPUT_LIMIT"


class BatchTargetsRefusedError(EvidenceToolError):
    """An atomic batch refusal: every requested target is refused or none is.

    ``target_refusals`` is the authoritative per-target detail — ``(target,
    code)`` pairs in request order, deduped. The call-level code
    (``TARGETS_REFUSED``) only summarises; witnesses match the pairs.
    """

    code = TARGETS_REFUSED_CODE

    def __init__(
        self,
        message: object = "Batch request refused",
        *,
        target_refusals: tuple[tuple[str, str], ...] = (),
    ) -> None:
        super().__init__(message)
        self.target_refusals = target_refusals


class TargetsEmptyError(EvidenceToolError):
    code = "TARGETS_EMPTY"


class BatchTooLargeError(EvidenceToolError):
    code = "BATCH_TOO_LARGE"


class NodeNotAllowedError(EvidenceToolError):
    code = "NODE_NOT_ALLOWED"


class EvidenceIntegrityError(EvidenceToolError):
    code = "EVIDENCE_INTEGRITY_ERROR"


InvalidRunId = InvalidRunIdError
RunNotFound = RunNotFoundError
RunContextMismatch = RunContextMismatchError
InvalidArtifact = InvalidArtifactError
NodeNotFound = NodeNotFoundError
NodeErrorNotFound = NodeErrorNotFoundError
InvalidDirection = InvalidDirectionError
RelationNotAllowed = RelationNotAllowedError
RelationNotFound = RelationNotFoundError
RunStateDrift = RunStateDriftError
ReadOnlyDatabase = ReadOnlyDatabaseError
ProfileSpecInvalid = ProfileSpecInvalidError
ProfileMetricUnavailable = ProfileMetricUnavailableError
ProfileSnapshotMismatch = ProfileSnapshotMismatchError
ProfileOutputLimit = ProfileOutputLimitError


def _clean(error: EvidenceToolError) -> EvidenceToolError:
    error.__cause__ = None
    error.__context__ = None
    return error


def raise_without_context(error: EvidenceToolError) -> NoReturn:
    try:
        raise error from None
    except EvidenceToolError as raised:
        raised.__context__ = None
        raise
