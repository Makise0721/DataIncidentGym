"""Offline path replay: each of the three failures has a budget-feasible,
contract-valid route from recorded public inputs.

Every case replays a minimal call sequence into a fresh kernel, submits the
corrected decision to the real ``finalize`` and scores it with the real
evaluator. Fixtures are independent copies under
``tests/fixtures/evidence_planning``, so nothing here reads ``artifacts/`` or
``.dig/``.

Scope: the replays drive the kernel's own ``prepare_tool`` / ``record_tool_result``
/ ``finalize`` path and score through the real evaluator on a hand-built trace, so
they cover the kernel contract, the tool budget and the acceptance checks. They do
**not** exercise the runner or SDK wiring, model request scheduling, or the route
choice a model would make — the prompt rules are pinned separately by text
contracts.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from data_incident_gym.diagnosis import (
    AffectedAssetClaim,
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    KernelStateTraceEvent,
    RootCauseClaim,
    ToolTraceEvent,
)
from data_incident_gym.diagnostic_agent import (
    P1_ROOT_CAUSE_CODES,
    policy_identity_for_strategy,
)
from data_incident_gym.diagnostic_contracts import (
    ClaimEvidence,
    ClaimKind,
    EvidenceGapKind,
    Hypothesis,
    HypothesisAssessment,
    HypothesisVerdict,
    InvestigationIntent,
    KernelDecision,
    KernelError,
)
from data_incident_gym.diagnostic_kernel import DiagnosticKernel
from data_incident_gym.evaluation import DeterministicEvaluator, EvaluationStatus
from data_incident_gym.evidence import EvidenceRecord
from data_incident_gym.lab_verifier import ScenarioVerification, ScenarioVerificationStatus
from data_incident_gym.scenarios import load_scenario_spec

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "evidence_planning"
CASE_IDS = {
    19: "silent_payment_drop_partition_b",
    44: "schema_type_change_order_customer_b",
    50: "silent_payment_drop_partition_a",
}
FAILED_NODE = "model.jaffle_shop.customers"
PAYMENT_SEED = "seed.jaffle_shop.raw_payments"
PROBE = "raw_orders"
GENERATED_LINEAGE = "GENERATED_LINEAGE"

RUN_RESULTS = ("DBT_RUN_RESULTS", "")
NODE_ERROR = ("DBT_NODE_ERROR", FAILED_NODE)
DOWNSTREAM_LINEAGE = ("DBT_LINEAGE", PAYMENT_SEED)

LOSS = Hypothesis(hypothesis_id="h_loss", root_cause_code="SOURCE_PAYMENT_INGESTION_LOSS")
DECLINE = Hypothesis(
    hypothesis_id="h_decline", root_cause_code="NORMAL_BUSINESS_PAYMENT_DECLINE"
)
TYPE_CHANGE = Hypothesis(
    hypothesis_id="h_col_type", root_cause_code="SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"
)
RENAME = Hypothesis(
    hypothesis_id="h_col_rename", root_cause_code="SOURCE_SCHEMA_COLUMN_RENAMED"
)
CAST = Hypothesis(
    hypothesis_id="h_cast", root_cause_code="TRANSFORMATION_COLUMN_CAST_CHANGED"
)
PAYMENT_HYPS = (LOSS, DECLINE)
SCHEMA_HYPS = (TYPE_CHANGE, RENAME, CAST)

RECEIPT = "RELATION_NOT_ALLOWED"
GAP_MATRIX_INVALID = "GAP_MATRIX_INVALID"
GAP_CHECK = "INSUFFICIENCY_GAP_DECLARED"


def _call(kind: str, tool: str, key, args: dict | None = None, hyps=()) -> tuple:
    """One planned call: gap kind, tool, arguments, evidence key, declarations."""

    return (kind, tool, args or {}, key, hyps)


def _arguments(tool: str, key, overrides: dict) -> dict:
    """The exact business arguments a tool requires for this planned call."""

    if tool == "get_dbt_run_results":
        return {"run_id": overrides["run_id"]}
    if tool == "get_dbt_node_error":
        return {"run_id": overrides["run_id"], "node_id": FAILED_NODE}
    if tool == "get_dbt_lineage":
        node = PAYMENT_SEED if key == DOWNSTREAM_LINEAGE else FAILED_NODE
        return {"node_id": node, "direction": overrides["direction"]}
    relation = overrides.get("relation_name") or key[1]
    return {"relation_name": relation}


def _lineage_call(direction: str, key) -> tuple:
    return _call("MAP_IMPACT" if direction == "downstream" else "DISCOVER_SOURCE_RELATION",
                 "get_dbt_lineage", key, {"direction": direction})


def _profile(relation: str) -> tuple:
    return ("RELATION_DATA_PROFILE", relation)


def _history(relation: str) -> tuple:
    return ("RELATION_HISTORY", relation)


def _schema(relation: str) -> tuple:
    return ("RELATION_SCHEMA", relation)


def _inputs(seq: int) -> dict:
    return json.loads((FIXTURES / f"seq{seq}_inputs.json").read_text(encoding="utf-8"))


def _observation_subject(inputs: dict, kind: str) -> str:
    """The subject the public brief binds to one named incident observation."""

    return next(
        item["subject"] for item in inputs["observations"] if item["kind"] == kind
    )


def _record_index(seq: int) -> dict[tuple[str, str], EvidenceRecord]:
    payload = json.loads((FIXTURES / f"seq{seq}_evidence.json").read_text(encoding="utf-8"))
    index: dict[tuple[str, str], EvidenceRecord] = {}
    for item in payload["records"]:
        record = EvidenceRecord.model_validate(item)
        content = record.content
        name = getattr(content, "relation_name", None) or getattr(content, "node_id", None) or ""
        index[(record.evidence_type.value, name)] = record
    return index


def _generated_lineage() -> EvidenceRecord:
    payload = json.loads(
        (FIXTURES / "seq44_generated_lineage.json").read_text(encoding="utf-8")
    )
    return EvidenceRecord.model_validate(payload[0])


def _kernel(seq: int, inputs: dict | None = None) -> DiagnosticKernel:
    inputs = inputs or _inputs(seq)
    observations = tuple(
        (item["kind"], item["subject"], item["value"]) for item in inputs["observations"]
    )
    return DiagnosticKernel.start(
        run_id=inputs["source_run_id"],
        allowed_root_cause_codes=P1_ROOT_CAUSE_CODES,
        model_request_limit=8,
        tool_call_limit=8,
        observable_schema_relations=tuple(inputs["observable_relations"]["schema"]),
        observable_profile_relations=tuple(inputs["observable_relations"]["profile"]),
        observable_history_relations=tuple(inputs["observable_relations"]["history"]),
        incident_subjects=tuple(inputs["incident_subjects"]),
        health_target_subjects=tuple(
            subject for kind, subject, _ in observations if kind == "CURRENT_PERIOD_COUNT"
        ),
        incident_logical_observed_at=datetime.fromisoformat(inputs["logical_observed_at"]),
        incident_observations=observations,
        lineage_node_candidates=tuple(inputs["lineage_node_candidates"]),
    )


def _replay(seq: int, kernel: DiagnosticKernel, plan: list[tuple]) -> list[tuple]:
    """Replay planned calls; a ``None`` key means the kernel must refuse the call."""

    index = _record_index(seq)
    run_id = _inputs(seq)["source_run_id"]
    log: list[tuple] = []
    for step, (kind, tool, args, key, hyps) in enumerate(plan):
        overrides = {
            "run_id": run_id,
            "direction": args.get("direction"),
            "relation_name": args.get("relation_name"),
        }
        arguments = _arguments(tool, key, overrides)
        try:
            prepared = kernel.prepare_tool(
                intent=InvestigationIntent(
                    gap_id=f"g_{step}", gap_kind=EvidenceGapKind(kind), new_hypotheses=hyps
                ),
                tool_name=tool,
                arguments=arguments,
            )
        except KernelError as error:
            log.append((tool, arguments, error.code, ()))
            continue
        record = _generated_lineage() if key == GENERATED_LINEAGE else index[key]
        accepted = kernel.record_tool_result(prepared, (record,))
        log.append((tool, arguments, None, tuple(item.evidence_id for item in accepted)))
    return log


def _evaluate(seq: int, kernel: DiagnosticKernel, decision: KernelDecision, log: list[tuple]):
    run_id = _inputs(seq)["source_run_id"]
    outcome = kernel.finalize(decision)
    snapshot = kernel.snapshot(model_requests_used=len(log))
    spec = load_scenario_spec(CASE_IDS[seq])
    expected_failure = spec.direct_failure is not None
    verification = ScenarioVerification(
        status=(
            ScenarioVerificationStatus.EXPECTED_FAILURE
            if expected_failure
            else ScenarioVerificationStatus.EXPECTED_ANOMALY
        ),
        incident_case_id=CASE_IDS[seq],
        run_id=run_id,
        dbt_exit_code=1 if expected_failure else 0,
        failed_nodes=(spec.direct_failure,) if expected_failure else (),
        skipped_nodes=(),
        affected_assets=tuple(sorted(spec.affected_assets)),
        schema_fingerprint="a" * 64,
        profile_spec_sha256="b" * 64,
    )
    inventory = tuple(record.evidence_id for record in kernel.evidence_records)
    claims = tuple(
        RootCauseClaim(
            kind="ROOT_CAUSE", root_cause_code=claim.value, evidence_ids=claim.evidence_ids
        )
        if claim.kind is ClaimKind.ROOT_CAUSE
        else AffectedAssetClaim(
            kind="AFFECTED_ASSET", asset=claim.value, evidence_ids=claim.evidence_ids
        )
        for claim in kernel.snapshot(model_requests_used=0).claims
    )
    diagnosis = Diagnosis(
        status=DiagnosisStatus(outcome.status.value),
        run_id=run_id,
        root_cause_code=outcome.root_cause_code,
        affected_assets=outcome.affected_assets,
        evidence_ids=inventory,
        claims=claims,
        unresolved_evidence=outcome.unresolved_evidence,
        summary=outcome.summary,
        recommended_actions=outcome.recommended_actions,
        confidence=outcome.confidence,
    )
    trace = [
        ToolTraceEvent(
            event_type="TOOL_CALL",
            tool_name=tool,
            arguments=args,
            fingerprint="0" * 64,
            evidence_ids=accepted,
            error_code=error,
            elapsed_ms=0,
        )
        for tool, args, error, accepted in log
    ]
    trace.append(KernelStateTraceEvent(event_type="KERNEL_STATE", state=snapshot))
    trace.append(
        DiagnosisTerminalTraceEvent(
            event_type="DIAGNOSIS_TERMINAL",
            strategy=DiagnosticStrategy.DIAGNOSTIC_KERNEL,
            status=DiagnosisStatus(outcome.status.value),
            evidence_inventory=inventory,
        )
    )
    result = DiagnosisRunResult(
        strategy=DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        policy_identity=policy_identity_for_strategy(DiagnosticStrategy.DIAGNOSTIC_KERNEL),
        diagnosis=diagnosis,
        evidence_records=kernel.evidence_records,
        trace=tuple(trace),
        metrics=DiagnosisMetrics(
            provider="synthetic",
            model="synthetic",
            model_requests=len(log),
            input_tokens=0,
            output_tokens=0,
            tool_call_attempts=len(log),
            successful_tool_calls=sum(1 for _, _, error, _a in log if error is None),
            elapsed_ms=0,
        ),
        kernel_state=snapshot,
    )
    evaluation = DeterministicEvaluator.evaluate(
        spec, verification, result, recovery_succeeded=True
    )
    return outcome, snapshot, evaluation


def _failed_checks(evaluation) -> list[str]:
    return [
        check.code.value
        for check in evaluation.checks
        if not check.passed and check.applicability.value == "APPLICABLE"
    ]


# -------------------------------------------------------------------- seq44


def _seq44_plan() -> list[tuple]:
    return [
        _call("LOCATE_FAILURE", "get_dbt_run_results", RUN_RESULTS, hyps=SCHEMA_HYPS),
        _call("EXPLAIN_FAILURE", "get_dbt_node_error", NODE_ERROR),
        _lineage_call("upstream", GENERATED_LINEAGE),
        _call("PROFILE_RELATION", "get_relation_data_profile", _profile("raw_orders")),
        _call("COMPARE_HISTORY", "get_relation_history", _history("raw_orders")),
        # raw_orders is outside the schema whitelist: the decisive receipt probe.
        _call("DISCRIMINATE_SCHEMA", "get_relation_schema", None, {"relation_name": PROBE}),
    ]


def _seq44_run_shape() -> list[tuple]:
    """The eight calls the run actually made: all successful, no probe."""

    return [
        _call("LOCATE_FAILURE", "get_dbt_run_results", RUN_RESULTS, hyps=SCHEMA_HYPS),
        _call("EXPLAIN_FAILURE", "get_dbt_node_error", NODE_ERROR),
        _call("DISCRIMINATE_SCHEMA", "get_relation_schema", _schema("raw_customers")),
        _call("DISCRIMINATE_SCHEMA", "get_relation_schema", _schema("raw_payments")),
        _call("PROFILE_RELATION", "get_relation_data_profile", _profile("raw_customers")),
        _call("PROFILE_RELATION", "get_relation_data_profile", _profile("raw_orders")),
        _call("PROFILE_RELATION", "get_relation_data_profile", _profile("raw_payments")),
        _call("COMPARE_HISTORY", "get_relation_history", _history("raw_orders")),
    ]


def _seq44_decision() -> KernelDecision:
    return KernelDecision(
        status="INSUFFICIENT_EVIDENCE",
        run_id=_inputs(44)["source_run_id"],
        unresolved_evidence=(
            {
                "evidence_kind": "TRANSFORMATION_DEFINITION",
                "subject": "model.jaffle_shop.stg_orders",
                "reason_code": "NOT_OBSERVABLE",
            },
        ),
        summary="The raw_orders schema is not observable in this run.",
        recommended_actions=("Request read access for raw_orders.",),
        confidence=0.3,
    )


def test_seq44_six_calls_reach_a_passing_abstention() -> None:
    """The accepted route costs six of eight calls, including the lineage call
    the run missed and the probe it never had budget for."""

    kernel = _kernel(44)
    log = _replay(44, kernel, _seq44_plan())
    outcome, snapshot, evaluation = _evaluate(44, kernel, _seq44_decision(), log)

    assert snapshot.tool_calls_used == 6
    assert log[-1][2] == RECEIPT
    assert outcome.status.value == "INSUFFICIENT_EVIDENCE"
    declared = [(item.evidence_kind, item.subject) for item in outcome.unresolved_evidence]
    assert declared == [
        ("RELATION_SCHEMA", "raw_orders"),
        ("TRANSFORMATION_DEFINITION", "model.jaffle_shop.stg_orders"),
    ]
    assert evaluation.status is EvaluationStatus.PASSED, _failed_checks(evaluation)


def test_seq44_the_runs_own_eight_calls_cannot_reach_the_receipt() -> None:
    """All eight calls succeeded, yet the probe is no longer affordable and the
    required lineage record was never collected at all."""

    kernel = _kernel(44)
    log = _replay(44, kernel, _seq44_run_shape())

    assert len(log) == 8
    assert all(error is None for _, _, error, _a in log), "the run's calls all succeeded"
    with pytest.raises(KernelError) as error:
        kernel.prepare_tool(
            intent=InvestigationIntent(
                gap_id="g_probe", gap_kind=EvidenceGapKind.DISCRIMINATE_SCHEMA
            ),
            tool_name="get_relation_schema",
            arguments={"relation_name": "raw_orders"},
        )
    assert error.value.code == "TOOL_CALL_LIMIT"
    collected = {record.evidence_type.value for record in kernel.evidence_records}
    assert "DBT_LINEAGE" not in collected


# -------------------------------------------------------------------- seq19


def _seq19_plan(second_probe: bool = True) -> list[tuple]:
    plan = [
        _call("LOCATE_FAILURE", "get_dbt_run_results", RUN_RESULTS, hyps=PAYMENT_HYPS),
        _lineage_call("downstream", DOWNSTREAM_LINEAGE),
        _call("PROFILE_RELATION", "get_relation_data_profile", _profile("raw_payments")),
        _call("COMPARE_HISTORY", "get_relation_history", None, {"relation_name": "raw_payments"}),
    ]
    if second_probe:
        plan.append(
            _call(
                "COMPARE_HISTORY",
                "get_relation_history",
                None,
                {"relation_name": "raw_orders"},
            )
        )
    return plan


def _seq19_decision(with_watermark: bool, inputs: dict | None = None) -> KernelDecision:
    """The declared subject is read from the public settled-window observation."""

    source = inputs or _inputs(19)
    declarations: tuple[dict[str, str], ...] = ()
    if with_watermark:
        declarations = (
            {
                "evidence_kind": "INGESTION_WATERMARK",
                "subject": _observation_subject(source, "SETTLED_PAYMENT_WINDOW_END"),
                "reason_code": "NOT_OBSERVABLE",
            },
        )
    return KernelDecision(
        status="INSUFFICIENT_EVIDENCE",
        run_id=source["source_run_id"],
        unresolved_evidence=declarations,
        summary="Payment and order history are not observable in this run.",
        recommended_actions=("Grant read access for payment history.",),
        confidence=0.3,
    )


def test_seq19_two_receipts_plus_a_watermark_declaration_pass() -> None:
    kernel = _kernel(19)
    log = _replay(19, kernel, _seq19_plan())
    outcome, snapshot, evaluation = _evaluate(19, kernel, _seq19_decision(True), log)

    assert snapshot.tool_calls_used == 5
    assert outcome.status.value == "INSUFFICIENT_EVIDENCE"
    declared = [(item.evidence_kind, item.subject) for item in outcome.unresolved_evidence]
    assert declared == [
        ("RELATION_HISTORY", "raw_payments"),
        ("RELATION_HISTORY", "raw_orders"),
        ("INGESTION_WATERMARK", "raw_orders"),
    ]
    assert evaluation.status is EvaluationStatus.PASSED, _failed_checks(evaluation)


def test_seq19_one_relation_receipt_leaves_the_other_gap_uncovered() -> None:
    """A single probe does not stand in for the second relation's gap."""

    kernel = _kernel(19)
    log = _replay(19, kernel, _seq19_plan(second_probe=False))

    assert log[-1][2] == RECEIPT
    outcome, _snapshot, evaluation = _evaluate(19, kernel, _seq19_decision(True), log)

    declared = [(item.evidence_kind, item.subject) for item in outcome.unresolved_evidence]
    assert declared == [
        ("RELATION_HISTORY", "raw_payments"),
        ("INGESTION_WATERMARK", "raw_orders"),
    ]
    assert GAP_CHECK in _failed_checks(evaluation)
    gap_check = next(
        check for check in evaluation.checks if check.code.value == GAP_CHECK
    )
    assert gap_check.actual == (GAP_MATRIX_INVALID,)


