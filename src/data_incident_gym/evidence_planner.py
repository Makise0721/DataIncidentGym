"""Public-evidence obligation planner (T12): the deterministic plan layer.

The planner strategy makes the model declare *why* it wants evidence before it
gets any: a ``plan_step`` names one read-only tool, its arguments and a public
intent. This module is the deterministic half — it validates that declaration
against public rules, executes the accepted step through the harness session and
keeps the obligation ledger. The model keeps every judgement call: which
obligations to pursue, what a receipt means, whether to replan after a refusal
and what the final answer is.

Two surfaces are deliberately kept apart:

- ``ToolReceipt`` — the real outcome of a real call: backend error code and the
  evidence records it returned. Only ever produced after an executed call.
- ``PlanVerdict`` — the outcome of validating a *declaration*. It carries a
  ``PLAN_*`` code, never an evidence record and never a backend code, and a
  validation refusal consumes no tool-call attempt.

An obligation's identity covers **all effective arguments**
(``<tool>:<canonical(arguments)>``, the arguments as canonical JSON), so
``get_dbt_lineage`` upstream and downstream are two obligations and closing one
never closes the other; JSON escaping also means two different argument sets can
never collide onto one id. Its ``evidence_kind`` and ``subject`` are display
fields only.

The validation order is fixed: terminal state, deadline, plan-refusal budget,
then the specific rules (tool name, arguments, outcome, run scope, obligation
state, tool budget). The budget is checked early on purpose — once it is spent
every later operation is refused with ``PLAN_OUTPUT_RETRY_EXHAUSTED`` instead of
yielding yet another specific code.

``SATISFIED`` needs all four of: the obligation has a last executed call, that
call was accepted, it returned non-empty evidence, and the closing declaration
cites at least one of its evidence ids. A refused or empty call can never be
marked satisfied — it can only be revoked with a public reason, which is the
honest "qualified abstention on a real refusal receipt" path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr

from data_incident_gym.diagnosis import DiagnosticStrategy, PolicyIdentity
from data_incident_gym.diagnostic_agent import (
    BASE_PROMPT,
    BASE_PROMPT_VERSION,
    MODEL_REQUEST_LIMIT,
    OUTPUT_RETRY_LIMIT,
    PLANNER_PROMPT,
    PLANNER_PROMPT_VERSION,
    TIMEOUT_SECONDS,
    TOOL_CALL_LIMIT,
)
from data_incident_gym.evidence import EvidenceRecord
from data_incident_gym.strategy_adapter import StrategySession, ToolReceipt, ToolRequest

PLANNER_PROTOCOL_VERSION = "p1.planner_controller.v1"

#: Validation codes of this layer. They never appear on a ``ToolReceipt``, and
#: backend codes (``RELATION_NOT_ALLOWED`` ...) never appear on a verdict.
PLAN_ERROR_CODES = frozenset(
    {
        "PLAN_SESSION_CLOSED",
        "PLAN_TOOL_NOT_ALLOWLISTED",
        "PLAN_ARGUMENTS_INVALID",
        "PLAN_OUTCOME_INVALID",
        "PLAN_RUN_SCOPE_MISMATCH",
        "PLAN_UNKNOWN_OBLIGATION",
        "PLAN_OBLIGATION_CLOSED",
        "PLAN_NO_EVIDENCE_FROM_LAST_CALL",
        "PLAN_EVIDENCE_NOT_RETURNED_BY_THIS_CALL",
        "PLAN_REVOKE_REASON_REQUIRED",
        "PLAN_OUTPUT_RETRY_EXHAUSTED",
        "PLAN_TOOL_BUDGET_EXHAUSTED",
        "PLAN_DEADLINE_EXCEEDED",
    }
)

ObligationOutcome = Literal["SATISFIED", "REVOKED"]

#: The outcomes a close declaration may actually carry. Checked at runtime: a
#: static ``Literal`` annotation is documentation, not enforcement, and treating
#: every unexpected value as "REVOKED" would silently accept a bogus close.
VALID_OUTCOMES = frozenset({"SATISFIED", "REVOKED"})


@dataclass(frozen=True)
class ToolObligationSpec:
    """How one read-only tool maps to an obligation's display fields."""

    evidence_kind: str
    subject_argument: str
    arguments: tuple[str, ...]


