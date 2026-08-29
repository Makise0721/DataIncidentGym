from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator
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
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    EvidenceGateTraceEvent,
    ModelProtocolTraceEvent,
    ToolTraceEvent,
)
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.diagnostic_kernel import (
    DiagnosticKernel,
    EvidenceGapKind,
    EvidenceGapStatus,
    Hypothesis,
    InvestigationIntent,
    KernelDecision,
    KernelError,
    KernelOutcome,
    KernelStateTraceEvent,
)
from data_incident_gym.evidence import EvidenceRecord, EvidenceToolError
from data_incident_gym.evidence_tools import EvidenceTools
from data_incident_gym.run_context import resolve_run_context

SYSTEM_PROMPT = """
Diagnose one verified data incident using only the four registered read-only evidence tools.
Every tool call must provide the flat InvestigationIntentTransport fields as top-level
arguments: gap_id, gap_kind, hypothesis_ids, new_hypothesis_ids, and
new_hypothesis_root_cause_codes. Use JSON lists for the three list fields; the two
new-hypothesis lists must be parallel and in the same order. The controller converts these
transport fields into its strict InvestigationIntent.
Tool arguments must come from the verified run context or prior structured evidence.

Maintain at least two candidate hypotheses before returning CONFIRMED. Use only this
versioned ontology:
- SOURCE_SCHEMA_COLUMN_RENAMED: a source column was renamed while a consumer still uses
  the former name.
- SOURCE_SCHEMA_COLUMN_TYPE_CHANGED: a source column kept its name but changed to an
  incompatible data type for a consumer.

Use gaps to locate the failure, inspect its error, discover the source relation,
discriminate competing schema hypotheses, and map downstream impact. Begin with
get_dbt_run_results. Before a successful get_dbt_node_error return, keep both
new_hypothesis_ids and new_hypothesis_root_cause_codes set to [] and keep hypothesis_ids set
to []. Only get_relation_schema may register hypotheses, and it may register the two ontology
hypotheses only once, with the two parallel lists. Use the evidence-driven order
get_dbt_run_results, get_dbt_node_error, upstream get_dbt_lineage, get_relation_schema,
then downstream get_dbt_lineage when those gaps are needed.

Match gap_kind to its tool: LOCATE_FAILURE to get_dbt_run_results, EXPLAIN_FAILURE to
get_dbt_node_error, DISCOVER_SOURCE_RELATION to upstream get_dbt_lineage,
DISCRIMINATE_SCHEMA to get_relation_schema, and MAP_IMPACT to downstream get_dbt_lineage.
Use a fresh gap_id for every tool call. If a controller rejects a call before evidence is
returned, correct its transport fields and use a fresh gap_id; do not resend rejected
hypothesis lists. Never repeat a successful query or re-register an existing hypothesis.
After registration, hypothesis_ids may contain only already registered IDs.

For CONFIRMED, return KernelDecision with one supported selected hypothesis, at least one
refuted alternative, and explicit ClaimEvidence entries for the root cause and every
affected asset. Root-cause claims must cite both the successful node-error record and the
successful relation-schema record. The failed node asset must cite its node-error record;
each downstream affected asset must cite the successful downstream lineage record. Cite
only current-run EvidenceRecord IDs from closed gaps. The Diagnostic Kernel validates the
claims but does not create claims or citations for you. If a required gap remains open,
return INSUFFICIENT_EVIDENCE instead of guessing. Never return hidden reasoning.

For a confirmed decision, these values must be exactly aligned:
selected_hypothesis_id -> corresponding hypothesis.root_cause_code -> ROOT_CAUSE claim.value.
For get_relation_schema, use only an exact relation name from upstream lineage
related_nodes[].name; never use related_nodes[].node_id.
""".strip()

