"""T13 slice 3: the two public-evidence A/B pairs (design §4.1).

Everything here is offline contract validation: the pairs are checked-in
scenario files whose certification, admission and dry run happen against a
database and are therefore not part of this test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from data_incident_gym.scenario_cards import (
    AB_SCENARIO_PAIRS,
    ab_partner,
    ab_symmetry_findings,
    build_scenario_card,
)
from data_incident_gym.scenarios import (
    P1_T13_PUBLIC_EVIDENCE_IDS,
    ObservableEvidenceContractV2,
    load_scenario_spec,
)

PAIR_ONE = ("schema_type_change_raw_customer_id_a", "schema_type_change_raw_customer_id_b")
PAIR_TWO = ("schema_type_change_raw_order_user_id_a", "schema_type_change_raw_order_user_id_b")
#: (case id, mutated relation.column) per design §4.1.
MUTATIONS = {
    "schema_type_change_raw_customer_id_a": ("raw_customers", "id"),
    "schema_type_change_raw_customer_id_b": ("raw_customers", "id"),
    "schema_type_change_raw_order_user_id_a": ("raw_orders", "user_id"),
    "schema_type_change_raw_order_user_id_b": ("raw_orders", "user_id"),
}
#: The failing node both pairs share, and the two origins its join reads.
FAILING_NODE = "model.jaffle_shop.customers"
DEFINITION_NODES = (
    "model.jaffle_shop.customers",
    "model.jaffle_shop.stg_customers",
    "model.jaffle_shop.stg_orders",
)
#: Exact pairing, so a crossed or missing partner cannot pass unnoticed.
PARTNERS = {
    PAIR_ONE[0]: PAIR_ONE[1],
    PAIR_ONE[1]: PAIR_ONE[0],
    PAIR_TWO[0]: PAIR_TWO[1],
    PAIR_TWO[1]: PAIR_TWO[0],
}


#: Required evidence types after the pre-certification revision (design §4.1
#: v8): the lineage call is load-bearing for the reference request derivation,
#: so both variants require it — the insufficient variant collects it too.
A_REQUIRED = (
    "DBT_RUN_RESULTS",
    "DBT_NODE_ERROR",
    "DBT_LINEAGE",
    "RELATION_SCHEMA",
    "RELATION_SCHEMA_EXPECTATION",
    "DBT_NODE_DEFINITION",
)
B_REQUIRED = (
    "DBT_RUN_RESULTS",
    "DBT_NODE_ERROR",
    "DBT_LINEAGE",
    "RELATION_SCHEMA",
)
#: The reference tool path every card must restate, derived from the required
#: types and the observable whitelists.
CARD_READONLY_PATH = (
    "get_dbt_run_results",
    "get_dbt_node_error",
    "get_relation_schema",
    "get_dbt_lineage",
    "get_relation_schema_expectation",
    "get_dbt_node_definition",
)


def test_the_two_pairs_are_registered_and_paired() -> None:
    assert len(P1_T13_PUBLIC_EVIDENCE_IDS) == 4
    assert PAIR_ONE in AB_SCENARIO_PAIRS
    assert PAIR_TWO in AB_SCENARIO_PAIRS
    for case_id, partner in PARTNERS.items():
        assert ab_partner(case_id) == partner


@pytest.mark.parametrize("case_id", P1_T13_PUBLIC_EVIDENCE_IDS)
def test_each_pair_member_declares_the_frozen_mutation(case_id: str, project_root: Path) -> None:
    scenario = load_scenario_spec(case_id, project_root)
    relation, column = MUTATIONS[case_id]
    mutations = scenario.reset_and_injection_contract.mutations
    assert len(mutations) == 1
    mutation = mutations[0]
    assert mutation.kind == "COLUMN_TYPE_CHANGE"
    assert (mutation.relation, mutation.column) == (relation, column)
    assert (mutation.from_type, mutation.to_type) == ("integer", "text")
    assert scenario.direct_failure == FAILING_NODE
    assert scenario.affected_assets == (FAILING_NODE,)
    assert scenario.fault_family.value == "SCHEMA_TYPE_CHANGE"
    assert scenario.observable_evidence_contract.schema_version == "observable_evidence.v2"


def test_the_confirmable_variants_name_the_decisive_whitelists(project_root: Path) -> None:
    """A: the two upstream relations are observable and expected, and the
    definitions on the failing node's dependency subgraph are readable."""

    for case_id in (PAIR_ONE[0], PAIR_TWO[0]):
        scenario = load_scenario_spec(case_id, project_root)
        contract = scenario.observable_evidence_contract
        assert isinstance(contract, ObservableEvidenceContractV2)
        assert contract.schema_relations == ("raw_customers", "raw_orders")
        assert contract.expectation_relations == ("raw_customers", "raw_orders")
        assert contract.definition_nodes == DEFINITION_NODES
        assert contract.unresolved_gaps == ()
        assert scenario.answerability.value == "CONFIRMABLE"
        assert scenario.expected_status == "CONFIRMED"
        assert scenario.ground_truth_or_acceptable_root_causes == (
            "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
        )
        assert scenario.required_evidence_types == A_REQUIRED


