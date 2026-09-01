import json
from pathlib import Path

import pytest

from data_incident_gym.scenarios import (
    P1_M7_SCENARIO_IDS,
    P1_M8_SCENARIO_IDS,
    P1_M9_SCENARIO_IDS,
    P1_M10_SCENARIO_IDS,
    P1_M11_SCENARIO_IDS,
    P1_SCENARIO_IDS,
    SUPPORTED_SCENARIO_IDS,
    Answerability,
    DeletePaymentRowsMutation,
    DuplicatePaymentRowsMutation,
    OrphanPaymentRowsMutation,
    ScenarioError,
    VariantRole,
    deleted_payment_rows,
    duplicate_payment_rows,
    load_scenario_spec,
    orphan_payment_rows,
    parse_scenario_spec,
)


def test_m7_catalog_is_exact_and_insufficient_twin_is_real(project_root: Path) -> None:
    from data_incident_gym.scenarios import P1_M7_SCENARIO_IDS, REGRESSION_SCENARIO_IDS

    assert P1_M7_SCENARIO_IDS == (
        "schema_type_change_payment_amount",
        "schema_type_change_order_customer_a",
        "schema_type_change_order_customer_b",
        "order_volume_pattern_a",
    )
    assert REGRESSION_SCENARIO_IDS == ("schema_rename_payment_amount",)

    regression = load_scenario_spec("schema_rename_payment_amount", project_root)
    assert regression.observable_evidence_contract.schema_relations == ("raw_payments",)
    assert regression.observable_evidence_contract.profile_relations == ()
    assert regression.observable_evidence_contract.history_relations == ()

    insufficient = load_scenario_spec("schema_type_change_order_customer_b", project_root)
    assert insufficient.variant_role is VariantRole.TEST_INSUFFICIENT
    assert insufficient.answerability is Answerability.INSUFFICIENT
    assert insufficient.expected_status == "INSUFFICIENT_EVIDENCE"
    assert set(insufficient.ground_truth_or_acceptable_root_causes) == {
        "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
        "TRANSFORMATION_COLUMN_CAST_CHANGED",
    }
    assert len(insufficient.observable_evidence_contract.unresolved_gaps) == 2


def test_m8_catalog_is_exact_and_test_pair_differs_only_by_evidence_boundary(
    project_root: Path,
) -> None:
    from data_incident_gym.scenarios import P1_M7_SCENARIO_IDS, P1_M8_SCENARIO_IDS

    assert P1_M8_SCENARIO_IDS == (
        "required_null_payment_id",
        "required_null_order_customer_a",
        "required_null_order_customer_b",
    )
    assert P1_M7_SCENARIO_IDS + P1_M8_SCENARIO_IDS == (
        "schema_type_change_payment_amount",
        "schema_type_change_order_customer_a",
        "schema_type_change_order_customer_b",
        "order_volume_pattern_a",
        "required_null_payment_id",
        "required_null_order_customer_a",
        "required_null_order_customer_b",
    )
    dev, confirmable, insufficient = (
        load_scenario_spec(case_id, project_root) for case_id in P1_M8_SCENARIO_IDS
    )
    assert dev.variant_role is VariantRole.DEV_CONFIRMABLE
    assert confirmable.variant_role is VariantRole.TEST_CONFIRMABLE
    assert insufficient.variant_role is VariantRole.TEST_INSUFFICIENT
    for field in (
        "incident_brief",
        "reset_and_injection_contract",
        "direct_failure",
        "affected_assets",
        "distractors",
    ):
        assert getattr(confirmable, field) == getattr(insufficient, field)
    assert confirmable.observable_evidence_contract.profile_relations == (
        "raw_orders",
        "raw_customers",
    )
    assert insufficient.observable_evidence_contract.profile_relations == ("raw_customers",)
    assert tuple(
        (gap.gap_kind, gap.subject, gap.reason_code, gap.tool_name)
        for gap in insufficient.observable_evidence_contract.unresolved_gaps
    ) == (
        (
            "RELATION_DATA_PROFILE",
            "raw_orders",
            "RELATION_NOT_ALLOWED",
            "get_relation_data_profile",
        ),
        (
            "TRANSFORMATION_DEFINITION",
            "model.jaffle_shop.stg_orders",
            "NOT_OBSERVABLE",
            None,
        ),
    )


def test_m8_rejects_a_null_mutation_outside_the_frozen_fixture_target(
    project_root: Path,
) -> None:
    source = project_root / "config" / "scenarios" / "required_null_order_customer_a.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["reset_and_injection_contract"]["mutations"][0]["selector_value"] = 43
    with pytest.raises(ScenarioError):
        parse_scenario_spec(json.dumps(payload), "unsupported M8 target")


