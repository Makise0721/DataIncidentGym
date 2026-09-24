from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_incident_gym.benchmark_manifest import build_manifest
from data_incident_gym.diagnosis import (
    DiagnosisMetrics,
    DiagnosticStrategy,
    ModelProtocolTraceEvent,
    PolicyIdentity,
)
from data_incident_gym.quality_baseline import (
    Axis1Result,
    Axis2ClaimVerdict,
    Axis2Result,
    Axis3Result,
    CellAnalysis,
    QualityBaselineError,
    _axis1,
    _axis3,
    _load_bundle_for_analysis,
    _load_terminal_cells,
    _status_direction,
    axis1_defect_types,
    axis3_defect_counts,
    overlap_matrix,
    render_markdown,
    verify_scoring_identity,
)

_RUN = "a" * 32
_DIG = "b" * 64


def test_axis1_separates_not_collected_from_collected_uncited() -> None:
    result = _axis1(("a", "b", "c"), collected={"a", "b", "x"}, cited={"a"})

    assert result.not_collected == ("c",)
    assert result.collected_uncited == ("b",)
    assert result.has_defect is True


def test_axis1_all_required_cited_has_no_defect() -> None:
    result = _axis1(("a", "b"), collected={"a", "b"}, cited={"a", "b"})

    assert result.has_defect is False


def test_axis1_without_required_types_is_not_applicable() -> None:
    result = _axis1((), collected=set(), cited=set())

    assert result.applicable is False
    assert result.has_defect is False


def test_axis1_defect_type_histogram() -> None:
    cells = [
        CellAnalysis(
            batch="b",
            sequence=1,
            run_id="r1",
            incident_case_id="c1",
            strategy="s",
            category="QUALITY_FAILED",
            axis1=Axis1Result(
                required=("a", "b"), not_collected=("a",), collected_uncited=("b",)
            ),
        ),
        CellAnalysis(
            batch="b",
            sequence=2,
            run_id="r2",
            incident_case_id="c2",
            strategy="s",
            category="QUALITY_FAILED",
            axis1=Axis1Result(required=("a",), not_collected=("a",), collected_uncited=()),
        ),
        CellAnalysis(
            batch="b",
            sequence=3,
            run_id="r3",
            incident_case_id="c3",
            strategy="s",
            category="PASSED",
            axis1=None,
        ),
    ]

    assert axis1_defect_types(cells) == {"a": 2, "b": 1}


@pytest.mark.parametrize(
    "expected, actual, direction",
    [
        ("CONFIRMED", "INSUFFICIENT_EVIDENCE", "wrong_abstention"),
        ("NO_INCIDENT", "INSUFFICIENT_EVIDENCE", "wrong_abstention"),
        ("INSUFFICIENT_EVIDENCE", "CONFIRMED", "should_abstain_but_confirmed"),
        ("CONFIRMED", "NO_INCIDENT", "other_status_error"),
    ],
)
def test_status_direction_mapping(expected: str, actual: str, direction: str) -> None:
    assert _status_direction(expected, actual) == direction


def _gap(gap_kind: str, subject: str, reason_code: str, tool_name: str | None = None):
    return SimpleNamespace(
        gap_kind=gap_kind,
        subject=subject,
        reason_code=reason_code,
        tool_name=tool_name,
    )


def _declared(evidence_kind: str, subject: str, reason_code: str):
    return SimpleNamespace(
        evidence_kind=evidence_kind, subject=subject, reason_code=reason_code
    )


def _axis3_scenario_and_run(expected, actual, trace=()):
    scenario = SimpleNamespace(
        observable_evidence_contract=SimpleNamespace(unresolved_gaps=expected)
    )
    diagnosis_run = SimpleNamespace(
        schema_version="p1.diagnosis.v1",
        diagnosis=SimpleNamespace(unresolved_evidence=actual),
        trace=trace,
    )
    return scenario, diagnosis_run


def test_axis3_records_missing_and_extra_sets_independently() -> None:
    expected = [_gap("RELATION_HISTORY", "raw_orders", "NOT_ALLOWED", tool_name=None)]
    actual = [_declared("TRANSFORMATION_DEFINITION", "stg_orders", "NOT_DECLARED")]
    scenario, diagnosis_run = _axis3_scenario_and_run(expected, actual)

    result = _axis3(scenario, diagnosis_run)

    assert result.missing == (("RELATION_HISTORY", "raw_orders", "NOT_ALLOWED"),)
    assert result.extra == (("TRANSFORMATION_DEFINITION", "stg_orders", "NOT_DECLARED"),)
    assert result.has_defect is True


