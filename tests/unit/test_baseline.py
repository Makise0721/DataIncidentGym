import json
import subprocess
import traceback
from dataclasses import FrozenInstanceError
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from psycopg import sql

import data_incident_gym.baseline as baseline_module
from data_incident_gym.baseline import (
    EXPECTED_RELATION_COUNTS,
    BaselineBuilder,
    BaselineError,
    BaselineSummary,
    ColumnSummary,
    RelationSummary,
    make_baseline_summary,
)
from data_incident_gym.config import Settings


def _relation(
    name: str,
    row_count: int,
    *columns: tuple[str, str, bool, int],
) -> RelationSummary:
    return RelationSummary(
        name=name,
        row_count=row_count,
        columns=tuple(ColumnSummary(*column) for column in columns),
    )


def test_baseline_summary_is_canonical_and_immutable() -> None:
    customers = _relation(
        "customers",
        100,
        ("id", "integer", False, 1),
        ("name", "text", True, 2),
    )
    raw_customers = _relation(
        "raw_customers",
        100,
        ("name", "text", True, 2),
        ("id", "integer", False, 1),
    )

    first = make_baseline_summary("analytics", (raw_customers, customers))
    second = make_baseline_summary("analytics", (customers, raw_customers))

    assert isinstance(first, BaselineSummary)
    assert [relation.name for relation in first.relations] == ["customers", "raw_customers"]
    assert [column.name for column in first.relations[1].columns] == ["id", "name"]
    assert first.fingerprint == second.fingerprint
    with pytest.raises(FrozenInstanceError):
        first.relations = ()  # type: ignore[misc]


@pytest.mark.parametrize(
    ("change", "expected_difference"),
    [
        ("column", True),
        ("data_type", True),
        ("nullable", True),
        ("row_count", True),
    ],
)
def test_baseline_summary_fingerprint_changes_when_state_changes(
    change: str,
    expected_difference: bool,
) -> None:
    base = _relation("customers", 100, ("id", "integer", False, 1))
    changed_column = _relation("customers", 100, ("customer_id", "integer", False, 1))
    changed_type = _relation("customers", 100, ("id", "bigint", False, 1))
    changed_nullable = _relation("customers", 100, ("id", "integer", True, 1))
    changed_count = _relation("customers", 101, ("id", "integer", False, 1))
    changed = {
        "column": changed_column,
        "data_type": changed_type,
        "nullable": changed_nullable,
        "row_count": changed_count,
    }[change]

    assert (
        make_baseline_summary("analytics", (base,)).fingerprint
        != make_baseline_summary("analytics", (changed,)).fingerprint
    ) is expected_difference


def test_baseline_summary_json_has_only_stable_state() -> None:
    summary = make_baseline_summary(
        "analytics",
        (_relation("customers", 100, ("id", "integer", False, 1)),),
    )

    parsed = json.loads(summary.to_json())
    assert parsed == summary.to_dict()
    assert set(parsed) == {"schema", "relations", "fingerprint"}
    rendered = summary.to_json()
    assert rendered.endswith("\n")
    assert not any(
        forbidden in rendered
        for forbidden in ("timestamp", "container", "oid", str(Path.cwd()))
    )


class _FakeCursor:
    def __init__(
        self,
        columns_by_relation: dict[str, list[tuple[object, ...]]],
        counts: dict[str, int],
    ) -> None:
        self.columns_by_relation = columns_by_relation
        self.counts = counts
        self.current_relation = ""
        self.executions: list[tuple[object, object]] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: object, params: object = None) -> None:
        self.executions.append((query, params))
        if params is not None:
            self.current_relation = params[1]  # type: ignore[index]

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.columns_by_relation.get(self.current_relation, [])

    def fetchone(self) -> tuple[int] | None:
        if self.current_relation not in self.counts:
            return None
        return (self.counts[self.current_relation],)


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self.fake_cursor = cursor

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return self.fake_cursor


