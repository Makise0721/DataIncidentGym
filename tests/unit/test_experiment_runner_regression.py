"""Offline acceptance for the experiment contract on the real runner:
108-cell scheduling, archive/ledger identity, rolling-window pause and the
read-only partial entry (proposal §2.2)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from data_incident_gym.benchmark_report import analyze_partial_suite
from data_incident_gym.diagnosis import DiagnosticStrategy
from data_incident_gym.diagnostic_agent import ModelIdentity
from data_incident_gym.planner_comparison_manifest import build_experiment_manifest
from data_incident_gym.planner_probe import PlannerProbeResult

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unit.test_benchmark_runner import (  # noqa: E402
    _doctor_result,
    _FakeWriter,
    _runner,
    _scripted_window_actions,
    _ScriptedEvaluationRunner,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _experiment():
    return build_experiment_manifest("a" * 40, project_root=PROJECT_ROOT)


def _passing_probe_factory(manifest):
    """The offline suite needs the probe seam wired, not a real model loop."""

    async def factory():
        return (
            ModelIdentity(
                provider=manifest.model_configuration.provider,
                model=manifest.model_configuration.model,
            ),
            PlannerProbeResult(
                passed=True,
                observed="PLAN_LOOP_COMPLETED",
                detail={"plan_step_receipts": 1},
            ),
        )

    return factory


def test_experiment_full_108_cell_suite_completes_offline(tmp_path: Path) -> None:
    manifest = _experiment()
    calls: list[tuple[str, DiagnosticStrategy, str]] = []
    doctor_calls: list[str] = []
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(),
        calls=calls,
        doctor_calls=doctor_calls,
        planner_probe_factory=_passing_probe_factory(manifest),
    )

    asyncio.run(runner.preflight())
    result = asyncio.run(runner.run())

    assert result.status == "COMPLETED"
    assert result.terminal_cells == 108
    assert result.completed_cells == 108
    assert result.failed_cells == 0
    assert len(calls) == 108
    assert [item[2] for item in calls] == [cell.run_id for cell in manifest.cells]
    strategy_counts = {
        strategy: sum(item[1] is strategy for item in calls)
        for strategy in (
            DiagnosticStrategy.EVIDENCE_PLANNER,
            DiagnosticStrategy.DIAGNOSTIC_KERNEL,
            DiagnosticStrategy.STATIC_SKILL,
        )
    }
    assert strategy_counts == {
        DiagnosticStrategy.EVIDENCE_PLANNER: 36,
        DiagnosticStrategy.DIAGNOSTIC_KERNEL: 36,
        DiagnosticStrategy.STATIC_SKILL: 36,
    }
    ledger_path = tmp_path / "artifacts" / "benchmarks" / "p1-planner-compare-v1" / "ledger.jsonl"
    ledger_lines = ledger_path.read_text(encoding="utf-8").splitlines()
    assert len(ledger_lines) == 216


def test_experiment_rolling_window_pauses_before_the_next_cell(tmp_path: Path) -> None:
    manifest = _experiment()
    actions = _scripted_window_actions(manifest, model_backed_evaluation_failures=10)
    scripted = _ScriptedEvaluationRunner(actions)
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(),
        calls=[],
        doctor_calls=[],
        writer=_FakeWriter(),
        evaluation_runner_factory=lambda: scripted,
        planner_probe_factory=_passing_probe_factory(manifest),
    )

    asyncio.run(runner.preflight())
    result = asyncio.run(runner.run())

    assert result.status != "COMPLETED"
    assert result.terminal_cells == 12
    assert result.stop_reason == "ROLLING_WINDOW_UNPASSED_PAUSE"


def test_experiment_partial_entry_reads_a_paused_suite(tmp_path: Path) -> None:
    manifest = _experiment()
    actions = _scripted_window_actions(manifest, model_backed_evaluation_failures=10)
    scripted = _ScriptedEvaluationRunner(actions)
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(),
        calls=[],
        doctor_calls=[],
        writer=_FakeWriter(),
        evaluation_runner_factory=lambda: scripted,
        planner_probe_factory=_passing_probe_factory(manifest),
    )

    asyncio.run(runner.preflight())
    asyncio.run(runner.run())

    suite_root = tmp_path / "artifacts" / "benchmarks" / "p1-planner-compare-v1"
    result = analyze_partial_suite(manifest, suite_root)

    reliability = result["reliability"]
    assert reliability["groups_complete"] + reliability["groups_incomplete"] > 0
    assert reliability["protocol_version"]
