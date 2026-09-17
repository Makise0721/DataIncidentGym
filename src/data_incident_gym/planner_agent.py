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
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

from pydantic_ai import Agent, ModelRetry, RunContext, RunUsage, UsageLimits
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.models import Model

from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.diagnosis import (
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    PlanTraceEvent,
    ToolTraceEvent,
)
from data_incident_gym.diagnostic_agent import (
    MODEL_REQUEST_LIMIT,
    OUTPUT_RETRY_LIMIT,
    PLANNER_PROMPT,
    TIMEOUT_SECONDS,
    ModelIdentity,
    load_base_prompt,
)
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evidence import EvidenceRecord
from data_incident_gym.evidence_planner import (
    PlannerController,
    PlannerDeps,
    evidence_planner_policy_identity,
    planner_output_definition,
    register_planner_tools,
)
from data_incident_gym.evidence_tools import EvidenceTools
from data_incident_gym.run_context import ObservableRunContext, resolve_run_context
from data_incident_gym.strategy_adapter import (
    FinalSubmission,
    StrategySession,
    builtin_declaration,
)

PLANNER_STRATEGY = DiagnosticStrategy.EVIDENCE_PLANNER

#: The fixed reason codes a MODEL_ERROR terminal may carry.
MODEL_ERROR_REASONS = frozenset(
    {
        "MODEL_DECLINED",
        "MODEL_REQUEST_LIMIT",
        "MODEL_TOOL_CALL_LIMIT",
        "MODEL_TIMEOUT",
        "MODEL_PROTOCOL_ERROR",
        "MODEL_RUNTIME_ERROR",
        "RUN_SETUP_ERROR",
    }
)


def _fingerprint(run_id: str, tool_name: str, arguments: dict[str, Any]) -> str:
    payload = {"arguments": arguments, "run_id": run_id, "tool_name": tool_name}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _user_prompt(context: ObservableRunContext) -> str:
    return (
        "Investigate the verified run using the public run-bound context below. "
        "Declare every evidence request with `plan_step` before it is executed, close each "
        "obligation with `close_obligation`, and submit with `submit_diagnosis`.\n"
        + json.dumps(
            {
                "run_id": context.run_id,
                "incident_brief": context.incident_brief.model_dump(mode="json"),
                "observable_relations": context.runtime.get("observable_relations", {}),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
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
    ) -> None:
        self._run_id = run_id
        self._context = context
        self._session = session
        self._controller = PlannerController(session)
        self._model = model
        self._model_identity = model_identity
        self._policy_identity = evidence_planner_policy_identity()
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
    ) -> EvidencePlannerRunner:
        """Build a planner run; ``backend``/``session`` are for deterministic tests."""

        context = resolve_run_context(run_id, project_root=project_root)
        if session is None:
            tools = backend if backend is not None else EvidenceTools.for_run(
                run_id, settings, project_root=project_root
            )
            identity = model_identity or ModelIdentity("none", "deterministic")
            session = StrategySession(
                run_id=run_id,
                tools=tools,
                context=context,
                declaration=builtin_declaration(
                    model_provider=identity.provider,
                    model_name=identity.model,
                    deterministic=model is None,
                ),
            )
        if model is None and model_identity is None:
            raise ValueError("model_identity is required when injecting a model")
        return cls(
            run_id=run_id,
            context=context,
            session=session,
            model=model,
            model_identity=model_identity or ModelIdentity("none", "deterministic"),
        )

    # -- agent -------------------------------------------------------------

    def _agent(self, deps: PlannerDeps) -> Agent[PlannerDeps, Any]:
        agent: Agent[PlannerDeps, Any] = Agent(
            self._model,
            deps_type=PlannerDeps,
            output_type=planner_output_definition(),
            system_prompt=f"{load_base_prompt()}\n\n{PLANNER_PROMPT}",
            retries={"tools": 1, "output": OUTPUT_RETRY_LIMIT},
        )
        register_planner_tools(agent)

        @agent.output_validator
        def validate_submission(
            ctx: RunContext[PlannerDeps], submission: FinalSubmission
        ) -> FinalSubmission:
            """The session judges the submission here, not after the run.

            A refusal is a retryable answer: the model gets the code back and may
            cite different evidence or conclude differently, bounded by the
            session's own submission-refusal budget.
            """

            del ctx
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
            return self._terminal("MODEL_TIMEOUT", started_at, started, deps, usage)
        except UsageLimitExceeded as error:
            return self._terminal(_usage_limit_reason(error), started_at, started, deps, usage)
        except UnexpectedModelBehavior:
            return self._terminal("MODEL_PROTOCOL_ERROR", started_at, started, deps, usage)
        except Exception:
            return self._terminal("MODEL_RUNTIME_ERROR", started_at, started, deps, usage)

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
        return self._result(diagnosis, started_at, started, deps, usage)

    def _result(
        self,
        diagnosis: Diagnosis,
        started_at: datetime,
        started: float,
        deps: PlannerDeps,
        usage: RunUsage,
    ) -> DiagnosisRunResult:
        del started_at
        records: tuple[EvidenceRecord, ...] = self._session.registered_evidence()
        trace: list[Any] = [
            ToolTraceEvent(
                event_type="TOOL_CALL",
                tool_name=step["tool_name"],
                arguments=dict(step["arguments"]),
                fingerprint=_fingerprint(self._run_id, step["tool_name"], step["arguments"]),
                evidence_ids=tuple(step["evidence_ids"]),
                error_code=step["error_code"],
                elapsed_ms=step["elapsed_ms"],
            )
            for step in self._controller.step_records()
        ]
        plan_snapshot = self._controller.snapshot()
        trace.extend(_plan_events(deps))
        trace.append(
            PlanTraceEvent(kind="STATE", open_obligations=tuple(plan_snapshot["obligations_open"]))
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


def _plan_events(deps: PlannerDeps) -> list[PlanTraceEvent]:
    """One plan event per declared step/close, in the order the model made them."""

    events: list[PlanTraceEvent] = []
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
            )
        )
    return events


def _usage_limit_reason(error: UsageLimitExceeded) -> str:
    text = str(error)
    if "tool_calls" in text or "tool calls" in text:
        return "MODEL_TOOL_CALL_LIMIT"
    return "MODEL_REQUEST_LIMIT"


__all__ = ["EvidencePlannerRunner", "MODEL_ERROR_REASONS", "PLANNER_STRATEGY"]
