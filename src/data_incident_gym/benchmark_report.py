"""Benchmark reporting: verify one completed suite and aggregate its metrics.

The report is management-plane output. It reads the frozen manifest, the doctor
receipt, the ledger and the archived run bundles; it never touches the database,
the model or the tools. Two groups of metrics are produced:

- the original run-level metrics (paired success, status accuracy, claim
  evidence validity, efficiency) computed from archived evaluation checks;
- the abstention, status-confusion, claim-support and citation metrics, which
  follow fixed denominators and reuse the evaluator's own per-claim rules.

Rules that hold for every new metric:

- A cell whose applicable safety gate failed is an invalid environment sample:
  it is excluded from every denominator and counted separately.
- A cell that ended in ``MODEL_ERROR`` is a failed run, not an abstention: it
  stays in its set's denominator, is never counted as abstaining, and is also
  reported on its own error rate.
- A rate with an empty denominator is ``null`` with a stable
  ``zero_denominator_reason``; it is never rendered as 0 or 100%.
- Claim and citation metrics recompute the evaluator's per-claim verdicts, so
  the running result inputs (evaluator source, schemas, profile spec) must equal
  the identity the manifest froze; otherwise the report fails closed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from contextlib import suppress
from pathlib import Path
from statistics import median
from typing import Any
from uuid import uuid4

from data_incident_gym.artifacts import (
    ARTIFACT_FILENAMES,
    EvidenceArtifact,
    RunMetadata,
    trace_schema_for_policy_identity,
    validate_trace_envelope,
)
from data_incident_gym.benchmark_manifest import (
    BenchmarkManifest,
    BenchmarkManifestError,
    ScenarioCatalogEntry,
    result_inputs_for_project,
)
from data_incident_gym.benchmark_runner import (
    BenchmarkDoctorReceipt,
    BenchmarkLedgerEntry,
)
from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.diagnosis import (
    KERNEL_STRATEGIES,
    MAIN_STRATEGIES,
    Diagnosis,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    KernelStateTraceEvent,
    ToolTraceEvent,
    refusal_witnessed,
)
from data_incident_gym.diagnostic_kernel import InvestigationState
from data_incident_gym.evaluation import (
    ALL_CLAIM_KINDS,
    EVALUATOR_VERSION,
    ControllerCheckCode,
    EvaluationCheckCode,
    EvaluationResult,
    claim_support_verdicts,
    claim_supported_by_records,
)
from data_incident_gym.reliability import (
    ReliabilityError,
    ReliabilityTrial,
    reliability_for,
)
from data_incident_gym.scenarios import ScenarioError, ScenarioSpec, load_scenario_spec


class BenchmarkReportError(RuntimeError):
    """Raised when a benchmark suite cannot be reported safely."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _load_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")),
        )
    except Exception as exc:
        raise BenchmarkReportError(f"invalid JSON: {path.name}") from exc


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _check(evaluation: EvaluationResult, code: EvaluationCheckCode) -> bool:
    return next(item for item in evaluation.checks if item.code is code).passed


def _wilson(successes: int, total: int) -> dict[str, float | int | None]:
    if total == 0:
        return {"successes": 0, "total": 0, "rate": None, "lower": None, "upper": None}
    z = 1.959963984540054
    rate = successes / total
    denominator = 1 + z * z / total
    centre = (rate + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total)) / denominator
    return {
        "successes": successes,
        "total": total,
        "rate": rate,
        "lower": max(0.0, centre - margin),
        "upper": min(1.0, centre + margin),
    }


def _ratio(
    numerator: int,
    denominator: int,
    *,
    applicable_set: str,
    zero_denominator_reason: str,
) -> dict[str, Any]:
    """One metric with a fixed denominator and an explicit empty-set rule."""

    if denominator == 0:
        return {
            "numerator": 0,
            "denominator": 0,
            "rate": None,
            "lower": None,
            "upper": None,
            "applicable_set": applicable_set,
            "zero_denominator_reason": zero_denominator_reason,
        }
    wilson = _wilson(numerator, denominator)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": wilson["rate"],
        "lower": wilson["lower"],
        "upper": wilson["upper"],
        "applicable_set": applicable_set,
        "zero_denominator_reason": None,
    }


_EXPECTED_STATUSES = ("CONFIRMED", "INSUFFICIENT_EVIDENCE", "NO_INCIDENT")
_ACTUAL_STATUSES = (*_EXPECTED_STATUSES, "MODEL_ERROR")


