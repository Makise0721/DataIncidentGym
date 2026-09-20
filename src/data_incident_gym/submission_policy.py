"""Harness-side submission gates (D2): I1 claim support and I2 gap receipts.

Per ``docs/superpowers/specs/2026-09-20-submission-gates-implementation-spec.md``
(owner ruling D2 + three constraints), the gates run inside the model output
turn's validator, so a refusal is a retryable ``ModelRetry`` with a fixed code
and the model can repair the submission. All private-contract reasoning stays
here on the harness side: the model only ever sees the fixed code and a generic
message.

Owner constraint 1 is implemented here: any internal error in the gate fails
closed (a retryable ``GATE_INTERNAL_ERROR`` refusal), never an acceptance.
Constraint 2 (retry-exhaustion terminal code) is mapped by the runner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from data_incident_gym.diagnosis import (
    ToolTraceEvent,
    refusal_witnessed,
)
from data_incident_gym.evaluation import (
    APPLICABLE_CLAIM_KINDS_BY_EXPECTED_STATUS,
    claim_supported_by_records,
)
from data_incident_gym.scenarios import ScenarioSpec

CLAIM_SUPPORT_REQUIRED = "CLAIM_SUPPORT_REQUIRED"
GAP_RECEIPT_REQUIRED = "GAP_RECEIPT_REQUIRED"
GATE_INTERNAL_ERROR = "GATE_INTERNAL_ERROR"

_CLAIM_SUPPORT_MESSAGE = (
    "one or more claims are not supported by their cited evidence; cite evidence "
    "returned by tool calls that supports each claim"
)
_GAP_RECEIPT_MESSAGE = (
    "a declared gap names unavailable evidence without a recorded tool refusal; "
    "probe the tool to record the refusal for this gap"
)
_GATE_INTERNAL_MESSAGE = "submission gate failed to evaluate the submission"


@dataclass(frozen=True)
class GateRefusal:
    code: str
    message: str


class SubmissionPolicy:
    """The harness-side submission policy for one scenario contract.

    ``check`` receives the candidate submission (a ``Diagnosis`` for the static
    path, a ``KernelDecision`` for the kernel path — both expose ``status``,
    ``claims`` and ``unresolved_evidence``), the run's registered evidence
    records (the evaluator's own inventory source) and the live tool trace.
    """

    def __init__(self, scenario: ScenarioSpec) -> None:
        self._scenario = scenario

    def check(
        self,
        submission: Any,
        records: tuple[Any, ...],
        trace: tuple[object, ...],
    ) -> GateRefusal | None:
        """Evaluate both gates; never raises, never accepts a broken check."""

        try:
            claims = tuple(getattr(submission, "claims", ()))
            unresolved = tuple(getattr(submission, "unresolved_evidence", ()))
            tool_trace = tuple(
                event for event in trace if isinstance(event, ToolTraceEvent)
            )
            refusal = self._claim_support_refusal(claims, records)
            if refusal is not None:
                return refusal
            return self._gap_receipt_refusal(unresolved, tool_trace)
        except Exception:
            return GateRefusal(code=GATE_INTERNAL_ERROR, message=_GATE_INTERNAL_MESSAGE)

    # -- I1 -----------------------------------------------------------------

    def _claim_support_refusal(
        self,
        claims: tuple[Any, ...],
        records: tuple[Any, ...],
    ) -> GateRefusal | None:
        applicable_kinds = APPLICABLE_CLAIM_KINDS_BY_EXPECTED_STATUS.get(
            self._scenario.expected_status, frozenset()
        )
        if not applicable_kinds:
            return None
        inventory = {record.evidence_id: record for record in records}
        for claim in claims:
            if claim.kind not in applicable_kinds:
                continue
            resolved = tuple(
                inventory[item] for item in claim.evidence_ids if item in inventory
            )
            if len(resolved) != len(claim.evidence_ids):
                return GateRefusal(
                    code=CLAIM_SUPPORT_REQUIRED, message=_CLAIM_SUPPORT_MESSAGE
                )
            if not claim_supported_by_records(
                self._scenario,
                claim,
                resolved,
                all_records=records,
            ):
                return GateRefusal(
                    code=CLAIM_SUPPORT_REQUIRED, message=_CLAIM_SUPPORT_MESSAGE
                )
        return None

    # -- I2 -----------------------------------------------------------------

    def _gap_receipt_refusal(
        self,
        unresolved: tuple[Any, ...],
        tool_trace: tuple[ToolTraceEvent, ...],
    ) -> GateRefusal | None:
        contract = {
            (gap.gap_kind, gap.subject, gap.reason_code): gap.tool_name
            for gap in self._scenario.observable_evidence_contract.unresolved_gaps
        }
        for item in unresolved:
            triple = (item.evidence_kind, item.subject, item.reason_code)
            tool_name = contract.get(triple)
            if tool_name is None:
                continue
            if not refusal_witnessed(
                tool_trace,
                tool_name=tool_name,
                target=item.subject,
                code=item.reason_code,
            ):
                return GateRefusal(code=GAP_RECEIPT_REQUIRED, message=_GAP_RECEIPT_MESSAGE)
        return None


__all__ = [
    "CLAIM_SUPPORT_REQUIRED",
    "GAP_RECEIPT_REQUIRED",
    "GATE_INTERNAL_ERROR",
    "GateRefusal",
    "SubmissionPolicy",
]
