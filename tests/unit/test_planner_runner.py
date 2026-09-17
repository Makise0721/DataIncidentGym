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
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from data_incident_gym.diagnosis import DiagnosisStatus, DiagnosticStrategy
from data_incident_gym.diagnostic_agent import ModelIdentity
from data_incident_gym.diagnostic_config import DiagnosticSettings
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


def _always(submission: dict[str, object]):
    def play(_messages, _info) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("submit_diagnosis", submission)])

    return play


def _runner(
    events,
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
        model=FunctionModel(events if callable(events) else _script(events)),
        model_identity=ModelIdentity("test", "scripted"),
        session=session,
    )


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    _write_run_context(tmp_path, RUN_ID)
    return tmp_path


# -- benchmark wiring (for_run construction) --------------------------------


def test_injecting_a_model_requires_an_identity(project_root: Path) -> None:
    with pytest.raises(ValueError, match="model_identity is required"):
        EvidencePlannerRunner.for_run(
            RUN_ID,
            DiagnosticSettings(_env_file=None),
            project_root,
            model=FunctionModel(lambda _messages, _info: None),
            backend=_PlannerTools(),
        )


def test_for_run_without_a_model_builds_the_settings_model(
    project_root: Path,
) -> None:
    """Benchmark wiring: the factory calls ``for_run`` with no injected model,
    so the settings' OpenAI-compatible endpoint must be assembled here."""

    settings = DiagnosticSettings(_env_file=None)
    runner = EvidencePlannerRunner.for_run(
        RUN_ID, settings, project_root, backend=_PlannerTools()
    )

    assert runner.model_identity == ModelIdentity("openai-compatible", settings.model_name)
    declaration = runner.session.declaration
    assert declaration.model_provider == "openai-compatible"
    assert declaration.model_name == settings.model_name
    # The settings-built client is owned — and closed — by the runner itself.
    assert runner._owned_model_client is not None


def test_diagnose_closes_the_owned_settings_client(project_root: Path) -> None:
    runner = EvidencePlannerRunner.for_run(
        RUN_ID, DiagnosticSettings(_env_file=None), project_root, backend=_PlannerTools()
    )
    client = runner._owned_model_client
    assert client is not None and not client.is_closed()
    # A scripted model keeps the run offline; the owned client still closes.
    runner._model = FunctionModel(_script([("submit_diagnosis", _abstain())]))

    result = asyncio.run(runner.diagnose())

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert client.is_closed()


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


def _forged() -> dict[str, object]:
    forged = "ev_" + "a" * 64
    submission = _confirmed()
    submission["evidence_ids"] = [forged]
    submission["claims"] = [
        {"kind": "ROOT_CAUSE", "root_cause_code": "SOURCE_REQUIRED_FIELD_NULL",
         "evidence_ids": [forged]},
        {"kind": "AFFECTED_ASSET", "asset": DOWNSTREAM, "evidence_ids": [forged]},
    ]
    return submission


def test_a_refused_submission_can_be_retried(project_root: Path) -> None:
    """Audit regression: the first refusal used to end the run."""

    runner = _runner(
        [("submit_diagnosis", _forged()), ("submit_diagnosis", _abstain())], project_root
    )

    result = asyncio.run(runner.diagnose())

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert runner.session.snapshot()["output_retries_used"] == 1
    assert runner.session.final_diagnosis is not None
    assert result.metrics.model_requests >= 2


def test_a_run_of_refused_submissions_fails_closed(project_root: Path) -> None:
    runner = _runner(_always(_forged()), project_root)

    result = asyncio.run(runner.diagnose())

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    assert result.diagnosis.summary == "MODEL_PROTOCOL_ERROR"
    assert result.evidence_records == ()
    assert runner.session.cancellation == "RUN_FAILED"
    assert runner.session.snapshot()["output_retries_used"] == 2
    # The failed run still reports what it spent (audit regression: zeros).
    assert result.metrics.model_requests >= 2
    assert result.metrics.output_tokens > 0


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


def test_plan_events_are_archived_without_counting_as_tool_calls(
    project_root: Path,
) -> None:
    """Audit regression: plan refusals and closes used to vanish from the trace."""

    invalid = ("plan_step", {"tool_name": "run_sql", "arguments": {"query": "select 1"},
                             "intent": "outside the tool set"})
    runner = _runner([*_steps(), invalid, *_close_events(), ("submit_diagnosis", _confirmed())],
                     project_root)

    result = asyncio.run(runner.diagnose())

    plan_events = [event for event in result.trace if event.event_type == "PLAN"]
    kinds = [event.kind for event in plan_events]
    assert kinds == ["STEP"] * 5 + ["CLOSE"] * 2 + ["STATE"]
    refused = [event for event in plan_events if event.accepted is False]
    assert [event.verdict_code for event in refused] == ["PLAN_TOOL_NOT_ALLOWLISTED"]
    assert refused[0].tool_name == "run_sql"
    assert [event.obligation_id for event in plan_events if event.kind == "CLOSE"] == list(
        _obligation_ids()[name] for name in ("node", "profile")
    )
    state = plan_events[-1]
    assert len(state.open_obligations) == 2
    # Plan verdicts stay out of the tool record: four calls, four tool events.
    assert len([event for event in result.trace if event.event_type == "TOOL_CALL"]) == 4
    assert result.metrics.tool_call_attempts == 4
    assert result.trace[-1].event_type == "DIAGNOSIS_TERMINAL"


