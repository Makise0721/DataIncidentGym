"""Scenario certification against the public-evidence reference solution (T04).

Certification answers one question per scenario: is the intended answer
reachable from the public surface alone, inside the reference budget? It runs
``ReferenceAnalystRunner`` through the ordinary evaluation pipeline (reset,
inject, build, diagnose, evaluate, recover) and then records findings that
separate three identities:

- the **public reference run**: tool calls, receipts and evaluation produced by
  the analyst that never saw the private contract;
- the **private answer witness**: the contract's expected status, root cause,
  assets and gap matrix, read by the certification harness to check the
  scenario's expectations are self-consistent with the public surface. The
  witness is not an agent baseline and not a public solvability proof;
- the **failure classification**: a failed certification is first attributed to
  the reference implementation, the tools, the budget or the scoring before any
  scenario defect is claimed.

A run that reaches the contract's expected answer and still fails the evaluator
is a scoring finding; a run that misses the expected answer is a reference
finding; neither silently becomes "the scenario is bad".

Every certificate carries ``scenario_digest``: the digest of the private
contract it was actually produced against. Admission compares it with the
contract that is current at admission time, so editing a scenario invalidates
its certificate instead of silently reusing a verdict that was never earned
under the new contract.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, model_validator

from data_incident_gym.artifacts import ArtifactWriter
from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.diagnosis import DiagnosticStrategy, ToolTraceEvent
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evaluation import DeterministicEvaluator
from data_incident_gym.evaluation_inputs import load_evaluation_input_bundle
from data_incident_gym.evaluation_runner import EvaluationRunner, EvaluationWorkflowError
from data_incident_gym.lab import IncidentLab
from data_incident_gym.reference_solver import (
    REFERENCE_ANALYST_TOOL_LIMIT,
    ReferenceAnalystRunner,
)
from data_incident_gym.scenarios import SUPPORTED_SCENARIO_IDS, ScenarioSpec, load_scenario_spec

CERTIFICATION_SCHEMA_VERSION = "p1.scenario_certification.v1"
CERTIFICATION_DIRNAME = Path("artifacts") / "certifications"
_RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_DIGEST_PATTERN = r"^[0-9a-f]{64}$"

FindingCode = Literal[
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
]

FailureClass = Literal["TOOL", "BUDGET", "SCORING", "REFERENCE_IMPLEMENTATION", "ENVIRONMENT"]


class CertificationError(RuntimeError):
    def __init__(self, code: str, *, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")
        self.__cause__ = None
        self.__context__ = None


class CertificationFinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: StrictStr
    satisfied: StrictBool
    detail: StrictStr | None = None


class ReferenceRunSummary(BaseModel):
    """Public facts about the reference run; no contract fields are included."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: StrictStr
    evaluation_status: StrictStr
    diagnosis_status: StrictStr
    root_cause_code: StrictStr | None = None
    affected_assets: tuple[StrictStr, ...] = ()
    tool_calls: int = Field(ge=0)
    successful_tool_calls: int = Field(ge=0)
    tool_error_codes: tuple[StrictStr, ...] = ()
    failed_check_codes: tuple[StrictStr, ...] = ()

    @model_validator(mode="after")
    def validate_run_id(self) -> ReferenceRunSummary:
        if _RUN_ID_PATTERN.fullmatch(self.run_id) is None:
            raise ValueError("run_id must be 32 lowercase hex characters")
        return self


