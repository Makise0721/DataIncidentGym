from __future__ import annotations

from typer.testing import CliRunner

from data_incident_gym.cli import CliStrategy, _diagnostic_strategy, app
from data_incident_gym.diagnosis import DiagnosticStrategy
from data_incident_gym.scenarios import SUPPORTED_SCENARIO_IDS

runner = CliRunner()


def test_top_level_help_lists_m7_commands_and_scenarios() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "diagnose" in result.stdout
    assert "doctor" in result.stdout


def test_eval_help_exposes_both_strategies_and_catalog() -> None:
    result = runner.invoke(app, ["eval", "run", "--help"])

    assert result.exit_code == 0
    assert "--strategy" in result.stdout
    assert all(case_id in result.stdout for case_id in SUPPORTED_SCENARIO_IDS)


def test_cli_strategy_maps_to_the_common_diagnostic_strategy() -> None:
    assert _diagnostic_strategy(CliStrategy.STATIC_SKILL) is DiagnosticStrategy.STATIC_SKILL
    assert (
        _diagnostic_strategy(CliStrategy.DIAGNOSTIC_KERNEL)
        is DiagnosticStrategy.DIAGNOSTIC_KERNEL
    )