def test_m8_public_brief_contains_no_private_row_or_answer_contract(
    project_root: Path,
) -> None:
    scenario = load_scenario_spec("required_null_order_customer_b", project_root)
    public = scenario.incident_brief.model_dump_json()
    for forbidden in (
        "selector_column",
        "selector_value",
        "expected_value",
        "SOURCE_REQUIRED_FIELD_NULL",
        "TRANSFORMATION_REQUIRED_FIELD_NULL",
        "TEST_INSUFFICIENT",
        "INSUFFICIENT_EVIDENCE",
    ):
        assert forbidden not in public


def test_m9_catalog_and_test_twin_are_exact(project_root: Path) -> None:
    assert P1_M9_SCENARIO_IDS == (
        "duplicate_payment_record",
        "duplicate_payment_coupon_a",
        "duplicate_payment_coupon_b",
    )
    assert len(P1_M7_SCENARIO_IDS + P1_M8_SCENARIO_IDS + P1_M9_SCENARIO_IDS) == 10

    dev, confirmable, insufficient = (
        load_scenario_spec(case_id, project_root) for case_id in P1_M9_SCENARIO_IDS
    )
    assert dev.variant_role is VariantRole.DEV_CONFIRMABLE
    assert confirmable.variant_role is VariantRole.TEST_CONFIRMABLE
    assert insufficient.variant_role is VariantRole.TEST_INSUFFICIENT
    for field_name in (
        "incident_brief",
        "reset_and_injection_contract",
        "direct_failure",
        "affected_assets",
        "distractors",
    ):
        assert getattr(confirmable, field_name) == getattr(insufficient, field_name)
    assert confirmable.direct_failure is None
    assert insufficient.direct_failure is None
    assert confirmable.observable_evidence_contract.profile_relations == ("raw_payments",)
    assert insufficient.observable_evidence_contract.profile_relations == ()
    assert tuple(
        (gap.gap_kind, gap.subject, gap.reason_code, gap.tool_name)
        for gap in insufficient.observable_evidence_contract.unresolved_gaps
    ) == (
        (
            "RELATION_DATA_PROFILE",
            "raw_payments",
            "RELATION_NOT_ALLOWED",
            "get_relation_data_profile",
        ),
        ("PAYMENT_EVENT_IDENTITY", "raw_payments", "NOT_OBSERVABLE", None),
    )

    exact = next(
        item
        for item in dev.reset_and_injection_contract.mutations
        if isinstance(item, DuplicatePaymentRowsMutation)
    )
    semantic = next(
        item
        for item in confirmable.reset_and_injection_contract.mutations
        if isinstance(item, DuplicatePaymentRowsMutation)
    )
    assert duplicate_payment_rows(exact) == (
        ((1, 1, "credit_card", 1000), (1, 1, "credit_card", 1000)),
    )
    assert duplicate_payment_rows(semantic) == (
        ((47, 42, "coupon", 1700), (114, 42, "coupon", 1700)),
        ((66, 58, "coupon", 1800), (115, 58, "coupon", 1800)),
        ((86, 76, "coupon", 200), (116, 76, "coupon", 200)),
    )


@pytest.mark.parametrize(
    "mutator",
    (
        lambda mutation: mutation.update(source_payment_ids=[2]),
        lambda mutation: mutation.update(inserted_payment_ids=[117]),
        lambda mutation: mutation.update(mode="EXACT_RECORD"),
        lambda mutation: mutation.update(relation="raw_orders"),
        lambda mutation: mutation.update(
            source_payment_ids=[66, 47, 86], inserted_payment_ids=[115, 114, 116]
        ),
        lambda mutation: mutation.update(row=[114, 42, "coupon", 1700]),
    ),
    ids=("source", "inserted", "mode", "relation", "order", "payload"),
)
def test_m9_rejects_any_non_frozen_duplicate_batch(
    project_root: Path,
    mutator,
) -> None:
    source = project_root / "config" / "scenarios" / "duplicate_payment_coupon_a.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    mutator(payload["reset_and_injection_contract"]["mutations"][0])
    with pytest.raises(ScenarioError):
        parse_scenario_spec(json.dumps(payload), "unsupported M9 duplicate batch")


def test_m9_public_brief_contains_no_private_duplicate_contract(project_root: Path) -> None:
    for case_id in P1_M9_SCENARIO_IDS:
        public = load_scenario_spec(case_id, project_root).incident_brief.model_dump_json()
        for forbidden in (
            "source_payment_ids",
            "inserted_payment_ids",
            "SOURCE_EXACT_PAYMENT_DUPLICATE",
            "SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
            "LEGITIMATE_SPLIT_PAYMENT",
            "TEST_INSUFFICIENT",
            "INSUFFICIENT_EVIDENCE",
            "114",
            "115",
            "116",
        ):
            assert forbidden not in public