class ScenarioCertification(BaseModel):
    """A verdict bound to the exact private contract it was produced against.

    ``scenario_digest`` is the digest of that contract. It is required: a
    certificate without one cannot be constructed or loaded, so it can never
    reach an admission gate that would have to guess which contract the verdict
    covers.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.scenario_certification.v1"] = CERTIFICATION_SCHEMA_VERSION
    case_id: StrictStr
    scenario_digest: StrictStr = Field(pattern=_DIGEST_PATTERN)
    certified: StrictBool
    findings: tuple[CertificationFinding, ...]
    failure_classes: tuple[StrictStr, ...] = ()
    run: ReferenceRunSummary | None = None

    @model_validator(mode="after")
    def validate_verdict(self) -> ScenarioCertification:
        if self.certified and any(not item.satisfied for item in self.findings):
            raise ValueError("certified scenarios cannot carry unsatisfied findings")
        if self.certified and self.failure_classes:
            raise ValueError("certified scenarios cannot carry failure classes")
        return self

    def finding(self, code: str) -> bool | None:
        for item in self.findings:
            if item.code == code:
                return item.satisfied
        return None


class CatalogCertificationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.scenario_certification.v1"] = CERTIFICATION_SCHEMA_VERSION
    created_at: datetime
    entries: tuple[ScenarioCertification, ...]

    @property
    def certified_count(self) -> int:
        return sum(entry.certified for entry in self.entries)

    @property
    def uncertified_case_ids(self) -> tuple[str, ...]:
        return tuple(entry.case_id for entry in self.entries if not entry.certified)


def reference_evaluation_runner(
    *,
    project_root: Path = PROJECT_ROOT,
    settings: Settings | None = None,
    diagnostic_settings: DiagnosticSettings | None = None,
) -> EvaluationRunner:
    """An evaluation runner whose strategy is the public reference analyst."""

    lab_settings = settings or Settings(_env_file=None)
    lab = IncidentLab(lab_settings, project_root)
    reference_settings = diagnostic_settings or DiagnosticSettings(_env_file=None)

    def diagnosis_factory(run_id: str, strategy: DiagnosticStrategy) -> ReferenceAnalystRunner:
        if strategy is not DiagnosticStrategy.REFERENCE_ANALYST:
            raise CertificationError("REFERENCE_STRATEGY_REQUIRED", detail=strategy.value)
        return ReferenceAnalystRunner.for_run(run_id, reference_settings, project_root)

    return EvaluationRunner(
        lab=lab,
        diagnostic_settings=reference_settings,
        diagnosis_factory=diagnosis_factory,
        private_scenario_loader=lambda case_id: load_scenario_spec(case_id, project_root),
        private_verification_loader=lab.verifier.load_verification,
        evaluator=DeterministicEvaluator.evaluate,
        artifact_writer=ArtifactWriter(project_root),
        clock=lambda: datetime.now(UTC),
        project_root=project_root,
    )


def _receipt_proved(
    scenario: ScenarioSpec,
    trace_events: tuple[ToolTraceEvent, ...],
) -> bool:
    for gap in scenario.observable_evidence_contract.unresolved_gaps:
        if gap.tool_name is None:
            continue
        matching = tuple(
            event
            for event in trace_events
            if event.tool_name == gap.tool_name
            and event.error_code is not None
            and gap.subject in event.arguments.values()
        )
        if len(matching) != 1 or matching[0].error_code != gap.reason_code:
            return False
    return True


def _expected_gap_keys(scenario: ScenarioSpec) -> set[tuple[str, str, str]]:
    return {
        (gap.gap_kind, gap.subject, gap.reason_code)
        for gap in scenario.observable_evidence_contract.unresolved_gaps
    }


def _classify_failure(
    *,
    scenario: ScenarioSpec,
    run: ReferenceRunSummary,
    expected_root_ok: bool,
    assets_ok: bool,
    gaps_ok: bool,
    status_ok: bool,
    receipts_ok: bool,
    types_ok: bool,
    cited_types_ok: bool,
    evaluation_passed: bool,
) -> tuple[str, ...]:
    if evaluation_passed:
        return ()
    classes: list[str] = []
    unexpected_errors = tuple(
        code for code in run.tool_error_codes if code != "RELATION_NOT_ALLOWED"
    )
    if unexpected_errors:
        classes.append("TOOL")
    if run.tool_calls >= REFERENCE_ANALYST_TOOL_LIMIT:
        classes.append("BUDGET")
    # SCORING requires the run to satisfy everything the contract demands
    # (status, root cause, assets, gap matrix, receipts, and the required
    # evidence types both collected in the inventory and cited by the final
    # diagnosis) and the evaluator to reject it anyway. A run that misses any
    # contract requirement — for example a required schema record that was
    # collected but never cited — is a reference-implementation finding, not a
    # scoring finding.
    contract_ok = (
        status_ok
        and expected_root_ok
        and assets_ok
        and gaps_ok
        and receipts_ok
        and types_ok
        and cited_types_ok
    )
    if contract_ok:
        classes.append("SCORING")
    if not contract_ok:
        classes.append("REFERENCE_IMPLEMENTATION")
    return tuple(dict.fromkeys(classes))


def _build_findings(
    *,
    scenario: ScenarioSpec,
    evaluation_status: str,
    diagnosis_status: str,
    root_cause_code: str | None,
    affected_assets: tuple[str, ...],
    gap_keys: set[tuple[str, str, str]],
    collected_evidence_types: set[str],
    cited_evidence_types: set[str],
    trace_events: tuple[ToolTraceEvent, ...],
    tool_calls: int,
) -> tuple[tuple[CertificationFinding, ...], dict[str, bool]]:
    expected_status = scenario.expected_status
    status_ok = diagnosis_status == expected_status
    confirmed = expected_status == "CONFIRMED"
    insufficient = expected_status == "INSUFFICIENT_EVIDENCE"
    expected_root_ok = (
        root_cause_code in scenario.ground_truth_or_acceptable_root_causes
        if confirmed
        else True
    )
    assets_ok = set(affected_assets) == set(scenario.affected_assets) if confirmed else True
    gaps_ok = gap_keys == _expected_gap_keys(scenario) if insufficient else True
    receipts_ok = _receipt_proved(scenario, trace_events) if insufficient else True
    required_types = set(scenario.required_evidence_types)
    types_ok = required_types.issubset(collected_evidence_types)
    missing_types = sorted(required_types - collected_evidence_types)
    cited_types_ok = required_types.issubset(cited_evidence_types)
    missing_cited_types = sorted(required_types - cited_evidence_types)
    unexpected_errors = tuple(
        code for code in (event.error_code for event in trace_events) if code is not None
    )
    unexpected_errors = tuple(
        code for code in unexpected_errors if code != "RELATION_NOT_ALLOWED"
    )
    evaluation_passed = evaluation_status == "PASSED"
    findings = (
        CertificationFinding(code="REFERENCE_RUN_COMPLETED", satisfied=True),
        CertificationFinding(
            code="EVALUATION_PASSED",
            satisfied=evaluation_passed,
            detail=None if evaluation_passed else "evaluator rejected the reference run",
        ),
        CertificationFinding(
            code="STATUS_MATCHES_CONTRACT",
            satisfied=status_ok,
            detail=f"expected {expected_status}, observed {diagnosis_status}",
        ),
        CertificationFinding(
            code="ROOT_CAUSE_ACCEPTABLE",
            satisfied=expected_root_ok,
            detail=None if expected_root_ok else f"observed {root_cause_code}",
        ),
        CertificationFinding(
            code="AFFECTED_ASSETS_MATCH_CONTRACT",
            satisfied=assets_ok,
            detail=None if assets_ok else "asset set differs from the contract",
        ),
        CertificationFinding(
            code="GAP_MATRIX_MATCHES_CONTRACT",
            satisfied=gaps_ok,
            detail=None if gaps_ok else "gap set differs from the contract",
        ),
        CertificationFinding(
            code="RECEIPTS_PRESENT",
            satisfied=receipts_ok,
            detail=None if receipts_ok else "blocked gaps lack a matching refusal receipt",
        ),
        CertificationFinding(
            code="REQUIRED_EVIDENCE_TYPES_COLLECTED",
            satisfied=types_ok,
            detail=None if types_ok else f"missing {', '.join(missing_types)}",
        ),
        CertificationFinding(
            code="REQUIRED_EVIDENCE_TYPES_CITED",
            satisfied=cited_types_ok,
            detail=None if cited_types_ok else f"missing {', '.join(missing_cited_types)}",
        ),
        CertificationFinding(
            code="WITHIN_REFERENCE_BUDGET",
            satisfied=tool_calls <= REFERENCE_ANALYST_TOOL_LIMIT,
            detail=f"tool calls {tool_calls}",
        ),
        CertificationFinding(
            code="NO_UNEXPECTED_TOOL_ERRORS",
            satisfied=not unexpected_errors,
            detail=None if not unexpected_errors else ", ".join(sorted(set(unexpected_errors))),
        ),
    )
    return findings, {
        "status_ok": status_ok,
        "root_ok": expected_root_ok,
        "assets_ok": assets_ok,
        "gaps_ok": gaps_ok,
        "receipts_ok": receipts_ok,
        "types_ok": types_ok,
        "cited_types_ok": cited_types_ok,
        "evaluation_passed": evaluation_passed,
    }


async def certify_scenario(
    case_id: str,
    *,
    project_root: Path = PROJECT_ROOT,
    runner: EvaluationRunner | None = None,
) -> ScenarioCertification:
    """Run the reference analyst and certify one scenario."""

    scenario = load_scenario_spec(case_id, project_root)
    selected_runner = runner or reference_evaluation_runner(project_root=project_root)
    try:
        attempt = await selected_runner.run(case_id, DiagnosticStrategy.REFERENCE_ANALYST)
    except EvaluationWorkflowError as error:
        return ScenarioCertification(
            case_id=case_id,
            scenario_digest=scenario.digest(),
            certified=False,
            findings=(
                CertificationFinding(
                    code="REFERENCE_RUN_COMPLETED",
                    satisfied=False,
                    detail=error.code,
                ),
            ),
            failure_classes=("ENVIRONMENT",),
        )

    bundle = load_evaluation_input_bundle(project_root, attempt.run_id)
    diagnosis = bundle.diagnosis_run.diagnosis
    trace_events = tuple(
        event for event in bundle.diagnosis_run.trace if isinstance(event, ToolTraceEvent)
    )
    run = ReferenceRunSummary(
        run_id=attempt.run_id,
        evaluation_status=attempt.evaluation.status.value,
        diagnosis_status=diagnosis.status.value,
        root_cause_code=diagnosis.root_cause_code,
        affected_assets=diagnosis.affected_assets,
        tool_calls=bundle.diagnosis_run.metrics.tool_call_attempts,
        successful_tool_calls=bundle.diagnosis_run.metrics.successful_tool_calls,
        tool_error_codes=tuple(
            event.error_code for event in trace_events if event.error_code is not None
        ),
        failed_check_codes=tuple(
            code.value for code in attempt.evaluation.failed_check_codes
        ),
    )
    evidence_type_by_id = {
        record.evidence_id: record.evidence_type.value
        for record in bundle.diagnosis_run.evidence_records
    }
    findings, flags = _build_findings(
        scenario=scenario,
        evaluation_status=run.evaluation_status,
        diagnosis_status=run.diagnosis_status,
        root_cause_code=run.root_cause_code,
        affected_assets=run.affected_assets,
        gap_keys={
            (item.evidence_kind, item.subject, item.reason_code)
            for item in diagnosis.unresolved_evidence
        },
        collected_evidence_types=set(evidence_type_by_id.values()),
        cited_evidence_types={
            evidence_type_by_id[evidence_id]
            for evidence_id in diagnosis.evidence_ids
            if evidence_id in evidence_type_by_id
        },
        trace_events=trace_events,
        tool_calls=run.tool_calls,
    )
    certified = all(item.satisfied for item in findings)
    failure_classes = (
        ()
        if certified
        else _classify_failure(
            scenario=scenario,
            run=run,
            expected_root_ok=flags["root_ok"],
            assets_ok=flags["assets_ok"],
            gaps_ok=flags["gaps_ok"],
            status_ok=flags["status_ok"],
            receipts_ok=flags["receipts_ok"],
            types_ok=flags["types_ok"],
            cited_types_ok=flags["cited_types_ok"],
            evaluation_passed=flags["evaluation_passed"],
        )
    )
    return ScenarioCertification(
        case_id=case_id,
        scenario_digest=scenario.digest(),
        certified=certified,
        findings=findings,
        failure_classes=failure_classes,
        run=run,
    )


async def certify_catalog(
    case_ids: tuple[str, ...] | None = None,
    *,
    project_root: Path = PROJECT_ROOT,
    runner: EvaluationRunner | None = None,
    created_at: datetime | None = None,
) -> CatalogCertificationReport:
    """Certify every requested scenario sequentially; never parallel."""

    selected = case_ids or SUPPORTED_SCENARIO_IDS
    selected_runner = runner or reference_evaluation_runner(project_root=project_root)
    entries: list[ScenarioCertification] = []
    for case_id in selected:
        entries.append(
            await certify_scenario(case_id, project_root=project_root, runner=selected_runner)
        )
    return CatalogCertificationReport(
        created_at=created_at or datetime.now(UTC),
        entries=tuple(entries),
    )


def write_certification_report(
    report: CatalogCertificationReport,
    path: Path,
) -> Path:
    target = Path(path)
    if target.is_symlink():
        raise CertificationError("CERTIFICATION_PATH_INVALID", detail=str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(
            report.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
            newline="",
        )
    except OSError:
        raise CertificationError("CERTIFICATION_WRITE_FAILED", detail=str(target)) from None
    return target


def run_certification(case_ids: tuple[str, ...] | None = None) -> CatalogCertificationReport:
    return asyncio.run(certify_catalog(case_ids))


__all__ = [
    "CERTIFICATION_DIRNAME",
    "CERTIFICATION_SCHEMA_VERSION",
    "CatalogCertificationReport",
    "CertificationError",
    "CertificationFinding",
    "ReferenceRunSummary",
    "ScenarioCertification",
    "certify_catalog",
    "certify_scenario",
    "reference_evaluation_runner",
    "run_certification",
    "write_certification_report",
]
