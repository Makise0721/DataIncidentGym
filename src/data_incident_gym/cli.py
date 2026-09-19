from __future__ import annotations

import asyncio
import hashlib
from enum import StrEnum
from pathlib import Path

import typer

from data_incident_gym.baseline import BaselineBuilder, BaselineError
from data_incident_gym.benchmark_archive import ArchiveError, archive_suite
from data_incident_gym.benchmark_manifest import (
    APPROVED_MANIFEST_IDS,
    DEFAULT_FORMAL_MODEL,
    FORMAL_MODEL_BASE_URLS,
    MANIFEST_ID,
    MANIFEST_PATH,
    BenchmarkManifestError,
    build_manifest,
    freeze_manifest,
    load_manifest,
    manifest_path_for,
    verify_manifest,
)
from data_incident_gym.benchmark_report import (
    BenchmarkReporter,
    BenchmarkReportError,
    analyze_partial_suite,
)
from data_incident_gym.benchmark_runner import (
    BenchmarkCellSelector,
    BenchmarkRunner,
    BenchmarkRunnerError,
    is_receipt_acceptable,
)
from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.diagnosis import DiagnosisStatus, DiagnosticStrategy
from data_incident_gym.diagnostic_agent import DiagnosisRunner
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.doctor import DoctorRunner, DoctorStatus
from data_incident_gym.evaluation import EvaluationStatus
from data_incident_gym.evaluation_inputs import (
    ArtifactInputStatus,
    EvaluationInputsError,
    classify_scoring_inputs,
)
from data_incident_gym.evaluation_rescore import (
    OfflineScoreError,
    compare_offline_scores,
    score_run_offline,
)
from data_incident_gym.evaluation_runner import EvaluationRunner, EvaluationWorkflowError
from data_incident_gym.lab import IncidentLab, LabError
from data_incident_gym.run_context import RunContextError, resolve_active_run
from data_incident_gym.scenario_admission import (
    ADMISSIONS_DIRNAME,
    AdmissionError,
    ScenarioAdmissionReport,
    build_admission,
    write_admission_report,
)
from data_incident_gym.scenario_certification import (
    CERTIFICATION_DIRNAME,
    CatalogCertificationReport,
    CertificationError,
    certify_catalog,
    write_certification_report,
)
from data_incident_gym.scenario_sets import ScenarioSetsError
from data_incident_gym.scenarios import SUPPORTED_SCENARIO_IDS, ScenarioError

app = typer.Typer(help="可复现的数据事故诊断实验场。")
pipeline_app = typer.Typer(help="构建并检查 dbt 数据管道。")
lab_app = typer.Typer(help="重置、注入并复现固定数据故障。")
eval_app = typer.Typer(help="运行确定性评测与报告闭环。")
benchmark_app = typer.Typer(help="冻结、验证或执行正式 P1 benchmark。")
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(lab_app, name="lab")
app.add_typer(eval_app, name="eval")
app.add_typer(benchmark_app, name="benchmark")


class CliStrategy(StrEnum):
    DIAGNOSTIC_KERNEL = "diagnostic-kernel"
    STATIC_SKILL = "static-skill"


RUN_ID_OPTION = typer.Option(None, "--run-id")
STRATEGY_OPTION = typer.Option(
    CliStrategy.DIAGNOSTIC_KERNEL,
    "--strategy",
    help="诊断策略：diagnostic-kernel 或 static-skill。",
)
SCORER_OPTION = typer.Option(
    "deterministic",
    "--scorer",
    help="离线评分器：目前仅支持 deterministic。",
)
CERTIFY_CASE_OPTION = typer.Option(
    None,
    "--case",
    help="只认证指定案例；可重复传入。默认认证整个目录。",
)
CERTIFY_OUTPUT_OPTION = typer.Option(
    None,
    "--output",
    help="认证报告输出路径；默认 artifacts/certifications/<case|all>.json。",
)
CERTIFY_OVERWRITE_OPTION = typer.Option(
    False,
    "--overwrite",
    help="允许覆盖既有认证报告。",
)
CERTIFY_ADMIT_OPTION = typer.Option(
    False,
    "--admit",
    help=(
        "改为写入场景准入报告（含认证结果、场景卡片与 A/B 对称性）到 --output；"
        "默认 artifacts/admissions/<case|all>.json。"
    ),
)
BENCHMARK_ID_OPTION = typer.Option(MANIFEST_ID, "--manifest-id")
IMPLEMENTATION_REVISION_OPTION = typer.Option(..., "--implementation-revision")
BENCHMARK_OUTPUT_OPTION = typer.Option(MANIFEST_PATH, "--output")
BENCHMARK_MANIFEST_OPTION = typer.Option(..., "--manifest")
BENCHMARK_MODEL_OPTION = typer.Option(
    DEFAULT_FORMAL_MODEL,
    "--model",
    help="冻结绑定的正式模型；必须属于已批准的模型/端点配对表。",
)
BENCHMARK_SHA256_OPTION = typer.Option(..., "--confirm-sha256")
BENCHMARK_STRATEGY_OPTION = typer.Option(
    None,
    "--only-strategy",
    help=(
        "开发期 smoke 子集：只执行该策略的 cell；会留下 subset.json，"
        "该 suite 永远无法出具正式报告。可重复传入。"
    ),
)
BENCHMARK_SEQUENCE_OPTION = typer.Option(
    None,
    "--only-sequence",
    help="开发期 smoke 子集：只执行该 sequence 的 cell。可重复传入。",
)


