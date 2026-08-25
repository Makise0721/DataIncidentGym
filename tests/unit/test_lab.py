import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest
from psycopg import sql

from data_incident_gym.baseline import (
    BaselineError,
    BaselineSummary,
    ColumnSummary,
    RelationSummary,
    make_baseline_summary,
)
from data_incident_gym.config import Settings
from data_incident_gym.incidents import CASE_ID, load_ground_truth
from data_incident_gym.lab import (
    FaultVerificationError,
    IncidentExecutionError,
    IncidentLab,
    InvalidIncidentState,
)
from data_incident_gym.lab_verifier import LabVerificationError


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


@pytest.mark.parametrize(
    "changed",
    [
        {"data_type": "bigint"},
        {"nullable": False},
        {"ordinal_position": 5},
    ],
)
def test_schema_state_rejects_metadata_drift(
    tmp_path: Path,
    changed: dict[str, object],
) -> None:
    lab, _ = _lab(tmp_path)
    columns = list(HEALTHY.columns)
    original = columns[0]
    columns[0] = type(original)(
        name=original.name,
        data_type=changed.get("data_type", original.data_type),
        nullable=changed.get("nullable", original.nullable),
        ordinal_position=changed.get("ordinal_position", original.ordinal_position),
    )

    drifted = RelationSummary("raw_payments", 113, tuple(columns))

    assert lab._classify_state(drifted, load_ground_truth(CASE_ID, tmp_path)) == "DRIFTED"


def test_schema_state_rejects_relation_name_drift(tmp_path: Path) -> None:
    lab, _ = _lab(tmp_path)
    drifted = RelationSummary("other_relation", 113, HEALTHY.columns)

    assert lab._classify_state(drifted, load_ground_truth(CASE_ID, tmp_path)) == "DRIFTED"


def test_schema_state_rejects_row_count_drift(tmp_path: Path) -> None:
    lab, _ = _lab(tmp_path)
    drifted = RelationSummary("raw_payments", 114, HEALTHY.columns)

    assert lab._classify_state(drifted, load_ground_truth(CASE_ID, tmp_path)) == "DRIFTED"


@pytest.mark.parametrize(
    "changed",
    [
        {"data_type": "bigint"},
        {"nullable": False},
        {"ordinal_position": 5},
    ],
)
def test_injected_schema_state_rejects_metadata_drift(
    tmp_path: Path,
    changed: dict[str, object],
) -> None:
    lab, _ = _lab(tmp_path)
    columns = list(INJECTED.columns)
    original = columns[0]
    columns[0] = type(original)(
        name=original.name,
        data_type=changed.get("data_type", original.data_type),
        nullable=changed.get("nullable", original.nullable),
        ordinal_position=changed.get("ordinal_position", original.ordinal_position),
    )

    drifted = RelationSummary("raw_payments", 113, tuple(columns))

    assert lab._classify_state(drifted, load_ground_truth(CASE_ID, tmp_path)) == "DRIFTED"


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
    assert "database-secret" not in "".join(traceback.format_exception(error.value))


def test_rename_error_redacts_password_and_exception_chain(tmp_path: Path) -> None:
    secret = "database-secret"

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def execute(self, _: object) -> None:
            raise RuntimeError(f"rename failed with {secret}")

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

        def transaction(self) -> Transaction:
            return Transaction()

        def cursor(self) -> Cursor:
            return Cursor()

    lab = IncidentLab(
        Settings(_env_file=None, postgres_password=secret),
        tmp_path,
        baseline_builder=SimpleNamespace(),
        db_connect=lambda **_: Connection(),
    )

    with pytest.raises(IncidentExecutionError) as error:
        lab._rename_column("raw_payments", "amount", "total_amount")

    assert "故障字段改名失败" in str(error.value)
    assert secret not in str(error.value)
    assert "***" in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert secret not in "".join(traceback.format_exception(error.value))


def test_baseline_error_redacts_password_and_exception_chain(tmp_path: Path) -> None:
    _prepare_ground_truth(tmp_path)
    secret = "database-secret"

    def start_postgres() -> None:
        raise BaselineError(f"runner failed with {secret}")

    lab = IncidentLab(
        Settings(_env_file=None, postgres_password=secret),
        tmp_path,
        baseline_builder=SimpleNamespace(start_postgres=start_postgres),
    )

    with pytest.raises(IncidentExecutionError) as error:
        lab.inject(CASE_ID)

    assert "database-secret" not in str(error.value)
    assert "***" in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert secret not in "".join(traceback.format_exception(error.value))


def test_reset_postcondition_error_has_no_database_secret_or_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_ground_truth(tmp_path)
    secret = "database-secret"
    baseline = FakeBaseline(make_baseline_summary("analytics", (DRIFTED,)))
    lab = IncidentLab(
        Settings(_env_file=None, postgres_password=secret),
        tmp_path,
        baseline_builder=baseline,
    )
    monkeypatch.setattr(lab, "_inspect_relation", lambda _: None)

    with pytest.raises(InvalidIncidentState) as error:
        lab.reset(CASE_ID)

    assert "重置后未恢复健康 Schema" in str(error.value)
    assert secret not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert secret not in "".join(traceback.format_exception(error.value))


