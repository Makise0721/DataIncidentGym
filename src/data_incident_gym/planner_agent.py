"""Planner runner (T12): the model drives the plan layer through an agent.

The model never touches an evidence tool directly. It declares intent with the
two action tools (``plan_step``, ``close_obligation``), each answer carrying the
real receipt or the ``PlanVerdict`` back into the conversation, and it ends the
run with the single terminal output tool (``submit_diagnosis``). Everything
between those calls lives in ``evidence_planner`` — this module only assembles
the agent, guards the run (sequential tools, deadline, usage limits) and turns
the session state into a ``DiagnosisRunResult``.

Fail-closed rules, shared with the built-in runners:

- a submitted diagnosis is relayed through ``session.submit``; if the protocol
  refuses it the run becomes ``MODEL_ERROR`` rather than a deliverable answer;
- timeout, usage limits, protocol errors and unexpected failures close the
  session with ``cancel`` and end in ``MODEL_ERROR`` with a fixed reason code;
- traces are built from the plan ledger (one ``ToolTraceEvent`` per executed
  step) plus the terminal event; the session's counters stay authoritative.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

from openai import AsyncOpenAI
from pydantic_ai import Agent, ModelRetry, RunContext, RunUsage, UsageLimits
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.diagnosis import (
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    EvidenceGateTraceEvent,
    ModelProtocolTraceEvent,
    PlanObligationRecord,
    PlanTraceEvent,
    ToolTraceEvent,
)
from data_incident_gym.diagnostic_agent import (
    MODEL_REQUEST_LIMIT,
    OUTPUT_RETRY_LIMIT,
    PLANNER_PROMPT,
    TIMEOUT_SECONDS,
    ModelIdentity,
    _transport_diagnostic,
    load_base_prompt,
)
from data_incident_gym.diagnostic_config import (
    DiagnosticSettings,
    openai_compatibility_kwargs,
)
from data_incident_gym.evidence import EvidenceRecord
from data_incident_gym.evidence_planner import (
    PlannerController,
    PlannerDeps,
    evidence_planner_policy_identity,
    planner_output_definition,
    register_planner_tools,
)
from data_incident_gym.evidence_tools import EvidenceTools
from data_incident_gym.fixed_rule import tool_surface_for_context
from data_incident_gym.run_context import ObservableRunContext, resolve_run_context
from data_incident_gym.strategy_adapter import (
    FinalSubmission,
    FinalSubmissionV2,
    StrategySession,
    builtin_declaration,
    tool_allowlist_for_context,
)
from data_incident_gym.submission_policy import SubmissionPolicy

PLANNER_STRATEGY = DiagnosticStrategy.EVIDENCE_PLANNER

#: The fixed reason codes a MODEL_ERROR terminal may carry.
MODEL_ERROR_REASONS = frozenset(
    {
        "MODEL_DECLINED",
        "MODEL_REQUEST_LIMIT",
        "MODEL_TOOL_CALL_LIMIT",
        "MODEL_TIMEOUT",
        "MODEL_PROTOCOL_ERROR",
        "MODEL_OUTPUT_RETRY_EXHAUSTED",
        "MODEL_RUNTIME_ERROR",
        "RUN_SETUP_ERROR",
    }
)


def _fingerprint(run_id: str, tool_name: str, arguments: dict[str, Any]) -> str:
    payload = {"arguments": arguments, "run_id": run_id, "tool_name": tool_name}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _user_prompt(context: ObservableRunContext) -> str:
    payload = {
        "run_id": context.run_id,
        "incident_brief": context.incident_brief.model_dump(mode="json"),
        "observable_relations": context.runtime.get("observable_relations", {}),
    }
    # The v2 node-definition whitelist is part of the model-visible context;
    # a v1 runtime has no such key and its prompt stays byte-identical.
    observable_nodes = context.runtime.get("observable_nodes") or {}
    if observable_nodes:
        payload["observable_nodes"] = observable_nodes
    return (
        "Investigate the verified run using the public run-bound context below. "
        "Declare every evidence request with `plan_step` before it is executed, close each "
        "obligation with `close_obligation`, and submit with `submit_diagnosis`.\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


class EvidencePlannerRunner:
    """One planner run: agent assembly, guarded execution, result assembly."""

    def __init__(
        self,
        *,
        run_id: str,
        context: ObservableRunContext,
        session: StrategySession,
        model: Model | None,
        model_identity: ModelIdentity,
        owned_model_client: AsyncOpenAI | None = None,
        submission_policy: SubmissionPolicy | None = None,
    ) -> None:
        self._run_id = run_id
        self._context = context
        self._session = session
        self._submission_policy = submission_policy
        self._controller = PlannerController(session)
        self._model = model
        self._model_identity = model_identity
        self._owned_model_client = owned_model_client
        self._policy_identity = evidence_planner_policy_identity(
            tool_surface_for_context(context)
        )
        self._accepted: Diagnosis | None = None

    @classmethod
    def for_run(
        cls,
        run_id: str,
        settings: DiagnosticSettings,
        project_root: Path = PROJECT_ROOT,
        *,
        model: Model | None = None,
        model_identity: ModelIdentity | None = None,
        backend: Any | None = None,
        session: StrategySession | None = None,
        submission_policy: SubmissionPolicy | None = None,
    ) -> EvidencePlannerRunner:
        """Build a planner run; ``backend``/``session`` are for deterministic tests.

        Without an injected model the run uses the settings' OpenAI-compatible
        endpoint and owns (and closes) the client itself, mirroring
        ``DiagnosisRunner.for_run``.
        """

        context = resolve_run_context(run_id, project_root=project_root)
        owned_model_client: AsyncOpenAI | None = None
        if model is None:
            client = AsyncOpenAI(
                base_url=str(settings.model_base_url),
                api_key=settings.model_api_key.get_secret_value(),
                max_retries=0,
            )
            model = OpenAIChatModel(
                settings.model_name, provider=OpenAIProvider(openai_client=client),
                **openai_compatibility_kwargs(settings),
            )
            model_identity = ModelIdentity("openai-compatible", settings.model_name)
            owned_model_client = client
        elif model_identity is None:
            raise ValueError("model_identity is required when injecting a model")
        if session is None:
            tools = backend if backend is not None else EvidenceTools.for_run(
                run_id, settings, project_root=project_root
            )
            session = StrategySession(
                run_id=run_id,
                tools=tools,
                context=context,
                declaration=builtin_declaration(
                    model_provider=model_identity.provider,
                    model_name=model_identity.model,
                    deterministic=False,
                ),
                allowlist=tool_allowlist_for_context(context),
            )
        return cls(
            run_id=run_id,
            context=context,
            session=session,
            submission_policy=submission_policy,
            model=model,
            model_identity=model_identity,
            owned_model_client=owned_model_client,
        )

    # -- agent -------------------------------------------------------------

    def _agent(self, deps: PlannerDeps) -> Agent[PlannerDeps, Any]:
        # The output tool carries the run's gap vocabulary: a v2 run's model
        # submits through FinalSubmissionV2, so what it is offered matches what
        # the session accepts.
        surface = tool_surface_for_context(self._context)
        agent: Agent[PlannerDeps, Any] = Agent(
            self._model,
            deps_type=PlannerDeps,
            output_type=planner_output_definition(surface),
            system_prompt=f"{load_base_prompt()}\n\n{PLANNER_PROMPT}",
            retries={"tools": 1, "output": OUTPUT_RETRY_LIMIT},
        )
        register_planner_tools(agent)

        @agent.output_validator
        def validate_submission(
            ctx: RunContext[PlannerDeps],
            submission: FinalSubmission | FinalSubmissionV2,
        ) -> FinalSubmission | FinalSubmissionV2:
            """The session judges the submission here, not after the run.

            A refusal is a retryable answer: the model gets the code back and may
            cite different evidence or conclude differently, bounded by the
            session's own submission-refusal budget.
            """

            ctx.deps.last_refusal_was_gate = False
            refusal = None
            policy = self._submission_policy
            if policy is not None:
                refusal = policy.check(
                    submission,
                    records=self._session.registered_evidence(),
                    trace=self._tool_trace_events(),
                )
            if refusal is not None:
                ctx.deps.last_refusal_was_gate = True
                ctx.deps.gate_refusals.append({"code": refusal.code})
                raise ModelRetry(f"{refusal.code}: {refusal.message}")
            receipt = self._session.submit(submission)
            if receipt.accepted and receipt.diagnosis is not None:
                self._accepted = receipt.diagnosis
                return submission
            error = receipt.error
            code = "SUBMISSION_INVALID" if error is None else error.code
            detail = "" if error is None or error.detail is None else f" ({error.detail})"
            snapshot = self._session.snapshot()
            budget = self._session.task_context().budget
            raise ModelRetry(
                f"the harness refused this submission [{code}]{detail}. "
                f"Refused submissions: {snapshot['output_retries_used']}"
                f"/{budget.output_retry_limit}. "
                "Cite only evidence the tools returned, then submit again."
            )

        return agent

    # -- run ---------------------------------------------------------------

    async def diagnose(self) -> DiagnosisRunResult:
        try:
            return await self._diagnose_guarded()
        finally:
            if self._owned_model_client is not None:
                with suppress(Exception):
                    await self._owned_model_client.close()

    async def _diagnose_guarded(self) -> DiagnosisRunResult:
        deps = PlannerDeps(controller=self._controller)
        started_at = datetime.now(UTC)
        started = monotonic()
        # One accumulator for every exit path: a failed run still reports what it
        # spent instead of showing zeros it did not earn.
        usage = RunUsage()
        self._accepted = None
        try:
            agent = self._agent(deps)
            with agent.parallel_tool_call_execution_mode("sequential"):
                async with asyncio.timeout(TIMEOUT_SECONDS):
                    await agent.run(
                        _user_prompt(self._context),
                        deps=deps,
                        usage=usage,
                        # Model turns are guarded here; the evidence-call budget is
                        # enforced by the plan layer, which answers with a verdict
                        # instead of killing the run (an SDK-side tool limit would
                        # preempt PLAN_TOOL_BUDGET_EXHAUSTED and hide the refusal).
                        usage_limits=UsageLimits(request_limit=MODEL_REQUEST_LIMIT),
                    )
        except TimeoutError:
            # The run-level watchdog, not a provider transport fact: the
            # terminal carries no transport diagnostic (proposal §2.4 — never
            # guess transport from a timeout summary alone).
            return self._terminal("MODEL_TIMEOUT", started_at, started, deps, usage)
        except UsageLimitExceeded as error:
            return self._terminal(_usage_limit_reason(error), started_at, started, deps, usage)
        except UnexpectedModelBehavior as error:
            reason = (
                "MODEL_OUTPUT_RETRY_EXHAUSTED"
                if deps.last_refusal_was_gate
                else "MODEL_PROTOCOL_ERROR"
            )
            return self._terminal(
                reason, started_at, started, deps, usage, provider_error=error
            )
        except Exception as error:
            return self._terminal(
                "MODEL_RUNTIME_ERROR", started_at, started, deps, usage, provider_error=error
            )

        if self._accepted is None:
            # The run ended without the validator ever accepting a submission.
            return self._terminal("MODEL_PROTOCOL_ERROR", started_at, started, deps, usage)
        return self._result(self._accepted, started_at, started, deps, usage)

    def _terminal(
        self,
        reason: str,
        started_at: datetime,
        started: float,
        deps: PlannerDeps,
        usage: RunUsage,
        provider_error: BaseException | None = None,
    ) -> DiagnosisRunResult:
        if reason not in MODEL_ERROR_REASONS:
            reason = "MODEL_RUNTIME_ERROR"
        if self._session.cancellation is None and self._session.final_diagnosis is None:
            self._session.cancel("STRATEGY_TIMEOUT" if reason == "MODEL_TIMEOUT" else "RUN_FAILED")
        diagnosis = Diagnosis(
            status=DiagnosisStatus.MODEL_ERROR,
            run_id=self._run_id,
            summary=reason,
            confidence=0.0,
        )
        return self._result(
            diagnosis, started_at, started, deps, usage, provider_error=provider_error
        )

    def _tool_trace_events(self) -> tuple[ToolTraceEvent, ...]:
        """The live tool trace, shared by the gate and the archived result."""

        return tuple(
            ToolTraceEvent(
                event_type="TOOL_CALL",
                tool_name=step["tool_name"],
                arguments=dict(step["arguments"]),
                fingerprint=_fingerprint(self._run_id, step["tool_name"], step["arguments"]),
                evidence_ids=tuple(step["evidence_ids"]),
                error_code=step["error_code"],
                target_refusals=tuple(step.get("target_refusals", ())),
                elapsed_ms=step["elapsed_ms"],
            )
            for step in self._controller.step_records()
        )

    def _result(
        self,
        diagnosis: Diagnosis,
        started_at: datetime,
        started: float,
        deps: PlannerDeps,
        usage: RunUsage,
        provider_error: BaseException | None = None,
    ) -> DiagnosisRunResult:
        del started_at
        records: tuple[EvidenceRecord, ...] = self._session.registered_evidence()
        trace: list[Any] = list(self._tool_trace_events())
        plan_snapshot = self._controller.snapshot()
        trace.extend(_plan_events(deps))
        # Sanitized transport classification for provider-origin failures only
        # (M23 vocabulary; no exception text/headers/bodies). Non-classifiable
        # failures add no protocol event, so nothing is guessed from counts.
        if provider_error is not None:
            transport = _transport_diagnostic(provider_error)
            if transport is not None:
                error_name = type(provider_error).__name__
                trace.append(
                    ModelProtocolTraceEvent(
                        event_type="MODEL_PROTOCOL",
                        stage="PROVIDER_RESPONSE",
                        tool_name=None,
                        category="PROVIDER_PROTOCOL_FAILURE",
                        error_reason=(diagnosis.summary,) if diagnosis.summary else (),
                        model_request_index=int(usage.requests),
                        error_type=(
                            "MODEL_API_ERROR"
                            if error_name in ("ModelHTTPError", "ModelAPIError")
                            else "OTHER"
                        ),
                        error_origin="PROVIDER",
                        transport_diagnostic=transport,
                    )
                )
        trace.append(
            PlanTraceEvent(
                kind="STATE",
                obligations=tuple(
                    PlanObligationRecord(
                        obligation_id=item.obligation_id,
                        status=item.status,
                        evidence_kind=item.evidence_kind,
                        subject=item.subject,
                        satisfied_with=item.satisfied_with,
                        close_reason=item.close_reason,
                    )
                    for item in self._controller.obligations()
                ),
                open_obligations=tuple(plan_snapshot["obligations_open"]),
            )
        )
        trace.append(
            DiagnosisTerminalTraceEvent(
                event_type="DIAGNOSIS_TERMINAL",
                strategy=PLANNER_STRATEGY,
                status=diagnosis.status,
                evidence_inventory=tuple(record.evidence_id for record in records),
            )
        )
        session_snapshot = self._session.snapshot()
        metrics = DiagnosisMetrics(
            provider=self._model_identity.provider,
            model=self._model_identity.model,
            model_requests=int(usage.requests),
            input_tokens=int(usage.input_tokens),
            output_tokens=int(usage.output_tokens),
            tool_call_attempts=session_snapshot["tool_call_attempts"],
            successful_tool_calls=session_snapshot["accepted_tool_calls"],
            elapsed_ms=max(0, int((monotonic() - started) * 1000)),
        )
        return DiagnosisRunResult(
            strategy=PLANNER_STRATEGY,
            policy_identity=self._policy_identity,
            diagnosis=diagnosis,
            evidence_records=records,
            trace=tuple(trace),
            metrics=metrics,
            kernel_state=None,
        )

    @property
    def controller(self) -> PlannerController:
        return self._controller

    @property
    def session(self) -> StrategySession:
        return self._session

    @property
    def model_identity(self) -> ModelIdentity:
        return self._model_identity


def _plan_events(deps: PlannerDeps) -> list[Any]:
    """One plan event per declared step/close, in the order the model made them.

    Submission-gate refusals are rendered first, as EVIDENCE_GATE events with
    their fixed codes, so archives name why a submission was refused.
    """

    events: list[Any] = []
    for turn in deps.turns:
        is_step = turn["tool"] == "plan_step"
        events.append(
            PlanTraceEvent(
                kind="STEP" if is_step else "CLOSE",
                accepted=bool(turn["accepted"]),
                verdict_code=turn.get("verdict_code"),
                obligation_id=turn.get("obligation_id"),
                tool_name=turn.get("tool_name") if is_step else None,
                plan_refusals_used=int(turn.get("plan_refusals_used", 0)),
                plan_refusal_limit=int(turn.get("plan_refusal_limit", 0)),
                tool_calls_used=int(turn.get("tool_calls_used", 0)),
                requested_outcome=None if is_step else turn.get("requested_outcome"),
                evidence_ids=() if is_step else tuple(turn.get("evidence_ids") or ()),
                reason=None if is_step else turn.get("reason"),
            )
        )
    # Gate refusals happen when the model submits, i.e. after its action
    # turns; keep the archive in chronological order.
    events.extend(
        EvidenceGateTraceEvent(
            event_type="EVIDENCE_GATE",
            reason_code=refusal["code"],
            accepted=False,
        )
        for refusal in deps.gate_refusals
    )
    return events


def _usage_limit_reason(error: UsageLimitExceeded) -> str:
    text = str(error)
    if "tool_calls" in text or "tool calls" in text:
        return "MODEL_TOOL_CALL_LIMIT"
    return "MODEL_REQUEST_LIMIT"


__all__ = ["EvidencePlannerRunner", "MODEL_ERROR_REASONS", "PLANNER_STRATEGY"]
