"""Offline diagnostic-quality baseline over archived benchmark cells (spec v2).

Read-only analysis per ``docs/superpowers/specs/2026-09-20-offline-diagnostic-
quality-baseline-spec.md``: every terminal cell passes the same strict
identity, binding and frozen-evaluator recomputation checks before
classification; RUN_ERROR is a business category, never a verification
exemption. Private scenario contracts are used inside this analyzer only;
nothing here feeds model context, changes scores, or rewrites artifacts.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from data_incident_gym.benchmark_manifest import (
    BenchmarkManifest,
    load_manifest,
    result_inputs_for_project,
)
from data_incident_gym.diagnosis import (
    DiagnosisRunResult,
    DiagnosisRunResultAny,
    DiagnosisStatus,
    DiagnosticStrategy,
    refusal_witnessed,
)
from data_incident_gym.evaluation import (
    DeterministicEvaluator,
    EvaluationResult,
    claim_support_verdicts,
    claim_supported_by_records,
)
from data_incident_gym.evaluation_inputs import (
    ARTIFACT_FILENAMES,
    SCORING_INPUTS_SCHEMA_VERSION,
    ArtifactDigest,
    EvaluationInputBundle,
    EvaluationInputBundleAny,
    EvaluationInputBundleV2,
    EvaluationInputBundleV3,
    EvaluationInputsError,
    EvaluatorIdentity,
    RecoveryProof,
    VerificationPayload,
    _payload_digest,
    _restore_typed_diagnosis,
    _restore_typed_kernel_state,
    require_known_evaluator,
    verification_from_payload,
)
from data_incident_gym.scenarios import ScenarioSpec


class QualityBaselineError(RuntimeError):
    """Raised when identity, integrity or recomputation verification fails."""


_TERMINAL_STATES = {"COMPLETED", "FAILED"}
_RECOVERY_HEALTHY = "HEALTHY"
_TRANSPORT_FIELD = "transport_diagnostic"


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
        return any(
            v.outcome != "supported" for v in self.verdicts if v.outcome != "not_applicable"
        )


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
    transport: str | None = None
    detail: str | None = None


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _strip_transport_field(payload: Any) -> None:
    """Remove the M23-added default field from an in-memory payload copy."""

    if isinstance(payload, dict):
        payload.pop(_TRANSPORT_FIELD, None)
        for value in payload.values():
            _strip_transport_field(value)
    elif isinstance(payload, list):
        for value in payload:
            _strip_transport_field(value)


def _payload_is_legacy(payload: dict[str, Any]) -> bool:
    """A payload is legacy when no trace event carries the M23-added field."""

    trace = (payload.get("diagnosis_run") or {}).get("trace", [])
    return not any(
        isinstance(event, dict) and _TRANSPORT_FIELD in event
        for event in trace
        if isinstance(event, dict)
    )


def _load_bundle_for_analysis(payload: dict[str, Any]) -> EvaluationInputBundleAny:
    """Strictly validate a bundle, with tested pre-M23 digest compatibility.

    Legacy payloads (trace events without ``transport_diagnostic``) record
    their diagnosis digest over a serialization that predates the field: the
    digest is canonical JSON of the run with the default field absent, while a
    re-serialized parsed model now always carries
    ``"transport_diagnostic": null``. For such payloads the diagnosis digest
    is recomputed over the field-stripped serialization — the exact reverse of
    what re-serialization added — and compared against the ORIGINAL recorded
    digest, which is preserved on the returned instance. The full loader flow
    (v2 diagnosis restoration, kernel-state typing, digest stability across
    restoration) is replicated; genuine mismatches and new-field payloads
    still raise; old bundles are never modified.
    """

    restored = _restore_typed_diagnosis(copy.deepcopy(payload))
    schema_version = restored.get("schema_version")
    if schema_version == "p1.evaluation_inputs.v1":
        bundle_model = EvaluationInputBundle
    elif schema_version == "p1.evaluation_inputs.v2":
        bundle_model = EvaluationInputBundleV2
    elif schema_version == "p1.evaluation_inputs.v3":
        bundle_model = EvaluationInputBundleV3
    else:
        raise QualityBaselineError("unsupported scoring-input bundle schema")
    legacy = False
    try:
        bundle = bundle_model.model_validate(restored)
    except ValidationError as exc:
        if bundle_model is not EvaluationInputBundle or (
            "diagnosis digest does not match" not in str(exc)
        ):
            raise
        if not _payload_is_legacy(restored):
            raise
        # Legacy path: re-validate the diagnosis run from a serialization with
        # the M23 default field stripped, and recompute the digest exactly the
        # way the pre-M23 writer did.
        legacy = True
        run_payload = copy.deepcopy(restored["diagnosis_run"])
        _strip_transport_field(run_payload)
        diagnosis_run = DiagnosisRunResult.model_validate(run_payload)
        legacy_digest = hashlib.sha256(
            _canonical_json(run_payload).encode("utf-8")
        ).hexdigest()
        if legacy_digest != restored["diagnosis_run_digest"]:
            raise QualityBaselineError(
                "legacy diagnosis digest mismatch after transport-field normalization"
            ) from None
    else:
        diagnosis_run = bundle.diagnosis_run

    scenario = ScenarioSpec.model_validate(restored["scenario"])
    verification = VerificationPayload.model_validate(restored["verification"])
    recovery = RecoveryProof.model_validate(restored["recovery"])
    evaluator = EvaluatorIdentity.model_validate(restored["original_evaluator"])
    require_known_evaluator(evaluator)
    artifact_digests = tuple(
        ArtifactDigest.model_validate(item) for item in restored["artifact_digests"]
    )
    bundle = bundle_model.model_construct(
        schema_version=restored.get(
            "schema_version", SCORING_INPUTS_SCHEMA_VERSION
        ),
        run_id=restored["run_id"],
        incident_case_id=restored["incident_case_id"],
        strategy=DiagnosticStrategy(restored["strategy"]),
        scenario=scenario,
        scenario_digest=restored["scenario_digest"],
        verification=verification,
        verification_digest=restored["verification_digest"],
        diagnosis_run=diagnosis_run,
        # The ORIGINAL recorded digest is kept as evidence on the instance; the
        # legacy-computed value above was only used to verify it.
        diagnosis_run_digest=restored["diagnosis_run_digest"],
        recovery=recovery,
        original_evaluator=evaluator,
        artifact_digests=artifact_digests,
    )
    if bundle.diagnosis_run.diagnosis.run_id != bundle.run_id:
        raise QualityBaselineError("bundle run_id does not match the diagnosis run")
    if bundle.diagnosis_run.strategy is not bundle.strategy:
        raise QualityBaselineError("bundle strategy does not match the diagnosis run")
    if bundle.verification.run_id != bundle.run_id:
        raise QualityBaselineError("bundle verification run_id mismatch")
    if bundle.verification.incident_case_id != bundle.incident_case_id:
        raise QualityBaselineError("bundle verification case mismatch")
    if bundle.scenario.incident_case_id != bundle.incident_case_id:
        raise QualityBaselineError("bundle scenario case mismatch")
    if bundle.recovery.incident_case_id != bundle.incident_case_id:
        raise QualityBaselineError("bundle recovery case mismatch")
    if bundle.scenario_digest != bundle.scenario.digest():
        raise QualityBaselineError("bundle scenario digest mismatch")
    if bundle.verification_digest != _payload_digest(bundle.verification):
        raise QualityBaselineError("bundle verification digest mismatch")
    names = tuple(item.name for item in bundle.artifact_digests)
    if len(names) != len(set(names)) or set(names) != set(ARTIFACT_FILENAMES):
        raise QualityBaselineError("bundle artifact digests must cover the six files")
    restored_run = _restore_typed_kernel_state(diagnosis_run)
    if restored_run is not diagnosis_run:
        # Same invariant the strict loader enforces: kernel-state typing must
        # not change the recorded digest (legacy mode compares the stripped
        # serialization, which is what the recorded digest was computed over).
        if legacy:
            check_payload = restored_run.model_dump(mode="json")
            _strip_transport_field(check_payload)
            restored_digest = hashlib.sha256(
                _canonical_json(check_payload).encode("utf-8")
            ).hexdigest()
        else:
            restored_digest = restored_run.digest()
        if restored_digest != restored["diagnosis_run_digest"]:
            raise QualityBaselineError(
                "diagnosis digest changed after kernel-state restoration"
            )
        bundle = bundle.model_copy(update={"diagnosis_run": restored_run})
    return bundle


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


def _read_strict_ledger(
    manifest: BenchmarkManifest,
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reject duplicate/conflicting terminals and terminals without STARTED.

    Ledger integrity is a batch-level precondition: a run may start at most
    once and reach at most one terminal state, and every terminal entry must
    have its STARTED predecessor.
    """

    seen: dict[str, dict[str, Any]] = {}
    terminals: list[dict[str, Any]] = []
    for entry in entries:
        state = entry.get("state")
        run_id = entry.get("run_id")
        if state not in {"STARTED", *_TERMINAL_STATES}:
            raise QualityBaselineError(f"unknown ledger state {state!r} for run {run_id}")
        prior = seen.get(run_id)
        if state == "STARTED":
            if prior is not None:
                raise QualityBaselineError(f"duplicate STARTED entry for run {run_id}")
            seen[run_id] = entry
        else:
            if prior is None:
                raise QualityBaselineError(
                    f"terminal ledger entry without STARTED for run {run_id}"
                )
            if prior.get("state") in _TERMINAL_STATES:
                raise QualityBaselineError(f"duplicate terminal entry for run {run_id}")
            seen[run_id] = entry
            terminals.append(entry)
    for cell in manifest.cells:
        if cell.run_id in seen and seen[cell.run_id].get("manifest_id") != manifest.manifest_id:
            raise QualityBaselineError(
                f"ledger manifest_id does not match the batch manifest for run {cell.run_id}"
            )
    return terminals


