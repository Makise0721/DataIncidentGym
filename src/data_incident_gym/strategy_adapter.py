"""Unified strategy access protocol (T09): ``p1.strategy_adapter.v1``.

The harness owns the run identity, the tool allowlist, the budget, the deadline
and the evidence registration. A strategy — built-in or third-party — only sees
the public task context and can only act through five operations:

- ``task_context``: the public, leak-free description of this run (the incident
  brief, the relation whitelist, the tool allowlist, the budget). It never
  contains the private scenario contract, the expected answer or the case id.
- ``call_tool``: one read-only evidence request, counted and registered by the
  harness; refusals keep the backend's real error code as the receipt.
- ``submit``: the final diagnosis, validated against the registered evidence —
  a strategy cannot cite an evidence id the tools never returned, and cannot
  swap ids for facts it did not observe.
- ``cancel``: give the run up with a fixed reason; the session closes.
- ``report_usage``: self-reported cost and counters; recorded as auxiliary
  only, never as the authoritative count.

Built-in strategies attach through ``ProtocolTools``, a facade with the
``EvidenceTools`` shape: every built-in tool call passes the same allowlist,
budget and registration checks, and the facade is transparent enough that the
runner's own behaviour — diagnosis, counters, checks — is unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from time import monotonic
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from data_incident_gym.diagnosis import (
    Confidence,
    Diagnosis,
    DiagnosisClaim,
    DiagnosisStatus,
    DiagnosisV2,
    NonBlankStr,
    RootCauseCode,
    TargetRefusal,
    UnresolvedEvidence,
    UnresolvedEvidenceV2,
)
from data_incident_gym.evidence import (
    EVIDENCE_BATCH_TOOLS,
    EvidenceRecord,
    EvidenceToolError,
    safe_error_code,
)
from data_incident_gym.run_context import IncidentBrief, ObservableRunContext

STRATEGY_PROTOCOL_VERSION = "p1.strategy_adapter.v1"

PROTOCOL_TOOL_ALLOWLIST = frozenset(
    {
        "get_dbt_run_results",
        "get_dbt_node_error",
        "get_dbt_lineage",
        "get_relation_schema",
        "get_relation_data_profile",
        "get_relation_history",
    }
)

#: The v2 tool surface: the six read-only tools plus the two T13 batch facts.
EVIDENCE_V2_TOOL_ALLOWLIST = PROTOCOL_TOOL_ALLOWLIST | frozenset(EVIDENCE_BATCH_TOOLS)


def tool_allowlist_for_context(context: ObservableRunContext) -> frozenset[str]:
    """The tool surface one run's context grants.

    Keeping the choice here means a v1 run can never be handed the v2 tools
    (its identity stays frozen), while a v2 run's session can serve the facts
    its contract requires.
    """

    return EVIDENCE_V2_TOOL_ALLOWLIST if context.is_v2 else PROTOCOL_TOOL_ALLOWLIST

#: Error codes the protocol itself emits. A refused tool call keeps the
#: backend's real error code (e.g. ``RELATION_NOT_ALLOWED``) instead of one of
#: these, so refusal receipts stay meaningful to the strategy.
PROTOCOL_ERROR_CODES = frozenset(
    {
        "DECLARATION_INVALID",
        "SESSION_CLOSED",
        "UNKNOWN_TOOL",
        "TOOL_NOT_ALLOWLISTED",
        "TOOL_ARGUMENT_INVALID",
        "TOOL_BUDGET_EXHAUSTED",
        "TOOL_BACKEND_ERROR",
        "EVIDENCE_NOT_REGISTERED",
        "SUBMISSION_INVALID",
        "SUBMISSION_ALREADY_FINAL",
        "MODEL_REQUEST_BUDGET_EXHAUSTED",
        "OUTPUT_RETRY_EXHAUSTED",
        "DEADLINE_EXCEEDED",
    }
)

CancellationReason = Literal[
    "STRATEGY_CANCELLED",
    "STRATEGY_TIMEOUT",
    "RUN_FAILED",
    "HARNESS_SHUTDOWN",
]


class StrategyProtocolError(RuntimeError):
    """Raised for construction-level protocol violations (bad declaration)."""

    def __init__(self, code: str, *, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")
        self.__cause__ = None
        self.__context__ = None


BUILTIN_FRAMEWORK = "data-incident-gym"


def builtin_declaration(
    *, model_provider: str, model_name: str, deterministic: bool
) -> StrategyDeclaration:
    """The declaration every built-in runner runs under."""

    return StrategyDeclaration(
        framework=BUILTIN_FRAMEWORK,
        framework_version=STRATEGY_PROTOCOL_VERSION,
        model_provider=model_provider,
        model_name=model_name,
        deterministic=deterministic,
        visible_context=("incident_brief", "relation_whitelist"),
    )


class ProtocolBudget(BaseModel):
    """The frozen per-run budget the harness enforces for every strategy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_request_limit: Literal[8] = 8
    tool_call_limit: Literal[8] = 8
    output_retry_limit: Literal[2] = 2
    timeout_seconds: Literal[300] = 300


