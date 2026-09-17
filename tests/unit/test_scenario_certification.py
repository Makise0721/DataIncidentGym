from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.diagnosis import ToolTraceEvent
from data_incident_gym.evaluation_runner import EvaluationWorkflowError
from data_incident_gym.scenario_certification import (
    CatalogCertificationReport,
    CertificationFinding,
    ReferenceRunSummary,
    ScenarioCertification,
    _build_findings,
    _classify_failure,
    _receipt_proved,
    certify_scenario,
    write_certification_report,
)
from data_incident_gym.scenarios import ScenarioSpec, load_scenario_spec

CASE_ID = "required_null_order_customer_b"
RUN_ID = "c" * 32
CREATED_AT = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
FINGERPRINT = "f" * 64


def _scenario(case_id: str = CASE_ID) -> ScenarioSpec:
    return load_scenario_spec(case_id)


def _trace_event(
    tool_name: str,
    arguments: dict[str, str],
    error_code: str | None,
) -> ToolTraceEvent:
    return ToolTraceEvent(
        event_type="TOOL_CALL",
        tool_name=tool_name,
        arguments=arguments,
        fingerprint="a" * 64,
        evidence_ids=(),
        error_code=error_code,
        elapsed_ms=1,
    )


def _run_summary(
    *,
    evaluation_status: str = "PASSED",
    diagnosis_status: str = "INSUFFICIENT_EVIDENCE",
    tool_calls: int = 4,
    tool_error_codes: tuple[str, ...] = ("RELATION_NOT_ALLOWED",),
) -> ReferenceRunSummary:
    return ReferenceRunSummary(
        run_id=RUN_ID,
        evaluation_status=evaluation_status,
        diagnosis_status=diagnosis_status,
        root_cause_code=None,
        affected_assets=(),
        tool_calls=tool_calls,
        successful_tool_calls=max(0, tool_calls - len(tool_error_codes)),
        tool_error_codes=tool_error_codes,
        failed_check_codes=(),
    )


def _expected_gaps(scenario: ScenarioSpec) -> set[tuple[str, str, str]]:
    return {
        (gap.gap_kind, gap.subject, gap.reason_code)
        for gap in scenario.observable_evidence_contract.unresolved_gaps
    }


def test_receipt_proof_requires_matching_refusal_event() -> None:
    scenario = _scenario()

    proven = _receipt_proved(
        scenario,
        (
            _trace_event(
                "get_relation_data_profile",
                {"relation_name": "raw_orders"},
                "RELATION_NOT_ALLOWED",
            ),
        ),
    )
    assert proven is True

    wrong_subject = _receipt_proved(
        scenario,
        (
            _trace_event(
                "get_relation_data_profile",
                {"relation_name": "raw_customers"},
                "RELATION_NOT_ALLOWED",
            ),
        ),
    )
    assert wrong_subject is False

    missing = _receipt_proved(scenario, ())
    assert missing is False


def test_findings_are_satisfied_for_a_contract_matching_run() -> None:
    scenario = _scenario()
    findings, flags = _build_findings(
        scenario=scenario,
        evaluation_status="PASSED",
        diagnosis_status=scenario.expected_status,
        root_cause_code=None,
        affected_assets=(),
        gap_keys=_expected_gaps(scenario),
        collected_evidence_types=set(scenario.required_evidence_types),
        cited_evidence_types=set(scenario.required_evidence_types),
        trace_events=(
            _trace_event(
                "get_relation_data_profile",
                {"relation_name": "raw_orders"},
                "RELATION_NOT_ALLOWED",
            ),
        ),
        tool_calls=4,
    )

    assert all(item.satisfied for item in findings)
    by_code = {item.code: item for item in findings}
    assert by_code["REQUIRED_EVIDENCE_TYPES_COLLECTED"].satisfied is True
    assert by_code["REQUIRED_EVIDENCE_TYPES_CITED"].satisfied is True
    assert flags["evaluation_passed"] is True
    assert _classify_failure(
        scenario=scenario,
        run=_run_summary(),
        expected_root_ok=flags["root_ok"],
        assets_ok=flags["assets_ok"],
        gaps_ok=flags["gaps_ok"],
        status_ok=flags["status_ok"],
        receipts_ok=flags["receipts_ok"],
        types_ok=flags["types_ok"],
        cited_types_ok=flags["cited_types_ok"],
        evaluation_passed=flags["evaluation_passed"],
    ) == ()