def _verify_cell_binding(
    manifest: BenchmarkManifest,
    ledger_entry: dict[str, Any],
    metadata: dict[str, Any],
) -> None:
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
    diagnosis_run: DiagnosisRunResultAny,
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
    diagnosis_run: DiagnosisRunResultAny,
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
    # Pass the full trace so a returned trace_sequence is the archive sequence,
    # including intervening model, plan, and gate events.
    unwitnessed = tuple(
        sorted(
            (gap.gap_kind, gap.subject, gap.reason_code)
            for gap in contract_gaps
            if gap.tool_name is not None
            and not refusal_witnessed(
                diagnosis_run.trace,
                tool_name=gap.tool_name,
                target=gap.subject,
                code=gap.reason_code,
                diagnosis_run_schema_version=diagnosis_run.schema_version,
            )
            .witnessed
            and (gap.gap_kind, gap.subject, gap.reason_code) in set(missing)
        )
    )
    return Axis3Result(
        expected=expected,
        missing=missing,
        extra=extra,
        unwitnessed_receipts=unwitnessed,
    )


def _run_transport_summary(diagnosis_run: DiagnosisRunResultAny) -> str | None:
    events = [
        event
        for event in diagnosis_run.trace
        if getattr(event, "event_type", None) == "MODEL_PROTOCOL"
    ]
    if not events:
        return None
    return "; ".join(
        f"provider_failure(err={event.error_type},"
        f"request_index={event.model_request_index},"
        f"transport={event.transport_diagnostic})"
        for event in events
    )


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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
    terminals = _read_strict_ledger(manifest, entries)
    started = {entry.get("run_id") for entry in entries if entry.get("state") == "STARTED"}
    terminal_ids = {entry.get("run_id") for entry in terminals}
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
    for entry in terminals:
        run_id = entry["run_id"]
        artifact_dir = project_root / "artifacts" / run_id
        base = dict(
            batch=manifest.manifest_id,
            sequence=entry["sequence"],
            run_id=run_id,
            incident_case_id=entry["incident_case_id"],
            strategy=entry["strategy"],
        )
        try:
            metadata = _load_json(artifact_dir / "metadata.json")
            diagnosis_payload = _load_json(artifact_dir / "diagnosis.json")
            archived = EvaluationResult.model_validate(
                _load_json(artifact_dir / "evaluation.json")
            )
            _verify_cell_binding(manifest, entry, metadata)
            bundle_payload = _load_json(
                project_root
                / ".dig"
                / "scoring-inputs"
                / run_id
                / "evaluation_inputs.json"
            )
            bundle = _load_bundle_for_analysis(bundle_payload)
        except (OSError, ValueError, KeyError, EvaluationInputsError, QualityBaselineError) as exc:
            cells.append(
                CellAnalysis(
                    **base,
                    category="CORRUPT",
                    detail=str(exc)[:200],
                )
            )
            continue
        # Plane binding: the archived diagnosis must be exactly the diagnosis
        # inside the verified scoring bundle, so a swapped or tampered
        # diagnosis.json can never dodge verification via a business category.
        if diagnosis_payload != bundle.diagnosis_run.diagnosis.model_dump(mode="json"):
            cells.append(
                CellAnalysis(
                    **base,
                    category="CORRUPT",
                    detail="archived diagnosis does not match the scoring bundle",
                )
            )
            continue
        if bundle.scenario_digest != _catalog_digest(manifest, entry["incident_case_id"]):
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
        if diagnosis.status is DiagnosisStatus.MODEL_ERROR:
            cells.append(
                CellAnalysis(
                    **base,
                    category="RUN_ERROR",
                    transport=_run_transport_summary(bundle.diagnosis_run),
                )
            )
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