class StrategyDeclaration(BaseModel):
    """What a strategy must declare before it may open a session.

    The declaration is the comparison surface: two strategies may only be
    labelled a strict same-condition comparison when their declarations, the
    granted tool allowlist and the budget all match. Declaring an extra tool
    does not grant it — the harness only ever grants the six read-only tools.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    framework: StrictStr
    framework_version: StrictStr
    model_provider: StrictStr
    model_name: StrictStr
    deterministic: StrictBool = False
    extra_tools: tuple[StrictStr, ...] = ()
    visible_context: tuple[StrictStr, ...] = ()

    def comparison_identity(
        self, *, tool_allowlist: frozenset[str], budget: ProtocolBudget
    ) -> tuple[Any, ...]:
        return (
            self.framework,
            self.framework_version,
            self.model_provider,
            self.model_name,
            self.deterministic,
            tuple(sorted(tool_allowlist)),
            budget.model_dump(mode="json"),
            tuple(sorted(self.visible_context)),
        )


def same_condition(
    first: StrategySession,
    second: StrategySession,
) -> bool:
    """Whether two sessions may be compared as strict same-condition runs.

    Different framework, model, granted tools, budget or declared visible
    context means the strategies did not see the same problem under the same
    constraints, and a report must not label them a controlled comparison.
    """

    return first.comparison_identity() == second.comparison_identity()


class TaskContext(BaseModel):
    """The public task description; deliberately free of private facts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol_version: StrictStr
    run_id: StrictStr
    incident_brief: IncidentBrief
    observable_relations: dict[str, tuple[str, ...]]
    tool_allowlist: tuple[StrictStr, ...]
    budget: ProtocolBudget
    declaration: StrategyDeclaration


class ToolRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: StrictStr = Field(min_length=1)
    tool_name: StrictStr
    #: Raw arguments as the client sent them. Values are not coerced here: the
    #: session judges key sets and value types so that every argument problem
    #: becomes a receipt instead of a construction-time error.
    arguments: dict[StrictStr, Any]


class ProtocolError(BaseModel):
    """The single error envelope every refusal uses."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: StrictStr
    detail: StrictStr | None = None
    request_id: StrictStr | None = None


class ToolReceipt(BaseModel):
    """One tool call's public outcome, facts included.

    ``evidence`` carries the records this call returned — the strategy reads
    the facts from the receipt, so an external client can actually diagnose
    from public evidence instead of trusting a preset answer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: StrictStr
    accepted: StrictBool
    evidence_ids: tuple[StrictStr, ...] = ()
    evidence: tuple[EvidenceRecord, ...] = ()
    duplicate: StrictBool = False
    error: ProtocolError | None = None
    tool_calls_used: StrictInt = Field(ge=0)
    #: v2 batch tools only: the atomic refusal's per-target ``(target, code)``
    #: detail, in request order. Empty for v1 calls and successful calls; the
    #: call-level error code never witnesses a gap on its own.
    target_refusals: tuple[TargetRefusal, ...] = ()