def test_axis3_matching_sets_with_no_gaps_is_not_applicable() -> None:
    scenario, diagnosis_run = _axis3_scenario_and_run([], [])

    result = _axis3(scenario, diagnosis_run)

    assert result.applicable is False
    assert result.has_defect is False


def test_axis3_tool_gap_without_witnessed_receipt_is_a_defect() -> None:
    expected = [
        _gap("RELATION_HISTORY", "raw_orders", "NOT_ALLOWED", tool_name="get_relation_history")
    ]
    scenario, diagnosis_run = _axis3_scenario_and_run(expected, [])

    result = _axis3(scenario, diagnosis_run)

    assert result.unwitnessed_receipts == (("RELATION_HISTORY", "raw_orders", "NOT_ALLOWED"),)
    assert result.has_defect is True


def test_axis3_defect_counts_roll_up_per_gap() -> None:
    cells = [
        CellAnalysis(
            batch="b",
            sequence=1,
            run_id="r1",
            incident_case_id="c1",
            strategy="s",
            category="QUALITY_FAILED",
            axis3=Axis3Result(
                expected=(("k", "s", "r"),),
                missing=(("k", "s", "r"),),
                extra=(("k2", "s2", "r2"),),
                unwitnessed_receipts=(("k", "s", "r"),),
            ),
        ),
        CellAnalysis(
            batch="b",
            sequence=2,
            run_id="r2",
            incident_case_id="c2",
            strategy="s",
            category="PASSED",
            axis3=None,
        ),
    ]

    assert axis3_defect_counts(cells) == {
        "cells_with_defect": 1,
        "missing_gaps": 1,
        "extra_gaps": 1,
        "unwitnessed_receipts": 1,
    }


def test_overlap_matrix_counts_per_run_and_excludes_non_quality_cells() -> None:
    def cell(seq: int, category: str, a1: bool, a2: bool, a3: bool) -> CellAnalysis:
        axis1 = Axis1Result(
            required=("t",),
            not_collected=("t",) if a1 else (),
            collected_uncited=(),
        )
        axis2 = Axis2Result(
            verdicts=(
                Axis2ClaimVerdict(
                    "ROOT_CAUSE", "v", "collected_insufficient" if a2 else "supported"
                ),
            )
        )
        axis3 = Axis3Result(
            expected=(("k", "s", "r"),) if a3 else (),
            missing=(("k", "s", "r"),) if a3 else (),
            extra=(),
            unwitnessed_receipts=(),
        )
        return CellAnalysis(
            batch="b",
            sequence=seq,
            run_id=f"r{seq}",
            incident_case_id=f"c{seq}",
            strategy="s",
            category=category,
            axis1=axis1,
            axis2=axis2,
            axis3=axis3,
        )

    cells = [
        cell(1, "QUALITY_FAILED", True, True, False),
        cell(2, "QUALITY_FAILED", True, True, False),
        cell(3, "QUALITY_FAILED", True, False, True),
        cell(4, "PASSED", True, True, True),
    ]

    assert overlap_matrix(cells) == {"110": 2, "101": 1}


def _write_ledger(project_root: Path, manifest_id: str, lines: list[str]) -> None:
    suite_dir = project_root / "artifacts" / "benchmarks" / manifest_id
    suite_dir.mkdir(parents=True, exist_ok=True)
    (suite_dir / "ledger.jsonl").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _entry(cell, state: str) -> str:
    return json.dumps(
        {
            "manifest_id": "p1-formal-v1",
            "sequence": cell.sequence,
            "run_id": cell.run_id,
            "incident_case_id": cell.incident_case_id,
            "strategy": cell.strategy.value,
            "state": state,
        }
    )


def test_classification_marks_corrupt_and_not_executed_cells(tmp_path: Path) -> None:
    manifest = build_manifest("a" * 40)
    cell_a, cell_b, cell_c = manifest.cells[0], manifest.cells[1], manifest.cells[2]
    _write_ledger(
        tmp_path,
        manifest.manifest_id,
        [
            _entry(cell_a, "STARTED"),
            _entry(cell_a, "COMPLETED"),
            _entry(cell_b, "STARTED"),
        ],
    )

    cells = _load_terminal_cells(manifest, tmp_path)
    by_run = {cell.run_id: cell for cell in cells}

    # P1-1: even a CORRUPT artifact set never becomes a business category;
    # the cell is excluded from all axes.
    assert by_run[cell_a.run_id].category == "CORRUPT"
    assert by_run[cell_b.run_id].category == "CORRUPT"
    assert "STARTED without a terminal" in (by_run[cell_b.run_id].detail or "")
    assert by_run[cell_c.run_id].category == "NOT_EXECUTED"


