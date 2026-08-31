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
    SetFieldNullMutation,
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


class _FakeCursor:
    def __init__(
        self,
        calls: list[tuple[str, tuple[object, ...]]],
        *,
        rows: list[tuple[object, ...]] | None = None,
        one: tuple[object, ...] | None = None,
        rowcount: int = 1,
    ) -> None:
        self.calls = calls
        self.rows = rows or []
        self.one = one
        self.rowcount = rowcount

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: object, params: tuple[object, ...] = ()) -> None:
        self.calls.append((str(statement), params))

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows

    def fetchone(self) -> tuple[object, ...] | None:
        return self.one


class _FakeTransaction:
    def __init__(self) -> None:
        self.saw_exception = False

    def __enter__(self) -> _FakeTransaction:
        return self

    def __exit__(self, exc_type, *_: object) -> None:
        self.saw_exception = exc_type is not None


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self.cursor_instance = cursor
        self.transaction_instance = _FakeTransaction()

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return self.cursor_instance

    def transaction(self) -> _FakeTransaction:
        return self.transaction_instance


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


def test_null_mutation_helpers_use_bound_values_and_exact_row_count(tmp_path: Path) -> None:
    lab = _lab(tmp_path)
    mutation = load_scenario_spec(
        "required_null_payment_id"
    ).reset_and_injection_contract.mutations[0]
    assert isinstance(mutation, SetFieldNullMutation)
    calls: list[tuple[str, tuple[object, ...]]] = []
    cursor = _FakeCursor(calls, rows=[(1,)], one=(0,))
    lab.db_connect = lambda **_: _FakeConnection(cursor)

    assert lab._read_null_target(mutation) == 1
    assert lab._null_count(mutation) == 0
    lab._write_null_target(
        mutation,
        expected_current=mutation.expected_value,
        replacement=None,
    )

    assert calls[0][1] == (1,)
    assert calls[1][1] == ()
    assert calls[2][1] == (None, 1, 1)
    assert "IS NOT DISTINCT FROM" in calls[2][0]


def test_null_mutation_helpers_reject_non_unique_selector_or_update(tmp_path: Path) -> None:
    lab = _lab(tmp_path)
    mutation = load_scenario_spec(
        "required_null_payment_id"
    ).reset_and_injection_contract.mutations[0]
    assert isinstance(mutation, SetFieldNullMutation)
    calls: list[tuple[str, tuple[object, ...]]] = []
    cursor = _FakeCursor(calls, rows=[(1,), (1,)], one=None, rowcount=0)
    lab.db_connect = lambda **_: _FakeConnection(cursor)

    with pytest.raises(InvalidIncidentState, match="恰好一行"):
        lab._read_null_target(mutation)

    cursor.rows = [(1,)]
    connection = _FakeConnection(cursor)
    lab.db_connect = lambda **_: connection
    with pytest.raises(InvalidIncidentState, match="恰好一行"):
        lab._write_null_target(
            mutation,
            expected_current=mutation.expected_value,
            replacement=None,
        )

    assert connection.transaction_instance.saw_exception is True


def test_null_mutation_prepare_apply_validate_and_restore_states(
    tmp_path: Path,
    monkeypatch,
) -> None:
    lab = _lab(tmp_path)
    scenario = load_scenario_spec("required_null_payment_id")
    mutation = scenario.reset_and_injection_contract.mutations[0]
    assert isinstance(mutation, SetFieldNullMutation)
    current = [mutation.expected_value]
    null_count = [0]
    writes: list[tuple[object, object]] = []
    monkeypatch.setattr(lab, "_read_null_target", lambda _mutation: current[0])
    monkeypatch.setattr(lab, "_null_count", lambda _mutation: null_count[0])

    lab._ensure_healthy_for_prepare(scenario)
    monkeypatch.setattr(
        lab,
        "_write_null_target",
        lambda _mutation, *, expected_current, replacement: (
            writes.append((expected_current, replacement)),
            current.__setitem__(0, replacement),
            null_count.__setitem__(0, 1),
        )[-1],
    )
    lab._apply_mutations(scenario)
    lab._validate_prepared_state(scenario)
    assert writes == [(mutation.expected_value, None)]

    monkeypatch.setattr(
        lab,
        "_write_null_target",
        lambda _mutation, *, expected_current, replacement: (
            writes.append((expected_current, replacement)),
            current.__setitem__(0, replacement),
            null_count.__setitem__(0, 0),
        )[-1],
    )
    lab._restore_mutations(scenario)
    assert current == [mutation.expected_value]
    assert null_count == [0]

    current[0] = "unexpected"
    with pytest.raises(InvalidIncidentState, match="未知"):
        lab._restore_mutations(scenario)