def _fake_database(
    *,
    missing: str | None = None,
    count_delta: int = 0,
) -> tuple[_FakeConnection, dict[str, object]]:
    columns = {
        name: [] if name == missing else [("id", "integer", "NO", 1)]
        for name in EXPECTED_RELATION_COUNTS
    }
    counts = {
        name: expected + (count_delta if name == "customers" else 0)
        for name, expected in EXPECTED_RELATION_COUNTS.items()
    }
    cursor = _FakeCursor(columns, counts)
    connection = _FakeConnection(cursor)
    connect_kwargs: dict[str, object] = {}

    def connect(**kwargs: object) -> _FakeConnection:
        connect_kwargs.update(kwargs)
        return connection

    return connection, {"connect": connect, "kwargs": connect_kwargs}


def test_inspect_database_uses_bound_catalog_query_and_identifier_counts(
    tmp_path: Path,
) -> None:
    connection, fake = _fake_database()
    settings = Settings(
        _env_file=None,
        postgres_host="db.example",
        postgres_port=55432,
        postgres_database="dig",
        postgres_schema="analytics",
        postgres_user="dig_admin",
        postgres_password="database-secret",
    )
    builder = BaselineBuilder(settings, tmp_path, db_connect=fake["connect"])  # type: ignore[arg-type]

    summary = builder.inspect_database()

    assert summary.schema == "analytics"
    assert {relation.name: relation.row_count for relation in summary.relations} == (
        EXPECTED_RELATION_COUNTS
    )
    assert fake["kwargs"] == {
        "host": "db.example",
        "port": 55432,
        "dbname": "dig",
        "user": "dig_admin",
        "password": "database-secret",
    }
    catalog_query, catalog_params = connection.fake_cursor.executions[0]
    assert isinstance(catalog_query, str)
    assert "table_schema = %s AND table_name = %s" in catalog_query
    assert catalog_params == ("analytics", "customers")
    count_query, count_params = connection.fake_cursor.executions[1]
    assert isinstance(count_query, sql.Composed)
    assert count_query.as_string(None) == 'SELECT count(*) FROM "analytics"."customers"'
    assert count_params is None
    assert "database-secret" not in summary.to_json()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"missing": "orders"}, "关系不存在：orders"),
        ({"count_delta": 1}, "行数不匹配：customers"),
    ],
)
def test_inspect_database_rejects_missing_or_unexpected_relation_state(
    tmp_path: Path,
    kwargs: dict[str, object],
    message: str,
) -> None:
    _, fake = _fake_database(**kwargs)  # type: ignore[arg-type]
    builder = BaselineBuilder(Settings(_env_file=None), tmp_path, db_connect=fake["connect"])  # type: ignore[arg-type]

    with pytest.raises(BaselineError, match=message):
        builder.inspect_database()


def test_inspect_database_wraps_connection_error_without_password(
    tmp_path: Path,
) -> None:
    settings = Settings(_env_file=None, postgres_password="database-secret")

    def connect(**_: object) -> None:
        raise RuntimeError("connection failed for database-secret")

    builder = BaselineBuilder(settings, tmp_path, db_connect=connect)

    with pytest.raises(BaselineError) as error:
        builder.inspect_database()

    assert "读取数据库失败" in str(error.value)
    assert "database-secret" not in str(error.value)
    assert "***" in str(error.value)
    assert "database-secret" not in "".join(traceback.format_exception(error.value))


def test_write_summary_persists_canonical_json(tmp_path: Path) -> None:
    builder = BaselineBuilder(Settings(_env_file=None), tmp_path)
    summary = make_baseline_summary(
        "analytics",
        (_relation("customers", 100, ("id", "integer", False, 1)),),
    )

    builder.write_summary(summary)

    summary_path = tmp_path / ".dig" / "baseline-summary.json"
    assert summary_path.is_file()
    assert summary_path.read_text(encoding="utf-8") == summary.to_json()


