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
    DiagnosisV2,
    DiagnosticStrategy,
    TargetRefusal,
    ToolTraceEvent,
    UnresolvedEvidence,
    UnresolvedEvidenceV2,
)
from data_incident_gym.diagnosis import (
    refusal_witnessed as _refusal_witnessed,
)
from data_incident_gym.evaluation import _insufficiency_matches
from data_incident_gym.evidence import EVIDENCE_BATCH_TOOLS, TARGETS_REFUSED_CODE
from data_incident_gym.evidence_planner import evidence_planner_policy_identity
from data_incident_gym.lab import _DEPENDENT_VIEWS
from data_incident_gym.scenario_certification import _receipt_proved
from data_incident_gym.scenarios import (
    _TYPE_CHANGE_TARGETS,
    P1_T13_PUBLIC_EVIDENCE_IDS,
    SUPPORTED_SCENARIO_IDS,
    ColumnTypeMutation,
    ObservableEvidenceContractV2,
    ScenarioSpec,
    load_scenario_spec,
)

RUN_ID = "c" * 32
FINGERPRINT = "d" * 64
PROFILE_TOOL = "get_relation_data_profile"
CUSTOMERS = "model.jaffle_shop.customers"
STG_ORDERS = "model.jaffle_shop.stg_orders"


def refusal_witnessed(*args, **kwargs) -> bool:
    """Keep legacy assertions explicit about their trusted v1 run schema."""

    kwargs["diagnosis_run_schema_version"] = "p1.diagnosis.v1"
    return _refusal_witnessed(*args, **kwargs).witnessed


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


def test_v1_serialization_is_byte_stable_for_every_v1_scenario() -> None:
    v1_keys = {
        "schema_version",
        "schema_relations",
        "profile_relations",
        "history_relations",
        "unresolved_gaps",
    }
    v2_case_ids = set(P1_T13_PUBLIC_EVIDENCE_IDS)
    for case_id in SUPPORTED_SCENARIO_IDS:
        spec = load_scenario_spec(case_id)
        payload = spec.model_dump(mode="json")["observable_evidence_contract"]
        if case_id in v2_case_ids:
            # The T13 pairs are the only scenarios on the v2 contract.
            assert payload["schema_version"] == "observable_evidence.v2", case_id
            continue
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
    assert spec.observable_evidence_contract.definition_nodes == (CUSTOMERS,)