def test_failure_classification_separates_scoring_from_reference() -> None:
    scenario = _scenario()
    _, flags = _build_findings(
        scenario=scenario,
        evaluation_status="PASSED",
        diagnosis_status=scenario.expected_status,
        root_cause_code=None,
        affected_assets=(),
        gap_keys=_expected_gaps(scenario),
        collected_evidence_types=set(scenario.required_evidence_types),
        cited_evidence_types=set(scenario.required_evidence_types),
        trace_events=(
            _trace_event(
                "get_relation_data_profile",
                {"relation_name": "raw_orders"},
                "RELATION_NOT_ALLOWED",
            ),
        ),
        tool_calls=4,
    )
    scoring_classes = _classify_failure(
        scenario=scenario,
        run=_run_summary(evaluation_status="FAILED"),
        expected_root_ok=flags["root_ok"],
        assets_ok=flags["assets_ok"],
        gaps_ok=flags["gaps_ok"],
        status_ok=flags["status_ok"],
        receipts_ok=flags["receipts_ok"],
        types_ok=flags["types_ok"],
        cited_types_ok=flags["cited_types_ok"],
        evaluation_passed=False,
    )
    assert scoring_classes == ("SCORING",)

    _, mismatched = _build_findings(
        scenario=scenario,
        evaluation_status="FAILED",
        diagnosis_status="CONFIRMED",
        root_cause_code="SOURCE_REQUIRED_FIELD_NULL",
        affected_assets=("model.jaffle_shop.orders",),
        gap_keys=set(),
        collected_evidence_types=set(scenario.required_evidence_types),
        cited_evidence_types=set(scenario.required_evidence_types),
        trace_events=(),
        tool_calls=8,
    )
    reference_classes = _classify_failure(
        scenario=scenario,
        run=_run_summary(
            evaluation_status="FAILED",
            diagnosis_status="CONFIRMED",
            tool_calls=8,
            tool_error_codes=(),
        ),
        expected_root_ok=mismatched["root_ok"],
        assets_ok=mismatched["assets_ok"],
        gaps_ok=mismatched["gaps_ok"],
        status_ok=mismatched["status_ok"],
        receipts_ok=mismatched["receipts_ok"],
        types_ok=mismatched["types_ok"],
        cited_types_ok=mismatched["cited_types_ok"],
        evaluation_passed=False,
    )
    assert "REFERENCE_IMPLEMENTATION" in reference_classes
    assert "BUDGET" in reference_classes

    tool_classes = _classify_failure(
        scenario=scenario,
        run=_run_summary(evaluation_status="FAILED", tool_error_codes=("EVIDENCE_TOOL_ERROR",)),
        expected_root_ok=True,
        assets_ok=True,
        gaps_ok=True,
        status_ok=True,
        receipts_ok=True,
        types_ok=True,
        cited_types_ok=True,
        evaluation_passed=False,
    )
    assert "TOOL" in tool_classes


def test_missing_required_evidence_type_is_reference_not_scoring() -> None:
    """Audit regression: correct root cause and assets, but the reference never
    collected the required schema record. The evaluator was right to reject it,
    so the finding is a reference-implementation gap, not a scoring problem.
    """

    scenario = _scenario()
    collected = set(scenario.required_evidence_types) - {"RELATION_SCHEMA"}
    findings, flags = _build_findings(
        scenario=scenario,
        evaluation_status="FAILED",
        diagnosis_status=scenario.expected_status,
        root_cause_code=None,
        affected_assets=(),
        gap_keys=_expected_gaps(scenario),
        collected_evidence_types=collected,
        cited_evidence_types=collected,
        trace_events=(
            _trace_event(
                "get_relation_data_profile",
                {"relation_name": "raw_orders"},
                "RELATION_NOT_ALLOWED",
            ),
        ),
        tool_calls=6,
    )

    assert flags["status_ok"] is True
    assert flags["gaps_ok"] is True
    assert flags["types_ok"] is False
    assert flags["cited_types_ok"] is False
    by_code = {item.code: item for item in findings}
    assert by_code["REQUIRED_EVIDENCE_TYPES_COLLECTED"].satisfied is False
    assert "RELATION_SCHEMA" in (by_code["REQUIRED_EVIDENCE_TYPES_COLLECTED"].detail or "")

    classes = _classify_failure(
        scenario=scenario,
        run=_run_summary(evaluation_status="FAILED"),
        expected_root_ok=flags["root_ok"],
        assets_ok=flags["assets_ok"],
        gaps_ok=flags["gaps_ok"],
        status_ok=flags["status_ok"],
        receipts_ok=flags["receipts_ok"],
        types_ok=flags["types_ok"],
        cited_types_ok=flags["cited_types_ok"],
        evaluation_passed=False,
    )
    assert classes == ("REFERENCE_IMPLEMENTATION",)