def test_m10_catalog_and_test_twin_are_exact(project_root: Path) -> None:
    assert P1_M10_SCENARIO_IDS == (
        "orphan_payment_record",
        "orphan_payment_coupon_a",
        "orphan_payment_coupon_b",
    )
    assert len(
        P1_M7_SCENARIO_IDS
        + P1_M8_SCENARIO_IDS
        + P1_M9_SCENARIO_IDS
        + P1_M10_SCENARIO_IDS
    ) == 13

    dev, confirmable, insufficient = (
        load_scenario_spec(case_id, project_root) for case_id in P1_M10_SCENARIO_IDS
    )
    assert dev.variant_role is VariantRole.DEV_CONFIRMABLE
    assert confirmable.variant_role is VariantRole.TEST_CONFIRMABLE
    assert insufficient.variant_role is VariantRole.TEST_INSUFFICIENT
    for field_name in (
        "incident_brief",
        "reset_and_injection_contract",
        "direct_failure",
        "affected_assets",
        "distractors",
    ):
        assert getattr(confirmable, field_name) == getattr(insufficient, field_name)
    assert confirmable.direct_failure is None
    assert insufficient.direct_failure is None
    assert confirmable.observable_evidence_contract.profile_relations == ("raw_payments",)
    assert confirmable.observable_evidence_contract.history_relations == ("raw_orders",)
    assert insufficient.observable_evidence_contract.profile_relations == ("raw_payments",)
    assert insufficient.observable_evidence_contract.history_relations == ()
    assert tuple(
        (gap.gap_kind, gap.subject, gap.reason_code, gap.tool_name)
        for gap in insufficient.observable_evidence_contract.unresolved_gaps
    ) == (
        (
            "RELATION_HISTORY",
            "raw_orders",
            "RELATION_NOT_ALLOWED",
            "get_relation_history",
        ),
        ("INGESTION_WATERMARK", "raw_orders", "NOT_OBSERVABLE", None),
    )

    single = next(
        item
        for item in dev.reset_and_injection_contract.mutations
        if isinstance(item, OrphanPaymentRowsMutation)
    )
    batch = next(
        item
        for item in confirmable.reset_and_injection_contract.mutations
        if isinstance(item, OrphanPaymentRowsMutation)
    )
    assert orphan_payment_rows(single) == ((114, 1000, "credit_card", 1000),)
    assert orphan_payment_rows(batch) == (
        (114, 1000, "coupon", 1700),
        (115, 1001, "coupon", 1800),
        (116, 1002, "coupon", 200),
    )


@pytest.mark.parametrize(
    "mutator",
    (
        lambda mutation: mutation.update(inserted_payment_ids=[117]),
        lambda mutation: mutation.update(missing_order_ids=[1003]),
        lambda mutation: mutation.update(mode="SINGLE_REFERENCE"),
        lambda mutation: mutation.update(relation="raw_orders"),
        lambda mutation: mutation.update(
            inserted_payment_ids=[115, 114, 116], missing_order_ids=[1001, 1000, 1002]
        ),
        lambda mutation: mutation.update(row=[114, 1000, "coupon", 1700]),
    ),
    ids=("payment", "order", "mode", "relation", "order", "payload"),
)
def test_m10_rejects_any_non_frozen_orphan_batch(
    project_root: Path,
    mutator,
) -> None:
    source = project_root / "config" / "scenarios" / "orphan_payment_coupon_a.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    mutator(payload["reset_and_injection_contract"]["mutations"][0])
    with pytest.raises(ScenarioError):
        parse_scenario_spec(json.dumps(payload), "unsupported M10 orphan batch")


def test_m10_public_brief_contains_no_private_orphan_contract(project_root: Path) -> None:
    for case_id in P1_M10_SCENARIO_IDS:
        public = load_scenario_spec(case_id, project_root).incident_brief.model_dump_json()
        for forbidden in (
            "inserted_payment_ids",
            "missing_order_ids",
            "SOURCE_PERMANENT_ORPHAN_PAYMENT",
            "NORMAL_LATE_ARRIVING_ORDER",
            "TEST_INSUFFICIENT",
            "INSUFFICIENT_EVIDENCE",
            "1000",
            "1001",
            "1002",
            case_id,
        ):
            assert forbidden not in public


