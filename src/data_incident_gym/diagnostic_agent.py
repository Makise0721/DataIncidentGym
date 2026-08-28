from __future__ import annotations

import asyncio
import hashlib
import json
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
from data_incident_gym.evidence import (
    DbtLineageFact,
    DbtNodeErrorFact,
    EvidenceRecord,
    EvidenceToolError,
)
from data_incident_gym.evidence_tools import EvidenceTools
from data_incident_gym.run_context import resolve_run_context

SYSTEM_PROMPT = """
Diagnose one data incident using only the four registered read-only evidence tools and the
verified run context in the user request. Follow this order unless already satisfied:
1. Get run results. Use its exact failed node IDs for node-error and lineage calls.
2. Get the node error for an exact failed node ID returned by run results.
3. Get lineage for an exact node ID returned by run results or another lineage result.
   Before returning CONFIRMED, get downstream lineage for the exact failed node ID. For each
   downstream related model, copy only its verbatim name field into affected_assets; do not copy
   its node_id.
4. Get relation schema only for an exact unqualified relation name returned in structured
   lineage or schema evidence. A relation name is not a dbt node ID, SQL, a guessed name,
   or a schema-qualified string.

Never invent node IDs, relation names, column names, or evidence IDs. Every tool argument
except the verified run ID must come from a previous tool's structured result. Do not repeat
an identical tool call; one tool retry is allowed when a tool returns an error code. Cite
only evidence IDs returned by tools. Return the required Diagnosis object. If the evidence
does not support a conclusion, return INSUFFICIENT_EVIDENCE without guessing.

Use the versioned root-cause ontology below only when compatible evidence supports it:
- SOURCE_SCHEMA_COLUMN_RENAMED: a source relation column was renamed while a dbt consumer
  still references the former column.

Finalization discipline:
- Cite only verbatim evidence IDs returned by tools; never rewrite, concatenate, or guess an ID.
- Set affected_assets only from node IDs or model names that appear verbatim in node-error or
  downstream lineage evidence. Copy them exactly; do not use relation names, schema-qualified
  names, or invented names.
- In a CONFIRMED Diagnosis, cite the node-error, relation-schema, and downstream-lineage evidence
  that support the conclusion. Do not cite upstream lineage in a CONFIRMED diagnosis.
- Once the required evidence, including downstream lineage used to establish affected_assets,
  is collected, immediately return the Diagnosis. Do not add same-type or exploratory queries.
""".strip()

SYSTEM_PROMPT_VERSION = "m5.diagnosis.v5"
SYSTEM_PROMPT_SHA256 = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()
_MAX_TOOL_ATTEMPTS = 8
_MODEL_ERROR_REASONS = {
    "MODEL_DECLINED",
    "MODEL_REQUEST_LIMIT",
    "MODEL_TIMEOUT",
    "MODEL_PROTOCOL_ERROR",
    "MODEL_RUNTIME_ERROR",
}
_PATH_PATTERN = re.compile(r"(?i)(?:[a-z]:[\\/]|\\\\|/)[^\r\n,;:()\[\]]+")
_SQL_PATTERN = re.compile(
    r"(?is)\b(?:select|insert|update|delete|alter|create|drop|grant|revoke)\b.*"
)
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(?:\b(?:password|passwd|secret|token|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+"
    r"|\bBearer\s+[^\s,;]+"
    r"|\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s]+@[^\s,;]+)"
)


class _ControllerInvariantError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _ToolCallLimitReached(RuntimeError):
    pass


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((monotonic() - started_at) * 1000))


@dataclass
class _RunState:
    run_id: str
    incident_case_id: str
    started_at: float = field(default_factory=monotonic)
    evidence_records: list[EvidenceRecord] = field(default_factory=list)
    evidence_inventory: list[str] = field(default_factory=list)
    fingerprints: set[str] = field(default_factory=set)
    accepted_tool_attempts: list[str] = field(default_factory=list)
    successful_calls: int = 0
    trace: list[ToolTraceEvent | EvidenceGateTraceEvent] = field(default_factory=list)
    usage: RunUsage = field(default_factory=RunUsage)
    last_tool_name: str | None = None
    last_arguments: dict[str, str] = field(default_factory=dict)

    @property
    def tool_call_attempts(self) -> int:
        return len(self.accepted_tool_attempts)

    def _record_trace(
        self,
        *,
        tool_name: str,
        arguments: dict[str, str],
        fingerprint: str,
        evidence_ids: tuple[str, ...] = (),
        error_code: str | None = None,
        elapsed_ms: int,
    ) -> None:
        self.trace.append(
            ToolTraceEvent(
                event_type="TOOL_CALL",
                tool_name=tool_name,
                arguments={key: _redact_trace_value(value) for key, value in arguments.items()},
                fingerprint=fingerprint,
                evidence_ids=evidence_ids,
                error_code=error_code,
                elapsed_ms=elapsed_ms,
            )
        )

    def record_evidence(self, records: tuple[EvidenceRecord, ...]) -> tuple[EvidenceRecord, ...]:
        known = {record.evidence_id: record for record in self.evidence_records}
        unique: list[EvidenceRecord] = []
        pending: dict[str, EvidenceRecord] = {}
        for record in records:
            if record.run_id != self.run_id:
                raise _ControllerInvariantError("RUN_CONTEXT_MISMATCH")
            previous = known.get(record.evidence_id) or pending.get(record.evidence_id)
            if previous is not None and previous != record:
                raise _ControllerInvariantError("EVIDENCE_ID_CONFLICT")
            if previous is None:
                pending[record.evidence_id] = record
                unique.append(record)
        self.evidence_records.extend(unique)
        self.evidence_inventory.extend(record.evidence_id for record in unique)
        return tuple(unique)


