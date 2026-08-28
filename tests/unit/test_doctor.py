from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

import pytest
from pydantic import ValidationError
from pydantic_ai.models.test import TestModel

import data_incident_gym.doctor as doctor_module
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.doctor import (
    CHECK_ORDER,
    EXPECTED_RECOMMENDATIONS,
    RECOMMENDATION_BY_CHECK,
    DoctorCheck,
    DoctorCheckCode,
    DoctorResult,
    DoctorRunner,
    DoctorStatus,
)

PROJECT_MARKER = "project-marker.txt"
RAW_ERROR = "password=TEST_REDACTED_VALUE C:\\secret\\doctor.log"
MODEL_NAME = "gemma4:e4b"


class FakeCommand:
    def __init__(self, failed_code: str | None, raw_error: str) -> None:
        self.failed_code = failed_code
        self.raw_error = raw_error
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> CompletedProcess[str]:
        self.calls.append((args, kwargs))
        assert isinstance(args, list)
        assert kwargs["shell"] is False
        assert kwargs["timeout"] > 0
        if args[0] == "uv":
            check = "UV"
            stdout = "uv 0.11.24\n"
        elif args[:2] == ["docker", "version"]:
            check = "DOCKER"
            stdout = "27.5.1\n"
        elif args[:3] == ["docker", "compose", "-f"]:
            check = "COMPOSE_POSTGRES"
            stdout = "postgres\n"
        else:
            check = "DBT_PROFILE_CONNECTION"
            stdout = "Connection test: OK\n"
        returncode = 1 if check == self.failed_code else 0
        return CompletedProcess(args, returncode, stdout, self.raw_error if returncode else "")


class FakeCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, object | None]] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: str, params: object | None = None) -> None:
        self.executed.append((query, params))


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_value = FakeCursor()
        self.commit_calls = 0

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self.cursor_value

    def commit(self) -> None:
        self.commit_calls += 1


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.read_limits: list[int] = []

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        self.read_limits.append(limit)
        return self.body


class TemporaryDirectoryFactory:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.paths: list[Path] = []

    @contextmanager
    def __call__(self):
        path = self.root / f"doctor-temp-{len(self.paths)}"
        path.mkdir()
        self.paths.append(path)
        try:
            yield str(path)
        finally:
            shutil.rmtree(path)


class DoctorDeps:
    def __init__(
        self,
        runner: DoctorRunner,
        command: FakeCommand,
        connection: FakeConnection,
        response: FakeResponse,
        temporary_directory: TemporaryDirectoryFactory,
        model: TestModel,
        project_root: Path,
    ) -> None:
        self.runner = runner
        self.command = command
        self.connection = connection
        self.response = response
        self.temporary_directory = temporary_directory
        self.model = model
        self.project_root = project_root


@pytest.fixture
def doctor_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    marker = tmp_path / PROJECT_MARKER
    marker.write_text("unchanged", encoding="utf-8")

    def factory(
        failed_code: str | None = None,
        raw_error: str = RAW_ERROR,
    ) -> DoctorDeps:
        monkeypatch.setattr(
            doctor_module.platform,
            "python_version",
            lambda: "3.12.9" if failed_code == "PYTHON" else "3.12.10",
        )
        command = FakeCommand(failed_code, raw_error)
        connection = FakeConnection()
        response = FakeResponse(
            json.dumps(
                {"data": [] if failed_code == "MODEL_PRESENT" else [{"id": MODEL_NAME}]}
            ).encode("utf-8")
        )
        temporary_directory = TemporaryDirectoryFactory(tmp_path)
        model = TestModel(
            call_tools=[]
            if failed_code == "MODEL_TOOL_STRUCTURED_OUTPUT"
            else ["read_probe_value"],
            custom_output_args={"tool_value": "DOCTOR_TOOL_OK"},
        )
        for key, value in {
            "DIG_DIAGNOSTIC_POSTGRES_HOST": "127.0.0.1",
            "DIG_DIAGNOSTIC_POSTGRES_PORT": "55432",
            "DIG_DIAGNOSTIC_POSTGRES_DATABASE": "data_incident_gym",
            "DIG_DIAGNOSTIC_POSTGRES_SCHEMA": "analytics",
            "DIG_DIAGNOSTIC_POSTGRES_USER": "dig_reader",
            "DIG_DIAGNOSTIC_POSTGRES_PASSWORD": "diagnostic-secret",
        }.items():
            monkeypatch.setenv(key, value)

        def connect(**_: object) -> FakeConnection:
            if failed_code == "POSTGRES_CONNECTION":
                raise RuntimeError(raw_error)
            return connection

        def url_open(url: str, *, timeout: float) -> FakeResponse:
            assert timeout == 5
            if failed_code == "OLLAMA_ENDPOINT":
                raise OSError(raw_error)
            return response

        settings = DiagnosticSettings(_env_file=None, model_name=MODEL_NAME)
        runner = DoctorRunner(
            settings,
            tmp_path,
            run_command=command,
            db_connect=connect,
            url_open=url_open,
            model=model,
            temporary_directory=temporary_directory,
        )
        return DoctorDeps(
            runner,
            command,
            connection,
            response,
            temporary_directory,
            model,
            tmp_path,
        )

    return factory


