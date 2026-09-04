from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from typer.testing import CliRunner

import data_incident_gym.cli as cli
from data_incident_gym.benchmark_manifest import MANIFEST_PATH, BenchmarkManifestError
from data_incident_gym.cli import (
    CliStrategy,
    _canonical_benchmark_manifest_path,
    _diagnostic_strategy,
    app,
)
from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.diagnosis import DiagnosticStrategy
from data_incident_gym.doctor import DoctorStatus
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

    with pytest.raises(BenchmarkManifestError, match="approved-manifest-id"):
        _canonical_benchmark_manifest_path(Path("other" , "manifest.json"))


def test_canonical_manifest_path_accepts_approved_rerun_identities() -> None:
    for manifest_id in ("p1-formal-v2", "p1-formal-v3", "p1-formal-v4"):
        resolved = _canonical_benchmark_manifest_path(
            Path(f"config/benchmark/{manifest_id}.json")
        )

        assert resolved == (PROJECT_ROOT / f"config/benchmark/{manifest_id}.json").resolve()


def test_canonical_manifest_path_rejects_unapproved_name() -> None:
    with pytest.raises(BenchmarkManifestError):
        _canonical_benchmark_manifest_path(Path("config/benchmark/p1-formal-v9.json"))


def test_confirmed_manifest_rejects_filename_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "p1-formal-v1.json"
    manifest_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli, "_canonical_benchmark_manifest_path", lambda _: manifest_path)
    monkeypatch.setattr(
        cli,
        "load_manifest",
        lambda _: SimpleNamespace(manifest_id="p1-formal-v2"),
    )

    with pytest.raises(BenchmarkManifestError, match="file name must match"):
        cli._confirmed_benchmark_manifest(
            manifest_path,
            hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        )


def test_benchmark_help_exposes_preflight_report_and_one_shot_run() -> None:
    result = runner.invoke(app, ["benchmark", "--help"])

    assert result.exit_code == 0
    help_text = click.unstyle(result.stdout)
    assert "preflight" in help_text
    assert "report" in help_text
    assert "run" in help_text


@pytest.mark.parametrize("force_color", [None, "1"])
def test_benchmark_run_and_preflight_help_expose_subset_options(force_color: str | None) -> None:
    for command in ("run", "preflight"):
        result = runner.invoke(
            app, ["benchmark", command, "--help"], env={"FORCE_COLOR": force_color}
        )

        assert result.exit_code == 0
        help_text = click.unstyle(result.stdout)
        assert "--only-strategy" in help_text
        assert "--only-sequence" in help_text


def test_benchmark_archive_command_is_registered() -> None:
    result = runner.invoke(app, ["benchmark", "--help"])

    assert result.exit_code == 0
    assert "archive" in result.stdout


def test_cell_selector_maps_kebab_strategy_names() -> None:
    selector = cli._cell_selector(
        SimpleNamespace(manifest_id="p1-formal-v3"), ["fixed-rule"], []
    )

    assert selector is not None
    assert selector.strategies == (DiagnosticStrategy.FIXED_RULE,)
    assert selector.manifest_id == "p1-formal-v3"


def test_cell_selector_is_none_without_flags() -> None:
    assert cli._cell_selector(SimpleNamespace(manifest_id="p1-formal-v2"), [], []) is None


def test_benchmark_preflight_accepts_fixed_rule_scope_without_model_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = SimpleNamespace(manifest_id="p1-formal-v3")
    receipt = SimpleNamespace(
        result=SimpleNamespace(status=DoctorStatus.FAILED),
        model_probe_required=False,
    )
    captured = []

    class StubRunner:
        async def preflight(self):
            return receipt

    monkeypatch.setattr(
        cli,
        "_confirmed_benchmark_manifest",
        lambda path, digest: (path, manifest),
    )
    monkeypatch.setattr(
        cli,
        "create_benchmark_runner",
        lambda loaded, selector=None: captured.append(selector) or StubRunner(),
    )
    monkeypatch.setattr(cli, "is_receipt_acceptable", lambda value: value is receipt)

    result = runner.invoke(
        app,
        [
            "benchmark",
            "preflight",
            "--manifest",
            "manifest.json",
            "--confirm-sha256",
            "abc",
            "--only-strategy",
            "fixed-rule",
        ],
    )

    assert result.exit_code == 0
    assert captured[0].strategies == (DiagnosticStrategy.FIXED_RULE,)
    assert "status: PASSED" in result.stdout
    assert "doctor_status: FAILED" in result.stdout
    assert "model_probe_required: False" in result.stdout
    assert "started_cells: 0" in result.stdout


def test_benchmark_report_uses_confirmed_manifest_and_read_only_reporter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest = SimpleNamespace(manifest_id="p1-formal-v1")
    summary = tmp_path / "summary.json"
    report = tmp_path / "report.md"
    monkeypatch.setattr(
        cli,
        "_confirmed_benchmark_manifest",
        lambda path, digest: (path, manifest),
    )
    monkeypatch.setattr(
        cli,
        "create_benchmark_reporter",
        lambda loaded: SimpleNamespace(write=lambda: (summary, report)),
    )

    result = runner.invoke(
        app,
        ["benchmark", "report", "--manifest", "manifest.json", "--confirm-sha256", "abc"],
    )

    assert result.exit_code == 0
    assert str(summary) in result.stdout
    assert str(report) in result.stdout
