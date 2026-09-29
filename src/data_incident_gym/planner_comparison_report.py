"""Strict offline report for a completed 108-cell planner comparison."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from data_incident_gym.artifacts import ARTIFACT_FILENAMES, RecoveryStatus
from data_incident_gym.benchmark_manifest import FORMAL_SCENARIO_IDS, BenchmarkManifestError
from data_incident_gym.benchmark_report import (
    _ENVIRONMENT_GATES,
    BenchmarkReporter,
    BenchmarkReportError,
    _evaluation_passed,
    _failed_applicable,
    _metric_values,
)
from data_incident_gym.benchmark_runner import BenchmarkLedgerEntry
from data_incident_gym.diagnosis import (
    DiagnosisStatus,
    DiagnosticStrategy,
    EvidenceGateTraceEvent,
    ModelProtocolTraceEvent,
    PlanTraceEvent,
    ToolTraceEvent,
)
from data_incident_gym.evaluation import (
    DeterministicEvaluator,
    EvaluationCheckCode,
    EvaluationStatus,
    claim_support_verdicts,
)
from data_incident_gym.evaluation_inputs import (
    EvaluationInputsError,
    default_evaluator_identity,
    load_evaluation_input_bundle,
    verification_from_payload,
)
from data_incident_gym.planner_comparison_manifest import (
    EXPERIMENT_STRATEGIES,
    PlannerComparisonManifest,
    PlannerComparisonManifestV2,
    verify_experiment_manifest,
)
from data_incident_gym.planner_probe_receipt import (
    PLANNER_PROBE_RECEIPT_FILENAME,
    PlannerProbeReceiptError,
    load_planner_probe_receipt,
)
from data_incident_gym.quality_baseline import QualityBaselineError, verify_scoring_identity
from data_incident_gym.reliability import ReliabilityError, ReliabilityTrial, reliability_for
from data_incident_gym.scenarios import ScenarioError, load_scenario_spec

PLANNER_STRATEGY = DiagnosticStrategy.EVIDENCE_PLANNER
KERNEL_STRATEGY = DiagnosticStrategy.DIAGNOSTIC_KERNEL
STATIC_STRATEGY = DiagnosticStrategy.STATIC_SKILL
_EXPECTED_LAYER_COUNTS = {
    DiagnosisStatus.CONFIRMED.value: 5,
    DiagnosisStatus.INSUFFICIENT_EVIDENCE.value: 5,
    DiagnosisStatus.NO_INCIDENT.value: 2,
}
_TRANSPORT_5XX = re.compile(r"^HTTP_5[0-9]{2}$")


class PlannerComparisonReportError(RuntimeError):
    """Raised when a planner-comparison archive is incomplete or inconsistent."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metric_dict(value: Any) -> dict[str, int]:
    return dict(sorted(Counter(value).items()))