def test_m11_catalog_and_test_twin_are_exact(project_root: Path) -> None:
    assert P1_M11_SCENARIO_IDS == (
        "silent_payment_drop_record",
        "silent_payment_drop_partition_a",
        "silent_payment_drop_partition_b",
        "order_volume_within_sla",
    )
    assert len(P1_SCENARIO_IDS) == 17
    assert len(P1_SCENARIO_IDS) == len(set(P1_SCENARIO_IDS))
    assert len(SUPPORTED_SCENARIO_IDS) == 18

    dev, confirmable, insufficient, health = (
        load_scenario_spec(case_id, project_root) for case_id in P1_M11_SCENARIO_IDS
    )
    assert dev.variant_role is VariantRole.DEV_CONFIRMABLE
    assert confirmable.variant_role is VariantRole.TEST_CONFIRMABLE
    assert insufficient.variant_role is VariantRole.TEST_INSUFFICIENT
    assert health.variant_role is VariantRole.NO_INCIDENT_CONTROL
    for field_name in (
        "incident_brief",
        "reset_and_injection_contract",
        "direct_failure",
        "affected_assets",
        "distractors",
    ):
        assert getattr(confirmable, field_name) == getattr(insufficient, field_name)
    assert confirmable.observable_evidence_contract.profile_relations == (
        "raw_orders",
        "raw_payments",
    )
    assert confirmable.observable_evidence_contract.history_relations == (
        "raw_orders",
        "raw_payments",
    )
    assert insufficient.observable_evidence_contract.profile_relations == (
        "raw_orders",
        "raw_payments",
    )
    assert insufficient.observable_evidence_contract.history_relations == ()
    assert tuple(
        (gap.gap_kind, gap.subject, gap.reason_code, gap.tool_name)
        for gap in insufficient.observable_evidence_contract.unresolved_gaps
    ) == (
        ("RELATION_HISTORY", "raw_payments", "RELATION_NOT_ALLOWED", "get_relation_history"),
        ("RELATION_HISTORY", "raw_orders", "RELATION_NOT_ALLOWED", "get_relation_history"),
        ("INGESTION_WATERMARK", "raw_orders", "NOT_OBSERVABLE", None),
    )
    mutation = next(
        item
        for item in confirmable.reset_and_injection_contract.mutations
        if isinstance(item, DeletePaymentRowsMutation)
    )
    assert deleted_payment_rows(mutation) == (
        (89, 78, "bank_transfer", 2600),
        (92, 80, "gift_card", 300),
    )
    assert health.observable_evidence_contract.history_relations == ("raw_orders",)
    assert all(
        "RELATION_SCHEMA" not in scenario.required_evidence_types
        for scenario in (dev, confirmable, insufficient)
    )


@pytest.mark.parametrize("case_id", P1_M11_SCENARIO_IDS[:3])
def test_m11_public_brief_contains_no_private_delete_contract(
    project_root: Path,
    case_id: str,
) -> None:
    public = load_scenario_spec(case_id, project_root).incident_brief.model_dump_json()
    for forbidden in (
        "deleted_payment_ids",
        "SOURCE_PAYMENT_INGESTION_LOSS",
        "NORMAL_BUSINESS_PAYMENT_DECLINE",
        "TEST_INSUFFICIENT",
        "INSUFFICIENT_EVIDENCE",
        "89",
        "92",
        case_id,
    ):
        assert forbidden not in public


def test_public_brief_does_not_include_private_scenario_fields(project_root: Path) -> None:
    private = load_scenario_spec("schema_type_change_order_customer_b", project_root)
    public = private.incident_brief.model_dump_json()
    for forbidden in (
        "TEST_INSUFFICIENT",
        "INSUFFICIENT_EVIDENCE",
        "TRANSFORMATION_COLUMN_CAST_CHANGED",
        "ground_truth",
        "answerability",
    ):
        assert forbidden not in public


def test_loader_rejects_duplicate_json_keys_and_unknown_ids(
    project_root: Path,
    tmp_path: Path,
) -> None:
    source = project_root / "config" / "scenarios" / "order_volume_pattern_a.json"
    payload = source.read_text(encoding="utf-8")
    with pytest.raises(ScenarioError):
        parse_scenario_spec(
            payload.replace(
                '"schema_version": "scenario.v1",',
                '"schema_version": "scenario.v1", "schema_version": "scenario.v1",',
                1,
            ),
            "duplicate",
        )

    with pytest.raises(ScenarioError, match="未知场景 ID"):
        load_scenario_spec("../../outside", tmp_path)


def test_scenario_parser_rejects_private_contract_drift(project_root: Path) -> None:
    source = project_root / "config" / "scenarios" / "order_volume_pattern_a.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["forbidden_leakage"] = ["SCENARIO_SPEC"]
    with pytest.raises(ScenarioError):
        parse_scenario_spec(json.dumps(payload), "missing leakage")