def test_seq19_receipts_alone_do_not_declare_the_watermark() -> None:
    kernel = _kernel(19)
    log = _replay(19, kernel, _seq19_plan())
    outcome, _snapshot, evaluation = _evaluate(19, kernel, _seq19_decision(False), log)

    kinds = [item.evidence_kind for item in outcome.unresolved_evidence]
    assert "INGESTION_WATERMARK" not in kinds
    assert GAP_CHECK in _failed_checks(evaluation)
    gap_check = next(
        check for check in evaluation.checks if check.code.value == GAP_CHECK
    )
    assert gap_check.actual == (GAP_MATRIX_INVALID,)


def _seq19_role_swapped_inputs() -> dict:
    """The same public brief with the settled-window role held by the other relation.

    Both relations stay legitimate incident subjects, and ``raw_orders`` is now
    listed first, so a subject taken from a name or from the brief's order would
    disagree with the observation.
    """

    inputs = _inputs(19)
    inputs["incident_subjects"] = [
        "seed.jaffle_shop.raw_payments",
        "raw_orders",
        "raw_payments",
        "payment_count_by_order_date",
    ]
    inputs["observations"] = [
        {**observation, "subject": "raw_payments"}
        if observation["kind"] == "SETTLED_PAYMENT_WINDOW_END"
        else observation
        for observation in inputs["observations"]
    ]
    return inputs