def test_duplicate_terminal_entry_is_rejected_not_double_counted(tmp_path: Path) -> None:
    manifest = build_manifest("a" * 40)
    cell_a = manifest.cells[0]
    lines = [
        _entry(cell_a, "STARTED"),
        _entry(cell_a, "COMPLETED"),
        _entry(cell_a, "COMPLETED"),
    ]
    _write_ledger(tmp_path, manifest.manifest_id, lines)

    with pytest.raises(QualityBaselineError, match="duplicate terminal"):
        _load_terminal_cells(manifest, tmp_path)


def test_terminal_without_started_is_rejected(tmp_path: Path) -> None:
    manifest = build_manifest("a" * 40)
    cell_a = manifest.cells[0]
    _write_ledger(tmp_path, manifest.manifest_id, [_entry(cell_a, "COMPLETED")])

    with pytest.raises(QualityBaselineError, match="without STARTED"):
        _load_terminal_cells(manifest, tmp_path)


def test_duplicate_started_entry_is_rejected(tmp_path: Path) -> None:
    manifest = build_manifest("a" * 40)
    cell_a = manifest.cells[0]
    _write_ledger(
        tmp_path,
        manifest.manifest_id,
        [_entry(cell_a, "STARTED"), _entry(cell_a, "STARTED")],
    )

    with pytest.raises(QualityBaselineError, match="duplicate STARTED"):
        _load_terminal_cells(manifest, tmp_path)


def test_verify_scoring_identity_passes_for_fresh_manifest_and_detects_drift() -> None:
    manifest = build_manifest("a" * 40)
    verify_scoring_identity(manifest, Path(__file__).resolve().parents[2])

    drifted = manifest.model_copy(
        update={
            "result_inputs": manifest.result_inputs.model_copy(
                update={"evaluator_sha256": "b" * 64}
            )
        }
    )
    with pytest.raises(QualityBaselineError, match="evaluator_sha256"):
        verify_scoring_identity(drifted, Path(__file__).resolve().parents[2])


def test_render_markdown_rows_correspond_to_structured_cells() -> None:
    cells = [
        CellAnalysis(
            batch="p1-formal-vX",
            sequence=10,
            run_id="r10",
            incident_case_id="case_ten",
            strategy="STATIC_SKILL",
            category="STATUS_ERROR",
            status_direction="wrong_abstention",
        ),
        CellAnalysis(
            batch="p1-formal-vX",
            sequence=14,
            run_id="r14",
            incident_case_id="case_fourteen",
            strategy="DIAGNOSTIC_KERNEL",
            category="QUALITY_FAILED",
            axis1=Axis1Result(
                required=("DBT_LINEAGE",),
                not_collected=("DBT_LINEAGE",),
                collected_uncited=(),
            ),
        ),
    ]

    rendered = render_markdown({"p1-formal-vX": cells})

    assert "| 10 | case_ten | STATIC_SKILL | STATUS_ERROR | wrong_abstention |" in rendered
    assert "DBT_LINEAGE" in rendered
    assert "case_fourteen" in rendered
    for cell in cells:
        assert f"| {cell.sequence} | {cell.incident_case_id} |" in rendered
    assert "case_twelve" not in rendered


# --- P1-3: pre-M23 bundle digest compatibility -------------------------------


def _policy_identity() -> PolicyIdentity:
    return PolicyIdentity(
        strategy=DiagnosticStrategy.STATIC_SKILL,
        base_prompt_version="p1.base.v1",
        base_prompt_sha256=_DIG,
        strategy_prompt_version="p1.static.v9",
        strategy_prompt_sha256=_DIG,
        controller_protocol_version="p1.controller.v1",
        controller_protocol_sha256=_DIG,
        tool_schema_sha256=_DIG,
    )


def _metrics() -> DiagnosisMetrics:
    return DiagnosisMetrics(
        provider="openai-compatible",
        model="deepseek/deepseek-v4.1-flash",
        model_requests=1,
        input_tokens=10,
        output_tokens=10,
        tool_call_attempts=0,
        successful_tool_calls=0,
        elapsed_ms=5,
    )


