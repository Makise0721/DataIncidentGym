"""Offline diagnostic-quality baseline over archived benchmark cells (spec v2).

Read-only analysis per ``docs/superpowers/specs/2026-09-20-offline-diagnostic-
quality-baseline-spec.md``: verify scoring identity, recompute each archived
cell with the frozen evaluator, then classify cells and grade the three
quality axes (collection completeness, per-claim citation binding,
insufficiency-gap declaration). Private scenario contracts are used inside
this analyzer only; nothing here feeds model context, changes scores, or
rewrites artifacts.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from data_incident_gym.benchmark_manifest import (
    BenchmarkManifest,
    load_manifest,
    result_inputs_for_project,
)
from data_incident_gym.diagnosis import (
    DiagnosisRunResult,
    DiagnosisStatus,
    refusal_witnessed,
)
from data_incident_gym.evaluation import (
    DeterministicEvaluator,
    EvaluationResult,
    claim_support_verdicts,
    claim_supported_by_records,
)
from data_incident_gym.evaluation_inputs import (
    load_evaluation_input_bundle,
    verification_from_payload,
)


class QualityBaselineError(RuntimeError):
    """Raised when identity, integrity or recomputation verification fails."""


_TERMINAL_STATES = {"COMPLETED", "FAILED"}
_RECOVERY_HEALTHY = "HEALTHY"


@dataclass
class Axis1Result:
    required: tuple[str, ...]
    not_collected: tuple[str, ...]
    collected_uncited: tuple[str, ...]

    @property
    def applicable(self) -> bool:
        return bool(self.required)

    @property
    def has_defect(self) -> bool:
        return self.applicable and bool(self.not_collected or self.collected_uncited)


@dataclass
class Axis2ClaimVerdict:
    kind: str
    value: str
    outcome: str  # supported | citation_insufficient_collected_sufficient | collected_insufficient


@dataclass
class Axis2Result:
    verdicts: tuple[Axis2ClaimVerdict, ...]

    @property
    def applicable(self) -> bool:
        return any(v.outcome != "not_applicable" for v in self.verdicts)

    @property
    def has_defect(self) -> bool:
        return any(v.outcome != "supported" for v in self.verdicts if v.outcome != "not_applicable")


@dataclass
class Axis3Result:
    expected: tuple[tuple[str, str, str], ...]
    missing: tuple[tuple[str, str, str], ...]
    extra: tuple[tuple[str, str, str], ...]
    unwitnessed_receipts: tuple[tuple[str, str, str], ...]

    @property
    def applicable(self) -> bool:
        return bool(self.expected or self.missing or self.extra)

    @property
    def has_defect(self) -> bool:
        return self.applicable and bool(
            self.missing or self.extra or self.unwitnessed_receipts
        )


@dataclass
class CellAnalysis:
    batch: str
    sequence: int
    run_id: str
    incident_case_id: str
    strategy: str
    category: str  # PASSED | QUALITY_FAILED | STATUS_ERROR | RUN_ERROR | CORRUPT | NOT_EXECUTED
    status_direction: str | None = None
    axis1: Axis1Result | None = None
    axis2: Axis2Result | None = None
    axis3: Axis3Result | None = None
    detail: str | None = None


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_scoring_identity(manifest: BenchmarkManifest, project_root: Path) -> None:
    """All recorded scoring-dependency digests must match the current tree."""

    frozen = manifest.result_inputs
    current = result_inputs_for_project(project_root)
    if frozen != current:
        differences = [
            name
            for name in (
                "profile_spec_sha256",
                "scenario_spec_schema_sha256",
                "diagnosis_schema_sha256",
                "evaluator_version",
                "evaluator_sha256",
            )
            if getattr(frozen, name) != getattr(current, name)
        ]
        raise QualityBaselineError(
            f"scoring identity drifted for {manifest.manifest_id}: {differences}"
        )


def _verify_cell_binding(
    manifest: BenchmarkManifest,
    ledger_entry: dict[str, Any],
    metadata: dict[str, Any],
) -> None:
    if ledger_entry.get("manifest_id") != manifest.manifest_id:
        raise QualityBaselineError("ledger manifest_id does not match the batch manifest")
    cell = next(
        (item for item in manifest.cells if item.run_id == ledger_entry.get("run_id")),
        None,
    )
    if cell is None:
        raise QualityBaselineError(f"ledger run_id not in manifest: {ledger_entry.get('run_id')}")
    if (
        ledger_entry.get("sequence") != cell.sequence
        or ledger_entry.get("incident_case_id") != cell.incident_case_id
        or ledger_entry.get("strategy") != cell.strategy.value
    ):
        raise QualityBaselineError(f"ledger cell identity mismatch for run {cell.run_id}")
    if metadata.get("benchmark_manifest_sha256") != manifest.digest():
        raise QualityBaselineError(f"artifact manifest digest mismatch for run {cell.run_id}")
    if metadata.get("run_id") != cell.run_id:
        raise QualityBaselineError(f"artifact run_id mismatch for run {cell.run_id}")


def _status_direction(expected: str, actual: str) -> str:
    if expected == "INSUFFICIENT_EVIDENCE" and actual == "CONFIRMED":
        return "should_abstain_but_confirmed"
    if expected in {"CONFIRMED", "NO_INCIDENT"} and actual == "INSUFFICIENT_EVIDENCE":
        return "wrong_abstention"
    return "other_status_error"


def _axis1(
    required: tuple[str, ...],
    collected: set[str],
    cited: set[str],
) -> Axis1Result:
    not_collected = tuple(sorted(set(required) - collected))
    collected_uncited = tuple(sorted((set(required) & collected) - cited))
    return Axis1Result(
        required=tuple(sorted(required)),
        not_collected=not_collected,
        collected_uncited=collected_uncited,
    )


def _axis2(
    scenario: Any,
    diagnosis_run: DiagnosisRunResult,
) -> Axis2Result:
    verdicts: list[Axis2ClaimVerdict] = []
    for verdict in claim_support_verdicts(
        scenario, diagnosis_run.diagnosis, diagnosis_run.evidence_records
    ):
        if not verdict.applicable:
            outcome = "not_applicable"
        elif verdict.supported:
            outcome = "supported"
        else:
            claim = next(
                claim
                for claim in diagnosis_run.diagnosis.claims
                if claim.kind == verdict.kind and claim.evidence_ids == verdict.evidence_ids
            )
            pool_supported = claim_supported_by_records(
                scenario,
                claim,
                tuple(diagnosis_run.evidence_records),
                all_records=tuple(diagnosis_run.evidence_records),
            )
            outcome = (
                "citation_insufficient_collected_sufficient"
                if pool_supported
                else "collected_insufficient"
            )
        verdicts.append(Axis2ClaimVerdict(verdict.kind, verdict.value, outcome))
    return Axis2Result(verdicts=tuple(verdicts))


def _axis3(
    scenario: Any,
    diagnosis_run: DiagnosisRunResult,
) -> Axis3Result:
    contract_gaps = scenario.observable_evidence_contract.unresolved_gaps
    expected = tuple(
        sorted((gap.gap_kind, gap.subject, gap.reason_code) for gap in contract_gaps)
    )
    actual = tuple(
        sorted(
            (item.evidence_kind, item.subject, item.reason_code)
            for item in diagnosis_run.diagnosis.unresolved_evidence
        )
    )
    missing = tuple(sorted(set(expected) - set(actual)))
    extra = tuple(sorted(set(actual) - set(expected)))
    trace_events = tuple(
        event
        for event in diagnosis_run.trace
        if getattr(event, "event_type", None) == "TOOL_TRACE"
    )
    unwitnessed = tuple(
        sorted(
            (gap.gap_kind, gap.subject, gap.reason_code)
            for gap in contract_gaps
            if gap.tool_name is not None
            and not refusal_witnessed(
                trace_events,
                tool_name=gap.tool_name,
                target=gap.subject,
                code=gap.reason_code,
            )
            and (gap.gap_kind, gap.subject, gap.reason_code) in set(missing)
        )
    )
    return Axis3Result(
        expected=expected,
        missing=missing,
        extra=extra,
        unwitnessed_receipts=unwitnessed,
    )


def _lenient_transport_summary(project_root: Path, run_id: str) -> str:
    """Read provider-failure attribution from a scoring-inputs bundle leniently.

    MODEL_ERROR cells' bundles can fail strict schema validation (a known
    writer-side seam); this read is for attribution reporting only and never
    feeds the axis computation.
    """

    path = (
        project_root / ".dig" / "scoring-inputs" / run_id / "evaluation_inputs.json"
    )
    if not path.is_file():
        return "no scoring-inputs bundle archived"
    try:
        payload = _load_json(path)
    except ValueError as exc:
        return f"scoring-inputs bundle unreadable: {str(exc)[:80]}"
    trace = (payload.get("diagnosis_run") or {}).get("trace", [])
    events = [
        event
        for event in trace
        if isinstance(event, dict) and event.get("event_type") == "MODEL_PROTOCOL"
    ]
    if not events:
        return "no provider protocol failure recorded"
    return "; ".join(
        f"provider_failure(err={event.get('error_type')},"
        f"request_index={event.get('model_request_index')},"
        f"transport={event.get('transport_diagnostic')})"
        for event in events
    )


def _load_terminal_cells(
    manifest: BenchmarkManifest,
    project_root: Path,
) -> list[CellAnalysis]:
    suite_dir = project_root / "artifacts" / "benchmarks" / manifest.manifest_id
    ledger_path = suite_dir / "ledger.jsonl"
    if not ledger_path.is_file():
        raise QualityBaselineError(f"missing ledger for {manifest.manifest_id}")
    entries = [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    started = {entry["run_id"] for entry in entries if entry.get("state") == "STARTED"}
    terminal_ids = {entry["run_id"] for entry in entries if entry.get("state") in _TERMINAL_STATES}
    cells: list[CellAnalysis] = []
    for cell in manifest.cells:
        if cell.run_id not in started:
            cells.append(
                CellAnalysis(
                    batch=manifest.manifest_id,
                    sequence=cell.sequence,
                    run_id=cell.run_id,
                    incident_case_id=cell.incident_case_id,
                    strategy=cell.strategy.value,
                    category="NOT_EXECUTED",
                )
            )
        elif cell.run_id not in terminal_ids:
            cells.append(
                CellAnalysis(
                    batch=manifest.manifest_id,
                    sequence=cell.sequence,
                    run_id=cell.run_id,
                    incident_case_id=cell.incident_case_id,
                    strategy=cell.strategy.value,
                    category="CORRUPT",
                    detail="ledger has STARTED without a terminal entry",
                )
            )
    terminal = [entry for entry in entries if entry.get("state") in _TERMINAL_STATES]
    for entry in terminal:
        run_id = entry["run_id"]
        artifact_dir = project_root / "artifacts" / run_id
        try:
            metadata = _load_json(artifact_dir / "metadata.json")
            diagnosis_payload = _load_json(artifact_dir / "diagnosis.json")
            diagnosis_status = diagnosis_payload.get("status")
            archived = EvaluationResult.model_validate(
                _load_json(artifact_dir / "evaluation.json")
            )
            _verify_cell_binding(manifest, entry, metadata)
        except (OSError, ValueError, KeyError) as exc:
            cells.append(
                CellAnalysis(
                    batch=manifest.manifest_id,
                    sequence=entry["sequence"],
                    run_id=run_id,
                    incident_case_id=entry.get("incident_case_id", ""),
                    strategy=entry.get("strategy", ""),
                    category="CORRUPT",
                    detail=str(exc)[:200],
                )
            )
            continue
        base = dict(
            batch=manifest.manifest_id,
            sequence=entry["sequence"],
            run_id=run_id,
            incident_case_id=entry["incident_case_id"],
            strategy=entry["strategy"],
        )
        if diagnosis_status == DiagnosisStatus.MODEL_ERROR.value:
            # MODEL_ERROR cells predate or lack valid scoring-input bundles (a
            # known writer-side seam); their transport attribution is read
            # leniently from the raw bundle JSON instead.
            transport = _lenient_transport_summary(project_root, run_id)
            cells.append(CellAnalysis(**base, category="RUN_ERROR", detail=transport))
            continue
        try:
            bundle = load_evaluation_input_bundle(project_root, run_id)
            scenario_digest_matches = bundle.scenario_digest == _catalog_digest(
                manifest, entry["incident_case_id"]
            )
        except (OSError, ValueError, KeyError, QualityBaselineError) as exc:
            cells.append(
                CellAnalysis(
                    **base,
                    category="CORRUPT",
                    detail=str(exc)[:200],
                )
            )
            continue
        if not scenario_digest_matches:
            cells.append(
                CellAnalysis(
                    **base,
                    category="CORRUPT",
                    detail="scenario digest does not match the manifest catalog",
                )
            )
            continue
        recovery_succeeded = metadata.get("recovery_status") == _RECOVERY_HEALTHY
        recomputed = DeterministicEvaluator.evaluate(
            bundle.scenario,
            verification_from_payload(bundle.verification),
            bundle.diagnosis_run,
            recovery_succeeded=recovery_succeeded,
        )
        if recomputed != archived:
            raise QualityBaselineError(
                f"recomputation mismatch for {manifest.manifest_id} seq {entry['sequence']}"
                f" ({run_id}): recomputed {recomputed.model_dump_json()[:200]}..."
            )
        diagnosis = bundle.diagnosis_run.diagnosis
        expected_status = bundle.scenario.expected_status
        actual_status = diagnosis.status.value
        base = dict(
            batch=manifest.manifest_id,
            sequence=entry["sequence"],
            run_id=run_id,
            incident_case_id=entry["incident_case_id"],
            strategy=entry["strategy"],
        )
        if diagnosis.status is DiagnosisStatus.MODEL_ERROR:
            cells.append(CellAnalysis(**base, category="RUN_ERROR"))
            continue
        if recomputed.status.value == "PASSED":
            cells.append(CellAnalysis(**base, category="PASSED"))
            continue
        if actual_status != expected_status:
            cells.append(
                CellAnalysis(
                    **base,
                    category="STATUS_ERROR",
                    status_direction=_status_direction(expected_status, actual_status),
                )
            )
            continue
        inventory = {r.evidence_id: r for r in bundle.diagnosis_run.evidence_records}
        cited_ids = tuple(dict.fromkeys(diagnosis.evidence_ids))
        cited_types = {
            inventory[item].evidence_type.value for item in cited_ids if item in inventory
        }
        collected = {
            r.evidence_type.value for r in bundle.diagnosis_run.evidence_records
        }
        cells.append(
            CellAnalysis(
                **base,
                category="QUALITY_FAILED",
                axis1=_axis1(
                    tuple(bundle.scenario.required_evidence_types), collected, cited_types
                ),
                axis2=_axis2(bundle.scenario, bundle.diagnosis_run),
                axis3=(
                    _axis3(bundle.scenario, bundle.diagnosis_run)
                    if expected_status == "INSUFFICIENT_EVIDENCE"
                    and actual_status == "INSUFFICIENT_EVIDENCE"
                    else None
                ),
            )
        )
    cells.sort(key=lambda item: (item.batch, item.sequence))
    return cells


def _catalog_digest(manifest: BenchmarkManifest, incident_case_id: str) -> str:
    for entry in manifest.scenario_catalog:
        if entry.incident_case_id == incident_case_id:
            return entry.scenario_spec_sha256
    raise QualityBaselineError(f"case not in manifest catalog: {incident_case_id}")


def analyze_batch(manifest_path: Path, project_root: Path) -> list[CellAnalysis]:
    manifest = load_manifest(manifest_path)
    verify_scoring_identity(manifest, project_root)
    return _load_terminal_cells(manifest, project_root)


def axis1_defect_types(cells: list[CellAnalysis]) -> Counter:
    counter: Counter = Counter()
    for cell in cells:
        if cell.axis1 is not None and cell.axis1.has_defect:
            counter.update(cell.axis1.not_collected)
            counter.update(cell.axis1.collected_uncited)
    return counter


def axis2_defect_outcomes(cells: list[CellAnalysis]) -> Counter:
    counter: Counter = Counter()
    for cell in cells:
        if cell.axis2 is not None and cell.axis2.has_defect:
            counter.update(
                v.outcome
                for v in cell.axis2.verdicts
                if v.outcome not in {"supported", "not_applicable"}
            )
    return counter


def axis3_defect_counts(cells: list[CellAnalysis]) -> dict[str, int]:
    missing = extra = unwitnessed = cells_with_defect = 0
    for cell in cells:
        if cell.axis3 is not None and cell.axis3.has_defect:
            cells_with_defect += 1
            missing += len(cell.axis3.missing)
            extra += len(cell.axis3.extra)
            unwitnessed += len(cell.axis3.unwitnessed_receipts)
    return {
        "cells_with_defect": cells_with_defect,
        "missing_gaps": missing,
        "extra_gaps": extra,
        "unwitnessed_receipts": unwitnessed,
    }


def overlap_matrix(cells: list[CellAnalysis]) -> dict[str, int]:
    matrix: Counter = Counter()
    for cell in cells:
        if cell.category != "QUALITY_FAILED":
            continue
        flags = (
            cell.axis1 is not None and cell.axis1.has_defect,
            cell.axis2 is not None and cell.axis2.has_defect,
            cell.axis3 is not None and cell.axis3.has_defect,
        )
        matrix["".join("1" if flag else "0" for flag in flags)] += 1
    return dict(sorted(matrix.items()))


__all__ = [
    "Axis1Result",
    "Axis2ClaimVerdict",
    "Axis2Result",
    "Axis3Result",
    "CellAnalysis",
    "QualityBaselineError",
    "analyze_batch",
    "axis1_defect_types",
    "axis2_defect_outcomes",
    "axis3_defect_counts",
    "overlap_matrix",
    "verify_scoring_identity",
]