def test_seq19_watermark_subject_follows_the_public_role_not_the_name() -> None:
    """Both relations are legitimate subjects in both briefs, so only the
    settled-window observation decides which one the declaration names. The swapped
    brief is judged by the kernel alone: this is public derivability and
    expressibility, never the original case's private expectations."""

    original = _inputs(19)
    swapped = _seq19_role_swapped_inputs()

    def rank(inputs: dict) -> dict[str, int]:
        return {name: index for index, name in enumerate(inputs["incident_subjects"])}

    assert rank(original)["raw_payments"] < rank(original)["raw_orders"]
    assert rank(swapped)["raw_orders"] < rank(swapped)["raw_payments"]
    assert _observation_subject(original, "SETTLED_PAYMENT_WINDOW_END") == "raw_orders"
    assert _observation_subject(swapped, "SETTLED_PAYMENT_WINDOW_END") == "raw_payments"

    kernel = _kernel(19, swapped)
    _replay(19, kernel, _seq19_plan())
    outcome = kernel.finalize(_seq19_decision(True, swapped))

    declared = [(item.evidence_kind, item.subject) for item in outcome.unresolved_evidence]
    assert declared == [
        ("RELATION_HISTORY", "raw_payments"),
        ("RELATION_HISTORY", "raw_orders"),
        ("INGESTION_WATERMARK", "raw_payments"),
    ]


