"""Scenario card and A/B symmetry tests (T06)."""

from __future__ import annotations

import ast

from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.fixed_rule import EVIDENCE_V2_TOOL_NAMES, FIXED_RULE_TOOL_NAMES
from data_incident_gym.scenario_cards import (
    AB_SCENARIO_PAIRS,
    CARD_SCHEMA_VERSION,
    HEALTH_CONTROL_SCENARIO_IDS,
    SCENARIO_CARD_BUDGET,
    ab_partner,
    ab_symmetry_findings,
    build_scenario_card,
)
from data_incident_gym.scenario_certification import (
    CertificationFinding,
    ReferenceRunSummary,
    ScenarioCertification,
)
from data_incident_gym.scenarios import (
    SUPPORTED_SCENARIO_IDS,
    SetFieldNullMutation,
    load_scenario_spec,
)

AB_PARTNER_IDS = tuple(case_id for pair in AB_SCENARIO_PAIRS for case_id in pair)
UNPAIRED_IDS = tuple(case_id for case_id in SUPPORTED_SCENARIO_IDS if case_id not in AB_PARTNER_IDS)


def _run_summary() -> ReferenceRunSummary:
    return ReferenceRunSummary(
        run_id="a" * 32,
        evaluation_status="PASSED",
        diagnosis_status="CONFIRMED",
        root_cause_code="SOURCE_REQUIRED_FIELD_NULL",
        affected_assets=("model.jaffle_shop.orders",),
        tool_calls=6,
        successful_tool_calls=6,
        tool_error_codes=(),
        failed_check_codes=(),
    )


def _certified(case_id: str) -> ScenarioCertification:
    return ScenarioCertification(
        case_id=case_id,
        scenario_digest=load_scenario_spec(case_id).digest(),
        certified=True,
        findings=(CertificationFinding(code="EVALUATION_PASSED", satisfied=True),),
        run=_run_summary(),
    )


def test_budget_is_the_frozen_reference_budget() -> None:
    assert SCENARIO_CARD_BUDGET == "8 model requests / 8 tool calls / 2 retries / 300s"
    assert CARD_SCHEMA_VERSION == "p1.scenario_card.v1"


def test_card_fields_are_present_for_every_catalog_scenario() -> None:
    for case_id in SUPPORTED_SCENARIO_IDS:
        card = build_scenario_card(case_id, certification=_certified(case_id))
        scenario = load_scenario_spec(case_id)

        assert card.case_id == case_id
        assert card.fault_mechanism.startswith(scenario.fault_family.value)
        assert scenario.fault_family.value in card.fault_mechanism
        assert card.budget == SCENARIO_CARD_BUDGET
        assert card.readonly_path
        surface_tools = (
            EVIDENCE_V2_TOOL_NAMES
            if scenario.observable_evidence_contract.schema_version == "observable_evidence.v2"
            else FIXED_RULE_TOOL_NAMES
        )
        assert set(card.readonly_path) <= set(surface_tools)
        assert card.ground_truth_forbidden_zone.leakage_codes == tuple(
            item.value for item in scenario.forbidden_leakage
        )
        assert card.ground_truth_forbidden_zone.private_paths == (
            f"config/scenarios/{case_id}.json",
        )
        assert (PROJECT_ROOT / card.source).is_file()
        assert card.source == f"config/scenarios/{case_id}.json"
        assert card.version.card == "p1.scenario_card.v1"
        assert card.version.scenario == "scenario.v1"
        assert card.completeness_issues() == ()


def test_uncertified_cards_only_miss_solvability() -> None:
    for case_id in SUPPORTED_SCENARIO_IDS:
        card = build_scenario_card(case_id)
        assert card.completeness_issues() == ("solvability",)
        assert card.solvability.certified is None


def test_readonly_path_restates_the_reference_tool_path() -> None:
    confirmable = build_scenario_card("required_null_order_customer_a")
    assert confirmable.readonly_path == (
        "get_dbt_run_results",
        "get_dbt_node_error",
        "get_relation_schema",
        "get_dbt_lineage",
        "get_relation_data_profile",
    )
    control = build_scenario_card("order_volume_within_sla")
    assert control.readonly_path == (
        "get_dbt_run_results",
        "get_relation_data_profile",
        "get_relation_history",
    )


def test_ab_pair_covers_exactly_the_paired_cards() -> None:
    # Five historical pairs, the two T12 dev-extension pairs and the two T13
    # public-evidence pairs.
    assert len(AB_SCENARIO_PAIRS) == 9
    assert len(AB_PARTNER_IDS) == 18
    for left, right in AB_SCENARIO_PAIRS:
        assert ab_partner(left) == right
        assert ab_partner(right) == left
        card = build_scenario_card(left)
        assert card.ab_pair is not None
        assert card.ab_pair.partner_case_id == right
        assert card.ab_pair.decisive_difference
    for case_id in UNPAIRED_IDS:
        assert ab_partner(case_id) is None
        assert build_scenario_card(case_id).ab_pair is None


def test_decisive_difference_names_the_lost_decisive_evidence() -> None:
    card = build_scenario_card("duplicate_payment_coupon_b")
    assert card.ab_pair is not None
    assert card.ab_pair.partner_case_id == "duplicate_payment_coupon_a"
    assert "loses read access to profile raw_payments" in card.ab_pair.decisive_difference
    assert "RELATION_DATA_PROFILE(raw_payments)/RELATION_NOT_ALLOWED" in (
        card.ab_pair.decisive_difference
    )


