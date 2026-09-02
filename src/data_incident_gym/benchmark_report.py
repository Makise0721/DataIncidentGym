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
    TraceEnvelope,
)
from data_incident_gym.benchmark_manifest import BenchmarkManifest
from data_incident_gym.benchmark_runner import (
    BenchmarkDoctorReceipt,
    BenchmarkLedgerEntry,
)
from data_incident_gym.diagnosis import (
    KERNEL_STRATEGIES,
    MAIN_STRATEGIES,
    Diagnosis,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    KernelStateTraceEvent,
)
from data_incident_gym.diagnostic_kernel import InvestigationState
from data_incident_gym.evaluation import (
    ControllerCheckCode,
    EvaluationCheckCode,
    EvaluationResult,
)


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
    }


def _efficiency(items: list[dict[str, Any]]) -> dict[str, Any]:
    passed = [item for item in items if item["evaluation"].status.value == "PASSED"]
    exact = 0
    equivalent = 0
    for item in passed:
        tool_calls = [
            event.event for event in item["trace"] if event.event.event_type == "TOOL_CALL"
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
            event.event.state
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
                    event.event.event_type == "TOOL_CALL"
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


class BenchmarkReporter:
    """Validate and aggregate an already completed benchmark suite.

    This class intentionally has no database, model, evaluator, or tool dependencies.
    """

    def __init__(self, manifest: BenchmarkManifest, suite_root: Path) -> None:
        self._manifest = manifest
        self._suite_root = Path(suite_root)

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
        if actual != expected or receipt.result.status.value != "PASSED":
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
    ) -> tuple[RunMetadata, Any, EvaluationResult, tuple[Any, ...]]:
        artifact_root = self._suite_root.parent.parent
        run_path = artifact_root / cell.run_id
        if run_path.is_symlink() or not run_path.is_dir():
            self._fail(f"artifact directory is missing: {cell.run_id}")
        children = tuple(run_path.iterdir())
        if {item.name for item in children} != set(ARTIFACT_FILENAMES) or any(
            item.is_symlink() or not item.is_file() for item in children
        ):
            self._fail(f"artifact bundle is not exactly six regular files: {cell.run_id}")
        try:
            metadata = RunMetadata.model_validate(_load_json(run_path / "metadata.json"))
            evidence = EvidenceArtifact.model_validate(_load_json(run_path / "evidence.json"))
            diagnosis = Diagnosis.model_validate(_load_json(run_path / "diagnosis.json"))
            evaluation = EvaluationResult.model_validate(_load_json(run_path / "evaluation.json"))
            trace = tuple(
                TraceEnvelope.model_validate(_load_json_line(line))
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
        if cell.strategy in {DiagnosticStrategy.NO_TOOL, DiagnosticStrategy.FIXED_RULE}:
            allowed_tools.clear()
        elif cell.strategy is DiagnosticStrategy.KERNEL_NO_LINEAGE:
            allowed_tools.remove("get_dbt_lineage")
        elif cell.strategy is DiagnosticStrategy.KERNEL_NO_SCHEMA:
            allowed_tools.remove("get_relation_schema")
        if any(
            item.event.event_type == "TOOL_CALL" and item.event.tool_name not in allowed_tools
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
        return metadata, diagnosis, evaluation, trace

    def _validate(self) -> tuple[list[dict[str, Any]], BenchmarkDoctorReceipt]:
        if self._suite_root.is_symlink() or not self._suite_root.is_dir():
            self._fail("benchmark suite root is missing or invalid")
        if (
            self._manifest.total_cells != 106
            or self._manifest.model_backed_count != 94
            or self._manifest.fixed_rule_count != 12
        ):
            self._fail("manifest does not contain the frozen 106-cell P1 schedule")
        doctor = self._doctor()
        ledger = self._ledger()
        records: list[dict[str, Any]] = []
        for index, cell in enumerate(self._manifest.cells):
            metadata, diagnosis, evaluation, trace = self._bundle(cell, doctor.checkout_revision)
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
                    "ledger": ledger[index * 2 + 1],
                    "invalid_gates": tuple(
                        [
                            {
                                "run_id": cell.run_id,
                                "gate": check.code.value,
                                "reason_code": check.reason_code,
                            }
                            for check in evaluation.checks
                            if check.applicability.value == "APPLICABLE"
                            and not check.passed
                            and check.code in _SAFETY_GATES
                        ]
                        + [
                            {
                                "run_id": cell.run_id,
                                "gate": check.code.value,
                                "reason_code": check.reason_code,
                            }
                            for check in getattr(evaluation, "controller_checks", ())
                            if not check.passed and check.code in _CONTROLLER_SAFETY_GATES
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
        }
        strategy_metrics = {
            strategy.value: {
                "cells": len(items),
                "completed": sum(item["ledger"].state == "COMPLETED" for item in items),
                "failed": sum(item["ledger"].state == "FAILED" for item in items),
                **(_metric_values(items) if strategy in MAIN_STRATEGIES else {}),
                "efficiency": _efficiency(items) if strategy in MAIN_STRATEGIES else None,
            }
            for strategy, items in groups.items()
        }
        invalid_gates = sorted(
            (gate for item in records for gate in item["invalid_gates"]),
            key=lambda gate: (gate["run_id"], gate["gate"], gate["reason_code"]),
        )
        main_values = {
            strategy.value: _metric_values(groups[strategy]) for strategy in MAIN_STRATEGIES
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
            "| Strategy | Cells | Completed | Failed |",
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
        rows.extend(
            [
                "",
                "## Main-strategy efficiency",
                "",
                "Only evaluator-passing cells contribute tool/token/request/elapsed summaries; "
                "failure rates retain the full strategy denominator.",
                "",
                "| Strategy | Successful tools median | Exact duplicates | Equivalent calls | "
                "Post-decisive median | Model errors | Timeouts | Requests total |",
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
                "| Policy | Cells | Completed | Failed |",
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


__all__ = ["BenchmarkReportError", "BenchmarkReporter"]
