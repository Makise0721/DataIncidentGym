from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from time import monotonic
from typing import Annotated, Any, Literal, Protocol

from openai import AsyncOpenAI
from pydantic import BaseModel, Field, StrictStr, ValidationError
from pydantic_ai import Agent, ModelRetry, RunContext, RunUsage, UsageLimits
from pydantic_ai.exceptions import (
    IncompleteToolCall,
    ModelAPIError,
    ToolFailed,
    ToolRetryError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.diagnosis import (
    KERNEL_STRATEGIES,
    MODEL_STRATEGIES,
    AffectedAssetClaim,
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    EvidenceGateTraceEvent,
    HealthStateClaim,
    KernelStateTraceEvent,
    ModelCallShape,
    ModelProtocolTraceEvent,
    PolicyIdentity,
    RejectedAssessmentSummary,
    RejectedClaimSummary,
    RejectedDecisionSummary,
    RejectedUnresolvedSummary,
    RootCauseClaim,
    ToolTraceEvent,
    TraceEvent,
)
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.diagnostic_contracts import gap_kind_for_tool
from data_incident_gym.diagnostic_kernel import (
    _HYPOTHESIS_ID_PATTERN,
    ClaimKind,
    DiagnosticKernel,
    Hypothesis,
    InvestigationIntent,
    InvestigationState,
    KernelDecision,
    KernelError,
    KernelFinalStatus,
    KernelOutcome,
    PreparedToolCall,
)
from data_incident_gym.evidence import DbtRunResultsFact, EvidenceRecord, EvidenceToolError
from data_incident_gym.evidence_tools import EvidenceTools
from data_incident_gym.run_context import ObservableRunContext, resolve_run_context

BASE_PROMPT_VERSION = "p1.base.v1"
KERNEL_PROMPT_VERSION = "p1.kernel.v14"
STATIC_PROMPT_VERSION = "p1.static.v5"
NO_TOOL_PROMPT_VERSION = "p1.no-tool.v1"
CONTROLLER_PROTOCOL_VERSION = "p1.controller.v15"

P1_ROOT_CAUSE_CODES = (
    "SOURCE_SCHEMA_COLUMN_RENAMED",
    "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
    "TRANSFORMATION_COLUMN_CAST_CHANGED",
    "SOURCE_REQUIRED_FIELD_NULL",
    "TRANSFORMATION_REQUIRED_FIELD_NULL",
    "SOURCE_EXACT_PAYMENT_DUPLICATE",
    "SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
    "LEGITIMATE_SPLIT_PAYMENT",
    "SOURCE_PERMANENT_ORPHAN_PAYMENT",
    "NORMAL_LATE_ARRIVING_ORDER",
    "SOURCE_PAYMENT_INGESTION_LOSS",
    "NORMAL_BUSINESS_PAYMENT_DECLINE",
)

MODEL_REQUEST_LIMIT = 8
TOOL_CALL_LIMIT = 8
OUTPUT_RETRY_LIMIT = 2
TIMEOUT_SECONDS = 300

TOOL_NAMES = (
    "get_dbt_run_results",
    "get_dbt_node_error",
    "get_relation_schema",
    "get_dbt_lineage",
    "get_relation_data_profile",
    "get_relation_history",
)


def _read_prompt(name: str) -> str:
    path = Path(__file__).parent / "prompts" / name
    try:
        content = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        text = content.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"无法读取诊断提示词：{name}") from exc
    if not text.strip():
        raise RuntimeError(f"诊断提示词不能为空：{name}")
    return text


BASE_PROMPT = _read_prompt("base_safety.md")
KERNEL_PROMPT = _read_prompt("diagnostic_kernel.md")
STATIC_PROMPT = _read_prompt("static_skill.md")
NO_TOOL_PROMPT = _read_prompt("no_tool.md")


def load_strategy_prompt(strategy: DiagnosticStrategy) -> str:
    strategy = DiagnosticStrategy(strategy)
    if strategy in KERNEL_STRATEGIES:
        return KERNEL_PROMPT
    if strategy is DiagnosticStrategy.NO_TOOL:
        return NO_TOOL_PROMPT
    return STATIC_PROMPT


def _enabled_tool_names(strategy: DiagnosticStrategy) -> tuple[str, ...]:
    if strategy is DiagnosticStrategy.NO_TOOL:
        return ()
    if strategy is DiagnosticStrategy.KERNEL_NO_LINEAGE:
        return tuple(name for name in TOOL_NAMES if name != "get_dbt_lineage")
    if strategy is DiagnosticStrategy.KERNEL_NO_SCHEMA:
        return tuple(name for name in TOOL_NAMES if name != "get_relation_schema")
    return TOOL_NAMES


def _is_kernel_strategy(strategy: DiagnosticStrategy) -> bool:
    return strategy in KERNEL_STRATEGIES


def load_base_prompt() -> str:
    return BASE_PROMPT


SYSTEM_PROMPT = f"{BASE_PROMPT}\n\n{KERNEL_PROMPT}"
SYSTEM_PROMPT_VERSION = KERNEL_PROMPT_VERSION
SYSTEM_PROMPT_SHA256 = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()

_MODEL_ERROR_REASONS = {
    "MODEL_DECLINED",
    "MODEL_REQUEST_LIMIT",
    "MODEL_TOOL_CALL_LIMIT",
    "MODEL_TIMEOUT",
    "MODEL_PROTOCOL_ERROR",
    "MODEL_RUNTIME_ERROR",
}


class _StaticDecision(Diagnosis):
    status: Literal[
        DiagnosisStatus.CONFIRMED,
        DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        DiagnosisStatus.NO_INCIDENT,
    ]


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
_SAFE_CONTROLLER_ERRORS = {
    "ARGUMENTS_INVALID",
    "DUPLICATE_EVIDENCE",
    "DUPLICATE_GAP_ID",
    "DUPLICATE_HYPOTHESIS",
    "DUPLICATE_TOOL_CALL",
    "EVIDENCE_EMPTY",
    "EVIDENCE_GAP_OPEN",
    "EVIDENCE_RECORD_INVALID",
    "EVIDENCE_SUBJECT_MISMATCH",
    "EVIDENCE_TYPE_MISMATCH",
    "GAP_TOOL_MISMATCH",
    "HYPOTHESIS_ASSESSMENT_INCOMPLETE",
    "HYPOTHESIS_REFERENCE_UNKNOWN",
    "INSUFFICIENCY_GAP_REQUIRED",
    "NODE_ARGUMENT_NOT_PROVEN",
    "ONTOLOGY_CODE_UNKNOWN",
    "RELATION_ARGUMENT_NOT_PROVEN",
    "RELATION_NOT_ALLOWED",
    "RUN_CONTEXT_MISMATCH",
}

