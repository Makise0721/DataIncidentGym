from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import socket
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from data_incident_gym.artifacts import ARTIFACT_FILENAMES, ArtifactRun, BudgetSummary
from data_incident_gym.diagnosis import (
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosisV2,
    DiagnosticStrategy,
    KernelStateTraceEvent,
    PolicyIdentity,
    UnresolvedEvidence,
    UnresolvedEvidenceV2,
)
from data_incident_gym.diagnostic_contracts import InvestigationState, KernelFinalStatus
from data_incident_gym.evaluation import (
    ControllerCheckCode,
    DeterministicEvaluator,
    EvaluationResult,
    EvaluationStatus,
    _controller_checks,
)
from data_incident_gym.evaluation_inputs import (
    ArtifactInputStatus,
    EvaluationInputBundle,
    EvaluationInputsError,
    RecoveryProof,
    _canonical_json,
    build_evaluation_input_bundle,
    classify_scoring_inputs,
    default_evaluator_identity,
    export_scoring_inputs,
    load_evaluation_input_bundle,
    load_scoring_inputs_export,
    write_evaluation_input_bundle,
)
from data_incident_gym.evaluation_rescore import (
    OfflineScoreError,
    compare_offline_scores,
    score_run_offline,
)
from data_incident_gym.evaluation_runner import EvaluationRunner
from data_incident_gym.lab import ScenarioRun
from data_incident_gym.lab_verifier import ScenarioVerification, ScenarioVerificationStatus
from data_incident_gym.scenarios import ScenarioSpec, load_scenario_spec

RUN_ID = "b" * 32
OTHER_RUN_ID = "c" * 32
CASE_ID = "required_null_order_customer_a"
CREATED_AT = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
RECOVERY_FINGERPRINT = "e" * 64


def _policy_identity(strategy: DiagnosticStrategy) -> PolicyIdentity:
    return PolicyIdentity(
        strategy=strategy,
        base_prompt_version="p1.base.v1",
        base_prompt_sha256="1" * 64,
        strategy_prompt_version="p1.static.v5",
        strategy_prompt_sha256="2" * 64,
        controller_protocol_version="p1.controller.v19",
        controller_protocol_sha256="3" * 64,
        tool_schema_sha256="4" * 64,
    )


def _metrics() -> DiagnosisMetrics:
    return DiagnosisMetrics(
        provider="synthetic",
        model="synthetic-model",
        model_requests=1,
        input_tokens=0,
        output_tokens=0,
        tool_call_attempts=0,
        successful_tool_calls=0,
        elapsed_ms=1,
    )


def _static_insufficient_run() -> DiagnosisRunResult:
    strategy = DiagnosticStrategy.STATIC_SKILL
    diagnosis = Diagnosis(
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        run_id=RUN_ID,
        summary="Evidence is insufficient.",
        unresolved_evidence=(
            UnresolvedEvidence(
                evidence_kind="RELATION_DATA_PROFILE",
                subject="raw_orders",
                reason_code="RELATION_NOT_ALLOWED",
            ),
        ),
        confidence=0.5,
    )
    return DiagnosisRunResult(
        strategy=strategy,
        policy_identity=_policy_identity(strategy),
        diagnosis=diagnosis,
        evidence_records=(),
        trace=(
            DiagnosisTerminalTraceEvent(
                event_type="DIAGNOSIS_TERMINAL",
                strategy=strategy,
                status=diagnosis.status,
                evidence_inventory=(),
            ),
        ),
        metrics=_metrics(),
    )


def _v2_insufficient_run() -> DiagnosisRunResult:
    """The T13 B reference run: v2 contract, two representative refusal gaps."""

    strategy = DiagnosticStrategy.STATIC_SKILL
    diagnosis = DiagnosisV2(
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        run_id=RUN_ID,
        summary="The decisive expectation and definition facts are not granted.",
        unresolved_evidence=(
            UnresolvedEvidenceV2(
                evidence_kind="RELATION_SCHEMA_EXPECTATION",
                subject="raw_customers",
                reason_code="RELATION_NOT_ALLOWED",
            ),
            UnresolvedEvidenceV2(
                evidence_kind="DBT_NODE_DEFINITION",
                subject="model.jaffle_shop.customers",
                reason_code="NODE_NOT_ALLOWED",
            ),
        ),
        confidence=0.0,
    )
    return DiagnosisRunResult(
        strategy=strategy,
        policy_identity=_policy_identity(strategy),
        diagnosis=diagnosis,
        evidence_records=(),
        trace=(
            DiagnosisTerminalTraceEvent(
                event_type="DIAGNOSIS_TERMINAL",
                strategy=strategy,
                status=diagnosis.status,
                evidence_inventory=(),
            ),
        ),
        metrics=_metrics(),
    )


