"""Strict, privacy-bounded T06 admission attestation tests."""

import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import data_incident_gym.planner_admission_attestation as attestation_module
from data_incident_gym.benchmark_manifest import FORMAL_SCENARIO_IDS, BenchmarkManifestError
from data_incident_gym.planner_admission_attestation import (
    ADMISSION_ATTESTATION_PATH,
    ADMISSION_REPORT_RELATIVE_PATH,
    PlannerAdmissionAttestation,
    build_admission_attestation,
    load_admission_attestation,
    validate_original_report_for_freeze,
    verify_admission_attestation,
    write_admission_attestation,
)
from data_incident_gym.scenario_admission import ScenarioAdmissionReport, build_admission
from data_incident_gym.scenario_certification import (
    CertificationFinding,
    ReferenceRunSummary,
    ScenarioCertification,
)
from data_incident_gym.scenario_sets import load_scenario_sets
from data_incident_gym.scenarios import load_scenario_spec

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FINDINGS = (
    "REFERENCE_RUN_COMPLETED",
    "EVALUATION_PASSED",
    "STATUS_MATCHES_CONTRACT",
    "ROOT_CAUSE_ACCEPTABLE",
    "AFFECTED_ASSETS_MATCH_CONTRACT",
    "GAP_MATRIX_MATCHES_CONTRACT",
    "RECEIPTS_PRESENT",
    "REQUIRED_EVIDENCE_TYPES_COLLECTED",
    "REQUIRED_EVIDENCE_TYPES_CITED",
    "WITHIN_REFERENCE_BUDGET",
    "NO_UNEXPECTED_TOOL_ERRORS",
)


def _init_project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "config").mkdir(parents=True)
    shutil.copytree(PROJECT_ROOT / "config" / "scenarios", root / "config" / "scenarios")
    shutil.copy2(PROJECT_ROOT / "config" / "scenario-sets.json", root / "config")
    (root / ".gitignore").write_text("artifacts/\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    return root


def _synthetic_report(root: Path) -> ScenarioAdmissionReport:
    scenario_sets = load_scenario_sets(root)
    entries = []
    for index, case_id in enumerate(FORMAL_SCENARIO_IDS, start=1):
        scenario = load_scenario_spec(case_id, root)
        status = getattr(scenario.expected_status, "value", scenario.expected_status)
        confirmed = status == "CONFIRMED"
        run = ReferenceRunSummary(
            run_id=f"{index:032x}",
            evaluation_status="PASSED",
            diagnosis_status=status,
            root_cause_code=(
                scenario.ground_truth_or_acceptable_root_causes[0] if confirmed else None
            ),
            affected_assets=scenario.affected_assets if confirmed else (),
            tool_calls=0,
            successful_tool_calls=0,
        )
        certification = ScenarioCertification(
            case_id=case_id,
            scenario_digest=scenario.digest(),
            certified=True,
            findings=tuple(
                CertificationFinding(code=code, satisfied=True) for code in _FINDINGS
            ),
            run=run,
        )
        entries.append(
            build_admission(
                case_id,
                certification,
                project_root=root,
                scenario_sets=scenario_sets,
            )
        )
    return ScenarioAdmissionReport(
        created_at=datetime(2026, 9, 29, tzinfo=UTC),
        entries=tuple(entries),
    )


def _write_raw_report(root: Path, report: ScenarioAdmissionReport) -> Path:
    path = root / ADMISSION_REPORT_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _write_attestation(root: Path, attestation: PlannerAdmissionAttestation) -> Path:
    path = write_admission_attestation(attestation, project_root=root)
    subprocess.run(
        ["git", "-C", str(root), "add", "--", ADMISSION_ATTESTATION_PATH.as_posix()],
        check=True,
    )
    return path


def test_attestation_is_hash_only_exclusive_and_verifiable(tmp_path: Path) -> None:
    root = _init_project(tmp_path)
    report_path = _write_raw_report(root, _synthetic_report(root))

    attestation = build_admission_attestation(report_path, project_root=root)
    assert attestation.raw_report_sha256 == hashlib.sha256(report_path.read_bytes()).hexdigest()
    assert tuple(entry.case_id for entry in attestation.entries) == FORMAL_SCENARIO_IDS
    assert set(attestation.model_dump(mode="json")) == {
        "schema_version",
        "raw_report_sha256",
        "entries",
    }
    assert set(attestation.entries[0].model_dump(mode="json")) == {
        "case_id",
        "scenario_digest",
        "admission_entry_sha256",
    }

    proof_path = _write_attestation(root, attestation)
    report_path.unlink()
    loaded = load_admission_attestation(project_root=root)
    assert loaded == attestation
    assert verify_admission_attestation(
        expected_sha256=attestation.digest(),
        formal_scenario_ids=FORMAL_SCENARIO_IDS,
        project_root=root,
    ) == attestation
    with pytest.raises(BenchmarkManifestError, match="already exists"):
        write_admission_attestation(attestation, project_root=root)
    assert proof_path.is_file()


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_version":"p1.scenario_admission.v1",'
        b'"schema_version":"p1.scenario_admission.v1"}',
        b'{"created_at":NaN}',
        b'{"created_at":Infinity}',
    ],
)
def test_raw_report_parser_rejects_duplicate_keys_and_nonfinite_values(
    tmp_path: Path,
    raw: bytes,
) -> None:
    root = _init_project(tmp_path)
    report_path = root / ADMISSION_REPORT_RELATIVE_PATH
    report_path.parent.mkdir(parents=True)
    report_path.write_bytes(raw)

    with pytest.raises(BenchmarkManifestError, match="JSON is invalid"):
        build_admission_attestation(report_path, project_root=root)


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("extra", "schema is invalid"),
        ("missing_case", "ordered formal 12"),
        ("reorder", "ordered formal 12"),
        ("duplicate_case", "ordered formal 12"),
        ("stale_digest", "scenario digest is stale"),
        ("duplicate_run", "run summary is inconsistent"),
        ("missing_finding", "findings are incomplete"),
        ("non_admitted", "non-admitted entry"),
    ],
)
def test_raw_report_rejects_invalid_admission_identity(
    tmp_path: Path,
    mutation: str,
    match: str,
) -> None:
    root = _init_project(tmp_path)
    report_path = _write_raw_report(root, _synthetic_report(root))
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if mutation == "extra":
        payload["private_extra"] = "must not be carried forward"
    elif mutation == "missing_case":
        payload["entries"].pop()
    elif mutation == "reorder":
        payload["entries"].reverse()
    elif mutation == "duplicate_case":
        payload["entries"][1] = json.loads(json.dumps(payload["entries"][0]))
    elif mutation == "stale_digest":
        payload["entries"][0]["scenario_digest"] = "0" * 64
        payload["entries"][0]["certification"]["scenario_digest"] = "0" * 64
    elif mutation == "duplicate_run":
        payload["entries"][1]["certification"]["run"]["run_id"] = payload["entries"][0][
            "certification"
        ]["run"]["run_id"]
    elif mutation == "missing_finding":
        payload["entries"][0]["certification"]["findings"].pop()
    elif mutation == "non_admitted":
        payload["entries"][0]["admitted"] = False
        payload["entries"][0]["reasons"] = ["CERTIFICATION_FAILED"]
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BenchmarkManifestError, match=match):
        build_admission_attestation(report_path, project_root=root)


