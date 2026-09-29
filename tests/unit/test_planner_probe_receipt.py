from datetime import UTC, datetime
from pathlib import Path

import pytest

from data_incident_gym.planner_comparison_manifest import build_experiment_manifest
from data_incident_gym.planner_probe_receipt import (
    PLANNER_PROBE_RECEIPT_FILENAME,
    PlannerProbeReceiptError,
    create_planner_probe_receipt,
    load_planner_probe_receipt,
    write_planner_probe_receipt,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_receipt_round_trips_timezone_aware_timestamp_and_bound_scope(tmp_path: Path) -> None:
    manifest = build_experiment_manifest("a" * 40, project_root=PROJECT_ROOT)
    receipt = create_planner_probe_receipt(
        manifest,
        checkout_revision="b" * 40,
        model_provider=manifest.model_configuration.provider,
        model_name=manifest.model_configuration.model,
        passed=True,
        observed="PLAN_LOOP_COMPLETED",
        transport=None,
        detail={"plan_step_receipts": 1},
        checked_at=datetime(2026, 9, 29, 10, 11, 12, tzinfo=UTC),
    )
    path = tmp_path / PLANNER_PROBE_RECEIPT_FILENAME

    write_planner_probe_receipt(path, receipt)
    loaded = load_planner_probe_receipt(
        path,
        manifest,
        checkout_revision="b" * 40,
    )

    assert loaded == receipt
    assert loaded.scope_cell_count == 108
    assert path.with_suffix(".sha256").is_file()


def test_receipt_rejects_file_tampering_and_manifest_mismatch(tmp_path: Path) -> None:
    manifest = build_experiment_manifest("a" * 40, project_root=PROJECT_ROOT)
    receipt = create_planner_probe_receipt(
        manifest,
        checkout_revision="b" * 40,
        model_provider=manifest.model_configuration.provider,
        model_name=manifest.model_configuration.model,
        passed=True,
        observed="PLAN_LOOP_COMPLETED",
        transport=None,
        detail={"plan_step_receipts": 1},
        checked_at=datetime(2026, 9, 29, tzinfo=UTC),
    )
    path = tmp_path / PLANNER_PROBE_RECEIPT_FILENAME
    write_planner_probe_receipt(path, receipt)
    encoded = path.read_bytes()
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")

    with pytest.raises(PlannerProbeReceiptError, match="tampered"):
        load_planner_probe_receipt(path, manifest, checkout_revision="b" * 40)

    path.write_bytes(encoded)
    changed_manifest = manifest.model_copy(update={"implementation_revision": "c" * 40})
    with pytest.raises(PlannerProbeReceiptError, match="identity or scope"):
        load_planner_probe_receipt(path, changed_manifest, checkout_revision="b" * 40)