class FinalSubmission(BaseModel):
    """A strategy's terminal answer; the harness owns the run id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DiagnosisStatus
    summary: NonBlankStr
    root_cause_code: RootCauseCode | None = None
    affected_assets: tuple[NonBlankStr, ...] = ()
    evidence_ids: tuple[StrictStr, ...] = ()
    claims: tuple[DiagnosisClaim, ...] = ()
    unresolved_evidence: tuple[UnresolvedEvidence, ...] = ()
    recommended_actions: tuple[NonBlankStr, ...] = ()
    confidence: Confidence


class FinalSubmissionV2(BaseModel):
    """The v2 terminal answer (T13): same shape, v2 gap vocabulary.

    A v2 surface must be able to declare expectation/definition gaps; the v1
    model and its schema stay byte-identical. The diagnosis built from this
    submission is a ``DiagnosisV2``; everything else validates identically.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DiagnosisStatus
    summary: NonBlankStr
    root_cause_code: RootCauseCode | None = None
    affected_assets: tuple[NonBlankStr, ...] = ()
    evidence_ids: tuple[StrictStr, ...] = ()
    claims: tuple[DiagnosisClaim, ...] = ()
    unresolved_evidence: tuple[UnresolvedEvidenceV2, ...] = ()
    recommended_actions: tuple[NonBlankStr, ...] = ()
    confidence: Confidence


class SubmissionReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: StrictBool
    diagnosis: Diagnosis | None = None
    error: ProtocolError | None = None


class CancellationReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: StrictBool
    reason: CancellationReason
    error: ProtocolError | None = None


_TOOL_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "get_dbt_run_results": ("run_id",),
    "get_dbt_node_error": ("run_id", "node_id"),
    "get_dbt_lineage": ("node_id", "direction"),
    "get_relation_schema": ("relation_name",),
    "get_relation_data_profile": ("relation_name",),
    "get_relation_history": ("relation_name",),
    # T13 v2 batch tools: the argument is the frozen comma-joined request
    # encoding, so the string-argument rule holds for them too. A v1 session
    # never grants them (the default allowlist stays the six tools).
    "get_relation_schema_expectation": ("relation_names",),
    "get_dbt_node_definition": ("node_ids",),
}