def _kernel_state() -> InvestigationState:
    return InvestigationState(
        schema_version="p1.investigation.v1",
        run_id=RUN_ID,
        revision=3,
        allowed_root_cause_codes=(
            "SOURCE_REQUIRED_FIELD_NULL",
            "TRANSFORMATION_REQUIRED_FIELD_NULL",
        ),
        hypotheses=(),
        gaps=(),
        assessments=(),
        claims=(),
        evidence_inventory=(),
        tool_fingerprints=(),
        model_request_limit=8,
        model_requests_used=1,
        model_requests_remaining=7,
        tool_call_limit=8,
        tool_calls_used=0,
        tool_calls_remaining=8,
        final_status=KernelFinalStatus.MODEL_ERROR,
        gate_reason="MODEL_RUNTIME_ERROR",
        selected_hypothesis_id=None,
    )


def _kernel_model_error_run() -> DiagnosisRunResult:
    strategy = DiagnosticStrategy.DIAGNOSTIC_KERNEL
    state = _kernel_state()
    diagnosis = Diagnosis(
        status=DiagnosisStatus.MODEL_ERROR,
        run_id=RUN_ID,
        summary="MODEL_RUNTIME_ERROR",
        confidence=0.0,
    )
    return DiagnosisRunResult(
        strategy=strategy,
        policy_identity=_policy_identity(strategy),
        diagnosis=diagnosis,
        evidence_records=(),
        trace=(
            KernelStateTraceEvent(event_type="KERNEL_STATE", state=state),
            DiagnosisTerminalTraceEvent(
                event_type="DIAGNOSIS_TERMINAL",
                strategy=strategy,
                status=diagnosis.status,
                evidence_inventory=(),
            ),
        ),
        metrics=_metrics(),
        kernel_state=state,
    )


def _verification(scenario: ScenarioSpec) -> ScenarioVerification:
    return ScenarioVerification(
        status=ScenarioVerificationStatus.EXPECTED_FAILURE,
        incident_case_id=scenario.incident_case_id,
        run_id=RUN_ID,
        dbt_exit_code=1,
        failed_nodes=(scenario.direct_failure,),
        skipped_nodes=(),
        affected_assets=tuple(sorted(scenario.affected_assets)),
        schema_fingerprint="a" * 64,
        profile_spec_sha256="b" * 64,
    )


def _write_artifact_files(
    artifact_dir: Path,
    evaluation: EvaluationResult | None,
) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACT_FILENAMES:
        if name == "evaluation.json" and evaluation is not None:
            payload = evaluation.model_dump_json(indent=2) + "\n"
        else:
            payload = "{}\n"
        (artifact_dir / name).write_text(payload, encoding="utf-8", newline="")


def _budget() -> BudgetSummary:
    return BudgetSummary(
        model_request_limit=8,
        tool_call_limit=8,
        output_retry_limit=2,
        timeout_seconds=300,
    )


def _recovery_proof(scenario: ScenarioSpec, *, state: str = "HEALTHY") -> RecoveryProof:
    return RecoveryProof(
        source="LAB_RESTORE",
        incident_case_id=scenario.incident_case_id,
        state=state,
        fingerprint=RECOVERY_FINGERPRINT if state == "HEALTHY" else None,
    )


def _prepared_bundle(project_root: Path) -> EvaluationInputBundle:
    """Everything up to (but excluding) the scoring-inputs write."""

    scenario = load_scenario_spec(CASE_ID)
    verification = _verification(scenario)
    run = _static_insufficient_run()
    artifact_dir = project_root / "artifacts" / RUN_ID
    evaluation = DeterministicEvaluator.evaluate(
        scenario, verification, run, recovery_succeeded=True
    )
    _write_artifact_files(artifact_dir, evaluation)
    return build_evaluation_input_bundle(
        scenario=scenario,
        verification=verification,
        diagnosis_run=run,
        recovery=_recovery_proof(scenario),
        artifact_dir=artifact_dir,
        budget=_budget(),
        evaluator=default_evaluator_identity(),
    )