def test_build_calls_stages_in_fixed_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str] = []
    summary = make_baseline_summary(
        "analytics",
        (_relation("customers", 100, ("id", "integer", False, 1)),),
    )
    builder = BaselineBuilder(Settings(_env_file=None), tmp_path)
    monkeypatch.setattr(
        baseline_module,
        "validate_upstream_fixture",
        lambda project_root: calls.append("validate_upstream_fixture") or "commit",
    )
    for stage in ("start_postgres", "run_dbt", "validate_dbt_artifacts"):
        monkeypatch.setattr(
            builder,
            stage,
            lambda stage=stage: calls.append(stage),
        )
    monkeypatch.setattr(
        builder,
        "inspect_database",
        lambda: calls.append("inspect_database") or summary,
    )
    monkeypatch.setattr(builder, "write_summary", lambda _: calls.append("write_summary"))

    assert builder.build() is summary
    assert calls == [
        "validate_upstream_fixture",
        "start_postgres",
        "run_dbt",
        "validate_dbt_artifacts",
        "inspect_database",
        "write_summary",
    ]


def test_build_runs_compose_seed_then_dbt_build_without_shell(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    settings = Settings(_env_file=None, postgres_password="command-secret")

    def fake_run(command: list[str], **kwargs: object) -> CompletedProcess[str]:
        calls.append((command, kwargs))
        return CompletedProcess(command, 0, stdout="ok", stderr="")

    builder = BaselineBuilder(
        settings=settings,
        project_root=tmp_path,
        run_command=fake_run,
    )
    builder.start_postgres()
    builder.run_dbt()

    commands = [command for command, _ in calls]
    assert commands[0][:3] == ["docker", "compose", "-f"]
    assert commands[0][-4:] == ["up", "-d", "--wait", "postgres"]
    assert commands[1][0:2] == ["dbt", "seed"]
    assert "--full-refresh" in commands[1]
    assert commands[2][0:2] == ["dbt", "build"]
    assert all(kwargs.get("shell") is not True for _, kwargs in calls)
    assert all(kwargs["cwd"] == tmp_path for _, kwargs in calls)
    assert all(kwargs["timeout"] == settings.command_timeout_seconds for _, kwargs in calls)
    assert all(kwargs["text"] is True for _, kwargs in calls)
    assert all(kwargs["encoding"] == "utf-8" for _, kwargs in calls)
    assert all(kwargs["errors"] == "replace" for _, kwargs in calls)
    assert all(
        "command-secret" not in argument for command in commands for argument in command
    )
    for _, kwargs in calls:
        env = kwargs["env"]
        assert isinstance(env, dict)
        assert env["DIG_POSTGRES_PASSWORD"] == "command-secret"

    for command in commands[1:]:
        assert str(tmp_path / ".dig" / "dbt" / "target") in command
        assert str(tmp_path / ".dig" / "dbt" / "logs") in command
    assert commands[1][-1] == "--full-refresh"
    assert commands[2][-1] == "--no-use-colors"


def test_nonzero_command_is_wrapped_and_password_is_redacted(
    tmp_path: Path,
) -> None:
    settings = Settings(_env_file=None, postgres_password="secret-password")

    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        return CompletedProcess(
            command,
            17,
            stdout="stdout secret-password",
            stderr="stderr secret-password",
        )

    builder = BaselineBuilder(settings, tmp_path, fake_run)

    with pytest.raises(BaselineError) as error:
        builder.start_postgres()

    message = str(error.value)
    assert "启动 PostgreSQL" in message
    assert "exit=17" in message
    assert "secret-password" not in message
    assert "***" in message


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("dbt", 3), OSError("not found")])
def test_command_execution_failures_are_wrapped(
    tmp_path: Path,
    failure: BaseException,
) -> None:
    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        raise failure

    builder = BaselineBuilder(Settings(_env_file=None), tmp_path, fake_run)

    with pytest.raises(BaselineError, match="执行") as error:
        builder.start_postgres()
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_baseline_error_conversion_does_not_retain_dbt_context(
    tmp_path: Path,
) -> None:
    settings = Settings(_env_file=None, postgres_password="database-secret")

    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        raise OSError("dbt failed with database-secret")

    builder = BaselineBuilder(settings, tmp_path, fake_run)

    with pytest.raises(BaselineError) as error:
        builder.run_dbt()

    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert "database-secret" not in str(error.value)


