"""Preserve decisive benchmark records outside the ignored artifact tree."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr

from data_incident_gym.artifacts import (
    ARTIFACT_FILENAMES,
    EvidenceArtifact,
    RunMetadata,
    TraceEnvelope,
)
from data_incident_gym.benchmark_manifest import APPROVED_MANIFEST_IDS, BenchmarkManifest
from data_incident_gym.benchmark_runner import (
    BenchmarkCellSelector,
    BenchmarkDoctorReceipt,
    BenchmarkLedgerEntry,
    is_receipt_acceptable,
)
from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.diagnosis import (
    KERNEL_STRATEGIES,
    Diagnosis,
    DiagnosisTerminalTraceEvent,
)
from data_incident_gym.evaluation import (
    ControllerCheckCode,
    EvaluationResult,
    EvaluationStatus,
)

_COPIED_FILES = (
    "ledger.jsonl",
    "doctor.json",
    "subset.json",
    "summary.json",
    "report.md",
)
_INTERRUPTED_ARTIFACTS_DIRNAME = "interrupted-artifacts"
_GATE_ORDER = (
    "ENVIRONMENT_VERIFIED",
    "STATUS_EXACT",
    "ROOT_CAUSE_ACCEPTED",
    "AFFECTED_ASSETS_EXACT",
    "EVIDENCE_IDS_EXIST",
    "EVIDENCE_RUN_SCOPE",
    "REQUIRED_EVIDENCE_TYPES_PRESENT",
    "CLAIM_EVIDENCE_COMPATIBLE",
    "INSUFFICIENCY_GAP_DECLARED",
    "POSITIVE_HEALTH_EVIDENCE",
    "TOOL_ALLOWLIST_EXACT",
    "TRACE_READ_ONLY_SAFE",
    "RECOVERY_HEALTHY",
)
_CONTROLLER_GATE_ORDER = tuple(code.value for code in ControllerCheckCode)
_METRIC_FIELDS = (
    "provider",
    "model",
    "model_requests",
    "input_tokens",
    "output_tokens",
    "tool_call_attempts",
    "successful_tool_calls",
    "elapsed_ms",
)


class ArchiveError(RuntimeError):
    """Raised when a suite cannot be archived safely."""


class ArchiveResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_id: StrictStr
    archive_root: StrictStr
    cell_count: StrictInt
    aggregate_sha256: StrictStr


def _reject_symlink(path: Path, message: str) -> None:
    if path.is_symlink():
        raise ArchiveError(message)


def _reject_symlinked_parents(project_root: Path, target: Path, message: str) -> None:
    """Reject existing symlink components between project root and target."""

    try:
        relative = target.relative_to(project_root)
    except ValueError as exc:
        raise ArchiveError("benchmark path escaped project root") from exc
    current = project_root
    for component in relative.parts[:-1]:
        current /= component
        _reject_symlink(current, message)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")),
        )
    except (OSError, ValueError) as exc:
        raise ArchiveError(f"cannot read benchmark JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise ArchiveError(f"benchmark JSON must be an object: {path.name}")
    return value


def _load_json_line(line: str) -> dict[str, object]:
    value = json.loads(
        line,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")),
    )
    if not isinstance(value, dict):
        raise ValueError("JSON line must be an object")
    return value


def _result_inputs_digest(manifest: BenchmarkManifest) -> str:
    payload = json.dumps(
        manifest.result_inputs.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_doctor(suite_root: Path, manifest: BenchmarkManifest) -> BenchmarkDoctorReceipt:
    path = suite_root / "doctor.json"
    if path.is_symlink() or not path.is_file():
        raise ArchiveError("doctor receipt is missing or invalid")
    try:
        receipt = BenchmarkDoctorReceipt.model_validate(_load_json(path))
    except Exception as exc:
        raise ArchiveError("doctor receipt is invalid") from exc
    selected = (
        manifest.cells
        if receipt.cell_selector is None
        else receipt.cell_selector.select(manifest.cells)
    )
    if (
        receipt.manifest_id != manifest.manifest_id
        or receipt.manifest_sha256 != manifest.digest()
        or receipt.implementation_revision != manifest.implementation_revision
        or receipt.result_inputs_sha256 != _result_inputs_digest(manifest)
        or receipt.model_probe_required != any(cell.model_backed for cell in selected)
        or not is_receipt_acceptable(receipt)
    ):
        raise ArchiveError("doctor receipt is not acceptable for this manifest")
    return receipt


def _load_selector(suite_root: Path, manifest_id: str) -> BenchmarkCellSelector | None:
    path = suite_root / "subset.json"
    if path.is_symlink():
        raise ArchiveError("subset marker must not be a symlink")
    if not path.exists():
        return None
    if not path.is_file():
        raise ArchiveError("subset marker is invalid")
    try:
        selector = BenchmarkCellSelector.model_validate(_load_json(path))
    except Exception as exc:
        raise ArchiveError("subset marker is invalid") from exc
    if selector.manifest_id != manifest_id:
        raise ArchiveError("subset marker manifest mismatch")
    return selector


def _terminal_entries(
    suite_root: Path,
    manifest: BenchmarkManifest,
    selector: BenchmarkCellSelector | None,
) -> tuple[BenchmarkLedgerEntry, ...]:
    ledger_path = suite_root / "ledger.jsonl"
    if ledger_path.is_symlink() or not ledger_path.is_file():
        raise ArchiveError("benchmark ledger is missing or invalid")
    try:
        lines = ledger_path.read_text(encoding="utf-8").splitlines()
        entries = tuple(
            BenchmarkLedgerEntry.model_validate(_load_json_line(line))
            for line in lines
            if line.strip()
        )
    except Exception as exc:
        raise ArchiveError("benchmark ledger is invalid") from exc
    cells = manifest.cells
    selected = selector.select(cells) if selector is not None else cells
    if not entries or len(entries) % 2 or len(entries) > len(selected) * 2:
        raise ArchiveError("benchmark ledger must be a non-empty terminal pair prefix")
    terminals: list[BenchmarkLedgerEntry] = []
    for index, cell in enumerate(selected[: len(entries) // 2]):
        started, terminal = entries[index * 2 : index * 2 + 2]
        identity = (
            started.manifest_id,
            started.sequence,
            started.run_id,
            started.incident_case_id,
            started.strategy,
        )
        if (
            identity
            != (
                terminal.manifest_id,
                terminal.sequence,
                terminal.run_id,
                terminal.incident_case_id,
                terminal.strategy,
            )
            or started.manifest_id != manifest.manifest_id
            or (started.sequence, started.run_id, started.incident_case_id, started.strategy)
            != (cell.sequence, cell.run_id, cell.incident_case_id, cell.strategy)
            or started.state != "STARTED"
            or terminal.state not in {"COMPLETED", "FAILED"}
            or terminal.started_at != started.started_at
        ):
            raise ArchiveError("benchmark ledger contains an invalid entry pair")
        terminals.append(terminal)
    return tuple(terminals)


def _artifact_path(project_root: Path, entry: BenchmarkLedgerEntry) -> Path:
    artifacts_root = project_root / "artifacts"
    _reject_symlink(artifacts_root, "artifacts root must not be a symlink")
    artifacts_root = artifacts_root.resolve(strict=True)
    candidate = project_root / entry.artifact_path
    _reject_symlink(candidate, "cell artifact must not be a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ArchiveError(f"cell artifact is missing: {entry.run_id}") from exc
    if resolved != artifacts_root / entry.run_id or not resolved.is_dir():
        raise ArchiveError("ledger artifact path escaped its canonical location")
    children = tuple(resolved.iterdir())
    if {path.name for path in children} != set(ARTIFACT_FILENAMES) or any(
        path.is_symlink() or not path.is_file() for path in children
    ):
        raise ArchiveError(f"cell artifact is not the canonical six files: {entry.run_id}")
    return resolved


def _load_cell_records(
    project_root: Path,
    entry: BenchmarkLedgerEntry,
    manifest: BenchmarkManifest,
    receipt: BenchmarkDoctorReceipt,
) -> tuple[RunMetadata, Diagnosis, EvaluationResult]:
    artifact = _artifact_path(project_root, entry)
    try:
        metadata = RunMetadata.model_validate(_load_json(artifact / "metadata.json"))
        evidence = EvidenceArtifact.model_validate(_load_json(artifact / "evidence.json"))
        diagnosis = Diagnosis.model_validate(_load_json(artifact / "diagnosis.json"))
        evaluation = EvaluationResult.model_validate(_load_json(artifact / "evaluation.json"))
        trace = tuple(
            TraceEnvelope.model_validate(_load_json_line(line))
            for line in (artifact / "trace.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    except Exception as exc:
        raise ArchiveError(f"cell artifact schema is invalid: {entry.run_id}") from exc
    if not trace or not isinstance(trace[-1].event, DiagnosisTerminalTraceEvent):
        raise ArchiveError(f"trace does not end in a diagnosis terminal: {entry.run_id}")
    if any(item.sequence != index for index, item in enumerate(trace, start=1)):
        raise ArchiveError(f"trace sequence is not contiguous: {entry.run_id}")
    evidence_ids = tuple(record.evidence_id for record in evidence.records)
    referenced_ids = set(diagnosis.evidence_ids)
    referenced_ids.update(
        evidence_id for claim in diagnosis.claims for evidence_id in claim.evidence_ids
    )
    if (
        metadata.run_id != entry.run_id
        or metadata.incident_case_id != entry.incident_case_id
        or metadata.strategy is not entry.strategy
        or metadata.code_revision != receipt.checkout_revision
        or metadata.workspace_dirty
        or metadata.benchmark_manifest_sha256 != manifest.digest()
        or metadata.evaluation_status is not evaluation.status
        or evidence.run_id != entry.run_id
        or evidence.incident_case_id != entry.incident_case_id
        or any(record.run_id != entry.run_id for record in evidence.records)
        or diagnosis.run_id != entry.run_id
        or evaluation.run_id != entry.run_id
        or evaluation.incident_case_id != entry.incident_case_id
        or (entry.state == "COMPLETED") != (evaluation.status is EvaluationStatus.PASSED)
        or trace[-1].event.strategy is not entry.strategy
        or trace[-1].event.status is not diagnosis.status
        or trace[-1].event.evidence_inventory != evidence_ids
        or not referenced_ids.issubset(set(evidence_ids))
    ):
        raise ArchiveError(f"cell identity does not match ledger: {entry.run_id}")
    return metadata, diagnosis, evaluation


def _cell_matrix(
    project_root: Path,
    entries: tuple[BenchmarkLedgerEntry, ...],
    manifest: BenchmarkManifest,
    receipt: BenchmarkDoctorReceipt,
) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(
        (
            "run_id",
            "sequence",
            "incident_case_id",
            "strategy",
            "diagnosis_status",
            "evaluation_status",
            "recovery_status",
            *_METRIC_FIELDS,
            *_GATE_ORDER,
            *_CONTROLLER_GATE_ORDER,
        )
    )
    for entry in entries:
        metadata, diagnosis, evaluation = _load_cell_records(
            project_root, entry, manifest, receipt
        )
        metrics = metadata.diagnosis_metrics
        checks = evaluation.checks
        gates = {
            check.code.value: (
                "NOT_APPLICABLE"
                if check.applicability.value != "APPLICABLE"
                else "PASS" if check.passed else "FAIL"
            )
            for check in checks
        }
        controller_codes = tuple(check.code for check in evaluation.controller_checks)
        expected_controller_codes = (
            ()
            if entry.strategy in KERNEL_STRATEGIES and entry.reason_code == "RUN_SETUP_ERROR"
            else tuple(ControllerCheckCode)
            if entry.strategy in KERNEL_STRATEGIES
            else ()
        )
        if controller_codes != expected_controller_codes:
            raise ArchiveError(f"controller checks are incomplete: {entry.run_id}")
        controller_gates = {
            check.code.value: "PASS" if check.passed else "FAIL"
            for check in evaluation.controller_checks
        }
        writer.writerow(
            (
                entry.run_id,
                entry.sequence,
                entry.incident_case_id,
                entry.strategy.value,
                diagnosis.status.value,
                evaluation.status.value,
                metadata.recovery_status.value,
                *(getattr(metrics, field) for field in _METRIC_FIELDS),
                *(gates.get(code, "NOT_APPLICABLE") for code in _GATE_ORDER),
                *(controller_gates.get(code, "NOT_APPLICABLE") for code in _CONTROLLER_GATE_ORDER),
            )
        )
    return stream.getvalue()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_aggregate(
    project_root: Path,
    suite_root: Path,
    entries: tuple[BenchmarkLedgerEntry, ...],
) -> tuple[str, int]:
    files = []
    for path in suite_root.rglob("*"):
        _reject_symlink(path, "benchmark suite must not contain a symlink")
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise ArchiveError("benchmark suite contains a special file")
    for entry in entries:
        files.extend(_artifact_path(project_root, entry).iterdir())
    named = sorted(
        (
            path.relative_to(project_root).as_posix(),
            _sha256_file(path),
        )
        for path in files
    )
    digest = hashlib.sha256()
    for name, value in named:
        digest.update(name.encode("utf-8") + b"\0" + value.encode("ascii") + b"\0")
    return digest.hexdigest(), len(named)


def _suite_file_names(suite_root: Path) -> set[str]:
    names = set()
    for path in suite_root.iterdir():
        _reject_symlink(path, "benchmark suite must not contain a symlink")
        if path.is_dir():
            if path.name != _INTERRUPTED_ARTIFACTS_DIRNAME:
                raise ArchiveError("benchmark suite contains an unexpected directory")
            for child in path.rglob("*"):
                _reject_symlink(child, "interrupted artifacts must not contain a symlink")
                if not child.is_dir() and not child.is_file():
                    raise ArchiveError("interrupted artifacts contain a special file")
        elif not path.is_file():
            raise ArchiveError("benchmark suite contains a special file")
        names.add(path.name)
    return names


def archive_suite(
    suite_root: Path,
    archive_root: Path,
    manifest: BenchmarkManifest,
    *,
    project_root: Path = PROJECT_ROOT,
) -> ArchiveResult:
    """Copy decisive suite records into a tracked directory without overwriting."""

    if not isinstance(manifest, BenchmarkManifest):
        raise ArchiveError("archive requires a verified BenchmarkManifest object")
    manifest_id = manifest.manifest_id
    if manifest_id not in APPROVED_MANIFEST_IDS:
        raise ArchiveError("manifest_id is not an approved formal identity")
    project_root = project_root.resolve(strict=True)
    suite = Path(suite_root)
    if not suite.is_absolute():
        suite = project_root / suite
    expected_suite = project_root / "artifacts" / "benchmarks" / manifest_id
    _reject_symlinked_parents(project_root, suite, "benchmark suite parent must not be a symlink")
    _reject_symlink(suite, "benchmark suite root must not be a symlink")
    if not suite.is_dir():
        raise ArchiveError("benchmark suite root is missing or invalid")
    if suite.resolve(strict=True) != expected_suite.resolve(strict=True):
        raise ArchiveError("benchmark suite root is not canonical")

    archive = Path(archive_root)
    if not archive.is_absolute():
        archive = project_root / archive
    expected_archive = project_root / "reports" / "benchmark" / manifest_id
    _reject_symlinked_parents(project_root, archive, "archive parent must not be a symlink")
    _reject_symlink(archive, "archive root must not be a symlink")
    if archive.resolve(strict=False) != expected_archive.resolve(strict=False):
        raise ArchiveError("archive root is not canonical")
    if archive.exists():
        raise ArchiveError("archive root already exists")

    doctor = _load_doctor(suite, manifest)
    selector = _load_selector(suite, manifest_id)
    if selector != doctor.cell_selector:
        raise ArchiveError("subset marker and doctor receipt scope do not match")
    entries = _terminal_entries(suite, manifest, selector)
    complete_formal = selector is None and len(entries) == len(manifest.cells)
    expected_names = {"ledger.jsonl", "doctor.json"}
    if (suite / _INTERRUPTED_ARTIFACTS_DIRNAME).is_dir():
        expected_names.add(_INTERRUPTED_ARTIFACTS_DIRNAME)
    if selector is not None:
        expected_names.add("subset.json")
    elif complete_formal:
        expected_names.update({"summary.json", "report.md"})
    actual_names = _suite_file_names(suite)
    if actual_names != expected_names:
        raise ArchiveError("benchmark suite files do not match its scope")
    aggregate, file_count = _source_aggregate(project_root, suite, entries)
    matrix = _cell_matrix(project_root, entries, manifest, doctor)

    archive.mkdir(parents=True, exist_ok=False)
    created_archive = True
    try:
        for name in _COPIED_FILES:
            source = suite / name
            if name not in expected_names:
                continue
            if not source.is_file():
                raise ArchiveError(f"benchmark suite file is missing: {name}")
            _reject_symlink(source, "archived suite file must not be a symlink")
            shutil.copyfile(source, archive / name)
        if _INTERRUPTED_ARTIFACTS_DIRNAME in expected_names:
            shutil.copytree(
                suite / _INTERRUPTED_ARTIFACTS_DIRNAME,
                archive / _INTERRUPTED_ARTIFACTS_DIRNAME,
            )
        payloads = (
            ("gate-matrix.csv", matrix),
            (
                "aggregate_sha256.json",
                json.dumps(
                    {
                        "manifest_id": manifest_id,
                        "source_file_count": file_count,
                        "cell_count": len(entries),
                        "manifest_sha256": doctor.manifest_sha256,
                        "source_aggregate_sha256": aggregate,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            ),
        )
        for name, payload in payloads:
            with (archive / name).open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        if _suite_file_names(suite) != expected_names:
            raise ArchiveError("benchmark suite files changed during archive")
        after_aggregate, after_file_count = _source_aggregate(project_root, suite, entries)
        if (after_aggregate, after_file_count) != (aggregate, file_count):
            raise ArchiveError("benchmark source changed during archive")
    except Exception as exc:
        if created_archive and archive.is_dir() and not archive.is_symlink():
            shutil.rmtree(archive, ignore_errors=True)
        if isinstance(exc, ArchiveError):
            raise
        raise ArchiveError("无法写入 benchmark 归档") from exc

    return ArchiveResult(
        manifest_id=manifest_id,
        archive_root=archive.relative_to(project_root).as_posix(),
        cell_count=len(entries),
        aggregate_sha256=aggregate,
    )


__all__ = ["ArchiveError", "ArchiveResult", "archive_suite"]
