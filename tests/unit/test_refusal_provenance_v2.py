from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from pydantic_ai.exceptions import ToolFailed

from data_incident_gym.artifacts import (
    TraceEnvelope,
    TraceEnvelopeV2,
    trace_schema_for_policy_identity,
    validate_trace_envelope,
)
from data_incident_gym.benchmark_report import _refusal_witness_source_counts
from data_incident_gym.diagnosis import (
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisRunResultV2,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    EvidenceGateTraceEvent,
    TargetRefusal,
    ToolTraceEvent,
    ToolTraceEventV2,
    refusal_witnessed,
)
from data_incident_gym.diagnostic_agent import (
    _execute_evidence,
    _RunState,
    _StaticPolicyAdapter,
    policy_identity_for_strategy,
)
from data_incident_gym.evidence import TARGETS_REFUSED_CODE, RelationNotAllowedError
from data_incident_gym.evidence_planner import evidence_planner_policy_identity
from data_incident_gym.fixed_rule import fixed_rule_policy_identity
from data_incident_gym.run_context import IncidentBrief, ObservableRunContext
from data_incident_gym.strategy_adapter import (
    StrategySession,
    builtin_declaration,
)

FINGERPRINT = "a" * 64
RELATION_TOOL = "get_relation_data_profile"
RELATION = "raw_orders"
NODE_TOOL = "get_dbt_node_definition"
NODE = "model.jaffle_shop.customers"


def _single_event(
    origin: str,
    *,
    error_code: str = "RELATION_NOT_ALLOWED",
) -> ToolTraceEventV2:
    return ToolTraceEventV2(
        event_type="TOOL_CALL_V2",
        tool_name=RELATION_TOOL,
        arguments={"relation_name": RELATION},
        fingerprint=FINGERPRINT,
        evidence_ids=(),
        error_code=error_code,
        elapsed_ms=1,
        outcome_origin=origin,
    )


def _batch_event(
    *,
    origin: str = "EVIDENCE_BACKEND",
    error_code: str = TARGETS_REFUSED_CODE,
    target_refusals: tuple[TargetRefusal, ...] = (
        TargetRefusal(target=NODE, code="NODE_NOT_ALLOWED"),
    ),
) -> ToolTraceEventV2:
    return ToolTraceEventV2(
        event_type="TOOL_CALL_V2",
        tool_name=NODE_TOOL,
        arguments={"node_ids": NODE},
        fingerprint=FINGERPRINT,
        evidence_ids=(),
        error_code=error_code,
        target_refusals=target_refusals,
        elapsed_ms=1,
        outcome_origin=origin,
    )


def test_v2_single_target_witness_keeps_precheck_and_backend_sources() -> None:
    for origin in ("CONTROLLER_PRECHECK", "EVIDENCE_BACKEND"):
        result = refusal_witnessed(
            (_single_event(origin),),
            tool_name=RELATION_TOOL,
            target=RELATION,
            code="RELATION_NOT_ALLOWED",
            diagnosis_run_schema_version="p1.diagnosis_run.v2",
        )
        assert result.witnessed is True
        assert result.outcome_origin == origin
        assert result.event_fingerprint == FINGERPRINT
        assert result.trace_sequence == 1
        assert result.failure_reason is None


@pytest.mark.parametrize(
    "origin",
    ("CONTROLLER_POSTCHECK", "PROTOCOL_GATE", "TOOL_RUNTIME"),
)
def test_v2_nonqualifying_single_target_sources_fail_closed(origin: str) -> None:
    result = refusal_witnessed(
        (_single_event(origin),),
        tool_name=RELATION_TOOL,
        target=RELATION,
        code="RELATION_NOT_ALLOWED",
        diagnosis_run_schema_version="p1.diagnosis_run.v2",
    )

    assert result.witnessed is False
    assert result.outcome_origin == origin
    assert result.failure_reason == "SOURCE_NOT_QUALIFIED"


def test_v2_t13_requires_one_backend_atomic_target_receipt() -> None:
    witnessed = refusal_witnessed(
        (_batch_event(),),
        tool_name=NODE_TOOL,
        target=NODE,
        code="NODE_NOT_ALLOWED",
        diagnosis_run_schema_version="p1.diagnosis_run.v2",
    )
    duplicate = refusal_witnessed(
        (
            _batch_event(),
            _batch_event(
                origin="PROTOCOL_GATE",
                error_code="EVIDENCE_TOOL_ERROR",
                target_refusals=(),
            ),
        ),
        tool_name=NODE_TOOL,
        target=NODE,
        code="NODE_NOT_ALLOWED",
        diagnosis_run_schema_version="p1.diagnosis_run.v2",
    )

    assert witnessed.witnessed is True
    assert witnessed.outcome_origin == "EVIDENCE_BACKEND"
    assert duplicate.witnessed is False
    assert duplicate.failure_reason == "AMBIGUOUS"


