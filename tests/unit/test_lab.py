from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from data_incident_gym.baseline import EXPECTED_RELATION_COUNTS, ColumnSummary, RelationSummary
from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.lab import (
    IncidentExecutionError,
    IncidentLab,
    InvalidIncidentState,
    ScenarioRun,
)
from data_incident_gym.lab_verifier import ScenarioVerificationStatus
from data_incident_gym.scenarios import (
    load_scenario_spec,
)


def _lab(tmp_path: Path) -> IncidentLab:
    return IncidentLab(
        Settings(_env_file=None),
        tmp_path,
        baseline_builder=SimpleNamespace(),
        dbt_runner=SimpleNamespace(),
        verifier=SimpleNamespace(),
        run_id_factory=lambda: "a" * 32,
    )


def test_lab_loads_only_the_committed_scenario_catalog(tmp_path: Path) -> None:
    del tmp_path
    scenario = _lab(PROJECT_ROOT)._load_case("schema_type_change_order_customer_a")

    assert scenario.variant_role.value == "TEST_CONFIRMABLE"
    assert scenario.direct_failure == "model.jaffle_shop.customers"
    assert scenario.affected_assets == ("model.jaffle_shop.customers",)


def test_lab_applies_type_change_and_distractor_mutations_in_declared_order(
    tmp_path: Path,
    monkeypatch,
) -> None:
    lab = _lab(tmp_path)
    scenario = load_scenario_spec("schema_type_change_order_customer_a")
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(lab, "_drop_dependency", lambda relation: calls.append(("drop", relation)))
    monkeypatch.setattr(
        lab,
        "_change_column_type",
        lambda mutation: calls.append(("type", mutation.column)),
    )
    monkeypatch.setattr(
        lab,
        "_add_nullable_column",
        lambda mutation: calls.append(("distractor", mutation.column)),
    )

    lab._apply_mutations(scenario)

    assert calls == [
        ("drop", "raw_orders"),
        ("type", "user_id"),
        ("distractor", "source_batch_note"),
    ]


def test_lab_restores_declared_mutations_in_reverse_order(tmp_path: Path, monkeypatch) -> None:
    lab = _lab(tmp_path)
    scenario = load_scenario_spec("schema_type_change_order_customer_a")
    calls: list[tuple[str, str]] = []
    injected_relations = {
        "raw_orders": RelationSummary(
            "raw_orders",
            99,
            (ColumnSummary("user_id", "text", True, 2),),
        ),
        "raw_payments": RelationSummary(
            "raw_payments",
            113,
            (ColumnSummary("source_batch_note", "text", True, 5),),
        ),
    }
    monkeypatch.setattr(
        lab,
        "_healthy_relation",
        lambda relation: injected_relations[relation],
    )
    monkeypatch.setattr(lab, "_drop_dependency", lambda relation: calls.append(("drop", relation)))
    monkeypatch.setattr(
        lab,
        "_change_column_type",
        lambda mutation, *, restore: calls.append(("type", f"{mutation.column}:{restore}")),
    )
    monkeypatch.setattr(
        lab,
        "_drop_nullable_column",
        lambda mutation: calls.append(("distractor", mutation.column)),
    )

    lab._restore_mutations(scenario)

    assert calls == [
        ("distractor", "source_batch_note"),
        ("drop", "raw_orders"),
        ("type", "user_id:True"),
    ]


def test_scenario_run_is_the_single_public_run_shape() -> None:
    run = ScenarioRun(
        run_id="a" * 32,
        artifact_dir=Path(".dig/lab/runs") / ("a" * 32),
        verification_status=ScenarioVerificationStatus.HEALTHY_CONTROL,
        dbt_exit_code=0,
    )

    assert run.verification_status is ScenarioVerificationStatus.HEALTHY_CONTROL
    assert run.dbt_exit_code == 0


def test_no_mutation_preparation_uses_the_full_baseline_projection(monkeypatch) -> None:
    lab = _lab(PROJECT_ROOT)
    scenario = load_scenario_spec("order_volume_pattern_a")
    inspected: list[tuple[str, ...]] = []
    monkeypatch.setattr(lab, "_load_case", lambda _: scenario)
    monkeypatch.setattr(lab, "_start_postgres", lambda: None)
    monkeypatch.setattr(lab, "_clear_active_run", lambda: None)
    monkeypatch.setattr(
        lab,
        "_inspect_relations",
        lambda names: inspected.append(tuple(names)) or (),
    )
    monkeypatch.setattr(lab, "_fingerprint", lambda *_: "a" * 64)

    result = lab.prepare("order_volume_pattern_a")

    assert result.state == "HEALTHY"
    assert result.fingerprint == "a" * 64
    assert inspected == [tuple(EXPECTED_RELATION_COUNTS)]


def test_reset_clears_stale_active_run_before_recovery_failure(tmp_path: Path, monkeypatch) -> None:
    lab = _lab(tmp_path)
    scenario = load_scenario_spec("schema_type_change_payment_amount")
    active = tmp_path / ".dig" / "lab" / "active_run.json"
    temporary = tmp_path / ".dig" / "lab" / "active_run.json.tmp"
    active.parent.mkdir(parents=True)
    active.write_text("stale", encoding="utf-8")
    temporary.write_text("stale temporary", encoding="utf-8")
    monkeypatch.setattr(lab, "_load_case", lambda _: scenario)
    monkeypatch.setattr(lab, "_start_postgres", lambda: None)
    monkeypatch.setattr(
        lab,
        "_build_healthy_baseline",
        lambda: (_ for _ in ()).throw(IncidentExecutionError("synthetic recovery failure")),
    )

    with pytest.raises(IncidentExecutionError, match="synthetic recovery failure"):
        lab.reset("schema_type_change_payment_amount")

    assert not active.exists()
    assert not temporary.exists()


def test_restore_accepts_schema_already_restored_by_partial_reset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    lab = _lab(tmp_path)
    scenario = load_scenario_spec("schema_rename_payment_amount")
    healthy = RelationSummary(
        "raw_payments",
        113,
        (ColumnSummary("amount", "integer", True, 4),),
    )
    rename_calls: list[bool] = []
    monkeypatch.setattr(lab, "_load_case", lambda _: scenario)
    monkeypatch.setattr(lab, "_healthy_relation", lambda _: healthy)
    monkeypatch.setattr(
        lab,
        "_rename_column",
        lambda _mutation, *, restore: rename_calls.append(restore),
    )
    monkeypatch.setattr(lab, "_clear_active_run", lambda: None)
    monkeypatch.setattr(lab, "_start_postgres", lambda: None)
    monkeypatch.setattr(
        lab,
        "_build_healthy_baseline",
        lambda: SimpleNamespace(fingerprint="a" * 64),
    )

    result = lab.restore("schema_rename_payment_amount")

    assert result.state == "HEALTHY"
    assert rename_calls == []


def test_build_rejects_unprepared_or_unknown_schema_drift(tmp_path: Path, monkeypatch) -> None:
    lab = _lab(tmp_path)
    scenario = load_scenario_spec("schema_type_change_payment_amount")
    healthy = RelationSummary(
        "raw_payments",
        1,
        (ColumnSummary("amount", "integer", True, 1),),
    )
    monkeypatch.setattr(lab, "_healthy_relation", lambda _: healthy)

    with pytest.raises(InvalidIncidentState, match="type mutation"):
        lab._validate_prepared_state(scenario)
