from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, Field, StrictStr
from pydantic_ai import Agent, ModelRetry, RunContext, RunUsage, UsageLimits
from pydantic_ai.exceptions import (
    IncompleteToolCall,
    ModelAPIError,
    ToolFailed,
    ToolRetryError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.diagnosis import (
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
    ModelProtocolTraceEvent,
    PolicyIdentity,
    RootCauseClaim,
    ToolTraceEvent,
)
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.diagnostic_kernel import (
    ClaimKind,
    DiagnosticKernel,
    InvestigationIntentTransport,
    InvestigationState,
    KernelDecision,
    KernelError,
    KernelOutcome,
    PreparedToolCall,
)
from data_incident_gym.evidence import EvidenceRecord, EvidenceToolError
from data_incident_gym.evidence_tools import EvidenceTools
from data_incident_gym.run_context import ObservableRunContext, resolve_run_context

BASE_PROMPT_VERSION = "p1.base.v1"
KERNEL_PROMPT_VERSION = "p1.kernel.v2"
STATIC_PROMPT_VERSION = "p1.static.v1"
CONTROLLER_PROTOCOL_VERSION = "p1.controller.v1"

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


def load_strategy_prompt(strategy: DiagnosticStrategy) -> str:
    if DiagnosticStrategy(strategy) is DiagnosticStrategy.DIAGNOSTIC_KERNEL:
        return KERNEL_PROMPT
    return STATIC_PROMPT


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
    "KERNEL_INTENT_INVALID",
    "KERNEL_INTENT_MISSING",
    "KERNEL_INTENT_SHAPE_INVALID",
    "NODE_ARGUMENT_NOT_PROVEN",
    "ONTOLOGY_CODE_UNKNOWN",
    "RELATION_ARGUMENT_NOT_PROVEN",
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
    pending_intent: InvestigationIntentTransport | None = None
    pending_intent_error: str | None = None
    protocol_failure: tuple[str, str, str | None] | None = None
    protocol_trace_recorded: bool = False

    def record_model_response(
        self,
        response: ModelResponse,
        parameters: ModelRequestParameters,
    ) -> None:
        self.last_response = response
        self.protocol_failure = None
        self.protocol_trace_recorded = False
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
        business_calls = tuple(
            part.tool_name
            for part in response.parts
            if isinstance(part, ToolCallPart) and part.tool_name in TOOL_NAMES
        )
        self.last_observation = (
            function_calls,
            output_calls,
            any(isinstance(part, TextPart) for part in response.parts),
        )
        self.pending_intent = None
        self.pending_intent_error = None
        if self.strategy is not DiagnosticStrategy.DIAGNOSTIC_KERNEL or not business_calls:
            return
        text_parts = tuple(part for part in response.parts if isinstance(part, TextPart))
        if len(business_calls) != 1 or len(text_parts) != 1:
            self.pending_intent_error = "KERNEL_INTENT_SHAPE_INVALID"
            return
        try:
            payload = json.loads(
                text_parts[0].content,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            self.pending_intent = InvestigationIntentTransport.model_validate(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            self.pending_intent_error = "KERNEL_INTENT_INVALID"

    def set_protocol_failure(
        self,
        *,
        category: str,
        stage: str,
        tool_name: str | None,
    ) -> None:
        self.protocol_failure = (category, stage, tool_name)

    def append_protocol_trace(self) -> None:
        if self.protocol_trace_recorded or self.protocol_failure is None:
            return
        category, stage, tool_name = self.protocol_failure
        self.trace.append(
            ModelProtocolTraceEvent(
                event_type="MODEL_PROTOCOL",
                stage=stage,
                tool_name=tool_name,
                category=category,
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


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


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
    ) -> PreparedEvidenceCall:
        del observation
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
    ) -> PreparedEvidenceCall:
        del observation
        kernel = self._state.kernel
        if kernel is None:
            raise _PolicyError("KERNEL_NOT_INITIALIZED")
        if self._state.pending_intent_error is not None:
            raise _PolicyError(self._state.pending_intent_error)
        intent = self._state.pending_intent
        if intent is None:
            raise _PolicyError("KERNEL_INTENT_MISSING")
        self._state.pending_intent = None
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
        response = await self._model.request(messages, model_settings, model_request_parameters)
        self._state.record_model_response(response, model_request_parameters)
        return response


def _record_protocol_failure(state: _RunState, error: BaseException) -> None:
    observation = state.last_observation
    if isinstance(error, (ModelAPIError, IncompleteToolCall)):
        state.set_protocol_failure(
            category="PROVIDER_PROTOCOL_FAILURE",
            stage="PROVIDER_RESPONSE",
            tool_name=None,
        )
    elif isinstance(error, (UnexpectedModelBehavior, ToolRetryError, ValueError, TypeError)):
        if observation is not None and observation[1]:
            state.set_protocol_failure(
                category="OUTPUT_SCHEMA_REJECTED",
                stage="OUTPUT_SCHEMA_VALIDATION",
                tool_name=observation[1][-1],
            )
        elif observation is not None and observation[0]:
            state.set_protocol_failure(
                category="TOOL_ARGUMENT_REJECTED",
                stage="TOOL_ARGUMENT_VALIDATION",
                tool_name=observation[0][-1],
            )
        else:
            state.set_protocol_failure(
                category="OUTPUT_SCHEMA_REJECTED",
                stage="OUTPUT_SCHEMA_VALIDATION",
                tool_name=None,
            )
    else:
        state.set_protocol_failure(
            category="PROVIDER_PROTOCOL_FAILURE",
            stage="PROVIDER_RESPONSE",
            tool_name=None,
        )


def _kernel_retry_message(code: str) -> str:
    messages = {
        "ARGUMENTS_INVALID": "Send only the business arguments for the selected tool.",
        "GAP_TOOL_MISMATCH": "Choose the business tool matching the declared evidence gap.",
        "KERNEL_INTENT_INVALID": "Return exactly one valid p1.kernel_intent.v1 JSON text part.",
        "KERNEL_INTENT_SHAPE_INVALID": (
            "Pair exactly one Kernel business call with one intent text part."
        ),
        "KERNEL_INTENT_MISSING": "Pair every Kernel business call with one intent JSON text part.",
        "RELATION_ARGUMENT_NOT_PROVEN": (
            "Use an exact observable relation from context or accepted evidence."
        ),
        "NODE_ARGUMENT_NOT_PROVEN": "Use a node identifier returned by accepted evidence.",
        "HYPOTHESIS_REFERENCE_UNKNOWN": "Reference only registered hypothesis IDs.",
        "DUPLICATE_GAP_ID": "Use a fresh gap_id for every business call.",
        "DUPLICATE_TOOL_CALL": "Do not repeat an equivalent successful query.",
        "EVIDENCE_GAP_OPEN": "Close all decisive evidence gaps before confirming.",
    }
    return f"{code}: {messages.get(code, 'Correct the structured investigation decision.')}"


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


def _register_evidence_tools(agent: Agent[_RunState, Any]) -> None:
    @agent.tool
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

    @agent.tool
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

    @agent.tool
    def get_relation_schema(
        ctx: RunContext[_RunState],
        relation_name: Annotated[
            StrictStr,
            Field(description="An exact observable relation name returned by evidence."),
        ],
    ) -> tuple[EvidenceRecord, ...]:
        return _execute_evidence(
            ctx,
            "get_relation_schema",
            {"relation_name": relation_name},
            lambda: ctx.deps.tools.get_relation_schema(relation_name),
        )

    @agent.tool
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

    @agent.tool
    def get_relation_data_profile(
        ctx: RunContext[_RunState],
        relation_name: Annotated[
            StrictStr,
            Field(description="An exact observable relation name returned by evidence."),
        ],
    ) -> tuple[EvidenceRecord, ...]:
        return _execute_evidence(
            ctx,
            "get_relation_data_profile",
            {"relation_name": relation_name},
            lambda: ctx.deps.tools.get_relation_data_profile(relation_name),
        )

    @agent.tool
    def get_relation_history(
        ctx: RunContext[_RunState],
        relation_name: Annotated[
            StrictStr,
            Field(description="An exact observable relation name returned by evidence."),
        ],
    ) -> tuple[EvidenceRecord, ...]:
        return _execute_evidence(
            ctx,
            "get_relation_history",
            {"relation_name": relation_name},
            lambda: ctx.deps.tools.get_relation_history(relation_name),
        )


def _execute_evidence(
    ctx: RunContext[_RunState],
    tool_name: str,
    arguments: dict[str, str],
    call: Callable[[], tuple[EvidenceRecord, ...]],
) -> tuple[EvidenceRecord, ...]:
    state = ctx.deps
    started_at = monotonic()
    try:
        prepared = state.adapter.prepare(
            tool_name=tool_name,
            arguments=arguments,
            observation=state.last_response,
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
        raise ToolFailed(_kernel_retry_message(error.code)) from None

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
    return Diagnosis(
        status=DiagnosisStatus(outcome.status.value),
        run_id=snapshot.run_id,
        root_cause_code=outcome.root_cause_code,
        summary=outcome.summary,
        affected_assets=outcome.affected_assets,
        evidence_ids=outcome.evidence_ids,
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
    ) -> None:
        self._run_id = run_id
        self._settings = settings
        self._project_root = project_root
        self._model = model
        self._tools = tools
        self._model_identity = model_identity
        self._strategy = strategy
        self._context = context
        self._budget = DiagnosisBudget()
        self._tool_schema_payload = self._build_tool_schema_payload()
        self._tool_schema_sha256 = _sha256_json(self._tool_schema_payload)
        self._final_diagnosis_schema_sha256 = _sha256_json(Diagnosis.model_json_schema())
        self._strategy_prompt = load_strategy_prompt(strategy)
        self._strategy_prompt_version = (
            KERNEL_PROMPT_VERSION
            if strategy is DiagnosticStrategy.DIAGNOSTIC_KERNEL
            else STATIC_PROMPT_VERSION
        )
        self._policy_identity = PolicyIdentity(
            strategy=strategy,
            base_prompt_version=BASE_PROMPT_VERSION,
            base_prompt_sha256=hashlib.sha256(BASE_PROMPT.encode("utf-8")).hexdigest(),
            strategy_prompt_version=self._strategy_prompt_version,
            strategy_prompt_sha256=hashlib.sha256(
                self._strategy_prompt.encode("utf-8")
            ).hexdigest(),
            controller_protocol_version=CONTROLLER_PROTOCOL_VERSION,
            controller_protocol_sha256=self._controller_protocol_hash(),
            tool_schema_sha256=self._tool_schema_sha256,
        )

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
        context = resolve_run_context(run_id, project_root=project_root)
        if model is None:
            provider = OpenAIProvider(
                base_url=str(settings.model_base_url),
                api_key=settings.model_api_key.get_secret_value(),
            )
            model = OpenAIChatModel(settings.model_name, provider=provider)
            model_identity = ModelIdentity("openai-compatible", settings.model_name)
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

    def _build_tool_schema_payload(self) -> list[dict[str, object]]:
        output_type: type[BaseModel] = (
            KernelDecision
            if self._strategy is DiagnosticStrategy.DIAGNOSTIC_KERNEL
            else Diagnosis
        )
        schema_agent = Agent(self._model, deps_type=_RunState, output_type=output_type)
        _register_evidence_tools(schema_agent)
        payload = _tool_schema_payload(schema_agent)
        if tuple(item["name"] for item in payload) != TOOL_NAMES:
            raise RuntimeError("evidence tool registration order is invalid")
        return payload

    def _controller_protocol_hash(self) -> str:
        output_type: type[BaseModel] = (
            KernelDecision
            if self._strategy is DiagnosticStrategy.DIAGNOSTIC_KERNEL
            else Diagnosis
        )
        return _sha256_json(
            {
                "strategy": self._strategy.value,
                "protocol_version": CONTROLLER_PROTOCOL_VERSION,
                "tool_schemas": self._tool_schema_payload,
                "budget": {
                    "model_request_limit": self._budget.model_request_limit,
                    "tool_call_limit": self._budget.tool_call_limit,
                    "output_retry_limit": self._budget.output_retry_limit,
                    "timeout_seconds": self._budget.timeout_seconds,
                },
                "decision_schema": output_type.model_json_schema(),
                "state_schema": (
                    InvestigationState.model_json_schema()
                    if self._strategy is DiagnosticStrategy.DIAGNOSTIC_KERNEL
                    else None
                ),
            }
        )

    def _kernel(self, context: ObservableRunContext) -> DiagnosticKernel:
        observable = context.runtime["observable_relations"]
        brief_subjects = set(context.incident_brief.subjects)
        return DiagnosticKernel.start(
            run_id=self._run_id,
            allowed_root_cause_codes=(
                "SOURCE_SCHEMA_COLUMN_RENAMED",
                "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                "TRANSFORMATION_COLUMN_CAST_CHANGED",
            ),
            model_request_limit=self._budget.model_request_limit,
            tool_call_limit=self._budget.tool_call_limit,
            observable_schema_relations=tuple(
                relation for relation in observable["schema"] if relation in brief_subjects
            ),
            observable_profile_relations=tuple(
                relation for relation in observable["profile"] if relation in brief_subjects
            ),
            observable_history_relations=tuple(
                relation for relation in observable["history"] if relation in brief_subjects
            ),
            incident_subjects=context.incident_brief.subjects,
            health_target_subjects=tuple(
                observation.subject
                for observation in context.incident_brief.observations
                if observation.kind == "CURRENT_PERIOD_COUNT"
            ),
        )

    def _agent(self, state: _RunState) -> Agent[_RunState, Any]:
        output_type: type[BaseModel] = (
            KernelDecision
            if self._strategy is DiagnosticStrategy.DIAGNOSTIC_KERNEL
            else Diagnosis
        )
        agent: Agent[_RunState, Any] = Agent(
            _ModelObservationAdapter(self._model, state),
            deps_type=_RunState,
            output_type=output_type,
            system_prompt=f"{BASE_PROMPT}\n\n{self._strategy_prompt}",
            retries={"tools": 1, "output": self._budget.output_retry_limit},
        )
        _register_evidence_tools(agent)

        @agent.output_validator
        def validate_output(ctx: RunContext[_RunState], output: Any) -> Any:
            current = ctx.deps
            if current.strategy is DiagnosticStrategy.DIAGNOSTIC_KERNEL:
                if not isinstance(output, KernelDecision) or current.kernel is None:
                    raise ModelRetry("MODEL_PROTOCOL_ERROR")
                try:
                    outcome = current.kernel.finalize(output)
                except KernelError as error:
                    current.set_protocol_failure(
                        category="DECISION_CONTRACT_REJECTED",
                        stage="OUTPUT_VALIDATION",
                        tool_name=None,
                    )
                    current.trace.append(
                        EvidenceGateTraceEvent(
                            event_type="EVIDENCE_GATE",
                            reason_code=error.code,
                            accepted=False,
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
                )
                raise ModelRetry("DIAGNOSIS_RUN_SCOPE_MISMATCH")
            if output.status is DiagnosisStatus.MODEL_ERROR:
                current.set_protocol_failure(
                    category="DECISION_CONTRACT_REJECTED",
                    stage="OUTPUT_VALIDATION",
                    tool_name=None,
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
                )
                raise ModelRetry("DIAGNOSIS_EVIDENCE_ID_UNKNOWN")
            current.static_diagnosis = output
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
        if state.strategy is DiagnosticStrategy.DIAGNOSTIC_KERNEL:
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
        if state.strategy is DiagnosticStrategy.DIAGNOSTIC_KERNEL:
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
        kernel = (
            self._kernel(self._context)
            if self._strategy is DiagnosticStrategy.DIAGNOSTIC_KERNEL
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
            if self._strategy is DiagnosticStrategy.DIAGNOSTIC_KERNEL and state.outcome is None:
                raise RuntimeError("MODEL_PROTOCOL_ERROR")
            if self._strategy is DiagnosticStrategy.STATIC_SKILL and state.static_diagnosis is None:
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
        except Exception:
            state.set_protocol_failure(
                category="PROVIDER_PROTOCOL_FAILURE",
                stage="PROVIDER_RESPONSE",
                tool_name=None,
            )
            state.append_protocol_trace()
            return self._model_error_result(state, "MODEL_RUNTIME_ERROR")


__all__ = [
    "BASE_PROMPT",
    "BASE_PROMPT_VERSION",
    "CONTROLLER_PROTOCOL_VERSION",
    "DiagnosisBudget",
    "DiagnosisRunner",
    "InvestigationIntentTransport",
    "KERNEL_PROMPT",
    "KERNEL_PROMPT_VERSION",
    "ModelIdentity",
    "STATIC_PROMPT",
    "STATIC_PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "SYSTEM_PROMPT_SHA256",
    "SYSTEM_PROMPT_VERSION",
    "TOOL_NAMES",
    "load_base_prompt",
    "load_strategy_prompt",
]
