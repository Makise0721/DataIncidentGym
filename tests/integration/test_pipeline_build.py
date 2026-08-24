import json
from pathlib import Path

import pytest

from data_incident_gym.baseline import EXPECTED_RELATION_COUNTS, BaselineBuilder
from data_incident_gym.config import Settings


@pytest.mark.integration
def test_pipeline_build_creates_healthy_postgres_dbt_state(project_root: Path) -> None:
    summary = BaselineBuilder(Settings(_env_file=None), project_root).build()

    assert {item.name: item.row_count for item in summary.relations} == (
        EXPECTED_RELATION_COUNTS
    )
    assert len(summary.relations) == 8
    assert len(summary.fingerprint) == 64

    raw_payments = next(
        relation for relation in summary.relations if relation.name == "raw_payments"
    )
    assert {column.name for column in raw_payments.columns} >= {
        "id",
        "order_id",
        "payment_method",
        "amount",
    }

    target = project_root / ".dig" / "dbt" / "target"
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    results = json.loads((target / "run_results.json").read_text(encoding="utf-8"))

    expected_models = {
        "model.jaffle_shop.stg_payments",
        "model.jaffle_shop.orders",
        "model.jaffle_shop.customers",
    }
    assert expected_models <= set(manifest["nodes"])
    assert results["results"]
    assert expected_models <= {result["unique_id"] for result in results["results"]}
    assert {result["status"] for result in results["results"]} <= {"success", "pass"}
