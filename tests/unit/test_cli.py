from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from typer.testing import CliRunner

import data_incident_gym.cli as cli
from data_incident_gym.artifacts import ARTIFACT_FILENAMES
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
from data_incident_gym.evaluation import EvaluationStatus
from data_incident_gym.evaluation_inputs import default_evaluator_identity
from data_incident_gym.evaluation_rescore import CheckChange, ScoreComparison
from data_incident_gym.scenario_certification import (
    CatalogCertificationReport,
    CertificationFinding,
    ReferenceRunSummary,
    ScenarioCertification,
)
from data_incident_gym.scenarios import SUPPORTED_SCENARIO_IDS, load_scenario_spec

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
    for manifest_id in (
        "p1-formal-v2",
        "p1-formal-v3",
        "p1-formal-v4",
        "p1-formal-v5",
        "p1-formal-v6",
        "p1-formal-v7",
        "p1-formal-v8",
        "p1-formal-v9",
        "p1-formal-v10",
        "p1-formal-v11",
        "p1-formal-v12",
        "p1-formal-v13",
        "p1-formal-v14",
        "p1-formal-v15",
        "p1-formal-v16",
        "p1-formal-v17",
        "p1-formal-v18",
        "p1-formal-v19",
        "p1-formal-v20",
        "p1-formal-v21",
        "p1-formal-v22",
        "p1-formal-v23",
    ):
        resolved = _canonical_benchmark_manifest_path(
            Path(f"config/benchmark/{manifest_id}.json")
        )

        assert resolved == (PROJECT_ROOT / f"config/benchmark/{manifest_id}.json").resolve()