def render_markdown(batches: dict[str, list[CellAnalysis]]) -> str:
    """Render per-cell tables and aggregates from the structured results."""

    lines: list[str] = []
    scenario_names: dict[str, dict[int, str]] = {}
    for batch, cells in batches.items():
        lines.append(f"## {batch}")
        lines.append("")
        lines.append(
            "| seq | scenario | strategy | category | direction | axis1 | axis2 | axis3 |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        scenario_names[batch] = {}
        for cell in cells:
            if cell.category == "NOT_EXECUTED":
                continue
            axis1 = (
                "+".join((*cell.axis1.not_collected, *cell.axis1.collected_uncited))
                if cell.axis1 is not None and cell.axis1.has_defect
                else ""
            )
            axis2 = (
                "+".join(
                    v.outcome
                    for v in cell.axis2.verdicts
                    if v.outcome not in {"supported", "not_applicable"}
                )
                if cell.axis2 is not None and cell.axis2.has_defect
                else ""
            )
            axis3 = (
                "missing:"
                + ",".join("/".join(gap) for gap in cell.axis3.missing)
                + " extra:"
                + ",".join("/".join(gap) for gap in cell.axis3.extra)
                + " unwitnessed:"
                + ",".join("/".join(gap) for gap in cell.axis3.unwitnessed_receipts)
                if cell.axis3 is not None and cell.axis3.has_defect
                else ""
            )
            lines.append(
                f"| {cell.sequence} | {cell.incident_case_id} | {cell.strategy} "
                f"| {cell.category} | {cell.status_direction or ''} | {axis1} | {axis2} | {axis3} |"
            )
        lines.append("")
    return "\n".join(lines)


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
    "render_markdown",
    "verify_scoring_identity",
]
