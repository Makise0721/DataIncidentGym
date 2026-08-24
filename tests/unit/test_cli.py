from types import SimpleNamespace

from typer.testing import CliRunner

import data_incident_gym.cli as cli
from data_incident_gym.baseline import BaselineError, make_baseline_summary

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