#: The six read-only tools, in canonical argument order.
TOOL_OBLIGATIONS: dict[str, ToolObligationSpec] = {
    "get_dbt_run_results": ToolObligationSpec("DBT_RUN_RESULTS", "run_id", ("run_id",)),
    "get_dbt_node_error": ToolObligationSpec(
        "DBT_NODE_ERROR", "node_id", ("run_id", "node_id")
    ),
    "get_dbt_lineage": ToolObligationSpec("DBT_LINEAGE", "node_id", ("node_id", "direction")),
    "get_relation_schema": ToolObligationSpec(
        "RELATION_SCHEMA", "relation_name", ("relation_name",)
    ),
    "get_relation_data_profile": ToolObligationSpec(
        "RELATION_DATA_PROFILE", "relation_name", ("relation_name",)
    ),
    "get_relation_history": ToolObligationSpec(
        "RELATION_HISTORY", "relation_name", ("relation_name",)
    ),
}


def canonical_arguments(arguments: dict[str, Any]) -> str:
    """Canonical JSON of the arguments: sorted keys, no whitespace.

    Plain ``name=value`` concatenation is not injective — a value containing the
    separator and the next key produces the same text as a different argument
    set (``{"direction": "upstream,node_id=x", "node_id": "y"}`` versus
    ``{"direction": "upstream", "node_id": "x,node_id=y"}``). JSON escapes every
    value, so the encoding is injective and equal calls still share one id.
    """

    return json.dumps(dict(sorted(arguments.items())), ensure_ascii=False, separators=(",", ":"))


def obligation_id_for(tool_name: str, arguments: dict[str, Any]) -> str:
    """The obligation identity: tool name plus **every** effective argument."""

    return f"{tool_name}:{canonical_arguments(arguments)}"