def _trace_events(item: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(envelope.event for envelope in item["trace"])


def _retry_observations(items: list[dict[str, Any]]) -> dict[str, Any]:
    events = tuple(event for item in items for event in _trace_events(item))
    protocol_events = tuple(
        event for event in events if isinstance(event, ModelProtocolTraceEvent)
    )
    retry_values = [
        event.output_retry_used
        for event in protocol_events
        if event.output_retry_used is not None
    ]
    return {
        "model_protocol": {
            "status": "INCOMPLETE",
            "output_retry_used_observations": retry_values or None,
            "events_without_output_retry_used": len(protocol_events) - len(retry_values),
        },
        "evidence_gate_refusal_events": sum(
            isinstance(event, EvidenceGateTraceEvent) and not event.accepted for event in events
        ),
    }


def _transport_kind(value: str | None) -> str:
    if value in {"CONNECTION_ERROR", "TIMEOUT"} or (
        value is not None and _TRANSPORT_5XX.fullmatch(value) is not None
    ):
        return "PAIRED_SENSITIVITY_ELIGIBLE"
    if value == "HTTP_429":
        return "HTTP_429_SEPARATE"
    return "NOT_ATTRIBUTED_AS_TRANSPORT"


class PlannerComparisonReporter:
    """Validate a complete experiment archive before deriving registered metrics.

    The report reads run artifacts, the experiment ledger and scoring-inputs.
    It never invokes a model, database, dbt command or evidence tool. It writes
    only the two derived report files requested by ``write``.
    """

    def __init__(
        self,
        manifest: PlannerComparisonManifest,
        suite_root: Path,
        *,
        project_root: Path,
    ) -> None:
        self._manifest = manifest
        self._suite_root = Path(suite_root)
        self._project_root = Path(project_root)
        self._base = BenchmarkReporter(
            manifest,  # type: ignore[arg-type] -- shared strict six-file reader
            self._suite_root,
            project_root=self._project_root,
        )

    def _fail(self, detail: str) -> None:
        raise PlannerComparisonReportError(detail)

    def _manifest_contract(self) -> None:
        if (
            self._manifest.total_cells != 108
            or self._manifest.model_backed_count != 108
            or tuple(policy.strategy for policy in self._manifest.policies) != EXPERIMENT_STRATEGIES
            or tuple(self._manifest.formal_scenario_ids) != FORMAL_SCENARIO_IDS
        ):
            self._fail("manifest is not the frozen 108-cell planner comparison schedule")

    def _load_cell(
        self,
        cell: Any,
        terminal: BenchmarkLedgerEntry,
        checkout_revision: str,
    ) -> dict[str, Any]:
        if terminal.state == "FAILED" and terminal.reason_code == "RUN_SETUP_ERROR":
            self._fail(f"setup failure blocks formal reporting at seq {cell.sequence}")

        try:
            metadata, diagnosis, archived, trace, evidence_records = self._base._bundle(
                cell, checkout_revision
            )
        except (BenchmarkReportError, EvaluationInputsError, OSError, ValueError) as exc:
            raise PlannerComparisonReportError(
                f"strict archive loading failed at seq {cell.sequence}: {exc}"
            ) from exc

        if any(
            isinstance(envelope.event, EvidenceGateTraceEvent)
            and envelope.event.reason_code == "GATE_INTERNAL_ERROR"
            for envelope in trace
        ):
            self._fail(f"trace records GATE_INTERNAL_ERROR at seq {cell.sequence}")

        try:
            bundle = load_evaluation_input_bundle(self._project_root, cell.run_id)
        except (EvaluationInputsError, OSError, ValueError) as exc:
            raise PlannerComparisonReportError(
                f"strict archive loading failed at seq {cell.sequence}: {exc}"
            ) from exc

        run_dir = self._project_root / "artifacts" / cell.run_id
        recorded_artifacts = {item.name: item.sha256 for item in bundle.artifact_digests}
        if set(recorded_artifacts) != set(ARTIFACT_FILENAMES):
            self._fail(f"scoring-input artifact identity is incomplete at seq {cell.sequence}")
        for filename in ARTIFACT_FILENAMES:
            artifact_path = run_dir / filename
            if artifact_path.is_symlink() or not artifact_path.is_file():
                self._fail(
                    f"artifact file is missing or invalid at seq {cell.sequence}: {filename}"
                )
            if _sha256(artifact_path) != recorded_artifacts[filename]:
                self._fail(f"artifact digest does not match scoring-inputs at seq {cell.sequence}")

        try:
            scenario = load_scenario_spec(cell.incident_case_id, self._project_root)
        except ScenarioError as exc:
            raise PlannerComparisonReportError(
                f"scenario contract cannot be loaded at seq {cell.sequence}"
            ) from exc
        catalog_entry = next(
            (
                entry
                for entry in self._manifest.scenario_catalog
                if entry.incident_case_id == cell.incident_case_id
            ),
            None,
        )
        if catalog_entry is None or scenario.digest() != catalog_entry.scenario_spec_sha256:
            self._fail(
                f"current scenario digest does not match the manifest at seq {cell.sequence}"
            )
        if (
            bundle.run_id != cell.run_id
            or bundle.incident_case_id != cell.incident_case_id
            or bundle.strategy is not cell.strategy
            or bundle.scenario_digest != catalog_entry.scenario_spec_sha256
            or bundle.scenario != scenario
            or bundle.diagnosis_run.diagnosis != diagnosis
            or bundle.diagnosis_run.evidence_records != evidence_records
            or tuple(envelope.event.model_dump(mode="json") for envelope in trace)
            != tuple(event.model_dump(mode="json") for event in bundle.diagnosis_run.trace)
            or bundle.diagnosis_run.metrics != metadata.diagnosis_metrics
            or bundle.original_evaluator != default_evaluator_identity()
        ):
            self._fail(
                f"scoring-input identity does not match the archived cell at seq {cell.sequence}"
            )

        policy = next(item for item in self._manifest.policies if item.strategy is cell.strategy)
        if (
            metadata.code_revision != checkout_revision
            or metadata.workspace_dirty
            or metadata.provider != self._manifest.model_configuration.provider
            or metadata.model != self._manifest.model_configuration.model
            or metadata.model_base_url != self._manifest.model_configuration.base_url
            or metadata.base_prompt_version != policy.policy_identity.base_prompt_version
            or metadata.base_prompt_sha256 != policy.policy_identity.base_prompt_sha256
            or metadata.strategy_prompt_version != policy.policy_identity.strategy_prompt_version
            or metadata.strategy_prompt_sha256 != policy.policy_identity.strategy_prompt_sha256
            or metadata.controller_protocol_version
            != policy.policy_identity.controller_protocol_version
            or metadata.controller_protocol_sha256
            != policy.policy_identity.controller_protocol_sha256
            or metadata.tool_schema_sha256 != policy.policy_identity.tool_schema_sha256
            or metadata.expected_status != scenario.expected_status
        ):
            self._fail(
                f"metadata policy/model/revision identity does not match seq {cell.sequence}"
            )

        recovery_check = next(
            item for item in archived.checks if item.code is EvaluationCheckCode.RECOVERY_HEALTHY
        )
        if (
            metadata.recovery_status is not RecoveryStatus.HEALTHY
            or bundle.recovery.incident_case_id != cell.incident_case_id
            or not bundle.recovery.recovered
            or not recovery_check.passed
        ):
            self._fail(f"recovery identity or status failed at seq {cell.sequence}")
        if _failed_applicable(archived, _ENVIRONMENT_GATES):
            self._fail(f"an environment gate failed at seq {cell.sequence}")
        if any(
            "INTERNAL_ERROR" in check.code.value or "INTERNAL_ERROR" in check.reason_code
            for check in archived.controller_checks
        ):
            self._fail(f"controller reported an internal gate error at seq {cell.sequence}")

        if (terminal.state == "COMPLETED") != (archived.status is EvaluationStatus.PASSED):
            self._fail(f"ledger terminal does not match evaluator status at seq {cell.sequence}")
        if terminal.state == "FAILED" and terminal.reason_code != "EVALUATION_FAILED":
            self._fail(f"ledger terminal reason is not reportable at seq {cell.sequence}")

        try:
            recomputed = DeterministicEvaluator.evaluate(
                bundle.scenario,
                verification_from_payload(bundle.verification),
                bundle.diagnosis_run,
                recovery_succeeded=bundle.recovery.recovered,
            )
        except Exception as exc:
            raise PlannerComparisonReportError(
                f"evaluator could not recompute seq {cell.sequence}"
            ) from exc
        if recomputed != archived:
            self._fail(f"complete EvaluationResult recomputation differs at seq {cell.sequence}")

        controller_failures = tuple(
            check.code.value for check in archived.controller_checks if not check.passed
        )
        environment_gates = tuple(
            {
                "run_id": cell.run_id,
                "gate": check.code.value,
                "reason_code": check.reason_code,
            }
            for check in _failed_applicable(archived, _ENVIRONMENT_GATES)
        )
        item = {
            "cell": cell,
            "metadata": metadata,
            "diagnosis": diagnosis,
            "evaluation": archived,
            "trace": trace,
            "scenario": scenario,
            "records": evidence_records,
            "claim_verdicts": claim_support_verdicts(scenario, diagnosis, evidence_records),
            "ledger": terminal,
            "environment_gates": environment_gates,
            "controller_failures": controller_failures,
            "recovery_status": metadata.recovery_status.value,
        }
        return item

    def _planner_metrics(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        plan_kind_counts: Counter[str] = Counter()
        step_rejections: Counter[str] = Counter()
        close_rejections: Counter[str] = Counter()
        refusal_codes: Counter[str] = Counter()
        plan_refusals_used = 0
        blocked_operations = 0
        registered = 0
        satisfied = 0
        revoked = 0
        opened = 0
        satisfied_uncovered: list[dict[str, Any]] = []
        step_proposals = 0
        step_executions = 0
        close_requests = 0
        state_events = 0
        tool_receipts = 0
        successful_tool_receipts = 0
        rejected_tool_receipts = 0
        backend_rejections: Counter[str] = Counter()
        unclassified_tool_errors: Counter[str] = Counter()
        backend_replans = 0
        backend_replans_changed_obligation = 0
        tool_error_replans = 0
        tool_error_replans_changed_obligation = 0
        plan_events_total = 0
        close_requested_by_outcome: Counter[str] = Counter()
        close_accepted_by_outcome: Counter[str] = Counter()

        for item in items:
            cell = item["cell"]
            events = _trace_events(item)
            plan_events = [event for event in events if isinstance(event, PlanTraceEvent)]
            tools = [event for event in events if isinstance(event, ToolTraceEvent)]
            plan_events_total += len(plan_events)
            plan_kind_counts.update(event.kind for event in plan_events)
            step_events = [event for event in plan_events if event.kind == "STEP"]
            close_events = [event for event in plan_events if event.kind == "CLOSE"]
            states = [event for event in plan_events if event.kind == "STATE"]
            step_proposals += len(step_events)
            close_requests += len(close_events)
            state_events += len(states)
            step_rejections.update(
                event.verdict_code or "MISSING_VERDICT_CODE"
                for event in step_events
                if not event.accepted
            )
            close_rejections.update(
                event.verdict_code or "MISSING_VERDICT_CODE"
                for event in close_events
                if not event.accepted
            )
            close_requested_by_outcome.update(
                event.requested_outcome or "MISSING_OUTCOME" for event in close_events
            )
            close_accepted_by_outcome.update(
                event.requested_outcome or "MISSING_OUTCOME"
                for event in close_events
                if event.accepted
            )
            for event in (*step_events, *close_events):
                if not event.accepted:
                    if event.verdict_code is None or not event.verdict_code.startswith("PLAN_"):
                        self._fail(
                            f"planner refusal lacks a PLAN_* verdict code at seq {cell.sequence}"
                        )
                    refusal_codes[event.verdict_code] += 1
                    if event.verdict_code == "PLAN_OUTPUT_RETRY_EXHAUSTED":
                        blocked_operations += 1
                    else:
                        plan_refusals_used += 1
            if cell.strategy is not PLANNER_STRATEGY:
                if plan_events:
                    self._fail(f"non-planner trace contains PLAN events at seq {cell.sequence}")
                continue

            if len(states) != 1 or plan_events[-1].kind != "STATE":
                self._fail(
                    f"planner trace must end with exactly one PLAN STATE at seq {cell.sequence}"
                )
            state = states[0]
            if state.open_obligations != tuple(
                sorted(
                    obligation.obligation_id
                    for obligation in state.obligations
                    if obligation.status == "OPEN"
                )
            ):
                self._fail(
                    f"planner STATE open-obligation list is inconsistent at seq {cell.sequence}"
                )
            accepted_steps = [event for event in step_events if event.accepted]
            if len(accepted_steps) != len(tools):
                self._fail(f"planner PlanVerdict/ToolReceipt counts differ at seq {cell.sequence}")
            for plan_step, tool in zip(accepted_steps, tools, strict=True):
                if plan_step.tool_name != tool.tool_name:
                    self._fail(
                        f"planner step does not match its ToolReceipt at seq {cell.sequence}"
                    )
            step_executions += len(tools)
            tool_receipts += len(tools)
            successful_tool_receipts += sum(tool.error_code is None for tool in tools)
            rejected_tool_receipts += sum(tool.error_code is not None for tool in tools)

            refused_count = sum(not event.accepted for event in (*step_events, *close_events))
            recorded_refusals = max(
                (event.plan_refusals_used for event in plan_events),
                default=0,
            )
            if recorded_refusals != refused_count - sum(
                event.verdict_code == "PLAN_OUTPUT_RETRY_EXHAUSTED"
                for event in (*step_events, *close_events)
                if not event.accepted
            ):
                self._fail(
                    f"planner refusal counters disagree with PLAN trace at seq {cell.sequence}"
                )

            state_by_id = {obligation.obligation_id: obligation for obligation in state.obligations}
            registered += len(state.obligations)
            satisfied += sum(value.status == "SATISFIED" for value in state.obligations)
            revoked += sum(value.status == "REVOKED" for value in state.obligations)
            opened += sum(value.status == "OPEN" for value in state.obligations)
            final_citations = set(item["diagnosis"].evidence_ids)
            final_citations.update(
                evidence_id
                for claim in item["diagnosis"].claims
                for evidence_id in claim.evidence_ids
            )
            uncovered = sorted(
                obligation_id
                for obligation_id, obligation in state_by_id.items()
                if obligation.status == "SATISFIED"
                and not set(obligation.satisfied_with).issubset(final_citations)
            )
            if uncovered:
                satisfied_uncovered.append(
                    {"sequence": cell.sequence, "run_id": cell.run_id, "obligations": uncovered}
                )

            for step_position, step in enumerate(step_events):
                if not step.accepted:
                    continue
                tool_index = sum(candidate.accepted for candidate in step_events[:step_position])
                tool = tools[tool_index]
                if tool.error_code is None:
                    continue
                later_steps = step_events[step_position + 1 :]
                next_step = later_steps[0] if later_steps else None
                replanned = next_step is not None
                changed = replanned and next_step.obligation_id != step.obligation_id
                tool_error_replans += replanned
                tool_error_replans_changed_obligation += bool(changed)
                origin = getattr(tool, "outcome_origin", None)
                if origin == "EVIDENCE_BACKEND":
                    backend_rejections[tool.error_code] += 1
                    backend_replans += replanned
                    backend_replans_changed_obligation += bool(changed)
                else:
                    unclassified_tool_errors[tool.error_code] += 1

        strategy_cell_counts = {
            strategy.value: sum(item["cell"].strategy is strategy for item in items)
            for strategy in EXPERIMENT_STRATEGIES
        }
        if strategy_cell_counts != {strategy.value: 36 for strategy in EXPERIMENT_STRATEGIES}:
            self._fail("planner report did not load exactly 36 cells per strategy")
        return {
            "planner_plan_trace_events": plan_events_total,
            "planner_plan_events_by_kind": _metric_dict(plan_kind_counts.elements()),
            "planner_step_proposals": step_proposals,
            "planner_step_validation_rejections_by_code": _metric_dict(step_rejections.elements()),
            "planner_step_execution_count": step_executions,
            "planner_plan_refusals_used": plan_refusals_used,
            "planner_operations_blocked_after_budget": blocked_operations,
            "planner_plan_refusal_codes": _metric_dict(refusal_codes.elements()),
            "planner_close_requests": close_requests,
            "planner_close_requested_by_outcome": dict(sorted(close_requested_by_outcome.items())),
            "planner_close_accepted_by_outcome": dict(sorted(close_accepted_by_outcome.items())),
            "planner_close_rejections_by_code": _metric_dict(close_rejections.elements()),
            "planner_state_events": state_events,
            "planner_obligations_registered": registered,
            "planner_obligations_satisfied": satisfied,
            "planner_obligations_revoked": revoked,
            "planner_obligations_open": opened,
            "planner_satisfied_obligations_not_covered_by_final_diagnosis": satisfied_uncovered,
            "planner_tool_receipts": {
                "attempted": tool_receipts,
                "successful": successful_tool_receipts,
                "rejected": rejected_tool_receipts,
                "backend_rejections_by_code": _metric_dict(backend_rejections.elements()),
                "errors_without_archived_backend_origin_by_code": _metric_dict(
                    unclassified_tool_errors.elements()
                ),
                "backend_rejection_followed_by_replan": backend_replans,
                "backend_rejection_followed_by_changed_obligation": (
                    backend_replans_changed_obligation
                ),
                "tool_error_followed_by_replan_origin_unclassified": tool_error_replans,
                "tool_error_followed_by_changed_obligation_origin_unclassified": (
                    tool_error_replans_changed_obligation
                ),
            },
        }

    def analyze(self) -> dict[str, Any]:
        self._manifest_contract()
        try:
            verify_experiment_manifest(self._manifest, project_root=self._project_root)
        except (BenchmarkManifestError, ValueError) as exc:
            raise PlannerComparisonReportError(
                f"manifest or policy identity does not match the current implementation: {exc}"
            ) from exc
        try:
            verify_scoring_identity(self._manifest, self._project_root)  # type: ignore[arg-type]
        except QualityBaselineError as exc:
            raise PlannerComparisonReportError(str(exc)) from exc
        if self._suite_root.is_symlink() or not self._suite_root.is_dir():
            self._fail("experiment suite root is missing or invalid")
        try:
            doctor = self._base._doctor()
            probe = load_planner_probe_receipt(
                self._suite_root / PLANNER_PROBE_RECEIPT_FILENAME,
                self._manifest,
                checkout_revision=doctor.checkout_revision,
            )
            ledger = self._base._ledger()
        except (BenchmarkReportError, PlannerProbeReceiptError) as exc:
            raise PlannerComparisonReportError(str(exc)) from exc
        if len(ledger) != 216:
            self._fail("formal planner report requires exactly 216 ledger rows")
        records: list[dict[str, Any]] = []
        for index, cell in enumerate(self._manifest.cells):
            started, terminal = ledger[index * 2 : index * 2 + 2]
            if started.state != "STARTED" or terminal.state not in {"COMPLETED", "FAILED"}:
                self._fail(f"ledger cell is not terminal at seq {cell.sequence}")
            if started.started_at != terminal.started_at:
                self._fail(f"ledger start time changed at seq {cell.sequence}")
            records.append(self._load_cell(cell, terminal, doctor.checkout_revision))

        by_strategy = {
            strategy: [item for item in records if item["cell"].strategy is strategy]
            for strategy in EXPERIMENT_STRATEGIES
        }
        success = {
            strategy.value: {
                "passed": sum(_evaluation_passed(item["evaluation"]) for item in items),
                "total": len(items),
            }
            for strategy, items in by_strategy.items()
        }
        pair_items: dict[tuple[str, int], dict[DiagnosticStrategy, dict[str, Any]]] = {}
        for item in records:
            cell = item["cell"]
            pair_items.setdefault((cell.incident_case_id, cell.repeat_index), {})[cell.strategy] = (
                item
            )
        paired: list[dict[str, Any]] = []
        pair_outcomes: Counter[str] = Counter()
        for (case_id, repeat), pair in sorted(pair_items.items()):
            planner_item = pair.get(PLANNER_STRATEGY)
            kernel_item = pair.get(KERNEL_STRATEGY)
            if planner_item is None or kernel_item is None:
                self._fail(f"planner/kernel pair is incomplete for {case_id} repeat {repeat}")
            planner_passed = _evaluation_passed(planner_item["evaluation"])
            kernel_passed = _evaluation_passed(kernel_item["evaluation"])
            outcome = (
                "both"
                if planner_passed and kernel_passed
                else "only_planner"
                if planner_passed
                else "only_kernel"
                if kernel_passed
                else "neither"
            )
            pair_outcomes[outcome] += 1
            paired.append(
                {
                    "incident_case_id": case_id,
                    "repeat_index": repeat,
                    "planner_sequence": planner_item["cell"].sequence,
                    "kernel_sequence": kernel_item["cell"].sequence,
                    "planner_passed": planner_passed,
                    "kernel_passed": kernel_passed,
                    "outcome": outcome,
                }
            )
        if len(paired) != 36:
            self._fail("planner/kernel comparison must contain exactly 36 pairs")

        layer_cases: dict[str, set[str]] = {key: set() for key in _EXPECTED_LAYER_COUNTS}
        for item in records:
            expected = item["scenario"].expected_status
            if expected not in layer_cases:
                self._fail(f"unregistered expected-status layer: {expected}")
            layer_cases[expected].add(item["cell"].incident_case_id)
        if {key: len(value) for key, value in layer_cases.items()} != _EXPECTED_LAYER_COUNTS:
            self._fail("scenario strata do not match the preregistered 5/5/2 groups")
        strata: dict[str, Any] = {}
        for expected, case_ids in layer_cases.items():
            layer = [item for item in records if item["cell"].incident_case_id in case_ids]
            strategies: dict[str, Any] = {}
            for strategy, _items in by_strategy.items():
                selected = [item for item in layer if item["cell"].strategy is strategy]
                strategies[strategy.value] = {
                    "passed": sum(_evaluation_passed(item["evaluation"]) for item in selected),
                    "total": len(selected),
                    "error_confirmations": sum(
                        item["diagnosis"].status is DiagnosisStatus.CONFIRMED
                        for item in selected
                        if expected != DiagnosisStatus.CONFIRMED.value
                    ),
                }
            strata[expected] = {
                "case_ids": sorted(case_ids),
                "strategies": strategies,
            }

        planner_only = pair_outcomes["only_planner"]
        kernel_only = pair_outcomes["only_kernel"]
        net_wins = planner_only - kernel_only
        planner_error_confirmations = sum(
            item["diagnosis"].status is DiagnosisStatus.CONFIRMED
            for item in records
            if item["cell"].strategy is PLANNER_STRATEGY
            and item["scenario"].expected_status != DiagnosisStatus.CONFIRMED.value
        )
        kernel_error_confirmations = sum(
            item["diagnosis"].status is DiagnosisStatus.CONFIRMED
            for item in records
            if item["cell"].strategy is KERNEL_STRATEGY
            and item["scenario"].expected_status != DiagnosisStatus.CONFIRMED.value
        )
        planner_not_below_in_any_layer = all(
            strata[layer]["strategies"][PLANNER_STRATEGY.value]["passed"]
            >= strata[layer]["strategies"][KERNEL_STRATEGY.value]["passed"]
            for layer in _EXPECTED_LAYER_COUNTS
        )
        admission_attestation_sha256 = (
            self._manifest.admission_attestation_sha256
            if isinstance(self._manifest, PlannerComparisonManifestV2)
            else None
        )
        if admission_attestation_sha256 is None:
            screening = "NOT_ESTABLISHED_T06_IDENTITY_PENDING"
        elif (
            net_wins >= 4
            and planner_not_below_in_any_layer
            and planner_error_confirmations <= kernel_error_confirmations
        ):
            screening = "POSITIVE_SIGNAL_FOR_SEPARATE_CONFIRMATION"
        elif net_wins <= 0:
            screening = "NO_POSITIVE_SIGNAL"
        else:
            screening = "MIXED_RESULT"

        metrics = {strategy.value: _metric_values(items) for strategy, items in by_strategy.items()}
        reliability: dict[str, Any] = {}
        for strategy, items in by_strategy.items():
            observed: dict[tuple[str, str], list[ReliabilityTrial]] = {}
            for item in items:
                cell = item["cell"]
                observed.setdefault((cell.incident_case_id, cell.strategy.value), []).append(
                    ReliabilityTrial(
                        repeat_index=cell.repeat_index,
                        passed=_evaluation_passed(item["evaluation"]),
                        invalid=False,
                        status=item["diagnosis"].status.value,
                        invalid_gate_codes=(),
                    )
                )
            try:
                reliability[strategy.value] = reliability_for(
                    schedule=(
                        (cell.incident_case_id, cell.strategy.value, cell.repeat_index)
                        for cell in self._manifest.cells
                        if cell.strategy is strategy
                    ),
                    observed={key: tuple(value) for key, value in observed.items()},
                )
            except ReliabilityError as exc:
                raise PlannerComparisonReportError(
                    f"T08 reliability protocol rejected the suite: {exc}"
                ) from None

        transport_cells: list[dict[str, Any]] = []
        sensitivity_pairs: set[tuple[str, int]] = set()
        http429_pairs: set[tuple[str, int]] = set()
        unattributed_model_errors: list[dict[str, Any]] = []
        for item in records:
            cell = item["cell"]
            if cell.strategy not in {PLANNER_STRATEGY, KERNEL_STRATEGY}:
                continue
            events = _trace_events(item)
            protocols = [event for event in events if isinstance(event, ModelProtocolTraceEvent)]
            transports = [
                event.transport_diagnostic for event in protocols if event.transport_diagnostic
            ]
            eligible = [
                value
                for value in transports
                if _transport_kind(value) == "PAIRED_SENSITIVITY_ELIGIBLE"
            ]
            limited = [
                value for value in transports if _transport_kind(value) == "HTTP_429_SEPARATE"
            ]
            if eligible:
                sensitivity_pairs.add((cell.incident_case_id, cell.repeat_index))
            if limited:
                http429_pairs.add((cell.incident_case_id, cell.repeat_index))
            if (
                item["diagnosis"].status is DiagnosisStatus.MODEL_ERROR
                and not eligible
                and not limited
            ):
                unattributed_model_errors.append(
                    {
                        "sequence": cell.sequence,
                        "run_id": cell.run_id,
                        "strategy": cell.strategy.value,
                        "reason": item["diagnosis"].summary,
                    }
                )
            transport_cells.append(
                {
                    "sequence": cell.sequence,
                    "run_id": cell.run_id,
                    "strategy": cell.strategy.value,
                    "diagnosis_status": item["diagnosis"].status.value,
                    "transport_diagnostics": transports,
                    "sensitivity_eligible": bool(eligible),
                    "http_429": bool(limited),
                }
            )
        transport_sensitive = None
        if len(sensitivity_pairs) <= 9:
            retained = [
                item
                for item in paired
                if (item["incident_case_id"], item["repeat_index"]) not in sensitivity_pairs
            ]
            sensitivity_outcomes = Counter(item["outcome"] for item in retained)
            transport_sensitive = {
                "excluded_pairs": len(sensitivity_pairs),
                "retained_pairs": len(retained),
                "outcomes": {
                    key: sensitivity_outcomes[key]
                    for key in ("both", "only_planner", "only_kernel", "neither")
                },
                "net_wins": sensitivity_outcomes["only_planner"]
                - sensitivity_outcomes["only_kernel"],
            }
        else:
            transport_sensitive = {
                "status": "NOT_ESTABLISHED_MORE_THAN_9_PAIRS",
                "excluded_pairs": len(sensitivity_pairs),
                "retained_pairs": 36 - len(sensitivity_pairs),
            }

        costs: dict[str, Any] = {}
        for strategy, items in by_strategy.items():
            totals = {
                "model_requests": sum(
                    item["metadata"].diagnosis_metrics.model_requests for item in items
                ),
                "input_tokens": sum(
                    item["metadata"].diagnosis_metrics.input_tokens for item in items
                ),
                "output_tokens": sum(
                    item["metadata"].diagnosis_metrics.output_tokens for item in items
                ),
                "tool_call_attempts": sum(
                    item["metadata"].diagnosis_metrics.tool_call_attempts for item in items
                ),
                "successful_tool_calls": sum(
                    item["metadata"].diagnosis_metrics.successful_tool_calls for item in items
                ),
                "elapsed_ms": sum(item["metadata"].diagnosis_metrics.elapsed_ms for item in items),
            }
            errors = Counter(
                item["diagnosis"].summary
                for item in items
                if item["diagnosis"].status is DiagnosisStatus.MODEL_ERROR
            )
            costs[strategy.value] = {
                "totals": totals,
                "retry_observations": _retry_observations(items),
                "model_error_causes": dict(sorted(errors.items())),
            }

        return {
            "schema_version": "p1.planner_comparison_report.v1",
            "manifest_id": self._manifest.manifest_id,
            "manifest_sha256": self._manifest.digest(),
            "implementation_revision": self._manifest.implementation_revision,
            "checkout_revision": doctor.checkout_revision,
            "result_inputs_sha256": hashlib.sha256(
                json.dumps(
                    self._manifest.result_inputs.model_dump(mode="json"),
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "integrity": {
                "status": "VERIFIED_COMPLETE",
                "cells": len(records),
                "terminal_ledger_rows": len(ledger),
                "six_file_bundles_verified": len(records),
                "scoring_input_bundles_verified": len(records),
                "full_evaluation_recomputations": len(records),
                "healthy_recoveries": len(records),
                "doctor_status": doctor.result.status.value,
                "planner_probe_status": "PASSED",
                "planner_probe_receipt_sha256": probe.receipt_sha256,
            },
            "t06_admission_identity": {
                "status": (
                    "BOUND"
                    if admission_attestation_sha256 is not None
                    else "NOT_BOUND_PENDING_OWNER_DECISION"
                ),
                **(
                    {"attestation_sha256": admission_attestation_sha256}
                    if admission_attestation_sha256 is not None
                    else {}
                ),
                "note": (
                    "Validated T06 admission attestation is bound by SHA-256 "
                    f"{admission_attestation_sha256}."
                    if admission_attestation_sha256 is not None
                    else (
                        "The current planner manifest schema has no T06 admission artifact "
                        "identity; "
                        "the owner must resolve this before v2 freeze or any screening conclusion."
                    )
                ),
            },
            "primary": {
                "success": success,
                "paired_planner_vs_kernel": {
                    "pairs": 36,
                    "outcomes": {
                        key: pair_outcomes[key]
                        for key in ("both", "only_planner", "only_kernel", "neither")
                    },
                    "net_wins": net_wins,
                    "criterion_net_wins_at_least_4": net_wins >= 4,
                    "criterion_planner_not_below_kernel_in_any_stratum": (
                        planner_not_below_in_any_layer
                    ),
                    "error_confirmations_insufficient_plus_healthy": {
                        "planner": planner_error_confirmations,
                        "kernel": kernel_error_confirmations,
                        "criterion_planner_not_above_kernel": (
                            planner_error_confirmations <= kernel_error_confirmations
                        ),
                    },
                },
                "strata": strata,
                "screening": screening,
                "no_default_strategy_change": True,
            },
            "t07": metrics,
            "t08": reliability,
            "plan": self._planner_metrics(records),
            "costs_and_failures": costs,
            "transport_sensitivity": {
                "primary_denominator_unchanged": True,
                "eligible_pair_count": len(sensitivity_pairs),
                "http_429_pair_count_separate": len(http429_pairs),
                "unattributed_model_errors": unattributed_model_errors,
                "cells": transport_cells,
                "paired_removal_sensitivity": transport_sensitive,
            },
            "cells": [
                {
                    "sequence": item["cell"].sequence,
                    "run_id": item["cell"].run_id,
                    "incident_case_id": item["cell"].incident_case_id,
                    "repeat_index": item["cell"].repeat_index,
                    "strategy": item["cell"].strategy.value,
                    "evaluation_status": item["evaluation"].status.value,
                    "diagnosis_status": item["diagnosis"].status.value,
                    "success": _evaluation_passed(item["evaluation"]),
                    "controller_failures": item["controller_failures"],
                    "ledger_state": item["ledger"].state,
                    "ledger_reason_code": item["ledger"].reason_code,
                    "recovery_status": item["recovery_status"],
                }
                for item in records
            ],
            "pairs": paired,
        }

    def write(self, output_dir: Path | None = None) -> tuple[Path, Path, dict[str, Any]]:
        summary = self.analyze()
        target = Path(output_dir) if output_dir is not None else self._suite_root / "formal-report"
        if not target.is_absolute():
            target = self._project_root / target
        if target.is_symlink() or target.exists():
            self._fail("formal report output already exists or is invalid")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.mkdir(exist_ok=False)
        summary_path = target / "summary.json"
        markdown_path = target / "report.md"
        try:
            with summary_path.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
            with markdown_path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(render_planner_comparison_markdown(summary))
        except OSError as exc:
            raise PlannerComparisonReportError("cannot write formal planner report") from exc
        return summary_path, markdown_path, summary


def render_planner_comparison_markdown(summary: dict[str, Any]) -> str:
    primary = summary["primary"]
    lines = [
        "# Planner comparison report",
        "",
        f"- Manifest: `{summary['manifest_id']}` (`{summary['manifest_sha256']}`)",
        f"- Integrity: `{summary['integrity']['status']}`; "
        f"{summary['integrity']['cells']} cells, "
        f"{summary['integrity']['full_evaluation_recomputations']} full recomputations.",
        f"- T06 admission identity: `{summary['t06_admission_identity']['status']}`.",
        "- This report is a fixed-sample screening result; it does not "
        "change the default strategy.",
        "",
        "## Primary success",
        "",
        "| Strategy | Passed | Total |",
        "| --- | ---: | ---: |",
    ]
    for strategy, values in primary["success"].items():
        lines.append(f"| {strategy} | {values['passed']} | {values['total']} |")
    pair = primary["paired_planner_vs_kernel"]
    lines.extend(
        [
            "",
            "Planner vs Kernel pairs: "
            + ", ".join(f"{key}={value}" for key, value in pair["outcomes"].items())
            + f"; net wins={pair['net_wins']}; screening=`{primary['screening']}`.",
            "",
            "## Prespecified strata",
            "",
            "| Expected layer | Strategy | Passed | Total | Error confirmations |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
    )
    for layer, detail in primary["strata"].items():
        for strategy, counts in detail["strategies"].items():
            lines.append(
                f"| {layer} | {strategy} | {counts['passed']} | {counts['total']} | "
                f"{counts['error_confirmations']} |"
            )
    lines.extend(
        [
            "",
            "## Registered metric details",
            "",
            "T07 status confusion, abstention, claim support and citation metrics; "
            "T08 complete-group "
            "pass^1–pass^3; and PLAN trajectory, refusal, tool-receipt and obligation metrics are "
            "available in `summary.json`.",
            "",
            "## T06 admission identity",
            "",
            summary["t06_admission_identity"]["note"],
            "",
            "## Cell results",
            "",
            "| Seq | Case | Repeat | Strategy | Evaluation | Diagnosis | Success | Ledger |",
            "| ---: | --- | ---: | --- | --- | --- | --- | --- |",
        ]
    )
    for cell in summary["cells"]:
        lines.append(
            f"| {cell['sequence']} | {cell['incident_case_id']} | {cell['repeat_index']} | "
            f"{cell['strategy']} | {cell['evaluation_status']} | {cell['diagnosis_status']} | "
            f"{cell['success']} | {cell['ledger_state']} |"
        )
    return "\n".join(lines) + "\n"


__all__ = [
    "PlannerComparisonReportError",
    "PlannerComparisonReporter",
    "render_planner_comparison_markdown",
]