@pytest.fixture
def doctor(doctor_factory) -> DoctorDeps:
    return doctor_factory()


@pytest.mark.asyncio
async def test_doctor_passes_only_when_every_read_only_check_passes(doctor: DoctorDeps) -> None:
    before = sorted(
        path.relative_to(doctor.project_root) for path in doctor.project_root.rglob("*")
    )

    result = await doctor.runner.run()

    assert result.status == DoctorStatus.PASSED
    assert tuple(check.code.value for check in result.checks) == CHECK_ORDER
    assert all(check.passed for check in result.checks)
    assert doctor.model.last_model_request_parameters is not None
    assert {
        tool.name for tool in doctor.model.last_model_request_parameters.function_tools
    } == {"read_probe_value"}
    assert doctor.connection.cursor_value.executed == [("SELECT 1", None)]
    assert doctor.connection.commit_calls == 0
    assert doctor.response.read_limits == [1024 * 1024 + 1]
    assert all(not path.exists() for path in doctor.temporary_directory.paths)
    after = sorted(
        path.relative_to(doctor.project_root) for path in doctor.project_root.rglob("*")
    )
    assert before == after


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_code", CHECK_ORDER)
async def test_each_failed_check_returns_fixed_recommendation_without_raw_error(
    doctor_factory,
    failed_code: str,
) -> None:
    doctor = doctor_factory(failed_code, raw_error=RAW_ERROR)

    result = await doctor.runner.run()

    failed = next(check for check in result.checks if check.code.value == failed_code)
    serialized = result.model_dump_json()
    assert result.status == DoctorStatus.FAILED
    assert failed.reason_code == f"{failed_code}_FAILED"
    assert failed.recommendation_code in EXPECTED_RECOMMENDATIONS
    assert "TEST_REDACTED_VALUE" not in serialized
    assert "C:\\secret\\doctor.log" not in serialized
    assert all(check.observed == "UNAVAILABLE" for check in result.checks if not check.passed)
    expected_command_count = {
        "PYTHON": 4,
        "UV": 4,
        "DOCKER": 2,
        "COMPOSE_POSTGRES": 3,
        "POSTGRES_CONNECTION": 3,
        "DBT_PROFILE_CONNECTION": 4,
        "OLLAMA_ENDPOINT": 4,
        "MODEL_PRESENT": 4,
        "MODEL_TOOL_STRUCTURED_OUTPUT": 4,
    }
    assert len(doctor.command.calls) == expected_command_count[failed_code]


