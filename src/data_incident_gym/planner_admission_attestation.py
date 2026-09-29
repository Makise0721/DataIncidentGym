"""Strict, privacy-bounded T06 admission proof for the planner-comparison v2 identity."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, model_validator

from data_incident_gym.benchmark_manifest import (
    FORMAL_SCENARIO_IDS,
    BenchmarkManifestError,
    _reject_duplicate_json_keys,
)
from data_incident_gym.reference_solver import REFERENCE_ANALYST_TOOL_LIMIT
from data_incident_gym.scenario_admission import (
    ScenarioAdmissionReport,
    build_admission,
)
from data_incident_gym.scenario_certification import _EXPECTED_REFUSAL_CODES
from data_incident_gym.scenario_sets import ScenarioSetsError, load_scenario_sets
from data_incident_gym.scenarios import ScenarioError, load_scenario_spec

ADMISSION_ATTESTATION_SCHEMA_VERSION = "p1.planner_admission_attestation.v1"
ADMISSION_ATTESTATION_PATH = Path(
    "config/benchmark/p1-planner-compare-v2-admission.json"
)
ADMISSION_REPORT_RELATIVE_PATH = Path(
    "artifacts/admissions/planner-compare-v2-formal12.json"
)
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
Sha256 = Annotated[StrictStr, Field(pattern=_SHA256_PATTERN)]

_EXPECTED_CERTIFICATION_FINDINGS = (
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


class PlannerAdmissionAttestationEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: StrictStr
    scenario_digest: Sha256
    admission_entry_sha256: Sha256


class PlannerAdmissionAttestation(BaseModel):
    """Public identity of a private admission report; it contains hashes only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[
        "p1.planner_admission_attestation.v1"
    ] = ADMISSION_ATTESTATION_SCHEMA_VERSION
    raw_report_sha256: Sha256
    entries: tuple[PlannerAdmissionAttestationEntry, ...] = Field(
        min_length=len(FORMAL_SCENARIO_IDS),
        max_length=len(FORMAL_SCENARIO_IDS),
    )

    @model_validator(mode="after")
    def validate_formal_entries(self) -> PlannerAdmissionAttestation:
        actual = tuple(entry.case_id for entry in self.entries)
        if actual != FORMAL_SCENARIO_IDS:
            raise ValueError("admission attestation must follow the formal scenario order")
        return self

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
        ) + "\n"

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def _parse_strict_json(raw: bytes, *, label: str) -> object:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_json_constant,
        )
    except (UnicodeDecodeError, ValueError):
        raise BenchmarkManifestError(f"{label} JSON is invalid") from None


