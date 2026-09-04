from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from data_incident_gym.artifacts import (
    ARTIFACT_FILENAMES,
    BudgetSummary,
    EvidenceArtifact,
    RecoveryStatus,
    RunMetadata,
    TraceEnvelope,
)
from data_incident_gym.benchmark_archive import ArchiveError, archive_suite
from data_incident_gym.benchmark_manifest import build_manifest, generate_cells
from data_incident_gym.benchmark_runner import (
    BenchmarkCellSelector,
    BenchmarkDoctorReceipt,
    BenchmarkLedgerEntry,
    _setup_failure_diagnosis,
    _setup_failure_evaluation,
)
from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.doctor import DoctorCheckCode, DoctorResult, DoctorRunner, DoctorStatus

SIX = (
    "metadata.json",
    "trace.jsonl",
    "evidence.json",
    "diagnosis.json",
    "evaluation.json",
    "report.md",
)
NOW = datetime(2026, 9, 3, tzinfo=UTC)


def _write_cell(project_root: Path, manifest, run_id: str) -> None:
    cell_spec = next(cell for cell in manifest.cells if cell.run_id == run_id)
    cell = project_root / "artifacts" / run_id
    cell.mkdir(parents=True)
    for name in SIX:
        (cell / name).write_text("{}\n", encoding="utf-8", newline="\n")
    diagnosis_run = _setup_failure_diagnosis(run_id=run_id, strategy=cell_spec.strategy)
    evaluation_model = _setup_failure_evaluation(
        cell_spec.incident_case_id,
        run_id,
        stage_code="RUN_SETUP_ERROR",
        recovery_succeeded=False,
    )
    identity = diagnosis_run.policy_identity
    metadata = RunMetadata(
        schema_version="p1.metadata.v1",
        incident_case_id=cell_spec.incident_case_id,
        run_id=run_id,
        strategy=cell_spec.strategy,
        code_revision="a" * 40,
        workspace_dirty=False,
        provider=diagnosis_run.metrics.provider,
        model=diagnosis_run.metrics.model,
        model_base_url=manifest.model_configuration.base_url,
        budget=BudgetSummary(
            model_request_limit=8, tool_call_limit=8, output_retry_limit=2, timeout_seconds=300
        ),
        base_prompt_version=identity.base_prompt_version,
        base_prompt_sha256=identity.base_prompt_sha256,
        strategy_prompt_version=identity.strategy_prompt_version,
        strategy_prompt_sha256=identity.strategy_prompt_sha256,
        controller_protocol_version=identity.controller_protocol_version,
        controller_protocol_sha256=identity.controller_protocol_sha256,
        tool_schema_sha256=identity.tool_schema_sha256,
        benchmark_manifest_sha256=manifest.digest(),
        variant_role=None,
        answerability=evaluation_model.answerability,
        expected_status=evaluation_model.expected_status,
        started_at=NOW,
        finished_at=NOW,
        elapsed_ms=0,
        diagnosis_metrics=diagnosis_run.metrics,
        evaluation_status=evaluation_model.status,
        recovery_status=RecoveryStatus.FAILED,
        artifact_files=ARTIFACT_FILENAMES,
    )
    (cell / "trace.jsonl").write_text(
        "".join(
            TraceEnvelope(
                schema_version="p1.trace.v1", sequence=index, event=event
            ).model_dump_json()
            + "\n"
            for index, event in enumerate(diagnosis_run.trace, start=1)
        ),
        encoding="utf-8",
        newline="\n",
    )
    (cell / "evidence.json").write_text(
        EvidenceArtifact(
            schema_version="p1.evidence.v1",
            incident_case_id=cell_spec.incident_case_id,
            run_id=run_id,
            records=(),
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    for name, value in (
        ("metadata.json", metadata.model_dump(mode="json")),
        ("diagnosis.json", diagnosis_run.diagnosis.model_dump(mode="json")),
        ("evaluation.json", evaluation_model.model_dump(mode="json")),
    ):
        (cell / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )


def _write_ledger(
    suite_root: Path,
    manifest_id: str,
    cell,
    *,
    reason: str = "EVALUATION_FAILED",
) -> None:
    values = (
        BenchmarkLedgerEntry.create(
            manifest_id=manifest_id,
            sequence=cell.sequence,
            run_id=cell.run_id,
            incident_case_id=cell.incident_case_id,
            strategy=cell.strategy,
            state="STARTED",
            now=NOW,
            started_at=NOW,
        ),
        BenchmarkLedgerEntry.create(
            manifest_id=manifest_id,
            sequence=cell.sequence,
            run_id=cell.run_id,
            incident_case_id=cell.incident_case_id,
            strategy=cell.strategy,
            state="FAILED",
            now=NOW,
            started_at=NOW,
            reason_code=reason,
        ),
    )
    (suite_root / "ledger.jsonl").write_text(
        "\n".join(value.model_dump_json() for value in values) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_doctor(suite_root: Path, manifest, selector=None, *, digest=None) -> None:
    doctor = DoctorResult(
        status=DoctorStatus.PASSED,
        checks=tuple(DoctorRunner._check(code, True, "OK") for code in DoctorCheckCode),
    )
    (suite_root / "doctor.json").write_text(
        BenchmarkDoctorReceipt(
            manifest_id=manifest.manifest_id,
            manifest_sha256=digest or manifest.digest(),
            implementation_revision=manifest.implementation_revision,
            checkout_revision=manifest.implementation_revision,
            result_inputs_sha256=_result_inputs_digest(manifest),
            cell_selector=selector,
            model_probe_required=any(
                cell.model_backed
                for cell in (
                    selector.select(manifest.cells) if selector is not None else manifest.cells
                )
            ),
            checked_at=NOW,
            result=doctor,
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _result_inputs_digest(manifest) -> str:
    import hashlib

    payload = json.dumps(
        manifest.result_inputs.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_archive_reads_ledger_artifacts_and_builds_gate_matrix(tmp_path: Path) -> None:
    manifest_id = "p1-formal-v2"
    cell = generate_cells(manifest_id)[0]
    run_id = cell.run_id
    selector = BenchmarkCellSelector(manifest_id=manifest_id, sequences=(cell.sequence,))
    suite_root = tmp_path / "artifacts" / "benchmarks" / manifest_id
    suite_root.mkdir(parents=True)
    _write_ledger(suite_root, manifest_id, cell)
    manifest = build_manifest("a" * 40, manifest_id=manifest_id, project_root=PROJECT_ROOT)
    _write_cell(tmp_path, manifest, run_id)
    _write_doctor(suite_root, manifest, selector)
    (suite_root / "subset.json").write_text(
        selector.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    interrupted = suite_root / "interrupted-artifacts" / run_id
    interrupted.mkdir(parents=True)
    (interrupted / "partial-output.txt").write_text("incomplete", encoding="utf-8")
    archive_root = tmp_path / "reports" / "benchmark" / manifest_id

    result = archive_suite(
        suite_root,
        archive_root,
        manifest,
        project_root=tmp_path,
    )

    assert (archive_root / "ledger.jsonl").exists()
    assert (archive_root / "doctor.json").exists()
    assert (archive_root / "subset.json").exists()
    assert (
        archive_root / "interrupted-artifacts" / run_id / "partial-output.txt"
    ).read_text(encoding="utf-8") == "incomplete"
    rows = (archive_root / "gate-matrix.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0].startswith("run_id,sequence,incident_case_id,strategy,diagnosis_status,")
    assert rows[1].startswith(
        f"{run_id},{cell.sequence},{cell.incident_case_id},{cell.strategy.value},"
        "MODEL_ERROR,FAILED,"
    )
    assert ",0,0,0," in rows[1]
    assert result.cell_count == 1
    aggregate = json.loads(
        (archive_root / "aggregate_sha256.json").read_text(encoding="utf-8")
    )
    assert aggregate["source_file_count"] == 10
    assert result.aggregate_sha256 == aggregate["source_aggregate_sha256"]


def test_archive_rejects_existing_target(tmp_path: Path) -> None:
    manifest_id = "p1-formal-v2"
    manifest = build_manifest("a" * 40, manifest_id=manifest_id, project_root=PROJECT_ROOT)
    suite_root = tmp_path / "artifacts" / "benchmarks" / manifest_id
    suite_root.mkdir(parents=True)
    archive_root = tmp_path / "reports" / "benchmark" / manifest_id
    archive_root.mkdir(parents=True)

    with pytest.raises(ArchiveError, match="already exists"):
        archive_suite(
            suite_root,
            archive_root,
            manifest,
            project_root=tmp_path,
        )


def test_archive_rejects_symlinked_suite(tmp_path: Path) -> None:
    manifest_id = "p1-formal-v2"
    manifest = build_manifest("a" * 40, manifest_id=manifest_id, project_root=PROJECT_ROOT)
    real_suite = tmp_path / "real-suite"
    real_suite.mkdir()
    suite_root = tmp_path / "artifacts" / "benchmarks" / manifest_id
    suite_root.parent.mkdir(parents=True)
    try:
        suite_root.symlink_to(real_suite, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not permitted on this platform")

    with pytest.raises(ArchiveError, match="symlink"):
        archive_suite(
            suite_root,
            tmp_path / "reports" / "benchmark" / manifest_id,
            manifest,
            project_root=tmp_path,
        )


def test_archive_rejects_symlinked_archive_parent(tmp_path: Path) -> None:
    manifest_id = "p1-formal-v2"
    manifest = build_manifest("a" * 40, manifest_id=manifest_id, project_root=PROJECT_ROOT)
    suite_root = tmp_path / "artifacts" / "benchmarks" / manifest_id
    suite_root.mkdir(parents=True)
    real_reports = tmp_path / "real-reports"
    real_reports.mkdir()
    reports = tmp_path / "reports"
    try:
        reports.symlink_to(real_reports, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not permitted on this platform")

    with pytest.raises(ArchiveError, match="symlink"):
        archive_suite(
            suite_root,
            reports / "benchmark" / manifest_id,
            manifest,
            project_root=tmp_path,
        )


def test_archive_rejects_symlink_inside_interrupted_artifacts(tmp_path: Path) -> None:
    manifest_id = "p1-formal-v2"
    manifest = build_manifest("a" * 40, manifest_id=manifest_id, project_root=PROJECT_ROOT)
    cell = manifest.cells[0]
    suite_root = tmp_path / "artifacts" / "benchmarks" / manifest_id
    suite_root.mkdir(parents=True)
    _write_ledger(suite_root, manifest_id, cell)
    _write_doctor(suite_root, manifest)
    interrupted = suite_root / "interrupted-artifacts" / cell.run_id
    interrupted.mkdir(parents=True)
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    try:
        (interrupted / "linked.txt").symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is not permitted on this platform")

    with pytest.raises(ArchiveError, match="symlink"):
        archive_suite(
            suite_root,
            tmp_path / "reports" / "benchmark" / manifest_id,
            manifest,
            project_root=tmp_path,
        )


def test_archive_accepts_partial_formal_prefix_without_report(tmp_path: Path) -> None:
    manifest_id = "p1-formal-v2"
    manifest = build_manifest("a" * 40, manifest_id=manifest_id, project_root=PROJECT_ROOT)
    cell = manifest.cells[0]
    suite_root = tmp_path / "artifacts" / "benchmarks" / manifest_id
    suite_root.mkdir(parents=True)
    _write_ledger(suite_root, manifest_id, cell)
    _write_cell(tmp_path, manifest, cell.run_id)
    _write_doctor(suite_root, manifest)

    archive_root = tmp_path / "reports" / "benchmark" / manifest_id
    result = archive_suite(suite_root, archive_root, manifest, project_root=tmp_path)

    assert result.cell_count == 1
    assert {path.name for path in archive_root.iterdir()} == {
        "ledger.jsonl",
        "doctor.json",
        "gate-matrix.csv",
        "aggregate_sha256.json",
    }


def test_archive_marks_kernel_setup_error_controller_checks_not_applicable(tmp_path: Path) -> None:
    manifest_id = "p1-formal-v2"
    manifest = build_manifest("a" * 40, manifest_id=manifest_id, project_root=PROJECT_ROOT)
    cell = next(cell for cell in manifest.cells if cell.strategy.value == "DIAGNOSTIC_KERNEL")
    selector = BenchmarkCellSelector(manifest_id=manifest_id, sequences=(cell.sequence,))
    suite_root = tmp_path / "artifacts" / "benchmarks" / manifest_id
    suite_root.mkdir(parents=True)
    _write_ledger(suite_root, manifest_id, cell, reason="RUN_SETUP_ERROR")
    _write_cell(tmp_path, manifest, cell.run_id)
    _write_doctor(suite_root, manifest, selector)
    (suite_root / "subset.json").write_text(
        selector.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
    )

    archive_root = tmp_path / "reports" / "benchmark" / manifest_id
    archive_suite(suite_root, archive_root, manifest, project_root=tmp_path)

    row = (archive_root / "gate-matrix.csv").read_text(encoding="utf-8").splitlines()[1]
    assert row.endswith(",NOT_APPLICABLE,NOT_APPLICABLE,NOT_APPLICABLE")


def test_archive_rejects_manifest_digest_mismatch(tmp_path: Path) -> None:
    manifest_id = "p1-formal-v2"
    manifest = build_manifest("a" * 40, manifest_id=manifest_id, project_root=PROJECT_ROOT)
    suite_root = tmp_path / "artifacts" / "benchmarks" / manifest_id
    suite_root.mkdir(parents=True)
    _write_doctor(suite_root, manifest, digest="b" * 64)

    with pytest.raises(ArchiveError, match="acceptable"):
        archive_suite(
            suite_root,
            tmp_path / "reports" / "benchmark" / manifest_id,
            manifest,
            project_root=tmp_path,
        )


def test_archive_rejects_result_inputs_digest_mismatch(tmp_path: Path) -> None:
    manifest_id = "p1-formal-v2"
    manifest = build_manifest("a" * 40, manifest_id=manifest_id, project_root=PROJECT_ROOT)
    cell = manifest.cells[0]
    suite_root = tmp_path / "artifacts" / "benchmarks" / manifest_id
    suite_root.mkdir(parents=True)
    _write_ledger(suite_root, manifest_id, cell)
    _write_doctor(suite_root, manifest)
    payload = json.loads((suite_root / "doctor.json").read_text(encoding="utf-8"))
    payload["result_inputs_sha256"] = "0" * 64
    (suite_root / "doctor.json").write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ArchiveError, match="acceptable"):
        archive_suite(
            suite_root,
            tmp_path / "reports" / "benchmark" / manifest_id,
            manifest,
            project_root=tmp_path,
        )


def test_archive_rejects_duplicate_ledger_json_key(tmp_path: Path) -> None:
    manifest_id = "p1-formal-v2"
    manifest = build_manifest("a" * 40, manifest_id=manifest_id, project_root=PROJECT_ROOT)
    cell = manifest.cells[0]
    suite_root = tmp_path / "artifacts" / "benchmarks" / manifest_id
    suite_root.mkdir(parents=True)
    _write_ledger(suite_root, manifest_id, cell)
    ledger_path = suite_root / "ledger.jsonl"
    ledger_path.write_text(
        ledger_path.read_text(encoding="utf-8").replace(
            '"state":"STARTED"', '"state":"STARTED","state":"STARTED"', 1
        ),
        encoding="utf-8",
    )
    _write_doctor(suite_root, manifest)

    with pytest.raises(ArchiveError, match="ledger is invalid"):
        archive_suite(
            suite_root,
            tmp_path / "reports" / "benchmark" / manifest_id,
            manifest,
            project_root=tmp_path,
        )


def test_archive_rejects_invalid_evidence_or_trace(tmp_path: Path) -> None:
    manifest_id = "p1-formal-v2"
    manifest = build_manifest("a" * 40, manifest_id=manifest_id, project_root=PROJECT_ROOT)
    cell = manifest.cells[0]
    selector = BenchmarkCellSelector(manifest_id=manifest_id, sequences=(cell.sequence,))
    suite_root = tmp_path / "artifacts" / "benchmarks" / manifest_id
    suite_root.mkdir(parents=True)
    _write_ledger(suite_root, manifest_id, cell)
    _write_cell(tmp_path, manifest, cell.run_id)
    _write_doctor(suite_root, manifest, selector)
    (suite_root / "subset.json").write_text(
        selector.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (tmp_path / "artifacts" / cell.run_id / "trace.jsonl").write_text(
        "{\"schema_version\":\"p1.trace.v1\",\"sequence\":1,\"event\":{}}\n",
        encoding="utf-8",
    )

    with pytest.raises(ArchiveError, match="artifact schema|trace"):
        archive_suite(
            suite_root,
            tmp_path / "reports" / "benchmark" / manifest_id,
            manifest,
            project_root=tmp_path,
        )