def test_the_archive_distinguishes_a_satisfied_from_a_revoked_close(
    project_root: Path,
) -> None:
    """Audit regression: both close paths used to archive identically."""

    ids, obligations = _ids(), _obligation_ids()

    def close_events(outcome: str) -> list[tuple[str, dict[str, object]]]:
        if outcome == "SATISFIED":
            return [("close_obligation", {"obligation_id": obligations["node"],
                                          "outcome": "SATISFIED",
                                          "evidence_ids": [ids["node"]]})]
        return [("close_obligation", {"obligation_id": obligations["node"],
                                      "outcome": "REVOKED",
                                      "reason": "the node error is not observable"})]

    runs = {}
    for outcome in ("SATISFIED", "REVOKED"):
        runner = _runner(
            [*_steps(), *close_events(outcome), ("submit_diagnosis", _confirmed())],
            project_root,
        )
        result = asyncio.run(runner.diagnose())
        # Reload from the serialized archive: no controller, no session.
        runs[outcome] = json.loads(json.dumps([event.model_dump(mode="json")
                                              for event in result.trace]))

    assert runs["SATISFIED"] != runs["REVOKED"]

    from pydantic import TypeAdapter

    from data_incident_gym.diagnosis import TraceEvent
    from data_incident_gym.evidence_planner import plan_outcome_summary

    reload_trace = TypeAdapter(TraceEvent)
    summary = {}
    for outcome, payload in runs.items():
        # The whole trace reloads through the discriminated union, exactly as an
        # archived run would be read back.
        trace = [reload_trace.validate_python(event) for event in payload]
        summary[outcome] = plan_outcome_summary(trace, _ids().values())

    assert summary["SATISFIED"]["satisfied"] == 1
    assert summary["SATISFIED"]["revoked"] == 0
    assert summary["SATISFIED"]["closed_by_outcome"] == {"SATISFIED": 1, "REVOKED": 0}
    assert summary["SATISFIED"]["satisfied_uncovered"] == []
    assert summary["REVOKED"]["satisfied"] == 0
    assert summary["REVOKED"]["revoked"] == 1
    assert summary["REVOKED"]["closed_by_outcome"] == {"SATISFIED": 0, "REVOKED": 1}
    assert summary["SATISFIED"]["open"] == summary["REVOKED"]["open"] == 3


def test_a_refused_close_does_not_count_as_closed_after_reload(
    project_root: Path,
) -> None:
    """Audit regression: refused closes used to land in ``closed_by_outcome``."""

    ids, obligations = _ids(), _obligation_ids()
    # Foreign evidence: the lineage receipt did not come from this obligation's
    # last call, so the close is refused and the obligation stays OPEN.
    refused = ("close_obligation", {"obligation_id": obligations["node"],
                                    "outcome": "SATISFIED",
                                    "evidence_ids": [ids["lineage"]]})
    retry = ("close_obligation", {"obligation_id": obligations["node"],
                                  "outcome": "SATISFIED",
                                  "evidence_ids": [ids["node"]]})
    profile = ("close_obligation", {"obligation_id": obligations["profile"],
                                    "outcome": "SATISFIED",
                                    "evidence_ids": [ids["profile"]]})
    runner = _runner(
        [*_steps(), refused, retry, profile, ("submit_diagnosis", _confirmed())],
        project_root,
    )

    result = asyncio.run(runner.diagnose())

    assert result.diagnosis.status is DiagnosisStatus.CONFIRMED
    closes = [event for event in result.trace
              if event.event_type == "PLAN" and event.kind == "CLOSE"]
    assert [(event.accepted, event.verdict_code) for event in closes] == [
        (False, "PLAN_EVIDENCE_NOT_RETURNED_BY_THIS_CALL"),
        (True, None),
        (True, None),
    ]

    # Reload from the serialized archive: no controller, no session.
    payload = json.loads(json.dumps([event.model_dump(mode="json")
                                     for event in result.trace]))
    from pydantic import TypeAdapter

    from data_incident_gym.diagnosis import TraceEvent
    from data_incident_gym.evidence_planner import plan_outcome_summary

    trace = [TypeAdapter(TraceEvent).validate_python(event) for event in payload]
    summary = plan_outcome_summary(trace, ids.values())

    assert summary["close_events"] == 3
    assert summary["requested_by_outcome"] == {"SATISFIED": 3, "REVOKED": 0}
    assert summary["closed_by_outcome"] == {"SATISFIED": 2, "REVOKED": 0}
    assert summary["refused_closes"] == 1
    assert summary["satisfied"] == 2
    assert summary["revoked"] == 0
    assert summary["open"] == 2