@pytest.mark.parametrize(
    "overrides",
    (
        {"expectation_relations": ("raw_payments",)},  # not a schema relation
        {"definition_nodes": (CUSTOMERS, CUSTOMERS)},
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


def test_v2_gaps_express_the_designed_e1_e2_evidence() -> None:
    scenario = _v2_b_scenario()
    gaps = scenario.observable_evidence_contract.unresolved_gaps

    assert [(gap.gap_kind, gap.subject, gap.tool_name, gap.reason_code) for gap in gaps] == [
        ("RELATION_SCHEMA_EXPECTATION", "raw_customers", "get_relation_schema_expectation",
         "RELATION_NOT_ALLOWED"),
        ("DBT_NODE_DEFINITION", CUSTOMERS, "get_dbt_node_definition", "NODE_NOT_ALLOWED"),
    ]
    # The v2 vocabulary cannot be smuggled into a v1 contract.
    v1_payload = json.loads(
        Path("config/scenarios/required_null_order_customer_b.json").read_text(encoding="utf-8")
    )
    v1_payload["observable_evidence_contract"]["unresolved_gaps"][1] = {
        "gap_kind": "DBT_NODE_DEFINITION",
        "subject": CUSTOMERS,
        "reason_code": "NODE_NOT_ALLOWED",
        "tool_name": "get_dbt_node_definition",
    }
    with pytest.raises(ValidationError):
        ScenarioSpec.model_validate(v1_payload)


def _v2_scenario() -> ScenarioSpec:
    payload = json.loads(
        Path("config/scenarios/required_null_order_customer_a.json").read_text(encoding="utf-8")
    )
    payload["observable_evidence_contract"]["schema_version"] = "observable_evidence.v2"
    payload["observable_evidence_contract"]["expectation_relations"] = ["raw_customers"]
    payload["observable_evidence_contract"]["definition_nodes"] = [CUSTOMERS]
    return ScenarioSpec.model_validate(payload)


def _v2_b_scenario() -> ScenarioSpec:
    """A real v2 contract carrying the design's E1/E2 gaps."""

    payload = json.loads(
        Path("config/scenarios/type_change_payment_amount_drift_b.json").read_text(
            encoding="utf-8"
        )
    )
    payload["observable_evidence_contract"] = {
        "schema_version": "observable_evidence.v2",
        "schema_relations": ["raw_customers", "raw_orders"],
        "profile_relations": [],
        "history_relations": [],
        "expectation_relations": [],
        "definition_nodes": [],
        "unresolved_gaps": [
            {
                "gap_kind": "RELATION_SCHEMA_EXPECTATION",
                "subject": "raw_customers",
                "reason_code": "RELATION_NOT_ALLOWED",
                "tool_name": "get_relation_schema_expectation",
            },
            {
                "gap_kind": "DBT_NODE_DEFINITION",
                "subject": CUSTOMERS,
                "reason_code": "NODE_NOT_ALLOWED",
                "tool_name": "get_dbt_node_definition",
            },
        ],
    }
    return ScenarioSpec.model_validate(payload)


# -- refusal witnesses: explicit protocol identity, v1 rule unchanged --------


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


def _batch_event(**overrides) -> ToolTraceEvent:
    payload = {
        "event_type": "TOOL_CALL",
        "tool_name": "get_dbt_node_definition",
        "arguments": {"node_ids": f"{CUSTOMERS},{STG_ORDERS}"},
        "fingerprint": FINGERPRINT,
        "evidence_ids": (),
        "error_code": TARGETS_REFUSED_CODE,
        "elapsed_ms": 1,
        "target_refusals": (
            TargetRefusal(target=CUSTOMERS, code="NODE_NOT_ALLOWED"),
            TargetRefusal(target=STG_ORDERS, code="NODE_NOT_FOUND"),
        ),
    }
    payload.update(overrides)
    return ToolTraceEvent.model_validate(payload)


def test_the_batch_tool_identity_is_frozen() -> None:
    assert EVIDENCE_BATCH_TOOLS == (
        "get_relation_schema_expectation",
        "get_dbt_node_definition",
    )
    assert PROFILE_TOOL not in EVIDENCE_BATCH_TOOLS


def test_v1_refusal_rule_is_unchanged() -> None:
    event = _event()

    assert refusal_witnessed(
        (event,), tool_name=PROFILE_TOOL, target="raw_orders", code="RELATION_NOT_ALLOWED"
    )
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


def test_a_v1_tool_cannot_carry_refusal_entries() -> None:
    """Audit reproduction: a successful old-tool event with refusal entries used
    to witness a refusal, because the rule dispatched on field content."""

    with pytest.raises(ValidationError):
        _event(target_refusals=(TargetRefusal(target="raw_orders", code="RELATION_NOT_ALLOWED"),))
    with pytest.raises(ValidationError):
        _event(error_code=TARGETS_REFUSED_CODE)  # v1 tool with the summary code


def test_refusal_entries_require_an_atomic_refusal_event() -> None:
    entry = (TargetRefusal(target=CUSTOMERS, code="NODE_NOT_ALLOWED"),)

    # The call-level code must be the summary code, never None or a real code.
    with pytest.raises(ValidationError):
        _batch_event(error_code=None, target_refusals=entry)
    with pytest.raises(ValidationError):
        _batch_event(error_code="NODE_NOT_ALLOWED", target_refusals=entry)
    # A refused batch call returns no evidence.
    with pytest.raises(ValidationError):
        _batch_event(target_refusals=entry, evidence_ids=("ev_" + "a" * 64,))


def test_a_successful_batch_event_never_witnesses() -> None:
    success = _batch_event(target_refusals=(), error_code=None)

    assert not refusal_witnessed(
        (success,), tool_name="get_dbt_node_definition", target=CUSTOMERS, code="NODE_NOT_ALLOWED"
    )


def test_v2_witness_requires_the_target_to_be_in_the_recorded_request() -> None:
    event = _batch_event(
        target_refusals=(TargetRefusal(target=STG_ORDERS, code="NODE_NOT_ALLOWED"),),
        arguments={"node_ids": CUSTOMERS},
    )

    # The refusal entry alone is not enough: the target was never requested.
    assert not refusal_witnessed(
        (event,),
        tool_name="get_dbt_node_definition",
        target=STG_ORDERS,
        code="NODE_NOT_ALLOWED",
    )


def test_v2_batch_refusals_witness_only_their_own_entries() -> None:
    mixed = _batch_event()

    assert refusal_witnessed(
        (mixed,), tool_name="get_dbt_node_definition", target=CUSTOMERS, code="NODE_NOT_ALLOWED"
    )
    assert refusal_witnessed(
        (mixed,), tool_name="get_dbt_node_definition", target=STG_ORDERS, code="NODE_NOT_FOUND"
    )
    # Mixed codes never cross over, and the call-level code never witnesses.
    assert not refusal_witnessed(
        (mixed,), tool_name="get_dbt_node_definition", target=STG_ORDERS, code="NODE_NOT_ALLOWED"
    )
    assert not refusal_witnessed(
        (mixed,), tool_name="get_dbt_node_definition", target=CUSTOMERS,
        code=TARGETS_REFUSED_CODE
    )


def test_v2_mixed_permission_never_witnesses_the_readable_target() -> None:
    """Request [readable stg_orders, forbidden stg_payments]: the refusal of the
    forbidden target must not support a gap about the readable one."""

    mixed = _batch_event(
        arguments={"node_ids": f"{STG_ORDERS},model.jaffle_shop.stg_payments"},
        target_refusals=(
            TargetRefusal(target="model.jaffle_shop.stg_payments", code="NODE_NOT_ALLOWED"),
        ),
    )

    assert not refusal_witnessed(
        (mixed,), tool_name="get_dbt_node_definition", target=STG_ORDERS, code="NODE_NOT_ALLOWED"
    )
    assert refusal_witnessed(
        (mixed,),
        tool_name="get_dbt_node_definition",
        target="model.jaffle_shop.stg_payments",
        code="NODE_NOT_ALLOWED",
    )


# -- the two archived-trace matchers share the rule --------------------------


def _insufficient_run(
    scenario: ScenarioSpec,
    tool_events: tuple[ToolTraceEvent, ...],
) -> DiagnosisRunResult:
    """A v1-vocabulary insufficient run; v2 kinds use ``_insufficient_run_v2``."""

    return _run(scenario, tool_events, unresolved_type=UnresolvedEvidence)


def _insufficient_run_v2(
    scenario: ScenarioSpec,
    tool_events: tuple[ToolTraceEvent, ...],
) -> DiagnosisRunResult:
    """The v2 run: its diagnosis uses the separate v2 contract.

    In-memory only — the archived v2 run/reload path arrives with the v2
    strategy surface (T13 slice 4); the witness predicate itself is shared.
    """

    return _run(scenario, tool_events, unresolved_type=UnresolvedEvidenceV2)


def _run(
    scenario: ScenarioSpec,
    tool_events: tuple[ToolTraceEvent, ...],
    *,
    unresolved_type,
) -> DiagnosisRunResult:
    diagnosis_type = Diagnosis if unresolved_type is UnresolvedEvidence else DiagnosisV2
    diagnosis = diagnosis_type(
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        run_id=RUN_ID,
        summary="The decisive evidence is not observable.",
        unresolved_evidence=tuple(
            unresolved_type(
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


def test_both_matchers_keep_the_v1_rule_for_v1_scenarios() -> None:
    scenario = load_scenario_spec("required_null_order_customer_b")

    v1_trace = (_event(),)
    assert _receipt_proved(scenario, v1_trace)
    assert _insufficiency_matches(scenario, _insufficient_run(scenario, v1_trace))

    # A v2 event never satisfies a v1 gap: the rule follows the tool's
    # protocol identity on both sides of the split.
    assert not _receipt_proved(scenario, (_batch_event(),))


def test_a_real_v2_contract_loads_and_both_matchers_witness_its_gaps() -> None:
    scenario = _v2_b_scenario()

    trace = (
        _event(
            tool_name="get_relation_schema_expectation",
            arguments={"relation_names": "raw_customers,raw_orders"},
            error_code=TARGETS_REFUSED_CODE,
            target_refusals=(
                TargetRefusal(target="raw_customers", code="RELATION_NOT_ALLOWED"),
            ),
        ),
        _batch_event(),
    )

    assert _receipt_proved(scenario, trace)
    assert _insufficiency_matches(scenario, _insufficient_run_v2(scenario, trace))
    # The v2 vocabulary cannot be expressed by the v1 diagnosis contract.
    with pytest.raises(ValidationError):
        _insufficient_run(scenario, trace)


def test_a_wrong_per_target_code_fails_both_matchers() -> None:
    scenario = _v2_b_scenario()

    wrong = (
        _event(
            tool_name="get_relation_schema_expectation",
            arguments={"relation_names": "raw_customers,raw_orders"},
            error_code=TARGETS_REFUSED_CODE,
            target_refusals=(
                TargetRefusal(target="raw_customers", code="RELATION_NOT_FOUND"),
            ),
        ),
        _batch_event(),
    )

    assert not _receipt_proved(scenario, wrong)
    assert not _insufficiency_matches(scenario, _insufficient_run_v2(scenario, wrong))