_PATH_PATTERN = re.compile(r"(?i)(?:[a-z]:[\\/]|\\\\|/)[^\r\n,;:()\[\]]+")
_SQL_PATTERN = re.compile(
    r"(?is)\b(?:select|insert|update|delete|alter|create|drop|grant|revoke)\b.*"
)
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(?:\b(?:password|passwd|secret|token|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+"
    r"|\bBearer\s+[^\s,;]+|\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s]+@[^\s,;]+)"
)


@dataclass(frozen=True)
class ModelIdentity:
    provider: str
    model: str


@dataclass(frozen=True)
class DiagnosisBudget:
    model_request_limit: int = MODEL_REQUEST_LIMIT
    tool_call_limit: int = TOOL_CALL_LIMIT
    output_retry_limit: int = OUTPUT_RETRY_LIMIT
    timeout_seconds: int = TIMEOUT_SECONDS


@dataclass(frozen=True)
class PolicySurface:
    tool_schema_payload: list[dict[str, object]]
    strategy_prompt_version: str
    strategy_prompt_sha256: str
    controller_protocol_sha256: str
    final_diagnosis_schema_sha256: str
    policy_identity: PolicyIdentity


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _redact_trace_value(value: str) -> str:
    value = _CREDENTIAL_PATTERN.sub("[redacted credential]", value)
    value = _SQL_PATTERN.sub("[redacted SQL]", value)
    return _PATH_PATTERN.sub("[redacted path]", value)


def _safe_tool_error_code(code: object) -> str:
    if isinstance(code, str) and code in _SAFE_TOOL_ERRORS:
        return code
    return "EVIDENCE_TOOL_ERROR"


def _controller_error_code(code: str) -> str:
    return code if code in _SAFE_CONTROLLER_ERRORS else "CONTROLLER_CONTRACT_REJECTED"


class _PolicyError(RuntimeError):
    def __init__(self, code: str, *, fingerprint: str | None = None) -> None:
        self.code = code
        self.fingerprint = fingerprint
        super().__init__(code)
        self.__cause__ = None
        self.__context__ = None


@dataclass(frozen=True)
class PreparedEvidenceCall:
    tool_name: str
    arguments: dict[str, str]
    fingerprint: str
    kernel_call: PreparedToolCall | None = None


class _PolicyAdapter(Protocol):
    def prepare(
        self,
        *,
        tool_name: str,
        arguments: dict[str, str],
        observation: ModelResponse | None,
        intent: InvestigationIntent | None,
    ) -> PreparedEvidenceCall:
        ...

    def accept(
        self,
        prepared: PreparedEvidenceCall,
        records: tuple[EvidenceRecord, ...],
    ) -> tuple[EvidenceRecord, ...]:
        ...

    def reject(self, prepared: PreparedEvidenceCall, error_code: str) -> None:
        ...


def _fingerprint(run_id: str, tool_name: str, arguments: dict[str, str]) -> str:
    return _sha256_json(
        {"arguments": arguments, "run_id": run_id, "tool_name": tool_name}
    )


def _expected_tool_arguments(tool_name: str) -> set[str]:
    return {
        "get_dbt_run_results": {"run_id"},
        "get_dbt_node_error": {"run_id", "node_id"},
        "get_relation_schema": {"relation_name"},
        "get_dbt_lineage": {"node_id", "direction"},
        "get_relation_data_profile": {"relation_name"},
        "get_relation_history": {"relation_name"},
    }[tool_name]


@dataclass
class _RunState:
    run_id: str
    strategy: DiagnosticStrategy
    tools: EvidenceTools
    context: ObservableRunContext
    adapter: _PolicyAdapter
    kernel: DiagnosticKernel | None = None
    started_at: float = field(default_factory=monotonic)
    trace: list[object] = field(default_factory=list)
    evidence_records: list[EvidenceRecord] = field(default_factory=list)
    usage: RunUsage = field(default_factory=RunUsage)
    successful_calls: int = 0
    outcome: KernelOutcome | None = None
    static_diagnosis: Diagnosis | None = None
    last_response: ModelResponse | None = None
    last_observation: tuple[tuple[str, ...], tuple[str, ...], bool] | None = None
    current_output_details: tuple[tuple[str, ...], tuple[str, ...]] | None = None
    current_response_shape: tuple[tuple[ModelCallShape, ...], str] | None = None
    current_error_type: str | None = None
    current_error_origin: str | None = None
    # Historical retry background: which surfaces had already been asked to
    # retry before the current request. It never by itself names the current
    # failure.
    retry_prompt_targets: tuple[str, ...] = ()
    # Adapter request attempt counter, 1-based. It counts calls into the adapter
    # (one model request each) and is independent of SDK usage accounting, so a
    # provider-level failure still advances it.
    request_attempt: int = 0
    registered_business_tools: frozenset[str] = frozenset()
    # Attempt index in which the output validator rejected a decision through the
    # kernel; cleared at the start of every attempt. Only genuine domain
    # rejections count -- "already finalized" is a state conflict, not a verdict.
    kernel_rejection_attempt: int | None = None
    output_retry_used: int | None = None
    output_retry_limit: int | None = None
    protocol_failure: tuple[str, str, str | None] | None = None
    protocol_trace_recorded: bool = False
    next_gap_number: int = 1

    def begin_request_attempt(
        self,
        messages: Sequence[ModelMessage],
        parameters: ModelRequestParameters,
    ) -> None:
        """Start a new adapter request attempt and isolate its observations.

        Everything describing a response is cleared *before* the underlying
        request runs, so a request that raises cannot inherit the previous
        round's response shape, retry count or kernel verdict. Persisted trace
        entries and kernel investigation state are untouched.
        """

        self.request_attempt += 1
        self.last_response = None
        self.last_observation = None
        self.current_output_details = None
        self.current_response_shape = None
        self.current_error_type = None
        self.current_error_origin = None
        self.kernel_rejection_attempt = None
        self.output_retry_used = None
        self.output_retry_limit = None
        self.protocol_failure = None
        self.protocol_trace_recorded = False
        self.registered_business_tools = frozenset(
            tool.name for tool in parameters.function_tools
        )
        self.retry_prompt_targets = _retry_prompt_targets(messages, parameters)


    def allocate_kernel_intent(
        self,
        tool_name: str,
        arguments: dict[str, str],
        hypothesis_ids: tuple[str, ...],
        new_hypotheses: tuple[Hypothesis, ...],
    ) -> InvestigationIntent:
        """Allocate the next run-scoped auto gap ID and derive the gap kind."""

        gap_kind = gap_kind_for_tool(tool_name, arguments)
        gap_id = f"g_auto_{self.next_gap_number}"
        self.next_gap_number += 1
        return InvestigationIntent(
            gap_id=gap_id,
            gap_kind=gap_kind,
            hypothesis_ids=hypothesis_ids,
            new_hypotheses=new_hypotheses,
        )

    def record_model_response(
        self,
        response: ModelResponse,
        parameters: ModelRequestParameters,
    ) -> None:
        """Record the response of the current attempt.

        Response-scoped cleanup happens in `begin_request_attempt`, before the
        request runs; this method only fills in what the response contains.
        """

        self.last_response = response
        function_names = {tool.name for tool in parameters.function_tools}
        output_names = {tool.name for tool in parameters.output_tools}
        function_calls = tuple(
            part.tool_name
            for part in response.parts
            if isinstance(part, ToolCallPart) and part.tool_name in function_names
        )
        output_calls = tuple(
            part.tool_name
            for part in response.parts
            if isinstance(part, ToolCallPart) and part.tool_name in output_names
        )
        self.last_observation = (
            function_calls,
            output_calls,
            any(isinstance(part, TextPart) for part in response.parts),
        )

    def set_protocol_failure(
        self,
        *,
        category: str,
        stage: str,
        tool_name: str | None,
        error_loc: tuple[str, ...] = (),
        error_kind: tuple[str, ...] = (),
        error_type: str | None = None,
        origin: str | None = None,
    ) -> None:
        self.protocol_failure = (category, stage, tool_name, error_loc, error_kind)
        self.current_error_type = error_type
        self.current_error_origin = origin

    def append_protocol_trace(self, *, output_retry_used: int | None = None) -> None:
        if self.protocol_trace_recorded or self.protocol_failure is None:
            return
        category, stage, tool_name, error_loc, error_kind = self.protocol_failure
        shapes, ended_with = self.current_response_shape or ((), None)
        if output_retry_used is None:
            output_retry_used = self.output_retry_used
        self.trace.append(
            ModelProtocolTraceEvent(
                event_type="MODEL_PROTOCOL",
                stage=stage,
                tool_name=tool_name,
                category=category,
                error_loc=error_loc,
                error_kind=error_kind,
                # The adapter attempt that produced this failure; a request that
                # raised before returning a response still advances it.
                model_request_index=self.request_attempt,
                output_retry_used=output_retry_used,
                call_shapes=shapes,
                response_ended_with=ended_with,
                error_type=self.current_error_type,
                error_origin=self.current_error_origin,
                retry_prompt_targets=self.retry_prompt_targets,
            )
        )
        self.protocol_trace_recorded = True

    def record_tool_trace(
        self,
        *,
        tool_name: str,
        arguments: dict[str, str],
        fingerprint: str,
        evidence_ids: tuple[str, ...] = (),
        error_code: str | None = None,
        started_at: float,
    ) -> None:
        self.trace.append(
            ToolTraceEvent(
                event_type="TOOL_CALL",
                tool_name=tool_name,
                arguments={
                    key: _redact_trace_value(value) for key, value in arguments.items()
                },
                fingerprint=fingerprint,
                evidence_ids=evidence_ids,
                error_code=error_code,
                elapsed_ms=max(0, int((monotonic() - started_at) * 1000)),
            )
        )


class _StaticPolicyAdapter:
    def __init__(self, state: _RunState, *, tool_call_limit: int) -> None:
        self._state = state
        self._tool_call_limit = tool_call_limit
        self._fingerprints: list[str] = []
        self._prepared: set[str] = set()

    def prepare(
        self,
        *,
        tool_name: str,
        arguments: dict[str, str],
        observation: ModelResponse | None,
        intent: InvestigationIntent | None,
    ) -> PreparedEvidenceCall:
        del observation, intent
        fingerprint = _fingerprint(self._state.run_id, tool_name, arguments)
        if len(self._fingerprints) >= self._tool_call_limit:
            raise _PolicyError("TOOL_CALL_LIMIT", fingerprint=fingerprint)
        if fingerprint in self._fingerprints:
            raise _PolicyError("DUPLICATE_TOOL_CALL", fingerprint=fingerprint)
        if tool_name not in TOOL_NAMES or set(arguments) != _expected_tool_arguments(tool_name):
            raise _PolicyError("ARGUMENTS_INVALID", fingerprint=fingerprint)
        if arguments.get("run_id") not in {None, self._state.run_id}:
            raise _PolicyError("RUN_CONTEXT_MISMATCH", fingerprint=fingerprint)
        self._fingerprints.append(fingerprint)
        self._prepared.add(fingerprint)
        return PreparedEvidenceCall(tool_name, dict(arguments), fingerprint)

    def accept(
        self,
        prepared: PreparedEvidenceCall,
        records: tuple[EvidenceRecord, ...],
    ) -> tuple[EvidenceRecord, ...]:
        if prepared.fingerprint not in self._prepared:
            raise _PolicyError("PREPARED_CALL_INVALID", fingerprint=prepared.fingerprint)
        if not records:
            raise _PolicyError("EVIDENCE_EMPTY", fingerprint=prepared.fingerprint)
        seen = {record.evidence_id for record in self._state.evidence_records}
        fresh: list[EvidenceRecord] = []
        for record in records:
            if not isinstance(record, EvidenceRecord) or record.run_id != self._state.run_id:
                raise _PolicyError("EVIDENCE_RECORD_INVALID", fingerprint=prepared.fingerprint)
            if record.evidence_id in seen or any(
                item.evidence_id == record.evidence_id for item in fresh
            ):
                raise _PolicyError("DUPLICATE_EVIDENCE", fingerprint=prepared.fingerprint)
            fresh.append(record)
        self._state.evidence_records.extend(fresh)
        self._prepared.remove(prepared.fingerprint)
        return tuple(fresh)

    def reject(self, prepared: PreparedEvidenceCall, error_code: str) -> None:
        del error_code
        self._prepared.discard(prepared.fingerprint)


class _KernelPolicyAdapter:
    def __init__(self, state: _RunState) -> None:
        self._state = state

    def prepare(
        self,
        *,
        tool_name: str,
        arguments: dict[str, str],
        observation: ModelResponse | None,
        intent: InvestigationIntent | None,
    ) -> PreparedEvidenceCall:
        del observation
        kernel = self._state.kernel
        if kernel is None:
            raise _PolicyError("KERNEL_NOT_INITIALIZED")
        if intent is None:
            raise _PolicyError("KERNEL_INTENT_MISSING")
        try:
            prepared = kernel.prepare_tool(
                intent=intent,
                tool_name=tool_name,
                arguments=arguments,
            )
        except KernelError as error:
            raise _PolicyError(error.code, fingerprint=error.fingerprint) from None
        return PreparedEvidenceCall(
            tool_name=tool_name,
            arguments=dict(arguments),
            fingerprint=prepared.fingerprint,
            kernel_call=prepared,
        )

    def accept(
        self,
        prepared: PreparedEvidenceCall,
        records: tuple[EvidenceRecord, ...],
    ) -> tuple[EvidenceRecord, ...]:
        kernel = self._state.kernel
        if kernel is None or prepared.kernel_call is None:
            raise _PolicyError("KERNEL_NOT_INITIALIZED", fingerprint=prepared.fingerprint)
        try:
            return kernel.record_tool_result(prepared.kernel_call, records)
        except KernelError as error:
            raise _PolicyError(error.code, fingerprint=prepared.fingerprint) from None

    def reject(self, prepared: PreparedEvidenceCall, error_code: str) -> None:
        kernel = self._state.kernel
        if kernel is not None and prepared.kernel_call is not None:
            with suppress(KernelError):
                kernel.record_tool_failure(prepared.kernel_call, error_code)


class _ModelObservationAdapter(Model):
    """Capture safe response-shape facts before PydanticAI validates the response."""

    def __init__(self, model: Model, state: _RunState) -> None:
        super().__init__(settings=model.settings, profile=model.profile)
        self._model = model
        self._state = state

    @property
    def provider(self):
        return self._model.provider

    @property
    def profile(self):
        return self._model.profile

    @property
    def model_name(self) -> str:
        return self._model.model_name

    @property
    def system(self):
        return self._model.system

    @property
    def base_url(self) -> str | None:
        return self._model.base_url

    @property
    def tool_deferral_mode(self):
        return self._model.tool_deferral_mode

    @property
    def tool_addition_mode(self):
        return self._model.tool_addition_mode

    def prepare_request(self, model_settings, model_request_parameters):
        return self._model.prepare_request(model_settings, model_request_parameters)

    def prepare_messages(self, messages, model_request_parameters=None):
        return self._model.prepare_messages(messages, model_request_parameters)

    def resolve_prompt_cache_retention(self, model_settings):
        return self._model.resolve_prompt_cache_retention(model_settings)

    async def request(
        self,
        messages,
        model_settings,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        # Isolate this attempt before the request runs: a request that raises
        # must not carry the previous round's response observations.
        self._state.begin_request_attempt(messages, model_request_parameters)
        response = await self._model.request(messages, model_settings, model_request_parameters)
        self._state.record_model_response(response, model_request_parameters)
        self._state.current_output_details = _output_call_details(
            response,
            model_request_parameters,
            self._state.strategy,
        )
        self._state.current_response_shape = _response_shape(response, model_request_parameters)
        return response


_MAX_ERROR_ITEMS = 3
_MAX_LOC_SEGMENTS = 4
_MAX_LOC_SEGMENT_CHARS = 64
_MAX_LOC_TOTAL_CHARS = 200
_UNKNOWN_FIELD_MARKER = "<unknown-field>"

# Known output/claim/tool schema fields; only these names may appear in trace
# error locations. Anything else is replaced with the fixed marker above, so
# model-supplied keys never reach the persisted trace.
_SAFE_LOC_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "run_id",
        "summary",
        "confidence",
        "selected_hypothesis_id",
        "assessments",
        "claims",
        "unresolved_evidence",
        "recommended_actions",
        "hypothesis_id",
        "root_cause_code",
        "verdict",
        "evidence_ids",
        "kind",
        "value",
        "relation_name",
        "history_name",
        "bucket",
        "current_value",
        "evidence_kind",
        "subject",
        "reason_code",
        "asset",
        "affected_assets",
        "node_id",
        "direction",
        "gap_id",
        "gap_kind",
        "tool_name",
        "error_code",
        "kernel_hypothesis_ids",
        "kernel_new_hypotheses",
        "arguments",
        "fingerprint",
    }
)