SYSTEM_PROMPT_VERSION = "m6.diagnosis.v1"
SYSTEM_PROMPT_SHA256 = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()
_M6_ROOT_CAUSE_CODES = (
    "SOURCE_SCHEMA_COLUMN_RENAMED",
    "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
)
_MODEL_ERROR_REASONS = {
    "MODEL_DECLINED",
    "MODEL_REQUEST_LIMIT",
    "MODEL_TIMEOUT",
    "MODEL_PROTOCOL_ERROR",
    "MODEL_RUNTIME_ERROR",
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
_KERNEL_RETRY_GUIDANCE = {
    "ARGUMENTS_INVALID": (
        "ARGUMENTS_INVALID: Send only the tool's business arguments and the flat "
        "transport fields, using JSON lists for list fields."
    ),
    "GAP_TOOL_MISMATCH": (
        "GAP_TOOL_MISMATCH: Match LOCATE_FAILURE to get_dbt_run_results, "
        "EXPLAIN_FAILURE to get_dbt_node_error, DISCOVER_SOURCE_RELATION to upstream "
        "get_dbt_lineage, DISCRIMINATE_SCHEMA to get_relation_schema, and MAP_IMPACT "
        "to downstream get_dbt_lineage."
    ),
    "NODE_ARGUMENT_NOT_PROVEN": (
        "NODE_ARGUMENT_NOT_PROVEN: Use a node_id returned by run results or prior lineage."
    ),
    "RELATION_ARGUMENT_NOT_PROVEN": (
        "RELATION_ARGUMENT_NOT_PROVEN: For get_relation_schema, set relation_name to an "
        "exact name from related_nodes[].name in a successful upstream get_dbt_lineage "
        "result; never use related_nodes[].node_id."
    ),
    "HYPOTHESIS_REQUIRES_NODE_ERROR": (
        "HYPOTHESIS_REQUIRES_NODE_ERROR: Do not register hypotheses until a successful "
        "get_dbt_node_error return."
    ),
    "DUPLICATE_HYPOTHESIS": (
        "DUPLICATE_HYPOTHESIS: Register each hypothesis ID only once and do not resend "
        "the new-hypothesis lists."
    ),
    "HYPOTHESIS_REFERENCE_UNKNOWN": (
        "HYPOTHESIS_REFERENCE_UNKNOWN: hypothesis_ids may contain only already registered "
        "hypothesis IDs."
    ),
    "SELECTED_HYPOTHESIS_NOT_SUPPORTED": (
        "SELECTED_HYPOTHESIS_NOT_SUPPORTED: Set selected_hypothesis_id to a registered "
        "hypothesis whose assessment verdict is SUPPORTED; do not select a REFUTED "
        "hypothesis."
    ),
    "DUPLICATE_TOOL_CALL": (
        "DUPLICATE_TOOL_CALL: Do not repeat a successful query; choose the next required "
        "evidence gap."
    ),
    "DUPLICATE_GAP_ID": (
        "DUPLICATE_GAP_ID: Use a fresh gap_id for every tool call."
    ),
    "ROOT_CLAIM_MISMATCH": (
        "ROOT_CLAIM_MISMATCH: Set the ROOT_CAUSE claim value exactly to the "
        "root_cause_code registered for selected_hypothesis_id. Do not use a hypothesis "
        "ID, summary, or generic label."
    ),
    "ROOT_CLAIM_REQUIRED": (
        "ROOT_CLAIM_REQUIRED: Return exactly one ROOT_CAUSE claim."
    ),
    "ROOT_CLAIM_EVIDENCE_INCOMPATIBLE": (
        "ROOT_CLAIM_EVIDENCE_INCOMPATIBLE: Cite both the successful node-error record "
        "and the successful relation-schema record on the ROOT_CAUSE claim."
    ),
    "ASSET_CLAIM_REQUIRED": (
        "ASSET_CLAIM_REQUIRED: Return one AFFECTED_ASSET claim for every affected asset."
    ),
    "ASSET_CLAIM_EVIDENCE_INCOMPATIBLE": (
        "ASSET_CLAIM_EVIDENCE_INCOMPATIBLE: Cite the node-error record for the failed "
        "node or the downstream lineage record for each downstream asset."
    ),
    "CLAIM_EVIDENCE_TYPES_INCOMPLETE": (
        "CLAIM_EVIDENCE_TYPES_INCOMPLETE: Claims must collectively cite node-error, "
        "relation-schema, and downstream-lineage records."
    ),
    "ASSESSMENT_EVIDENCE_UNKNOWN": (
        "ASSESSMENT_EVIDENCE_UNKNOWN: Use only evidence IDs returned by successful "
        "evidence tools for hypothesis assessments; do not invent or alter IDs."
    ),
    "CLAIM_EVIDENCE_UNKNOWN": (
        "CLAIM_EVIDENCE_UNKNOWN: Use only evidence IDs returned by successful evidence "
        "tools for claims; do not invent or alter IDs."
    ),
    "HYPOTHESIS_ASSESSMENT_INCOMPLETE": (
        "HYPOTHESIS_ASSESSMENT_INCOMPLETE: Include exactly one assessment for every "
        "registered hypothesis."
    ),
    "REFUTED_HYPOTHESIS_REQUIRED": (
        "REFUTED_HYPOTHESIS_REQUIRED: Mark at least one non-selected hypothesis REFUTED."
    ),
    "EVIDENCE_GAP_OPEN": (
        "EVIDENCE_GAP_OPEN: If any gap is BLOCKED, return INSUFFICIENT_EVIDENCE with "
        "empty claims. Otherwise close every OPEN gap before CONFIRMED."
    ),
}
_GENERIC_KERNEL_RETRY_GUIDANCE = (
    "Rebuild the decision from current closed evidence and "
    "registered hypotheses; keep selected_hypothesis_id, hypothesis.root_cause_code, "
    "and ROOT_CAUSE claim.value aligned."
)


def _kernel_retry_message(code: str) -> str:
    return _KERNEL_RETRY_GUIDANCE.get(
        code, f"{code}: {_GENERIC_KERNEL_RETRY_GUIDANCE}"
    )


@dataclass(frozen=True)
class ModelIdentity:
    provider: str
    model: str


_TransportGapId = Annotated[
    StrictStr,
    Field(description="The gap identifier, such as g_failure."),
]
_TransportGapKind = Annotated[
    EvidenceGapKind,
    Field(description="The observable evidence gap kind."),
]
_TransportHypothesisIds = Annotated[
    list[StrictStr],
    Field(
        default_factory=list,
        description="Known hypothesis identifiers referenced by this gap.",
    ),
]
_TransportNewHypothesisIds = Annotated[
    list[StrictStr],
    Field(
        default_factory=list,
        description="New hypothesis identifiers, parallel to new_hypothesis_root_cause_codes.",
    ),
]
_TransportNewHypothesisCodes = Annotated[
    list[StrictStr],
    Field(
        default_factory=list,
        description="New hypothesis root-cause codes, parallel to new_hypothesis_ids.",
    ),
]


class InvestigationIntentTransport(BaseModel):
    """Shallow JSON transport for model tool calls; the Kernel still owns strict intent rules."""

    model_config = ConfigDict(extra="forbid")

    gap_id: _TransportGapId
    gap_kind: _TransportGapKind
    hypothesis_ids: _TransportHypothesisIds
    new_hypothesis_ids: _TransportNewHypothesisIds
    new_hypothesis_root_cause_codes: _TransportNewHypothesisCodes

    @model_validator(mode="after")
    def require_parallel_new_hypotheses(self) -> InvestigationIntentTransport:
        if len(self.new_hypothesis_ids) != len(self.new_hypothesis_root_cause_codes):
            raise ValueError(
                "new_hypothesis_ids and new_hypothesis_root_cause_codes must be parallel"
            )
        return self

    def to_investigation_intent(self) -> InvestigationIntent:
        return InvestigationIntent(
            gap_id=self.gap_id,
            gap_kind=self.gap_kind,
            hypothesis_ids=tuple(self.hypothesis_ids),
            new_hypotheses=tuple(
                Hypothesis(
                    hypothesis_id=hypothesis_id,
                    root_cause_code=root_cause_code,
                )
                for hypothesis_id, root_cause_code in zip(
                    self.new_hypothesis_ids,
                    self.new_hypothesis_root_cause_codes,
                    strict=True,
                )
            ),
        )


def _transport_intent(
    *,
    gap_id: str,
    gap_kind: EvidenceGapKind,
    hypothesis_ids: list[str],
    new_hypothesis_ids: list[str],
    new_hypothesis_root_cause_codes: list[str],
) -> InvestigationIntentTransport:
    return InvestigationIntentTransport(
        gap_id=gap_id,
        gap_kind=gap_kind,
        hypothesis_ids=hypothesis_ids,
        new_hypothesis_ids=new_hypothesis_ids,
        new_hypothesis_root_cause_codes=new_hypothesis_root_cause_codes,
    )


_DEFAULT_MODEL_IDENTITY = ModelIdentity("pydantic-function", "scripted-kernel-model")


@dataclass(frozen=True)
class _ModelResponseObservation:
    function_tool_names: tuple[str, ...]
    output_tool_names: tuple[str, ...]
    has_tool_call: bool
    has_text_output: bool


@dataclass
class _RunState:
    kernel: DiagnosticKernel
    started_at: float = field(default_factory=monotonic)
    trace: list[object] = field(default_factory=list)
    usage: RunUsage = field(default_factory=RunUsage)
    successful_calls: int = 0
    outcome: KernelOutcome | None = None
    last_model_response: _ModelResponseObservation | None = None
    protocol_failure: tuple[str, str, str | None] | None = None
    protocol_trace_recorded: bool = False

    def record_model_response(
        self, response: ModelResponse, model_request_parameters: ModelRequestParameters
    ) -> None:
        self.protocol_failure = None
        self.protocol_trace_recorded = False
        declared_function_tools = {
            tool.name for tool in model_request_parameters.function_tools
        }
        declared_output_tools = {
            tool.name for tool in model_request_parameters.output_tools
        }
        self.last_model_response = _ModelResponseObservation(
            function_tool_names=tuple(
                part.tool_name
                for part in response.parts
                if isinstance(part, ToolCallPart)
                and part.tool_name in declared_function_tools
            ),
            output_tool_names=tuple(
                part.tool_name
                for part in response.parts
                if isinstance(part, ToolCallPart)
                and part.tool_name in declared_output_tools
            ),
            has_tool_call=any(isinstance(part, ToolCallPart) for part in response.parts),
            has_text_output=any(isinstance(part, TextPart) for part in response.parts),
        )

    def set_protocol_failure(
        self, *, category: str, stage: str, tool_name: str | None
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
                arguments={key: _redact_trace_value(value) for key, value in arguments.items()},
                fingerprint=fingerprint,
                evidence_ids=evidence_ids,
                error_code=error_code,
                elapsed_ms=max(0, int((monotonic() - started_at) * 1000)),
            )
        )