def _redact_trace_value(value: str) -> str:
    value = _CREDENTIAL_PATTERN.sub("[redacted credential]", value)
    value = _SQL_PATTERN.sub("[redacted SQL]", value)
    value = _PATH_PATTERN.sub("[redacted path]", value)
    return value


def _canonical_fingerprint(run_id: str, tool_name: str, arguments: dict[str, str]) -> str:
    payload = {"arguments": arguments, "run_id": run_id, "tool_name": tool_name}
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _safe_diagnosis(
    *,
    state: _RunState,
    status: DiagnosisStatus,
    summary: str,
    evidence_ids: tuple[str, ...] = (),
    confidence: float = 0.0,
) -> Diagnosis:
    return Diagnosis(
        status=status,
        incident_case_id=state.incident_case_id,
        run_id=state.run_id,
        root_cause_code=None,
        summary=summary,
        affected_assets=(),
        evidence_ids=evidence_ids,
        recommended_actions=("Collect additional evidence before making a change.",),
        confidence=confidence,
    )


def _model_error(state: _RunState, reason: str) -> Diagnosis:
    if reason not in _MODEL_ERROR_REASONS:
        reason = "MODEL_RUNTIME_ERROR"
    return _safe_diagnosis(state=state, status=DiagnosisStatus.MODEL_ERROR, summary=reason)


def _asset_supported(record: EvidenceRecord, asset: str) -> bool:
    content = record.content
    if isinstance(content, DbtNodeErrorFact):
        return content.node_id == asset or content.node_id.rsplit(".", 1)[-1] == asset
    if isinstance(content, DbtLineageFact) and content.direction == "downstream":
        return any(
            node.name == asset
            for node in content.related_nodes
        )
    return False


