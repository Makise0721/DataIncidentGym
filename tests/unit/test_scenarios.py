import json
from pathlib import Path

import pytest

from data_incident_gym.scenarios import (
    Answerability,
    ScenarioError,
    VariantRole,
    load_scenario_spec,
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
