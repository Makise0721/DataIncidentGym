from __future__ import annotations

import asyncio

import typer

from data_incident_gym.baseline import BaselineBuilder, BaselineError
from data_incident_gym.config import Settings
from data_incident_gym.diagnosis import DiagnosisStatus
from data_incident_gym.diagnostic_agent import DiagnosisRunner
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.doctor import DoctorRunner, DoctorStatus
from data_incident_gym.evaluation import EvaluationStatus
from data_incident_gym.evaluation_runner import EvaluationRunner, EvaluationWorkflowError
from data_incident_gym.incidents import IncidentCaseError
from data_incident_gym.lab import IncidentLab, LabError
from data_incident_gym.run_context import RunContextError, resolve_active_run

app = typer.Typer(help="可复现的数据事故诊断实验场。")
pipeline_app = typer.Typer(help="构建并检查 dbt 数据管道。")
lab_app = typer.Typer(help="重置、注入并复现固定数据故障。")
eval_app = typer.Typer(help="运行确定性评测与报告闭环。")
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(lab_app, name="lab")
app.add_typer(eval_app, name="eval")

DOCTOR_RECOMMENDATIONS_ZH = {
    "USE_PYTHON_3_12": "建议使用 Python 3.12.10。",
    "INSTALL_UV_0_11_24": "建议安装并使用 uv 0.11.24。",
    "START_DOCKER_DESKTOP": "建议启动 Docker Desktop。",
    "START_POSTGRES_COMPOSE": "建议启动 compose 中的 postgres 服务。",
    "CHECK_POSTGRES_SETTINGS": "建议检查独立 diagnostic PostgreSQL 连接配置。",
    "CHECK_DBT_PROFILE": "建议检查独立 diagnostic dbt profile 与连接。",
    "START_OLLAMA": "建议启动 Ollama 并确认 OpenAI-compatible endpoint 可访问。",
    "IMPORT_QWEN3_5_9B": "建议导入并暴露 qwen3.5:9b 模型。",
    "CHECK_MODEL_TOOL_CALLING": "建议检查模型的工具调用和结构化输出能力。",
}


def create_baseline_builder() -> BaselineBuilder:
    return BaselineBuilder(Settings())


def create_incident_lab() -> IncidentLab:
    return IncidentLab(Settings())


def create_diagnosis_runner(run_id: str) -> DiagnosisRunner:
    return DiagnosisRunner.for_run(run_id, DiagnosticSettings())


def create_evaluation_runner() -> EvaluationRunner:
    return EvaluationRunner.for_project(Settings(), DiagnosticSettings())


def create_doctor_runner() -> DoctorRunner:
    return DoctorRunner.for_project(DiagnosticSettings())


def _exit_lab_error(error: LabError | IncidentCaseError) -> None:
    code = getattr(error, "code", "INCIDENT_CASE_ERROR")
    typer.echo(f"故障实验失败 [{code}]：{error}", err=True)
    raise typer.Exit(code=1) from error


def _exit_diagnosis_error() -> None:
    typer.echo("诊断失败 [MODEL_ERROR]：无法建立安全的诊断运行上下文。", err=True)
    raise typer.Exit(code=1)