class _ModelObservationAdapter(Model):
    """Observe only safe response shape facts before PydanticAI validates them."""

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
    def system(self) -> str:
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

    def prepare_request(
        self,
        model_settings,
        model_request_parameters: ModelRequestParameters,
    ):
        return self._model.prepare_request(model_settings, model_request_parameters)

    def prepare_messages(
        self,
        messages,
        model_request_parameters: ModelRequestParameters | None = None,
    ):
        return self._model.prepare_messages(messages, model_request_parameters)

    def resolve_prompt_cache_retention(self, model_settings):
        return self._model.resolve_prompt_cache_retention(model_settings)

    async def request(
        self,
        messages,
        model_settings,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        response = await self._model.request(
            messages, model_settings, model_request_parameters
        )
        self._state.record_model_response(response, model_request_parameters)
        return response


class _ControllerInvariantError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_PATH_PATTERN = re.compile(r"(?i)(?:[a-z]:[\\/]|\\\\|/)[^\r\n,;:()\[\]]+")
_SQL_PATTERN = re.compile(
    r"(?is)\b(?:select|insert|update|delete|alter|create|drop|grant|revoke)\b.*"
)
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(?:\b(?:password|passwd|secret|token|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+"
    r"|\bBearer\s+[^\s,;]+"
    r"|\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s]+@[^\s,;]+)"
)


def _redact_trace_value(value: str) -> str:
    value = _CREDENTIAL_PATTERN.sub("[redacted credential]", value)
    value = _SQL_PATTERN.sub("[redacted SQL]", value)
    value = _PATH_PATTERN.sub("[redacted path]", value)
    return value


def _safe_tool_error_code(code: object) -> str:
    return code if isinstance(code, str) and code in _SAFE_TOOL_ERRORS else "EVIDENCE_TOOL_ERROR"


def _diagnosis_from_outcome(state: _RunState, outcome: KernelOutcome) -> Diagnosis:
    snapshot = state.kernel.snapshot(model_requests_used=state.usage.requests)
    return Diagnosis(
        status=DiagnosisStatus(outcome.status.value),
        incident_case_id=snapshot.incident_case_id,
        run_id=snapshot.run_id,
        root_cause_code=outcome.root_cause_code,
        summary=outcome.summary,
        affected_assets=outcome.affected_assets,
        evidence_ids=outcome.evidence_ids,
        recommended_actions=outcome.recommended_actions,
        confidence=outcome.confidence,
    )


def _last_observed_tool_name(
    state: _RunState, *, output: bool
) -> str | None:
    observation = state.last_model_response
    if observation is None:
        return None
    names = observation.output_tool_names if output else observation.function_tool_names
    return names[-1] if names else None


def _record_protocol_failure(state: _RunState, error: BaseException) -> None:
    if isinstance(error, (ModelAPIError, IncompleteToolCall)):
        state.set_protocol_failure(
            category="PROVIDER_PROTOCOL_FAILURE",
            stage="PROVIDER_RESPONSE",
            tool_name=None,
        )
        return

    observation = state.last_model_response
    if isinstance(error, UnexpectedModelBehavior | ToolRetryError | ValueError | TypeError):
        if (
            state.protocol_failure is not None
            and state.protocol_failure[1] == "OUTPUT_VALIDATION"
        ):
            return
        if observation is not None and observation.output_tool_names:
            state.set_protocol_failure(
                category="OUTPUT_SCHEMA_REJECTED",
                stage="OUTPUT_SCHEMA_VALIDATION",
                tool_name=_last_observed_tool_name(state, output=True),
            )
            return
        if observation is not None and observation.function_tool_names:
            state.set_protocol_failure(
                category="TOOL_ARGUMENT_REJECTED",
                stage="TOOL_ARGUMENT_VALIDATION",
                tool_name=_last_observed_tool_name(state, output=False),
            )
            return
        if observation is not None and observation.has_text_output:
            state.set_protocol_failure(
                category="OUTPUT_SCHEMA_REJECTED",
                stage="OUTPUT_SCHEMA_VALIDATION",
                tool_name=None,
            )
            return

    state.set_protocol_failure(
        category="PROVIDER_PROTOCOL_FAILURE",
        stage="PROVIDER_RESPONSE",
        tool_name=None,
    )


class DiagnosisRunner:
    def __init__(
        self,
        run_id: str,
        settings: DiagnosticSettings,
        project_root: Path,
        model: Model,
        tools: EvidenceTools,
        model_identity: ModelIdentity,
    ) -> None:
        self._run_id = run_id
        self._settings = settings
        self._project_root = project_root
        self._model = model
        self._tools = tools
        self._model_identity = model_identity

    @classmethod
    def for_run(
        cls,
        run_id: str,
        settings: DiagnosticSettings,
        project_root: Path = PROJECT_ROOT,
        *,
        model: Model | None = None,
        tools: EvidenceTools | None = None,
        model_identity: ModelIdentity | None = None,
    ) -> DiagnosisRunner:
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
        return cls(run_id, settings, project_root, model, tools, model_identity)

    def _agent(self, state: _RunState) -> Agent[_RunState, KernelDecision]:
        agent = Agent(
            _ModelObservationAdapter(self._model, state),
            deps_type=_RunState,
            output_type=KernelDecision,
            system_prompt=SYSTEM_PROMPT,
        )

        def execute(
            ctx: RunContext[_RunState],
            tool_name: str,
            arguments: dict[str, str],
            intent: InvestigationIntentTransport,
            call: Callable[[], tuple[EvidenceRecord, ...]],
        ) -> tuple[EvidenceRecord, ...]:
            state = ctx.deps
            started_at = monotonic()
            strict_intent = intent.to_investigation_intent()
            try:
                prepared = state.kernel.prepare_tool(
                    intent=strict_intent,
                    tool_name=tool_name,
                    arguments=arguments,
                )
            except KernelError as error:
                if error.fingerprint is not None:
                    state.record_tool_trace(
                        tool_name=tool_name,
                        arguments=arguments,
                        fingerprint=error.fingerprint,
                        error_code=error.code,
                        started_at=started_at,
                    )
                raise ToolFailed(_kernel_retry_message(error.code)) from None

            try:
                records = tuple(call())
            except EvidenceToolError as error:
                error_code = _safe_tool_error_code(getattr(error, "code", None))
                state.kernel.record_tool_failure(prepared, error_code)
                state.record_tool_trace(
                    tool_name=tool_name,
                    arguments=arguments,
                    fingerprint=prepared.fingerprint,
                    error_code=error_code,
                    started_at=started_at,
                )
                raise ToolFailed(error_code) from None
            except Exception:
                state.kernel.record_tool_failure(prepared, "EVIDENCE_TOOL_ERROR")
                state.record_tool_trace(
                    tool_name=tool_name,
                    arguments=arguments,
                    fingerprint=prepared.fingerprint,
                    error_code="EVIDENCE_TOOL_ERROR",
                    started_at=started_at,
                )
                raise ToolFailed("EVIDENCE_TOOL_ERROR") from None

            try:
                accepted = state.kernel.record_tool_result(prepared, records)
            except KernelError as error:
                error_code = _safe_tool_error_code(error.code)
                state.kernel.record_tool_failure(prepared, error_code)
                state.record_tool_trace(
                    tool_name=tool_name,
                    arguments=arguments,
                    fingerprint=prepared.fingerprint,
                    error_code=error_code,
                    started_at=started_at,
                )
                raise ToolFailed(_kernel_retry_message(error_code)) from None

            state.successful_calls += 1
            state.record_tool_trace(
                tool_name=tool_name,
                arguments=arguments,
                fingerprint=prepared.fingerprint,
                evidence_ids=tuple(record.evidence_id for record in accepted),
                started_at=started_at,
            )
            return accepted

        @agent.tool
        def get_dbt_run_results(
            ctx: RunContext[_RunState],
            run_id: Annotated[
                str,
                Field(description="The exact verified run_id from the diagnostic context."),
            ],
            gap_id: _TransportGapId,
            gap_kind: _TransportGapKind,
            hypothesis_ids: _TransportHypothesisIds,
            new_hypothesis_ids: _TransportNewHypothesisIds,
            new_hypothesis_root_cause_codes: _TransportNewHypothesisCodes,
        ) -> tuple[EvidenceRecord, ...]:
            """Return run results for the verified run and close a locate-failure gap."""
            return execute(
                ctx,
                "get_dbt_run_results",
                {"run_id": run_id},
                _transport_intent(
                    gap_id=gap_id,
                    gap_kind=gap_kind,
                    hypothesis_ids=hypothesis_ids,
                    new_hypothesis_ids=new_hypothesis_ids,
                    new_hypothesis_root_cause_codes=new_hypothesis_root_cause_codes,
                ),
                lambda: self._tools.get_dbt_run_results(run_id),
            )

        @agent.tool
        def get_dbt_node_error(
            ctx: RunContext[_RunState],
            run_id: Annotated[
                str,
                Field(description="The exact verified run_id from the diagnostic context."),
            ],
            node_id: Annotated[
                str,
                Field(description="An exact failed node_id returned by run results."),
            ],
            gap_id: _TransportGapId,
            gap_kind: _TransportGapKind,
            hypothesis_ids: _TransportHypothesisIds,
            new_hypothesis_ids: _TransportNewHypothesisIds,
            new_hypothesis_root_cause_codes: _TransportNewHypothesisCodes,
        ) -> tuple[EvidenceRecord, ...]:
            """Return the error for a failed node proven by run-results evidence."""
            return execute(
                ctx,
                "get_dbt_node_error",
                {"node_id": node_id, "run_id": run_id},
                _transport_intent(
                    gap_id=gap_id,
                    gap_kind=gap_kind,
                    hypothesis_ids=hypothesis_ids,
                    new_hypothesis_ids=new_hypothesis_ids,
                    new_hypothesis_root_cause_codes=new_hypothesis_root_cause_codes,
                ),
                lambda: self._tools.get_dbt_node_error(run_id, node_id),
            )

        @agent.tool
        def get_relation_schema(
            ctx: RunContext[_RunState],
            relation_name: Annotated[
                str,
                Field(
                    description=(
                        "An exact unqualified relation name copied from "
                        "related_nodes[].name of a successful upstream get_dbt_lineage "
                        "result; never use related_nodes[].node_id."
                    )
                ),
            ],
            gap_id: _TransportGapId,
            gap_kind: _TransportGapKind,
            hypothesis_ids: _TransportHypothesisIds,
            new_hypothesis_ids: _TransportNewHypothesisIds,
            new_hypothesis_root_cause_codes: _TransportNewHypothesisCodes,
        ) -> tuple[EvidenceRecord, ...]:
            """Return catalog metadata for a relation proven by upstream lineage."""
            return execute(
                ctx,
                "get_relation_schema",
                {"relation_name": relation_name},
                _transport_intent(
                    gap_id=gap_id,
                    gap_kind=gap_kind,
                    hypothesis_ids=hypothesis_ids,
                    new_hypothesis_ids=new_hypothesis_ids,
                    new_hypothesis_root_cause_codes=new_hypothesis_root_cause_codes,
                ),
                lambda: self._tools.get_relation_schema(relation_name),
            )

        @agent.tool
        def get_dbt_lineage(
            ctx: RunContext[_RunState],
            node_id: Annotated[
                str,
                Field(description="An exact node_id returned by run results or prior lineage."),
            ],
            direction: Literal["upstream", "downstream"],
            gap_id: _TransportGapId,
            gap_kind: _TransportGapKind,
            hypothesis_ids: _TransportHypothesisIds,
            new_hypothesis_ids: _TransportNewHypothesisIds,
            new_hypothesis_root_cause_codes: _TransportNewHypothesisCodes,
        ) -> tuple[EvidenceRecord, ...]:
            """Return bounded lineage for a node proven by structured evidence."""
            return execute(
                ctx,
                "get_dbt_lineage",
                {"direction": direction, "node_id": node_id},
                _transport_intent(
                    gap_id=gap_id,
                    gap_kind=gap_kind,
                    hypothesis_ids=hypothesis_ids,
                    new_hypothesis_ids=new_hypothesis_ids,
                    new_hypothesis_root_cause_codes=new_hypothesis_root_cause_codes,
                ),
                lambda: self._tools.get_dbt_lineage(node_id, direction),
            )

        @agent.output_validator
        def validate_output(
            ctx: RunContext[_RunState], output: KernelDecision
        ) -> KernelDecision:
            state = ctx.deps
            if output.status == "INSUFFICIENT_EVIDENCE" and not any(
                gap.status in {EvidenceGapStatus.CLOSED, EvidenceGapStatus.BLOCKED}
                for gap in state.kernel.snapshot(
                    model_requests_used=state.usage.requests
                ).gaps
            ):
                state.set_protocol_failure(
                    category="PREMATURE_FINALIZATION",
                    stage="OUTPUT_VALIDATION",
                    tool_name=_last_observed_tool_name(state, output=True),
                )
                raise ModelRetry("INVESTIGATION_REQUIRED")
            try:
                outcome = state.kernel.finalize(output)
            except KernelError as error:
                state.set_protocol_failure(
                    category="DECISION_CONTRACT_REJECTED",
                    stage="OUTPUT_VALIDATION",
                    tool_name=_last_observed_tool_name(state, output=True),
                )
                state.trace.append(
                    EvidenceGateTraceEvent(
                        event_type="EVIDENCE_GATE",
                        reason_code=error.code,
                        accepted=False,
                    )
                )
                raise ModelRetry(_kernel_retry_message(error.code)) from None
            state.outcome = outcome
            state.trace.append(
                EvidenceGateTraceEvent(
                    event_type="EVIDENCE_GATE",
                    reason_code=outcome.status.value,
                    accepted=True,
                )
            )
            return output

        return agent

    def _result(self, state: _RunState) -> DiagnosisRunResult:
        if state.outcome is None:
            raise _ControllerInvariantError("MODEL_PROTOCOL_ERROR")
        snapshot = state.kernel.snapshot(model_requests_used=state.usage.requests)
        diagnosis = _diagnosis_from_outcome(state, state.outcome)
        trace = (
            *state.trace,
            KernelStateTraceEvent(event_type="KERNEL_STATE", state=snapshot),
        )
        return DiagnosisRunResult(
            diagnosis=diagnosis,
            evidence_records=state.kernel.evidence_records,
            trace=trace,
            investigation_state=snapshot,
            metrics=DiagnosisMetrics(
                provider=self._model_identity.provider,
                model=self._model_identity.model,
                model_requests=state.usage.requests,
                input_tokens=state.usage.input_tokens or 0,
                output_tokens=state.usage.output_tokens or 0,
                tool_call_attempts=snapshot.tool_calls_used,
                successful_tool_calls=state.successful_calls,
                elapsed_ms=max(0, int((monotonic() - state.started_at) * 1000)),
            ),
        )

    def _model_error_result(self, state: _RunState, reason: str) -> DiagnosisRunResult:
        if reason not in _MODEL_ERROR_REASONS:
            reason = "MODEL_RUNTIME_ERROR"
        if state.outcome is None:
            state.outcome = state.kernel.terminate_model_error(reason)
            state.trace.append(
                EvidenceGateTraceEvent(
                    event_type="EVIDENCE_GATE",
                    reason_code=reason,
                    accepted=True,
                )
            )
        return self._result(state)

    async def diagnose(self, incident_case_id: str) -> DiagnosisRunResult:
        context = resolve_run_context(
            self._run_id,
            incident_case_id,
            project_root=self._project_root,
        )
        kernel = DiagnosticKernel.start(
            incident_case_id=context.incident_case_id,
            run_id=context.run_id,
            allowed_root_cause_codes=_M6_ROOT_CAUSE_CODES,
            model_request_limit=8,
            tool_call_limit=8,
        )
        state = _RunState(kernel)
        agent = self._agent(state)
        prompt = (
            f"Investigate incident case {context.incident_case_id!r} for verified run "
            f"{context.run_id!r}. Use the read-only evidence tools and return KernelDecision."
        )
        try:
            with agent.parallel_tool_call_execution_mode("sequential"):
                async with asyncio.timeout(300):
                    await agent.run(
                        prompt,
                        deps=state,
                        usage=state.usage,
                        usage_limits=UsageLimits(request_limit=8, tool_calls_limit=8),
                        retries={"tools": 1, "output": 2},
                    )
            if state.outcome is None:
                raise _ControllerInvariantError("MODEL_PROTOCOL_ERROR")
            return self._result(state)
        except TimeoutError:
            return self._model_error_result(state, "MODEL_TIMEOUT")
        except UsageLimitExceeded:
            return self._model_error_result(state, "MODEL_REQUEST_LIMIT")
        except (
            IncompleteToolCall,
            ModelAPIError,
            UnexpectedModelBehavior,
            ModelRetry,
            ToolFailed,
            ToolRetryError,
            ValueError,
            TypeError,
        ) as error:
            _record_protocol_failure(state, error)
            state.append_protocol_trace()
            return self._model_error_result(state, "MODEL_PROTOCOL_ERROR")
        except _ControllerInvariantError:
            state.set_protocol_failure(
                category="PROVIDER_PROTOCOL_FAILURE",
                stage="PROVIDER_RESPONSE",
                tool_name=None,
            )
            state.append_protocol_trace()
            return self._model_error_result(state, "MODEL_PROTOCOL_ERROR")
        except Exception:
            return self._model_error_result(state, "MODEL_RUNTIME_ERROR")