def _legacy_bundle_payload() -> tuple[dict, str]:
    """A bundle payload whose recorded digest predates the M23 field."""

    from data_incident_gym.evaluation_inputs import (
        RecoveryProof,
        VerificationPayload,
        _payload_digest,
        default_evaluator_identity,
    )
    from data_incident_gym.scenarios import load_scenario_spec

    scenario = load_scenario_spec("duplicate_payment_coupon_a")
    verification = VerificationPayload(
        status="EXPECTED_FAILURE",
        incident_case_id=scenario.incident_case_id,
        run_id=_RUN,
        dbt_exit_code=1,
        failed_nodes=("model.jaffle_shop.customers",),
        skipped_nodes=(),
        affected_assets=("raw_customers",),
        schema_fingerprint=_DIG,
        profile_spec_sha256=_DIG,
    )
    protocol_event = ModelProtocolTraceEvent(
        event_type="MODEL_PROTOCOL",
        stage="PROVIDER_RESPONSE",
        tool_name=None,
        category="PROVIDER_PROTOCOL_FAILURE",
        model_request_index=5,
        error_type="MODEL_API_ERROR",
        error_origin="PROVIDER",
        retry_prompt_targets=("<output>",),
    ).model_dump(mode="json")
    terminal_event = {
        "event_type": "DIAGNOSIS_TERMINAL",
        "strategy": "STATIC_SKILL",
        "status": "MODEL_ERROR",
        "evidence_inventory": [],
    }
    diagnosis_run = {
        "schema_version": "p1.diagnosis.v1",
        "strategy": "STATIC_SKILL",
        "policy_identity": _policy_identity().model_dump(mode="json"),
        "diagnosis": {
            "schema_version": "p1.diagnosis.v1",
            "status": "MODEL_ERROR",
            "run_id": _RUN,
            "summary": "MODEL_PROTOCOL_ERROR",
            "recommended_actions": ["Do not retry."],
            "confidence": 0.0,
            "affected_assets": [],
            "evidence_ids": [],
            "claims": [],
            "unresolved_evidence": [],
        },
        "evidence_records": [],
        "trace": [protocol_event, terminal_event],
        "metrics": _metrics().model_dump(mode="json"),
        "kernel_state": None,
    }

    # Simulate the pre-M23 writer: the recorded digest covers the payload with
    # the (then nonexistent) transport field absent from every trace event.
    def _strip_transport(obj):
        if isinstance(obj, dict):
            obj.pop("transport_diagnostic", None)
            for value in obj.values():
                _strip_transport(value)
        elif isinstance(obj, list):
            for value in obj:
                _strip_transport(value)

    _strip_transport(diagnosis_run)
    recorded_digest = hashlib.sha256(
        json.dumps(diagnosis_run, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    payload = {
        "schema_version": "p1.evaluation_inputs.v1",
        "run_id": _RUN,
        "incident_case_id": scenario.incident_case_id,
        "strategy": "STATIC_SKILL",
        "scenario": scenario.model_dump(mode="json"),
        "scenario_digest": scenario.digest(),
        "verification": verification.model_dump(mode="json"),
        "verification_digest": _payload_digest(verification),
        "diagnosis_run": diagnosis_run,
        "diagnosis_run_digest": recorded_digest,
        "recovery": RecoveryProof(
            source="LAB_RESTORE",
            incident_case_id=scenario.incident_case_id,
            state="HEALTHY",
        ).model_dump(mode="json"),
        "budget": {
            "model_request_limit": 8,
            "tool_call_limit": 8,
            "output_retry_limit": 2,
            "timeout_seconds": 300,
        },
        "original_evaluator": default_evaluator_identity().model_dump(mode="json"),
        "artifact_digests": [
            {"name": name, "sha256": _DIG}
            for name in (
                "metadata.json",
                "trace.jsonl",
                "evidence.json",
                "diagnosis.json",
                "evaluation.json",
                "report.md",
            )
        ],
    }
    return payload, recorded_digest


def test_legacy_bundle_loads_with_original_digest_preserved() -> None:
    payload, recorded_digest = _legacy_bundle_payload()

    bundle = _load_bundle_for_analysis(copy.deepcopy(payload))

    assert bundle.diagnosis_run_digest == recorded_digest
    protocol = [
        event
        for event in bundle.diagnosis_run.trace
        if getattr(event, "event_type", None) == "MODEL_PROTOCOL"
    ]
    assert len(protocol) == 1
    assert protocol[0].error_type == "MODEL_API_ERROR"
    assert protocol[0].transport_diagnostic is None


def test_legacy_bundle_detects_tampered_payload() -> None:
    payload, _ = _legacy_bundle_payload()
    tampered = copy.deepcopy(payload)
    tampered["diagnosis_run"]["diagnosis"]["confidence"] = 0.9

    with pytest.raises(QualityBaselineError, match="legacy diagnosis digest mismatch"):
        _load_bundle_for_analysis(tampered)


def test_new_field_payload_with_wrong_digest_still_raises() -> None:
    payload, _ = _legacy_bundle_payload()
    modern = copy.deepcopy(payload)
    for event in modern["diagnosis_run"]["trace"]:
        if event.get("event_type") == "MODEL_PROTOCOL":
            event["transport_diagnostic"] = "transport=HTTP_500"
    modern["diagnosis_run_digest"] = "c" * 64

    with pytest.raises(Exception, match="diagnosis digest does not match"):
        _load_bundle_for_analysis(modern)