def test_build_uses_unique_run_paths_and_returns_expected_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lab, _ = _lab(tmp_path)
    lab.settings = Settings(_env_file=None, postgres_password="TEST_REDACTED_VALUE")
    monkeypatch.setattr(lab, "_inspect_relation", lambda _: INJECTED)

    class FakeDbtRunner:
        def run_incident(self, target: Path, logs: Path):
            target.mkdir(parents=True)
            logs.mkdir(parents=True)
            (target / "manifest.json").write_text(
                '{"message": "TEST_REDACTED_VALUE"}',
                encoding="utf-8",
            )
            (target / "run_results.json").write_text(
                '{"message": "TEST_REDACTED_VALUE"}',
                encoding="utf-8",
            )
            (logs / "dbt.log").write_text(
                "failure TEST_REDACTED_VALUE",
                encoding="utf-8",
            )
            return SimpleNamespace(
                return_code=1,
                stdout="out TEST_REDACTED_VALUE",
                stderr="err TEST_REDACTED_VALUE",
            )

    verification = SimpleNamespace(status="EXPECTED_FAILURE")

    class FakeVerifier:
        def __init__(self) -> None:
            self.run_ids: list[str] = []

        def verify(self, run_id: str):
            self.run_ids.append(run_id)
            return verification

    fake_verifier = FakeVerifier()
    lab.dbt_runner = FakeDbtRunner()
    lab.verifier = fake_verifier
    lab.run_id_factory = lambda: "0123456789abcdef0123456789abcdef"

    result = lab.build(CASE_ID)

    assert result.dbt_exit_code == 1
    assert result.verification.status == "EXPECTED_FAILURE"
    assert fake_verifier.run_ids == [result.run_id]
    assert result.artifact_dir == (
        tmp_path / ".dig/lab/runs/0123456789abcdef0123456789abcdef"
    )
    assert (result.artifact_dir / "metadata.json").is_file()
    assert "TEST_REDACTED_VALUE" not in (
        result.artifact_dir / "dbt/stdout.log"
    ).read_text(encoding="utf-8")
    assert "TEST_REDACTED_VALUE" not in (
        result.artifact_dir / "dbt/stderr.log"
    ).read_text(encoding="utf-8")
    assert "TEST_REDACTED_VALUE" not in (
        result.artifact_dir / "dbt/logs/dbt.log"
    ).read_text(encoding="utf-8")
    assert "TEST_REDACTED_VALUE" not in (
        result.artifact_dir / "dbt/target/manifest.json"
    ).read_text(encoding="utf-8")
    assert "TEST_REDACTED_VALUE" not in (
        result.artifact_dir / "dbt/target/run_results.json"
    ).read_text(encoding="utf-8")
    assert not (tmp_path / ".dig/dbt/target/run_results.json").exists()

    with pytest.raises(IncidentExecutionError):
        lab.build(CASE_ID)
    assert (result.artifact_dir / "metadata.json").is_file()


def test_build_rejects_noninjected_schema(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lab, _ = _lab(tmp_path)
    monkeypatch.setattr(lab, "_inspect_relation", lambda _: HEALTHY)

    with pytest.raises(InvalidIncidentState, match="要求已注入状态"):
        lab.build(CASE_ID)


def test_build_preserves_scene_when_verification_fails_without_secret_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lab, _ = _lab(tmp_path)
    lab.settings = Settings(_env_file=None, postgres_password="TEST_REDACTED_VALUE")
    monkeypatch.setattr(lab, "_inspect_relation", lambda _: INJECTED)

    class FakeDbtRunner:
        def run_incident(self, target: Path, logs: Path):
            target.mkdir(parents=True)
            logs.mkdir(parents=True)
            (target / "manifest.json").write_text("{}", encoding="utf-8")
            (target / "run_results.json").write_text("{}", encoding="utf-8")
            (logs / "dbt.log").write_text(
                "failure TEST_REDACTED_VALUE",
                encoding="utf-8",
            )
            return SimpleNamespace(
                return_code=1,
                stdout="stdout TEST_REDACTED_VALUE",
                stderr="stderr TEST_REDACTED_VALUE",
            )

    class FailingVerifier:
        def verify(self, _: str):
            raise LabVerificationError("invalid TEST_REDACTED_VALUE")

    lab.dbt_runner = FakeDbtRunner()
    lab.verifier = FailingVerifier()
    lab.run_id_factory = lambda: "0123456789abcdef0123456789abcdef"

    with pytest.raises(FaultVerificationError) as error:
        lab.build(CASE_ID)

    assert "TEST_REDACTED_VALUE" not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    run_root = tmp_path / ".dig/lab/runs/0123456789abcdef0123456789abcdef"
    assert (run_root / "ground_truth.json").is_file()
    assert (run_root / "metadata.json").is_file()
    assert "TEST_REDACTED_VALUE" not in (
        run_root / "dbt/stdout.log"
    ).read_text(encoding="utf-8")
