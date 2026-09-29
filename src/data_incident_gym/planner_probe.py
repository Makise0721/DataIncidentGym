"""Planner compatibility probe for the experimental preflight (§2.3).

The generic doctor structured-output probe does not prove the planner loop.
This probe drives the REAL planner machinery (controller verdicts, session
receipts, final-output validation) against a given model — in preflight the
manifest-bound settings model — with a deterministic read-only backend, so a
pass means: the model can emit a valid ``plan_step``, receive the receipt and
verdict, and finish through ``submit_diagnosis``. No database, no artifacts.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic_ai.models import Model

from data_incident_gym.diagnosis import DiagnosisStatus
from data_incident_gym.diagnostic_agent import ModelIdentity, _transport_diagnostic
from data_incident_gym.evidence import (
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
)
from data_incident_gym.planner_agent import (
    EvidencePlannerRunner,
    planner_builtin_declaration,
)
from data_incident_gym.run_context import IncidentBrief, ObservableRunContext
from data_incident_gym.strategy_adapter import StrategySession

PLANNER_PROBE_RUN_ID = hashlib.sha256(b"planner-compatibility-probe").hexdigest()[:32]
PLANNER_PROBE_TIMEOUT_SECONDS = 120
PROBE_RELATION = "raw_payments"

_OBSERVED_AT = datetime(2026, 9, 28, tzinfo=UTC)


@dataclass(frozen=True)
class PlannerProbeResult:
    passed: bool
    observed: str
    transport: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def _probe_record() -> EvidenceRecord:
    return EvidenceRecord.create(
        run_id=PLANNER_PROBE_RUN_ID,
        evidence_type=EvidenceType.DBT_RUN_RESULTS,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject=PLANNER_PROBE_RUN_ID,
        observed_at=_OBSERVED_AT,
        content=DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=PLANNER_PROBE_RUN_ID,
            run_status="SUCCEEDED",
            dbt_exit_code=0,
            failed_nodes=(),
            skipped_nodes=(),
        ),
    )


class _ProbeBackend:
    """Deterministic stand-in for the six read-only tools: one record per
    call, no refusals, no database. Wire compatibility is the probe's
    question; DB health is the doctor's."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _one(self, tool_name: str, subject: str) -> tuple[EvidenceRecord, ...]:
        self.calls.append(f"{tool_name}:{subject}")
        return (_probe_record(),)

    def get_dbt_run_results(self, run_id: str) -> tuple[EvidenceRecord, ...]:
        return self._one("get_dbt_run_results", run_id)

    def get_dbt_node_error(self, run_id: str, node_id: str) -> tuple[EvidenceRecord, ...]:
        return self._one("get_dbt_node_error", node_id)

    def get_dbt_lineage(self, node_id: str, direction: str) -> tuple[EvidenceRecord, ...]:
        return self._one("get_dbt_lineage", f"{node_id}:{direction}")

    def get_relation_schema(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        return self._one("get_relation_schema", relation_name)

    def get_relation_data_profile(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        return self._one("get_relation_data_profile", relation_name)

    def get_relation_history(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        return self._one("get_relation_history", relation_name)


def _probe_context() -> ObservableRunContext:
    return ObservableRunContext(
        run_id=PLANNER_PROBE_RUN_ID,
        artifact_dir=Path("."),
        runtime={"observable_relations": {"schema": [PROBE_RELATION]}},
        incident_brief=IncidentBrief(
            schema_version="incident_brief.v1",
            signal_code="PROBE",
            summary="Planner compatibility probe: plan one schema check and finish.",
            subjects=(),
            logical_observed_at=_OBSERVED_AT,
            observations=(),
        ),
    )


async def run_planner_compatibility_probe(
    model: Model,
    model_identity: ModelIdentity,
    *,
    timeout_seconds: int = PLANNER_PROBE_TIMEOUT_SECONDS,
) -> PlannerProbeResult:
    context = _probe_context()
    session = StrategySession(
        run_id=PLANNER_PROBE_RUN_ID,
        tools=_ProbeBackend(),
        context=context,
        declaration=planner_builtin_declaration(
            model_provider=model_identity.provider,
            model_name=model_identity.model,
            deterministic=False,
        ),
    )
    runner = EvidencePlannerRunner(
        run_id=PLANNER_PROBE_RUN_ID,
        context=context,
        session=session,
        model=model,
        model_identity=model_identity,
    )
    transport: str | None = None
    try:
        result = await asyncio.wait_for(runner.diagnose(), timeout=timeout_seconds)
    except TimeoutError:
        return PlannerProbeResult(
            passed=False,
            observed="PROBE_TIMEOUT",
            detail={"timeout_seconds": timeout_seconds},
        )
    except Exception as error:  # noqa: BLE001 -- probe reports, never leaks
        transport = _transport_diagnostic(error)
        return PlannerProbeResult(
            passed=False,
            observed="MODEL_ERROR",
            transport=transport,
            detail={"error_class": type(error).__name__},
        )

    plan_step_receipts = sum(
        1
        for event in result.trace
        if getattr(event, "event_type", None) == "TOOL_CALL"
        and tuple(getattr(event, "evidence_ids", ()) or ())
    )
    terminal_status = result.diagnosis.status.value
    detail = {
        "plan_step_receipts": plan_step_receipts,
        "terminal_status": terminal_status,
        "model_requests": int(result.metrics.model_requests),
    }
    if result.diagnosis.status is DiagnosisStatus.MODEL_ERROR:
        protocol = [
            event
            for event in result.trace
            if getattr(event, "event_type", None) == "MODEL_PROTOCOL"
        ]
        transport = next(
            (getattr(event, "transport_diagnostic", None) for event in protocol),
            None,
        )
        return PlannerProbeResult(
            passed=False,
            observed="MODEL_ERROR",
            transport=transport,
            detail=detail,
        )
    if plan_step_receipts < 1:
        return PlannerProbeResult(
            passed=False,
            observed="NO_ACCEPTED_PLAN_STEP",
            detail=detail,
        )
    return PlannerProbeResult(
        passed=True,
        observed="PLAN_LOOP_COMPLETED",
        transport=transport,
        detail=detail,
    )


__all__ = [
    "PLANNER_PROBE_RUN_ID",
    "PLANNER_PROBE_TIMEOUT_SECONDS",
    "PlannerProbeResult",
    "run_planner_compatibility_probe",
]
