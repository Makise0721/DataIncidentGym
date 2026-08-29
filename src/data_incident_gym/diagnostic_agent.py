from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Annotated, Literal

from pydantic import Field
from pydantic_ai import Agent, ModelRetry, RunContext, RunUsage, UsageLimits
from pydantic_ai.exceptions import ToolFailed, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.diagnosis import (
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    EvidenceGateTraceEvent,
    ToolTraceEvent,
)
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.diagnostic_kernel import (
    DiagnosticKernel,
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
Every tool call must include an InvestigationIntent naming one observable EvidenceGap.
Tool arguments must come from the verified run context or prior structured evidence.

Maintain at least two candidate hypotheses before returning CONFIRMED. Use only this
versioned ontology:
- SOURCE_SCHEMA_COLUMN_RENAMED: a source column was renamed while a consumer still uses
  the former name.
- SOURCE_SCHEMA_COLUMN_TYPE_CHANGED: a source column kept its name but changed to an
  incompatible data type for a consumer.

Use gaps to locate the failure, inspect its error, discover the source relation,
discriminate competing schema hypotheses, and map downstream impact. The order is chosen
from the evidence already observed; do not make an unsupported or duplicate call.

For CONFIRMED, return KernelDecision with one supported selected hypothesis, at least one
refuted alternative, and explicit ClaimEvidence entries for the root cause and every
affected asset. Cite only current-run EvidenceRecord IDs. The Diagnostic Kernel validates
the claims but does not create claims or citations for you. If a required gap remains open,
return INSUFFICIENT_EVIDENCE instead of guessing. Never return hidden reasoning.
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


@dataclass(frozen=True)
class ModelIdentity:
    provider: str
    model: str


_DEFAULT_MODEL_IDENTITY = ModelIdentity("pydantic-function", "scripted-kernel-model")


@dataclass
class _RunState:
    kernel: DiagnosticKernel
    started_at: float = field(default_factory=monotonic)
    trace: list[object] = field(default_factory=list)
    usage: RunUsage = field(default_factory=RunUsage)
    successful_calls: int = 0
    outcome: KernelOutcome | None = None

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

    def _agent(self) -> Agent[_RunState, KernelDecision]:
        agent = Agent(
            self._model,
            deps_type=_RunState,
            output_type=KernelDecision,
            system_prompt=SYSTEM_PROMPT,
        )

        def execute(
            ctx: RunContext[_RunState],
            tool_name: str,
            arguments: dict[str, str],
            intent: InvestigationIntent,
            call: Callable[[], tuple[EvidenceRecord, ...]],
        ) -> tuple[EvidenceRecord, ...]:
            state = ctx.deps
            started_at = monotonic()
            try:
                prepared = state.kernel.prepare_tool(
                    intent=intent,
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
                raise ToolFailed(error.code) from None

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
                raise ToolFailed(error_code) from None

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
            intent: InvestigationIntent,
        ) -> tuple[EvidenceRecord, ...]:
            """Return run results for the verified run and close a locate-failure gap."""
            return execute(
                ctx,
                "get_dbt_run_results",
                {"run_id": run_id},
                intent,
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
            intent: InvestigationIntent,
        ) -> tuple[EvidenceRecord, ...]:
            """Return the error for a failed node proven by run-results evidence."""
            return execute(
                ctx,
                "get_dbt_node_error",
                {"node_id": node_id, "run_id": run_id},
                intent,
                lambda: self._tools.get_dbt_node_error(run_id, node_id),
            )

        @agent.tool
        def get_relation_schema(
            ctx: RunContext[_RunState],
            relation_name: Annotated[
                str,
                Field(description="An exact unqualified relation name from prior lineage."),
            ],
            intent: InvestigationIntent,
        ) -> tuple[EvidenceRecord, ...]:
            """Return catalog metadata for a relation proven by upstream lineage."""
            return execute(
                ctx,
                "get_relation_schema",
                {"relation_name": relation_name},
                intent,
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
            intent: InvestigationIntent,
        ) -> tuple[EvidenceRecord, ...]:
            """Return bounded lineage for a node proven by structured evidence."""
            return execute(
                ctx,
                "get_dbt_lineage",
                {"direction": direction, "node_id": node_id},
                intent,
                lambda: self._tools.get_dbt_lineage(node_id, direction),
            )

        @agent.output_validator
        def validate_output(
            ctx: RunContext[_RunState], output: KernelDecision
        ) -> KernelDecision:
            state = ctx.deps
            try:
                outcome = state.kernel.finalize(output)
            except KernelError as error:
                state.trace.append(
                    EvidenceGateTraceEvent(
                        event_type="EVIDENCE_GATE",
                        reason_code=error.code,
                        accepted=False,
                    )
                )
                raise ModelRetry(error.code) from None
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
        agent = self._agent()
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
        except (UnexpectedModelBehavior, ModelRetry, ToolFailed, ValueError, TypeError):
            return self._model_error_result(state, "MODEL_PROTOCOL_ERROR")
        except _ControllerInvariantError:
            return self._model_error_result(state, "MODEL_PROTOCOL_ERROR")
        except Exception:
            return self._model_error_result(state, "MODEL_RUNTIME_ERROR")