class StrategySession:
    """The harness side of the protocol: the only door to a run."""

    def __init__(
        self,
        *,
        run_id: str,
        tools: Any,
        context: ObservableRunContext,
        declaration: StrategyDeclaration,
        allowlist: frozenset[str] = PROTOCOL_TOOL_ALLOWLIST,
        budget: ProtocolBudget | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        unknown = sorted(set(declaration.extra_tools) - set(allowlist))
        if unknown:
            raise StrategyProtocolError(
                "DECLARATION_INVALID",
                detail=f"extra tools not granted: {', '.join(unknown)}",
            )
        self._run_id = run_id
        self._tools = tools
        self._context = context
        self._declaration = declaration
        self._allowlist = frozenset(allowlist)
        self._budget = budget or ProtocolBudget()
        self._clock = clock or monotonic
        self._opened_at = self._clock()
        self._registered: dict[str, EvidenceRecord] = {}
        self._attempts = 0
        self._receipts: list[ToolReceipt] = []
        self._final: Diagnosis | None = None
        self._cancellation: CancellationReason | None = None
        self._self_reported_usage: dict[str, Any] = {}
        self._last_records: tuple[EvidenceRecord, ...] = ()
        self._last_backend_error: BaseException | None = None
        self._model_requests_granted = 0
        self._output_retries_used = 0

    # -- identity ---------------------------------------------------------

    def comparison_identity(self) -> tuple[Any, ...]:
        return self._declaration.comparison_identity(
            tool_allowlist=self._allowlist, budget=self._budget
        )

    @property
    def declaration(self) -> StrategyDeclaration:
        return self._declaration

    @property
    def final_diagnosis(self) -> Diagnosis | None:
        return self._final

    @property
    def cancellation(self) -> CancellationReason | None:
        return self._cancellation

    def task_context(self) -> TaskContext:
        observable = {
            kind: tuple(sorted(relations))
            for kind, relations in (
                self._context.runtime.get("observable_relations", {}) or {}
            ).items()
        }
        return TaskContext(
            protocol_version=STRATEGY_PROTOCOL_VERSION,
            run_id=self._run_id,
            incident_brief=self._context.incident_brief,
            observable_relations=observable,
            tool_allowlist=tuple(sorted(self._allowlist)),
            budget=self._budget,
            declaration=self._declaration,
        )

    # -- operations -------------------------------------------------------

    def call_tool(self, request: ToolRequest) -> ToolReceipt:
        def receipt(
            *,
            accepted: bool,
            evidence_ids: tuple[str, ...] = (),
            evidence: tuple[EvidenceRecord, ...] = (),
            duplicate: bool = False,
            code: str | None = None,
            detail: str | None = None,
            target_refusals: tuple[TargetRefusal, ...] = (),
        ) -> ToolReceipt:
            error = (
                ProtocolError(code=code, detail=detail, request_id=request.request_id)
                if code
                else None
            )
            return ToolReceipt(
                request_id=request.request_id,
                accepted=accepted,
                evidence_ids=evidence_ids,
                evidence=evidence,
                duplicate=duplicate,
                error=error,
                tool_calls_used=self._attempts,
                target_refusals=target_refusals,
            )

        if self._final is not None or self._cancellation is not None:
            return receipt(accepted=False, code="SESSION_CLOSED")
        if self._deadline_exceeded():
            return receipt(accepted=False, code="DEADLINE_EXCEEDED")
        if request.tool_name not in _TOOL_ARGUMENTS:
            return receipt(accepted=False, code="UNKNOWN_TOOL")
        if request.tool_name not in self._allowlist:
            return receipt(accepted=False, code="TOOL_NOT_ALLOWLISTED")
        self._attempts += 1
        if self._attempts > self._budget.tool_call_limit:
            return receipt(accepted=False, code="TOOL_BUDGET_EXHAUSTED")
        expected = _TOOL_ARGUMENTS[request.tool_name]
        if set(request.arguments) != set(expected) or any(
            not isinstance(request.arguments[name], str) for name in expected
        ):
            return receipt(
                accepted=False,
                code="TOOL_ARGUMENT_INVALID",
                detail=f"expected arguments {', '.join(expected)}",
            )
        for name in ("run_id",):
            if name in expected and request.arguments[name] != self._run_id:
                return receipt(
                    accepted=False,
                    code="TOOL_ARGUMENT_INVALID",
                    detail="run-scoped argument must match this session's run",
                )
        self._last_backend_error = None
        self._last_records = ()
        try:
            records = self._dispatch(request.tool_name, request.arguments)
        except EvidenceToolError as error:
            # The refusal is the receipt: keep the backend's real error code and,
            # for v2 batch tools, the authoritative per-target detail.
            self._last_backend_error = error
            detailed = tuple(
                TargetRefusal(target=target, code=code)
                for target, code in getattr(error, "target_refusals", ())
            )
            result = receipt(
                accepted=False, code=safe_error_code(error), target_refusals=detailed
            )
        except Exception:
            self._last_backend_error = None
            result = receipt(accepted=False, code="TOOL_BACKEND_ERROR")
        else:
            self._last_records = tuple(records)
            duplicate = False
            for record in records:
                if record.evidence_id in self._registered:
                    duplicate = True
                else:
                    self._registered[record.evidence_id] = record
            result = receipt(
                accepted=True,
                evidence_ids=tuple(record.evidence_id for record in records),
                evidence=tuple(records),
                duplicate=duplicate,
            )
        self._receipts.append(result)
        return result

    def _dispatch(self, tool_name: str, arguments: dict[str, str]) -> tuple[EvidenceRecord, ...]:
        if tool_name == "get_dbt_run_results":
            return self._tools.get_dbt_run_results(arguments["run_id"])
        if tool_name == "get_dbt_node_error":
            return self._tools.get_dbt_node_error(arguments["run_id"], arguments["node_id"])
        if tool_name == "get_dbt_lineage":
            return self._tools.get_dbt_lineage(arguments["node_id"], arguments["direction"])
        if tool_name == "get_relation_schema":
            return self._tools.get_relation_schema(arguments["relation_name"])
        if tool_name == "get_relation_data_profile":
            return self._tools.get_relation_data_profile(arguments["relation_name"])
        if tool_name == "get_relation_history":
            return self._tools.get_relation_history(arguments["relation_name"])
        if tool_name == "get_relation_schema_expectation":
            return self._tools.get_relation_schema_expectation(arguments["relation_names"])
        return self._tools.get_dbt_node_definition(arguments["node_ids"])

    def acquire_model_request(self) -> ProtocolError | None:
        """Grant one model-request slot, or explain the refusal.

        A client that calls a model must draw its slot here first; the harness
        cannot observe a foreign process's calls, so the budget is enforced at
        the acquisition gate and self-reported usage is cross-checked against
        the granted slots (see ``snapshot``).
        """

        if self._final is not None or self._cancellation is not None:
            return ProtocolError(code="SESSION_CLOSED")
        if self._deadline_exceeded():
            return ProtocolError(code="DEADLINE_EXCEEDED")
        if self._model_requests_granted >= self._budget.model_request_limit:
            return ProtocolError(code="MODEL_REQUEST_BUDGET_EXHAUSTED")
        self._model_requests_granted += 1
        return None

    def _deadline_exceeded(self) -> bool:
        return (self._clock() - self._opened_at) > self._budget.timeout_seconds

    def submit(self, submission: FinalSubmission | FinalSubmissionV2) -> SubmissionReceipt:
        if self._cancellation is not None:
            return SubmissionReceipt(
                accepted=False,
                error=ProtocolError(code="SESSION_CLOSED", detail="session was cancelled"),
            )
        if self._final is not None:
            return SubmissionReceipt(
                accepted=False, error=ProtocolError(code="SUBMISSION_ALREADY_FINAL")
            )
        if self._deadline_exceeded():
            return SubmissionReceipt(accepted=False, error=ProtocolError(code="DEADLINE_EXCEEDED"))
        if self._output_retries_used >= self._budget.output_retry_limit:
            return SubmissionReceipt(
                accepted=False,
                error=ProtocolError(
                    code="OUTPUT_RETRY_EXHAUSTED",
                    detail="the retry budget is spent; cancel the run or stop",
                ),
            )
        cited = set(submission.evidence_ids)
        cited.update(
            evidence_id for claim in submission.claims for evidence_id in claim.evidence_ids
        )
        unregistered = sorted(cited - set(self._registered))
        if unregistered:
            self._output_retries_used += 1
            return SubmissionReceipt(
                accepted=False,
                error=ProtocolError(
                    code="EVIDENCE_NOT_REGISTERED",
                    detail=f"citations not returned by any tool call: {', '.join(unregistered)}",
                ),
            )
        try:
            diagnosis_type = DiagnosisV2 if isinstance(submission, FinalSubmissionV2) else Diagnosis
            diagnosis = diagnosis_type(
                run_id=self._run_id,
                status=submission.status,
                summary=submission.summary,
                root_cause_code=submission.root_cause_code,
                affected_assets=submission.affected_assets,
                evidence_ids=submission.evidence_ids,
                claims=submission.claims,
                unresolved_evidence=submission.unresolved_evidence,
                recommended_actions=submission.recommended_actions,
                confidence=submission.confidence,
            )
        except Exception as exc:
            self._output_retries_used += 1
            return SubmissionReceipt(
                accepted=False,
                error=ProtocolError(code="SUBMISSION_INVALID", detail=str(exc).splitlines()[0]),
            )
        self._final = diagnosis
        return SubmissionReceipt(accepted=True, diagnosis=diagnosis)

    def cancel(self, reason: CancellationReason) -> CancellationReceipt:
        if self._final is not None:
            return CancellationReceipt(
                accepted=False,
                reason=reason,
                error=ProtocolError(code="SESSION_CLOSED", detail="session already final"),
            )
        self._cancellation = reason
        return CancellationReceipt(accepted=True, reason=reason)

    def report_usage(self, **usage: Any) -> None:
        """Record self-reported counters; the harness counts remain the truth."""

        self._self_reported_usage.update(usage)

    # -- introspection ----------------------------------------------------

    def registered_evidence_ids(self) -> tuple[str, ...]:
        return tuple(self._registered)

    def registered_evidence(self) -> tuple[EvidenceRecord, ...]:
        """The evidence records registered so far, in registration order."""

        return tuple(self._registered.values())

    def snapshot(self) -> dict[str, Any]:
        reported_requests = self._self_reported_usage.get("model_requests")
        usage_violation = (
            isinstance(reported_requests, int)
            and reported_requests > self._model_requests_granted
        )
        return {
            "protocol_version": STRATEGY_PROTOCOL_VERSION,
            "run_id": self._run_id,
            "tool_call_attempts": self._attempts,
            "model_requests_granted": self._model_requests_granted,
            "output_retries_used": self._output_retries_used,
            "elapsed_seconds": round(self._clock() - self._opened_at, 6),
            "deadline_seconds": self._budget.timeout_seconds,
            "usage_violation": usage_violation,
            "accepted_tool_calls": sum(receipt.accepted for receipt in self._receipts),
            "refusals_by_code": {
                code: sum(
                    receipt.error is not None and receipt.error.code == code
                    for receipt in self._receipts
                )
                for code in sorted(
                    {receipt.error.code for receipt in self._receipts if receipt.error}
                )
            },
            "registered_evidence": len(self._registered),
            "submitted": self._final is not None,
            "cancelled": self._cancellation,
            "self_reported_usage": dict(self._self_reported_usage),
            "note": "harness counters are authoritative; self-reported usage is auxiliary",
        }

    def tools_facade(self) -> ProtocolTools:
        """A drop-in ``EvidenceTools`` facade for a built-in runner."""

        return ProtocolTools(self)


class ProtocolTools:
    """Route a built-in runner's six tool calls through a protocol session.

    The facade is transparent for compliant runners: accepted calls return the
    same records, and backend refusals re-raise the original exception so the
    runner's own error classification is unchanged. Protocol-level rejections
    (allowlist, budget, arguments) raise ``StrategyProtocolError`` — a runner
    that stays inside its own limits never triggers them.
    """

    def __init__(self, session: StrategySession) -> None:
        self._session = session

    def _call(self, tool_name: str, arguments: dict[str, str]) -> tuple[EvidenceRecord, ...]:
        request = ToolRequest(
            request_id=f"{tool_name}:{self._session._attempts + 1}",
            tool_name=tool_name,
            arguments=arguments,
        )
        receipt = self._session.call_tool(request)
        if receipt.accepted:
            return self._session._last_records  # noqa: SLF001 - facade seam
        error = receipt.error
        backend_error = self._session._last_backend_error  # noqa: SLF001 - facade seam
        if (
            backend_error is not None
            and error is not None
            and error.code == safe_error_code(backend_error)
        ):
            raise backend_error
        raise StrategyProtocolError(
            error.code if error else "TOOL_BACKEND_ERROR", detail=getattr(error, "detail", None)
        )

    def get_dbt_run_results(self, run_id: str) -> tuple[EvidenceRecord, ...]:
        return self._call("get_dbt_run_results", {"run_id": run_id})

    def get_dbt_node_error(self, run_id: str, node_id: str) -> tuple[EvidenceRecord, ...]:
        return self._call("get_dbt_node_error", {"run_id": run_id, "node_id": node_id})

    def get_dbt_lineage(self, node_id: str, direction: str) -> tuple[EvidenceRecord, ...]:
        return self._call("get_dbt_lineage", {"node_id": node_id, "direction": direction})

    def get_relation_schema(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        return self._call("get_relation_schema", {"relation_name": relation_name})

    def get_relation_data_profile(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        return self._call("get_relation_data_profile", {"relation_name": relation_name})

    def get_relation_history(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        return self._call("get_relation_history", {"relation_name": relation_name})

    def get_relation_schema_expectation(self, relation_names: str) -> tuple[EvidenceRecord, ...]:
        return self._call(
            "get_relation_schema_expectation", {"relation_names": relation_names}
        )

    def get_dbt_node_definition(self, node_ids: str) -> tuple[EvidenceRecord, ...]:
        return self._call("get_dbt_node_definition", {"node_ids": node_ids})


__all__ = [
    "BUILTIN_FRAMEWORK",
    "PROTOCOL_ERROR_CODES",
    "EVIDENCE_V2_TOOL_ALLOWLIST",
    "PROTOCOL_TOOL_ALLOWLIST",
    "STRATEGY_PROTOCOL_VERSION",
    "tool_allowlist_for_context",
    "CancellationReceipt",
    "FinalSubmission",
    "FinalSubmissionV2",
    "ProtocolBudget",
    "ProtocolError",
    "ProtocolTools",
    "StrategyDeclaration",
    "StrategyProtocolError",
    "StrategySession",
    "SubmissionReceipt",
    "builtin_declaration",
    "TaskContext",
    "ToolReceipt",
    "ToolRequest",
    "same_condition",
]