def test_t13_cards_name_the_batch_tools_and_the_withheld_whitelists() -> None:
    """The T13 pairs: both variants exercise the two batch facts (granted in A,
    refused in B), and the decisive difference names the two withheld v2
    whitelists explicitly."""

    confirmable = build_scenario_card("schema_type_change_raw_customer_id_a")
    assert confirmable.readonly_path == (
        "get_dbt_run_results",
        "get_dbt_node_error",
        "get_relation_schema",
        "get_dbt_lineage",
        "get_relation_schema_expectation",
        "get_dbt_node_definition",
    )
    insufficient = build_scenario_card("schema_type_change_raw_customer_id_b")
    assert insufficient.readonly_path == confirmable.readonly_path
    assert insufficient.ab_pair is not None
    difference = insufficient.ab_pair.decisive_difference
    assert "expectation raw_customers,raw_orders" in difference
    assert (
        "definition_nodes model.jaffle_shop.customers,"
        "model.jaffle_shop.stg_customers,model.jaffle_shop.stg_orders" in difference
    )
    assert "RELATION_SCHEMA_EXPECTATION(raw_customers)/RELATION_NOT_ALLOWED" in difference
    assert (
        "DBT_NODE_DEFINITION(model.jaffle_shop.customers)/NODE_NOT_ALLOWED" in difference
    )
    # The mirrored pair states the same surface difference.
    mirrored = build_scenario_card("schema_type_change_raw_order_user_id_b")
    assert mirrored.ab_pair is not None
    assert mirrored.ab_pair.decisive_difference == difference


def test_healthy_control_marks_only_the_health_scenarios() -> None:
    for case_id in HEALTH_CONTROL_SCENARIO_IDS:
        card = build_scenario_card(case_id)
        assert card.healthy_control.is_control is True
        assert card.healthy_control.control_case_id is None
    for case_id in SUPPORTED_SCENARIO_IDS:
        if case_id in HEALTH_CONTROL_SCENARIO_IDS:
            continue
        card = build_scenario_card(case_id)
        assert card.healthy_control.is_control is False
        assert card.healthy_control.control_case_id is None


def test_legitimate_alternatives_follow_answerability() -> None:
    confirmable = build_scenario_card("required_null_order_customer_a")
    assert confirmable.legitimate_alternatives == ()
    insufficient = build_scenario_card("required_null_order_customer_b")
    assert insufficient.legitimate_alternatives == (
        "SOURCE_REQUIRED_FIELD_NULL",
        "TRANSFORMATION_REQUIRED_FIELD_NULL",
    )
    control = build_scenario_card("order_volume_pattern_a")
    assert control.legitimate_alternatives == ()


def test_ab_symmetry_passes_for_every_catalog_pair_in_both_directions() -> None:
    for left, right in AB_SCENARIO_PAIRS:
        for first, second in ((left, right), (right, left)):
            findings = ab_symmetry_findings(
                load_scenario_spec(first), load_scenario_spec(second)
            )
            codes = {item.code for item in findings}
            assert codes == {
                "AB_PAIR_FAULT_FAMILY_MISMATCH",
                "AB_PAIR_CASE_ID_COLLISION",
                "AB_PAIR_SURFACE_MISMATCH",
                "AB_PAIR_ROLE_STRUCTURE",
            }
            assert all(item.satisfied for item in findings), (first, codes)


def test_ab_symmetry_flags_brief_surface_changes() -> None:
    left = load_scenario_spec("required_null_order_customer_a")
    right = load_scenario_spec("required_null_order_customer_b")
    tampered_brief = left.incident_brief.model_copy(update={"summary": "A different alert."})
    tampered = left.model_copy(update={"incident_brief": tampered_brief})

    findings = ab_symmetry_findings(tampered, right)
    surface = next(item for item in findings if item.code == "AB_PAIR_SURFACE_MISMATCH")
    assert surface.satisfied is False
    assert surface.detail is not None and "incident_brief" in surface.detail


def test_ab_symmetry_flags_mutation_selector_changes() -> None:
    left = load_scenario_spec("required_null_order_customer_a")
    right = load_scenario_spec("required_null_order_customer_b")
    contract = left.reset_and_injection_contract
    fault = next(
        mutation
        for mutation in contract.mutations
        if isinstance(mutation, SetFieldNullMutation) and mutation.purpose == "FAULT"
    )
    tampered_mutation = fault.model_copy(update={"selector_value": 43})
    tampered_contract = contract.model_copy(
        update={
            "mutations": tuple(
                tampered_mutation if mutation is fault else mutation
                for mutation in contract.mutations
            )
        }
    )
    tampered = left.model_copy(update={"reset_and_injection_contract": tampered_contract})

    findings = ab_symmetry_findings(tampered, right)
    surface = next(item for item in findings if item.code == "AB_PAIR_SURFACE_MISMATCH")
    assert surface.satisfied is False
    assert surface.detail is not None and "reset_and_injection_contract" in surface.detail


def test_ab_symmetry_allows_answer_side_differences_only() -> None:
    left = load_scenario_spec("required_null_order_customer_a")
    right = load_scenario_spec("required_null_order_customer_b")
    flipped = right.model_copy(update={"expected_status": "CONFIRMED"})

    findings = ab_symmetry_findings(left, flipped)
    assert all(item.satisfied for item in findings)


def test_diagnosis_plane_never_imports_scenario_management_modules() -> None:
    """Mirrors test_p1_isolation: cards/admissions are management-plane only."""

    management_modules = {
        "data_incident_gym.scenario_cards",
        "data_incident_gym.scenario_sets",
        "data_incident_gym.scenario_admission",
        "data_incident_gym.scenario_certification",
    }
    package = PROJECT_ROOT / "src" / "data_incident_gym"
    sources = [package / name for name in (
        "diagnostic_agent.py",
        "diagnostic_kernel.py",
        "diagnosis.py",
        "evidence_tools.py",
        "fixed_rule.py",
    )]

    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        assert imported.isdisjoint(management_modules), path.name