# -------------------------------------------------------------------- seq50


def _seq50_plan() -> list[tuple]:
    return [
        _call("LOCATE_FAILURE", "get_dbt_run_results", RUN_RESULTS, hyps=PAYMENT_HYPS),
        _call("PROFILE_RELATION", "get_relation_data_profile", _profile("raw_payments")),
        _call("PROFILE_RELATION", "get_relation_data_profile", _profile("raw_orders")),
        _lineage_call("downstream", DOWNSTREAM_LINEAGE),
        _call("COMPARE_HISTORY", "get_relation_history", _history("raw_payments")),
        _call("COMPARE_HISTORY", "get_relation_history", _history("raw_orders")),
    ]


def _split_evidence(kernel: DiagnosticKernel) -> tuple[tuple[str, ...], tuple[str, ...]]:
    root = tuple(
        record.evidence_id
        for record in kernel.evidence_records
        if record.evidence_type.value != "DBT_LINEAGE"
    )
    lineage = tuple(
        record.evidence_id
        for record in kernel.evidence_records
        if record.evidence_type.value == "DBT_LINEAGE"
    )
    return root, lineage


def _seq50_decision(kernel: DiagnosticKernel, assets: tuple[str, ...]) -> KernelDecision:
    root, lineage = _split_evidence(kernel)
    return KernelDecision(
        status="CONFIRMED",
        run_id=_inputs(50)["source_run_id"],
        selected_hypothesis_id="h_loss",
        assessments=(
            HypothesisAssessment(
                hypothesis_id="h_loss", verdict=HypothesisVerdict.SUPPORTED, evidence_ids=root
            ),
            HypothesisAssessment(
                hypothesis_id="h_decline", verdict=HypothesisVerdict.REFUTED, evidence_ids=root
            ),
        ),
        claims=(
            ClaimEvidence(
                kind=ClaimKind.ROOT_CAUSE,
                value="SOURCE_PAYMENT_INGESTION_LOSS",
                relation_name="raw_payments",
                evidence_ids=root,
            ),
            *(
                ClaimEvidence(kind=ClaimKind.AFFECTED_ASSET, value=asset, evidence_ids=lineage)
                for asset in assets
            ),
        ),
        summary="Settled payments on the target date fell short of the expected count.",
        recommended_actions=("Investigate the payment ingestion window.",),
        confidence=0.6,
    )