def test_v2_envelope_and_run_schema_mismatch_is_rejected() -> None:
    event = _single_event("EVIDENCE_BACKEND")
    payload = {
        "schema_version": "p1.trace.v2",
        "sequence": 1,
        "event": event.model_dump(mode="json"),
    }

    parsed = validate_trace_envelope(payload, expected_schema_version="p1.trace.v2")
    assert isinstance(parsed, TraceEnvelopeV2)
    with pytest.raises(ValueError, match="does not match"):
        validate_trace_envelope(payload, expected_schema_version="p1.trace.v1")
    with pytest.raises(ValidationError):
        TraceEnvelope.model_validate(payload)


def test_trace_schema_selection_uses_trusted_policy_identity() -> None:
    model_policy = policy_identity_for_strategy(DiagnosticStrategy.DIAGNOSTIC_KERNEL)
    planner_policy = evidence_planner_policy_identity()

    assert model_policy.controller_protocol_version == "p1.controller.v22"
    assert trace_schema_for_policy_identity(model_policy) == "p1.trace.v3"
    assert planner_policy.controller_protocol_version == "p1.planner_controller.v2"
    assert trace_schema_for_policy_identity(planner_policy) == "p1.trace.v1"


def _run_for_identity(
    run_model: type[DiagnosisRunResult] | type[DiagnosisRunResultV2],
    strategy: DiagnosticStrategy,
    policy_identity,
):
    run_id = "d" * 32
    diagnosis = Diagnosis(
        status=DiagnosisStatus.MODEL_ERROR,
        run_id=run_id,
        summary="MODEL_RUNTIME_ERROR",
        confidence=0.0,
    )
    return run_model(
        strategy=strategy,
        policy_identity=policy_identity,
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
        metrics=DiagnosisMetrics(
            provider="synthetic",
            model="synthetic",
            model_requests=0,
            input_tokens=0,
            output_tokens=0,
            tool_call_attempts=0,
            successful_tool_calls=0,
            elapsed_ms=0,
        ),
    )


@pytest.mark.parametrize(
    ("run_model", "protocol_version"),
    (
        (DiagnosisRunResult, "p1.controller.v21"),
        (DiagnosisRunResultV2, "p1.controller.v20"),
    ),
)
def test_diagnosis_run_rejects_crossed_protocol_versions(
    run_model: type[DiagnosisRunResult] | type[DiagnosisRunResultV2],
    protocol_version: str,
) -> None:
    identity = policy_identity_for_strategy(DiagnosticStrategy.STATIC_SKILL).model_copy(
        update={"controller_protocol_version": protocol_version}
    )

    with pytest.raises(ValidationError):
        _run_for_identity(run_model, DiagnosticStrategy.STATIC_SKILL, identity)


def test_fixed_rule_and_planner_runs_remain_v1() -> None:
    fixed = _run_for_identity(
        DiagnosisRunResult,
        DiagnosticStrategy.FIXED_RULE,
        fixed_rule_policy_identity(),
    )
    planner = _run_for_identity(
        DiagnosisRunResult,
        DiagnosticStrategy.EVIDENCE_PLANNER,
        evidence_planner_policy_identity(),
    )

    assert fixed.schema_version == "p1.diagnosis.v1"
    assert planner.schema_version == "p1.diagnosis.v1"


def test_v2_run_rejects_non_model_strategy_even_with_v21_policy() -> None:
    identity = fixed_rule_policy_identity().model_copy(
        update={"controller_protocol_version": "p1.controller.v21"}
    )

    with pytest.raises(ValidationError, match="reserved for built-in model strategies"):
        _run_for_identity(DiagnosisRunResultV2, DiagnosticStrategy.FIXED_RULE, identity)


def test_v1_witness_is_explicitly_legacy_unattributed() -> None:
    event = ToolTraceEvent(
        event_type="TOOL_CALL",
        tool_name=RELATION_TOOL,
        arguments={"relation_name": RELATION},
        fingerprint=FINGERPRINT,
        evidence_ids=(),
        error_code="RELATION_NOT_ALLOWED",
        elapsed_ms=1,
    )

    result = refusal_witnessed(
        (event,),
        tool_name=RELATION_TOOL,
        target=RELATION,
        code="RELATION_NOT_ALLOWED",
        diagnosis_run_schema_version="p1.diagnosis.v1",
    )

    assert result.witnessed is True
    assert result.outcome_origin == "LEGACY_UNATTRIBUTED"