@pipeline_app.command("build")
def pipeline_build() -> None:
    """重置 seeds，运行 dbt build，并生成健康基线摘要。"""
    try:
        summary = create_baseline_builder().build()
    except BaselineError as exc:
        typer.echo(f"健康基线构建失败：{exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("健康基线构建成功。")
    typer.echo(f"schema: {summary.schema}")
    typer.echo(f"relations: {len(summary.relations)}")
    typer.echo(f"fingerprint: {summary.fingerprint}")
    typer.echo("summary: .dig/baseline-summary.json")


@app.command("diagnose")
def diagnose(
    case_id: str,
    run_id: str | None = typer.Option(None, "--run-id"),
) -> None:
    """使用固定案例和已验证运行的只读证据进行诊断。"""
    try:
        selected_run_id = (
            run_id
            if run_id is not None
            else resolve_active_run(incident_case_id=case_id).run_id
        )
        result = asyncio.run(create_diagnosis_runner(selected_run_id).diagnose(case_id))
    except RunContextError:
        _exit_diagnosis_error()
    except Exception:
        _exit_diagnosis_error()

    typer.echo(
        {
            DiagnosisStatus.CONFIRMED: "诊断完成。",
            DiagnosisStatus.INSUFFICIENT_EVIDENCE: "证据不足，拒绝确认。",
            DiagnosisStatus.MODEL_ERROR: "诊断失败。",
        }[result.diagnosis.status]
    )
    typer.echo(result.diagnosis.model_dump_json(indent=2))
    if result.diagnosis.status == DiagnosisStatus.INSUFFICIENT_EVIDENCE:
        raise typer.Exit(code=2)
    if result.diagnosis.status == DiagnosisStatus.MODEL_ERROR:
        raise typer.Exit(code=1)


@eval_app.command("run")
def eval_run(case_id: str) -> None:
    """对一个固定案例执行一次独立的完整评测。"""
    try:
        result = asyncio.run(create_evaluation_runner().run(case_id))
    except EvaluationWorkflowError as error:
        typer.echo(f"评测运行失败 [{error.code}]。", err=True)
        raise typer.Exit(code=1) from None
    except Exception:
        typer.echo("评测运行失败 [EVALUATION_SETUP_FAILED]。", err=True)
        raise typer.Exit(code=1) from None

    typer.echo("评测通过。" if result.status == EvaluationStatus.PASSED else "评测未通过。")
    typer.echo(f"status: {result.status.value}")
    typer.echo(f"run_id: {result.run_id}")
    typer.echo(f"artifacts: artifacts/{result.run_id}")
    if result.status != EvaluationStatus.PASSED:
        raise typer.Exit(code=1)


@app.command("doctor")
def doctor() -> None:
    """只读检查 P0 环境、依赖和模型最小能力。"""
    try:
        result = asyncio.run(create_doctor_runner().run())
    except Exception:
        typer.echo("doctor 失败 [DOCTOR_SETUP_FAILED]。", err=True)
        raise typer.Exit(code=1) from None
    for check in result.checks:
        state = "通过" if check.passed else "失败"
        typer.echo(f"[{state}] {check.code.value}: {check.observed}")
        if check.recommendation_code is not None:
            typer.echo(DOCTOR_RECOMMENDATIONS_ZH[check.recommendation_code])
    typer.echo("说明：doctor 通过不代表 P0 评测通过。")
    if result.status == DoctorStatus.FAILED:
        raise typer.Exit(code=1)


@lab_app.command("reset")
def lab_reset(case_id: str) -> None:
    """把固定案例恢复为健康状态。"""
    try:
        result = create_incident_lab().reset(case_id)
    except (LabError, IncidentCaseError) as exc:
        _exit_lab_error(exc)
    typer.echo("故障案例重置成功。")
    typer.echo(f"state: {result.state}")
    typer.echo(f"fingerprint: {result.fingerprint}")


@lab_app.command("inject")
def lab_inject(case_id: str) -> None:
    """向健康基线注入固定字段改名故障。"""
    try:
        result = create_incident_lab().inject(case_id)
    except (LabError, IncidentCaseError) as exc:
        _exit_lab_error(exc)
    typer.echo("故障注入成功。")
    typer.echo(f"state: {result.state}")
    typer.echo(f"fingerprint: {result.fingerprint}")


@lab_app.command("build")
def lab_build(case_id: str) -> None:
    """运行无 seed 的 dbt build 并验证预期故障。"""
    try:
        result = create_incident_lab().build(case_id)
    except (LabError, IncidentCaseError) as exc:
        _exit_lab_error(exc)
    typer.echo("预期故障复现成功。")
    typer.echo(f"status: {result.verification.status}")
    typer.echo(f"run_id: {result.run_id}")
    typer.echo(f"dbt_exit_code: {result.dbt_exit_code}")
    typer.echo(f"artifacts: {result.artifact_dir}")