def _seq50_assets() -> tuple[str, ...]:
    return tuple(
        node.node_id
        for node in _record_index(50)[DOWNSTREAM_LINEAGE].content.related_nodes
        if node.resource_type == "model"
    )


def _seq50_payment_series():
    return _record_index(50)[_history("raw_payments")].content.snapshot.histories[0]


def _seq50_health_decision(kernel: DiagnosticKernel) -> KernelDecision:
    series = _seq50_payment_series()
    return KernelDecision(
        status="NO_INCIDENT",
        run_id=_inputs(50)["source_run_id"],
        claims=(
            ClaimEvidence(
                kind=ClaimKind.HEALTH_STATE,
                value="raw_payments",
                relation_name="raw_payments",
                history_name=series.name,
                bucket="2018-03-23",
                current_value=3,
                evidence_ids=tuple(r.evidence_id for r in kernel.evidence_records),
            ),
        ),
        summary="The payment series is healthy.",
        recommended_actions=(),
        confidence=0.4,
    )


def test_seq50_six_calls_confirm_and_declare_all_three_assets() -> None:
    """The correct incident path submitted straight to a fresh kernel; the
    rejection-then-recovery control on a single kernel is the next test."""

    kernel = _kernel(50)
    log = _replay(50, kernel, _seq50_plan())
    assets = _seq50_assets()
    outcome, snapshot, evaluation = _evaluate(50, kernel, _seq50_decision(kernel, assets), log)

    assert snapshot.tool_calls_used == 6
    assert outcome.status.value == "CONFIRMED"
    assert outcome.root_cause_code == "SOURCE_PAYMENT_INGESTION_LOSS"
    assert outcome.affected_assets == assets
    assert len(assets) == 3
    assert evaluation.status is EvaluationStatus.PASSED, _failed_checks(evaluation)


