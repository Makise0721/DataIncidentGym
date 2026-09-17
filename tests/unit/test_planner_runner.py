"""T12 planner runner regressions: agent loop, terminals and budget boundaries.

A scripted model drives the real agent: the two action tools return receipts and
verdicts into the conversation, the terminal tool ends the run, and the harness
session keeps the authoritative counters. The scripts need no database and no
real model.

Mechanism -> path -> assertion (the mapping the audit asked for):

| mechanism | path | assertion |
| --- | --- | --- |
| plan, evidence, submit | action tools plus terminal output | CONFIRMED, 4 attempts |
| tool-call budget | ninth ``plan_step`` meets the plan layer | verdict, 8 attempts |
| plan-refusal budget | two invalid declarations, then a valid one | third is blocked |
| refused submission | unregistered evidence id | ``MODEL_ERROR`` (``MODEL_PROTOCOL_ERROR``) |
| deadline | scripted model outlives the deadline | ``MODEL_ERROR`` (``MODEL_TIMEOUT``) |
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from data_incident_gym.diagnosis import DiagnosisStatus, DiagnosticStrategy
from data_incident_gym.diagnostic_agent import ModelIdentity
from data_incident_gym.evidence_planner import evidence_planner_policy_identity
from data_incident_gym.planner_agent import EvidencePlannerRunner
from data_incident_gym.strategy_adapter import StrategySession

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unit.test_evidence_planner import (  # noqa: E402
    NODE,
    RUN_ID,
    _PlannerTools,
    _record_for,
)
from unit.test_kernel_ledger_instructions import _write_run_context  # noqa: E402
from unit.test_strategy_adapter import _context, _declaration  # noqa: E402

RELATION = "raw_orders"
DOWNSTREAM = "model.jaffle_shop.orders"


def _ids() -> dict[str, str]:
    return {
        "run": _record_for("get_dbt_run_results", RUN_ID).evidence_id,
        "node": _record_for("get_dbt_node_error", NODE).evidence_id,
        "lineage": _record_for("get_dbt_lineage", f"{NODE}:downstream").evidence_id,
        "profile": _record_for("get_relation_data_profile", RELATION).evidence_id,
    }


def _steps() -> list[tuple[str, dict[str, object]]]:
    return [
        ("plan_step", {"tool_name": "get_dbt_run_results", "arguments": {"run_id": RUN_ID},
                       "intent": "read the run"}),
        ("plan_step", {"tool_name": "get_dbt_node_error",
                       "arguments": {"run_id": RUN_ID, "node_id": NODE},
                       "intent": "read the failure"}),
        ("plan_step", {"tool_name": "get_relation_data_profile",
                       "arguments": {"relation_name": RELATION}, "intent": "check the column"}),
        ("plan_step", {"tool_name": "get_dbt_lineage",
                       "arguments": {"node_id": NODE, "direction": "downstream"},
                       "intent": "name the asset"}),
    ]


def _obligation_ids() -> dict[str, str]:
    from data_incident_gym.evidence_planner import obligation_id_for

    return {
        "run": obligation_id_for("get_dbt_run_results", {"run_id": RUN_ID}),
        "node": obligation_id_for("get_dbt_node_error", {"node_id": NODE, "run_id": RUN_ID}),
        "profile": obligation_id_for(
            "get_relation_data_profile", {"relation_name": RELATION}
        ),
        "lineage": obligation_id_for(
            "get_dbt_lineage", {"node_id": NODE, "direction": "downstream"}
        ),
    }


def _close_events() -> list[tuple[str, dict[str, object]]]:
    ids, obligations = _ids(), _obligation_ids()
    return [
        ("close_obligation", {"obligation_id": obligations["node"], "outcome": "SATISFIED",
                              "evidence_ids": [ids["node"]]}),
        ("close_obligation", {"obligation_id": obligations["profile"],
                              "outcome": "SATISFIED", "evidence_ids": [ids["profile"]]}),
    ]


def _confirmed() -> dict[str, object]:
    ids = _ids()
    return {
        "status": "CONFIRMED",
        "summary": "Node error plus the null column confirms a required field is null.",
        "root_cause_code": "SOURCE_REQUIRED_FIELD_NULL",
        "affected_assets": [DOWNSTREAM],
        "evidence_ids": [ids["node"], ids["profile"], ids["lineage"]],
        "claims": [
            {"kind": "ROOT_CAUSE", "root_cause_code": "SOURCE_REQUIRED_FIELD_NULL",
             "evidence_ids": [ids["node"], ids["profile"]]},
            {"kind": "AFFECTED_ASSET", "asset": DOWNSTREAM,
             "evidence_ids": [ids["lineage"]]},
        ],
        "confidence": 0.9,
    }


def _abstain() -> dict[str, object]:
    return {
        "status": "INSUFFICIENT_EVIDENCE",
        "summary": "Budget ran out before the decisive evidence was collected.",
        "unresolved_evidence": [
            {"evidence_kind": "RELATION_DATA_PROFILE", "subject": RELATION,
             "reason_code": "NOT_OBSERVABLE"}
        ],
        "confidence": 0.2,
    }


def _script(events):
    """Play one message per model turn.

    An event is either ``(tool_name, arguments)``, a list of such pairs (one
    turn carrying several calls) or a float (sleep that long, then submit).
    """

    remaining = list(events)

    def play(_messages, _info) -> ModelResponse:
        if not remaining:
            return ModelResponse(parts=[ToolCallPart("submit_diagnosis", _abstain())])
        item = remaining.pop(0)
        if isinstance(item, float):
            time.sleep(item)
            return ModelResponse(parts=[ToolCallPart("submit_diagnosis", _abstain())])
        if isinstance(item, list):
            return ModelResponse(parts=[ToolCallPart(name, args) for name, args in item])
        name, arguments = item
        return ModelResponse(parts=[ToolCallPart(name, arguments)])

    return play


def _runner(
    events: list[tuple[str, dict[str, object]] | float],
    project_root: Path,
    *,
    backend: _PlannerTools | None = None,
) -> EvidencePlannerRunner:
    session = StrategySession(
        run_id=RUN_ID,
        tools=backend or _PlannerTools(),
        context=_context(),
        declaration=_declaration(),
    )
    return EvidencePlannerRunner.for_run(
        RUN_ID,
        SimpleNamespace(),
        project_root,
        model=FunctionModel(_script(events)),
        model_identity=ModelIdentity("test", "scripted"),
        session=session,
    )


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    _write_run_context(tmp_path, RUN_ID)
    return tmp_path


def test_the_planner_loop_submits_a_confirmed_diagnosis(project_root: Path) -> None:
    runner = _runner([*_steps(), *_close_events(), ("submit_diagnosis", _confirmed())],
                     project_root)

    result = asyncio.run(runner.diagnose())

    assert result.strategy is DiagnosticStrategy.EVIDENCE_PLANNER
    assert result.policy_identity == evidence_planner_policy_identity()
    assert result.diagnosis.status is DiagnosisStatus.CONFIRMED
    assert result.metrics.tool_call_attempts == 4
    assert result.metrics.successful_tool_calls == 4
    assert [record.evidence_id for record in result.evidence_records] == [
        _ids()["run"], _ids()["node"], _ids()["profile"], _ids()["lineage"]
    ]
    tool_events = [event for event in result.trace if event.event_type == "TOOL_CALL"]
    assert [event.tool_name for event in tool_events] == [
        "get_dbt_run_results", "get_dbt_node_error", "get_relation_data_profile",
        "get_dbt_lineage",
    ]
    assert all(event.error_code is None for event in tool_events)
    terminal = result.trace[-1]
    assert terminal.event_type == "DIAGNOSIS_TERMINAL"
    assert terminal.status is DiagnosisStatus.CONFIRMED
    assert terminal.evidence_inventory == tuple(
        record.evidence_id for record in result.evidence_records
    )
    assert runner.session.final_diagnosis is not None
    snapshot = runner.controller.snapshot()
    assert len(snapshot["obligations_satisfied"]) == 2
    assert len(snapshot["obligations_open"]) == 2


def test_the_tool_budget_stops_the_ninth_step(project_root: Path) -> None:
    # One turn carries the first eight calls; the ninth meets the budget.
    batch = [
        ("plan_step", {"tool_name": "get_dbt_node_error",
                       "arguments": {"run_id": RUN_ID, "node_id": f"test.node_{index}"},
                       "intent": "probe"})
        for index in range(8)
    ]
    ninth = ("plan_step", {"tool_name": "get_dbt_node_error",
                           "arguments": {"run_id": RUN_ID, "node_id": "test.node_8"},
                           "intent": "probe"})
    runner = _runner([batch, ninth, ("submit_diagnosis", _abstain())], project_root)

    result = asyncio.run(runner.diagnose())

    assert result.metrics.tool_call_attempts == 8
    assert result.metrics.successful_tool_calls == 8
    assert runner.controller.snapshot()["refusals_by_code"] == {"PLAN_TOOL_BUDGET_EXHAUSTED": 1}
    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert len([event for event in result.trace if event.event_type == "TOOL_CALL"]) == 8


def test_the_refusal_budget_blocks_steps_but_the_submission_still_lands(
    project_root: Path,
) -> None:
    invalid = ("plan_step", {"tool_name": "run_sql", "arguments": {"query": "select 1"},
                             "intent": "outside the tool set"})
    runner = _runner([invalid, invalid, invalid, ("submit_diagnosis", _abstain())], project_root)

    result = asyncio.run(runner.diagnose())

    snapshot = runner.controller.snapshot()
    assert snapshot["plan_refusals_used"] == 2
    assert snapshot["plan_operations_blocked"] == 1
    assert snapshot["refusals_by_code"] == {"PLAN_TOOL_NOT_ALLOWLISTED": 2}
    assert result.metrics.tool_call_attempts == 0
    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert runner.session.final_diagnosis is not None


def test_a_refused_submission_fails_closed(project_root: Path) -> None:
    forged = "ev_" + "a" * 64
    submission = _confirmed()
    submission["evidence_ids"] = [forged]
    submission["claims"] = [
        {"kind": "ROOT_CAUSE", "root_cause_code": "SOURCE_REQUIRED_FIELD_NULL",
         "evidence_ids": [forged]},
        {"kind": "AFFECTED_ASSET", "asset": DOWNSTREAM, "evidence_ids": [forged]},
    ]
    runner = _runner([("submit_diagnosis", submission)], project_root)

    result = asyncio.run(runner.diagnose())

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    assert result.diagnosis.summary == "MODEL_PROTOCOL_ERROR"
    assert result.evidence_records == ()
    assert runner.session.cancellation == "RUN_FAILED"
    assert result.trace[-1].status is DiagnosisStatus.MODEL_ERROR


def test_a_deadline_overflow_ends_in_a_timeout_terminal(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import data_incident_gym.planner_agent as planner_agent

    monkeypatch.setattr(planner_agent, "TIMEOUT_SECONDS", 0.05)
    runner = _runner([0.4, ("submit_diagnosis", _confirmed())], project_root)

    result = asyncio.run(runner.diagnose())

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    assert result.diagnosis.summary == "MODEL_TIMEOUT"
    assert runner.session.cancellation == "STRATEGY_TIMEOUT"
    assert result.metrics.tool_call_attempts == 0