def test_attestation_verification_rejects_replaced_digest(tmp_path: Path) -> None:
    root = _init_project(tmp_path)
    report_path = _write_raw_report(root, _synthetic_report(root))
    attestation = build_admission_attestation(report_path, project_root=root)
    _write_attestation(root, attestation)

    with pytest.raises(BenchmarkManifestError, match="does not match manifest"):
        verify_admission_attestation(
            expected_sha256="f" * 64,
            formal_scenario_ids=FORMAL_SCENARIO_IDS,
            project_root=root,
        )


def test_freeze_rejects_source_report_that_differs_from_tracked_proof(
    tmp_path: Path,
) -> None:
    root = _init_project(tmp_path)
    report_path = _write_raw_report(root, _synthetic_report(root))
    attestation = build_admission_attestation(report_path, project_root=root)
    _write_attestation(root, attestation)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["created_at"] = "2026-09-30T00:00:00Z"
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BenchmarkManifestError, match="does not match tracked attestation"):
        validate_original_report_for_freeze(
            report_path,
            expected_sha256=attestation.digest(),
            project_root=root,
        )


def test_untracked_attestation_is_rejected(tmp_path: Path) -> None:
    root = _init_project(tmp_path)
    report_path = _write_raw_report(root, _synthetic_report(root))
    attestation = build_admission_attestation(report_path, project_root=root)
    write_admission_attestation(attestation, project_root=root)

    with pytest.raises(BenchmarkManifestError, match="must be tracked"):
        load_admission_attestation(project_root=root)


def test_raw_report_rejects_formal_case_outside_dev_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _init_project(tmp_path)
    report_path = _write_raw_report(root, _synthetic_report(root))
    monkeypatch.setattr(
        attestation_module,
        "load_scenario_sets",
        lambda _root: SimpleNamespace(
            set_for=lambda case_id: "holdout" if case_id == FORMAL_SCENARIO_IDS[0] else "dev"
        ),
    )

    with pytest.raises(BenchmarkManifestError, match="not registered as dev"):
        build_admission_attestation(report_path, project_root=root)


def test_raw_report_must_come_from_same_git_repository(tmp_path: Path) -> None:
    project_root = _init_project(tmp_path / "current")
    foreign_root = _init_project(tmp_path / "foreign")
    foreign_report = _write_raw_report(foreign_root, _synthetic_report(foreign_root))

    with pytest.raises(BenchmarkManifestError, match="another Git repository"):
        build_admission_attestation(foreign_report, project_root=project_root)


@pytest.mark.parametrize(
    "raw",
    [b'{"raw_report_sha256":"a","raw_report_sha256":"b"}', b'{"x":NaN}'],
)
def test_proof_parser_rejects_duplicate_keys_and_nonfinite_values(
    tmp_path: Path,
    raw: bytes,
) -> None:
    root = _init_project(tmp_path)
    report_path = _write_raw_report(root, _synthetic_report(root))
    attestation = build_admission_attestation(report_path, project_root=root)
    proof_path = _write_attestation(root, attestation)
    proof_path.write_bytes(raw)

    with pytest.raises(BenchmarkManifestError, match="JSON is invalid"):
        load_admission_attestation(project_root=root)
