from __future__ import annotations

from pathlib import Path

import click
import pytest
from typer.testing import CliRunner

from data_incident_gym.benchmark_manifest import MANIFEST_PATH, BenchmarkManifestError
from data_incident_gym.cli import (
    CliStrategy,
    _canonical_benchmark_manifest_path,
    _diagnostic_strategy,
    app,
)
from data_incident_gym.config import PROJECT_ROOT
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
    help_text = click.unstyle(result.stdout)
    assert "--strategy" in help_text
    assert all(case_id in help_text for case_id in SUPPORTED_SCENARIO_IDS)


def test_cli_strategy_maps_to_the_common_diagnostic_strategy() -> None:
    assert _diagnostic_strategy(CliStrategy.STATIC_SKILL) is DiagnosticStrategy.STATIC_SKILL
    assert (
        _diagnostic_strategy(CliStrategy.DIAGNOSTIC_KERNEL)
        is DiagnosticStrategy.DIAGNOSTIC_KERNEL
    )


def test_cli_benchmark_commands_use_the_canonical_manifest_path() -> None:
    assert _canonical_benchmark_manifest_path(MANIFEST_PATH) == (
        PROJECT_ROOT / MANIFEST_PATH
    ).resolve()

    with pytest.raises(BenchmarkManifestError, match="config/benchmark/p1-formal-v1.json"):
        _canonical_benchmark_manifest_path(Path("other" , "manifest.json"))
