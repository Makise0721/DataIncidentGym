import json
from pathlib import Path

import pytest

from data_incident_gym.profiles import (
    MAX_GROUP_ROWS,
    MAX_HISTORY_POINTS,
    ProfileError,
    load_profile_spec,
    parse_profile_spec,
)


def test_checked_in_profile_spec_is_scenario_independent(project_root: Path) -> None:
    spec = load_profile_spec(project_root)

    assert spec.schema_version == "profile_spec.v1"
    assert tuple(item.relation_name for item in spec.relations) == (
        "raw_customers",
        "raw_orders",
        "raw_payments",
    )
    assert spec.max_group_rows == MAX_GROUP_ROWS == 128
    assert spec.max_history_points == MAX_HISTORY_POINTS == 90
    canonical = spec.canonical_json()
    assert "incident_case_id" not in canonical
    assert "root_cause_code" not in canonical
    assert "expected_status" not in canonical

    orders = spec.relation("raw_orders")
    assert orders.histories[0].name == "order_count_by_day"
    assert orders.histories[0].periodicity.value == "DAY_OF_WEEK"
    assert orders.histories[0].sla_seconds == 86400
    assert (
        orders.relationships[1].name,
        orders.relationships[1].local_columns,
        orders.relationships[1].referenced_relation,
        orders.relationships[1].referenced_columns,
    ) == (
        "id_to_raw_payments_order_id",
        ("id",),
        "raw_payments",
        ("order_id",),
    )
    payments = spec.relation("raw_payments")
    assert payments.business_keys[0].columns == ("id",)
    assert payments.business_fingerprints[0].columns == (
        "order_id",
        "payment_method",
        "amount",
    )
    assert payments.histories[0].join_path is not None


@pytest.mark.parametrize(
    "change",
    [
        lambda payload: payload["relations"][0].update({"relation_name": "raw.customers"}),
        lambda payload: payload["relations"][0]["columns"].append({"name": "id"}),
        lambda payload: payload["relations"][1]["histories"][0].update({"max_points": 91}),
        lambda payload: payload["relations"][1]["relationships"][0].update(
            {"referenced_columns": ["missing"]}
        ),
    ],
)
def test_profile_spec_rejects_unsafe_or_unbounded_contracts(
    project_root: Path,
    change,
) -> None:
    source = project_root / "config" / "profiles" / "jaffle_shop.v1.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    change(payload)

    with pytest.raises(ProfileError):
        parse_profile_spec(json.dumps(payload), "changed profile")


def test_profile_spec_rejects_duplicate_json_keys(project_root: Path) -> None:
    source = project_root / "config" / "profiles" / "jaffle_shop.v1.json"
    payload = source.read_text(encoding="utf-8")
    payload = payload.replace(
        '"schema_version": "profile_spec.v1",',
        '"schema_version": "profile_spec.v1", "schema_version": "profile_spec.v1",',
        1,
    )

    with pytest.raises(ProfileError, match="重复键"):
        parse_profile_spec(payload)