# Fixed Pydantic error-kind vocabulary; anything outside is dropped.
_SAFE_ERROR_KINDS = frozenset(
    {
        "missing",
        "extra_forbidden",
        "literal_error",
        "value_error",
        "string_pattern_mismatch",
        "string_type",
        "model_attributes_type",
        "union_tag_invalid",
        "json_invalid",
        "int_type",
        "float_type",
        "bool_type",
        "dict_type",
        "list_type",
        "tuple_type",
        "string_too_short",
        "string_too_long",
        "int_too_small",
        "int_too_big",
        "finite_number",
        "greater_than",
        "less_than",
        "url_parsing",
        "uuid_parsing",
        "date_parsing",
        "datetime_parsing",
        "time_parsing",
        "timezone_aware",
        "timezone_naive",
        "recursion_loop",
        "model_type",
        "none_required",
        "not_none",
        "no_such_attribute",
        "invalid_key",
        "enum",
    }
)


def _safe_validation_details(
    error: ValidationError,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Project Pydantic error details onto allowlisted, bounded trace fields."""

    locs: list[str] = []
    kinds: list[str] = []
    total_chars = 0
    for item in error.errors()[:_MAX_ERROR_ITEMS]:
        error_type = item.get("type")
        if not isinstance(error_type, str) or error_type not in _SAFE_ERROR_KINDS:
            continue
        kinds.append(error_type)
        unknown_seen = False
        for segment in item.get("loc", ())[:_MAX_LOC_SEGMENTS]:
            if not isinstance(segment, str):
                continue
            if segment not in _SAFE_LOC_FIELDS or len(segment) > _MAX_LOC_SEGMENT_CHARS:
                unknown_seen = True
                continue
            if segment not in locs and total_chars + len(segment) <= _MAX_LOC_TOTAL_CHARS:
                locs.append(segment)
                total_chars += len(segment)
        if unknown_seen and _UNKNOWN_FIELD_MARKER not in locs:
            locs.append(_UNKNOWN_FIELD_MARKER)
    return tuple(locs), tuple(dict.fromkeys(kinds))


def _output_call_details(
    response: ModelResponse,
    parameters: ModelRequestParameters,
    strategy: DiagnosticStrategy,
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """Locally validate the structured output call for safe error attribution.

    PydanticAI performs the same validation afterwards; parsing here gives the
    current round's field-level error before the SDK raises a terminal
    exception that carries no details. Raw values never leave this function.
    """

    try:
        output_names = {tool.name for tool in parameters.output_tools}
        if not output_names:
            return None
        payload: object | None = None
        for part in response.parts:
            if isinstance(part, ToolCallPart) and part.tool_name in output_names:
                payload = part.args_as_dict()
                break
        if not isinstance(payload, dict):
            return None
        model_type = KernelDecision if _is_kernel_strategy(strategy) else _StaticDecision
        model_type.model_validate(payload)
    except ValidationError as error:
        return _safe_validation_details(error)
    except Exception:
        # A malformed or SDK-specific payload must not break the diagnosis;
        # coarse classification still applies when details are unavailable.
        return None
    return None


def _response_shape(
    response: ModelResponse,
    parameters: ModelRequestParameters,
) -> tuple[tuple[ModelCallShape, ...], str]:
    """Structural shape of a model response: call order, names, argument parse.

    Only names, parse outcomes and the closing part kind are derived; argument
    values and free text are never captured.
    """

    output_names = {tool.name for tool in parameters.output_tools}
    function_names = {tool.name for tool in parameters.function_tools}
    shapes: list[ModelCallShape] = []
    last_kind = "EMPTY"
    for part in response.parts:
        if isinstance(part, ToolCallPart):
            is_output = part.tool_name in output_names
            raw = part.args
            if isinstance(raw, dict):
                parse: Literal["OBJECT", "INVALID_JSON", "EMPTY"] = (
                    "OBJECT" if raw else "EMPTY"
                )
            elif isinstance(raw, str) and raw.strip():
                try:
                    decoded = json.loads(raw)
                except (ValueError, TypeError):
                    parse = "INVALID_JSON"
                else:
                    parse = "OBJECT" if isinstance(decoded, dict) and decoded else "EMPTY"
            else:
                parse = "EMPTY"
            shapes.append(
                ModelCallShape(
                    tool_name=part.tool_name,
                    is_output_call=is_output,
                    arguments_parse=parse,
                )
            )
            if is_output:
                last_kind = "OUTPUT_CALL"
            elif part.tool_name in function_names:
                last_kind = "BUSINESS_CALL"
        elif isinstance(part, TextPart) and part.content.strip():
            if last_kind == "EMPTY":
                last_kind = "TEXT_ONLY"
    return tuple(shapes), last_kind


_ERROR_TYPE_LABELS = {
    "UnexpectedModelBehavior": "UNEXPECTED_MODEL_BEHAVIOR",
    "ToolRetryError": "TOOL_RETRY_ERROR",
    "ModelAPIError": "MODEL_API_ERROR",
    "IncompleteToolCall": "INCOMPLETE_TOOL_CALL",
    "ValueError": "VALUE_ERROR",
    "TypeError": "TYPE_ERROR",
}

_RETRY_TARGET_OUTPUT = "<output>"


def _error_type_label(error: BaseException) -> str:
    """Safe exception label: a fixed name from an allowlist, never the message."""

    for klass in type(error).__mro__:
        label = _ERROR_TYPE_LABELS.get(klass.__name__)
        if label is not None:
            return label
    return "OTHER"


def _retry_prompt_targets(
    messages: Sequence[ModelMessage],
    parameters: ModelRequestParameters,
) -> tuple[str, ...]:
    """Distinct retry-prompt targets seen in the conversation so far.

    A retry prompt naming a structured-output tool is an output retry; one
    naming a business tool is a rejected business call. Only names are kept, and
    names outside the registered tools are collapsed to a marker, so
    model-supplied text never reaches the trace.
    """

    output_names = {tool.name for tool in parameters.output_tools}
    known = {tool.name for tool in (*parameters.function_tools, *parameters.output_tools)}
    targets: list[str] = []
    for message in messages:
        for part in getattr(message, "parts", ()):
            if not isinstance(part, RetryPromptPart):
                continue
            name = part.tool_name
            if name is None or name in output_names:
                target = _RETRY_TARGET_OUTPUT
            elif name in known:
                target = name
            else:
                target = _UNKNOWN_FIELD_MARKER
            if target not in targets:
                targets.append(target)
    return tuple(targets)


def _record_protocol_failure(state: _RunState, error: BaseException) -> None:
    """Classify a terminal protocol failure and record how it can be attributed.

    ``category`` and ``stage`` keep their existing meaning and selection; the
    added ``error_type``/``error_origin`` fields carry what the terminal
    exception and the recorded retry prompts actually show, so an analyst can
    tell an output-validation failure from a business-argument failure instead
    of inferring the mechanism from the category alone.
    """

    observation = state.last_observation
    error_type = _error_type_label(error)
    origin = _failure_origin(state, observation)
    if isinstance(error, (ModelAPIError, IncompleteToolCall)):
        state.set_protocol_failure(
            category="PROVIDER_PROTOCOL_FAILURE",
            stage="PROVIDER_RESPONSE",
            tool_name=None,
            error_type=error_type,
            origin="PROVIDER",
        )
    elif isinstance(error, (UnexpectedModelBehavior, ToolRetryError, ValueError, TypeError)):
        if observation is not None and observation[1]:
            details = state.current_output_details or ((), ())
            state.set_protocol_failure(
                category="OUTPUT_SCHEMA_REJECTED",
                stage="OUTPUT_SCHEMA_VALIDATION",
                tool_name=observation[1][-1],
                error_loc=details[0],
                error_kind=details[1],
                error_type=error_type,
                origin=origin,
            )
        elif observation is not None and observation[0]:
            state.set_protocol_failure(
                category="TOOL_ARGUMENT_REJECTED",
                stage="TOOL_ARGUMENT_VALIDATION",
                tool_name=observation[0][-1],
                error_type=error_type,
                origin=origin,
            )
        else:
            state.set_protocol_failure(
                category="OUTPUT_SCHEMA_REJECTED",
                stage="OUTPUT_SCHEMA_VALIDATION",
                tool_name=None,
                error_type=error_type,
                origin=origin,
            )
    else:
        state.set_protocol_failure(
            category="PROVIDER_PROTOCOL_FAILURE",
            stage="PROVIDER_RESPONSE",
            tool_name=None,
            error_type=error_type,
            origin="PROVIDER",
        )


def _failure_origin(
    state: _RunState,
    observation: tuple[tuple[str, ...], tuple[str, ...], bool] | None,
) -> str:
    """Attribute a terminal protocol failure from the current request's evidence.

    Only the current request may name the origin. Historical retry targets are
    observation background: they are never used to promote a currently
    unattributable failure into a definite class, because a surface retried
    earlier says nothing about which surface ended this request.
    """

    return _closing_shape_origin(state, observation)


def _business_retry_targets(state: _RunState) -> tuple[str, ...]:
    """Retry targets that name a registered business tool.

    A name outside the registered functions (collapsed to the unknown marker by
    `_retry_prompt_targets`) may be a wrong tool choice, a hallucinated name or
    provider noise; it must not be reported as an argument failure.
    """

    return tuple(
        target
        for target in state.retry_prompt_targets
        if target != _RETRY_TARGET_OUTPUT
        and target != _UNKNOWN_FIELD_MARKER
        and target in state.registered_business_tools
    )


def _closing_shape_origin(
    state: _RunState,
    observation: tuple[tuple[str, ...], tuple[str, ...], bool] | None,
) -> str:
    """Attribute a failure only when the current response proves one source.

    A definite class is returned only when this request's closing response leaves
    exactly one candidate. A response that carries both a structured-output call
    and a registered business call cannot be attributed here: a business call
    whose JSON decoded may still violate the tool's type contract, so the shape
    alone cannot show that it passed validation, and a kernel verdict proves only
    that a decision was refused, not that it was the terminating failure. An
    earlier acceptance is run state, not evidence about the current response, so
    it never excludes a current output failure either.
    """

    if observation is None:
        return "UNKNOWN"
    has_output = bool(observation[1])
    has_business = bool(observation[0])
    if has_output and has_business:
        return "UNKNOWN"
    if has_output:
        if state.kernel_rejection_attempt == state.request_attempt:
            return "KERNEL_DECISION"
        if state.current_output_details is not None:
            return "OUTPUT_VALIDATION"
        return "UNKNOWN"
    if has_business:
        return "BUSINESS_TOOL_ARGUMENTS"
    return "UNKNOWN"


_MAX_SUMMARY_ITEMS = 16
_MAX_CLAIM_EVIDENCE_REFS = 32


def _rejected_decision_summary(
    decision: KernelDecision,
    kernel: DiagnosticKernel,
    model_request_index: int,
) -> RejectedDecisionSummary:
    """Project a rejected kernel decision onto bounded, known-only fields.

    Only registered hypothesis IDs, ontology root codes, public node/relation
    identifiers from accepted evidence or the incident brief, and accepted
    evidence references are kept; unknown content is reduced to counts.

    Each array keeps at most `_MAX_SUMMARY_ITEMS` items and each claim keeps at
    most `_MAX_CLAIM_EVIDENCE_REFS` references. Items dropped by a cap are not
    inspected at all, so they are counted as truncated and never as unknown.
    The identities differ by treatment of unknown content:

    - assessments drop unregistered hypotheses, so
      `total = len(kept) + unknown_hypothesis_count + truncated`;
    - claims and unresolved_evidence redact unknown values in place, so
      `total = len(kept) + truncated` and the `unknown_*` counts describe
      redacted items inside `kept`;
    - one claim's references: `total = kept + unknown_evidence_count + truncated`.
    """

    records = kernel.evidence_records
    accepted_ids = {record.evidence_id for record in records}
    known_relations: set[str] = {
        relation
        for tool_relations in kernel.provable_relations_by_tool().values()
        for relation in tool_relations
    }
    known_nodes: set[str] = set(kernel.incident_subjects)
    for record in records:
        content = record.content
        node_id = getattr(content, "node_id", None)
        if isinstance(node_id, str):
            known_nodes.add(node_id)
        for node in getattr(content, "related_nodes", ()):
            known_nodes.add(node.node_id)
        relation = getattr(content, "relation_name", None)
        if isinstance(relation, str):
            known_relations.add(relation)
        if isinstance(content, DbtRunResultsFact):
            known_nodes.update(content.failed_nodes)
            known_nodes.update(content.skipped_nodes)
    known_codes = set(kernel.allowed_root_cause_codes)
    registered = {item.hypothesis_id for item in kernel.hypotheses}

    assessments: list[RejectedAssessmentSummary] = []
    unknown_hypothesis_count = 0
    truncated_assessment_count = _overflow(decision.assessments, _MAX_SUMMARY_ITEMS)
    for item in decision.assessments[:_MAX_SUMMARY_ITEMS]:
        if item.hypothesis_id not in registered:
            unknown_hypothesis_count += 1
            continue
        assessments.append(
            RejectedAssessmentSummary(
                hypothesis_id=item.hypothesis_id,
                verdict=item.verdict.value,
            )
        )

    claims: list[RejectedClaimSummary] = []
    unknown_claim_count = 0
    unknown_evidence_count = 0
    truncated_evidence_count = 0
    truncated_claim_count = _overflow(decision.claims, _MAX_SUMMARY_ITEMS)
    for item in decision.claims[:_MAX_SUMMARY_ITEMS]:
        known_value: str | None = None
        if item.kind is ClaimKind.ROOT_CAUSE:
            if item.value in known_codes:
                known_value = item.value
            else:
                unknown_claim_count += 1
        elif item.kind is ClaimKind.AFFECTED_ASSET:
            if item.value in known_nodes:
                known_value = item.value
            else:
                unknown_claim_count += 1
        else:
            known_value = None
        relation_name = (
            item.relation_name if item.relation_name in known_relations else None
        )
        kept_evidence: list[str] = []
        for evidence_id in item.evidence_ids[:_MAX_CLAIM_EVIDENCE_REFS]:
            if evidence_id in accepted_ids:
                kept_evidence.append(evidence_id)
            else:
                unknown_evidence_count += 1
        truncated_evidence_count += _overflow(item.evidence_ids, _MAX_CLAIM_EVIDENCE_REFS)
        claims.append(
            RejectedClaimSummary(
                kind=item.kind.value,
                known_value=known_value,
                relation_name=relation_name,
                evidence_ids=tuple(kept_evidence),
            )
        )

    unresolved: list[RejectedUnresolvedSummary] = []
    unknown_subject_count = 0
    public_subjects = known_nodes | known_relations
    truncated_unresolved_count = _overflow(decision.unresolved_evidence, _MAX_SUMMARY_ITEMS)
    for item in decision.unresolved_evidence[:_MAX_SUMMARY_ITEMS]:
        subject = item.subject if item.subject in public_subjects else None
        if subject is None:
            unknown_subject_count += 1
        unresolved.append(
            RejectedUnresolvedSummary(
                evidence_kind=item.evidence_kind,
                reason_code=item.reason_code,
                subject=subject,
            )
        )

    return RejectedDecisionSummary(
        model_request_index=model_request_index,
        status=decision.status,
        selected_hypothesis_id=(
            decision.selected_hypothesis_id
            if decision.selected_hypothesis_id in registered
            else None
        ),
        assessments=tuple(assessments),
        claims=tuple(claims),
        unresolved_evidence=tuple(unresolved),
        unknown_hypothesis_count=unknown_hypothesis_count,
        unknown_claim_count=unknown_claim_count,
        unknown_evidence_count=unknown_evidence_count,
        unknown_subject_count=unknown_subject_count,
        truncated_assessment_count=truncated_assessment_count,
        truncated_claim_count=truncated_claim_count,
        truncated_unresolved_count=truncated_unresolved_count,
        truncated_evidence_count=truncated_evidence_count,
        total_assessments=len(decision.assessments),
        total_claims=len(decision.claims),
        total_unresolved=len(decision.unresolved_evidence),
        truncated=any(
            (
                truncated_assessment_count,
                truncated_claim_count,
                truncated_unresolved_count,
                truncated_evidence_count,
            )
        ),
    )


def _overflow(items: Sequence[object], limit: int) -> int:
    """Number of items a bounded projection dropped from the end of `items`."""

    return max(0, len(items) - limit)


def _kernel_state_summary(kernel: DiagnosticKernel, snapshot: InvestigationState) -> str:
    """Project the model-visible investigation ledger for the current request."""

    payload = {
        "hypotheses": [
            {"hypothesis_id": item.hypothesis_id, "root_cause_code": item.root_cause_code}
            for item in snapshot.hypotheses
        ],
        "gaps": [
            {
                "gap_id": gap.gap_id,
                "gap_kind": gap.gap_kind.value,
                "tool_name": gap.tool_name,
                "subject": gap.subject,
                "status": gap.status.value,
                "error_code": gap.error_code,
            }
            for gap in snapshot.gaps
        ],
        "evidence": [
            {
                "evidence_id": record.evidence_id,
                "evidence_type": record.evidence_type.value,
                "subject": record.subject,
            }
            for record in kernel.evidence_records
        ],
        "provable_relations": {
            tool_name: list(relations)
            for tool_name, relations in kernel.provable_relations_by_tool().items()
        },
        "provable_lineage_nodes": list(kernel.provable_lineage_nodes()),
        "model_requests_remaining": snapshot.model_requests_remaining,
        "tool_calls_remaining": snapshot.tool_calls_remaining,
    }
    return (
        "CURRENT INVESTIGATION LEDGER (controller-maintained; authoritative):\n"
        + _canonical_json(payload)
        + "\nGap identifiers and kinds are allocated by the controller; reference the gaps "
        "above by their gap_id; the evidence list is the exact accepted inventory and "
        "gap error_code values are the real rejection receipts; query only relations "
        "listed under provable_relations for that tool and only node identifiers listed "
        "under provable_lineage_nodes for get_dbt_lineage; a relation name or "
        "schema-qualified name is not a node identifier and rejected node arguments must "
        "never be retried; the lists are exact and complete and relations returned by "
        "evidence do not extend them. One boundary probe per blocked-relevant relation "
        "is allowed to record its permission receipt; it counts against the budget and "
        "returns no data. Budget remaining requests conservatively."
    )


def _kernel_ledger_instructions(ctx: RunContext[_RunState]) -> str | None:
    """The one authoritative investigation ledger for the current request.

    The ledger is resolved when the request is prepared, so every model call
    sees the current hypotheses, gaps, accepted evidence, permissions and
    budget exactly once instead of once per registered tool.
    """

    kernel = ctx.deps.kernel
    if kernel is None:
        return None
    snapshot = kernel.snapshot(model_requests_used=ctx.deps.usage.requests)
    return _kernel_state_summary(kernel, snapshot)


def _kernel_retry_message(
    code: str,
    *,
    provable_relations: tuple[str, ...] | None = None,
    provable_lineage_nodes: tuple[str, ...] | None = None,
) -> str:
    """Model-facing feedback for one rejection code.

    Every code a kernel or policy object can raise carries its own correction
    here, because the generic fallback tells the model that something was wrong
    but not what to change. Only controller-internal state conflicts, which a
    different next response cannot fix, are left out; the guard in
    ``tests/unit/test_kernel_rejection_feedback.py`` fails when a new code
    appears without feedback.
    """

    messages = {
        # Call preparation.
        "ARGUMENTS_INVALID": (
            "Send exactly the argument keys the selected tool declares, as strings; "
            "extra keys, missing keys and non-string values are rejected."
        ),
        "GAP_TOOL_MISMATCH": (
            "Choose the tool and direction the declared gap kind requires: each gap "
            "kind maps to exactly one tool."
        ),
        "KERNEL_INTENT_MISSING": "Every Kernel business call must carry its binding arguments.",
        "RUN_CONTEXT_MISMATCH": (
            "Use the run identifier of the run under investigation; an argument or "
            "decision naming another run is rejected."
        ),
        "NODE_ARGUMENT_NOT_PROVEN": "Use a canonical node identifier from the run catalog.",
        "TOOL_CALL_LIMIT": (
            "The tool-call budget is exhausted, so no further business call is accepted "
            "and no new hypothesis can be registered; finalize with the evidence you "
            "already have."
        ),
        "HYPOTHESIS_REFERENCE_UNKNOWN": "Reference only registered hypothesis IDs.",
        "DUPLICATE_GAP_ID": "Gap identifiers are controller-allocated; retry the call as-is.",
        "DUPLICATE_HYPOTHESIS": (
            "Re-sending a registered hypothesis with its original root cause code is "
            "allowed; an existing hypothesis ID cannot be redefined with a different "
            "root cause code."
        ),
        "DUPLICATE_TOOL_CALL": (
            "This exact query was already attempted, so a repeat registers nothing, "
            "adds no hypothesis and consumes no new evidence; query a different "
            "subject or finalize."
        ),
        "ONTOLOGY_CODE_UNKNOWN": (
            "Use a root cause code from the ontology the ledger lists; an unknown code "
            "registers nothing and rejects the whole call before any declaration in it "
            "takes effect."
        ),
        # Evidence acceptance.
        "EVIDENCE_EMPTY": (
            "The tool returned no record, so the gap cannot close; query a subject that "
            "yields a typed record."
        ),
        "EVIDENCE_RECORD_INVALID": (
            "The tool returned something that is not an evidence record; re-run the "
            "query for the same subject."
        ),
        "EVIDENCE_TYPE_MISMATCH": (
            "The returned record type does not match the declared gap kind; query a "
            "tool whose record type the gap kind expects."
        ),
        "EVIDENCE_SUBJECT_MISMATCH": (
            "The returned record belongs to a subject other than the one the call "
            "asked for; query the subject you declare."
        ),
        "DUPLICATE_EVIDENCE": (
            "This evidence was already accepted by another gap, so a repeated record "
            "cannot close a second gap; query a different subject."
        ),
        # Decision validation: confirmed path first, then the abstention path.
        "ALTERNATIVE_HYPOTHESIS_REQUIRED": (
            "A terminal decision needs at least two registered hypotheses. Hypotheses "
            "are registered only with a business call, so a competing cause that was "
            "never declared cannot be added once the tool budget is spent."
        ),
        "EVIDENCE_GAP_OPEN": (
            "Every opened evidence gap must close with a successful typed tool result "
            "before CONFIRMED or NO_INCIDENT; finalize INSUFFICIENT_EVIDENCE when a "
            "gap cannot close."
        ),
        "HYPOTHESIS_ASSESSMENT_INCOMPLETE": (
            "Assess every registered hypothesis exactly once: the assessment IDs must "
            "be exactly the registered hypothesis IDs, with no additions."
        ),
        "SELECTED_HYPOTHESIS_NOT_SUPPORTED": (
            "The selected hypothesis must carry a SUPPORTED verdict."
        ),
        "REFUTED_HYPOTHESIS_REQUIRED": (
            "CONFIRMED needs at least one other registered hypothesis assessed "
            "REFUTED, not only supported ones."
        ),
        "CLAIMS_INCOMPLETE": (
            "CONFIRMED needs exactly one ROOT_CAUSE claim and at least one "
            "AFFECTED_ASSET claim."
        ),
        "ROOT_CLAIM_MISMATCH": (
            "The ROOT_CAUSE claim must carry the root cause code of the selected "
            "hypothesis exactly; copy that registered code instead of composing a new "
            "value."
        ),
        "ROOT_CLAIM_EVIDENCE_INCOMPATIBLE": (
            "The ROOT_CAUSE claim is usually missing a category of record its root cause "
            "requires, such as the failed node's error or an upstream relation fact; "
            "records cited by another claim do not count for it. If those records are "
            "already in the accepted-evidence list, cite them in the ROOT_CAUSE claim; "
            "otherwise investigate the missing category before retrying. If the public "
            "evidence is genuinely insufficient, re-investigate or finalize "
            "INSUFFICIENT_EVIDENCE under the existing contract."
        ),
        "ASSET_CLAIM_EVIDENCE_INCOMPATIBLE": (
            "Each affected-asset value must be supported by the records that same claim "
            "cites: the node's own error, a downstream lineage listing it, or a "
            "distance-1 upstream model of the failed node. A profile cannot support an "
            "asset value, so remove any asset claim whose value no cited record names."
        ),
        "CLAIM_EVIDENCE_UNBOUND": (
            "Cite only evidence IDs from the accepted-evidence list that belong to a "
            "closed gap; an unknown or still-open ID rejects the decision."
        ),
        "DECISION_SCOPE_MISMATCH": (
            "The decision run_id must be the run under investigation."
        ),
        "INSUFFICIENCY_GAP_REQUIRED": (
            "INSUFFICIENT_EVIDENCE needs at least one open or blocked gap or a matching "
            "unresolved-evidence declaration; with every gap closed, declare the missing "
            "category or confirm the diagnosis instead."
        ),
        "UNRESOLVED_EVIDENCE_UNBOUND": (
            "Re-check each declared gap: schema, data-profile and history declarations "
            "must bind a blocked gap already recorded for the same subject and tool; "
            "watermark, payment-event-identity and transformation-definition "
            "declarations need a relevant public subject and a fact that is not "
            "observable in this run. Fix the invalid items, then re-check the "
            "remaining independent and justified gaps instead of deleting them all."
        ),
        # Health path.
        "HEALTH_RUN_NOT_PROVEN": (
            "NO_INCIDENT needs an accepted run-results record showing a successful run "
            "with no failed and no skipped nodes."
        ),
        "HEALTH_CLAIM_REQUIRED": (
            "A NO_INCIDENT decision carries only HEALTH_STATE claims, and every claim "
            "it carries must be one."
        ),
        "HEALTH_EVIDENCE_INCOMPATIBLE": (
            "Each health claim must cite both a data profile and a history record for "
            "the relation it declares."
        ),
        "HEALTH_HISTORY_NOT_DECLARED": (
            "The cited history record must contain the series the claim names in "
            "history_name."
        ),
        "HEALTH_POINT_NOT_ALERT_TARGET": (
            "Name the alert target as relation/series/bucket; a point the run does not "
            "publish as an alert target is rejected."
        ),
        "HEALTH_POINT_MISMATCH": (
            "The declared current_value must equal the value of the cited history point "
            "with that bucket."
        ),
        "HEALTH_RELATION_MISMATCH": (
            "The cited profile and history must belong to the same relation the claim "
            "declares."
        ),
        "HEALTH_WATERMARK_NOT_PROVEN": (
            "The cited series must carry the order_date watermark column and a "
            "watermark value."
        ),
        "HEALTH_WATERMARK_INVALID": (
            "The watermark, bucket and observation time must be usable event-time "
            "values; an unparseable value is rejected."
        ),
        "HEALTH_SLA_NOT_DECLARED": (
            "A current-partition claim needs the series' declared SLA before it can be "
            "called healthy."
        ),
        "HEALTH_SLA_NOT_SATISFIED": (
            "In the current partition the logical observation time must fall inside the "
            "declared SLA window; otherwise the alert is not healthy and NO_INCIDENT "
            "does not hold."
        ),
        "HEALTH_POINT_AFTER_WATERMARK": (
            "A bucket later than the watermark is not a completed period and cannot be "
            "cited as the current point."
        ),
        "HEALTH_RANGE_NOT_PROVEN": (
            "A non-current claim needs at least four prior same-period points and a "
            "current value inside their inclusive min/max range."
        ),
        # Relations and lineage keep their public whitelist appended below.
        "RELATION_NOT_ALLOWED": "This relation is not queryable in this run.",
    }
    message = messages.get(code, "Correct the structured investigation decision.")
    if code == "RELATION_NOT_ALLOWED" and provable_relations:
        listed = ", ".join(provable_relations)
        message += f" Currently provable for this tool: {listed}."
    if code == "RELATION_NOT_ALLOWED" and not provable_relations:
        message += " No relation is provable for this tool."
    if code == "RELATION_NOT_ALLOWED":
        message += (
            " Bind your unresolved-evidence declaration to this blocked gap; do not "
            "retry this relation or a variant of it."
        )
    if code == "NODE_ARGUMENT_NOT_PROVEN" and provable_lineage_nodes:
        listed = ", ".join(provable_lineage_nodes)
        message += f" Lineage nodes callable in this run: {listed}."
    return f"{code}: {message}"


def _usage_limit_reason(error: UsageLimitExceeded) -> str:
    if "tool_calls_limit" in str(error):
        return "MODEL_TOOL_CALL_LIMIT"
    return "MODEL_REQUEST_LIMIT"


def _tool_schema_payload(agent: Agent[Any, Any]) -> list[dict[str, object]]:
    tools = agent._function_toolset.tools  # type: ignore[attr-defined]
    return [
        {
            "name": name,
            "parameters": tool.function_schema.json_schema,
        }
        for name, tool in tools.items()
    ]


def _register_evidence_tools(
    agent: Agent[_RunState, Any],
    *,
    kernel_mode: bool = False,
    excluded_tool_names: frozenset[str] = frozenset(),
) -> None:
    enabled_tool_names = set(TOOL_NAMES) - excluded_tool_names

    def register(tool_name: str):
        if tool_name not in enabled_tool_names:
            return lambda function: function
        return agent.tool

    if kernel_mode:
        _register_kernel_evidence_tools(agent, register)
        return

    @register("get_dbt_run_results")
    def get_dbt_run_results(
        ctx: RunContext[_RunState],
        run_id: Annotated[StrictStr, Field(description="The exact verified run identifier.")],
    ) -> tuple[EvidenceRecord, ...]:
        return _execute_evidence(
            ctx,
            "get_dbt_run_results",
            {"run_id": run_id},
            lambda: ctx.deps.tools.get_dbt_run_results(run_id),
        )

    @register("get_dbt_node_error")
    def get_dbt_node_error(
        ctx: RunContext[_RunState],
        run_id: Annotated[StrictStr, Field(description="The exact verified run identifier.")],
        node_id: Annotated[
            StrictStr,
            Field(description="A node identifier returned by run evidence."),
        ],
    ) -> tuple[EvidenceRecord, ...]:
        return _execute_evidence(
            ctx,
            "get_dbt_node_error",
            {"run_id": run_id, "node_id": node_id},
            lambda: ctx.deps.tools.get_dbt_node_error(run_id, node_id),
        )

    @register("get_relation_schema")
    def get_relation_schema(
        ctx: RunContext[_RunState],
        relation_name: Annotated[
            StrictStr,
            Field(
                description=(
                    "An exact relation name from the run's observable relation list "
                    "for this tool."
                )
            ),
        ],
    ) -> tuple[EvidenceRecord, ...]:
        return _execute_evidence(
            ctx,
            "get_relation_schema",
            {"relation_name": relation_name},
            lambda: ctx.deps.tools.get_relation_schema(relation_name),
        )

    @register("get_dbt_lineage")
    def get_dbt_lineage(
        ctx: RunContext[_RunState],
        node_id: Annotated[StrictStr, Field(description="A node identifier returned by evidence.")],
        direction: Literal["upstream", "downstream"],
    ) -> tuple[EvidenceRecord, ...]:
        return _execute_evidence(
            ctx,
            "get_dbt_lineage",
            {"node_id": node_id, "direction": direction},
            lambda: ctx.deps.tools.get_dbt_lineage(node_id, direction),
        )

    @register("get_relation_data_profile")
    def get_relation_data_profile(
        ctx: RunContext[_RunState],
        relation_name: Annotated[
            StrictStr,
            Field(
                description=(
                    "An exact relation name from the run's observable relation list "
                    "for this tool."
                )
            ),
        ],
    ) -> tuple[EvidenceRecord, ...]:
        return _execute_evidence(
            ctx,
            "get_relation_data_profile",
            {"relation_name": relation_name},
            lambda: ctx.deps.tools.get_relation_data_profile(relation_name),
        )

    @register("get_relation_history")
    def get_relation_history(
        ctx: RunContext[_RunState],
        relation_name: Annotated[
            StrictStr,
            Field(
                description=(
                    "An exact relation name from the run's observable relation list "
                    "for this tool."
                )
            ),
        ],
    ) -> tuple[EvidenceRecord, ...]:
        return _execute_evidence(
            ctx,
            "get_relation_history",
            {"relation_name": relation_name},
            lambda: ctx.deps.tools.get_relation_history(relation_name),
        )


def _register_kernel_evidence_tools(
    agent: Agent[_RunState, Any],
    register: Callable[[str], Callable[[Any], Any]],
) -> None:
    """Register kernel tools; the controller allocates gap IDs and kinds."""

    @register("get_dbt_run_results")
    def get_dbt_run_results(
        ctx: RunContext[_RunState],
        run_id: Annotated[StrictStr, Field(description="The exact verified run identifier.")],
        kernel_hypothesis_ids: tuple[
            Annotated[StrictStr, Field(pattern=_HYPOTHESIS_ID_PATTERN)], ...
        ] = (),
        kernel_new_hypotheses: tuple[Hypothesis, ...] = (),
    ) -> tuple[EvidenceRecord, ...]:
        return _execute_evidence(
            ctx,
            "get_dbt_run_results",
            {"run_id": run_id},
            lambda: ctx.deps.tools.get_dbt_run_results(run_id),
            kernel_hypothesis_ids=kernel_hypothesis_ids,
            kernel_new_hypotheses=kernel_new_hypotheses,
        )

    @register("get_dbt_node_error")
    def get_dbt_node_error(
        ctx: RunContext[_RunState],
        run_id: Annotated[StrictStr, Field(description="The exact verified run identifier.")],
        node_id: Annotated[
            StrictStr,
            Field(description="A node identifier returned by run evidence."),
        ],
        kernel_hypothesis_ids: tuple[
            Annotated[StrictStr, Field(pattern=_HYPOTHESIS_ID_PATTERN)], ...
        ] = (),
        kernel_new_hypotheses: tuple[Hypothesis, ...] = (),
    ) -> tuple[EvidenceRecord, ...]:
        return _execute_evidence(
            ctx,
            "get_dbt_node_error",
            {"run_id": run_id, "node_id": node_id},
            lambda: ctx.deps.tools.get_dbt_node_error(run_id, node_id),
            kernel_hypothesis_ids=kernel_hypothesis_ids,
            kernel_new_hypotheses=kernel_new_hypotheses,
        )

    @register("get_relation_schema")
    def get_relation_schema(
        ctx: RunContext[_RunState],
        relation_name: Annotated[
            StrictStr,
            Field(
                description=(
                    "An exact relation name from the run's observable relation list "
                    "for this tool."
                )
            ),
        ],
        kernel_hypothesis_ids: tuple[
            Annotated[StrictStr, Field(pattern=_HYPOTHESIS_ID_PATTERN)], ...
        ] = (),
        kernel_new_hypotheses: tuple[Hypothesis, ...] = (),
    ) -> tuple[EvidenceRecord, ...]:
        return _execute_evidence(
            ctx,
            "get_relation_schema",
            {"relation_name": relation_name},
            lambda: ctx.deps.tools.get_relation_schema(relation_name),
            kernel_hypothesis_ids=kernel_hypothesis_ids,
            kernel_new_hypotheses=kernel_new_hypotheses,
        )

    @register("get_dbt_lineage")
    def get_dbt_lineage(
        ctx: RunContext[_RunState],
        node_id: Annotated[StrictStr, Field(description="A node identifier returned by evidence.")],
        direction: Literal["upstream", "downstream"],
        kernel_hypothesis_ids: tuple[
            Annotated[StrictStr, Field(pattern=_HYPOTHESIS_ID_PATTERN)], ...
        ] = (),
        kernel_new_hypotheses: tuple[Hypothesis, ...] = (),
    ) -> tuple[EvidenceRecord, ...]:
        return _execute_evidence(
            ctx,
            "get_dbt_lineage",
            {"node_id": node_id, "direction": direction},
            lambda: ctx.deps.tools.get_dbt_lineage(node_id, direction),
            kernel_hypothesis_ids=kernel_hypothesis_ids,
            kernel_new_hypotheses=kernel_new_hypotheses,
        )

    @register("get_relation_data_profile")
    def get_relation_data_profile(
        ctx: RunContext[_RunState],
        relation_name: Annotated[
            StrictStr,
            Field(
                description=(
                    "An exact relation name from the run's observable relation list "
                    "for this tool."
                )
            ),
        ],
        kernel_hypothesis_ids: tuple[
            Annotated[StrictStr, Field(pattern=_HYPOTHESIS_ID_PATTERN)], ...
        ] = (),
        kernel_new_hypotheses: tuple[Hypothesis, ...] = (),
    ) -> tuple[EvidenceRecord, ...]:
        return _execute_evidence(
            ctx,
            "get_relation_data_profile",
            {"relation_name": relation_name},
            lambda: ctx.deps.tools.get_relation_data_profile(relation_name),
            kernel_hypothesis_ids=kernel_hypothesis_ids,
            kernel_new_hypotheses=kernel_new_hypotheses,
        )

    @register("get_relation_history")
    def get_relation_history(
        ctx: RunContext[_RunState],
        relation_name: Annotated[
            StrictStr,
            Field(
                description=(
                    "An exact relation name from the run's observable relation list "
                    "for this tool."
                )
            ),
        ],
        kernel_hypothesis_ids: tuple[
            Annotated[StrictStr, Field(pattern=_HYPOTHESIS_ID_PATTERN)], ...
        ] = (),
        kernel_new_hypotheses: tuple[Hypothesis, ...] = (),
    ) -> tuple[EvidenceRecord, ...]:
        return _execute_evidence(
            ctx,
            "get_relation_history",
            {"relation_name": relation_name},
            lambda: ctx.deps.tools.get_relation_history(relation_name),
            kernel_hypothesis_ids=kernel_hypothesis_ids,
            kernel_new_hypotheses=kernel_new_hypotheses,
        )


def _build_policy_surface(
    strategy: DiagnosticStrategy,
    *,
    model: Model | None = None,
) -> PolicySurface:
    strategy = DiagnosticStrategy(strategy)
    if strategy not in MODEL_STRATEGIES:
        raise ValueError("strategy is not model-backed")
    output_type: type[BaseModel] = (
        KernelDecision if _is_kernel_strategy(strategy) else _StaticDecision
    )
    schema_agent = Agent(
        model or FunctionModel(lambda _messages, _info: None),
        deps_type=_RunState,
        output_type=output_type,
    )
    enabled = _enabled_tool_names(strategy)
    _register_evidence_tools(
        schema_agent,
        kernel_mode=_is_kernel_strategy(strategy),
        excluded_tool_names=frozenset(set(TOOL_NAMES) - set(enabled)),
    )
    tool_schema_payload = _tool_schema_payload(schema_agent)
    if tuple(item["name"] for item in tool_schema_payload) != enabled:
        raise RuntimeError("evidence tool registration order is invalid")
    strategy_prompt = load_strategy_prompt(strategy)
    strategy_prompt_version = (
        KERNEL_PROMPT_VERSION
        if strategy in KERNEL_STRATEGIES
        else NO_TOOL_PROMPT_VERSION
        if strategy is DiagnosticStrategy.NO_TOOL
        else STATIC_PROMPT_VERSION
    )
    final_diagnosis_schema_sha256 = _sha256_json(Diagnosis.model_json_schema())
    controller_protocol_sha256 = _sha256_json(
        {
            "strategy": strategy.value,
            "protocol_version": CONTROLLER_PROTOCOL_VERSION,
            "tool_schemas": tool_schema_payload,
            "budget": {
                "model_request_limit": MODEL_REQUEST_LIMIT,
                "tool_call_limit": TOOL_CALL_LIMIT,
                "output_retry_limit": OUTPUT_RETRY_LIMIT,
                "timeout_seconds": TIMEOUT_SECONDS,
            },
            "decision_schema": output_type.model_json_schema(),
            "state_schema": (
                InvestigationState.model_json_schema()
                if _is_kernel_strategy(strategy)
                else None
            ),
        }
    )
    policy_identity = PolicyIdentity(
        strategy=strategy,
        base_prompt_version=BASE_PROMPT_VERSION,
        base_prompt_sha256=hashlib.sha256(BASE_PROMPT.encode("utf-8")).hexdigest(),
        strategy_prompt_version=strategy_prompt_version,
        strategy_prompt_sha256=hashlib.sha256(strategy_prompt.encode("utf-8")).hexdigest(),
        controller_protocol_version=CONTROLLER_PROTOCOL_VERSION,
        controller_protocol_sha256=controller_protocol_sha256,
        tool_schema_sha256=_sha256_json(tool_schema_payload),
    )
    return PolicySurface(
        tool_schema_payload=tool_schema_payload,
        strategy_prompt_version=strategy_prompt_version,
        strategy_prompt_sha256=policy_identity.strategy_prompt_sha256,
        controller_protocol_sha256=controller_protocol_sha256,
        final_diagnosis_schema_sha256=final_diagnosis_schema_sha256,
        policy_identity=policy_identity,
    )


@lru_cache(maxsize=len(MODEL_STRATEGIES))
def _cached_policy_surface(strategy: DiagnosticStrategy) -> PolicySurface:
    return _build_policy_surface(strategy)


def policy_surface_for_strategy(
    strategy: DiagnosticStrategy,
    *,
    model: Model | None = None,
) -> PolicySurface:
    strategy = DiagnosticStrategy(strategy)
    return (
        _cached_policy_surface(strategy)
        if model is None
        else _build_policy_surface(strategy, model=model)
    )


def policy_identity_for_strategy(
    strategy: DiagnosticStrategy,
    *,
    model: Model | None = None,
) -> PolicyIdentity:
    return policy_surface_for_strategy(strategy, model=model).policy_identity


def _execute_evidence(
    ctx: RunContext[_RunState],
    tool_name: str,
    arguments: dict[str, str],
    call: Callable[[], tuple[EvidenceRecord, ...]],
    *,
    kernel_hypothesis_ids: tuple[str, ...] | None = None,
    kernel_new_hypotheses: tuple[Hypothesis, ...] = (),
) -> tuple[EvidenceRecord, ...]:
    state = ctx.deps
    started_at = monotonic()
    intent: InvestigationIntent | None = None
    if kernel_hypothesis_ids is not None:
        intent = state.allocate_kernel_intent(
            tool_name,
            arguments,
            kernel_hypothesis_ids,
            kernel_new_hypotheses,
        )
    try:
        prepared = state.adapter.prepare(
            tool_name=tool_name,
            arguments=arguments,
            observation=state.last_response,
            intent=intent,
        )
    except _PolicyError as error:
        fingerprint = error.fingerprint or _fingerprint(state.run_id, tool_name, arguments)
        state.record_tool_trace(
            tool_name=tool_name,
            arguments=arguments,
            fingerprint=fingerprint,
            error_code=_controller_error_code(error.code),
            started_at=started_at,
        )
        provable = None
        lineage_nodes = None
        if error.code == "RELATION_NOT_ALLOWED" and state.kernel is not None:
            provable = state.kernel.provable_relations_by_tool().get(tool_name)
        if (
            error.code == "NODE_ARGUMENT_NOT_PROVEN"
            and tool_name == "get_dbt_lineage"
            and state.kernel is not None
        ):
            lineage_nodes = state.kernel.provable_lineage_nodes()
        raise ToolFailed(
            _kernel_retry_message(
                error.code,
                provable_relations=provable,
                provable_lineage_nodes=lineage_nodes,
            )
        ) from None

    try:
        records = tuple(call())
    except EvidenceToolError as error:
        error_code = _safe_tool_error_code(getattr(error, "code", None))
        state.adapter.reject(prepared, error_code)
        state.record_tool_trace(
            tool_name=tool_name,
            arguments=arguments,
            fingerprint=prepared.fingerprint,
            error_code=error_code,
            started_at=started_at,
        )
        raise ToolFailed(error_code) from None
    except Exception:
        state.adapter.reject(prepared, "EVIDENCE_TOOL_ERROR")
        state.record_tool_trace(
            tool_name=tool_name,
            arguments=arguments,
            fingerprint=prepared.fingerprint,
            error_code="EVIDENCE_TOOL_ERROR",
            started_at=started_at,
        )
        raise ToolFailed("EVIDENCE_TOOL_ERROR") from None

    try:
        accepted = state.adapter.accept(prepared, records)
    except _PolicyError as error:
        state.adapter.reject(prepared, _safe_tool_error_code(error.code))
        state.record_tool_trace(
            tool_name=tool_name,
            arguments=arguments,
            fingerprint=prepared.fingerprint,
            error_code=_controller_error_code(error.code),
            started_at=started_at,
        )
        raise ToolFailed(_kernel_retry_message(error.code)) from None

    state.successful_calls += 1
    state.record_tool_trace(
        tool_name=tool_name,
        arguments=arguments,
        fingerprint=prepared.fingerprint,
        evidence_ids=tuple(record.evidence_id for record in records),
        started_at=started_at,
    )
    return accepted


def _claims_to_diagnosis_claims(state: _RunState) -> tuple[object, ...]:
    if state.kernel is None:
        return ()
    claims: list[object] = []
    for claim in state.kernel.snapshot(model_requests_used=state.usage.requests).claims:
        if claim.kind is ClaimKind.ROOT_CAUSE:
            claims.append(
                RootCauseClaim(
                    kind="ROOT_CAUSE",
                    root_cause_code=claim.value,
                    evidence_ids=claim.evidence_ids,
                )
            )
        elif claim.kind is ClaimKind.AFFECTED_ASSET:
            claims.append(
                AffectedAssetClaim(
                    kind="AFFECTED_ASSET",
                    asset=claim.value,
                    evidence_ids=claim.evidence_ids,
                )
            )
        elif claim.kind is ClaimKind.HEALTH_STATE:
            claims.append(
                HealthStateClaim(
                    kind="HEALTH_STATE",
                    relation_name=claim.relation_name,
                    history_name=claim.history_name,
                    bucket=claim.bucket,
                    current_value=claim.current_value,
                    evidence_ids=claim.evidence_ids,
                )
            )
    return tuple(claims)


def _diagnosis_from_kernel(state: _RunState, outcome: KernelOutcome) -> Diagnosis:
    if state.kernel is None:
        raise RuntimeError("Kernel is not initialized")
    snapshot = state.kernel.snapshot(model_requests_used=state.usage.requests)
    evidence_ids = (
        tuple(record.evidence_id for record in state.kernel.evidence_records)
        if outcome.status is KernelFinalStatus.CONFIRMED
        else outcome.evidence_ids
    )
    return Diagnosis(
        status=DiagnosisStatus(outcome.status.value),
        run_id=snapshot.run_id,
        root_cause_code=outcome.root_cause_code,
        summary=outcome.summary,
        affected_assets=outcome.affected_assets,
        evidence_ids=evidence_ids,
        claims=_claims_to_diagnosis_claims(state),
        unresolved_evidence=outcome.unresolved_evidence,
        recommended_actions=outcome.recommended_actions,
        confidence=outcome.confidence,
    )


class DiagnosisRunner:
    def __init__(
        self,
        *,
        run_id: str,
        settings: DiagnosticSettings,
        project_root: Path,
        model: Model,
        tools: EvidenceTools,
        model_identity: ModelIdentity,
        strategy: DiagnosticStrategy,
        context: ObservableRunContext,
        owned_model_client: AsyncOpenAI | None = None,
    ) -> None:
        self._run_id = run_id
        self._settings = settings
        self._project_root = project_root
        self._model = model
        self._tools = tools
        self._model_identity = model_identity
        self._strategy = strategy
        self._context = context
        self._owned_model_client = owned_model_client
        self._budget = DiagnosisBudget()
        surface = policy_surface_for_strategy(strategy, model=model)
        self._tool_schema_payload = surface.tool_schema_payload
        self._tool_schema_sha256 = surface.policy_identity.tool_schema_sha256
        self._final_diagnosis_schema_sha256 = surface.final_diagnosis_schema_sha256
        self._strategy_prompt = load_strategy_prompt(strategy)
        self._strategy_prompt_version = surface.strategy_prompt_version
        self._policy_identity = surface.policy_identity

    @classmethod
    def for_run(
        cls,
        run_id: str,
        settings: DiagnosticSettings,
        strategy: DiagnosticStrategy = DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        project_root: Path = PROJECT_ROOT,
        *,
        model: Model | None = None,
        tools: EvidenceTools | None = None,
        model_identity: ModelIdentity | None = None,
    ) -> DiagnosisRunner:
        if isinstance(strategy, Path):
            project_root = strategy
            strategy = DiagnosticStrategy.DIAGNOSTIC_KERNEL
        strategy = DiagnosticStrategy(strategy)
        if strategy not in MODEL_STRATEGIES:
            raise ValueError("strategy is not model-backed")
        context = resolve_run_context(run_id, project_root=project_root)
        owned_model_client = None
        if model is None:
            client = AsyncOpenAI(
                base_url=str(settings.model_base_url),
                api_key=settings.model_api_key.get_secret_value(),
                max_retries=0,
            )
            provider = OpenAIProvider(openai_client=client)
            model = OpenAIChatModel(settings.model_name, provider=provider)
            model_identity = ModelIdentity("openai-compatible", settings.model_name)
            owned_model_client = client
        elif model_identity is None:
            raise ValueError("model_identity is required when injecting a model")
        if tools is None:
            tools = EvidenceTools.for_run(run_id, settings, project_root=project_root)
        assert model_identity is not None
        assert tools is not None
        return cls(
            run_id=run_id,
            settings=settings,
            project_root=project_root,
            model=model,
            tools=tools,
            model_identity=model_identity,
            strategy=strategy,
            context=context,
            owned_model_client=owned_model_client,
        )

    @property
    def strategy(self) -> DiagnosticStrategy:
        return self._strategy

    @property
    def model_identity(self) -> ModelIdentity:
        return self._model_identity

    @property
    def budget(self) -> DiagnosisBudget:
        return self._budget

    @property
    def incident_brief(self):
        return self._context.incident_brief

    @property
    def tool_schema_sha256(self) -> str:
        return self._tool_schema_sha256

    @property
    def final_diagnosis_schema_sha256(self) -> str:
        return self._final_diagnosis_schema_sha256

    @property
    def policy_identity(self) -> PolicyIdentity:
        return self._policy_identity

    def _kernel(self, context: ObservableRunContext) -> DiagnosticKernel:
        observable = context.runtime["observable_relations"]
        resolver = getattr(self._tools, "lineage_node_candidates", None)
        lineage_node_candidates = (
            tuple(resolver(context.incident_brief.subjects))
            if callable(resolver)
            else ()
        )
        return DiagnosticKernel.start(
            run_id=self._run_id,
            allowed_root_cause_codes=P1_ROOT_CAUSE_CODES,
            model_request_limit=self._budget.model_request_limit,
            tool_call_limit=self._budget.tool_call_limit,
            observable_schema_relations=tuple(
                relation
                for relation in observable["schema"]
                if self._strategy is not DiagnosticStrategy.KERNEL_NO_SCHEMA
            ),
            observable_profile_relations=tuple(observable["profile"]),
            observable_history_relations=tuple(observable["history"]),
            incident_subjects=context.incident_brief.subjects,
            health_target_subjects=tuple(
                observation.subject
                for observation in context.incident_brief.observations
                if observation.kind == "CURRENT_PERIOD_COUNT"
            ),
            incident_logical_observed_at=context.incident_brief.logical_observed_at,
            incident_observations=tuple(
                (observation.kind, observation.subject, observation.value)
                for observation in context.incident_brief.observations
            ),
            lineage_node_candidates=lineage_node_candidates,
        )

    def _agent(self, state: _RunState) -> Agent[_RunState, Any]:
        output_type: type[BaseModel] = (
            KernelDecision
            if _is_kernel_strategy(self._strategy)
            else _StaticDecision
        )
        agent: Agent[_RunState, Any] = Agent(
            _ModelObservationAdapter(self._model, state),
            deps_type=_RunState,
            output_type=output_type,
            instructions=(
                [_kernel_ledger_instructions]
                if _is_kernel_strategy(self._strategy)
                else None
            ),
            system_prompt=f"{BASE_PROMPT}\n\n{self._strategy_prompt}",
            retries={"tools": 1, "output": self._budget.output_retry_limit},
        )
        enabled = _enabled_tool_names(self._strategy)
        _register_evidence_tools(
            agent,
            kernel_mode=_is_kernel_strategy(self._strategy),
            excluded_tool_names=frozenset(set(TOOL_NAMES) - set(enabled)),
        )

        @agent.output_validator
        def validate_output(ctx: RunContext[_RunState], output: Any) -> Any:
            current = ctx.deps
            # Output-validation retries consumed so far, from the validator's own
            # counter. It is None when a round never reaches this validator, and
            # it is not a total of every SDK retry the request may have made.
            current.output_retry_used = ctx.retry
            current.output_retry_limit = ctx.max_retries
            if _is_kernel_strategy(current.strategy):
                if not isinstance(output, KernelDecision) or current.kernel is None:
                    raise ModelRetry("MODEL_PROTOCOL_ERROR")
                try:
                    outcome = current.kernel.finalize(output)
                except KernelError as error:
                    # KERNEL_FINALIZED means an earlier decision in this same
                    # attempt already succeeded; that is a state conflict, not a
                    # domain verdict, so it must not claim KERNEL_DECISION.
                    if error.code != "KERNEL_FINALIZED":
                        current.kernel_rejection_attempt = current.request_attempt
                    current.set_protocol_failure(
                        category="DECISION_CONTRACT_REJECTED",
                        stage="OUTPUT_VALIDATION",
                        tool_name=None,
                        origin="KERNEL_DECISION",
                    )
                    rejected_decision = None
                    try:
                        rejected_decision = _rejected_decision_summary(
                            output,
                            current.kernel,
                            current.usage.requests,
                        )
                    except Exception:
                        rejected_decision = None
                    current.trace.append(
                        EvidenceGateTraceEvent(
                            event_type="EVIDENCE_GATE",
                            reason_code=error.code,
                            accepted=False,
                            rejected_decision=rejected_decision,
                        )
                    )
                    raise ModelRetry(_kernel_retry_message(error.code)) from None
                current.outcome = outcome
                current.trace.append(
                    EvidenceGateTraceEvent(
                        event_type="EVIDENCE_GATE",
                        reason_code=outcome.status.value,
                        accepted=True,
                    )
                )
                return output

            if not isinstance(output, Diagnosis) or output.run_id != current.run_id:
                current.set_protocol_failure(
                    category="DECISION_CONTRACT_REJECTED",
                    stage="OUTPUT_VALIDATION",
                    tool_name=None,
                    origin="OUTPUT_VALIDATION",
                )
                raise ModelRetry("DIAGNOSIS_RUN_SCOPE_MISMATCH")
            if output.status is DiagnosisStatus.MODEL_ERROR:
                current.set_protocol_failure(
                    category="DECISION_CONTRACT_REJECTED",
                    stage="OUTPUT_VALIDATION",
                    tool_name=None,
                    origin="OUTPUT_VALIDATION",
                )
                raise ModelRetry("MODEL_ERROR_IS_CONTROLLER_GENERATED")
            known_ids = {record.evidence_id for record in current.evidence_records}
            if any(
                evidence_id not in known_ids
                for claim in output.claims
                for evidence_id in claim.evidence_ids
            ) or any(evidence_id not in known_ids for evidence_id in output.evidence_ids):
                current.set_protocol_failure(
                    category="DECISION_CONTRACT_REJECTED",
                    stage="OUTPUT_VALIDATION",
                    tool_name=None,
                    origin="OUTPUT_VALIDATION",
                )
                raise ModelRetry("DIAGNOSIS_EVIDENCE_ID_UNKNOWN")
            current.static_diagnosis = Diagnosis.model_validate(output.model_dump(mode="json"))
            current.trace.append(
                EvidenceGateTraceEvent(
                    event_type="EVIDENCE_GATE",
                    reason_code=output.status.value,
                    accepted=True,
                )
            )
            return output

        return agent

    def _user_prompt(self) -> str:
        payload = {
            "run_id": self._context.run_id,
            "incident_brief": self._context.incident_brief.model_dump(mode="json"),
            "runtime": self._context.runtime,
        }
        return (
            "Investigate the verified run using the public run-bound context below. "
            "The run identifier is the only identity needed for tool calls.\n"
            + _canonical_json(payload)
        )

    def _result(self, state: _RunState) -> DiagnosisRunResult:
        if _is_kernel_strategy(state.strategy):
            if state.outcome is None or state.kernel is None:
                raise RuntimeError("diagnosis outcome is missing")
            diagnosis = _diagnosis_from_kernel(state, state.outcome)
            evidence_records = state.kernel.evidence_records
            kernel_state = state.kernel.snapshot(model_requests_used=state.usage.requests)
            trace = [
                *state.trace,
                KernelStateTraceEvent(event_type="KERNEL_STATE", state=kernel_state),
            ]
        else:
            if state.static_diagnosis is None:
                raise RuntimeError("static diagnosis is missing")
            diagnosis = state.static_diagnosis
            evidence_records = tuple(state.evidence_records)
            kernel_state = None
            trace = list(state.trace)
        trace.append(
            DiagnosisTerminalTraceEvent(
                event_type="DIAGNOSIS_TERMINAL",
                strategy=self._strategy,
                status=diagnosis.status,
                evidence_inventory=tuple(record.evidence_id for record in evidence_records),
            )
        )
        return DiagnosisRunResult(
            strategy=self._strategy,
            policy_identity=self._policy_identity,
            diagnosis=diagnosis,
            evidence_records=evidence_records,
            trace=tuple(trace),
            metrics=DiagnosisMetrics(
                provider=self._model_identity.provider,
                model=self._model_identity.model,
                model_requests=state.usage.requests,
                input_tokens=state.usage.input_tokens or 0,
                output_tokens=state.usage.output_tokens or 0,
                tool_call_attempts=sum(
                    isinstance(event, ToolTraceEvent) for event in trace
                ),
                successful_tool_calls=state.successful_calls,
                elapsed_ms=max(0, int((monotonic() - state.started_at) * 1000)),
            ),
            kernel_state=kernel_state,
        )

    def _model_error_result(self, state: _RunState, reason: str) -> DiagnosisRunResult:
        if reason not in _MODEL_ERROR_REASONS:
            reason = "MODEL_RUNTIME_ERROR"
        if _is_kernel_strategy(state.strategy):
            if state.kernel is None:
                raise RuntimeError("Kernel is not initialized")
            if state.outcome is None:
                state.outcome = state.kernel.terminate_model_error(reason)
                state.trace.append(
                    EvidenceGateTraceEvent(
                        event_type="EVIDENCE_GATE",
                        reason_code=reason,
                        accepted=True,
                    )
                )
        elif state.static_diagnosis is None:
            state.static_diagnosis = Diagnosis(
                status=DiagnosisStatus.MODEL_ERROR,
                run_id=state.run_id,
                summary=reason,
                confidence=0.0,
            )
            state.trace.append(
                EvidenceGateTraceEvent(
                    event_type="EVIDENCE_GATE",
                    reason_code=reason,
                    accepted=True,
                )
            )
        return self._result(state)

    async def diagnose(self) -> DiagnosisRunResult:
        try:
            try:
                return await self._diagnose_once()
            except Exception:
                # _diagnose_once handles everything after run-state construction;
                # construction-time or teardown failures must still fail closed
                # into a safe terminal instead of escaping into the harness.
                return self._safe_terminal_result()
        finally:
            if self._owned_model_client is not None:
                with suppress(Exception):
                    await self._owned_model_client.close()

    def _safe_terminal_result(self) -> DiagnosisRunResult:
        diagnosis = Diagnosis(
            status=DiagnosisStatus.MODEL_ERROR,
            run_id=self._run_id,
            summary="MODEL_RUNTIME_ERROR",
            confidence=0.0,
        )
        trace: tuple[TraceEvent, ...] = ()
        kernel_state: InvestigationState | None = None
        if _is_kernel_strategy(self._strategy):
            kernel_state = InvestigationState(
                schema_version="p1.investigation.v1",
                run_id=self._run_id,
                revision=0,
                allowed_root_cause_codes=P1_ROOT_CAUSE_CODES,
                hypotheses=(),
                gaps=(),
                assessments=(),
                claims=(),
                evidence_inventory=(),
                tool_fingerprints=(),
                model_request_limit=self._budget.model_request_limit,
                model_requests_used=0,
                model_requests_remaining=self._budget.model_request_limit,
                tool_call_limit=self._budget.tool_call_limit,
                tool_calls_used=0,
                tool_calls_remaining=self._budget.tool_call_limit,
                final_status=KernelFinalStatus.MODEL_ERROR,
                gate_reason="MODEL_RUNTIME_ERROR",
                selected_hypothesis_id=None,
            )
            trace = (
                KernelStateTraceEvent(event_type="KERNEL_STATE", state=kernel_state),
            )
        trace = (
            *trace,
            DiagnosisTerminalTraceEvent(
                event_type="DIAGNOSIS_TERMINAL",
                strategy=self._strategy,
                status=DiagnosisStatus.MODEL_ERROR,
                evidence_inventory=(),
            ),
        )
        return DiagnosisRunResult(
            strategy=self._strategy,
            policy_identity=self._policy_identity,
            diagnosis=diagnosis,
            evidence_records=(),
            trace=trace,
            metrics=DiagnosisMetrics(
                provider=self._model_identity.provider,
                model=self._model_identity.model,
                model_requests=0,
                input_tokens=0,
                output_tokens=0,
                tool_call_attempts=0,
                successful_tool_calls=0,
                elapsed_ms=0,
            ),
            kernel_state=kernel_state,
        )

    async def _diagnose_once(self) -> DiagnosisRunResult:
        kernel = (
            self._kernel(self._context)
            if _is_kernel_strategy(self._strategy)
            else None
        )
        state = _RunState(
            run_id=self._run_id,
            strategy=self._strategy,
            tools=self._tools,
            context=self._context,
            adapter=None,  # type: ignore[arg-type]
            kernel=kernel,
        )
        if kernel is not None:
            state.adapter = _KernelPolicyAdapter(state)
        else:
            state.adapter = _StaticPolicyAdapter(
                state,
                tool_call_limit=self._budget.tool_call_limit,
            )
        agent = self._agent(state)
        try:
            with agent.parallel_tool_call_execution_mode("sequential"):
                async with asyncio.timeout(self._budget.timeout_seconds):
                    await agent.run(
                        self._user_prompt(),
                        deps=state,
                        usage=state.usage,
                        usage_limits=UsageLimits(
                            request_limit=self._budget.model_request_limit,
                            tool_calls_limit=self._budget.tool_call_limit,
                        ),
                    )
            if _is_kernel_strategy(self._strategy) and state.outcome is None:
                raise RuntimeError("MODEL_PROTOCOL_ERROR")
            if not _is_kernel_strategy(self._strategy) and state.static_diagnosis is None:
                raise RuntimeError("MODEL_PROTOCOL_ERROR")
            return self._result(state)
        except TimeoutError:
            return self._model_error_result(state, "MODEL_TIMEOUT")
        except UsageLimitExceeded as error:
            return self._model_error_result(state, _usage_limit_reason(error))
        except (
            IncompleteToolCall,
            ModelAPIError,
            ModelRetry,
            ToolFailed,
            ToolRetryError,
            UnexpectedModelBehavior,
            ValueError,
            TypeError,
        ) as error:
            _record_protocol_failure(state, error)
            state.append_protocol_trace()
            return self._model_error_result(state, "MODEL_PROTOCOL_ERROR")
        except Exception as error:
            state.set_protocol_failure(
                category="PROVIDER_PROTOCOL_FAILURE",
                stage="PROVIDER_RESPONSE",
                tool_name=None,
                error_type=_error_type_label(error),
                origin="PROVIDER",
            )
            state.append_protocol_trace()
            return self._model_error_result(state, "MODEL_RUNTIME_ERROR")


__all__ = [
    "BASE_PROMPT",
    "BASE_PROMPT_VERSION",
    "CONTROLLER_PROTOCOL_VERSION",
    "DiagnosisBudget",
    "DiagnosisRunner",
    "KERNEL_PROMPT",
    "KERNEL_PROMPT_VERSION",
    "ModelIdentity",
    "NO_TOOL_PROMPT",
    "NO_TOOL_PROMPT_VERSION",
    "P1_ROOT_CAUSE_CODES",
    "PolicySurface",
    "STATIC_PROMPT",
    "STATIC_PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "SYSTEM_PROMPT_SHA256",
    "SYSTEM_PROMPT_VERSION",
    "TOOL_NAMES",
    "load_base_prompt",
    "load_strategy_prompt",
    "policy_identity_for_strategy",
    "policy_surface_for_strategy",
]