def _valid_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cells that are not invalid environment samples.

    A cell with a failed applicable *environment* gate cannot be interpreted as
    a diagnostic outcome, so every metric below uses valid cells only and the
    excluded count is reported next to it. Agent-policy gate failures are not
    environment problems and stay in the denominators as failed trials.
    """

    return [item for item in items if not item["environment_gates"]]


def _expected_status(item: dict[str, Any]) -> str:
    return item["evaluation"].expected_status


def _abstained(item: dict[str, Any]) -> bool:
    return item["diagnosis"].status is DiagnosisStatus.INSUFFICIENT_EVIDENCE


def _failed_controller_gates(evaluation: Any) -> tuple[Any, ...]:
    """Applicable controller (kernel) gates that failed.

    ``EvaluationResult.status`` only reflects the evidence checks, so a run that
    violated the kernel contract can still carry ``PASSED``; every success
    judgement below has to consult these separately.
    """

    return tuple(
        check
        for check in getattr(evaluation, "controller_checks", ())
        if not check.passed and check.code in _CONTROLLER_SAFETY_GATES
    )


def _failed_applicable(evaluation: Any, codes: frozenset[EvaluationCheckCode]) -> tuple[Any, ...]:
    return tuple(
        check
        for check in evaluation.checks
        if check.applicability.value == "APPLICABLE" and not check.passed and check.code in codes
    )


def _evaluation_passed(evaluation: Any) -> bool:
    """Success for one archived run: evidence checks passed *and* every
    applicable controller gate passed."""

    if evaluation.status.value != "PASSED":
        return False
    return not _failed_controller_gates(evaluation)


def _passed(item: dict[str, Any]) -> bool:
    return _evaluation_passed(item["evaluation"])


def _abstention_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    valid = _valid_items(items)
    confirmable = [
        item for item in valid if _expected_status(item) == DiagnosisStatus.CONFIRMED.value
    ]
    health = [
        item for item in valid if _expected_status(item) == DiagnosisStatus.NO_INCIDENT.value
    ]
    expected_abstention = [
        item
        for item in valid
        if _expected_status(item) == DiagnosisStatus.INSUFFICIENT_EVIDENCE.value
    ]
    true_positives = sum(_abstained(item) for item in expected_abstention)
    return {
        "cells": {"valid": len(valid), "invalid_excluded": len(items) - len(valid)},
        "confirmable_over_abstention": _ratio(
            sum(_abstained(item) for item in confirmable),
            len(confirmable),
            applicable_set="valid cells whose contract expects CONFIRMED",
            zero_denominator_reason="no valid confirmable cells",
        ),
        "health_over_abstention": _ratio(
            sum(_abstained(item) for item in health),
            len(health),
            applicable_set="valid cells whose contract expects NO_INCIDENT",
            zero_denominator_reason="no valid healthy-control cells",
        ),
        "qualified_abstention": _ratio(
            sum(_abstained(item) and _passed(item) for item in expected_abstention),
            len(expected_abstention),
            applicable_set="valid cells whose contract expects INSUFFICIENT_EVIDENCE",
            zero_denominator_reason="no valid expected-abstention cells",
        ),
        "abstention_precision": _ratio(
            true_positives,
            sum(_abstained(item) for item in valid),
            applicable_set="valid cells whose diagnosis abstained",
            zero_denominator_reason="no valid cell abstained",
        ),
        "abstention_recall": _ratio(
            true_positives,
            len(expected_abstention),
            applicable_set="valid cells whose contract expects INSUFFICIENT_EVIDENCE",
            zero_denominator_reason="no valid expected-abstention cells",
        ),
    }


def _status_confusion(items: list[dict[str, Any]]) -> dict[str, Any]:
    valid = _valid_items(items)
    matrix = {expected: dict.fromkeys(_ACTUAL_STATUSES, 0) for expected in _EXPECTED_STATUSES}
    classified = 0
    for item in valid:
        expected = _expected_status(item)
        actual = item["diagnosis"].status.value
        if expected in matrix and actual in matrix[expected]:
            matrix[expected][actual] += 1
            classified += 1
    return {
        "expected_states": list(_EXPECTED_STATUSES),
        "actual_states": list(_ACTUAL_STATUSES),
        "matrix": matrix,
        "valid_cells": len(valid),
        "invalid_cells": len(items) - len(valid),
        "unclassified_cells": len(valid) - classified,
        "model_error_rate": _ratio(
            sum(item["diagnosis"].status is DiagnosisStatus.MODEL_ERROR for item in valid),
            len(valid),
            applicable_set="valid cells",
            zero_denominator_reason="no valid cells",
        ),
    }


def _claim_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    applicable = 0
    supported = 0
    inapplicable: dict[str, int] = {}
    unsupported_kinds: dict[str, int] = {}
    for item in _valid_items(items):
        for verdict in item["claim_verdicts"]:
            if verdict.applicable:
                applicable += 1
                supported += bool(verdict.supported)
            elif verdict.kind in ALL_CLAIM_KINDS:
                inapplicable[verdict.kind] = inapplicable.get(verdict.kind, 0) + 1
            else:
                unsupported_kinds[verdict.kind] = unsupported_kinds.get(verdict.kind, 0) + 1
    return {
        "support_coverage": _ratio(
            supported,
            applicable,
            applicable_set=(
                "structured claims in valid cells whose kind has a deterministic support rule "
                "under the contract's expected status"
            ),
            zero_denominator_reason="no applicable structured claims in valid cells",
        ),
        "inapplicable_claim_types": dict(sorted(inapplicable.items())),
        "unsupported_claim_types": dict(sorted(unsupported_kinds.items())),
    }


def _citation_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    cited = 0
    known = 0
    single_checked = 0
    single_supported = 0
    removable = 0
    load_bearing = 0
    health_citations = 0
    for item in _valid_items(items):
        records = item["records"]
        inventory = {record.evidence_id: record for record in records}
        diagnosis = item["diagnosis"]
        scenario = item["scenario"]
        citations = tuple(
            dict.fromkeys(
                (
                    *diagnosis.evidence_ids,
                    *(
                        evidence_id
                        for claim in diagnosis.claims
                        for evidence_id in claim.evidence_ids
                    ),
                )
            )
        )
        cited += len(citations)
        known += sum(evidence_id in inventory for evidence_id in citations)
        for claim, verdict in zip(diagnosis.claims, item["claim_verdicts"], strict=True):
            if not verdict.applicable:
                continue
            resolved = tuple(
                inventory[evidence_id]
                for evidence_id in claim.evidence_ids
                if evidence_id in inventory
            )
            if claim.kind == "HEALTH_STATE":
                # A health claim is proven by several records jointly, so a
                # single citation cannot support it; those citations are
                # reported separately instead of being scored as unsupported.
                health_citations += len(resolved)
            else:
                for record in resolved:
                    single_checked += 1
                    single_supported += claim_supported_by_records(
                        scenario, claim, (record,), all_records=records
                    )
            if verdict.supported:
                for record in resolved:
                    removable += 1
                    reduced = tuple(
                        candidate for candidate in resolved if candidate is not record
                    )
                    if not claim_supported_by_records(
                        scenario, claim, reduced, all_records=records
                    ):
                        load_bearing += 1
    return {
        "existence": _ratio(
            known,
            cited,
            applicable_set=(
                "citations in valid cells, top-level and per-claim, deduplicated per cell"
            ),
            zero_denominator_reason="no citations in valid cells",
        ),
        "single_record_support": _ratio(
            single_supported,
            single_checked,
            applicable_set=(
                "citations of ROOT_CAUSE and AFFECTED_ASSET claims in valid cells"
            ),
            zero_denominator_reason="no single-record claim citations in valid cells",
        ),
        "redundancy": {
            "load_bearing": _ratio(
                load_bearing,
                removable,
                applicable_set="citations of supported applicable claims in valid cells",
                zero_denominator_reason="no citations on supported applicable claims",
            ),
            "redundant_citations": removable - load_bearing,
            "note": (
                "removing a citation without changing the support verdict is redundancy, "
                "not an error; it is reported, never penalized"
            ),
        },
        "health_claim_citations": health_citations,
    }


_SAFETY_GATES = frozenset(
    {
        EvaluationCheckCode.ENVIRONMENT_VERIFIED,
        EvaluationCheckCode.EVIDENCE_IDS_EXIST,
        EvaluationCheckCode.EVIDENCE_RUN_SCOPE,
        EvaluationCheckCode.RECOVERY_HEALTHY,
        EvaluationCheckCode.TOOL_ALLOWLIST_EXACT,
        EvaluationCheckCode.TRACE_READ_ONLY_SAFE,
    }
)
# Gates whose failure means the harness, not the agent, was unsound: the private
# scenario did not verify, evidence escaped its run, or the lab could not be
# restored. Every other safety gate — fabricated citations
# (EVIDENCE_IDS_EXIST), tools outside the allowlist, write attempts, invalid
# kernel state — is an agent failure and stays in the failure denominators; an
# agent must never escape a failure rate by breaking the rules.
_ENVIRONMENT_GATES = frozenset(
    {
        EvaluationCheckCode.ENVIRONMENT_VERIFIED,
        EvaluationCheckCode.EVIDENCE_RUN_SCOPE,
        EvaluationCheckCode.RECOVERY_HEALTHY,
    }
)
_CONTROLLER_SAFETY_GATES = frozenset(
    {
        ControllerCheckCode.KERNEL_STATE_VALID,
        ControllerCheckCode.KERNEL_HYPOTHESIS_GATE,
        ControllerCheckCode.KERNEL_EVIDENCE_GAP_GATE,
    }
)
_EVIDENCE_TOOL_NAMES = frozenset(
    {
        "get_dbt_run_results",
        "get_dbt_node_error",
        "get_relation_schema",
        "get_dbt_lineage",
        "get_relation_data_profile",
        "get_relation_history",
    }
)


def _family(item: dict[str, Any]) -> str:
    case_id = item["cell"].incident_case_id
    return case_id[:-2] if case_id.endswith(("_a", "_b")) else case_id


def _metric_values(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    confirmable = [
        item
        for item in items
        if item["metadata"].variant_role == "TEST_CONFIRMABLE"
        and item["evaluation"].expected_status == DiagnosisStatus.CONFIRMED.value
    ]
    insufficient = [
        item
        for item in items
        if item["metadata"].variant_role == "TEST_INSUFFICIENT"
        and item["evaluation"].expected_status == DiagnosisStatus.INSUFFICIENT_EVIDENCE.value
    ]
    controls = [
        item
        for item in items
        if item["metadata"].variant_role == "NO_INCIDENT_CONTROL"
        and item["evaluation"].expected_status == DiagnosisStatus.NO_INCIDENT.value
    ]
    confirmable_by_pair = {(_family(item), item["cell"].repeat_index): item for item in confirmable}
    insufficient_by_pair = {
        (_family(item), item["cell"].repeat_index): item for item in insufficient
    }
    paired = 0
    for key, confirmed in confirmable_by_pair.items():
        gap = insufficient_by_pair.get(key)
        if (
            gap is not None
            and _check(confirmed["evaluation"], EvaluationCheckCode.ROOT_CAUSE_ACCEPTED)
            and _check(confirmed["evaluation"], EvaluationCheckCode.STATUS_EXACT)
            and _check(gap["evaluation"], EvaluationCheckCode.STATUS_EXACT)
            and _check(gap["evaluation"], EvaluationCheckCode.INSUFFICIENCY_GAP_DECLARED)
        ):
            paired += 1
    claim_count = sum(len(item["diagnosis"].claims) for item in items)
    valid_claims = sum(
        len(item["diagnosis"].claims)
        for item in items
        if item["diagnosis"].claims
        and _check(item["evaluation"], EvaluationCheckCode.CLAIM_EVIDENCE_COMPATIBLE)
    )
    asset_sets: list[tuple[set[str], set[str]]] = []
    label_universe: set[str] = set()
    for item in confirmable:
        check = next(
            check
            for check in item["evaluation"].checks
            if check.code is EvaluationCheckCode.AFFECTED_ASSETS_EXACT
        )
        expected = set(check.expected) - {"NOT_APPLICABLE"}
        actual = set(check.actual) - {"NOT_APPLICABLE"}
        label_universe.update(expected | actual)
        asset_sets.append((expected, actual))
    asset_f1 = []
    for label in sorted(label_universe):
        true_positive = sum(
            label in expected and label in actual for expected, actual in asset_sets
        )
        false_positive = sum(
            label not in expected and label in actual for expected, actual in asset_sets
        )
        false_negative = sum(
            label in expected and label not in actual for expected, actual in asset_sets
        )
        denominator = 2 * true_positive + false_positive + false_negative
        asset_f1.append(2 * true_positive / denominator if denominator else 0.0)
    return {
        "paired_success": _wilson(paired, len(confirmable)),
        "root_cause_accuracy": _wilson(
            sum(
                _check(item["evaluation"], EvaluationCheckCode.ROOT_CAUSE_ACCEPTED)
                for item in confirmable
            ),
            len(confirmable),
        ),
        "unsupported_confirmation_rate": _wilson(
            sum(item["diagnosis"].status is DiagnosisStatus.CONFIRMED for item in insufficient),
            len(insufficient),
        ),
        "status_accuracy": _wilson(
            sum(_check(item["evaluation"], EvaluationCheckCode.STATUS_EXACT) for item in items),
            len(items),
        ),
        "claim_evidence_validity": _wilson(valid_claims, claim_count),
        "no_incident_accuracy": _wilson(
            sum(
                item["diagnosis"].status is DiagnosisStatus.NO_INCIDENT
                and _check(item["evaluation"], EvaluationCheckCode.POSITIVE_HEALTH_EVIDENCE)
                for item in controls
            ),
            len(controls),
        ),
        "affected_assets_macro_f1": (
            {
                "value": sum(asset_f1) / len(asset_f1),
                "samples": len(asset_sets),
                "labels": len(asset_f1),
                "label_universe": sorted(label_universe),
            }
            if asset_f1
            else {
                "value": None,
                "samples": len(asset_sets),
                "labels": 0,
                "label_universe": sorted(label_universe),
                "reason": "asset labels unavailable",
            }
        ),
        "abstention": _abstention_metrics(items),
        "status_confusion": _status_confusion(items),
        "claim_support": _claim_metrics(items),
        "citation_quality": _citation_metrics(items),
    }


def _efficiency(items: list[dict[str, Any]]) -> dict[str, Any]:
    # Same success definition as every other metric: a kernel-gate violation is
    # not a passing cell even when the evaluator's evidence checks passed.
    passed = [item for item in items if _passed(item)]
    exact = 0
    equivalent = 0
    for item in passed:
        tool_calls = [
            event.event
            for event in item["trace"]
            if isinstance(event.event, ToolTraceEvent)
        ]
        fingerprints = [event.fingerprint for event in tool_calls]
        exact += len(fingerprints) - len(set(fingerprints))
        by_evidence: dict[tuple[str, ...], list[str]] = {}
        for event in tool_calls:
            if event.evidence_ids:
                by_evidence.setdefault(tuple(sorted(event.evidence_ids)), []).append(
                    event.fingerprint
                )
        equivalent += sum(
            sum(fingerprint != group[0] for fingerprint in group[1:])
            for group in by_evidence.values()
            if group
        )
    post_decisive: list[int] = []
    post_decisive_unavailable = 0
    for item in passed:
        cited = set(item["diagnosis"].evidence_ids)
        if not cited:
            continue
        kernel_states = [
            InvestigationState.model_validate(event.event.state)
            for event in item["trace"]
            if isinstance(event.event, KernelStateTraceEvent)
        ]
        if any(
            getattr(gap.status, "value", gap.status) == "BLOCKED"
            for state in kernel_states
            for gap in state.gaps
        ):
            post_decisive_unavailable += 1
            continue
        seen: set[str] = set()
        decisive_at: int | None = None
        for index, event in enumerate(item["trace"]):
            seen.update(getattr(event.event, "evidence_ids", ()))
            if cited.issubset(seen):
                decisive_at = index
                break
        if decisive_at is not None:
            post_decisive.append(
                sum(
                    isinstance(event.event, ToolTraceEvent)
                    for event in item["trace"][decisive_at + 1 :]
                )
            )

    def rates(code: str) -> dict[str, float | int | None]:
        count = sum(getattr(item["diagnosis"], "summary", None) == code for item in items)
        return _wilson(count, len(items))

    metrics = [item["metadata"].diagnosis_metrics for item in passed]
    return {
        "passed_cells": len(passed),
        "successful_tools_median": median([metric.successful_tool_calls for metric in metrics])
        if metrics
        else None,
        "exact_duplicate_calls": exact,
        "equivalent_calls": equivalent,
        "post_decisive_tool_calls_median": (median(post_decisive) if post_decisive else None),
        "post_decisive_tool_calls_reason": (
            None
            if post_decisive
            else (
                "trace does not expose blocked gap attempts"
                if post_decisive_unavailable
                else "trace lacks a decisive evidence prefix"
            )
        ),
        "budget_exhaustion_rate": {
            "model_request_limit": rates("MODEL_REQUEST_LIMIT"),
            "model_tool_call_limit": rates("MODEL_TOOL_CALL_LIMIT"),
        },
        "timeout_rate": rates("MODEL_TIMEOUT"),
        "model_error_rate": _wilson(
            sum(item["diagnosis"].status is DiagnosisStatus.MODEL_ERROR for item in items),
            len(items),
        ),
        "tokens": {
            "input_total": sum(metric.input_tokens for metric in metrics),
            "output_total": sum(metric.output_tokens for metric in metrics),
            "input_median": median([metric.input_tokens for metric in metrics])
            if metrics
            else None,
            "output_median": median([metric.output_tokens for metric in metrics])
            if metrics
            else None,
        },
        "requests": {
            "total": sum(metric.model_requests for metric in metrics),
            "median": median([metric.model_requests for metric in metrics]) if metrics else None,
        },
        "elapsed_ms": {
            "total": sum(metric.elapsed_ms for metric in metrics),
            "median": median([metric.elapsed_ms for metric in metrics]) if metrics else None,
        },
    }


def _refusal_witness_source_counts(
    items: list[dict[str, Any]], diagnosis_run_schema_version: str
) -> dict[str, Any]:
    counts = {
        "CONTROLLER_PRECHECK": 0,
        "EVIDENCE_BACKEND": 0,
        "LEGACY_UNATTRIBUTED": 0,
    }
    expected_tool_bound_gaps = 0
    denominator = 0
    gap_set_mismatch_cells = 0
    inapplicable_reasons = {
        "SCENARIO_NOT_INSUFFICIENT_EVIDENCE": 0,
        "RUN_NOT_INSUFFICIENT_EVIDENCE": 0,
        "CONTRACT_GAP_NOT_DECLARED": 0,
    }
    for item in items:
        scenario = item["scenario"]
        diagnosis = item["diagnosis"]
        contract_gaps = scenario.observable_evidence_contract.unresolved_gaps
        tool_gaps = tuple(gap for gap in contract_gaps if gap.tool_name is not None)
        expected_tool_bound_gaps += len(tool_gaps)
        if not tool_gaps:
            continue
        if scenario.expected_status != DiagnosisStatus.INSUFFICIENT_EVIDENCE.value:
            inapplicable_reasons["SCENARIO_NOT_INSUFFICIENT_EVIDENCE"] += len(tool_gaps)
            continue
        if diagnosis.status is not DiagnosisStatus.INSUFFICIENT_EVIDENCE:
            inapplicable_reasons["RUN_NOT_INSUFFICIENT_EVIDENCE"] += len(tool_gaps)
            continue
        expected_gaps = {
            (gap.gap_kind, gap.subject, gap.reason_code) for gap in contract_gaps
        }
        actual_gaps = {
            (gap.evidence_kind, gap.subject, gap.reason_code)
            for gap in diagnosis.unresolved_evidence
        }
        if actual_gaps != expected_gaps:
            gap_set_mismatch_cells += 1
        trace_events = tuple(envelope.event for envelope in item["trace"])
        for gap in tool_gaps:
            gap_key = (gap.gap_kind, gap.subject, gap.reason_code)
            if gap_key not in actual_gaps:
                inapplicable_reasons["CONTRACT_GAP_NOT_DECLARED"] += 1
                continue
            denominator += 1
            witness = refusal_witnessed(
                trace_events,
                tool_name=gap.tool_name,
                target=gap.subject,
                code=gap.reason_code,
                diagnosis_run_schema_version=diagnosis_run_schema_version,
            )
            if witness.witnessed and witness.outcome_origin in counts:
                counts[witness.outcome_origin] += 1
    return {
        "denominator_definition": (
            "declared tool-bound contract gaps in insufficient-evidence cells, counted per gap "
            "when that gap is present in the diagnosis; full gap-set mismatches are reported "
            "separately"
        ),
        "expected_tool_bound_gaps": expected_tool_bound_gaps,
        "eligible_tool_bound_gaps": denominator,
        "gap_set_mismatch_cells": gap_set_mismatch_cells,
        "inapplicable_tool_bound_gaps": sum(inapplicable_reasons.values()),
        "inapplicable_reasons": inapplicable_reasons,
        **{
            source: {
                "numerator": numerator,
                "denominator": denominator,
                "rate": numerator / denominator if denominator else None,
                "zero_denominator_reason": (
                    None if denominator else "NO_APPLICABLE_DECLARED_TOOL_GAPS"
                ),
            }
            for source, numerator in counts.items()
        },
    }


class BenchmarkReporter:
    """Validate and aggregate an already completed benchmark suite.

    This class intentionally has no database, model or tool dependencies. It
    does read the private scenario contracts and the evaluator's deterministic
    per-claim rules, because the claim and citation metrics must be computed
    with the same rules that scored the runs; the running result inputs are
    checked against the frozen manifest identity before any metric is derived.
    """

    def __init__(
        self,
        manifest: BenchmarkManifest,
        suite_root: Path,
        *,
        project_root: Path = PROJECT_ROOT,
    ) -> None:
        self._manifest = manifest
        self._suite_root = Path(suite_root)
        self._project_root = Path(project_root)

    def _scenario(self, case_id: str, catalog: dict[str, ScenarioCatalogEntry]) -> ScenarioSpec:
        entry = catalog.get(case_id)
        if entry is None:
            self._fail(f"scenario is outside the frozen catalog: {case_id}")
        try:
            scenario = load_scenario_spec(case_id, self._project_root)
        except ScenarioError as exc:
            raise BenchmarkReportError(f"scenario contract cannot be loaded: {case_id}") from exc
        if scenario.digest() != entry.scenario_spec_sha256:
            self._fail(f"scenario contract drifted from the frozen manifest: {case_id}")
        return scenario

    def _reliability(
        self, strategy: DiagnosticStrategy, items: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Repeat stability for one strategy's cells (protocol p1.reliability.v1).

        Only this strategy's scheduled groups are planned here: other strategies'
        repeats are different groups and must never lend their n. The planned
        repetition count comes from the frozen manifest schedule; invalid
        environment samples and missing repeats stay visible instead of being
        scored or silently dropped.
        """

        observed: dict[tuple[str, str], list[ReliabilityTrial]] = {}
        for item in items:
            cell = item["cell"]
            if cell.strategy is not strategy:
                # Defence in depth: a caller that mixes strategies must not leak
                # a foreign group into this strategy's block.
                self._fail(f"reliability received a foreign-strategy cell: {cell.run_id}")
            observed.setdefault((cell.incident_case_id, cell.strategy.value), []).append(
                ReliabilityTrial(
                    repeat_index=cell.repeat_index,
                    passed=_passed(item),
                    invalid=bool(item["environment_gates"]),
                    status=item["diagnosis"].status.value,
                    invalid_gate_codes=tuple(
                        sorted({str(gate["gate"]) for gate in item["environment_gates"]})
                    ),
                )
            )
        try:
            return reliability_for(
                schedule=(
                    (cell.incident_case_id, cell.strategy.value, cell.repeat_index)
                    for cell in self._manifest.cells
                    if cell.strategy is strategy
                ),
                observed={key: tuple(value) for key, value in observed.items()},
            )
        except ReliabilityError as exc:
            raise BenchmarkReportError(f"reliability protocol rejected the suite: {exc}") from None

    @staticmethod
    def result_inputs_digest(manifest: BenchmarkManifest) -> str:
        """Return the digest used by the benchmark doctor's receipt."""

        return _canonical_digest(manifest.result_inputs.model_dump(mode="json"))

    def _fail(self, message: str) -> None:
        raise BenchmarkReportError(message)

    def _doctor(self) -> BenchmarkDoctorReceipt:
        path = self._suite_root / "doctor.json"
        if path.is_symlink() or not path.is_file():
            self._fail("doctor receipt is missing or invalid")
        try:
            receipt = BenchmarkDoctorReceipt.model_validate(_load_json(path))
        except BenchmarkReportError:
            raise
        except Exception as exc:
            raise BenchmarkReportError("doctor receipt is invalid") from exc
        expected = (
            self._manifest.manifest_id,
            self._manifest.digest(),
            self._manifest.implementation_revision,
            self.result_inputs_digest(self._manifest),
        )
        actual = (
            receipt.manifest_id,
            receipt.manifest_sha256,
            receipt.implementation_revision,
            receipt.result_inputs_sha256,
        )
        if (
            actual != expected
            or receipt.cell_selector is not None
            or receipt.model_probe_required is not True
            or receipt.result.status.value != "PASSED"
        ):
            self._fail("doctor receipt does not match a passing manifest-bound doctor")
        return receipt

    def _ledger(self) -> list[BenchmarkLedgerEntry]:
        path = self._suite_root / "ledger.jsonl"
        if path.is_symlink() or not path.is_file():
            self._fail("benchmark ledger is missing")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise BenchmarkReportError("benchmark ledger cannot be read") from exc
        if len(lines) != len(self._manifest.cells) * 2:
            self._fail("benchmark ledger does not contain exactly two entries per cell")
        entries: list[BenchmarkLedgerEntry] = []
        for line in lines:
            if not line.strip():
                self._fail("benchmark ledger contains a blank line")
            try:
                entries.append(BenchmarkLedgerEntry.model_validate(_load_json_line(line)))
            except Exception as exc:
                raise BenchmarkReportError("benchmark ledger is invalid") from exc
        for index, cell in enumerate(self._manifest.cells):
            started, terminal = entries[index * 2 : index * 2 + 2]
            for entry in (started, terminal):
                if (
                    entry.manifest_id != self._manifest.manifest_id
                    or entry.sequence != cell.sequence
                    or entry.run_id != cell.run_id
                    or entry.incident_case_id != cell.incident_case_id
                    or entry.strategy is not cell.strategy
                    or entry.artifact_path != f"artifacts/{cell.run_id}"
                ):
                    self._fail("benchmark ledger identity or order does not match manifest")
            if started.state != "STARTED" or terminal.state not in {"COMPLETED", "FAILED"}:
                self._fail("benchmark ledger contains a non-terminal cell")
            if terminal.started_at != started.started_at:
                self._fail("benchmark ledger terminal does not preserve the start timestamp")
        return entries

    def _bundle(
        self, cell: Any, checkout_revision: str
    ) -> tuple[RunMetadata, Any, EvaluationResult, tuple[Any, ...], tuple[Any, ...]]:
        artifact_root = self._suite_root.parent.parent
        run_path = artifact_root / cell.run_id
        if run_path.is_symlink() or not run_path.is_dir():
            self._fail(f"artifact directory is missing: {cell.run_id}")
        children = tuple(run_path.iterdir())
        if {item.name for item in children} != set(ARTIFACT_FILENAMES) or any(
            item.is_symlink() or not item.is_file() for item in children
        ):
            self._fail(f"artifact bundle is not exactly six regular files: {cell.run_id}")
        policy = next(item for item in self._manifest.policies if item.strategy is cell.strategy)
        try:
            metadata = RunMetadata.model_validate(_load_json(run_path / "metadata.json"))
            evidence = EvidenceArtifact.model_validate(_load_json(run_path / "evidence.json"))
            diagnosis = Diagnosis.model_validate(_load_json(run_path / "diagnosis.json"))
            evaluation = EvaluationResult.model_validate(_load_json(run_path / "evaluation.json"))
            trace = tuple(
                validate_trace_envelope(
                    _load_json_line(line),
                    expected_schema_version=trace_schema_for_policy_identity(
                        policy.policy_identity
                    ),
                )
                for line in (run_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
                if line
            )
        except BenchmarkReportError:
            raise
        except Exception as exc:
            raise BenchmarkReportError(f"artifact bundle is invalid: {cell.run_id}") from exc
        if not trace or not isinstance(trace[-1].event, DiagnosisTerminalTraceEvent):
            self._fail(f"trace does not end in a diagnosis terminal: {cell.run_id}")
        if any(envelope.sequence != index for index, envelope in enumerate(trace, start=1)):
            self._fail(f"trace sequence is not contiguous: {cell.run_id}")
        if (
            metadata.incident_case_id != cell.incident_case_id
            or metadata.run_id != cell.run_id
            or metadata.strategy is not cell.strategy
            or metadata.code_revision != checkout_revision
            or metadata.workspace_dirty
            or metadata.model_base_url != self._manifest.model_configuration.base_url
            or metadata.benchmark_manifest_sha256 != self._manifest.digest()
            or metadata.evaluation_status is not evaluation.status
        ):
            self._fail(f"artifact metadata identity does not match manifest: {cell.run_id}")
        if (
            evidence.incident_case_id != cell.incident_case_id
            or evidence.run_id != cell.run_id
            or diagnosis.run_id != cell.run_id
            or evaluation.incident_case_id != cell.incident_case_id
            or evaluation.run_id != cell.run_id
        ):
            self._fail(f"artifact structured identity does not match manifest: {cell.run_id}")
        terminal = trace[-1].event
        if terminal.strategy is not cell.strategy or terminal.status is not diagnosis.status:
            self._fail(f"trace terminal does not match diagnosis: {cell.run_id}")
        evidence_ids = tuple(record.evidence_id for record in evidence.records)
        known_ids = set(evidence_ids)
        referenced_ids = set(diagnosis.evidence_ids)
        referenced_ids.update(
            evidence_id for claim in diagnosis.claims for evidence_id in claim.evidence_ids
        )
        if any(record.run_id != cell.run_id for record in evidence.records):
            self._fail(f"evidence artifact is not run-bound: {cell.run_id}")
        if not referenced_ids.issubset(known_ids):
            self._fail(f"diagnosis references unknown evidence: {cell.run_id}")
        if terminal.evidence_inventory != evidence_ids:
            self._fail(f"trace evidence inventory does not match evidence artifact: {cell.run_id}")
        allowed_tools = set(_EVIDENCE_TOOL_NAMES)
        if cell.strategy is DiagnosticStrategy.NO_TOOL:
            allowed_tools.clear()
        elif cell.strategy is DiagnosticStrategy.KERNEL_NO_LINEAGE:
            allowed_tools.remove("get_dbt_lineage")
        elif cell.strategy is DiagnosticStrategy.KERNEL_NO_SCHEMA:
            allowed_tools.remove("get_relation_schema")
        if any(
            isinstance(item.event, ToolTraceEvent) and item.event.tool_name not in allowed_tools
            for item in trace
        ):
            self._fail(f"trace uses a tool outside the strategy allowlist: {cell.run_id}")
        kernel_events = tuple(
            item.event for item in trace if isinstance(item.event, KernelStateTraceEvent)
        )
        if cell.strategy in KERNEL_STRATEGIES:
            if len(kernel_events) != 1 or trace[-2].event is not kernel_events[0]:
                self._fail(f"Kernel trace is not explicitly stateful: {cell.run_id}")
            try:
                state = InvestigationState.model_validate(kernel_events[0].state)
            except Exception as exc:
                raise BenchmarkReportError(
                    f"Kernel InvestigationState is invalid: {cell.run_id}"
                ) from exc
            if state.run_id != cell.run_id:
                self._fail(f"Kernel InvestigationState run_id mismatch: {cell.run_id}")
        elif kernel_events:
            self._fail(f"non-Kernel trace contains Kernel state: {cell.run_id}")
        if cell.strategy is DiagnosticStrategy.FIXED_RULE:
            metrics = metadata.diagnosis_metrics
            if metrics.model_requests or metrics.input_tokens or metrics.output_tokens:
                self._fail(f"fixed-rule artifact reports model usage: {cell.run_id}")
        return metadata, diagnosis, evaluation, trace, evidence.records

    def _validate(self) -> tuple[list[dict[str, Any]], BenchmarkDoctorReceipt]:
        if self._suite_root.is_symlink() or not self._suite_root.is_dir():
            self._fail("benchmark suite root is missing or invalid")
        if (
            self._manifest.total_cells != 106
            or self._manifest.model_backed_count != 94
            or self._manifest.fixed_rule_count != 12
        ):
            self._fail("manifest does not contain the frozen 106-cell P1 schedule")
        try:
            running_result_inputs = result_inputs_for_project(self._project_root)
        except BenchmarkManifestError as exc:
            raise BenchmarkReportError("result inputs cannot be read from the project") from exc
        if running_result_inputs != self._manifest.result_inputs:
            # The claim and citation metrics re-derive the evaluator's per-claim
            # verdicts; if the running evaluator, schemas or contracts are not
            # the ones the manifest froze, those metrics would mix identities.
            self._fail("running result inputs do not match the frozen manifest identity")
        doctor = self._doctor()
        ledger = self._ledger()
        catalog = {entry.incident_case_id: entry for entry in self._manifest.scenario_catalog}
        records: list[dict[str, Any]] = []
        for index, cell in enumerate(self._manifest.cells):
            metadata, diagnosis, evaluation, trace, evidence_records = self._bundle(
                cell, doctor.checkout_revision
            )
            scenario = self._scenario(cell.incident_case_id, catalog)
            terminal = ledger[index * 2 + 1]
            if (terminal.state == "COMPLETED") != (evaluation.status.value == "PASSED"):
                self._fail(f"ledger terminal state does not match evaluation: {cell.run_id}")
            records.append(
                {
                    "cell": cell,
                    "metadata": metadata,
                    "diagnosis": diagnosis,
                    "evaluation": evaluation,
                    "trace": trace,
                    "scenario": scenario,
                    "records": evidence_records,
                    "claim_verdicts": claim_support_verdicts(scenario, diagnosis, evidence_records),
                    "ledger": ledger[index * 2 + 1],
                    "environment_gates": tuple(
                        {
                            "run_id": cell.run_id,
                            "gate": check.code.value,
                            "reason_code": check.reason_code,
                        }
                        for check in _failed_applicable(evaluation, _ENVIRONMENT_GATES)
                    ),
                    "invalid_gates": tuple(
                        [
                            {
                                "run_id": cell.run_id,
                                "gate": check.code.value,
                                "reason_code": check.reason_code,
                            }
                            for check in _failed_applicable(evaluation, _SAFETY_GATES)
                        ]
                        + [
                            {
                                "run_id": cell.run_id,
                                "gate": check.code.value,
                                "reason_code": check.reason_code,
                            }
                            for check in _failed_controller_gates(evaluation)
                        ]
                    ),
                }
            )
        return records, doctor

    def summary(
        self, records: list[dict[str, Any]], doctor: BenchmarkDoctorReceipt
    ) -> dict[str, Any]:
        groups = {
            strategy: [item for item in records if item["cell"].strategy is strategy]
            for strategy in DiagnosticStrategy
            if any(item["cell"].strategy is strategy for item in records)
        }
        strategy_metrics: dict[str, Any] = {}
        for strategy, items in groups.items():
            policy = next(
                item for item in self._manifest.policies if item.strategy is strategy
            )
            trace_schema = trace_schema_for_policy_identity(policy.policy_identity)
            diagnosis_run_schema = {
                "p1.trace.v1": "p1.diagnosis.v1",
                "p1.trace.v2": "p1.diagnosis_run.v2",
                "p1.trace.v3": "p1.diagnosis_run.v3",
            }[trace_schema]
            strategy_metrics[strategy.value] = {
                "cells": len(items),
                "completed": sum(item["ledger"].state == "COMPLETED" for item in items),
                "failed": sum(item["ledger"].state == "FAILED" for item in items),
                **(_metric_values(items) if strategy in MAIN_STRATEGIES else {}),
                "efficiency": _efficiency(items) if strategy in MAIN_STRATEGIES else None,
                "refusal_witness_sources": _refusal_witness_source_counts(
                    items, diagnosis_run_schema
                ),
                "reliability": self._reliability(strategy, items),
            }
        invalid_gates = sorted(
            (gate for item in records for gate in item["invalid_gates"]),
            key=lambda gate: (gate["run_id"], gate["gate"], gate["reason_code"]),
        )
        main_values = {
            strategy.value: _metric_values(groups.get(strategy, ()))
            for strategy in MAIN_STRATEGIES
        }
        static = main_values[DiagnosticStrategy.STATIC_SKILL.value]
        kernel = main_values[DiagnosticStrategy.DIAGNOSTIC_KERNEL.value]
        compare_keys = (
            "paired_success",
            "root_cause_accuracy",
            "unsupported_confirmation_rate",
            "status_accuracy",
            "claim_evidence_validity",
            "no_incident_accuracy",
        )
        kernel_wins = 0
        kernel_losses = 0
        for key in compare_keys:
            static_rate = static[key]["rate"]
            kernel_rate = kernel[key]["rate"]
            if static_rate is None or kernel_rate is None or static_rate == kernel_rate:
                continue
            better_for_kernel = (
                kernel_rate > static_rate
                if key != "unsupported_confirmation_rate"
                else kernel_rate < static_rate
            )
            if better_for_kernel:
                kernel_wins += 1
            else:
                kernel_losses += 1
        core_improved = (
            kernel["paired_success"]["rate"] is not None
            and static["paired_success"]["rate"] is not None
            and kernel["paired_success"]["rate"] > static["paired_success"]["rate"]
        ) or (
            kernel["claim_evidence_validity"]["rate"] is not None
            and static["claim_evidence_validity"]["rate"] is not None
            and kernel["claim_evidence_validity"]["rate"]
            > static["claim_evidence_validity"]["rate"]
        )
        safety_preserved = (
            kernel["root_cause_accuracy"]["rate"] is not None
            and static["root_cause_accuracy"]["rate"] is not None
            and kernel["root_cause_accuracy"]["rate"] >= static["root_cause_accuracy"]["rate"]
            and kernel["unsupported_confirmation_rate"]["rate"] is not None
            and static["unsupported_confirmation_rate"]["rate"] is not None
            and kernel["unsupported_confirmation_rate"]["rate"]
            <= static["unsupported_confirmation_rate"]["rate"]
        )
        if invalid_gates:
            conclusion = ("INVALID", "正式样本存在环境或安全硬门失败，结果无效。")
        elif core_improved and safety_preserved:
            conclusion = ("KERNEL_ADVANTAGE", "Diagnostic Kernel 在当前固定样本上表现出优势。")
        elif kernel_wins and kernel_losses:
            conclusion = ("TRADEOFF", "Static 与 Diagnostic Kernel 指标互有胜负。")
        else:
            conclusion = ("NOT_PROVEN", "当前固定样本尚未证明 Diagnostic Kernel 优势。")
        return {
            "schema_version": "p1.benchmark_summary.v1",
            "manifest_id": self._manifest.manifest_id,
            "manifest_sha256": self._manifest.digest(),
            "implementation_revision": self._manifest.implementation_revision,
            "evaluator_version": EVALUATOR_VERSION,
            "checkout_revision": getattr(doctor, "checkout_revision", None),
            "doctor": {"status": doctor.result.status.value},
            "cells": {
                "total": len(records),
                "model_backed": sum(item["cell"].model_backed for item in records),
                "fixed_rule": sum(
                    item["cell"].strategy is DiagnosticStrategy.FIXED_RULE for item in records
                ),
            },
            "strategies": strategy_metrics,
            "main_metrics": main_values,
            "hard_gates": {
                "doctor_passed": True,
                "ledger_complete": True,
                "artifacts_complete": True,
                "identity_aligned": True,
                "kernel_state_valid": True,
                "fixed_rule_zero_model_usage": True,
                "evaluator_safety": not invalid_gates,
            },
            "invalid_gates": invalid_gates,
            "conclusion": {"status": conclusion[0], "text": conclusion[1]},
        }

    @staticmethod
    def _markdown(summary: dict[str, Any]) -> str:
        def ratio(metric: dict[str, Any]) -> str:
            if metric["rate"] is None:
                return "n/a"
            return (
                f"{metric['successes']}/{metric['total']} ({metric['rate']:.3f}; "
                f"95% CI {metric['lower']:.3f}-{metric['upper']:.3f})"
            )

        rows = [
            "# P1 Benchmark Report",
            "",
            f"- Manifest: `{summary['manifest_id']}`",
            f"- Manifest SHA-256: `{summary['manifest_sha256']}`",
            f"- Implementation revision: `{summary['implementation_revision']}`",
            f"- Checkout revision: `{summary['checkout_revision']}`",
            "",
            "## Conclusion",
            "",
            summary["conclusion"]["text"],
            "",
            "## Coverage",
            "",
                "| Strategy | Cells | Completed (ledger) | Failed (ledger) |",
                "|---|---:|---:|---:|",
        ]
        for strategy, data in summary["strategies"].items():
            rows.append(
                f"| {strategy} | {data['cells']} | {data['completed']} | {data['failed']} |"
            )
        rows.extend(
            [
                "",
                "## Main metrics",
                "",
                "| Strategy | Paired success | Root cause | Unsupported confirmation | "
                "Status | Claim evidence | No incident | Assets macro-F1 |",
                "|---|---|---|---|---|---|---|---:|",
            ]
        )
        for strategy in (
            DiagnosticStrategy.STATIC_SKILL.value,
            DiagnosticStrategy.DIAGNOSTIC_KERNEL.value,
        ):
            metrics = summary["main_metrics"][strategy]
            asset_f1 = metrics["affected_assets_macro_f1"]["value"]
            rows.append(
                "| "
                + " | ".join(
                    [
                        strategy,
                        ratio(metrics["paired_success"]),
                        ratio(metrics["root_cause_accuracy"]),
                        ratio(metrics["unsupported_confirmation_rate"]),
                        ratio(metrics["status_accuracy"]),
                        ratio(metrics["claim_evidence_validity"]),
                        ratio(metrics["no_incident_accuracy"]),
                        "n/a" if asset_f1 is None else f"{asset_f1:.3f}",
                    ]
                )
                + " |"
            )
        def diagnostic(metric: dict[str, Any]) -> str:
            if metric["rate"] is None:
                return "n/a"
            return f"{metric['numerator']}/{metric['denominator']} ({metric['rate']:.3f})"

        main_strategies = (
            DiagnosticStrategy.STATIC_SKILL.value,
            DiagnosticStrategy.DIAGNOSTIC_KERNEL.value,
        )
        rows.extend(
            [
                "",
                "## Abstention and status",
                "",
                "Invalid environment cells (failed scenario-verification, run-scope or recovery "
                "gates) are excluded from every set below and counted separately. A MODEL_ERROR "
                "run stays in its set's denominator and is never counted as an abstention, and an "
                "agent-side rule violation — a fabricated citation, a tool outside the allowlist, "
                "a write attempt — is a failed trial, never an invalid sample. An empty "
                "denominator renders as n/a, never as 0 or 100%.",
                "",
                "| Strategy | Confirmable over-abstention | Healthy over-abstention | "
                "Qualified abstention | Abstention precision | Abstention recall | "
                "Model errors (valid cells) | Valid cells | Invalid excluded |",
                "|---|---|---|---|---|---|---:|---:|---:|",
            ]
        )
        for strategy in main_strategies:
            metrics = summary["main_metrics"][strategy]
            abstention = metrics["abstention"]
            rows.append(
                "| "
                + " | ".join(
                    [
                        strategy,
                        diagnostic(abstention["confirmable_over_abstention"]),
                        diagnostic(abstention["health_over_abstention"]),
                        diagnostic(abstention["qualified_abstention"]),
                        diagnostic(abstention["abstention_precision"]),
                        diagnostic(abstention["abstention_recall"]),
                        diagnostic(metrics["status_confusion"]["model_error_rate"]),
                        str(abstention["cells"]["valid"]),
                        str(abstention["cells"]["invalid_excluded"]),
                    ]
                )
                + " |"
            )
        rows.extend(
            [
                "",
                "### Expected × actual status (valid cells)",
                "",
                "| Strategy | Expected | CONFIRMED | INSUFFICIENT_EVIDENCE | NO_INCIDENT | "
                "MODEL_ERROR |",
                "|---|---|---:|---:|---:|---:|",
            ]
        )
        for strategy in main_strategies:
            confusion = summary["main_metrics"][strategy]["status_confusion"]
            for expected in confusion["expected_states"]:
                counts = confusion["matrix"][expected]
                rows.append(
                    "| "
                    + " | ".join(
                        [
                            strategy,
                            expected,
                            str(counts["CONFIRMED"]),
                            str(counts["INSUFFICIENT_EVIDENCE"]),
                            str(counts["NO_INCIDENT"]),
                            str(counts["MODEL_ERROR"]),
                        ]
                    )
                    + " |"
                )
        rows.extend(
            [
                "",
                "## Claim and citation quality",
                "",
                "Coverage counts only structured claims whose kind has a deterministic support "
                "rule under the contract's expected status; other kinds are listed below and "
                "never scored as unsupported. Redundancy is not an error: removing a citation "
                "without changing the support verdict is reported, never penalized. Citation "
                "existence is also a hard prerequisite of the suite validation, so it reports "
                "1.0 for every suite the report accepts; it stays as an explicit invariant and "
                "as the denominator context for single-record support.",
                "",
                "| Strategy | Claim support coverage | Citation existence | "
                "Single-record citation support | Load-bearing citations | Redundant citations |",
                "|---|---|---|---|---|---|",
            ]
        )
        excluded_notes: list[str] = []
        for strategy in main_strategies:
            metrics = summary["main_metrics"][strategy]
            claims = metrics["claim_support"]
            citations = metrics["citation_quality"]
            rows.append(
                "| "
                + " | ".join(
                    [
                        strategy,
                        diagnostic(claims["support_coverage"]),
                        diagnostic(citations["existence"]),
                        diagnostic(citations["single_record_support"]),
                        diagnostic(citations["redundancy"]["load_bearing"]),
                        str(citations["redundancy"]["redundant_citations"]),
                    ]
                )
                + " |"
            )
            excluded = ", ".join(
                f"{kind}={count}"
                for kind, count in (
                    *claims["inapplicable_claim_types"].items(),
                    *claims["unsupported_claim_types"].items(),
                )
            )
            excluded_notes.append(
                f"- `{strategy}`: excluded claim types: {excluded or 'none'}; "
                f"health-claim citations excluded from single-record support: "
                f"{citations['health_claim_citations']}"
            )
        rows.extend(["", *excluded_notes])
        def witness_ratio(metric: dict[str, Any]) -> str:
            if metric["rate"] is None:
                return "n/a"
            return f"{metric['numerator']}/{metric['denominator']} ({metric['rate']:.3f})"

        rows.extend(
            [
                "",
                "## Refusal witness sources",
                "",
                "These are descriptive per-gap metrics. The denominator contains declared "
                "tool-bound contract gaps in insufficient-evidence cells when that individual "
                "gap is present in the diagnosis. A different gap in the same cell does not "
                "remove a matched gap from the denominator; full gap-set mismatches and "
                "inapplicable gaps are reported separately.",
                "",
                "| Strategy | Controller precheck | Evidence backend | Legacy unattributed | "
                "Eligible gaps | Gap-set mismatch cells | Inapplicable gaps |",
                "|---|---|---|---|---:|---:|---:|",
            ]
        )
        witness_notes: list[str] = []
        for strategy, data in summary["strategies"].items():
            sources = data["refusal_witness_sources"]
            rows.append(
                "| "
                + " | ".join(
                    [
                        strategy,
                        witness_ratio(sources["CONTROLLER_PRECHECK"]),
                        witness_ratio(sources["EVIDENCE_BACKEND"]),
                        witness_ratio(sources["LEGACY_UNATTRIBUTED"]),
                        str(sources["eligible_tool_bound_gaps"]),
                        str(sources["gap_set_mismatch_cells"]),
                        str(sources["inapplicable_tool_bound_gaps"]),
                    ]
                )
                + " |"
            )
            reasons = ", ".join(
                f"{reason}={count}"
                for reason, count in sources["inapplicable_reasons"].items()
                if count
            )
            witness_notes.append(
                f"- `{strategy}`: expected declared tool-bound gaps="
                f"{sources['expected_tool_bound_gaps']}; inapplicable reasons="
                f"{reasons or 'none'}; zero-denominator rates use "
                f"`{sources['CONTROLLER_PRECHECK']['zero_denominator_reason'] or 'none'}`."
            )
        rows.extend(["", *witness_notes])
        rows.extend(
            [
                "",
                "## Repeat reliability (p1.reliability.v1)",
                "",
                "The planned repetition count comes from the frozen schedule; a group is complete "
                "only when every planned repeat produced a valid sample, and an incomplete group "
                "reports no main pass^k (its complete-subset analysis, with coverage, is in "
                "summary.json, never as the headline). pass^k = C(s, k)/C(n, k) is the probability "
                "that k of k repetitions succeed, not \"at least one of k\". MODEL_ERROR counts as "
                "a failed trial; an invalid environment sample counts as neither. The macro "
                "average covers complete groups only and never pools trials across scenarios, "
                "strategies or versions.",
                "",
                "| Strategy | Groups | Complete | Incomplete | Unscheduled | Macro pass^1 | "
                "Macro pass^2 | Macro pass^3 |",
                "|---|---:|---:|---:|---:|---|---|---|",
            ]
        )

        def macro_cell(metric: dict[str, Any]) -> str:
            if metric["value"] is None:
                return "n/a"
            return (
                f"{metric['value']:.3f} ({metric['groups']} groups / {metric['trials']} trials)"
            )

        for strategy, data in summary["strategies"].items():
            reliability = data["reliability"]
            macro = reliability["macro_pass_hat"]
            rows.append(
                "| "
                + " | ".join(
                    [
                        strategy,
                        str(reliability["groups_total"]),
                        str(reliability["groups_complete"]),
                        str(reliability["groups_incomplete"]),
                        str(reliability["groups_unscheduled"]),
                        macro_cell(macro["1"]),
                        macro_cell(macro["2"]),
                        macro_cell(macro["3"]),
                    ]
                )
                + " |"
            )
        incomplete = [
            (strategy, group)
            for strategy, data in summary["strategies"].items()
            for group in data["reliability"]["groups"]
            if group["in_schedule"] and not group["complete"]
        ]
        if incomplete:
            rows.extend(["", "### Incomplete groups", ""])
            rows.extend(
                f"- `{strategy}` `{group['group_id']}`: missing "
                f"{list(group['missing_repeat_indices'])}, invalid "
                f"{list(group['invalid_repeat_indices'])} (valid "
                f"{group['valid_trials']}/{group['planned_repetitions']})"
                for strategy, group in incomplete
            )
        rows.extend(
            [
                "",
                "## Main-strategy efficiency",
                "",
                "Only evaluator-passing cells contribute tool/token/request/elapsed summaries; "
                "failure rates retain the full strategy denominator.",
                "",
                "| Strategy | Successful tools median | Exact duplicates | Equivalent calls | "
                "Post-decisive median | Model errors (all cells) | Timeouts | Requests total |",
                "|---|---:|---:|---:|---:|---|---|---:|",
            ]
        )
        for strategy in (
            DiagnosticStrategy.STATIC_SKILL.value,
            DiagnosticStrategy.DIAGNOSTIC_KERNEL.value,
        ):
            efficiency = summary["strategies"][strategy]["efficiency"]
            rows.append(
                "| "
                + " | ".join(
                    [
                        strategy,
                        str(efficiency["successful_tools_median"]),
                        str(efficiency["exact_duplicate_calls"]),
                        str(efficiency["equivalent_calls"]),
                        str(efficiency["post_decisive_tool_calls_median"]),
                        ratio(efficiency["model_error_rate"]),
                        ratio(efficiency["timeout_rate"]),
                        str(efficiency["requests"]["total"]),
                    ]
                )
                + " |"
            )
        rows.extend(
            [
                "",
                "### Failure and usage details",
                "",
                "| Strategy | Request-limit exhaustion | Tool-limit exhaustion | "
                "Input tokens | Output tokens | Elapsed median ms |",
                "|---|---|---|---:|---:|---:|",
            ]
        )
        for strategy in (
            DiagnosticStrategy.STATIC_SKILL.value,
            DiagnosticStrategy.DIAGNOSTIC_KERNEL.value,
        ):
            efficiency = summary["strategies"][strategy]["efficiency"]
            exhaustion = efficiency["budget_exhaustion_rate"]
            rows.append(
                "| "
                + " | ".join(
                    [
                        strategy,
                        ratio(exhaustion["model_request_limit"]),
                        ratio(exhaustion["model_tool_call_limit"]),
                        str(efficiency["tokens"]["input_total"]),
                        str(efficiency["tokens"]["output_total"]),
                        str(efficiency["elapsed_ms"]["median"]),
                    ]
                )
                + " |"
            )
        rows.extend(
            [
                "",
                "## Hard gates",
                "",
                "| Gate | Passed |",
                "|---|---|",
            ]
        )
        for gate, passed in summary["hard_gates"].items():
            rows.append(f"| {gate} | {'yes' if passed else 'no'} |")
        if summary["invalid_gates"]:
            rows.extend(["", "### Invalid gates", ""])
            rows.extend(
                f"- `{item['run_id']}`: `{item['gate']}` (`{item['reason_code']}`)"
                for item in summary["invalid_gates"]
            )
        rows.extend(
            [
                "",
                "## Auxiliary policies",
                "",
                "No Tool、Kernel 消融和 Fixed Rule 单独列示，不纳入 Static/Kernel 优势判定。",
                "",
                "| Policy | Cells | Completed (ledger) | Failed (ledger) |",
                "|---|---:|---:|---:|",
            ]
        )
        for strategy in (
            DiagnosticStrategy.NO_TOOL.value,
            DiagnosticStrategy.KERNEL_NO_LINEAGE.value,
            DiagnosticStrategy.KERNEL_NO_SCHEMA.value,
            DiagnosticStrategy.FIXED_RULE.value,
        ):
            data = summary["strategies"][strategy]
            rows.append(
                f"| {strategy} | {data['cells']} | {data['completed']} | {data['failed']} |"
            )
        return "\n".join(rows) + "\n"

    @staticmethod
    def _write_if_identical(path: Path, payload: bytes) -> None:
        if path.is_symlink():
            raise BenchmarkReportError(f"report output is a symlink: {path.name}")
        if path.exists():
            if not path.is_file() or path.read_bytes() != payload:
                raise BenchmarkReportError(f"report output already differs: {path.name}")
            return
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.rename(path)
        except FileExistsError:
            if path.is_file() and path.read_bytes() == payload:
                return
            raise BenchmarkReportError(f"report output already differs: {path.name}") from None
        except OSError as exc:
            raise BenchmarkReportError(f"cannot write report output: {path.name}") from exc
        finally:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)

    def write(self) -> tuple[Path, Path]:
        subset_path = self._suite_root / "subset.json"
        if subset_path.is_symlink():
            self._fail("benchmark subset marker must not be a symlink")
        if subset_path.exists():
            self._fail("subset suites cannot produce a formal report")
        records, doctor = self._validate()
        summary = self.summary(records, doctor)
        summary_bytes = (
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        report_bytes = self._markdown(summary).encode("utf-8")
        summary_path = self._suite_root / "summary.json"
        report_path = self._suite_root / "report.md"
        self._write_if_identical(summary_path, summary_bytes)
        self._write_if_identical(report_path, report_bytes)
        return summary_path, report_path


def _load_json_line(line: str) -> Any:
    try:
        return json.loads(
            line,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")),
        )
    except Exception as exc:
        raise BenchmarkReportError("trace contains invalid JSON") from exc


PARTIAL_ANALYSIS_SCHEMA_VERSION = "p1.partial_suite_analysis.v1"


def _terminal_ledger_entries(
    path: Path,
) -> tuple[dict[str, BenchmarkLedgerEntry], tuple[str, ...]]:
    """run_id -> terminal ledger entry, plus any run with conflicting terminals."""

    entries: dict[str, BenchmarkLedgerEntry] = {}
    conflicts: list[str] = []
    if path.is_symlink() or not path.is_file():
        return entries, ()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BenchmarkReportError("benchmark ledger cannot be read") from exc
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = BenchmarkLedgerEntry.model_validate(_load_json_line(line))
        except BenchmarkReportError:
            raise
        except Exception as exc:
            raise BenchmarkReportError("benchmark ledger is invalid") from exc
        if entry.state not in {"COMPLETED", "FAILED"}:
            continue
        previous = entries.get(entry.run_id)
        if previous is not None and previous.state != entry.state:
            conflicts.append(entry.run_id)
        entries[entry.run_id] = entry
    return entries, tuple(sorted(set(conflicts)))


def _read_partial_json(path: Path) -> Any | None:
    """Read one archived file for partial analysis; unusable files return None."""

    if path.is_symlink() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_partial_cell(
    cell: Any,
    manifest: BenchmarkManifest,
    artifact_root: Path,
) -> tuple[tuple[RunMetadata, EvaluationResult, Diagnosis] | None, str | None]:
    """Load and identity-check one cell's archived bundle.

    Every file must parse, validate against its schema and belong to this very
    cell: a substituted evaluation, a bundle from another scenario or run, or a
    structurally broken file all mean "no usable sample for this repeat" and
    never a scored trial for somebody else's result.
    """

    directory = artifact_root / cell.run_id
    raw_metadata = _read_partial_json(directory / "metadata.json")
    raw_evaluation = _read_partial_json(directory / "evaluation.json")
    raw_diagnosis = _read_partial_json(directory / "diagnosis.json")
    if raw_metadata is None or raw_evaluation is None or raw_diagnosis is None:
        return None, "ARTIFACTS_UNREADABLE"
    try:
        metadata = RunMetadata.model_validate(raw_metadata)
        evaluation = EvaluationResult.model_validate(raw_evaluation)
        diagnosis = Diagnosis.model_validate(raw_diagnosis)
    except Exception:
        return None, "ARTIFACTS_INVALID"
    if metadata.benchmark_manifest_sha256 != manifest.digest():
        return None, "MANIFEST_IDENTITY_MISMATCH"
    if (
        metadata.run_id != cell.run_id
        or metadata.incident_case_id != cell.incident_case_id
        or metadata.strategy is not cell.strategy
    ):
        return None, "IDENTITY_MISMATCH"
    if (
        evaluation.run_id != cell.run_id
        or evaluation.incident_case_id != cell.incident_case_id
    ):
        return None, "IDENTITY_MISMATCH"
    if diagnosis.run_id != cell.run_id:
        return None, "IDENTITY_MISMATCH"
    return (metadata, evaluation, diagnosis), None


def analyze_partial_suite(
    manifest: BenchmarkManifest,
    suite_root: Path,
) -> dict[str, Any]:
    """Reliability for a suite that is incomplete, still running or interrupted.

    ``BenchmarkReporter.write`` keeps refusing incomplete suites and stays the
    only formal-report entry point. This read-only analysis exists so the
    protocol's incomplete-group rules are reachable: a repeat that never
    produced a usable sample stays missing, an environment-invalid sample is
    isolated, and nothing is imputed or borrowed from another group.

    Only archived status is read — no evaluator rule is recomputed — but every
    scored sample must be bound to this manifest *and* to the target repeat: the
    ledger entry, metadata, evaluation and diagnosis must all agree with the
    cell's run, scenario, strategy and sequence, and the bundle must carry this
    manifest's digest. A file that parses but has the wrong shape, or a bundle
    belonging to another cell, is marked missing instead of being scored.
    """

    suite = Path(suite_root)
    if suite.is_symlink() or not suite.is_dir():
        raise BenchmarkReportError("benchmark suite root is missing or invalid")
    artifact_root = suite.parent.parent
    entries, conflicts = _terminal_ledger_entries(suite / "ledger.jsonl")
    observed: dict[tuple[str, str], list[ReliabilityTrial]] = {}
    cells: list[dict[str, Any]] = []
    for cell in manifest.cells:
        record: dict[str, Any] = {
            "run_id": cell.run_id,
            "incident_case_id": cell.incident_case_id,
            "strategy": cell.strategy.value,
            "repeat_index": cell.repeat_index,
        }
        entry = entries.get(cell.run_id)
        if entry is None:
            record.update({"state": "MISSING", "reason_code": "NO_TERMINAL_LEDGER_ENTRY"})
            cells.append(record)
            continue
        if cell.run_id in conflicts:
            record.update({"state": "MISSING", "reason_code": "LEDGER_TERMINAL_CONFLICT"})
            cells.append(record)
            continue
        if (
            entry.manifest_id != manifest.manifest_id
            or entry.sequence != cell.sequence
            or entry.incident_case_id != cell.incident_case_id
            or entry.strategy is not cell.strategy
        ):
            record.update({"state": "MISSING", "reason_code": "IDENTITY_MISMATCH"})
            cells.append(record)
            continue
        materials, reason = _load_partial_cell(cell, manifest, artifact_root)
        if reason is not None or materials is None:
            record.update({"state": "MISSING", "reason_code": reason})
            cells.append(record)
            continue
        _, parsed, diagnosis = materials
        expected_state = "COMPLETED" if parsed.status.value == "PASSED" else "FAILED"
        if entry.state != expected_state:
            record.update({"state": "MISSING", "reason_code": "LEDGER_STATE_MISMATCH"})
            cells.append(record)
            continue
        environment_failures = tuple(
            check.code.value for check in _failed_applicable(parsed, _ENVIRONMENT_GATES)
        )
        controller_failures = tuple(check.code.value for check in _failed_controller_gates(parsed))
        record.update(
            {
                "state": "INVALID_SAMPLE" if environment_failures else "SCORED",
                "evaluation_status": parsed.status.value,
                "diagnosis_status": diagnosis.status.value,
                "passed": _evaluation_passed(parsed),
                "environment_gate_failures": environment_failures,
                "controller_gate_failures": controller_failures,
            }
        )
        cells.append(record)
        observed.setdefault((cell.incident_case_id, cell.strategy.value), []).append(
            ReliabilityTrial(
                repeat_index=cell.repeat_index,
                passed=record["passed"],
                invalid=bool(environment_failures),
                status=diagnosis.status.value,
                invalid_gate_codes=environment_failures,
            )
        )
    return {
        "schema_version": PARTIAL_ANALYSIS_SCHEMA_VERSION,
        "manifest_id": manifest.manifest_id,
        "manifest_sha256": manifest.digest(),
        "implementation_revision": manifest.implementation_revision,
        "reliability": reliability_for(
            schedule=(
                (cell.incident_case_id, cell.strategy.value, cell.repeat_index)
                for cell in manifest.cells
            ),
            observed={key: tuple(value) for key, value in observed.items()},
        ),
        "cells": cells,
        "ledger_terminal_conflicts": list(conflicts),
        "note": (
            "partial analysis reads archived status only; it never writes a formal report and "
            "never relaxes the completeness requirements of BenchmarkReporter.write"
        ),
    }


__all__ = [
    "BenchmarkReportError",
    "BenchmarkReporter",
    "PARTIAL_ANALYSIS_SCHEMA_VERSION",
    "analyze_partial_suite",
]