@pytest.mark.parametrize(
    "missing_name",
    ["manifest.json", "run_results.json", "dbt.log"],
)
def test_validate_dbt_artifacts_rejects_missing_files(
    tmp_path: Path,
    missing_name: str,
) -> None:
    builder = BaselineBuilder(Settings(_env_file=None), tmp_path)
    target = builder.dbt_target
    logs = builder.dbt_logs
    target.mkdir(parents=True)
    logs.mkdir(parents=True)

    (target / "manifest.json").write_text('{"nodes": {}}', encoding="utf-8")
    (target / "run_results.json").write_text(
        json.dumps({"results": [{"status": "success"}]}),
        encoding="utf-8",
    )
    (logs / "dbt.log").write_text("log", encoding="utf-8")
    (target / missing_name).unlink(missing_ok=True)
    (logs / missing_name).unlink(missing_ok=True)

    with pytest.raises(BaselineError, match="缺少 dbt artifact"):
        builder.validate_dbt_artifacts()


def test_validate_dbt_artifacts_rejects_empty_results(tmp_path: Path) -> None:
    builder = BaselineBuilder(Settings(_env_file=None), tmp_path)
    builder.dbt_target.mkdir(parents=True)
    builder.dbt_logs.mkdir(parents=True)
    (builder.dbt_target / "manifest.json").write_text('{"nodes": {}}', encoding="utf-8")
    (builder.dbt_target / "run_results.json").write_text(
        json.dumps({"results": []}),
        encoding="utf-8",
    )
    (builder.dbt_logs / "dbt.log").write_text("log", encoding="utf-8")

    with pytest.raises(BaselineError, match="results 不能为空"):
        builder.validate_dbt_artifacts()


@pytest.mark.parametrize("status", ["error", "fail", "skipped", "warn", "unknown"])
def test_validate_dbt_artifacts_rejects_non_success_status(
    tmp_path: Path,
    status: str,
) -> None:
    builder = BaselineBuilder(Settings(_env_file=None), tmp_path)
    builder.dbt_target.mkdir(parents=True)
    builder.dbt_logs.mkdir(parents=True)
    (builder.dbt_target / "manifest.json").write_text('{"nodes": {}}', encoding="utf-8")
    (builder.dbt_target / "run_results.json").write_text(
        json.dumps({"results": [{"status": status}]}),
        encoding="utf-8",
    )
    (builder.dbt_logs / "dbt.log").write_text("log", encoding="utf-8")

    with pytest.raises(BaselineError, match="非法 dbt status"):
        builder.validate_dbt_artifacts()


@pytest.mark.parametrize("empty_name", ["manifest.json", "run_results.json", "dbt.log"])
def test_validate_dbt_artifacts_rejects_empty_files(
    tmp_path: Path,
    empty_name: str,
) -> None:
    builder = BaselineBuilder(Settings(_env_file=None), tmp_path)
    builder.dbt_target.mkdir(parents=True)
    builder.dbt_logs.mkdir(parents=True)
    (builder.dbt_target / "manifest.json").write_text('{"nodes": {}}', encoding="utf-8")
    (builder.dbt_target / "run_results.json").write_text(
        json.dumps({"results": [{"status": "success"}]}),
        encoding="utf-8",
    )
    (builder.dbt_logs / "dbt.log").write_text("log", encoding="utf-8")
    target = builder.dbt_target / empty_name
    log = builder.dbt_logs / empty_name
    (log if empty_name == "dbt.log" else target).write_text("", encoding="utf-8")

    with pytest.raises(BaselineError, match="为空"):
        builder.validate_dbt_artifacts()


def test_validate_dbt_artifacts_rejects_invalid_manifest(tmp_path: Path) -> None:
    builder = BaselineBuilder(Settings(_env_file=None), tmp_path)
    builder.dbt_target.mkdir(parents=True)
    builder.dbt_logs.mkdir(parents=True)
    (builder.dbt_target / "manifest.json").write_text("not-json", encoding="utf-8")
    (builder.dbt_target / "run_results.json").write_text(
        json.dumps({"results": [{"status": "success"}]}),
        encoding="utf-8",
    )
    (builder.dbt_logs / "dbt.log").write_text("log", encoding="utf-8")

    with pytest.raises(BaselineError, match="无法解析 dbt artifact"):
        builder.validate_dbt_artifacts()