def test_witness_sequence_is_the_full_trace_sequence() -> None:
    gate_event = EvidenceGateTraceEvent(
        event_type="EVIDENCE_GATE", reason_code="GATE_ACCEPTED", accepted=True
    )
    result = refusal_witnessed(
        (gate_event, _single_event("EVIDENCE_BACKEND")),
        tool_name=RELATION_TOOL,
        target=RELATION,
        code="RELATION_NOT_ALLOWED",
        diagnosis_run_schema_version="p1.diagnosis_run.v2",
    )

    assert result.witnessed is True
    assert result.trace_sequence == 2


def test_report_source_metrics_keep_a_fixed_descriptive_denominator() -> None:
    gap = SimpleNamespace(
        tool_name=RELATION_TOOL,
        subject=RELATION,
        reason_code="RELATION_NOT_ALLOWED",
        gap_kind="RELATION_DATA_PROFILE",
    )
    scenario = SimpleNamespace(
        expected_status="INSUFFICIENT_EVIDENCE",
        observable_evidence_contract=SimpleNamespace(unresolved_gaps=(gap,)),
    )
    diagnosis = SimpleNamespace(
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        unresolved_evidence=(
            SimpleNamespace(
                evidence_kind="RELATION_DATA_PROFILE",
                subject=RELATION,
                reason_code="RELATION_NOT_ALLOWED",
            ),
        ),
    )
    items = [
        {
            "scenario": scenario,
            "diagnosis": diagnosis,
            "trace": (SimpleNamespace(event=_single_event("CONTROLLER_PRECHECK")),),
        },
        {
            "scenario": scenario,
            "diagnosis": diagnosis,
            "trace": (SimpleNamespace(event=_single_event("EVIDENCE_BACKEND")),),
        },
        {
            "scenario": scenario,
            "diagnosis": SimpleNamespace(
                status=DiagnosisStatus.CONFIRMED, unresolved_evidence=()
            ),
            "trace": (SimpleNamespace(event=_single_event("EVIDENCE_BACKEND")),),
        },
    ]

    counts = _refusal_witness_source_counts(items, "p1.diagnosis_run.v2")

    assert counts["expected_tool_bound_gaps"] == 3
    assert counts["eligible_tool_bound_gaps"] == 2
    assert counts["gap_set_mismatch_cells"] == 0
    assert counts["inapplicable_tool_bound_gaps"] == 1
    assert counts["inapplicable_reasons"]["RUN_NOT_INSUFFICIENT_EVIDENCE"] == 1
    assert counts["CONTROLLER_PRECHECK"] == {
        "numerator": 1,
        "denominator": 2,
        "rate": 0.5,
        "zero_denominator_reason": None,
    }
    assert counts["EVIDENCE_BACKEND"]["numerator"] == 1
    assert counts["LEGACY_UNATTRIBUTED"]["numerator"] == 0

    empty = _refusal_witness_source_counts(
        [items[-1]], "p1.diagnosis_run.v2"
    )
    assert empty["eligible_tool_bound_gaps"] == 0
    assert empty["EVIDENCE_BACKEND"]["rate"] is None
    assert empty["EVIDENCE_BACKEND"]["zero_denominator_reason"] == (
        "NO_APPLICABLE_DECLARED_TOOL_GAPS"
    )