def _diagnostic_strategy(strategy: CliStrategy) -> DiagnosticStrategy:
    return {
        CliStrategy.DIAGNOSTIC_KERNEL: DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        CliStrategy.STATIC_SKILL: DiagnosticStrategy.STATIC_SKILL,
    }[strategy]

DOCTOR_RECOMMENDATIONS_ZH = {
    "USE_PYTHON_3_12": "建议使用 Python 3.12.10。",
    "INSTALL_UV_0_11_24": "建议安装并使用 uv 0.11.24。",
    "START_DOCKER_DESKTOP": "建议启动 Docker Desktop。",
    "START_POSTGRES_COMPOSE": "建议启动 compose 中的 postgres 服务。",
    "CHECK_POSTGRES_SETTINGS": "建议检查独立 diagnostic PostgreSQL 连接配置。",
    "CHECK_DBT_PROFILE": "建议检查独立 diagnostic dbt profile 与连接。",
    "CHECK_PROFILE_SPEC": "建议检查 ProfileSpec 配置。",
    "CHECK_PROFILE_SNAPSHOT": "建议先构建健康基线并生成 profile snapshot。",
    "CHECK_PROFILE_READ_ONLY": "建议检查诊断账号的只读聚合读取和基线一致性。",
    "CHECK_PROFILE_BOUNDS": "建议检查 profile 输出上限和非法关系探针。",
    "CHECK_MODEL_ENDPOINT": "建议检查模型服务 endpoint 与 MIMO_API_KEY 配置。",
    "CHECK_MIMO_MODEL_ACCESS": "建议确认 MiMo 账号可访问 mimo-v2.5-pro。",
    "CHECK_MODEL_TOOL_CALLING": "建议检查模型的工具调用和结构化输出能力。",
}


def create_baseline_builder() -> BaselineBuilder:
    return BaselineBuilder(Settings())


def create_incident_lab() -> IncidentLab:
    return IncidentLab(Settings())


def create_diagnosis_runner(
    run_id: str,
    strategy: DiagnosticStrategy = DiagnosticStrategy.DIAGNOSTIC_KERNEL,
) -> DiagnosisRunner:
    return DiagnosisRunner.for_run(run_id, DiagnosticSettings(), strategy)


def create_evaluation_runner() -> EvaluationRunner:
    return EvaluationRunner.for_project(Settings(), DiagnosticSettings())


def create_doctor_runner() -> DoctorRunner:
    return DoctorRunner.for_project(DiagnosticSettings())


def create_benchmark_runner(manifest, selector=None) -> BenchmarkRunner:
    return BenchmarkRunner.for_project(manifest, cell_selector=selector)


def create_benchmark_reporter(manifest) -> BenchmarkReporter:
    suite_root = PROJECT_ROOT / "artifacts" / "benchmarks" / manifest.manifest_id
    return BenchmarkReporter(manifest, suite_root)