def _path_has_symlink_component(path: Path, root: Path) -> bool:
    path = Path(os.path.abspath(path))
    root = Path(os.path.abspath(root))
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _git_common_dir(root: Path) -> Path:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-common-dir"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise BenchmarkManifestError("Git repository identity is unavailable") from None
    if result.returncode != 0 or not result.stdout.strip():
        raise BenchmarkManifestError("Git repository identity is unavailable")
    common_dir = Path(result.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = root / common_dir
    try:
        return common_dir.resolve(strict=True)
    except OSError:
        raise BenchmarkManifestError("Git repository identity is unavailable") from None


def _read_ignored_admission_report(report_path: Path, project_root: Path) -> bytes:
    candidate = Path(report_path).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    candidate = Path(os.path.abspath(candidate))
    if (
        _path_has_symlink_component(candidate, Path(candidate.anchor))
        or not candidate.is_file()
    ):
        raise BenchmarkManifestError("raw admission report is missing or unsafe")
    try:
        resolved = candidate.resolve(strict=True)
        git_root_result = subprocess.run(
            ["git", "-C", str(resolved.parent), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise BenchmarkManifestError("raw admission report source is not a Git worktree") from None
    if git_root_result.returncode != 0 or not git_root_result.stdout.strip():
        raise BenchmarkManifestError("raw admission report source is not a Git worktree")
    try:
        git_root = Path(git_root_result.stdout.strip()).resolve(strict=True)
        relative = resolved.relative_to(git_root)
    except (OSError, ValueError):
        raise BenchmarkManifestError(
            "raw admission report path is outside its Git worktree"
        ) from None
    if _git_common_dir(git_root) != _git_common_dir(project_root):
        raise BenchmarkManifestError("raw admission report belongs to another Git repository")
    if relative.as_posix() != ADMISSION_REPORT_RELATIVE_PATH.as_posix():
        raise BenchmarkManifestError("raw admission report path is not the canonical ignored path")
    try:
        ignored_result = subprocess.run(
            [
                "git",
                "-C",
                str(git_root),
                "check-ignore",
                "--quiet",
                "--",
                relative.as_posix(),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise BenchmarkManifestError("raw admission report ignore status is unavailable") from None
    if ignored_result.returncode != 0:
        raise BenchmarkManifestError("raw admission report must remain ignored and untracked")
    try:
        return resolved.read_bytes()
    except OSError:
        raise BenchmarkManifestError("raw admission report could not be read") from None


def _validate_certification(entry, scenario, run_ids: set[str]) -> None:
    certification = entry.certification
    run = certification.run
    if (
        not entry.admitted
        or entry.reasons
        or certification.case_id != entry.case_id
        or not certification.certified
        or certification.failure_classes
        or run is None
    ):
        raise BenchmarkManifestError("raw admission report contains a non-admitted entry")
    finding_codes = tuple(finding.code for finding in certification.findings)
    if finding_codes != _EXPECTED_CERTIFICATION_FINDINGS or any(
        not finding.satisfied for finding in certification.findings
    ):
        raise BenchmarkManifestError("raw admission report certification findings are incomplete")
    if (
        run.run_id in run_ids
        or run.evaluation_status != "PASSED"
        or run.diagnosis_status != scenario.expected_status
        or run.failed_check_codes
        or run.tool_calls > REFERENCE_ANALYST_TOOL_LIMIT
        or run.successful_tool_calls > run.tool_calls
        or any(code not in _EXPECTED_REFUSAL_CODES for code in run.tool_error_codes)
    ):
        raise BenchmarkManifestError("raw admission report run summary is inconsistent")
    if scenario.expected_status == "CONFIRMED":
        if run.root_cause_code not in scenario.ground_truth_or_acceptable_root_causes:
            raise BenchmarkManifestError("raw admission report root cause is inconsistent")
        if set(run.affected_assets) != set(scenario.affected_assets):
            raise BenchmarkManifestError("raw admission report affected assets are inconsistent")
    run_ids.add(run.run_id)


def build_admission_attestation(
    report_path: Path,
    *,
    project_root: Path,
) -> PlannerAdmissionAttestation:
    """Strictly validate the ignored source report and derive a public hash-only proof."""

    root = Path(project_root).resolve(strict=True)
    raw_report = _read_ignored_admission_report(Path(report_path), root)
    payload = _parse_strict_json(raw_report, label="raw admission report")
    try:
        report = ScenarioAdmissionReport.model_validate(payload)
    except (ValidationError, TypeError, ValueError):
        raise BenchmarkManifestError("raw admission report schema is invalid") from None

    if tuple(entry.case_id for entry in report.entries) != FORMAL_SCENARIO_IDS:
        raise BenchmarkManifestError(
            "raw admission report must contain the ordered formal 12 exactly once"
        )
    try:
        scenario_sets = load_scenario_sets(root)
    except (OSError, ScenarioSetsError, ValueError):
        raise BenchmarkManifestError("current scenario-set registry is invalid") from None

    run_ids: set[str] = set()
    attestation_entries: list[PlannerAdmissionAttestationEntry] = []
    for entry in report.entries:
        try:
            scenario = load_scenario_spec(entry.case_id, root)
        except (OSError, ScenarioError, ValueError):
            raise BenchmarkManifestError("current formal scenario contract is invalid") from None
        current_digest = scenario.digest()
        if (
            entry.scenario_digest != current_digest
            or entry.certification.scenario_digest != current_digest
        ):
            raise BenchmarkManifestError("raw admission report scenario digest is stale")
        if scenario_sets.set_for(entry.case_id) != "dev":
            raise BenchmarkManifestError("formal admission scenario is not registered as dev")
        _validate_certification(entry, scenario, run_ids)
        if entry.card.completeness_issues() or any(
            not finding.satisfied for finding in entry.symmetry_findings
        ):
            raise BenchmarkManifestError("raw admission report card or symmetry is incomplete")
        try:
            rebuilt = build_admission(
                entry.case_id,
                entry.certification,
                project_root=root,
                scenario_sets=scenario_sets,
            )
        except (OSError, ScenarioError, ScenarioSetsError, ValueError):
            raise BenchmarkManifestError("raw admission report could not be recomputed") from None
        if rebuilt.model_dump(mode="json") != entry.model_dump(mode="json"):
            raise BenchmarkManifestError("raw admission report differs from recomputed admission")
        canonical_entry = json.dumps(
            entry.model_dump(mode="json"),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        attestation_entries.append(
            PlannerAdmissionAttestationEntry(
                case_id=entry.case_id,
                scenario_digest=current_digest,
                admission_entry_sha256=hashlib.sha256(canonical_entry).hexdigest(),
            )
        )

    return PlannerAdmissionAttestation(
        raw_report_sha256=hashlib.sha256(raw_report).hexdigest(),
        entries=tuple(attestation_entries),
    )


def admission_attestation_path(project_root: Path) -> Path:
    return Path(project_root) / ADMISSION_ATTESTATION_PATH


def write_admission_attestation(
    attestation: PlannerAdmissionAttestation,
    *,
    project_root: Path,
) -> Path:
    root = Path(project_root).resolve(strict=True)
    target = admission_attestation_path(root)
    if target.is_symlink() or target.exists():
        raise BenchmarkManifestError("admission attestation output already exists")
    for parent in (root / "config", root / "config" / "benchmark"):
        if parent.is_symlink():
            raise BenchmarkManifestError("admission attestation output parent is unsafe")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8", newline="") as handle:
            handle.write(attestation.canonical_json())
    except OSError:
        raise BenchmarkManifestError("admission attestation could not be written") from None
    return target


def load_admission_attestation(*, project_root: Path) -> PlannerAdmissionAttestation:
    root = Path(project_root).resolve(strict=True)
    path = admission_attestation_path(root)
    if path.is_symlink() or not path.is_file() or _path_has_symlink_component(path, root):
        raise BenchmarkManifestError("tracked admission attestation is missing or unsafe")
    try:
        raw = path.read_bytes()
    except OSError:
        raise BenchmarkManifestError("tracked admission attestation could not be read") from None
    payload = _parse_strict_json(raw, label="admission attestation")
    try:
        attestation = PlannerAdmissionAttestation.model_validate(payload)
    except (ValidationError, TypeError, ValueError):
        raise BenchmarkManifestError("admission attestation schema is invalid") from None
    if raw != attestation.canonical_json().encode("utf-8"):
        raise BenchmarkManifestError("admission attestation is not in canonical format")
    try:
        tracked = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--error-unmatch",
                "--",
                ADMISSION_ATTESTATION_PATH.as_posix(),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise BenchmarkManifestError(
            "tracked admission attestation status is unavailable"
        ) from None
    if tracked.returncode != 0:
        raise BenchmarkManifestError("admission attestation must be tracked by Git")
    return attestation


def verify_admission_attestation(
    *,
    expected_sha256: str,
    formal_scenario_ids: tuple[str, ...],
    project_root: Path,
) -> PlannerAdmissionAttestation:
    """Verify a checked-in proof against its manifest and current public contracts."""

    attestation = load_admission_attestation(project_root=project_root)
    if attestation.digest() != expected_sha256:
        raise BenchmarkManifestError("admission attestation digest does not match manifest")
    if formal_scenario_ids != FORMAL_SCENARIO_IDS:
        raise BenchmarkManifestError("manifest formal scenario order is invalid")
    if tuple(entry.case_id for entry in attestation.entries) != formal_scenario_ids:
        raise BenchmarkManifestError("admission attestation formal scenarios do not match manifest")

    try:
        scenario_sets = load_scenario_sets(project_root)
    except (OSError, ScenarioSetsError, ValueError):
        raise BenchmarkManifestError("current scenario-set registry is invalid") from None
    for entry in attestation.entries:
        if scenario_sets.set_for(entry.case_id) != "dev":
            raise BenchmarkManifestError("admission attestation scenario is not registered as dev")
        try:
            current_digest = load_scenario_spec(entry.case_id, project_root).digest()
        except (OSError, ScenarioError, ValueError):
            raise BenchmarkManifestError("current formal scenario contract is invalid") from None
        if entry.scenario_digest != current_digest:
            raise BenchmarkManifestError("admission attestation scenario digest is stale")
    return attestation


def validate_original_report_for_freeze(
    report_path: Path | None,
    *,
    expected_sha256: str,
    project_root: Path,
) -> PlannerAdmissionAttestation:
    """Require the ignored source report at v2 freeze and match its proof to the manifest."""

    if report_path is None:
        raise BenchmarkManifestError("v2 freeze requires --admission-report")
    source_attestation = build_admission_attestation(report_path, project_root=project_root)
    stored_attestation = load_admission_attestation(project_root=project_root)
    if source_attestation != stored_attestation:
        raise BenchmarkManifestError("source admission report does not match tracked attestation")
    if stored_attestation.digest() != expected_sha256:
        raise BenchmarkManifestError("tracked admission attestation digest does not match manifest")
    return stored_attestation