def test_the_insufficient_variants_withhold_exactly_the_two_v2_facts(
    project_root: Path,
) -> None:
    """B: the observation stays readable while both new facts are refused; the
    two gaps carry the refusal codes the tools actually raise."""

    for case_id in (PAIR_ONE[1], PAIR_TWO[1]):
        scenario = load_scenario_spec(case_id, project_root)
        contract = scenario.observable_evidence_contract
        assert isinstance(contract, ObservableEvidenceContractV2)
        assert contract.schema_relations == ("raw_customers", "raw_orders")
        assert contract.expectation_relations == ()
        assert contract.definition_nodes == ()
        gaps = {
            (gap.gap_kind, gap.subject, gap.reason_code, gap.tool_name)
            for gap in contract.unresolved_gaps
        }
        assert gaps == {
            (
                "RELATION_SCHEMA_EXPECTATION",
                "raw_customers",
                "RELATION_NOT_ALLOWED",
                "get_relation_schema_expectation",
            ),
            (
                "DBT_NODE_DEFINITION",
                FAILING_NODE,
                "NODE_NOT_ALLOWED",
                "get_dbt_node_definition",
            ),
        }
        assert scenario.answerability.value == "INSUFFICIENT"
        assert scenario.expected_status == "INSUFFICIENT_EVIDENCE"
        assert len(scenario.ground_truth_or_acceptable_root_causes) == 2
        # The refused facts are not required evidence: nothing collected them.
        # The lineage call is required in B too: the request derivation reads it
        # and the run collects and cites it before the refused probes.
        assert scenario.required_evidence_types == B_REQUIRED
        assert not {"RELATION_SCHEMA_EXPECTATION", "DBT_NODE_DEFINITION"} & set(B_REQUIRED)


@pytest.mark.parametrize("pair", [PAIR_ONE, PAIR_TWO])
def test_each_pair_is_symmetric_and_differs_only_in_the_answer(
    pair: tuple[str, str], project_root: Path
) -> None:
    left = load_scenario_spec(pair[0], project_root)
    right = load_scenario_spec(pair[1], project_root)
    findings = ab_symmetry_findings(left, right)
    unsatisfied = [finding for finding in findings if not finding.satisfied]
    assert not unsatisfied, unsatisfied
    # Mirror property: the two pairs must not differ in which node fails, only
    # in which upstream column carries the deviation.
    assert left.incident_brief == right.incident_brief
    assert left.reset_and_injection_contract == right.reset_and_injection_contract
    assert left.fault_family == right.fault_family


def test_the_pairs_differ_from_each_other_only_by_the_mutated_column(
    project_root: Path,
) -> None:
    """Pair 2 is the directional mirror: same node, same evidence path, the
    deviation on the other join origin."""

    first = load_scenario_spec(PAIR_ONE[0], project_root)
    second = load_scenario_spec(PAIR_TWO[0], project_root)
    assert first.direct_failure == second.direct_failure
    assert first.incident_brief == second.incident_brief
    assert first.observable_evidence_contract == second.observable_evidence_contract
    assert first.affected_assets == second.affected_assets
    assert first.ground_truth_or_acceptable_root_causes == (
        second.ground_truth_or_acceptable_root_causes
    )


def test_the_cards_are_paired_and_uncertified_until_the_dry_run(project_root: Path) -> None:
    for case_id in P1_T13_PUBLIC_EVIDENCE_IDS:
        card = build_scenario_card(case_id, project_root=project_root)
        assert card.ab_pair is not None
        assert card.ab_pair.decisive_difference
        # No certification run has happened yet: solvability stays undecided.
        assert card.solvability.certified is None
        assert "solvability" in card.completeness_issues()


def test_the_four_cards_restate_the_lineage_bearing_reference_path(
    project_root: Path,
) -> None:
    """Final card text, pinned: every variant exercises the lineage step (the
    request derivation) plus the two batch facts, granted in A and refused in
    B. `get_dbt_lineage` must derive naturally from the required types."""

    for case_id in P1_T13_PUBLIC_EVIDENCE_IDS:
        card = build_scenario_card(case_id, project_root=project_root)
        assert card.readonly_path == CARD_READONLY_PATH, case_id