class DiagnosisRunner:
    def __init__(
        self,
        run_id: str,
        settings: DiagnosticSettings,
        project_root: Path,
        model: Model,
        tools: EvidenceTools,
    ) -> None:
        self._run_id = run_id
        self._settings = settings
        self._project_root = project_root
        self._model = model
        self._tools = tools

    @classmethod
    def for_run(
        cls,
        run_id: str,
        settings: DiagnosticSettings,
        project_root: Path = PROJECT_ROOT,
        *,
        model: Model | None = None,
        tools: EvidenceTools | None = None,
    ) -> DiagnosisRunner:
        if model is None:
            provider = OpenAIProvider(
                base_url=str(settings.model_base_url),
                api_key=settings.model_api_key.get_secret_value(),
            )
            model = OpenAIChatModel(settings.model_name, provider=provider)
        if tools is None:
            tools = EvidenceTools.for_run(run_id, settings, project_root=project_root)
        return cls(run_id, settings, project_root, model, tools)

    def _agent(self) -> Agent[_RunState, Diagnosis]:
        agent = Agent(
            self._model,
            deps_type=_RunState,
            output_type=Diagnosis,
            system_prompt=SYSTEM_PROMPT,
        )

        def execute(
            ctx: RunContext[_RunState],
            tool_name: str,
            arguments: dict[str, str],
            call: Callable[[], tuple[EvidenceRecord, ...]],
        ) -> tuple[EvidenceRecord, ...]:
            state = ctx.deps
            tool_started_at = monotonic()
            fingerprint = _canonical_fingerprint(state.run_id, tool_name, arguments)
            state.last_tool_name = tool_name
            state.last_arguments = dict(arguments)
            if state.tool_call_attempts >= _MAX_TOOL_ATTEMPTS:
                state._record_trace(
                    tool_name=tool_name,
                    arguments=arguments,
                    fingerprint=fingerprint,
                    error_code="TOOL_CALL_LIMIT",
                    elapsed_ms=_elapsed_ms(tool_started_at),
                )
                state.accepted_tool_attempts.append(fingerprint)
                raise _ToolCallLimitReached
            state.accepted_tool_attempts.append(fingerprint)
            if fingerprint in state.fingerprints:
                state._record_trace(
                    tool_name=tool_name,
                    arguments=arguments,
                    fingerprint=fingerprint,
                    error_code="DUPLICATE_TOOL_CALL",
                    elapsed_ms=_elapsed_ms(tool_started_at),
                )
                raise ToolFailed("DUPLICATE_TOOL_CALL")
            state.fingerprints.add(fingerprint)
            try:
                records = call()
            except _ControllerInvariantError:
                raise
            except EvidenceToolError as error:
                code = getattr(error, "code", "EVIDENCE_TOOL_ERROR")
                state._record_trace(
                    tool_name=tool_name,
                    arguments=arguments,
                    fingerprint=fingerprint,
                    error_code=code,
                    elapsed_ms=_elapsed_ms(tool_started_at),
                )
                raise ToolFailed(code) from None
            except Exception:
                state._record_trace(
                    tool_name=tool_name,
                    arguments=arguments,
                    fingerprint=fingerprint,
                    error_code="EVIDENCE_TOOL_ERROR",
                    elapsed_ms=_elapsed_ms(tool_started_at),
                )
                raise ToolFailed("EVIDENCE_TOOL_ERROR") from None
            try:
                records = state.record_evidence(tuple(records))
            except _ControllerInvariantError as error:
                state._record_trace(
                    tool_name=tool_name,
                    arguments=arguments,
                    fingerprint=fingerprint,
                    error_code=error.code,
                    elapsed_ms=_elapsed_ms(tool_started_at),
                )
                raise
            state.successful_calls += 1
            state._record_trace(
                tool_name=tool_name,
                arguments=arguments,
                fingerprint=fingerprint,
                evidence_ids=tuple(record.evidence_id for record in records),
                elapsed_ms=_elapsed_ms(tool_started_at),
            )
            return records

        @agent.tool
        def get_dbt_run_results(
            ctx: RunContext[_RunState],
            run_id: Annotated[
                str,
                Field(description="The exact verified run_id from the diagnostic context."),
            ],
        ) -> tuple[EvidenceRecord, ...]:
            """Return run results; call this first and reuse its exact node IDs."""
            return execute(
                ctx,
                "get_dbt_run_results",
                {"run_id": run_id},
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
                Field(description="An exact failed node_id returned by get_dbt_run_results."),
            ],
        ) -> tuple[EvidenceRecord, ...]:
            """Return the error for an exact failed node_id from run-results evidence."""
            return execute(
                ctx,
                "get_dbt_node_error",
                {"node_id": node_id, "run_id": run_id},
                lambda: self._tools.get_dbt_node_error(run_id, node_id),
            )

        @agent.tool
        def get_relation_schema(
            ctx: RunContext[_RunState],
            relation_name: Annotated[
                str,
                Field(
                    description=(
                        "An exact unqualified relation name returned in structured lineage "
                        "or schema evidence; never a dbt node_id or guessed name."
                    )
                ),
            ],
        ) -> tuple[EvidenceRecord, ...]:
            """Return read-only catalog metadata for an exact relation name from prior evidence."""
            return execute(
                ctx,
                "get_relation_schema",
                {"relation_name": relation_name},
                lambda: self._tools.get_relation_schema(relation_name),
            )

        @agent.tool
        def get_dbt_lineage(
            ctx: RunContext[_RunState],
            node_id: Annotated[
                str,
                Field(
                    description=(
                        "An exact node_id returned by run-results or a prior lineage result."
                    )
                ),
            ],
            direction: Literal["upstream", "downstream"],
        ) -> tuple[EvidenceRecord, ...]:
            """Return bounded upstream or downstream lineage for a prior structured node_id."""
            return execute(
                ctx,
                "get_dbt_lineage",
                {"direction": direction, "node_id": node_id},
                lambda: self._tools.get_dbt_lineage(node_id, direction),
            )

        @agent.output_validator
        def validate_output(ctx: RunContext[_RunState], output: Diagnosis) -> Diagnosis:
            state = ctx.deps
            if output.run_id != state.run_id or output.incident_case_id != state.incident_case_id:
                state.trace.append(
                    EvidenceGateTraceEvent(
                        event_type="EVIDENCE_GATE",
                        reason_code="OUTPUT_SCOPE_MISMATCH",
                        accepted=False,
                    )
                )
                raise ModelRetry("DIAGNOSIS_SCOPE_MISMATCH")
            known = {record.evidence_id: record for record in state.evidence_records}
            if any(evidence_id not in known for evidence_id in output.evidence_ids):
                state.trace.append(
                    EvidenceGateTraceEvent(
                        event_type="EVIDENCE_GATE",
                        reason_code="UNKNOWN_EVIDENCE_ID",
                        accepted=False,
                    )
                )
                raise ModelRetry("UNKNOWN_EVIDENCE_ID")
            if output.status == DiagnosisStatus.MODEL_ERROR:
                state.trace.append(
                    EvidenceGateTraceEvent(
                        event_type="EVIDENCE_GATE", reason_code="MODEL_DECLINED", accepted=True
                    )
                )
                return _model_error(state, "MODEL_DECLINED")
            if output.status == DiagnosisStatus.INSUFFICIENT_EVIDENCE:
                state.trace.append(
                    EvidenceGateTraceEvent(
                        event_type="EVIDENCE_GATE",
                        reason_code="INSUFFICIENT_EVIDENCE",
                        accepted=True,
                    )
                )
                return output
            cited = tuple(known[evidence_id] for evidence_id in output.evidence_ids)
            types = {record.evidence_type.value for record in cited}
            required = {"DBT_NODE_ERROR", "RELATION_SCHEMA", "DBT_LINEAGE"}
            if not required.issubset(types):
                state.trace.append(
                    EvidenceGateTraceEvent(
                        event_type="EVIDENCE_GATE",
                        reason_code="EVIDENCE_TYPES_INCOMPLETE",
                        accepted=False,
                    )
                )
                return _safe_diagnosis(
                    state=state,
                    status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
                    summary="EVIDENCE_TYPES_INCOMPLETE",
                    evidence_ids=output.evidence_ids,
                    confidence=min(output.confidence, 0.5),
                )
            unsupported = [
                asset
                for asset in output.affected_assets
                if not any(_asset_supported(record, asset) for record in cited)
            ]
            if unsupported:
                state.trace.append(
                    EvidenceGateTraceEvent(
                        event_type="EVIDENCE_GATE",
                        reason_code="AFFECTED_ASSET_UNSUPPORTED",
                        accepted=False,
                    )
                )
                return _safe_diagnosis(
                    state=state,
                    status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
                    summary="AFFECTED_ASSET_UNSUPPORTED",
                    evidence_ids=output.evidence_ids,
                    confidence=min(output.confidence, 0.5),
                )
            state.trace.append(
                EvidenceGateTraceEvent(
                    event_type="EVIDENCE_GATE", reason_code="CONFIRMED", accepted=True
                )
            )
            return output

        return agent

    def _result(self, state: _RunState, diagnosis: Diagnosis) -> DiagnosisRunResult:
        elapsed_ms = max(0, int((monotonic() - state.started_at) * 1000))
        return DiagnosisRunResult(
            diagnosis=diagnosis,
            evidence_records=tuple(state.evidence_records),
            trace=tuple(state.trace),
            metrics=DiagnosisMetrics(
                provider="openai-compatible",
                model=self._settings.model_name,
                model_requests=state.usage.requests,
                input_tokens=state.usage.input_tokens or 0,
                output_tokens=state.usage.output_tokens or 0,
                tool_call_attempts=state.tool_call_attempts,
                successful_tool_calls=state.successful_calls,
                elapsed_ms=elapsed_ms,
            ),
        )

    async def diagnose(self, incident_case_id: str) -> DiagnosisRunResult:
        context = resolve_run_context(
            self._run_id,
            incident_case_id,
            project_root=self._project_root,
        )
        state = _RunState(context.run_id, context.incident_case_id)
        agent = self._agent()
        prompt = (
            f"Investigate incident case {context.incident_case_id!r} for verified run "
            f"{context.run_id!r}. Use the read-only evidence tools and return Diagnosis."
        )
        try:
            with agent.parallel_tool_call_execution_mode("sequential"):
                async with asyncio.timeout(300):
                    result = await agent.run(
                        prompt,
                        deps=state,
                        usage=state.usage,
                        usage_limits=UsageLimits(request_limit=8, tool_calls_limit=8),
                        retries={"tools": 1, "output": 2},
                    )
            return self._result(state, result.output)
        except _ToolCallLimitReached:
            return self._result(state, _model_error(state, "MODEL_REQUEST_LIMIT"))
        except TimeoutError:
            return self._result(state, _model_error(state, "MODEL_TIMEOUT"))
        except UsageLimitExceeded:
            return self._result(state, _model_error(state, "MODEL_REQUEST_LIMIT"))
        except (UnexpectedModelBehavior, ModelRetry, ValueError, TypeError):
            return self._result(state, _model_error(state, "MODEL_PROTOCOL_ERROR"))
        except _ControllerInvariantError:
            return self._result(state, _model_error(state, "MODEL_PROTOCOL_ERROR"))
        except Exception:
            return self._result(state, _model_error(state, "MODEL_RUNTIME_ERROR"))
