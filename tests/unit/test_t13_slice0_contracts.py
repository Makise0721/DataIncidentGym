"""T13 slice 0: frozen target, observable_evidence.v2 contract, refusal witnesses.

Everything here is offline: the new type-change target is only *registered*
(the database dry run is a separate, authorized step), the v2 contract is
defined without exposing any new tool, and the per-target refusal rule is
pinned against synthetic traces while the v1 rule is proven unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from data_incident_gym.diagnosis import (
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    TargetRefusal,
    ToolTraceEvent,
    UnresolvedEvidence,
    refusal_witnessed,
)
from data_incident_gym.evaluation import _insufficiency_matches
from data_incident_gym.evidence_planner import evidence_planner_policy_identity
from data_incident_gym.lab import _DEPENDENT_VIEWS
from data_incident_gym.scenario_certification import _receipt_proved
from data_incident_gym.scenarios import (
    _TYPE_CHANGE_TARGETS,
    SUPPORTED_SCENARIO_IDS,
    ColumnTypeMutation,
    ObservableEvidenceContractV2,
    ScenarioSpec,
    load_scenario_spec,
)

RUN_ID = "c" * 32
FINGERPRINT = "d" * 64
PROFILE_TOOL = "get_relation_data_profile"


def _type_change(**overrides) -> ColumnTypeMutation:
    payload = {
        "kind": "COLUMN_TYPE_CHANGE",
        "relation": "raw_customers",
        "column": "id",
        "from_type": "integer",
        "to_type": "text",
    }
    payload.update(overrides)
    return ColumnTypeMutation.model_validate(payload)


# -- frozen target registration (T1′ new, T2′ reused) ------------------------


def test_the_t13_target_is_registered_and_strict() -> None:
    mutation = _type_change()

    assert (mutation.relation, mutation.column) == ("raw_customers", "id")
    assert (mutation.from_type, mutation.to_type) == ("integer", "text")


@pytest.mark.parametrize(
    "overrides",
    (
        {"to_type": "integer"},  # same type
        {"from_type": "text", "to_type": "integer"},  # wrong direction
        {"relation": "raw_payments", "column": "id"},  # unregistered pair
        {"relation": "raw_orders", "column": "id"},
        {"relation": "raw_customers", "column": "user_id"},
    ),
)
def test_unregistered_type_change_combinations_stay_rejected(overrides) -> None:
    with pytest.raises(ValidationError):
        _type_change(**overrides)


def test_the_pre_t13_targets_keep_their_exact_semantics() -> None:
    assert _TYPE_CHANGE_TARGETS == {
        ("raw_payments", "amount"): ("integer", "text"),
        ("raw_orders", "user_id"): ("integer", "text"),
        ("raw_customers", "id"): ("integer", "text"),
    }
    # T2′ reuses the frozen order-side target: no new registration needed.
    assert _type_change(relation="raw_orders", column="user_id")


def test_every_type_changeable_relation_has_its_dependent_view() -> None:
    """PostgreSQL refuses a type change while a staging view uses the column."""

    assert set(_DEPENDENT_VIEWS) >= {relation for relation, _ in _TYPE_CHANGE_TARGETS}
    assert _DEPENDENT_VIEWS["raw_customers"] == "stg_customers"


# -- observable_evidence v1 unchanged, v2 added ------------------------------


def test_v1_serialization_is_byte_stable_for_every_catalog_scenario() -> None:
    v1_keys = {
        "schema_version",
        "schema_relations",
        "profile_relations",
        "history_relations",
        "unresolved_gaps",
    }
    for case_id in SUPPORTED_SCENARIO_IDS:
        spec = load_scenario_spec(case_id)
        payload = spec.model_dump(mode="json")["observable_evidence_contract"]
        assert set(payload) == v1_keys, case_id
        # Round-trip through the union must preserve the digest the
        # certification/admission records carry.
        assert ScenarioSpec.model_validate(spec.model_dump(mode="json")).digest() == spec.digest()


def test_v2_contract_defaults_to_no_new_evidence() -> None:
    contract = ObservableEvidenceContractV2.model_validate(
        {
            "schema_version": "observable_evidence.v2",
            "schema_relations": ["raw_customers", "raw_orders"],
            "profile_relations": [],
            "history_relations": [],
            "unresolved_gaps": [],
        }
    )

    assert contract.expectation_relations == ()
    assert contract.definition_nodes == ()

    spec = _v2_scenario()
    assert type(spec.observable_evidence_contract) is ObservableEvidenceContractV2
    assert spec.observable_evidence_contract.definition_nodes == (
        "model.jaffle_shop.customers",
    )


@pytest.mark.parametrize(
    "overrides",
    (
        {"expectation_relations": ("raw_payments",)},  # not a schema relation
        {"definition_nodes": ("model.jaffle_shop.customers", "model.jaffle_shop.customers")},
        {"definition_nodes": ("  ",)},
        {"schema_relations": ("raw_customers", "raw_customers")},
    ),
)
def test_v2_whitelists_are_validated(overrides) -> None:
    payload = {
        "schema_version": "observable_evidence.v2",
        "schema_relations": ["raw_customers", "raw_orders"],
        "profile_relations": [],
        "history_relations": [],
        "unresolved_gaps": [],
    }
    payload.update(overrides)
    with pytest.raises(ValidationError):
        ObservableEvidenceContractV2.model_validate(payload)


def _v2_scenario() -> ScenarioSpec:
    payload = json.loads(
        Path("config/scenarios/required_null_order_customer_a.json").read_text(encoding="utf-8")
    )
    payload["observable_evidence_contract"]["schema_version"] = "observable_evidence.v2"
    payload["observable_evidence_contract"]["expectation_relations"] = ["raw_customers"]
    payload["observable_evidence_contract"]["definition_nodes"] = ["model.jaffle_shop.customers"]
    return ScenarioSpec.model_validate(payload)


# -- refusal witnesses: v2 per-target entries, v1 rule unchanged -------------


def _event(**overrides) -> ToolTraceEvent:
    payload = {
        "event_type": "TOOL_CALL",
        "tool_name": PROFILE_TOOL,
        "arguments": {"relation_name": "raw_orders"},
        "fingerprint": FINGERPRINT,
        "evidence_ids": (),
        "error_code": "RELATION_NOT_ALLOWED",
        "elapsed_ms": 1,
    }
    payload.update(overrides)
    return ToolTraceEvent.model_validate(payload)


def test_v1_refusal_rule_is_unchanged() -> None:
    event = _event()

    assert refusal_witnessed(
        (event,), tool_name=PROFILE_TOOL, target="raw_orders", code="RELATION_NOT_ALLOWED"
    )
    # Wrong code, wrong target, no refusal at all.
    assert not refusal_witnessed(
        (event,), tool_name=PROFILE_TOOL, target="raw_orders", code="RELATION_NOT_FOUND"
    )
    assert not refusal_witnessed(
        (event,), tool_name=PROFILE_TOOL, target="raw_customers", code="RELATION_NOT_ALLOWED"
    )
    assert not refusal_witnessed(
        (_event(error_code=None),),
        tool_name=PROFILE_TOOL,
        target="raw_orders",
        code="RELATION_NOT_ALLOWED",
    )
    # Two refusals for the same target keep the original "exactly one" rule.
    assert not refusal_witnessed(
        (event, event), tool_name=PROFILE_TOOL, target="raw_orders", code="RELATION_NOT_ALLOWED"
    )


def test_v2_batch_refusals_witness_only_their_own_entries() -> None:
    mixed = _event(
        error_code="TARGETS_REFUSED",
        arguments={"relation_names": "raw_customers,raw_orders"},
        target_refusals=(
            TargetRefusal(target="raw_customers", code="RELATION_NOT_ALLOWED"),
            TargetRefusal(target="raw_orders", code="RELATION_NOT_FOUND"),
        ),
    )

    assert refusal_witnessed(
        (mixed,), tool_name=PROFILE_TOOL, target="raw_customers", code="RELATION_NOT_ALLOWED"
    )
    assert refusal_witnessed(
        (mixed,), tool_name=PROFILE_TOOL, target="raw_orders", code="RELATION_NOT_FOUND"
    )
    # Mixed codes never cross over, and the call-level code never witnesses.
    assert not refusal_witnessed(
        (mixed,), tool_name=PROFILE_TOOL, target="raw_orders", code="RELATION_NOT_ALLOWED"
    )
    assert not refusal_witnessed(
        (mixed,), tool_name=PROFILE_TOOL, target="raw_customers", code="TARGETS_REFUSED"
    )


def test_v2_mixed_permission_never_witnesses_the_readable_target() -> None:
    """Request [readable raw_orders, forbidden raw_customers]: the refusal of the
    forbidden target must not support a gap about the readable one."""

    mixed = _event(
        error_code="TARGETS_REFUSED",
        arguments={"relation_names": "raw_orders,raw_customers"},
        target_refusals=(
            TargetRefusal(target="raw_customers", code="RELATION_NOT_ALLOWED"),
        ),
    )

    assert not refusal_witnessed(
        (mixed,), tool_name=PROFILE_TOOL, target="raw_orders", code="RELATION_NOT_ALLOWED"
    )
    assert refusal_witnessed(
        (mixed,), tool_name=PROFILE_TOOL, target="raw_customers", code="RELATION_NOT_ALLOWED"
    )


# -- the two archived-trace matchers share the rule --------------------------


def _insufficient_run(
    scenario: ScenarioSpec,
    tool_events: tuple[ToolTraceEvent, ...],
) -> DiagnosisRunResult:
    diagnosis = Diagnosis(
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        run_id=RUN_ID,
        summary="The decisive evidence is not observable.",
        unresolved_evidence=tuple(
            UnresolvedEvidence(
                evidence_kind=gap.gap_kind,
                subject=gap.subject,
                reason_code=gap.reason_code,
            )
            for gap in scenario.observable_evidence_contract.unresolved_gaps
        ),
        confidence=0.2,
    )
    terminal = DiagnosisTerminalTraceEvent(
        event_type="DIAGNOSIS_TERMINAL",
        strategy=DiagnosticStrategy.EVIDENCE_PLANNER,
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        evidence_inventory=(),
    )
    return DiagnosisRunResult(
        strategy=DiagnosticStrategy.EVIDENCE_PLANNER,
        policy_identity=evidence_planner_policy_identity(),
        diagnosis=diagnosis,
        evidence_records=(),
        trace=(*tool_events, terminal),
        metrics=DiagnosisMetrics(
            provider="test",
            model="test",
            model_requests=1,
            input_tokens=0,
            output_tokens=0,
            tool_call_attempts=len(tool_events),
            successful_tool_calls=0,
            elapsed_ms=1,
        ),
    )


def test_both_matchers_accept_v1_and_v2_refusals_for_the_same_gap() -> None:
    scenario = load_scenario_spec("required_null_order_customer_b")

    v1_trace = (_event(),)
    assert _receipt_proved(scenario, v1_trace)
    assert _insufficiency_matches(scenario, _insufficient_run(scenario, v1_trace))

    v2_trace = (
        _event(
            error_code="TARGETS_REFUSED",
            arguments={"relation_names": "raw_orders"},
            target_refusals=(
                TargetRefusal(target="raw_orders", code="RELATION_NOT_ALLOWED"),
            ),
        ),
    )
    assert _receipt_proved(scenario, v2_trace)
    assert _insufficiency_matches(scenario, _insufficient_run(scenario, v2_trace))


def test_a_wrong_per_target_code_fails_both_matchers() -> None:
    scenario = load_scenario_spec("required_null_order_customer_b")

    wrong = (
        _event(
            error_code="TARGETS_REFUSED",
            arguments={"relation_names": "raw_orders"},
            target_refusals=(TargetRefusal(target="raw_orders", code="RELATION_NOT_FOUND"),),
        ),
    )

    assert not _receipt_proved(scenario, wrong)
    assert not _insufficiency_matches(scenario, _insufficient_run(scenario, wrong))
