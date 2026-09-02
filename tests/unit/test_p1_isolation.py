from __future__ import annotations

import ast
from pathlib import Path

from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.scenarios import SUPPORTED_SCENARIO_IDS, load_scenario_spec

DIAGNOSIS_MODULES = (
    "diagnostic_agent.py",
    "diagnostic_kernel.py",
    "diagnosis.py",
    "evidence_tools.py",
    "fixed_rule.py",
)
FORBIDDEN_TEXT = (
    "DEV_CONFIRMABLE",
    "TEST_CONFIRMABLE",
    "TEST_INSUFFICIENT",
    "NO_INCIDENT_CONTROL",
    "answerability",
    "expected_status",
    "config/scenarios",
    ".dig/lab/private",
)


def _diagnosis_source_paths() -> tuple[Path, ...]:
    package = PROJECT_ROOT / "src" / "data_incident_gym"
    prompts = tuple(sorted((package / "prompts").glob("*.md")))
    return tuple(package / name for name in DIAGNOSIS_MODULES) + prompts


def test_diagnosis_plane_cannot_import_or_read_private_scenarios() -> None:
    for path in _diagnosis_source_paths():
        text = path.read_text(encoding="utf-8")
        assert all(value not in text for value in FORBIDDEN_TEXT), path.name
        if path.suffix != ".py":
            continue
        tree = ast.parse(text, filename=str(path))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        assert "data_incident_gym.scenarios" not in imported, path.name


def test_private_case_ids_never_reach_diagnosis_sources() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in _diagnosis_source_paths())

    for case_id in (
        "schema_type_change_payment_amount",
        "schema_type_change_order_customer_a",
        "schema_type_change_order_customer_b",
        "order_volume_pattern_a",
        "schema_rename_payment_amount",
        "duplicate_payment_record",
        "duplicate_payment_coupon_a",
        "duplicate_payment_coupon_b",
        "orphan_payment_record",
        "orphan_payment_coupon_a",
        "orphan_payment_coupon_b",
        "silent_payment_drop_record",
        "silent_payment_drop_partition_a",
        "silent_payment_drop_partition_b",
        "order_volume_within_sla",
    ):
        assert case_id not in source
        assert case_id == load_scenario_spec(case_id).incident_case_id

    assert tuple(load_scenario_spec(case_id).incident_case_id for case_id in SUPPORTED_SCENARIO_IDS)


def test_duplicate_payment_private_rows_never_reach_diagnosis_sources() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in _diagnosis_source_paths())
    for private_value in (
        "47",
        "66",
        "86",
        "114",
        "115",
        "116",
        "coupon_count",
        "89",
        "92",
        "111",
        "silent_payment_drop_",
    ):
        assert private_value not in source