def _canonical_benchmark_manifest_path(path: Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    if candidate.is_symlink():
        raise BenchmarkManifestError("formal manifest path must not be a symlink")
    resolved = candidate.resolve(strict=False)
    approved = {
        (PROJECT_ROOT / manifest_path_for(manifest_id)).resolve(strict=False)
        for manifest_id in APPROVED_MANIFEST_IDS
    }
    if resolved not in approved:
        raise BenchmarkManifestError(
            "formal manifest path must be config/benchmark/<approved-manifest-id>.json"
        )
    return resolved


def _confirmed_benchmark_manifest(path: Path, confirm_sha256: str):
    manifest_path = _canonical_benchmark_manifest_path(path)
    actual_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if confirm_sha256 != actual_sha256:
        raise BenchmarkRunnerError("manifest SHA-256 confirmation does not match file")
    loaded = load_manifest(manifest_path)
    if manifest_path.stem != loaded.manifest_id:
        raise BenchmarkManifestError("manifest file name must match its manifest_id")
    verify_manifest(loaded)
    return manifest_path, loaded


def _cell_selector(
    manifest,
    only_strategy: list[str],
    only_sequence: list[int],
) -> BenchmarkCellSelector | None:
    only_strategy = only_strategy or []
    only_sequence = only_sequence or []
    if not only_strategy and not only_sequence:
        return None
    strategies = tuple(
        DiagnosticStrategy(value.upper().replace("-", "_")) for value in only_strategy
    )
    return BenchmarkCellSelector(
        manifest_id=manifest.manifest_id,
        strategies=strategies,
        sequences=tuple(only_sequence),
    )


def _exit_lab_error(error: LabError | ScenarioError) -> None:
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


@app.command(
    "diagnose",
    help=(
        "使用固定案例和已验证运行的只读证据进行诊断。\n"
        "支持案例：\n- "
        + "\n- ".join(SUPPORTED_SCENARIO_IDS)
        + "。"
    ),
)
def diagnose(
    case_id: str,
    run_id: str | None = RUN_ID_OPTION,
    strategy: CliStrategy = STRATEGY_OPTION,
) -> None:
    """使用固定案例和已验证运行的只读证据进行诊断。"""
    try:
        selected_run_id = (
            run_id
            if run_id is not None
            else resolve_active_run().run_id
        )
        result = asyncio.run(
            create_diagnosis_runner(
                selected_run_id,
                _diagnostic_strategy(strategy),
            ).diagnose()
        )
    except RunContextError:
        _exit_diagnosis_error()
    except Exception:
        _exit_diagnosis_error()

    typer.echo(
        {
            DiagnosisStatus.CONFIRMED: "诊断完成。",
            DiagnosisStatus.INSUFFICIENT_EVIDENCE: "证据不足，拒绝确认。",
            DiagnosisStatus.NO_INCIDENT: "未发现事故，健康证据成立。",
            DiagnosisStatus.MODEL_ERROR: "诊断失败。",
        }[result.diagnosis.status]
    )
    typer.echo(result.diagnosis.model_dump_json(indent=2))
    if result.diagnosis.status == DiagnosisStatus.INSUFFICIENT_EVIDENCE:
        raise typer.Exit(code=2)
    if result.diagnosis.status == DiagnosisStatus.MODEL_ERROR:
        raise typer.Exit(code=1)


@eval_app.command(
    "run",
    help=(
        "对一个固定案例执行一次独立的完整评测。\n支持案例：\n- "
        + "\n- ".join(SUPPORTED_SCENARIO_IDS)
        + "。"
    ),
)
def eval_run(
    case_id: str,
    strategy: CliStrategy = STRATEGY_OPTION,
) -> None:
    """对一个固定案例执行一次独立的完整评测。"""
    try:
        result = asyncio.run(
            create_evaluation_runner().run(case_id, _diagnostic_strategy(strategy))
        )
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
    if result.scoring_inputs_dir is not None:
        typer.echo(f"scoring_inputs: {result.scoring_inputs_dir}")
    if result.status != EvaluationStatus.PASSED:
        raise typer.Exit(code=1)


def _exit_scoring_inputs_error(run_id: str, error: EvaluationInputsError) -> None:
    try:
        classification = classify_scoring_inputs(PROJECT_ROOT, run_id)
    except EvaluationInputsError:
        typer.echo(f"离线评分失败 [{error.code}]。", err=True)
        raise typer.Exit(code=1) from None
    reasons = ", ".join(classification.reasons)
    allowed = {
        ArtifactInputStatus.RE_SCORABLE: "可完整重评",
        ArtifactInputStatus.PARTIAL_ANALYSIS: "仅可部分分析，不产出评分",
        ArtifactInputStatus.NOT_RE_SCORABLE: "不可复评",
    }[classification.status]
    typer.echo(
        f"离线评分失败 [{error.code}]：该运行分类为 {classification.status.value}"
        f"（{allowed}；{reasons}）。",
        err=True,
    )
    raise typer.Exit(code=1) from None


@eval_app.command(
    "score",
    help=(
        "对已归档的评分输入执行只读离线重评。\n"
        "不调用模型、数据库或 dbt，不修改原始产物，也不改变原批次结论。"
    ),
)
def eval_score(
    run_id: str,
    scorer: str = SCORER_OPTION,
) -> None:
    """对已归档的评分输入执行只读离线重评。"""
    if scorer != "deterministic":
        typer.echo(f"离线评分失败 [SCORER_UNKNOWN]：未知评分器 {scorer}。", err=True)
        raise typer.Exit(code=1)
    try:
        result = score_run_offline(PROJECT_ROOT, run_id)
    except EvaluationInputsError as error:
        _exit_scoring_inputs_error(run_id, error)
    except OfflineScoreError as error:
        detail = f"：{error.detail}" if error.detail else ""
        typer.echo(f"离线评分失败 [{error.code}]{detail}。", err=True)
        raise typer.Exit(code=1) from None
    typer.echo("离线重评完成。" if result.created else "派生评分已存在，返回既有结果。")
    typer.echo(f"status: {result.evaluation.status.value}")
    typer.echo(f"run_id: {result.run_id}")
    typer.echo(f"score_id: {result.score_id}")
    typer.echo(f"rescore: {result.score_dir}")
    if not result.diff.available:
        typer.echo(f"changed_checks: 无法比较（{result.diff.unavailable_reason}）")
        return
    changed = result.changed_check_codes
    if changed:
        typer.echo(f"changed_checks: {', '.join(changed)}")
    else:
        typer.echo("changed_checks: 无（与原归档评分逐项一致）")


@eval_app.command(
    "compare-scores",
    help="比较同一运行的两个派生评分，输出逐项差异；只读，不重新评分。",
)
def eval_compare_scores(
    run_id: str,
    score_id_a: str,
    score_id_b: str,
) -> None:
    """比较同一运行的两个派生评分。"""
    try:
        comparison = compare_offline_scores(PROJECT_ROOT, run_id, score_id_a, score_id_b)
    except (OfflineScoreError, EvaluationInputsError) as error:
        code = getattr(error, "code", "OFFLINE_SCORE_SETUP_FAILED")
        typer.echo(f"评分比较失败 [{code}]。", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"run_id: {comparison.run_id}")
    typer.echo(
        f"A: {comparison.score_id_a[:12]} status={comparison.status_a} "
        f"scorer={comparison.scorer_a.name}"
    )
    typer.echo(
        f"B: {comparison.score_id_b[:12]} status={comparison.status_b} "
        f"scorer={comparison.scorer_b.name}"
    )
    changed = comparison.changed_check_codes
    if not changed:
        typer.echo("两个评分的全部检查逐项一致。")
        return
    for item in comparison.changes:
        if item.change == "UNCHANGED" and not item.details_changed:
            continue
        details = "（expected/actual 变化）" if item.details_changed else ""
        typer.echo(
            f"[{item.change}] {item.kind} {item.code}: "
            f"{item.before_passed} -> {item.after_passed}{details}"
        )


@app.command(
    "certify",
    help=(
        "运行公开证据参考解并认证场景可解性；确定性，不需要模型预算。\n"
        "参考解只见公开 brief 与六个只读工具；私有合同只用于认证侧核对。\n"
        "--admit 时写入场景准入报告（含场景卡片）。"
    ),
)
def certify(
    case: list[str] = CERTIFY_CASE_OPTION,
    output: Path = CERTIFY_OUTPUT_OPTION,
    overwrite: bool = CERTIFY_OVERWRITE_OPTION,
    admit: bool = CERTIFY_ADMIT_OPTION,
) -> None:
    """运行公开证据参考解并认证场景可解性。"""
    case_ids = tuple(case) if case else None
    if case_ids is not None:
        unknown = sorted({item for item in case_ids if item not in SUPPORTED_SCENARIO_IDS})
        if unknown:
            typer.echo(f"认证失败 [UNKNOWN_SCENARIO]：{'、'.join(unknown)}。", err=True)
            raise typer.Exit(code=1)
    try:
        report = asyncio.run(certify_catalog(case_ids, project_root=PROJECT_ROOT))
    except CertificationError as error:
        typer.echo(f"认证失败 [{error.code}]。", err=True)
        raise typer.Exit(code=1) from None
    except Exception:
        typer.echo("认证失败 [CERTIFICATION_SETUP_FAILED]。", err=True)
        raise typer.Exit(code=1) from None
    default_name = (
        f"{case_ids[0]}.json" if case_ids is not None and len(case_ids) == 1 else "catalog.json"
    )
    if admit:
        _write_admissions(report, case_ids, output, overwrite)
        return
    target = Path(output) if output is not None else CERTIFICATION_DIRNAME / default_name
    if not target.is_absolute():
        target = PROJECT_ROOT / target
    if target.exists() and not overwrite:
        typer.echo(f"认证报告已存在：{target}（使用 --overwrite 覆盖）。", err=True)
        raise typer.Exit(code=1)
    write_certification_report(report, target)
    for entry in report.entries:
        verdict = "通过" if entry.certified else "未通过"
        classes = ",".join(entry.failure_classes) if entry.failure_classes else "-"
        typer.echo(f"[{verdict}] {entry.case_id} failure_classes={classes}")
    typer.echo(f"certified: {report.certified_count}/{len(report.entries)}")
    typer.echo(f"report: {target}")
    if report.certified_count != len(report.entries):
        raise typer.Exit(code=1)


def _write_admissions(
    report: CatalogCertificationReport,
    case_ids: tuple[str, ...] | None,
    output: Path | None,
    overwrite: bool,
) -> None:
    """从认证报告构建准入报告并写盘；不通过时以非零码退出。"""

    default_name = (
        f"{case_ids[0]}.json" if case_ids is not None and len(case_ids) == 1 else "all.json"
    )
    target = Path(output) if output is not None else ADMISSIONS_DIRNAME / default_name
    if not target.is_absolute():
        target = PROJECT_ROOT / target
    if target.exists() and not overwrite:
        typer.echo(f"准入报告已存在：{target}（使用 --overwrite 覆盖）。", err=True)
        raise typer.Exit(code=1)
    try:
        entries = tuple(
            build_admission(entry.case_id, entry, project_root=PROJECT_ROOT)
            for entry in report.entries
        )
        admission_report = ScenarioAdmissionReport(created_at=report.created_at, entries=entries)
        target = write_admission_report(admission_report, target)
    except ScenarioSetsError as error:
        typer.echo(f"准入失败 [{error.code}]。", err=True)
        raise typer.Exit(code=1) from None
    except ScenarioError as error:
        typer.echo(f"准入失败 [SCENARIO_INVALID]：{error}。", err=True)
        raise typer.Exit(code=1) from None
    except AdmissionError as error:
        typer.echo(f"准入失败 [{error.code}]。", err=True)
        raise typer.Exit(code=1) from None
    for entry in report.entries:
        verdict = "通过" if entry.certified else "未通过"
        classes = ",".join(entry.failure_classes) if entry.failure_classes else "-"
        typer.echo(f"[{verdict}] {entry.case_id} failure_classes={classes}")
    for entry in entries:
        verdict = "准入" if entry.admitted else "拒绝"
        reasons = ",".join(entry.reasons) if entry.reasons else "-"
        typer.echo(f"[{verdict}] {entry.case_id} reasons={reasons}")
    typer.echo(f"certified: {report.certified_count}/{len(report.entries)}")
    typer.echo(f"admitted: {admission_report.admitted_count}/{len(admission_report.entries)}")
    typer.echo(f"report: {target}")
    if admission_report.admitted_count != len(admission_report.entries):
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


@benchmark_app.command("freeze")
def benchmark_freeze(
    manifest_id: str = BENCHMARK_ID_OPTION,
    implementation_revision: str = IMPLEMENTATION_REVISION_OPTION,
    output: Path = BENCHMARK_OUTPUT_OPTION,
    model: str = BENCHMARK_MODEL_OPTION,
) -> None:
    """生成一次性的正式 Manifest；不会发起模型请求。"""
    try:
        base_url = FORMAL_MODEL_BASE_URLS.get(model)
        if base_url is None:
            raise BenchmarkManifestError(
                "formal model must be one of the approved pairings: "
                + ", ".join(FORMAL_MODEL_BASE_URLS)
            )
        manifest = build_manifest(
            implementation_revision,
            manifest_id=manifest_id,
            model_name=model,
            model_base_url=base_url,
        )
        verify_manifest(manifest)
        path = freeze_manifest(manifest, output)
    except (BenchmarkManifestError, ValueError) as exc:
        typer.echo(f"benchmark manifest 冻结失败：{exc}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"manifest: {path}")
    typer.echo(f"sha256: {manifest.digest()}")
    typer.echo("cells: 106; model_backed: 94; fixed_rule: 12")


@benchmark_app.command("verify")
def benchmark_verify(
    manifest: Path = BENCHMARK_MANIFEST_OPTION,
) -> None:
    """验证正式 Manifest 与当前结果输入；不会发起模型请求。"""
    try:
        manifest = _canonical_benchmark_manifest_path(manifest)
        loaded = load_manifest(manifest)
        verify_manifest(loaded)
    except (BenchmarkManifestError, ValueError) as exc:
        typer.echo(f"benchmark manifest 验证失败：{exc}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"manifest: {manifest}")
    typer.echo(f"sha256: {loaded.digest()}")
    typer.echo("verified: 17 catalog scenarios; 12 formal scenarios; 106 cells; 94 model-backed")


@benchmark_app.command("run")
def benchmark_run(
    manifest: Path = BENCHMARK_MANIFEST_OPTION,
    confirm_sha256: str = BENCHMARK_SHA256_OPTION,
    only_strategy: list[str] = BENCHMARK_STRATEGY_OPTION,
    only_sequence: list[int] = BENCHMARK_SEQUENCE_OPTION,
) -> None:
    """执行已冻结的 benchmark；无重试、替换或扩展样本选项。"""
    try:
        _, loaded = _confirmed_benchmark_manifest(manifest, confirm_sha256)
        selector = _cell_selector(loaded, only_strategy, only_sequence)
        result = asyncio.run(create_benchmark_runner(loaded, selector).run())
    except (BenchmarkManifestError, BenchmarkRunnerError, OSError, ValueError) as exc:
        typer.echo(f"benchmark 执行失败：{exc}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"status: {result.status}")
    typer.echo(f"cells: {result.terminal_cells}/{result.total_cells}")
    typer.echo(f"subset: {result.subset}")
    typer.echo(f"model_probe_required: {result.model_probe_required}")
    typer.echo(f"ledger: {result.ledger_path}")
    if result.status != "COMPLETED":
        raise typer.Exit(code=1)


@benchmark_app.command("preflight")
def benchmark_preflight(
    manifest: Path = BENCHMARK_MANIFEST_OPTION,
    confirm_sha256: str = BENCHMARK_SHA256_OPTION,
    only_strategy: list[str] = BENCHMARK_STRATEGY_OPTION,
    only_sequence: list[int] = BENCHMARK_SEQUENCE_OPTION,
) -> None:
    """执行与所选 cell 范围绑定的 doctor；不会创建 cell 或 ledger。"""
    try:
        _, loaded = _confirmed_benchmark_manifest(manifest, confirm_sha256)
        selector = _cell_selector(loaded, only_strategy, only_sequence)
        receipt = asyncio.run(create_benchmark_runner(loaded, selector).preflight())
        acceptable = is_receipt_acceptable(receipt)
    except (BenchmarkManifestError, BenchmarkRunnerError, OSError, ValueError) as exc:
        typer.echo(f"benchmark preflight 失败：{exc}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"status: {'PASSED' if acceptable else 'FAILED'}")
    typer.echo(f"doctor_status: {receipt.result.status.value}")
    typer.echo(f"model_probe_required: {receipt.model_probe_required}")
    typer.echo(
        "receipt: "
        f"{PROJECT_ROOT / 'artifacts' / 'benchmarks' / loaded.manifest_id / 'doctor.json'}"
    )
    typer.echo("started_cells: 0")
    if not acceptable:
        raise typer.Exit(code=1)


@benchmark_app.command("report")
def benchmark_report(
    manifest: Path = BENCHMARK_MANIFEST_OPTION,
    confirm_sha256: str = BENCHMARK_SHA256_OPTION,
) -> None:
    """只读校验并汇总正式 suite；不会调用模型、数据库或 evaluator。"""
    try:
        _, loaded = _confirmed_benchmark_manifest(manifest, confirm_sha256)
        summary_path, report_path = create_benchmark_reporter(loaded).write()
    except (
        BenchmarkManifestError,
        BenchmarkReportError,
        BenchmarkRunnerError,
        OSError,
        ValueError,
    ) as exc:
        typer.echo(f"benchmark report 失败：{exc}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"summary: {summary_path}")
    typer.echo(f"report: {report_path}")


@benchmark_app.command("partial")
def benchmark_partial(
    manifest: Path = BENCHMARK_MANIFEST_OPTION,
    confirm_sha256: str = BENCHMARK_SHA256_OPTION,
) -> None:
    """只读分析未完成 suite 的重复稳定性；不产出正式报告，也不放宽其完整性要求。"""
    try:
        _, loaded = _confirmed_benchmark_manifest(manifest, confirm_sha256)
        suite_root = PROJECT_ROOT / "artifacts" / "benchmarks" / loaded.manifest_id
        result = analyze_partial_suite(loaded, suite_root)
    except (
        BenchmarkManifestError,
        BenchmarkReportError,
        BenchmarkRunnerError,
        OSError,
        ValueError,
    ) as exc:
        typer.echo(f"部分分析失败：{exc}", err=True)
        raise typer.Exit(code=1) from None
    reliability = result["reliability"]
    typer.echo(f"protocol: {reliability['protocol_version']}")
    typer.echo(
        f"groups: {reliability['groups_complete']} complete / "
        f"{reliability['groups_incomplete']} incomplete"
    )
    macro = reliability["macro_pass_hat"]
    for key in ("1", "2", "3"):
        metric = macro[key]
        value = "n/a" if metric["value"] is None else f"{metric['value']:.3f}"
        typer.echo(
            f"macro pass^{key}: {value} ({metric['groups']} groups / {metric['trials']} trials)"
        )
    missing = [cell["run_id"] for cell in result["cells"] if cell["state"] == "MISSING"]
    typer.echo(f"missing cells: {len(missing)}")
    if missing:
        typer.echo("缺失格不进入分母；完整子集分析见返回结构的 complete_subset，附覆盖量。")


@benchmark_app.command("archive")
def benchmark_archive(
    manifest: Path = BENCHMARK_MANIFEST_OPTION,
    confirm_sha256: str = BENCHMARK_SHA256_OPTION,
) -> None:
    """只读保全 suite 的取证数据；不会调用模型、数据库或 evaluator。"""
    try:
        _, loaded = _confirmed_benchmark_manifest(manifest, confirm_sha256)
        suite_root = PROJECT_ROOT / "artifacts" / "benchmarks" / loaded.manifest_id
        archive_root = PROJECT_ROOT / "reports" / "benchmark" / loaded.manifest_id
        result = archive_suite(suite_root, archive_root, loaded)
    except (
        ArchiveError,
        BenchmarkManifestError,
        BenchmarkRunnerError,
        OSError,
        ValueError,
    ) as exc:
        typer.echo(f"benchmark 归档失败：{exc}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"archive: {result.archive_root}")
    typer.echo(f"cells: {result.cell_count}")
    typer.echo(f"source sha256: {result.aggregate_sha256}")


@lab_app.command("reset")
def lab_reset(case_id: str) -> None:
    """把固定案例恢复为健康状态。"""
    try:
        result = create_incident_lab().reset(case_id)
    except (LabError, ScenarioError) as exc:
        _exit_lab_error(exc)
    typer.echo("故障案例重置成功。")
    typer.echo(f"state: {result.state}")
    typer.echo(f"fingerprint: {result.fingerprint}")


@lab_app.command("inject")
def lab_inject(case_id: str) -> None:
    """向健康基线注入固定字段变更故障。"""
    try:
        result = create_incident_lab().prepare(case_id)
    except (LabError, ScenarioError) as exc:
        _exit_lab_error(exc)
    typer.echo("故障注入成功。")
    typer.echo(f"state: {result.state}")
    typer.echo(f"fingerprint: {result.fingerprint}")


@lab_app.command("build")
def lab_build(case_id: str) -> None:
    """运行无 seed 的 dbt build 并验证预期故障。"""
    try:
        result = create_incident_lab().build(case_id)
    except (LabError, ScenarioError) as exc:
        _exit_lab_error(exc)
    typer.echo("预期故障复现成功。")
    typer.echo(f"status: {result.verification_status}")
    typer.echo(f"run_id: {result.run_id}")
    typer.echo(f"dbt_exit_code: {result.dbt_exit_code}")
    typer.echo(f"artifacts: {result.artifact_dir}")
