"""Scenario admission: certification + card + A/B symmetry + set membership (T06).

Admission is the management-plane gate that decides whether a scenario may
enter a benchmark suite. It combines four verdicts:

- the ``ScenarioCertification`` produced by the public-evidence reference run;
- the ``ScenarioCard`` built from the private contract and that certification;
- the ``ab_symmetry_findings`` verdict for paired scenarios;
- the dev/holdout membership from the scenario-set registry.

A scenario is admitted only when it is certified, symmetric with its partner,
card-complete, and a dev scenario. Holdout scenarios carry no certification
yet, so they cannot pass this gate until they are certified by the same
mechanism; dev scenarios used for prompt development stay dev forever.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, model_validator

from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.evaluation_runner import EvaluationRunner
from data_incident_gym.scenario_cards import (
    ScenarioCard,
    ab_partner,
    ab_symmetry_findings,
    build_scenario_card,
)
from data_incident_gym.scenario_certification import (
    CertificationFinding,
    ScenarioCertification,
    certify_scenario,
    reference_evaluation_runner,
)
from data_incident_gym.scenario_sets import ScenarioSets, load_scenario_sets
from data_incident_gym.scenarios import SUPPORTED_SCENARIO_IDS, load_scenario_spec

ADMISSION_SCHEMA_VERSION = "p1.scenario_admission.v1"
ADMISSIONS_DIRNAME = Path("artifacts") / "admissions"

AdmissionReasonCode = Literal[
    "CERTIFICATION_FAILED",
    "CERTIFICATION_CASE_MISMATCH",
    "CERTIFICATION_DIGEST_MISMATCH",
    "SYMMETRY_MISMATCH",
    "CARD_INCOMPLETE",
    "HOLDOUT_SCENARIO",
    "UNREGISTERED_SCENARIO",
]


class AdmissionError(RuntimeError):
    def __init__(self, code: str, *, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")
        self.__cause__ = None
        self.__context__ = None


class ScenarioAdmission(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.scenario_admission.v1"] = ADMISSION_SCHEMA_VERSION
    case_id: StrictStr
    admitted: StrictBool
    scenario_digest: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    certification: ScenarioCertification
    card: ScenarioCard
    symmetry_findings: tuple[CertificationFinding, ...] = ()
    reasons: tuple[StrictStr, ...] = ()

    @model_validator(mode="after")
    def validate_verdict(self) -> ScenarioAdmission:
        if self.admitted:
            if self.reasons:
                raise ValueError("admitted scenarios cannot carry rejection reasons")
            if not self.certification.certified:
                raise ValueError("admitted scenarios require a certified run")
            if self.certification.case_id != self.case_id:
                raise ValueError("admitted scenarios require a matching certification case")
            if self.certification.scenario_digest != self.scenario_digest:
                raise ValueError(
                    "admitted scenarios require a certificate for the current contract digest"
                )
            if any(not item.satisfied for item in self.symmetry_findings):
                raise ValueError("admitted scenarios cannot carry symmetry violations")
            issues = self.card.completeness_issues()
            if issues:
                raise ValueError(f"admitted scenarios require a complete card: {issues}")
        elif not self.reasons:
            raise ValueError("rejected admissions must carry rejection reasons")
        return self


class ScenarioAdmissionReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.scenario_admission.v1"] = ADMISSION_SCHEMA_VERSION
    created_at: datetime
    entries: tuple[ScenarioAdmission, ...]

    @property
    def admitted_count(self) -> int:
        return sum(entry.admitted for entry in self.entries)

    @property
    def rejected_case_ids(self) -> tuple[str, ...]:
        return tuple(entry.case_id for entry in self.entries if not entry.admitted)


def build_admission(
    case_id: str,
    certification: ScenarioCertification,
    *,
    project_root: Path = PROJECT_ROOT,
    scenario_sets: ScenarioSets | None = None,
) -> ScenarioAdmission:
    """Combine certification, card, symmetry and set membership for one case.

    The certification is bound to the admitted scenario by content, not by file
    location: its ``case_id`` must equal the admission target, and its
    ``scenario_digest`` must equal the digest of the contract that is current
    right now. A certificate from another scenario, or one produced before this
    scenario's contract changed, is stale by definition and is rejected
    (``CERTIFICATION_CASE_MISMATCH`` / ``CERTIFICATION_DIGEST_MISMATCH``); a
    certificate that carries no digest at all cannot even be parsed. The
    admission record stores the same digest, so readers can see which contract
    the verdict covers.
    """

    sets = scenario_sets or load_scenario_sets(project_root)
    spec = load_scenario_spec(case_id, project_root)
    current_digest = spec.digest()
    card = build_scenario_card(case_id, project_root=project_root, certification=certification)
    symmetry: tuple[CertificationFinding, ...] = ()
    partner_id = ab_partner(case_id)
    if partner_id is not None:
        symmetry = ab_symmetry_findings(spec, load_scenario_spec(partner_id, project_root))
    reasons: list[str] = []
    if certification.case_id != case_id:
        reasons.append("CERTIFICATION_CASE_MISMATCH")
    if certification.scenario_digest != current_digest:
        reasons.append("CERTIFICATION_DIGEST_MISMATCH")
    membership = sets.set_for(case_id)
    if membership == "holdout":
        reasons.append("HOLDOUT_SCENARIO")
    if membership == "unknown":
        reasons.append("UNREGISTERED_SCENARIO")
    if not certification.certified:
        reasons.append("CERTIFICATION_FAILED")
    if any(not item.satisfied for item in symmetry):
        reasons.append("SYMMETRY_MISMATCH")
    issues = card.completeness_issues()
    if issues:
        reasons.append("CARD_INCOMPLETE")
    return ScenarioAdmission(
        case_id=case_id,
        admitted=not reasons,
        scenario_digest=spec.digest(),
        certification=certification,
        card=card,
        symmetry_findings=symmetry,
        reasons=tuple(reasons),
    )


async def admit_scenario(
    case_id: str,
    *,
    project_root: Path = PROJECT_ROOT,
    runner: EvaluationRunner | None = None,
) -> ScenarioAdmission:
    """Certify one scenario and return its admission verdict."""

    certification = await certify_scenario(case_id, project_root=project_root, runner=runner)
    return build_admission(case_id, certification, project_root=project_root)


async def admit_catalog(
    case_ids: tuple[str, ...] | None = None,
    *,
    project_root: Path = PROJECT_ROOT,
    runner: EvaluationRunner | None = None,
    created_at: datetime | None = None,
) -> ScenarioAdmissionReport:
    """Admit every requested scenario sequentially; never parallel."""

    selected = case_ids or SUPPORTED_SCENARIO_IDS
    selected_runner = runner or reference_evaluation_runner(project_root=project_root)
    entries: list[ScenarioAdmission] = []
    for case_id in selected:
        certification = await certify_scenario(
            case_id, project_root=project_root, runner=selected_runner
        )
        entries.append(build_admission(case_id, certification, project_root=project_root))
    return ScenarioAdmissionReport(
        created_at=created_at or datetime.now(UTC),
        entries=tuple(entries),
    )


def write_admission_report(report: ScenarioAdmissionReport, path: Path) -> Path:
    target = Path(path)
    if target.is_symlink():
        raise AdmissionError("ADMISSION_PATH_INVALID", detail=str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(
            report.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
            newline="",
        )
    except OSError:
        raise AdmissionError("ADMISSION_WRITE_FAILED", detail=str(target)) from None
    return target


def run_admission(case_ids: tuple[str, ...] | None = None) -> ScenarioAdmissionReport:
    return asyncio.run(admit_catalog(case_ids))


__all__ = [
    "ADMISSIONS_DIRNAME",
    "ADMISSION_SCHEMA_VERSION",
    "AdmissionError",
    "ScenarioAdmission",
    "ScenarioAdmissionReport",
    "admit_catalog",
    "admit_scenario",
    "build_admission",
    "run_admission",
    "write_admission_report",
]