def test_collected_but_uncited_evidence_is_reference_not_scoring() -> None:
    """Audit regression: the required schema record exists in the run inventory
    but the final diagnosis never cites it. The evaluator correctly fails
    REQUIRED_EVIDENCE_TYPES_PRESENT, so this is a reference-implementation
    finding (collection does not satisfy the citation duty), never SCORING.
    """

    scenario = _scenario()
    required = set(scenario.required_evidence_types)
    findings, flags = _build_findings(
        scenario=scenario,
        evaluation_status="FAILED",
        diagnosis_status=scenario.expected_status,
        root_cause_code=None,
        affected_assets=(),
        gap_keys=_expected_gaps(scenario),
        collected_evidence_types=required,
        cited_evidence_types=required - {"RELATION_SCHEMA"},
        trace_events=(
            _trace_event(
                "get_relation_data_profile",
                {"relation_name": "raw_orders"},
                "RELATION_NOT_ALLOWED",
            ),
        ),
        tool_calls=6,
    )

    assert flags["types_ok"] is True
    assert flags["cited_types_ok"] is False
    by_code = {item.code: item for item in findings}
    assert by_code["REQUIRED_EVIDENCE_TYPES_COLLECTED"].satisfied is True
    assert by_code["REQUIRED_EVIDENCE_TYPES_CITED"].satisfied is False
    assert "RELATION_SCHEMA" in (by_code["REQUIRED_EVIDENCE_TYPES_CITED"].detail or "")

    classes = _classify_failure(
        scenario=scenario,
        run=_run_summary(evaluation_status="FAILED"),
        expected_root_ok=flags["root_ok"],
        assets_ok=flags["assets_ok"],
        gaps_ok=flags["gaps_ok"],
        status_ok=flags["status_ok"],
        receipts_ok=flags["receipts_ok"],
        types_ok=flags["types_ok"],
        cited_types_ok=flags["cited_types_ok"],
        evaluation_passed=False,
    )
    assert classes == ("REFERENCE_IMPLEMENTATION",)


def test_certification_model_rejects_inconsistent_verdicts() -> None:
    finding = CertificationFinding(code="EVALUATION_PASSED", satisfied=False)
    digest = _scenario().digest()

    with pytest.raises(ValidationError):
        ScenarioCertification(
            case_id=CASE_ID,
            scenario_digest=digest,
            certified=True,
            findings=(finding,),
        )
    with pytest.raises(ValidationError):
        ScenarioCertification(
            case_id=CASE_ID,
            scenario_digest=digest,
            certified=True,
            findings=(CertificationFinding(code="EVALUATION_PASSED", satisfied=True),),
            failure_classes=("SCORING",),
        )


def test_certification_cannot_exist_without_a_contract_digest() -> None:
    """The digest of the certified contract is part of the verdict: a
    certificate payload without one (or with a malformed one) is unloadable, so
    it can never reach the admission gate."""

    payload = ScenarioCertification(
        case_id=CASE_ID,
        scenario_digest=_scenario().digest(),
        certified=True,
        findings=(CertificationFinding(code="EVALUATION_PASSED", satisfied=True),),
        run=_run_summary(),
    ).model_dump(mode="json")

    assert payload["scenario_digest"] == _scenario().digest()

    del payload["scenario_digest"]
    with pytest.raises(ValidationError):
        ScenarioCertification.model_validate(payload)

    payload["scenario_digest"] = "not-a-digest"
    with pytest.raises(ValidationError):
        ScenarioCertification.model_validate(payload)


def test_certify_scenario_binds_the_loaded_contract_digest() -> None:
    """The certificate produced by the pipeline — pass or fail — records the
    digest of the contract that was actually loaded and certified."""

    class _FailingRunner:
        async def run(self, case_id: str, strategy: object) -> object:
            raise EvaluationWorkflowError("LAB_SETUP_FAILED")

    certificate = asyncio.run(
        certify_scenario(CASE_ID, project_root=PROJECT_ROOT, runner=_FailingRunner())  # type: ignore[arg-type]
    )

    assert certificate.certified is False
    assert certificate.failure_classes == ("ENVIRONMENT",)
    assert certificate.scenario_digest == load_scenario_spec(CASE_ID).digest()


def test_certification_report_round_trip(tmp_path: Path) -> None:
    report = CatalogCertificationReport(
        created_at=CREATED_AT,
        entries=(
            ScenarioCertification(
                case_id=CASE_ID,
                scenario_digest=_scenario().digest(),
                certified=False,
                findings=(
                    CertificationFinding(
                        code="EVALUATION_PASSED",
                        satisfied=False,
                        detail="evaluator rejected the reference run",
                    ),
                ),
                failure_classes=("SCORING",),
                run=_run_summary(evaluation_status="FAILED"),
            ),
        ),
    )

    path = write_certification_report(report, tmp_path / "certifications" / "catalog.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    restored = CatalogCertificationReport.model_validate(payload)

    assert restored == report
    assert restored.certified_count == 0
    assert restored.uncertified_case_ids == (CASE_ID,)