def _prepare_project(
    project_root: Path,
    run: DiagnosisRunResult | None = None,
    evaluation: EvaluationResult | None = None,
    scenario: ScenarioSpec | None = None,
) -> EvaluationInputBundle:
    scenario = scenario or load_scenario_spec(CASE_ID)
    verification = _verification(scenario)
    run = run or _static_insufficient_run()
    artifact_dir = project_root / "artifacts" / RUN_ID
    if evaluation is None:
        evaluation = DeterministicEvaluator.evaluate(
            scenario, verification, run, recovery_succeeded=True
        )
    _write_artifact_files(artifact_dir, evaluation)
    bundle = build_evaluation_input_bundle(
        scenario=scenario,
        verification=verification,
        diagnosis_run=run,
        recovery=_recovery_proof(scenario),
        artifact_dir=artifact_dir,
        budget=_budget(),
        evaluator=default_evaluator_identity(),
    )
    write_evaluation_input_bundle(project_root, bundle, created_at=CREATED_AT)
    return bundle


def _bundle_dir(project_root: Path) -> Path:
    return project_root / ".dig" / "scoring-inputs" / RUN_ID


def _rewrite_with_consistent_digests(
    project_root: Path,
    mutate: Callable[[dict], None],
) -> None:
    run_dir = _bundle_dir(project_root)
    inputs_path = run_dir / "evaluation_inputs.json"
    payload = json.loads(inputs_path.read_text(encoding="utf-8"))
    mutate(payload)
    inputs_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline=""
    )
    index_path = run_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["files"] = {
        "evaluation_inputs.json": hashlib.sha256(inputs_path.read_bytes()).hexdigest()
    }
    index["inputs_digest"] = hashlib.sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()
    index_path.write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8", newline=""
    )