@pytest.mark.asyncio
async def test_missing_explicit_diagnostic_database_config_fails_closed_without_db_or_dbt(
    doctor: DoctorDeps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in (
        "DIG_DIAGNOSTIC_POSTGRES_HOST",
        "DIG_DIAGNOSTIC_POSTGRES_PORT",
        "DIG_DIAGNOSTIC_POSTGRES_DATABASE",
        "DIG_DIAGNOSTIC_POSTGRES_SCHEMA",
        "DIG_DIAGNOSTIC_POSTGRES_USER",
        "DIG_DIAGNOSTIC_POSTGRES_PASSWORD",
    ):
        monkeypatch.delenv(key, raising=False)

    result = await doctor.runner.run()

    checks = {check.code: check for check in result.checks}
    assert checks[DoctorCheckCode.POSTGRES_CONNECTION].reason_code == (
        "POSTGRES_CONNECTION_FAILED"
    )
    assert checks[DoctorCheckCode.DBT_PROFILE_CONNECTION].reason_code == (
        "DBT_PROFILE_CONNECTION_FAILED"
    )
    assert doctor.connection.cursor_value.executed == []
    assert [args for args, _ in doctor.command.calls if args[0] == "dbt"] == []


@pytest.mark.asyncio
async def test_doctor_uses_only_diagnostic_profile_for_dbt_and_never_writes(
    doctor: DoctorDeps,
) -> None:
    await doctor.runner.run()

    dbt_calls = [call for call in doctor.command.calls if call[0][0] == "dbt"]
    assert len(dbt_calls) == 1
    args, kwargs = dbt_calls[0]
    assert args == [
        "dbt",
        "debug",
        "--project-dir",
        str(doctor.project_root / "third_party" / "jaffle_shop"),
        "--profiles-dir",
        str(doctor.project_root / "config" / "dbt" / "diagnostic"),
        "--target",
        "dev",
        "--log-path",
        str(doctor.temporary_directory.paths[0] / "logs"),
        "--connection",
        "--no-write-json",
        "--no-partial-parse",
        "--no-use-colors",
        "--no-send-anonymous-usage-stats",
        "--no-upload-to-artifacts-ingest-api",
    ]
    assert kwargs["shell"] is False
    environment = kwargs["env"]
    assert environment["DIG_DIAGNOSTIC_POSTGRES_USER"] == "dig_reader"
    assert "DIG_POSTGRES_PASSWORD" not in environment
    assert "DIG_POSTGRES_USER" not in environment
    assert all(
        not any(token in argument for token in (" up", " down", " restart"))
        for call_args, _ in doctor.command.calls
        for argument in call_args
    )
    assert all(
        not any(token in argument.lower() for token in ("build", "seed", "run"))
        for call_args, _ in doctor.command.calls
        if call_args[0] == "dbt"
        for argument in call_args
    )


def _passing_checks() -> tuple[DoctorCheck, ...]:
    return tuple(
        DoctorCheck(
            code=code,
            passed=True,
            observed="OK",
            reason_code=f"{code.value}_PASSED",
            recommendation_code=None,
        )
        for code in DoctorCheckCode
    )


def test_doctor_result_is_frozen_strict_and_complete() -> None:
    result = DoctorResult(status=DoctorStatus.PASSED, checks=_passing_checks())

    with pytest.raises((TypeError, ValidationError)):
        result.status = DoctorStatus.FAILED  # type: ignore[misc]
    with pytest.raises(ValidationError):
        DoctorCheck(
            code=DoctorCheckCode.PYTHON,
            passed=1,
            observed="OK",
            reason_code="PYTHON_PASSED",
            recommendation_code=None,
        )
    with pytest.raises(ValidationError):
        DoctorResult.model_validate(
            {
                "status": "PASSED",
                "checks": [check.model_dump() for check in _passing_checks()],
                "extra": 1,
            }
        )
    with pytest.raises(ValidationError):
        DoctorResult(
            status=DoctorStatus.PASSED,
            checks=_passing_checks()[:-1],
        )


def test_recommendation_table_covers_exactly_the_nine_checks() -> None:
    assert tuple(code.value for code in RECOMMENDATION_BY_CHECK) == CHECK_ORDER
    assert set(RECOMMENDATION_BY_CHECK.values()) == EXPECTED_RECOMMENDATIONS
