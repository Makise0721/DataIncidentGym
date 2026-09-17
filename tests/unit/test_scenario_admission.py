"""Scenario admission and certify --admit CLI wiring tests (T06)."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

import data_incident_gym.cli as cli
from data_incident_gym.cli import app
from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.scenario_admission import (
    ScenarioAdmission,
    ScenarioAdmissionReport,
    build_admission,
    write_admission_report,
)
from data_incident_gym.scenario_certification import (
    CatalogCertificationReport,
    CertificationFinding,
    ReferenceRunSummary,
    ScenarioCertification,
)
from data_incident_gym.scenario_sets import (
    SCENARIO_SETS_PATH,
    ScenarioSetEntry,
    ScenarioSets,
    load_scenario_sets,
)
from data_incident_gym.scenarios import SUPPORTED_SCENARIO_IDS, load_scenario_spec

CASE_A = "required_null_order_customer_a"
CASE_B = "required_null_order_customer_b"
LEGACY_CASE = "required_null_payment_id"
CREATED_AT = datetime(2026, 9, 16, 8, 0, tzinfo=UTC)

runner = CliRunner()


def _run_summary(diagnosis_status: str = "CONFIRMED") -> ReferenceRunSummary:
    return ReferenceRunSummary(
        run_id="a" * 32,
        evaluation_status="PASSED",
        diagnosis_status=diagnosis_status,
        root_cause_code="SOURCE_REQUIRED_FIELD_NULL",
        affected_assets=("model.jaffle_shop.orders",),
        tool_calls=6,
        successful_tool_calls=6,
        tool_error_codes=(),
        failed_check_codes=(),
    )


def _certified(case_id: str) -> ScenarioCertification:
    return ScenarioCertification(
        case_id=case_id,
        scenario_digest=load_scenario_spec(case_id).digest(),
        certified=True,
        findings=(CertificationFinding(code="EVALUATION_PASSED", satisfied=True),),
        run=_run_summary(),
    )


def _uncertified(case_id: str) -> ScenarioCertification:
    return ScenarioCertification(
        case_id=case_id,
        scenario_digest=load_scenario_spec(case_id).digest(),
        certified=False,
        findings=(
            CertificationFinding(
                code="EVALUATION_PASSED",
                satisfied=False,
                detail="evaluator rejected the reference run",
            ),
        ),
        failure_classes=("SCORING",),
        run=_run_summary(diagnosis_status="CONFIRMED"),
    )


def _copy_management_config(tmp_path: Path, case_ids: tuple[str, ...]) -> None:
    scenarios_dir = tmp_path / "config" / "scenarios"
    scenarios_dir.mkdir(parents=True)
    for case_id in case_ids:
        shutil.copyfile(
            PROJECT_ROOT / "config" / "scenarios" / f"{case_id}.json",
            scenarios_dir / f"{case_id}.json",
        )
    shutil.copyfile(PROJECT_ROOT / SCENARIO_SETS_PATH, tmp_path / SCENARIO_SETS_PATH)


def test_admits_a_certified_paired_scenario() -> None:
    admission = build_admission(CASE_A, _certified(CASE_A))

    assert admission.admitted is True
    assert admission.reasons == ()
    assert admission.certification.certified is True
    assert admission.card.case_id == CASE_A
    assert admission.card.solvability.certified is True
    assert admission.symmetry_findings
    assert all(item.satisfied for item in admission.symmetry_findings)


def test_rejects_a_failed_certification() -> None:
    admission = build_admission(CASE_A, _uncertified(CASE_A))

    assert admission.admitted is False
    assert admission.reasons == ("CERTIFICATION_FAILED",)
    assert admission.card.solvability.certified is False
    assert "failure classes: SCORING" in (admission.card.solvability.summary or "")


def test_rejects_holdout_and_unknown_membership(tmp_path: Path) -> None:
    _copy_management_config(tmp_path, (LEGACY_CASE,))
    # A programmatically constructed registry stands in for a properly
    # in-taken future holdout scenario; the checked-in loader rejects moving a
    # historical development scenario into holdout (see test_scenario_sets).
    sets = ScenarioSets(
        partition_rule="by fault mechanism and task structure",
        dev_scenarios=(),
        holdout_scenarios=(
            ScenarioSetEntry(case_id=LEGACY_CASE, reason="reserved unseen variant"),
        ),
    )

    holdout = build_admission(
        LEGACY_CASE, _certified(LEGACY_CASE), project_root=tmp_path, scenario_sets=sets
    )
    assert holdout.admitted is False
    assert "HOLDOUT_SCENARIO" in holdout.reasons

    empty = ScenarioSets(
        partition_rule="by fault mechanism and task structure",
        dev_scenarios=(),
    )
    unregistered = build_admission(
        LEGACY_CASE, _certified(LEGACY_CASE), project_root=tmp_path, scenario_sets=empty
    )
    assert unregistered.admitted is False
    assert "UNREGISTERED_SCENARIO" in unregistered.reasons


def test_admission_rejects_certification_from_another_scenario(tmp_path: Path) -> None:
    """Audit regression: a certificate from another scenario can never admit
    this one, and the admission binds the scenario contract digest it was
    computed against."""

    _copy_management_config(tmp_path, (CASE_A, CASE_B))
    wrong_certificate = _certified(CASE_B)
    admission = build_admission(CASE_A, wrong_certificate, project_root=tmp_path)

    assert admission.admitted is False
    assert "CERTIFICATION_CASE_MISMATCH" in admission.reasons
    assert admission.certification.case_id == CASE_B
    assert admission.scenario_digest == load_scenario_spec(CASE_A, tmp_path).digest()


def test_admission_rejects_certificate_for_a_changed_contract(tmp_path: Path) -> None:
    """Audit regression: a certificate is bound to the contract digest it was
    produced against. Adding a required evidence type to the same case (without
    re-certifying) must make the old certificate stale, never admitted."""

    _copy_management_config(tmp_path, (CASE_A, CASE_B))
    certificate = _certified(CASE_A)
    assert certificate.scenario_digest == load_scenario_spec(CASE_A, tmp_path).digest()

    path = tmp_path / "config" / "scenarios" / f"{CASE_A}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["required_evidence_types"] = [
        *payload["required_evidence_types"],
        "RELATION_HISTORY",
    ]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    changed = load_scenario_spec(CASE_A, tmp_path)
    assert changed.digest() != certificate.scenario_digest

    admission = build_admission(CASE_A, certificate, project_root=tmp_path)

    assert admission.admitted is False
    assert admission.reasons == ("CERTIFICATION_DIGEST_MISMATCH",)
    assert admission.scenario_digest == changed.digest()


def test_admission_requires_a_certificate_for_the_current_digest() -> None:
    """A mismatched digest cannot be admitted even if the payload claims it was,
    and a certificate that carries no digest cannot be parsed at all."""

    admission = build_admission(CASE_A, _certified(CASE_A))

    stale_payload = admission.model_dump(mode="json")
    stale_payload["certification"]["scenario_digest"] = "0" * 64
    with pytest.raises(ValidationError, match="current contract digest"):
        ScenarioAdmission.model_validate(stale_payload)

    digestless_payload = admission.model_dump(mode="json")
    del digestless_payload["certification"]["scenario_digest"]
    with pytest.raises(ValidationError):
        ScenarioAdmission.model_validate(digestless_payload)


def test_symmetry_violation_blocks_admission(tmp_path: Path) -> None:
    _copy_management_config(tmp_path, (CASE_A, CASE_B))
    partner_path = tmp_path / "config" / "scenarios" / f"{CASE_B}.json"
    payload = json.loads(partner_path.read_text(encoding="utf-8"))
    payload["incident_brief"]["summary"] = "A different surface alert."
    partner_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    admission = build_admission(CASE_A, _certified(CASE_A), project_root=tmp_path)

    assert admission.admitted is False
    assert "SYMMETRY_MISMATCH" in admission.reasons
    surface = next(
        item for item in admission.symmetry_findings if item.code == "AB_PAIR_SURFACE_MISMATCH"
    )
    assert surface.satisfied is False


def test_admission_report_round_trip(tmp_path: Path) -> None:
    report = ScenarioAdmissionReport(
        created_at=CREATED_AT,
        entries=(
            build_admission(CASE_A, _certified(CASE_A)),
            build_admission(CASE_A, _uncertified(CASE_A)),
        ),
    )

    path = write_admission_report(report, tmp_path / "admissions" / "all.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    restored = ScenarioAdmissionReport.model_validate(payload)

    assert restored == report
    assert restored.admitted_count == 1
    assert restored.rejected_case_ids == (CASE_A,)


def test_admission_model_rejects_contradictory_verdicts() -> None:
    admission = build_admission(CASE_A, _certified(CASE_A))
    payload = admission.model_dump(mode="json")
    payload["admitted"] = False
    payload["reasons"] = ()

    with pytest.raises(ValidationError):
        ScenarioAdmission.model_validate(payload)


def test_certify_admit_writes_admission_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _copy_management_config(tmp_path, (CASE_A, CASE_B))
    report = CatalogCertificationReport(
        created_at=CREATED_AT,
        entries=(_certified(CASE_A),),
    )

    async def _stub_catalog(case_ids: object, *, project_root: Path) -> CatalogCertificationReport:
        return report

    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cli, "certify_catalog", _stub_catalog)

    result = runner.invoke(app, ["certify", "--case", CASE_A, "--admit"])

    assert result.exit_code == 0
    assert "admitted: 1/1" in result.stdout
    assert "[准入]" in result.stdout
    report_path = tmp_path / "artifacts" / "admissions" / f"{CASE_A}.json"
    assert report_path.is_file()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "p1.scenario_admission.v1"
    assert payload["entries"][0]["admitted"] is True
    assert payload["entries"][0]["card"]["budget"] == (
        "8 model requests / 8 tool calls / 2 retries / 300s"
    )


def test_certify_admit_fails_when_a_case_is_not_admitted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _copy_management_config(tmp_path, (CASE_A, CASE_B))
    report = CatalogCertificationReport(
        created_at=CREATED_AT,
        entries=(_uncertified(CASE_A),),
    )

    async def _stub_catalog(case_ids: object, *, project_root: Path) -> CatalogCertificationReport:
        return report

    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cli, "certify_catalog", _stub_catalog)

    result = runner.invoke(app, ["certify", "--case", CASE_A, "--admit"])

    assert result.exit_code == 1
    assert "admitted: 0/1" in result.stdout
    assert "CERTIFICATION_FAILED" in result.stdout


def test_certify_admit_respects_overwrite_semantics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _copy_management_config(tmp_path, (CASE_A, CASE_B))
    target = tmp_path / "artifacts" / "admissions" / f"{CASE_A}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")
    report = CatalogCertificationReport(
        created_at=CREATED_AT,
        entries=(_certified(CASE_A),),
    )

    async def _stub_catalog(case_ids: object, *, project_root: Path) -> CatalogCertificationReport:
        return report

    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cli, "certify_catalog", _stub_catalog)

    result = runner.invoke(app, ["certify", "--case", CASE_A, "--admit"])

    assert result.exit_code == 1
    assert "准入报告已存在" in result.stderr

    overwritten = runner.invoke(app, ["certify", "--case", CASE_A, "--admit", "--overwrite"])

    assert overwritten.exit_code == 0


def test_registry_membership_covers_every_catalog_scenario() -> None:
    sets = load_scenario_sets()
    for case_id in SUPPORTED_SCENARIO_IDS:
        assert sets.set_for(case_id) in {"dev", "holdout"}