def test_benchmark_freeze_passes_the_selected_model_and_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI must hand the builder the endpoint that belongs to the selected
    model — selecting DeepSeek while keeping the default endpoint is exactly
    the crossed pairing the pairing table refuses."""

    captured: dict[str, str] = {}

    def fake_build(
        revision: str,
        *,
        manifest_id: str,
        model_name: str,
        model_base_url: str,
        project_root: object = None,
    ) -> SimpleNamespace:
        captured["revision"] = revision
        captured["manifest_id"] = manifest_id
        captured["model"] = model_name
        captured["base_url"] = model_base_url
        return SimpleNamespace(
            manifest_id=manifest_id,
            digest=lambda: "f" * 64,
            canonical_json=lambda: "{}" + chr(10),
        )

    monkeypatch.setattr(cli, "build_manifest", fake_build)
    monkeypatch.setattr(cli, "verify_manifest", lambda manifest, **_: manifest)
    monkeypatch.setattr(
        cli,
        "freeze_manifest",
        lambda manifest, output, **_: Path("config/benchmark") / f"{manifest.manifest_id}.json",
    )

    result = runner.invoke(
        app,
        [
            "benchmark",
            "freeze",
            "--manifest-id",
            "p1-formal-v24",
            "--implementation-revision",
            "b" * 40,
            "--output",
            "config/benchmark/p1-formal-v24.json",
            "--model",
            "deepseek/deepseek-v4.1-flash",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["model"] == "deepseek/deepseek-v4.1-flash"
    assert captured["base_url"] == "https://api.commandcode.ai/provider/v1"
    assert captured["manifest_id"] == "p1-formal-v24"


def test_benchmark_freeze_refuses_a_model_outside_the_pairing_table() -> None:
    result = runner.invoke(
        app,
        [
            "benchmark",
            "freeze",
            "--manifest-id",
            "p1-formal-v24",
            "--implementation-revision",
            "b" * 40,
            "--output",
            "config/benchmark/p1-formal-v24.json",
            "--model",
            "gpt-9",
        ],
    )

    assert result.exit_code == 1, result.output
    assert "approved pairings" in result.output


def test_canonical_manifest_path_rejects_unapproved_name() -> None:
    with pytest.raises(BenchmarkManifestError):
        _canonical_benchmark_manifest_path(Path("config/benchmark/p1-formal-v31.json"))


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


def test_benchmark_run_prints_stop_reason_for_rolling_window_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = SimpleNamespace(manifest_id="p1-formal-v3")
    run_result = SimpleNamespace(
        status="FAILED",
        terminal_cells=13,
        total_cells=106,
        subset=False,
        model_probe_required=True,
        stop_reason="ROLLING_WINDOW_UNPASSED_PAUSE",
        ledger_path=Path("artifacts/benchmarks/p1-formal-v3/ledger.jsonl"),
    )

    class StubRunner:
        async def run(self):
            return run_result

    monkeypatch.setattr(
        cli,
        "_confirmed_benchmark_manifest",
        lambda path, digest: (path, manifest),
    )
    monkeypatch.setattr(
        cli,
        "create_benchmark_runner",
        lambda loaded, selector=None: StubRunner(),
    )

    result = runner.invoke(
        app,
        [
            "benchmark",
            "run",
            "--manifest",
            "manifest.json",
            "--confirm-sha256",
            "abc",
        ],
    )

    assert result.exit_code == 1
    assert "status: FAILED" in result.stdout
    assert "cells: 13/106" in result.stdout
    assert "stop_reason: ROLLING_WINDOW_UNPASSED_PAUSE" in result.stdout


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


def test_benchmark_partial_reports_incomplete_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = SimpleNamespace(manifest_id="p1-formal-v1")
    captured: dict[str, object] = {}

    def _stub_partial(loaded: object, suite_root: Path) -> dict[str, object]:
        captured["suite_root"] = suite_root
        return {
            "reliability": {
                "protocol_version": "p1.reliability.v1",
                "groups_complete": 11,
                "groups_incomplete": 1,
                "macro_pass_hat": {
                    "1": {"value": 0.75, "groups": 11, "trials": 33},
                    "2": {"value": None, "groups": 0, "trials": 0},
                    "3": {"value": None, "groups": 0, "trials": 0},
                },
            },
            "cells": [
                {"run_id": "a" * 32, "state": "MISSING"},
                {"run_id": "b" * 32, "state": "SCORED"},
            ],
        }

    monkeypatch.setattr(
        cli,
        "_confirmed_benchmark_manifest",
        lambda path, digest: (path, manifest),
    )
    monkeypatch.setattr(cli, "analyze_partial_suite", _stub_partial)

    result = runner.invoke(
        app,
        ["benchmark", "partial", "--manifest", "manifest.json", "--confirm-sha256", "abc"],
    )

    assert result.exit_code == 0
    assert "groups: 11 complete / 1 incomplete" in result.stdout
    assert "macro pass^1: 0.750 (11 groups / 33 trials)" in result.stdout
    assert "macro pass^2: n/a" in result.stdout
    assert "missing cells: 1" in result.stdout
    assert captured["suite_root"] == cli.PROJECT_ROOT / "artifacts" / "benchmarks" / "p1-formal-v1"


OFFLINE_RUN_ID = "b" * 32
OFFLINE_SCORE_ID = "d" * 64


def test_eval_score_wires_offline_scoring_and_reports_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    score_dir = tmp_path / "artifacts" / "rescores" / OFFLINE_RUN_ID / OFFLINE_SCORE_ID

    def _stub_score(project_root: Path, run_id: str) -> SimpleNamespace:
        captured["project_root"] = project_root
        captured["run_id"] = run_id
        return SimpleNamespace(
            created=True,
            run_id=run_id,
            score_id=OFFLINE_SCORE_ID,
            score_dir=score_dir,
            evaluation=SimpleNamespace(status=EvaluationStatus.FAILED),
            diff=SimpleNamespace(available=True, unavailable_reason=None),
            changed_check_codes=(),
        )

    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cli, "score_run_offline", _stub_score)

    result = runner.invoke(app, ["eval", "score", OFFLINE_RUN_ID])

    assert result.exit_code == 0
    assert captured == {"project_root": tmp_path, "run_id": OFFLINE_RUN_ID}
    assert f"score_id: {OFFLINE_SCORE_ID}" in result.stdout
    assert "changed_checks: 无" in result.stdout


def test_eval_score_rejects_unknown_scorer() -> None:
    result = runner.invoke(app, ["eval", "score", OFFLINE_RUN_ID, "--scorer", "unknown"])

    assert result.exit_code == 1
    assert "SCORER_UNKNOWN" in result.stderr


def test_eval_score_reports_unavailable_diff(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def _stub_score(project_root: Path, run_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            created=True,
            run_id=run_id,
            score_id=OFFLINE_SCORE_ID,
            score_dir=tmp_path / "artifacts" / "rescores" / run_id / OFFLINE_SCORE_ID,
            evaluation=SimpleNamespace(status=EvaluationStatus.FAILED),
            diff=SimpleNamespace(
                available=False,
                unavailable_reason="ARCHIVED_EVALUATION_MISSING",
            ),
            changed_check_codes=(),
        )

    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cli, "score_run_offline", _stub_score)

    result = runner.invoke(app, ["eval", "score", OFFLINE_RUN_ID])

    assert result.exit_code == 0
    assert "无法比较" in result.stdout
    assert "逐项一致" not in result.stdout


def test_eval_score_classifies_runs_without_scoring_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    artifact_dir = tmp_path / "artifacts" / OFFLINE_RUN_ID
    artifact_dir.mkdir(parents=True)
    for name in ARTIFACT_FILENAMES:
        (artifact_dir / name).write_text("{}\n", encoding="utf-8")

    result = runner.invoke(app, ["eval", "score", OFFLINE_RUN_ID])

    assert result.exit_code == 1
    assert "PARTIAL_ANALYSIS" in result.stderr


def test_eval_compare_scores_reports_changed_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity_a = default_evaluator_identity()
    identity_b = identity_a.model_copy(update={"name": "CONTROLLED_FLIP"})
    comparison = ScoreComparison(
        run_id=OFFLINE_RUN_ID,
        score_id_a="a" * 64,
        score_id_b="b" * 64,
        status_a="FAILED",
        status_b="FAILED",
        scorer_a=identity_a,
        scorer_b=identity_b,
        changes=(
            CheckChange(
                code="ROOT_CAUSE_ACCEPTED",
                kind="EVIDENCE",
                change="FAILED_TO_PASSED",
                before_passed=False,
                after_passed=True,
                before_expected=("ACCEPTABLE_ROOT_CAUSE",),
                after_expected=("ACCEPTABLE_ROOT_CAUSE",),
                before_actual=("MISMATCH",),
                after_actual=("MATCH",),
            ),
        ),
    )
    monkeypatch.setattr(cli, "compare_offline_scores", lambda *args: comparison)

    result = runner.invoke(
        app,
        ["eval", "compare-scores", OFFLINE_RUN_ID, "a" * 64, "b" * 64],
    )

    assert result.exit_code == 0
    assert "ROOT_CAUSE_ACCEPTED" in result.stdout
    assert "FAILED_TO_PASSED" in result.stdout


def test_certify_command_is_registered_and_rejects_unknown_cases() -> None:
    help_result = runner.invoke(app, ["certify", "--help"])

    assert help_result.exit_code == 0
    assert "--case" in click.unstyle(help_result.stdout)

    result = runner.invoke(app, ["certify", "--case", "not_a_scenario"])

    assert result.exit_code == 1
    assert "UNKNOWN_SCENARIO" in result.stderr


def test_certify_writes_report_and_reports_uncertified_cases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run = ReferenceRunSummary(
        run_id="a" * 32,
        evaluation_status="FAILED",
        diagnosis_status="CONFIRMED",
        root_cause_code="SOURCE_REQUIRED_FIELD_NULL",
        affected_assets=("model.jaffle_shop.orders",),
        tool_calls=4,
        successful_tool_calls=4,
        tool_error_codes=(),
        failed_check_codes=("STATUS_EXACT",),
    )
    report = CatalogCertificationReport(
        created_at=datetime(2026, 9, 15, tzinfo=UTC),
        entries=(
            ScenarioCertification(
                case_id=SUPPORTED_SCENARIO_IDS[0],
                scenario_digest=load_scenario_spec(SUPPORTED_SCENARIO_IDS[0]).digest(),
                certified=True,
                findings=(CertificationFinding(code="EVALUATION_PASSED", satisfied=True),),
                run=run,
            ),
            ScenarioCertification(
                case_id=SUPPORTED_SCENARIO_IDS[1],
                scenario_digest=load_scenario_spec(SUPPORTED_SCENARIO_IDS[1]).digest(),
                certified=False,
                findings=(
                    CertificationFinding(
                        code="EVALUATION_PASSED",
                        satisfied=False,
                        detail="evaluator rejected the reference run",
                    ),
                ),
                failure_classes=("SCORING",),
                run=run,
            ),
        ),
    )
    captured: dict[str, object] = {}

    async def _stub_catalog(case_ids: object, *, project_root: Path) -> CatalogCertificationReport:
        captured["case_ids"] = case_ids
        captured["project_root"] = project_root
        return report

    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cli, "certify_catalog", _stub_catalog)

    result = runner.invoke(app, ["certify", "--case", SUPPORTED_SCENARIO_IDS[0]])

    assert result.exit_code == 1
    assert captured["case_ids"] == (SUPPORTED_SCENARIO_IDS[0],)
    assert captured["project_root"] == tmp_path
    assert "certified: 1/2" in result.stdout
    assert "SCORING" in result.stdout

    report_path = tmp_path / "artifacts" / "certifications" / f"{SUPPORTED_SCENARIO_IDS[0]}.json"
    assert report_path.is_file()


def test_eval_compare_scores_reports_details_only_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = default_evaluator_identity()
    comparison = ScoreComparison(
        run_id=OFFLINE_RUN_ID,
        score_id_a="a" * 64,
        score_id_b="b" * 64,
        status_a="FAILED",
        status_b="FAILED",
        scorer_a=identity,
        scorer_b=identity.model_copy(update={"name": "CONTROLLED_DETAILS"}),
        changes=(
            CheckChange(
                code="TOOL_ALLOWLIST_EXACT",
                kind="EVIDENCE",
                change="UNCHANGED",
                details_changed=True,
                before_passed=True,
                after_passed=True,
                before_expected=("ALLOWED_TOOLS",),
                after_expected=("ALLOWED_TOOLS",),
                before_actual=(),
                after_actual=("REVIEWED",),
            ),
        ),
    )
    monkeypatch.setattr(cli, "compare_offline_scores", lambda *args: comparison)

    result = runner.invoke(
        app,
        ["eval", "compare-scores", OFFLINE_RUN_ID, "a" * 64, "b" * 64],
    )

    assert result.exit_code == 0
    assert "TOOL_ALLOWLIST_EXACT" in result.stdout
    assert "expected/actual 变化" in result.stdout
    assert "逐项一致" not in result.stdout
