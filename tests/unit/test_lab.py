from pathlib import Path
from types import SimpleNamespace

import pytest
from psycopg import sql

from data_incident_gym.baseline import (
    BaselineSummary,
    ColumnSummary,
    RelationSummary,
    make_baseline_summary,
)
from data_incident_gym.config import Settings
from data_incident_gym.incidents import CASE_ID, load_ground_truth
from data_incident_gym.lab import (
    IncidentExecutionError,
    IncidentLab,
    InvalidIncidentState,
)


def _relation(*names: str) -> RelationSummary:
    return RelationSummary(
        name="raw_payments",
        row_count=113,
        columns=tuple(
            ColumnSummary(name, "integer" if name != "payment_method" else "text", True, index)
            for index, name in enumerate(names, start=1)
        ),
    )


HEALTHY = _relation("id", "order_id", "payment_method", "amount")
INJECTED = _relation("id", "order_id", "payment_method", "total_amount")
DRIFTED = _relation("id", "order_id", "payment_method", "other_amount")


class FakeBaseline:
    def __init__(self, summary: BaselineSummary) -> None:
        self.summary = summary
        self.calls: list[str] = []

    def start_postgres(self) -> None:
        self.calls.append("start_postgres")

    def build(self) -> BaselineSummary:
        self.calls.append("build")
        return self.summary


def _prepare_ground_truth(tmp_path: Path) -> None:
    truth = load_ground_truth(CASE_ID)
    path = tmp_path / "config/incidents/schema_rename_payment_amount.json"
    path.parent.mkdir(parents=True)
    path.write_text(truth.to_json(), encoding="utf-8")


def _lab(tmp_path: Path) -> tuple[IncidentLab, FakeBaseline]:
    _prepare_ground_truth(tmp_path)
    summary = make_baseline_summary("analytics", (HEALTHY,))
    baseline = FakeBaseline(summary)
    lab = IncidentLab(
        Settings(_env_file=None),
        tmp_path,
        baseline_builder=baseline,
    )
    return lab, baseline


@pytest.mark.parametrize("current", [None, HEALTHY])
def test_reset_builds_healthy_from_missing_or_healthy_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    current: RelationSummary | None,
) -> None:
    lab, baseline = _lab(tmp_path)
    monkeypatch.setattr(lab, "_inspect_relation", lambda _: current)

    result = lab.reset(CASE_ID)

    assert baseline.calls == ["start_postgres", "build"]
    assert result.state == "HEALTHY"
    assert len(result.fingerprint) == 64


def test_reset_reverses_known_fault_then_builds_healthy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lab, baseline = _lab(tmp_path)
    renames: list[tuple[str, str, str]] = []
    monkeypatch.setattr(lab, "_inspect_relation", lambda _: INJECTED)
    monkeypatch.setattr(
        lab,
        "_rename_column",
        lambda relation, source, target: renames.append((relation, source, target)),
    )

    result = lab.reset(CASE_ID)

    assert baseline.calls == ["start_postgres", "build"]
    assert renames == [("raw_payments", "total_amount", "amount")]
    assert result.state == "HEALTHY"
    assert len(result.fingerprint) == 64


def test_reset_rejects_unknown_drift_without_build(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lab, baseline = _lab(tmp_path)
    monkeypatch.setattr(lab, "_inspect_relation", lambda _: DRIFTED)

    with pytest.raises(InvalidIncidentState, match="未知 Schema 状态"):
        lab.reset(CASE_ID)

    assert baseline.calls == ["start_postgres"]


def test_inject_requires_healthy_state_and_verifies_postcondition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lab, baseline = _lab(tmp_path)
    states = iter((HEALTHY, INJECTED))
    renames: list[tuple[str, str, str]] = []
    monkeypatch.setattr(lab, "_inspect_relation", lambda _: next(states))
    monkeypatch.setattr(
        lab,
        "_rename_column",
        lambda relation, source, target: renames.append((relation, source, target)),
    )

    result = lab.inject(CASE_ID)

    assert baseline.calls == ["start_postgres"]
    assert renames == [("raw_payments", "amount", "total_amount")]
    assert result.state == "INJECTED"
    assert len(result.fingerprint) == 64


@pytest.mark.parametrize("state", [INJECTED, DRIFTED, None])
def test_inject_rejects_nonhealthy_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    state: RelationSummary | None,
) -> None:
    lab, _ = _lab(tmp_path)
    monkeypatch.setattr(lab, "_inspect_relation", lambda _: state)

    with pytest.raises(InvalidIncidentState):
        lab.inject(CASE_ID)


def test_rename_uses_fixed_quoted_identifiers(tmp_path: Path) -> None:
    executed: list[object] = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def execute(self, query: object) -> None:
            executed.append(query)

    class Transaction:
        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def cursor(self) -> Cursor:
            return Cursor()

        def transaction(self) -> Transaction:
            return Transaction()

    lab = IncidentLab(
        Settings(_env_file=None),
        tmp_path,
        baseline_builder=SimpleNamespace(),
        db_connect=lambda **_: Connection(),
    )

    lab._rename_column("raw_payments", "amount", "total_amount")

    assert len(executed) == 1
    assert isinstance(executed[0], sql.Composed)
    assert executed[0].as_string(None) == (
        'ALTER TABLE "analytics"."raw_payments" '
        'RENAME COLUMN "amount" TO "total_amount"'
    )


def test_schema_read_error_redacts_password_and_exception_chain(tmp_path: Path) -> None:
    _prepare_ground_truth(tmp_path)

    def connect(**_: object) -> None:
        raise RuntimeError("failed with database-secret")

    lab = IncidentLab(
        Settings(_env_file=None, postgres_password="database-secret"),
        tmp_path,
        baseline_builder=SimpleNamespace(start_postgres=lambda: None),
        db_connect=connect,
    )

    with pytest.raises(IncidentExecutionError) as error:
        lab.inject(CASE_ID)

    assert "database-secret" not in str(error.value)
    assert "***" in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None

def test_baseline_error_redacts_password_and_exception_chain(tmp_path: Path) -> None:
    _prepare_ground_truth(tmp_path)

    def start_postgres() -> None:
        raise RuntimeError("runner failed with database-secret")

    lab = IncidentLab(
        Settings(_env_file=None, postgres_password="database-secret"),
        tmp_path,
        baseline_builder=SimpleNamespace(start_postgres=start_postgres),
    )

    with pytest.raises(IncidentExecutionError) as error:
        lab.inject(CASE_ID)

    assert "database-secret" not in str(error.value)
    assert "***" in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
