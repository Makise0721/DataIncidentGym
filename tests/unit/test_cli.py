import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import data_incident_gym.cli as cli
from data_incident_gym.baseline import BaselineError, make_baseline_summary
from data_incident_gym.diagnosis import Diagnosis, DiagnosisMetrics, DiagnosisRunResult
from data_incident_gym.evaluation import (
    EvaluationCheck,
    EvaluationCheckCode,
    EvaluationResult,
    EvaluationStatus,
)
from data_incident_gym.evaluation_runner import EvaluationAttemptResult
from data_incident_gym.incidents import IncidentCaseError
from data_incident_gym.lab import InvalidIncidentState
from data_incident_gym.run_context import ActiveRun, RunContextError

runner = CliRunner()
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _plain_help(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


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
    assert "故障实验失败" in result.stderr
    assert "INVALID_INCIDENT_STATE" in result.stderr
    assert "当前状态：INJECTED" in result.stderr
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout


def test_incident_case_error_uses_stable_error_code(monkeypatch) -> None:
    class FakeLab:
        def reset(self, case_id: str):
            raise IncidentCaseError("TEST_REDACTED_VALUE")

    monkeypatch.setattr(cli, "create_incident_lab", lambda: FakeLab())

    result = runner.invoke(
        cli.app,
        ["lab", "reset", "schema_rename_payment_amount"],
    )

    assert result.exit_code != 0
    assert "故障实验失败" in result.stderr
    assert "INCIDENT_CASE_ERROR" in result.stderr
    assert "TEST_REDACTED_VALUE" in result.stderr
    assert "Traceback" not in result.stderr


def test_lab_help_is_chinese_and_lists_only_m2_actions() -> None:
    lab_help = runner.invoke(cli.app, ["lab", "--help"])

    assert lab_help.exit_code == 0
    assert "重置、注入并复现固定数据故障" in lab_help.stdout
    assert "reset" in lab_help.stdout
    assert "inject" in lab_help.stdout
    assert "build" in lab_help.stdout
    for forbidden in (
        "replay",
        "evidence",
        "diagnose",
        "eval",
        "--sql",
        "--table",
        "--column",
        "--skip-seed",
        "--run-id",
        "--path",
    ):
        assert forbidden not in lab_help.stdout


def test_lab_registers_only_reset_inject_and_build() -> None:
    assert {
        command.name for command in cli.lab_app.registered_commands
    } == {"reset", "inject", "build"}


def test_lab_rejects_unscoped_options_and_extra_arguments_before_delegation(
    monkeypatch,
) -> None:
    def fail_if_called():
        raise AssertionError("CLI parser should reject the invocation first")

    monkeypatch.setattr(cli, "create_incident_lab", fail_if_called)
    invalid_invocations = (
        ["lab", "reset", "schema_rename_payment_amount", "--sql", "select 1"],
        ["lab", "inject", "schema_rename_payment_amount", "--table", "raw_payments"],
        ["lab", "build", "schema_rename_payment_amount", "--column", "amount"],
        ["lab", "reset", "schema_rename_payment_amount", "--skip-seed"],
        ["lab", "reset", "schema_rename_payment_amount", "--run-id", "run"],
        ["lab", "reset", "schema_rename_payment_amount", "--path", "run"],
        ["lab", "reset", "schema_rename_payment_amount", "extra"],
    )

    for invocation in invalid_invocations:
        result = runner.invoke(cli.app, invocation)
        assert result.exit_code == 2


def _diagnosis(status: str) -> Diagnosis:
    if status == "CONFIRMED":
        return Diagnosis(
            status=status,
            incident_case_id="schema_rename_payment_amount",
            run_id="a" * 32,
            root_cause_code="SOURCE_SCHEMA_COLUMN_RENAMED",
            summary="Evidence confirms the incident.",
            affected_assets=("stg_payments",),
            evidence_ids=("ev_" + "b" * 64,),
            recommended_actions=("Collect additional evidence before making a change.",),
            confidence=0.9,
        )
    if status == "INSUFFICIENT_EVIDENCE":
        return Diagnosis(
            status=status,
            incident_case_id="schema_rename_payment_amount",
            run_id="a" * 32,
            root_cause_code=None,
            summary="INSUFFICIENT_EVIDENCE",
            affected_assets=(),
            evidence_ids=(),
            recommended_actions=("Collect additional evidence before making a change.",),
            confidence=0.0,
        )
    return Diagnosis(
        status="MODEL_ERROR",
        incident_case_id="schema_rename_payment_amount",
        run_id="a" * 32,
        root_cause_code=None,
        summary="MODEL_RUNTIME_ERROR",
        affected_assets=(),
        evidence_ids=(),
        recommended_actions=("Collect additional evidence before making a change.",),
        confidence=0.0,
    )


def _diagnosis_result(status: str) -> DiagnosisRunResult:
    return DiagnosisRunResult(
        diagnosis=_diagnosis(status),
        evidence_records=(),
        trace=(),
        metrics=DiagnosisMetrics(
            provider="openai-compatible",
            model="gemma4:e4b",
            model_requests=1,
            input_tokens=0,
            output_tokens=0,
            tool_call_attempts=0,
            successful_tool_calls=0,
            elapsed_ms=1,
        ),
    )


def test_top_level_help_adds_only_diagnose_and_diagnose_options_are_bounded() -> None:
    app_help = runner.invoke(cli.app, ["--help"])
    diagnose_help = runner.invoke(cli.app, ["diagnose", "--help"])
    diagnose_help_text = _plain_help(diagnose_help.stdout)

    assert app_help.exit_code == 0
    assert "diagnose" in app_help.stdout
    assert {command.name for command in cli.app.registered_commands} == {"diagnose"}
    assert diagnose_help.exit_code == 0
    assert "case_id" in diagnose_help_text
    assert "--run-id" in diagnose_help_text
    for forbidden in (
        "--path",
        "--sql",
        "--table",
        "--prompt",
        "--model",
        "--base-url",
        "--budget",
    ):
        assert forbidden not in diagnose_help_text


def test_diagnose_active_and_explicit_run_use_only_case_and_optional_run_id(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class FakeRunner:
        async def diagnose(self, case_id: str):
            calls.append(("diagnose", case_id))
            return _diagnosis_result("INSUFFICIENT_EVIDENCE")

    def fake_factory(run_id: str):
        calls.append(("runner", run_id))
        return FakeRunner()

    monkeypatch.setattr(cli, "create_diagnosis_runner", fake_factory)
    monkeypatch.setattr(
        cli,
        "resolve_active_run",
        lambda **_: ActiveRun(
            "schema_rename_payment_amount", "a" * 32, "m4.active_fault_run.v1", "EXPECTED_FAILURE"
        ),
    )

    active = runner.invoke(cli.app, ["diagnose", "schema_rename_payment_amount"])
    explicit = runner.invoke(
        cli.app,
        ["diagnose", "schema_rename_payment_amount", "--run-id", "a" * 32],
    )

    assert active.exit_code == 2
    assert explicit.exit_code == 2
    assert calls == [
        ("runner", "a" * 32),
        ("diagnose", "schema_rename_payment_amount"),
        ("runner", "a" * 32),
        ("diagnose", "schema_rename_payment_amount"),
    ]


def test_diagnose_confirmed_prints_chinese_message_and_strict_json(monkeypatch) -> None:
    class FakeRunner:
        async def diagnose(self, case_id: str):
            return _diagnosis_result("CONFIRMED")

    monkeypatch.setattr(cli, "create_diagnosis_runner", lambda _: FakeRunner())
    result = runner.invoke(
        cli.app, ["diagnose", "schema_rename_payment_amount", "--run-id", "a" * 32]
    )

    assert result.exit_code == 0
    assert "诊断完成" in result.stdout
    payload = json.loads(result.stdout[result.stdout.index("{") :])
    assert payload == _diagnosis("CONFIRMED").model_dump(mode="json")
    assert "trace" not in payload
    assert "metrics" not in payload


def test_diagnose_insufficient_is_structured_exit_2(monkeypatch) -> None:
    class FakeRunner:
        async def diagnose(self, case_id: str):
            return _diagnosis_result("INSUFFICIENT_EVIDENCE")

    monkeypatch.setattr(cli, "create_diagnosis_runner", lambda _: FakeRunner())
    result = runner.invoke(
        cli.app, ["diagnose", "schema_rename_payment_amount", "--run-id", "a" * 32]
    )

    assert result.exit_code == 2
    assert "INSUFFICIENT_EVIDENCE" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


@pytest.mark.parametrize("status", ["MODEL_ERROR"])
def test_diagnose_model_error_is_safe_exit_1(monkeypatch, status: str) -> None:
    class FakeRunner:
        async def diagnose(self, case_id: str):
            return _diagnosis_result(status)

    monkeypatch.setattr(cli, "create_diagnosis_runner", lambda _: FakeRunner())
    result = runner.invoke(
        cli.app, ["diagnose", "schema_rename_payment_amount", "--run-id", "a" * 32]
    )

    assert result.exit_code == 1
    assert "MODEL_RUNTIME_ERROR" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_diagnose_preflight_and_provider_errors_are_redacted(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "resolve_active_run",
        lambda **_: (_ for _ in ()).throw(
            RunContextError("provider=raw password=TEST_REDACTED_VALUE C:\\secret\\run.json")
        ),
    )
    preflight = runner.invoke(cli.app, ["diagnose", "schema_rename_payment_amount"])

    assert preflight.exit_code == 1
    assert "诊断失败" in preflight.stderr
    assert "TEST_REDACTED_VALUE" not in preflight.stdout + preflight.stderr
    assert "C:\\secret\\run.json" not in preflight.stdout + preflight.stderr
    assert "Traceback" not in preflight.stdout + preflight.stderr

    class FailingRunner:
        async def diagnose(self, case_id: str):
            raise RuntimeError(
                "provider raw exception api_key=TEST_REDACTED_VALUE C:\\secret\\trace.log"
            )

    monkeypatch.setattr(cli, "create_diagnosis_runner", lambda _: FailingRunner())
    provider = runner.invoke(
        cli.app,
        ["diagnose", "schema_rename_payment_amount", "--run-id", "a" * 32],
    )
    assert provider.exit_code == 1
    assert "TEST_REDACTED_VALUE" not in provider.stdout + provider.stderr
    assert "C:\\secret\\trace.log" not in provider.stdout + provider.stderr
    assert "provider raw exception" not in provider.stdout + provider.stderr
    assert "Traceback" not in provider.stdout + provider.stderr


def _evaluation(status: EvaluationStatus) -> EvaluationResult:
    failed_code = (
        EvaluationCheckCode.DIAGNOSIS_CONFIRMED
        if status is EvaluationStatus.FAILED
        else None
    )
    checks = tuple(
        EvaluationCheck(
            code=code,
            passed=code is not failed_code,
            expected=(),
            actual=(),
            reason_code=f"{code.value}_{'FAILED' if code is failed_code else 'PASSED'}",
        )
        for code in EvaluationCheckCode
    )
    return EvaluationResult(
        schema_version="m5.evaluation.v1",
        incident_case_id="schema_rename_payment_amount",
        run_id="a" * 32,
        status=status,
        checks=checks,
        failed_check_codes=() if failed_code is None else (failed_code,),
    )


def _attempt(status: EvaluationStatus) -> EvaluationAttemptResult:
    return EvaluationAttemptResult(
        incident_case_id="schema_rename_payment_amount",
        run_id="a" * 32,
        status=status,
        evaluation=_evaluation(status),
        artifact_dir=Path("artifacts") / ("a" * 32),
    )


def test_eval_run_is_one_bounded_attempt(monkeypatch) -> None:
    calls: list[str] = []

    class FakeEvaluationRunner:
        async def run(self, case_id: str) -> EvaluationAttemptResult:
            calls.append(case_id)
            return _attempt(EvaluationStatus.PASSED)

    monkeypatch.setattr(cli, "create_evaluation_runner", lambda: FakeEvaluationRunner())
    result = runner.invoke(cli.app, ["eval", "run", "schema_rename_payment_amount"])

    assert result.exit_code == 0
    assert calls == ["schema_rename_payment_amount"]
    assert "PASSED" in result.stdout
    assert "artifacts/" + ("a" * 32) in result.stdout


def test_eval_run_failed_score_keeps_artifact_path_and_exits_nonzero(monkeypatch) -> None:
    class FakeEvaluationRunner:
        async def run(self, case_id: str) -> EvaluationAttemptResult:
            return _attempt(EvaluationStatus.FAILED)

    monkeypatch.setattr(cli, "create_evaluation_runner", lambda: FakeEvaluationRunner())
    result = runner.invoke(cli.app, ["eval", "run", "schema_rename_payment_amount"])

    assert result.exit_code == 1
    assert "FAILED" in result.stdout
    assert "artifacts" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_eval_run_setup_error_is_fixed_and_redacted(monkeypatch) -> None:
    def failing_factory():
        raise RuntimeError("provider=raw TEST_REDACTED_VALUE C:\\secret\\settings.toml")

    monkeypatch.setattr(cli, "create_evaluation_runner", failing_factory)
    result = runner.invoke(cli.app, ["eval", "run", "schema_rename_payment_amount"])

    assert result.exit_code == 1
    assert "EVALUATION_SETUP_FAILED" in result.stderr
    assert "TEST_REDACTED_VALUE" not in result.stdout + result.stderr
    assert "C:\\secret\\settings.toml" not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr


def test_eval_run_has_one_case_argument_and_rejects_unscoped_options() -> None:
    help_result = runner.invoke(cli.app, ["eval", "run", "--help"])
    assert help_result.exit_code == 0
    assert "case_id" in _plain_help(help_result.stdout)
    for forbidden in (
        "--repeat",
        "--runs",
        "--run-id",
        "--model",
        "--base-url",
        "--prompt",
        "--path",
        "--sql",
        "--table",
        "--repair",
    ):
        assert forbidden not in help_result.stdout
        result = runner.invoke(
            cli.app,
            ["eval", "run", "schema_rename_payment_amount", forbidden, "value"],
        )
        assert result.exit_code == 2