def test_seq50_health_claim_for_the_payment_relation_is_structurally_refused() -> None:
    """Why the NO_INCIDENT route cannot be repaired by adding citations: the
    payment history carries no watermark or SLA, so a health claim on that
    relation is refused however it is cited."""

    kernel = _kernel(50)
    log = _replay(50, kernel, _seq50_plan())
    assert len(log) == 6

    series = _seq50_payment_series()
    assert series.watermark_column is None
    assert series.watermark_value is None
    assert series.sla_seconds is None

    with pytest.raises(KernelError) as error:
        kernel.finalize(_seq50_health_decision(kernel))

    assert error.value.code == "HEALTH_WATERMARK_NOT_PROVEN"


def test_seq50_recovers_on_the_same_kernel_after_the_health_claim_is_refused() -> None:
    """The refused healthy conclusion and the corrected incident conclusion share
    one kernel state: the refusal leaves no accepted claim behind, and the
    corrected decision is accepted on that same state."""

    kernel = _kernel(50)
    log = _replay(50, kernel, _seq50_plan())

    with pytest.raises(KernelError) as error:
        kernel.finalize(_seq50_health_decision(kernel))
    assert error.value.code == "HEALTH_WATERMARK_NOT_PROVEN"

    refused = kernel.snapshot(model_requests_used=len(log))
    assert refused.final_status is None
    assert refused.claims == ()

    outcome, _snapshot, evaluation = _evaluate(
        50, kernel, _seq50_decision(kernel, _seq50_assets()), log
    )

    assert outcome.status.value == "CONFIRMED"
    assert evaluation.status is EvaluationStatus.PASSED, _failed_checks(evaluation)
