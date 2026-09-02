from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from data_incident_gym.run_context import (
    ACTIVE_RUN_PATH,
    IncidentBrief,
    RunContextError,
    clear_active_run,
    publish_active_run,
    resolve_active_run,
    resolve_run_context,
)

RUN_ID = "a" * 32


def _write_public_run(project_root: Path, *, run_id: str = RUN_ID) -> Path:
    run_root = project_root / ".dig" / "lab" / "runs" / run_id
    run_root.mkdir(parents=True)
    (run_root / "runtime.json").write_text(
        json.dumps(
            {
                "schema_version": "p1.runtime.v1",
                "run_id": run_id,
                "dbt_exit_code": 1,
                "artifacts": {
                    "manifest": "dbt/target/manifest.json",
                    "run_results": "dbt/target/run_results.json",
                    "dbt_log": "dbt/logs/dbt.log",
                    "schema": "schema.json",
                    "profile_snapshot": "profile_snapshot.json",
                    "incident_brief": "incident_brief.json",
                },
                "observable_relations": {
                    "schema": ["raw_payments"],
                    "profile": ["raw_payments"],
                    "history": ["raw_orders"],
                },
                "profile_spec_sha256": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    brief = IncidentBrief(
        schema_version="incident_brief.v1",
        signal_code="DBT_BUILD_FAILED",
        summary="A dbt model build failed.",
        subjects=("model.jaffle_shop.stg_payments",),
        logical_observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        observations=(),
    )
    (run_root / "incident_brief.json").write_text(
        brief.model_dump_json(),
        encoding="utf-8",
    )
    return run_root


def test_public_context_round_trips_without_private_fields(tmp_path: Path) -> None:
    run_root = _write_public_run(tmp_path)

    context = resolve_run_context(RUN_ID, tmp_path)

    assert context.run_id == RUN_ID
    assert context.artifact_dir == run_root
    assert set(context.runtime) == {
        "schema_version",
        "run_id",
        "dbt_exit_code",
        "artifacts",
        "observable_relations",
        "profile_spec_sha256",
    }
    assert "scenario_spec_sha256" not in context.runtime
    assert "expected_status" not in context.runtime


def test_active_pointer_contains_only_run_identity_and_is_validated(tmp_path: Path) -> None:
    _write_public_run(tmp_path)

    active = publish_active_run(tmp_path, run_id=RUN_ID)

    assert active == resolve_active_run(tmp_path)
    payload = json.loads((tmp_path / ACTIVE_RUN_PATH).read_text(encoding="utf-8"))
    assert payload == {"run_id": RUN_ID, "schema_version": "p1.active_run.v1"}


@pytest.mark.parametrize(
    "payload",
    [
        '{"run_id":"' + RUN_ID + '","run_id":"' + RUN_ID + '","schema_version":"p1.active_run.v1"}',
        {"run_id": RUN_ID, "schema_version": "p1.active_run.v1", "private": "answer"},
        {"run_id": 1, "schema_version": "p1.active_run.v1"},
    ],
)
def test_active_pointer_rejects_invalid_public_contract(
    tmp_path: Path,
    payload: object,
) -> None:
    _write_public_run(tmp_path)
    pointer = tmp_path / ACTIVE_RUN_PATH
    pointer.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        pointer.write_text(payload, encoding="utf-8")
    else:
        pointer.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RunContextError):
        resolve_active_run(tmp_path)


def test_clear_active_run_removes_only_fixed_pointer(tmp_path: Path) -> None:
    _write_public_run(tmp_path)
    publish_active_run(tmp_path, run_id=RUN_ID)
    unrelated = tmp_path / ".dig" / "lab" / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")

    clear_active_run(tmp_path)

    assert not (tmp_path / ACTIVE_RUN_PATH).exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"
