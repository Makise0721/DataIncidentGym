from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

import data_incident_gym.cli as cli
from data_incident_gym.baseline import BaselineError, make_baseline_summary
from data_incident_gym.lab import InvalidIncidentState

runner = CliRunner()


def _summary() -> SimpleNamespace:
    summary = make_baseline_summary("analytics", ())
    return SimpleNamespace(
        schema=summary.schema,
        relations=summary.relations,
        fingerprint=summary.fingerprint,
    )


def test_pipeline_build_success_does_not_call_real_infrastructure(monkeypatch) -> None:
    summary = _summary()

    class FakeBuilder:
        def build(self):
            return summary

    monkeypatch.setattr(cli, "create_baseline_builder", lambda: FakeBuilder())

    result = runner.invoke(cli.app, ["pipeline", "build"])

    assert result.exit_code == 0
    assert "健康基线构建成功" in result.stdout
    assert "schema: analytics" in result.stdout
    assert "relations: 0" in result.stdout
    assert f"fingerprint: {summary.fingerprint}" in result.stdout
    assert "summary: .dig/baseline-summary.json" in result.stdout


def test_pipeline_build_baseline_error_is_chinese_stderr_without_traceback(monkeypatch) -> None:
    class FakeBuilder:
        def build(self):
            raise BaselineError("执行 dbt 失败")

    monkeypatch.setattr(cli, "create_baseline_builder", lambda: FakeBuilder())

    result = runner.invoke(cli.app, ["pipeline", "build"])

    assert result.exit_code != 0
    assert "健康基线构建失败：执行 dbt 失败" in result.stderr
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout


def test_help_is_in_chinese_for_app_pipeline_and_build() -> None:
    app_help = runner.invoke(cli.app, ["--help"])
    pipeline_help = runner.invoke(cli.app, ["pipeline", "--help"])
    build_help = runner.invoke(cli.app, ["pipeline", "build", "--help"])

    assert app_help.exit_code == 0
    assert "可复现的数据事故诊断实验场" in app_help.stdout
    assert pipeline_help.exit_code == 0
    assert "构建并检查 dbt 数据管道" in pipeline_help.stdout
    assert build_help.exit_code == 0
    assert "重置 seeds，运行 dbt build，并生成健康基线摘要" in build_help.stdout


def test_lab_commands_delegate_without_real_infrastructure(monkeypatch) -> None:
    class FakeLab:
        def reset(self, case_id: str):
            assert case_id == "schema_rename_payment_amount"
            return SimpleNamespace(state="HEALTHY", fingerprint="a" * 64)

        def inject(self, case_id: str):
            assert case_id == "schema_rename_payment_amount"
            return SimpleNamespace(state="INJECTED", fingerprint="b" * 64)

        def build(self, case_id: str):
            assert case_id == "schema_rename_payment_amount"
            return SimpleNamespace(
                run_id="0123456789abcdef0123456789abcdef",
                dbt_exit_code=1,
                artifact_dir=Path(".dig/lab/runs/0123456789abcdef0123456789abcdef"),
                verification=SimpleNamespace(status="EXPECTED_FAILURE"),
            )

    monkeypatch.setattr(cli, "create_incident_lab", lambda: FakeLab())

    reset = runner.invoke(cli.app, ["lab", "reset", "schema_rename_payment_amount"])
    inject = runner.invoke(cli.app, ["lab", "inject", "schema_rename_payment_amount"])
    build = runner.invoke(cli.app, ["lab", "build", "schema_rename_payment_amount"])

    assert reset.exit_code == 0
    assert "HEALTHY" in reset.stdout
    assert inject.exit_code == 0
    assert "INJECTED" in inject.stdout
    assert build.exit_code == 0
    assert "EXPECTED_FAILURE" in build.stdout
    assert "dbt_exit_code: 1" in build.stdout


def test_lab_error_is_chinese_stderr_with_nonzero_exit(monkeypatch) -> None:
    class FakeLab:
        def inject(self, case_id: str):
            raise InvalidIncidentState("当前状态：INJECTED")

    monkeypatch.setattr(cli, "create_incident_lab", lambda: FakeLab())

    result = runner.invoke(
        cli.app,
        ["lab", "inject", "schema_rename_payment_amount"],
    )

    assert result.exit_code != 0
    assert "INVALID_INCIDENT_STATE" in result.stderr
    assert "当前状态：INJECTED" in result.stderr
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout


def test_lab_help_is_chinese_and_lists_only_m2_actions() -> None:
    lab_help = runner.invoke(cli.app, ["lab", "--help"])

    assert lab_help.exit_code == 0
    assert "重置、注入并复现固定数据故障" in lab_help.stdout
    assert "reset" in lab_help.stdout
    assert "inject" in lab_help.stdout
    assert "build" in lab_help.stdout
    for forbidden in ("replay", "evidence", "diagnose", "eval"):
        assert forbidden not in lab_help.stdout