def _file_digests(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _flipped_root_check_evaluator(
    scenario: ScenarioSpec,
    verification: ScenarioVerification,
    diagnosis_run: DiagnosisRunResult,
    *,
    recovery_succeeded: bool,
) -> EvaluationResult:
    """Controlled scorer variant: flips only the ROOT_CAUSE_ACCEPTED check."""

    result = DeterministicEvaluator.evaluate(
        scenario, verification, diagnosis_run, recovery_succeeded=recovery_succeeded
    )
    payload = result.model_dump(mode="json")
    checks = []
    for check in payload["checks"]:
        if check["code"] == "ROOT_CAUSE_ACCEPTED" and check["applicability"] == "APPLICABLE":
            passed = not check["passed"]
            check = {
                **check,
                "passed": passed,
                "reason_code": f"ROOT_CAUSE_ACCEPTED_{'PASSED' if passed else 'FAILED'}",
            }
        checks.append(check)
    failed = tuple(
        check["code"]
        for check in checks
        if check["applicability"] == "APPLICABLE" and not check["passed"]
    )
    payload["checks"] = checks
    payload["failed_check_codes"] = list(failed)
    payload["status"] = "PASSED" if not failed else "FAILED"
    return EvaluationResult.model_validate(payload)


def _details_only_evaluator(
    scenario: ScenarioSpec,
    verification: ScenarioVerification,
    diagnosis_run: DiagnosisRunResult,
    *,
    recovery_succeeded: bool,
) -> EvaluationResult:
    """Controlled scorer variant: rewrites one check's actual strings only."""

    result = DeterministicEvaluator.evaluate(
        scenario, verification, diagnosis_run, recovery_succeeded=recovery_succeeded
    )
    payload = result.model_dump(mode="json")
    checks = []
    for check in payload["checks"]:
        if check["code"] == "TOOL_ALLOWLIST_EXACT" and check["applicability"] == "APPLICABLE":
            check = {**check, "actual": [*check["actual"], "REVIEWED"]}
        checks.append(check)
    payload["checks"] = checks
    return EvaluationResult.model_validate(payload)


# ---------------------------------------------------------------------------
# T02: frozen inputs, strict loading, classification
# ---------------------------------------------------------------------------


def test_a_v2_diagnosis_round_trips_by_its_contract_marker(tmp_path: Path) -> None:
    """Certification stop, round 3 (2026-09-19): the scoring-inputs loader
    revalidated every persisted diagnosis against the frozen v1 model and
    refused a v2 run's own bundle. The loader must pick the contract class by
    the diagnosis's own ``schema_version`` marker — never by content."""

    _prepare_project(tmp_path, run=_v2_insufficient_run())

    loaded = load_evaluation_input_bundle(tmp_path, RUN_ID)

    diagnosis = loaded.diagnosis_run.diagnosis
    assert type(diagnosis) is DiagnosisV2
    assert {
        (item.evidence_kind, item.subject, item.reason_code)
        for item in diagnosis.unresolved_evidence
    } == {
        ("RELATION_SCHEMA_EXPECTATION", "raw_customers", "RELATION_NOT_ALLOWED"),
        ("DBT_NODE_DEFINITION", "model.jaffle_shop.customers", "NODE_NOT_ALLOWED"),
    }


def test_a_v1_diagnosis_keeps_the_frozen_contract(tmp_path: Path) -> None:
    _prepare_project(tmp_path)

    loaded = load_evaluation_input_bundle(tmp_path, RUN_ID)

    assert type(loaded.diagnosis_run.diagnosis) is Diagnosis
    assert loaded.diagnosis_run.diagnosis.schema_version == "p1.diagnosis.v1"


def test_round_trip_preserves_inputs_and_classifies_re_scorable(tmp_path: Path) -> None:
    bundle = _prepare_project(tmp_path)

    loaded = load_evaluation_input_bundle(tmp_path, RUN_ID)

    assert loaded.inputs_digest() == bundle.inputs_digest()
    assert loaded.scenario.digest() == bundle.scenario.digest()
    assert loaded.verification.incident_case_id == CASE_ID
    assert loaded.diagnosis_run.digest() == bundle.diagnosis_run.digest()
    assert loaded.recovery.recovered is True

    classification = classify_scoring_inputs(tmp_path, RUN_ID)
    assert classification.status is ArtifactInputStatus.RE_SCORABLE
    assert classification.inputs_digest == bundle.inputs_digest()


def test_kernel_state_is_restored_as_typed_state(tmp_path: Path) -> None:
    run = _kernel_model_error_run()
    _prepare_project(tmp_path, run=run, evaluation=None)

    loaded = load_evaluation_input_bundle(tmp_path, RUN_ID)

    assert isinstance(loaded.diagnosis_run.kernel_state, InvestigationState)
    assert loaded.diagnosis_run.kernel_state == _kernel_state()
    checks = _controller_checks(loaded.diagnosis_run)
    by_code = {check.code: check for check in checks}
    assert by_code[ControllerCheckCode.KERNEL_STATE_VALID].passed is True


def test_loader_rejects_tampered_inputs_file(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    inputs_path = _bundle_dir(tmp_path) / "evaluation_inputs.json"
    text = inputs_path.read_text(encoding="utf-8")
    inputs_path.write_text(text.replace(RUN_ID, OTHER_RUN_ID, 1), encoding="utf-8")

    with pytest.raises(EvaluationInputsError) as error:
        load_evaluation_input_bundle(tmp_path, RUN_ID)

    assert error.value.code == "SCORING_INPUTS_INVALID"


def test_loader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    index_path = _bundle_dir(tmp_path) / "index.json"
    index_path.write_text(
        '{"run_id": "' + RUN_ID + '", "run_id": "' + OTHER_RUN_ID + '"}',
        encoding="utf-8",
    )

    with pytest.raises(EvaluationInputsError) as error:
        load_evaluation_input_bundle(tmp_path, RUN_ID)

    assert error.value.code == "SCORING_INPUTS_INVALID"
    assert error.value.detail == "duplicate JSON key"


def test_loader_rejects_bundle_copied_to_another_run(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    shutil.copytree(
        _bundle_dir(tmp_path),
        tmp_path / ".dig" / "scoring-inputs" / OTHER_RUN_ID,
    )

    with pytest.raises(EvaluationInputsError) as error:
        load_evaluation_input_bundle(tmp_path, OTHER_RUN_ID)

    assert error.value.code == "SCORING_INPUTS_INVALID"


def test_loader_rejects_unknown_evaluator_version(tmp_path: Path) -> None:
    _prepare_project(tmp_path)

    def mutate(payload: dict) -> None:
        payload["original_evaluator"]["version"] = "p1.evaluator.v99"

    _rewrite_with_consistent_digests(tmp_path, mutate)

    with pytest.raises(EvaluationInputsError) as error:
        load_evaluation_input_bundle(tmp_path, RUN_ID)

    assert error.value.code == "UNKNOWN_EVALUATOR_VERSION"


def test_loader_rejects_changed_scenario_content(tmp_path: Path) -> None:
    _prepare_project(tmp_path)

    def mutate(payload: dict) -> None:
        payload["scenario"]["affected_assets"] = ["model.jaffle_shop.customers"]

    _rewrite_with_consistent_digests(tmp_path, mutate)

    with pytest.raises(EvaluationInputsError) as error:
        load_evaluation_input_bundle(tmp_path, RUN_ID)

    assert error.value.code == "SCORING_INPUTS_INVALID"


def test_classification_distinguishes_partial_and_missing(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts" / RUN_ID
    _write_artifact_files(artifact_dir, None)

    partial = classify_scoring_inputs(tmp_path, RUN_ID)
    assert partial.status is ArtifactInputStatus.PARTIAL_ANALYSIS
    assert "SCORING_INPUTS_MISSING" in partial.reasons

    missing = classify_scoring_inputs(tmp_path, OTHER_RUN_ID)
    assert missing.status is ArtifactInputStatus.NOT_RE_SCORABLE
    assert missing.reasons == ("ARTIFACT_RUN_MISSING",)


def test_export_round_trip_and_tamper_rejection(tmp_path: Path) -> None:
    bundle = _prepare_project(tmp_path)
    export_path = export_scoring_inputs(tmp_path, RUN_ID, tmp_path / "export.json")

    exported = load_scoring_inputs_export(export_path)

    assert exported.inputs_digest() == bundle.inputs_digest()

    payload = json.loads(export_path.read_text(encoding="utf-8"))
    payload["inputs"] = payload["inputs"].replace(RUN_ID, OTHER_RUN_ID, 1)
    export_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(EvaluationInputsError):
        load_scoring_inputs_export(export_path)


def test_loader_rejects_symlinked_run_directory(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    run_dir = _bundle_dir(tmp_path)
    target = tmp_path / "elsewhere"
    shutil.move(str(run_dir), target)
    try:
        run_dir.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available in this environment")

    with pytest.raises(EvaluationInputsError) as error:
        load_evaluation_input_bundle(tmp_path, RUN_ID)

    assert error.value.code == "SCORING_INPUTS_ESCAPE"


# ---------------------------------------------------------------------------
# T03: offline scoring, version diff, idempotency
# ---------------------------------------------------------------------------


def test_offline_score_matches_direct_evaluation(tmp_path: Path) -> None:
    _prepare_project(tmp_path)

    result = score_run_offline(tmp_path, RUN_ID, now=CREATED_AT)

    direct = DeterministicEvaluator.evaluate(
        load_scenario_spec(CASE_ID),
        _verification(load_scenario_spec(CASE_ID)),
        _static_insufficient_run(),
        recovery_succeeded=True,
    )
    assert result.created is True
    assert result.evaluation.model_dump() == direct.model_dump()
    assert result.diff.available is True
    assert result.changed_check_codes == ()
    assert result.evaluation.status is EvaluationStatus.FAILED


def test_offline_score_keeps_original_bundle_and_artifacts_unchanged(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    before = _file_digests(tmp_path / ".dig")
    artifacts_before = _file_digests(tmp_path / "artifacts" / RUN_ID)

    first = score_run_offline(tmp_path, RUN_ID, now=CREATED_AT)
    second = score_run_offline(tmp_path, RUN_ID, now=CREATED_AT)

    assert first.created is True
    assert second.created is False
    assert second.score_id == first.score_id
    assert second.evaluation.model_dump() == first.evaluation.model_dump()
    assert _file_digests(tmp_path / ".dig") == before
    assert _file_digests(tmp_path / "artifacts" / RUN_ID) == artifacts_before


def test_controlled_scorer_changes_only_its_check(tmp_path: Path) -> None:
    _prepare_project(tmp_path)

    baseline = score_run_offline(tmp_path, RUN_ID, now=CREATED_AT)
    controlled = score_run_offline(
        tmp_path,
        RUN_ID,
        scorer=_flipped_root_check_evaluator,
        scorer_name="CONTROLLED_FLIP",
        now=CREATED_AT,
    )

    assert controlled.score_id != baseline.score_id
    assert controlled.changed_check_codes == ("ROOT_CAUSE_ACCEPTED",)
    baseline_check = next(
        check
        for check in baseline.evaluation.checks
        if check.code.value == "ROOT_CAUSE_ACCEPTED"
    )
    controlled_check = next(
        check
        for check in controlled.evaluation.checks
        if check.code.value == "ROOT_CAUSE_ACCEPTED"
    )
    assert baseline_check.passed is False
    assert controlled_check.passed is True
    provenance = json.loads(
        (controlled.score_dir / "provenance.json").read_text(encoding="utf-8")
    )
    baseline_provenance = json.loads(
        (baseline.score_dir / "provenance.json").read_text(encoding="utf-8")
    )
    assert provenance["scorer"]["name"] == "CONTROLLED_FLIP"
    assert (
        provenance["scorer"]["source_digest"]
        != baseline_provenance["scorer"]["source_digest"]
    )


def test_compare_offline_scores_reports_identity_changes(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    baseline = score_run_offline(tmp_path, RUN_ID, now=CREATED_AT)
    controlled = score_run_offline(
        tmp_path,
        RUN_ID,
        scorer=_flipped_root_check_evaluator,
        scorer_name="CONTROLLED_FLIP",
        now=CREATED_AT,
    )

    comparison = compare_offline_scores(
        tmp_path, RUN_ID, baseline.score_id, controlled.score_id
    )

    assert comparison.status_a == "FAILED"
    assert comparison.status_b == "FAILED"
    assert comparison.changed_check_codes == ("ROOT_CAUSE_ACCEPTED",)
    changed = next(
        item for item in comparison.changes if item.code == "ROOT_CAUSE_ACCEPTED"
    )
    assert changed.change == "FAILED_TO_PASSED"
    assert comparison.scorer_a.name != comparison.scorer_b.name


def test_offline_score_rejects_missing_inputs(tmp_path: Path) -> None:
    (tmp_path / "artifacts" / RUN_ID).mkdir(parents=True)

    with pytest.raises(EvaluationInputsError) as error:
        score_run_offline(tmp_path, RUN_ID)

    assert error.value.code == "SCORING_INPUTS_MISSING"


def test_archived_evaluation_tamper_disables_diff(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    evaluation_path = tmp_path / "artifacts" / RUN_ID / "evaluation.json"
    evaluation_path.write_text("{}\n", encoding="utf-8", newline="")

    result = score_run_offline(tmp_path, RUN_ID, now=CREATED_AT)

    assert result.diff.available is False
    assert result.diff.unavailable_reason == "ARCHIVED_EVALUATION_DIGEST_MISMATCH"
    assert result.evaluation.status is EvaluationStatus.FAILED


def test_offline_score_needs_no_network(tmp_path: Path) -> None:
    _prepare_project(tmp_path)

    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted during offline scoring")

    original = socket.socket
    socket.socket = _blocked  # type: ignore[assignment]
    try:
        result = score_run_offline(tmp_path, RUN_ID, now=CREATED_AT)
    finally:
        socket.socket = original  # type: ignore[assignment]

    assert result.created is True


# ---------------------------------------------------------------------------
# Audit regressions: kernel scoring, cache integrity, cross-run binding,
# detail changes, unavailable diffs, recovery provenance, service chain
# ---------------------------------------------------------------------------


def test_kernel_run_rescore_reports_controller_checks(tmp_path: Path) -> None:
    run = _kernel_model_error_run()
    _prepare_project(tmp_path, run=run)

    result = score_run_offline(tmp_path, RUN_ID, now=CREATED_AT)

    assert result.created is True
    assert result.diff.available is True
    assert result.changed_check_codes == ()
    assert len(result.evaluation.controller_checks) == 3
    controller_entries = {
        item.code: item for item in result.diff.changes if item.kind == "CONTROLLER"
    }
    assert set(controller_entries) == {
        ControllerCheckCode.KERNEL_STATE_VALID.value,
        ControllerCheckCode.KERNEL_HYPOTHESIS_GATE.value,
        ControllerCheckCode.KERNEL_EVIDENCE_GAP_GATE.value,
    }
    assert all(item.change == "UNCHANGED" for item in controller_entries.values())
    scenario = load_scenario_spec(CASE_ID)
    direct = DeterministicEvaluator.evaluate(
        scenario, _verification(scenario), run, recovery_succeeded=True
    )
    assert result.evaluation.model_dump() == direct.model_dump()


def test_loader_rejects_bundle_copied_to_another_run_with_patched_index(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    target_dir = tmp_path / ".dig" / "scoring-inputs" / OTHER_RUN_ID
    shutil.copytree(_bundle_dir(tmp_path), target_dir)
    index_path = target_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["run_id"] = OTHER_RUN_ID
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(EvaluationInputsError) as error:
        load_evaluation_input_bundle(tmp_path, OTHER_RUN_ID)

    assert error.value.code == "SCORING_INPUTS_INVALID"
    assert error.value.detail == "bundle run_id does not match the requested run"
    classification = classify_scoring_inputs(tmp_path, OTHER_RUN_ID)
    assert classification.status is ArtifactInputStatus.NOT_RE_SCORABLE


@pytest.mark.parametrize("filename", ["evaluation.json", "diff.json", "report.md"])
def test_cached_score_rejects_tampered_derived_files(
    tmp_path: Path,
    filename: str,
) -> None:
    _prepare_project(tmp_path)
    baseline = score_run_offline(tmp_path, RUN_ID, now=CREATED_AT)
    path = baseline.score_dir / filename
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(OfflineScoreError) as error:
        score_run_offline(tmp_path, RUN_ID, now=CREATED_AT)

    assert error.value.code == "OFFLINE_SCORE_DIR_INVALID"


def test_cached_score_rejects_status_flip(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    baseline = score_run_offline(tmp_path, RUN_ID, now=CREATED_AT)
    assert baseline.evaluation.status is EvaluationStatus.FAILED
    evaluation_path = baseline.score_dir / "evaluation.json"
    payload = json.loads(evaluation_path.read_text(encoding="utf-8"))
    payload["status"] = "PASSED"
    evaluation_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(OfflineScoreError) as error:
        score_run_offline(tmp_path, RUN_ID, now=CREATED_AT)

    assert error.value.code == "OFFLINE_SCORE_DIR_INVALID"


def test_details_only_change_is_reported(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    baseline = score_run_offline(tmp_path, RUN_ID, now=CREATED_AT)
    details = score_run_offline(
        tmp_path,
        RUN_ID,
        scorer=_details_only_evaluator,
        scorer_name="CONTROLLED_DETAILS",
        now=CREATED_AT,
    )

    assert details.evaluation.status is baseline.evaluation.status
    assert details.changed_check_codes == ("TOOL_ALLOWLIST_EXACT",)
    entry = next(
        item for item in details.diff.changes if item.code == "TOOL_ALLOWLIST_EXACT"
    )
    assert entry.change == "UNCHANGED"
    assert entry.details_changed is True
    assert entry.before_actual != entry.after_actual
    report = (details.score_dir / "report.md").read_text(encoding="utf-8")
    assert "TOOL_ALLOWLIST_EXACT" in report


def test_unavailable_diff_is_reported_as_unavailable(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    (tmp_path / "artifacts" / RUN_ID / "evaluation.json").write_text(
        "{}\n", encoding="utf-8"
    )

    result = score_run_offline(tmp_path, RUN_ID, now=CREATED_AT)

    assert result.diff.available is False
    assert result.diff.unavailable_reason == "ARCHIVED_EVALUATION_DIGEST_MISMATCH"
    report = (result.score_dir / "report.md").read_text(encoding="utf-8")
    assert "无法与归档评分比较" in report
    assert "逐项一致" not in report


def test_bundle_records_recovery_proof_with_fingerprint(tmp_path: Path) -> None:
    _prepare_project(tmp_path)

    loaded = load_evaluation_input_bundle(tmp_path, RUN_ID)

    assert loaded.recovery.source == "LAB_RESTORE"
    assert loaded.recovery.incident_case_id == CASE_ID
    assert loaded.recovery.state == "HEALTHY"
    assert loaded.recovery.fingerprint == RECOVERY_FINGERPRINT
    assert loaded.recovery.recovered is True


def test_bundle_rejects_malformed_recovery_fingerprint(tmp_path: Path) -> None:
    scenario = load_scenario_spec(CASE_ID)
    artifact_dir = tmp_path / "artifacts" / RUN_ID
    _write_artifact_files(artifact_dir, None)

    with pytest.raises(ValidationError):
        build_evaluation_input_bundle(
            scenario=scenario,
            verification=_verification(scenario),
            diagnosis_run=_static_insufficient_run(),
            recovery=RecoveryProof(
                source="LAB_RESTORE",
                incident_case_id=CASE_ID,
                state="HEALTHY",
                fingerprint="not-a-digest",
            ),
            artifact_dir=artifact_dir,
            budget=_budget(),
            evaluator=default_evaluator_identity(),
        )


def test_runner_service_chain_writes_attachment_then_rescores(tmp_path: Path) -> None:
    scenario = load_scenario_spec(CASE_ID)
    verification = _verification(scenario)
    run = _static_insufficient_run()

    class FakeLab:
        def reset(self, _case_id: str) -> SimpleNamespace:
            return SimpleNamespace(
                case_id=CASE_ID, state="HEALTHY", fingerprint=RECOVERY_FINGERPRINT
            )

        def prepare(self, _case_id: str) -> SimpleNamespace:
            return SimpleNamespace(case_id=CASE_ID, state="HEALTHY")

        def build(self, _case_id: str) -> ScenarioRun:
            return ScenarioRun(
                run_id=RUN_ID,
                artifact_dir=tmp_path / ".dig" / "lab" / "runs" / RUN_ID,
                verification_status=ScenarioVerificationStatus.EXPECTED_FAILURE,
                dbt_exit_code=1,
            )

        def restore(self, _case_id: str) -> SimpleNamespace:
            return SimpleNamespace(
                case_id=CASE_ID, state="HEALTHY", fingerprint=RECOVERY_FINGERPRINT
            )

    class FakeDiagnosis:
        async def diagnose(self) -> DiagnosisRunResult:
            return run

    class FakeWriter:
        def write(self, artifact_run: ArtifactRun) -> Path:
            artifact_dir = tmp_path / "artifacts" / RUN_ID
            _write_artifact_files(artifact_dir, artifact_run.evaluation)
            return artifact_dir

    runner = EvaluationRunner(
        lab=FakeLab(),
        diagnostic_settings=SimpleNamespace(model_base_url="http://127.0.0.1:11434/v1"),
        diagnosis_factory=lambda run_id, strategy: FakeDiagnosis(),
        private_scenario_loader=lambda case_id: scenario,
        private_verification_loader=lambda run_id: verification,
        evaluator=DeterministicEvaluator.evaluate,
        artifact_writer=FakeWriter(),
        clock=lambda: CREATED_AT,
        project_root=tmp_path,
    )

    attempt = asyncio.run(runner.run(CASE_ID, DiagnosticStrategy.STATIC_SKILL))

    assert attempt.scoring_inputs_dir == tmp_path / ".dig" / "scoring-inputs" / RUN_ID
    bundle = load_evaluation_input_bundle(tmp_path, RUN_ID)
    assert bundle.recovery.incident_case_id == CASE_ID
    assert bundle.recovery.fingerprint == RECOVERY_FINGERPRINT
    assert bundle.diagnosis_run.digest() == run.digest()

    result = score_run_offline(tmp_path, RUN_ID, now=CREATED_AT)

    assert result.created is True
    assert result.diff.available is True
    assert result.changed_check_codes == ()


def test_the_bundle_write_retries_a_transient_windows_rename_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit round: a 1-in-100 Windows rename lock used to fail a completed run.

    A probe on this repository measured ~1% of scoring-input renames failing
    once with WinError 5 and succeeding afterwards, so the writer retries
    ``PermissionError`` a bounded number of times — it cannot tell in advance
    which occurrence is transient.
    """

    bundle = _prepared_bundle(tmp_path)
    original_rename = Path.rename
    locks = {"count": 0}

    def flaky_rename(self: Path, target: Path) -> Path:
        if self.name.startswith(".") and locks["count"] == 0:
            locks["count"] += 1
            raise PermissionError(5, "access denied", str(self))
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky_rename)

    written = write_evaluation_input_bundle(tmp_path, bundle, created_at=CREATED_AT)

    assert locks["count"] == 1
    assert written == _bundle_dir(tmp_path)
    assert (written / "evaluation_inputs.json").is_file()


def test_the_bundle_write_still_fails_closed_under_a_persistent_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from data_incident_gym import evaluation_inputs

    bundle = _prepared_bundle(tmp_path)
    attempts = {"count": 0}

    def locked_rename(self: Path, target: Path) -> Path:
        if self.name.startswith("."):
            attempts["count"] += 1
            raise PermissionError(5, "access denied", str(self))
        raise AssertionError("only the bundle rename may be exercised")

    monkeypatch.setattr(Path, "rename", locked_rename)

    with pytest.raises(EvaluationInputsError) as error:
        write_evaluation_input_bundle(tmp_path, bundle, created_at=CREATED_AT)

    assert error.value.code == "SCORING_INPUTS_WRITE_FAILED"
    assert attempts["count"] == evaluation_inputs._RENAME_ATTEMPTS
    assert not (tmp_path / ".dig" / "scoring-inputs" / f".{RUN_ID}.tmp").exists()


def test_the_offline_score_writer_shares_the_rename_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same 1-in-100 Windows rename lock also hit the rescore writer."""

    from data_incident_gym import evaluation_rescore

    final = tmp_path / "rescores" / ("a" * 64)
    final.parent.mkdir(parents=True, exist_ok=True)
    original_rename = Path.rename
    locks = {"count": 0}

    def flaky_rename(self: Path, target: Path) -> Path:
        if self.name.startswith(".") and locks["count"] == 0:
            locks["count"] += 1
            raise PermissionError(5, "access denied", str(self))
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky_rename)

    evaluation_rescore._write_score_dir(final, {"index.json": "{}"})

    assert locks["count"] == 1
    assert (final / "index.json").is_file()


def test_the_offline_score_writer_still_fails_closed_under_a_persistent_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from data_incident_gym import evaluation_inputs, evaluation_rescore

    final = tmp_path / "rescores" / ("b" * 64)
    final.parent.mkdir(parents=True, exist_ok=True)
    attempts = {"count": 0}

    def locked_rename(self: Path, target: Path) -> Path:
        attempts["count"] += 1
        raise PermissionError(5, "access denied", str(self))

    monkeypatch.setattr(Path, "rename", locked_rename)

    with pytest.raises(evaluation_rescore.OfflineScoreError) as error:
        evaluation_rescore._write_score_dir(final, {"index.json": "{}"})

    assert error.value.code == "OFFLINE_SCORE_WRITE_FAILED"
    assert attempts["count"] == evaluation_inputs._RENAME_ATTEMPTS
    assert not (tmp_path / "rescores" / f".{final.name}.tmp").exists()