def _digest(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def obligation_tool_schemas() -> list[dict[str, Any]]:
    """The six tools as the planner declares them to the model.

    The schema binds argument **names**, their types, the required set and the
    closed-object rule — a name list alone would leave a changed argument set
    (or a changed type) invisible to both the identity and the model.
    """

    return [
        {
            "name": tool,
            "input_schema": {
                "type": "object",
                "properties": {name: {"type": "string"} for name in spec.arguments},
                "required": list(spec.arguments),
                "additionalProperties": False,
            },
        }
        for tool, spec in sorted(TOOL_OBLIGATIONS.items())
    ]


def planner_controller_payload() -> dict[str, Any]:
    """Everything the planner's controller protocol promises, in one payload.

    Any change here changes the policy identity digest, so the plan contract
    cannot drift silently under an existing identity. The per-tool entries carry
    the whole mapping — tool name, evidence kind, subject argument and argument
    set — so no part of the obligation identity can be edited outside the digest.

    Slice 3 (runner wiring) adds the three model output-tool schemas
    (``plan_step``, ``close_obligation``, ``submit_diagnosis``) to this payload:
    they are part of the model-visible surface and must be bound the same way.
    """

    return {
        "protocol_version": PLANNER_PROTOCOL_VERSION,
        "tools": {
            tool: {
                "evidence_kind": spec.evidence_kind,
                "subject_argument": spec.subject_argument,
                "arguments": list(spec.arguments),
            }
            for tool, spec in sorted(TOOL_OBLIGATIONS.items())
        },
        "tool_schema_sha256": _digest(obligation_tool_schemas()),
        "budget": {
            "model_request_limit": MODEL_REQUEST_LIMIT,
            "tool_call_limit": TOOL_CALL_LIMIT,
            "timeout_seconds": TIMEOUT_SECONDS,
        },
        "plan_refusal_budget": {
            "limit": OUTPUT_RETRY_LIMIT,
            "counter": "plan_refusals",
            "note": "separate from the T09 submission retry counter",
        },
        "outcomes": sorted(VALID_OUTCOMES),
        "error_codes": sorted(PLAN_ERROR_CODES),
        "obligation_schema": EvidenceObligation.model_json_schema(),
        "verdict_schema": PlanVerdict.model_json_schema(),
    }


def evidence_planner_policy_identity() -> PolicyIdentity:
    """The planner's policy identity: prompt, controller payload and tool schema.

    The planner is a *model* strategy, so its controller surface is bound here
    rather than through ``_build_policy_surface`` — that registry builds the
    kernel/static decision schemas, which are not this strategy's surface.
    """

    return PolicyIdentity(
        strategy=DiagnosticStrategy.EVIDENCE_PLANNER,
        base_prompt_version=BASE_PROMPT_VERSION,
        base_prompt_sha256=hashlib.sha256(BASE_PROMPT.encode("utf-8")).hexdigest(),
        strategy_prompt_version=PLANNER_PROMPT_VERSION,
        strategy_prompt_sha256=hashlib.sha256(PLANNER_PROMPT.encode("utf-8")).hexdigest(),
        controller_protocol_version=PLANNER_PROTOCOL_VERSION,
        controller_protocol_sha256=_digest(planner_controller_payload()),
        tool_schema_sha256=_digest(obligation_tool_schemas()),
    )


class EvidenceObligation(BaseModel):
    """One obligation as the harness sees it; the model never names it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    obligation_id: StrictStr
    tool_name: StrictStr
    arguments: dict[StrictStr, StrictStr]
    evidence_kind: StrictStr
    subject: StrictStr
    status: Literal["OPEN", "SATISFIED", "REVOKED"]
    calls: StrictInt
    last_call_accepted: StrictBool | None = None
    last_call_evidence_ids: tuple[StrictStr, ...] = ()
    satisfied_with: tuple[StrictStr, ...] = ()
    close_reason: StrictStr | None = None
    last_intent: StrictStr | None = None


class PlanVerdict(BaseModel):
    """The outcome of validating a declaration — never a backend receipt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: StrictBool
    code: StrictStr | None = None
    obligation_id: StrictStr | None = None
    detail: StrictStr | None = None
    plan_refusals_used: StrictInt = 0
    plan_refusal_limit: StrictInt = 0
    tool_calls_used: StrictInt = 0


class PlanStepResult(BaseModel):
    """A validated step: the verdict, plus the real receipt when one exists."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: PlanVerdict
    receipt: ToolReceipt | None = None

    @property
    def evidence(self) -> tuple[EvidenceRecord, ...]:
        """Facts actually returned; empty for validation refusals by design."""

        return () if self.receipt is None else self.receipt.evidence


@dataclass
class _Obligation:
    obligation_id: str
    tool_name: str
    arguments: dict[str, str]
    evidence_kind: str
    subject: str
    status: str = "OPEN"
    calls: int = 0
    last_call_accepted: bool | None = None
    last_call_evidence_ids: tuple[str, ...] = ()
    satisfied_with: tuple[str, ...] = ()
    close_reason: str | None = None
    last_intent: str | None = None
    history: list[str] = field(default_factory=list)


class PlannerController:
    """Deterministic plan validation on top of one harness-owned session.

    Every accepted step is executed through ``session.call_tool`` so counting,
    registration and error codes stay exactly where T09 put them: the planner
    adds a gate, not a second policy.
    """

    def __init__(self, session: StrategySession) -> None:
        self._session = session
        self._obligations: dict[str, _Obligation] = {}
        self._executed = 0
        self._proposed = 0
        self._refusals = 0
        self._blocked = 0
        self._refusal_codes: dict[str, int] = {}
        self._refusal_limit = session.task_context().budget.output_retry_limit

    # -- public operations -------------------------------------------------

    def plan_step(
        self, tool_name: str, arguments: dict[str, Any], *, intent: str = ""
    ) -> PlanStepResult:
        """Validate one declared step; execute it only when it is acceptable."""

        self._proposed += 1
        code, detail = self._validate_step(tool_name, arguments)
        if code is not None:
            return PlanStepResult(verdict=self._refuse(code, detail))

        obligation = self._obligation_for(tool_name, arguments)
        obligation.last_intent = intent or None
        self._executed += 1
        receipt = self._session.call_tool(
            ToolRequest(
                request_id=f"plan:{self._executed}",
                tool_name=tool_name,
                arguments=dict(arguments),
            )
        )
        obligation.calls += 1
        obligation.last_call_accepted = receipt.accepted
        obligation.last_call_evidence_ids = tuple(receipt.evidence_ids)
        obligation.history.append("EXECUTED")
        return PlanStepResult(
            verdict=PlanVerdict(
                accepted=True,
                obligation_id=obligation.obligation_id,
                plan_refusals_used=self._refusals,
                plan_refusal_limit=self._refusal_limit,
                tool_calls_used=self._session.snapshot()["tool_call_attempts"],
            ),
            receipt=receipt,
        )

    def close_obligation(
        self,
        obligation_id: str,
        outcome: str,
        *,
        evidence_ids: tuple[str, ...] = (),
        reason: str | None = None,
    ) -> PlanVerdict:
        """Close one obligation; ``SATISFIED`` must be earned, never assumed.

        ``outcome`` is validated here at runtime — the annotation documents the
        two outcomes, it does not enforce them.
        """

        code, detail = self._entry_gate()
        if code is None and outcome not in VALID_OUTCOMES:
            code = "PLAN_OUTCOME_INVALID"
            detail = f"expected one of {', '.join(sorted(VALID_OUTCOMES))}, got {outcome!r}"
        obligation = self._obligations.get(obligation_id)
        if code is None and obligation is None:
            code, detail = "PLAN_UNKNOWN_OBLIGATION", f"never planned: {obligation_id}"
        if code is None and obligation is not None and obligation.status != "OPEN":
            code, detail = "PLAN_OBLIGATION_CLOSED", f"already {obligation.status}"
        if code is None and outcome == "REVOKED" and not (reason or "").strip():
            code = "PLAN_REVOKE_REASON_REQUIRED"
            detail = "a revoked obligation needs a public reason"
        if code is None and outcome == "SATISFIED":
            code, detail = self._validate_satisfaction(obligation, evidence_ids)
        if code is not None:
            return self._refuse(code, detail)

        assert obligation is not None
        if outcome == "SATISFIED":
            obligation.status = "SATISFIED"
            obligation.satisfied_with = tuple(evidence_ids)
        else:
            obligation.status = "REVOKED"
            obligation.close_reason = reason
        obligation.history.append(outcome)
        return PlanVerdict(
            accepted=True,
            obligation_id=obligation_id,
            detail=reason,
            plan_refusals_used=self._refusals,
            plan_refusal_limit=self._refusal_limit,
            tool_calls_used=self._session.snapshot()["tool_call_attempts"],
        )

    # -- introspection -----------------------------------------------------

    def obligations(self) -> tuple[EvidenceObligation, ...]:
        return tuple(
            EvidenceObligation(
                obligation_id=item.obligation_id,
                tool_name=item.tool_name,
                arguments=dict(item.arguments),
                evidence_kind=item.evidence_kind,
                subject=item.subject,
                status=item.status,  # type: ignore[arg-type]
                calls=item.calls,
                last_call_accepted=item.last_call_accepted,
                last_call_evidence_ids=item.last_call_evidence_ids,
                satisfied_with=item.satisfied_with,
                close_reason=item.close_reason,
                last_intent=item.last_intent,
            )
            for item in self._obligations.values()
        )

    def snapshot(self) -> dict[str, Any]:
        open_ids = sorted(
            item.obligation_id for item in self._obligations.values() if item.status == "OPEN"
        )
        return {
            "protocol_version": PLANNER_PROTOCOL_VERSION,
            "plan_steps_proposed": self._proposed,
            "plan_steps_executed": self._executed,
            "plan_refusals_used": self._refusals,
            "plan_refusal_limit": self._refusal_limit,
            "plan_operations_blocked": self._blocked,
            "refusals_by_code": dict(self._refusal_codes),
            "obligations": len(self._obligations),
            "obligations_open": open_ids,
            "obligations_satisfied": sorted(
                item.obligation_id
                for item in self._obligations.values()
                if item.status == "SATISFIED"
            ),
            "obligations_revoked": sorted(
                item.obligation_id
                for item in self._obligations.values()
                if item.status == "REVOKED"
            ),
            "note": "plan verdicts are validation outcomes, not tool receipts",
        }

    # -- validation --------------------------------------------------------

    def _validate_step(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> tuple[str | None, str | None]:
        code, detail = self._entry_gate()
        if code is not None:
            return code, detail
        context = self._session.task_context()
        if tool_name not in TOOL_OBLIGATIONS:
            return "PLAN_TOOL_NOT_ALLOWLISTED", f"unknown tool: {tool_name}"
        if tool_name not in set(context.tool_allowlist):
            return "PLAN_TOOL_NOT_ALLOWLISTED", f"not granted: {tool_name}"
        expected = TOOL_OBLIGATIONS[tool_name].arguments
        if set(arguments) != set(expected) or any(
            not isinstance(arguments[name], str) for name in expected
        ):
            return "PLAN_ARGUMENTS_INVALID", f"expected arguments {', '.join(expected)}"
        run_id = context.run_id
        if arguments.get("run_id", run_id) != run_id:
            return "PLAN_RUN_SCOPE_MISMATCH", "run-scoped argument must match this run"
        obligation_id = obligation_id_for(tool_name, arguments)
        known = self._obligations.get(obligation_id)
        if known is not None and known.status != "OPEN":
            return "PLAN_OBLIGATION_CLOSED", f"obligation is {known.status}"
        if self._session.snapshot()["tool_call_attempts"] >= context.budget.tool_call_limit:
            return "PLAN_TOOL_BUDGET_EXHAUSTED", "tool-call budget is spent"
        return None, None

    def _validate_satisfaction(
        self, obligation: _Obligation | None, evidence_ids: tuple[str, ...]
    ) -> tuple[str | None, str | None]:
        assert obligation is not None
        if (
            obligation.last_call_accepted is not True
            or not obligation.last_call_evidence_ids
        ):
            return (
                "PLAN_NO_EVIDENCE_FROM_LAST_CALL",
                "the last call was refused or returned no evidence; revoke it instead",
            )
        if not evidence_ids:
            return "PLAN_NO_EVIDENCE_FROM_LAST_CALL", "SATISFIED needs at least one evidence id"
        foreign = sorted(set(evidence_ids) - set(obligation.last_call_evidence_ids))
        if foreign:
            return (
                "PLAN_EVIDENCE_NOT_RETURNED_BY_THIS_CALL",
                f"not returned by this obligation's last call: {', '.join(foreign)}",
            )
        return None, None

    def _entry_gate(self) -> tuple[str | None, str | None]:
        """The checks that precede every operation-specific rule, in order.

        Terminal state, then deadline, then the plan-refusal budget: once the
        budget is spent both entry points answer ``PLAN_OUTPUT_RETRY_EXHAUSTED``
        instead of producing another specific code, so the refusal counter can
        never grow past its limit.
        """

        code, detail = self._terminal_or_deadline()
        if code is not None:
            return code, detail
        if self._refusals >= self._refusal_limit:
            return "PLAN_OUTPUT_RETRY_EXHAUSTED", "plan refusal budget is spent"
        return None, None

    def _terminal_or_deadline(self) -> tuple[str | None, str | None]:
        snapshot = self._session.snapshot()
        if snapshot["submitted"] or snapshot["cancelled"] is not None:
            return "PLAN_SESSION_CLOSED", "the session already reached a terminal state"
        if snapshot["elapsed_seconds"] > snapshot["deadline_seconds"]:
            return "PLAN_DEADLINE_EXCEEDED", "the run deadline has passed"
        return None, None

    # -- internals ---------------------------------------------------------

    def _obligation_for(self, tool_name: str, arguments: dict[str, Any]) -> _Obligation:
        obligation_id = obligation_id_for(tool_name, arguments)
        existing = self._obligations.get(obligation_id)
        if existing is not None:
            return existing
        spec = TOOL_OBLIGATIONS[tool_name]
        obligation = _Obligation(
            obligation_id=obligation_id,
            tool_name=tool_name,
            arguments={name: arguments[name] for name in spec.arguments},
            evidence_kind=spec.evidence_kind,
            subject=str(arguments[spec.subject_argument]),
        )
        obligation.history.append("CREATED")
        self._obligations[obligation_id] = obligation
        return obligation

    def _refuse(self, code: str, detail: str | None) -> PlanVerdict:
        assert code in PLAN_ERROR_CODES, code
        if code != "PLAN_OUTPUT_RETRY_EXHAUSTED":
            self._refusals += 1
            self._refusal_codes[code] = self._refusal_codes.get(code, 0) + 1
        else:
            self._blocked += 1
        return PlanVerdict(
            accepted=False,
            code=code,
            detail=detail,
            plan_refusals_used=self._refusals,
            plan_refusal_limit=self._refusal_limit,
            tool_calls_used=self._session.snapshot()["tool_call_attempts"],
        )


__all__ = [
    "PLANNER_PROTOCOL_VERSION",
    "PLAN_ERROR_CODES",
    "VALID_OUTCOMES",
    "EvidenceObligation",
    "PlanStepResult",
    "PlanVerdict",
    "PlannerController",
    "TOOL_OBLIGATIONS",
    "canonical_arguments",
    "evidence_planner_policy_identity",
    "obligation_id_for",
    "obligation_tool_schemas",
    "planner_controller_payload",
]