def test_source_metrics_keep_matched_gaps_from_a_mismatched_cell() -> None:
    extra_gap = SimpleNamespace(
        evidence_kind="OTHER_EVIDENCE",
        subject="unexpected.subject",
        reason_code="UNEXPECTED_GAP",
    )
    missing_contract_gap = SimpleNamespace(
        tool_name=NODE_TOOL,
        subject=NODE,
        reason_code="NODE_NOT_ALLOWED",
        gap_kind="NODE_DEFINITION",
    )
    scenario = SimpleNamespace(
        expected_status="INSUFFICIENT_EVIDENCE",
        observable_evidence_contract=SimpleNamespace(
            unresolved_gaps=(
                SimpleNamespace(
                    tool_name=RELATION_TOOL,
                    subject=RELATION,
                    reason_code="RELATION_NOT_ALLOWED",
                    gap_kind="RELATION_DATA_PROFILE",
                ),
                missing_contract_gap,
            )
        ),
    )
    diagnosis = SimpleNamespace(
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        unresolved_evidence=(
            SimpleNamespace(
                evidence_kind="RELATION_DATA_PROFILE",
                subject=RELATION,
                reason_code="RELATION_NOT_ALLOWED",
            ),
            extra_gap,
        ),
    )

    counts = _refusal_witness_source_counts(
        [
            {
                "scenario": scenario,
                "diagnosis": diagnosis,
                "trace": (
                    SimpleNamespace(event=_single_event("CONTROLLER_PRECHECK")),
                ),
            }
        ],
        "p1.diagnosis_run.v2",
    )

    assert counts["expected_tool_bound_gaps"] == 2
    assert counts["eligible_tool_bound_gaps"] == 1
    assert counts["inapplicable_tool_bound_gaps"] == 1
    assert counts["inapplicable_reasons"]["CONTRACT_GAP_NOT_DECLARED"] == 1
    assert counts["gap_set_mismatch_cells"] == 1
    assert counts["CONTROLLER_PRECHECK"]["numerator"] == 1
    assert counts["CONTROLLER_PRECHECK"]["denominator"] == 1


class _RefusingBackend:
    def __init__(self, *, fail_uncontrolled: bool = False) -> None:
        self.dispatches = 0
        self.fail_uncontrolled = fail_uncontrolled

    def get_relation_data_profile(self, relation_name: str) -> tuple[()]:
        self.dispatches += 1
        if self.fail_uncontrolled:
            raise RuntimeError("synthetic backend crash")
        if relation_name == "raw_customers":
            raise RelationNotAllowedError(relation_name)
        raise AssertionError("unexpected relation reached backend")


def _state_with_protocol_tools(backend: _RefusingBackend):
    run_id = "c" * 32
    context = ObservableRunContext(
        run_id=run_id,
        artifact_dir=Path("."),
        runtime={"observable_relations": {"profile": ["raw_customers", "raw_orders"]}},
        incident_brief=IncidentBrief(
            schema_version="incident_brief.v1",
            signal_code="DBT_TEST_FAILED",
            summary="A dbt test failed.",
            subjects=("test.jaffle_shop.orders",),
            logical_observed_at=datetime(2026, 9, 24, tzinfo=UTC),
            observations=(),
        ),
    )
    session = StrategySession(
        run_id=run_id,
        tools=backend,
        context=context,
        declaration=builtin_declaration(
            model_provider="synthetic", model_name="refusal-provenance-test", deterministic=True
        ),
    )
    state = _RunState(
        run_id=run_id,
        strategy=DiagnosticStrategy.STATIC_SKILL,
        tools=session.tools_facade(),
        context=context,
        adapter=None,  # type: ignore[arg-type]
    )
    state.adapter = _StaticPolicyAdapter(state, tool_call_limit=8)
    return state, session


def _execute_profile_call(state: _RunState, relation_name: str) -> None:
    _execute_evidence(
        SimpleNamespace(deps=state),
        RELATION_TOOL,
        {"relation_name": relation_name},
        lambda: state.tools.get_relation_data_profile(relation_name),
    )


def test_execute_evidence_keeps_backend_then_protocol_gate_sources_separate() -> None:
    backend = _RefusingBackend()
    state, session = _state_with_protocol_tools(backend)

    with pytest.raises(ToolFailed, match="RELATION_NOT_ALLOWED"):
        _execute_profile_call(state, "raw_customers")
    assert state.trace[-1].outcome_origin == "EVIDENCE_BACKEND"
    assert backend.dispatches == 1

    session.cancel("STRATEGY_CANCELLED")
    with pytest.raises(ToolFailed, match="EVIDENCE_TOOL_ERROR"):
        _execute_profile_call(state, "raw_orders")

    assert state.trace[-1].outcome_origin == "PROTOCOL_GATE"
    assert state.trace[-1].error_code == "EVIDENCE_TOOL_ERROR"
    assert backend.dispatches == 1


def test_execute_evidence_marks_post_dispatch_runtime_failure() -> None:
    backend = _RefusingBackend(fail_uncontrolled=True)
    state, _session = _state_with_protocol_tools(backend)

    with pytest.raises(ToolFailed, match="EVIDENCE_TOOL_ERROR"):
        _execute_profile_call(state, "raw_customers")

    assert state.trace[-1].outcome_origin == "TOOL_RUNTIME"
    assert backend.dispatches == 1
