from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_incident_gym.benchmark_manifest import build_manifest
from data_incident_gym.quality_baseline import (
    Axis1Result,
    Axis2ClaimVerdict,
    Axis2Result,
    Axis3Result,
    CellAnalysis,
    QualityBaselineError,
    _axis1,
    _axis3,
    _load_terminal_cells,
    _status_direction,
    axis1_defect_types,
    axis3_defect_counts,
    overlap_matrix,
    verify_scoring_identity,
)


def test_axis1_separates_not_collected_from_collected_uncited() -> None:
    result = _axis1(("a", "b", "c"), collected={"a", "b", "x"}, cited={"a"})

    assert result.not_collected == ("c",)
    assert result.collected_uncited == ("b",)
    assert result.has_defect is True


def test_axis1_all_required_cited_has_no_defect() -> None:
    result = _axis1(("a", "b"), collected={"a", "b"}, cited={"a", "b"})

    assert result.not_collected == ()
    assert result.collected_uncited == ()
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
            axis1=Axis1Result(required=("a", "b"), not_collected=("a",), collected_uncited=("b",)),
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

    histogram = axis1_defect_types(cells)

    assert histogram == {"a": 2, "b": 1}


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

    assert result.missing == (("RELATION_HISTORY", "raw_orders", "NOT_ALLOWED"),)
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

    counts = axis3_defect_counts(cells)

    assert counts == {
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

    matrix = overlap_matrix(cells)

    assert matrix == {"110": 2, "101": 1}


def _write_ledger(project_root: Path, manifest_id: str, lines: list[str]) -> None:
    suite_dir = project_root / "artifacts" / "benchmarks" / manifest_id
    suite_dir.mkdir(parents=True, exist_ok=True)
    (suite_dir / "ledger.jsonl").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def test_classification_marks_corrupt_and_not_executed_cells(tmp_path: Path) -> None:
    manifest = build_manifest("a" * 40)
    cell_a, cell_b, cell_c = manifest.cells[0], manifest.cells[1], manifest.cells[2]
    started_a = {
        "manifest_id": manifest.manifest_id,
        "sequence": cell_a.sequence,
        "run_id": cell_a.run_id,
        "incident_case_id": cell_a.incident_case_id,
        "strategy": cell_a.strategy.value,
        "state": "STARTED",
    }
    completed_a = {**started_a, "state": "COMPLETED", "reason_code": None}
    started_b = {**started_a, "sequence": cell_b.sequence, "run_id": cell_b.run_id}
    _write_ledger(
        tmp_path,
        manifest.manifest_id,
        [
            json.dumps(started_a),
            json.dumps(completed_a),
            json.dumps(started_b),
        ],
    )

    cells = _load_terminal_cells(manifest, tmp_path)
    by_run = {cell.run_id: cell for cell in cells}

    assert by_run[cell_a.run_id].category == "CORRUPT"
    assert by_run[cell_b.run_id].category == "CORRUPT"
    assert by_run[cell_b.run_id].detail is not None
    assert by_run[cell_c.run_id].category == "NOT_EXECUTED"


def test_verify_scoring_identity_passes_for_fresh_manifest_and_detects_drift() -> None:
    manifest = build_manifest("a" * 40)
    verify_scoring_identity(manifest, Path(".").resolve())

    drifted = manifest.model_copy(
        update={
            "result_inputs": manifest.result_inputs.model_copy(
                update={"evaluator_sha256": "b" * 64}
            )
        }
    )
    with pytest.raises(QualityBaselineError, match="evaluator_sha256"):
        verify_scoring_identity(drifted, Path(".").resolve())
