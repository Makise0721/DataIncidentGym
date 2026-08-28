from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import subprocess
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from subprocess import CompletedProcess
from tempfile import TemporaryDirectory
from typing import Any, Literal, Self
from urllib.request import Request, urlopen

import psycopg
from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, model_validator
from pydantic_ai import Agent, ModelRetry, RunContext, UsageLimits
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.diagnostic_config import DiagnosticSettings

RunCommand = Callable[..., CompletedProcess[str]]
DatabaseConnect = Callable[..., Any]
UrlOpen = Callable[..., AbstractContextManager[Any]]
TemporaryDirectoryFactory = Callable[[], AbstractContextManager[str]]

_EXPECTED_PYTHON = "3.12.10"
_EXPECTED_UV = "0.11.24"
_COMMAND_TIMEOUT_SECONDS = 30
_URL_TIMEOUT_SECONDS = 5
_MAX_RESPONSE_BYTES = 1024 * 1024
_VERSION_PATTERN = re.compile(r"^\d+(?:\.\d+){2,}$")
_SAFE_MODEL_NAME = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,127}$")
_DIAGNOSTIC_DATABASE_ENV_KEYS = (
    "DIG_DIAGNOSTIC_POSTGRES_HOST",
    "DIG_DIAGNOSTIC_POSTGRES_PORT",
    "DIG_DIAGNOSTIC_POSTGRES_DATABASE",
    "DIG_DIAGNOSTIC_POSTGRES_SCHEMA",
    "DIG_DIAGNOSTIC_POSTGRES_USER",
    "DIG_DIAGNOSTIC_POSTGRES_PASSWORD",
)


class DoctorStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class DoctorCheckCode(StrEnum):
    PYTHON = "PYTHON"
    UV = "UV"
    DOCKER = "DOCKER"
    COMPOSE_POSTGRES = "COMPOSE_POSTGRES"
    POSTGRES_CONNECTION = "POSTGRES_CONNECTION"
    DBT_PROFILE_CONNECTION = "DBT_PROFILE_CONNECTION"
    MODEL_ENDPOINT = "MODEL_ENDPOINT"
    MODEL_PRESENT = "MODEL_PRESENT"
    MODEL_TOOL_STRUCTURED_OUTPUT = "MODEL_TOOL_STRUCTURED_OUTPUT"


CHECK_ORDER = tuple(code.value for code in DoctorCheckCode)

RECOMMENDATION_BY_CHECK = {
    DoctorCheckCode.PYTHON: "USE_PYTHON_3_12",
    DoctorCheckCode.UV: "INSTALL_UV_0_11_24",
    DoctorCheckCode.DOCKER: "START_DOCKER_DESKTOP",
    DoctorCheckCode.COMPOSE_POSTGRES: "START_POSTGRES_COMPOSE",
    DoctorCheckCode.POSTGRES_CONNECTION: "CHECK_POSTGRES_SETTINGS",
    DoctorCheckCode.DBT_PROFILE_CONNECTION: "CHECK_DBT_PROFILE",
    DoctorCheckCode.MODEL_ENDPOINT: "CHECK_MODEL_ENDPOINT",
    DoctorCheckCode.MODEL_PRESENT: "CHECK_MIMO_MODEL_ACCESS",
    DoctorCheckCode.MODEL_TOOL_STRUCTURED_OUTPUT: "CHECK_MODEL_TOOL_CALLING",
}
EXPECTED_RECOMMENDATIONS = set(RECOMMENDATION_BY_CHECK.values())


class DoctorCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: DoctorCheckCode
    passed: StrictBool
    observed: StrictStr
    reason_code: StrictStr
    recommendation_code: StrictStr | None


class DoctorResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DoctorStatus
    checks: tuple[DoctorCheck, ...]

    @model_validator(mode="after")
    def validate_complete_checks(self) -> Self:
        if tuple(check.code for check in self.checks) != tuple(DoctorCheckCode):
            raise ValueError("doctor checks must be complete and ordered")
        for check in self.checks:
            suffix = "PASSED" if check.passed else "FAILED"
            expected_reason = f"{check.code.value}_{suffix}"
            if check.reason_code != expected_reason:
                raise ValueError("doctor reason code must match check")
            if not check.observed.strip():
                raise ValueError("doctor observed value must not be blank")
            expected_recommendation = (
                None if check.passed else RECOMMENDATION_BY_CHECK[check.code]
            )
            if check.recommendation_code != expected_recommendation:
                raise ValueError("doctor recommendation must match failed check")
        expected = (
            DoctorStatus.PASSED
            if all(check.passed for check in self.checks)
            else DoctorStatus.FAILED
        )
        if self.status != expected:
            raise ValueError("doctor status must match checks")
        return self


class DoctorProbeOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_value: Literal["DOCTOR_TOOL_OK"]


@dataclass
class ProbeState:
    tool_called: bool = False


class DoctorRunner:
    def __init__(
        self,
        diagnostic_settings: DiagnosticSettings,
        project_root: Path,
        *,
        run_command: RunCommand,
        db_connect: DatabaseConnect,
        url_open: UrlOpen,
        model: Model,
        temporary_directory: TemporaryDirectoryFactory,
    ) -> None:
        self._diagnostic_settings = diagnostic_settings
        self._project_root = project_root
        self._run_command = run_command
        self._db_connect = db_connect
        self._url_open = url_open
        self._model = model
        self._temporary_directory = temporary_directory

    @classmethod
    def for_project(
        cls,
        diagnostic_settings: DiagnosticSettings,
        project_root: Path = PROJECT_ROOT,
    ) -> DoctorRunner:
        provider = OpenAIProvider(
            base_url=str(diagnostic_settings.model_base_url),
            api_key=diagnostic_settings.model_api_key.get_secret_value(),
        )
        model = OpenAIChatModel(diagnostic_settings.model_name, provider=provider)
        return cls(
            diagnostic_settings,
            project_root,
            run_command=subprocess.run,
            db_connect=psycopg.connect,
            url_open=urlopen,
            model=model,
            temporary_directory=TemporaryDirectory,
        )

    @staticmethod
    def _check(
        code: DoctorCheckCode,
        passed: bool,
        observed: str,
    ) -> DoctorCheck:
        safe_observed = observed if passed else "UNAVAILABLE"
        return DoctorCheck(
            code=code,
            passed=passed,
            observed=safe_observed,
            reason_code=f"{code.value}_{'PASSED' if passed else 'FAILED'}",
            recommendation_code=None if passed else RECOMMENDATION_BY_CHECK[code],
        )

    @staticmethod
    def _process_environment() -> dict[str, str]:
        return {
            key: value
            for key in (
                "PATH",
                "PATHEXT",
                "SystemRoot",
                "WINDIR",
                "TEMP",
                "TMP",
                "ProgramFiles",
            )
            if (value := os.environ.get(key)) is not None
        }

    def _diagnostic_environment(self) -> dict[str, str]:
        return {
            **self._process_environment(),
            "DIG_DIAGNOSTIC_POSTGRES_HOST": self._diagnostic_settings.postgres_host,
            "DIG_DIAGNOSTIC_POSTGRES_PORT": str(self._diagnostic_settings.postgres_port),
            "DIG_DIAGNOSTIC_POSTGRES_DATABASE": self._diagnostic_settings.postgres_database,
            "DIG_DIAGNOSTIC_POSTGRES_SCHEMA": self._diagnostic_settings.postgres_schema,
            "DIG_DIAGNOSTIC_POSTGRES_USER": self._diagnostic_settings.postgres_user,
            "DIG_DIAGNOSTIC_POSTGRES_PASSWORD": (
                self._diagnostic_settings.postgres_password.get_secret_value()
            ),
            "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
        }

    def _has_explicit_diagnostic_database_config(self) -> bool:
        if all(key in os.environ for key in _DIAGNOSTIC_DATABASE_ENV_KEYS):
            return True
        env_file = self._project_root / ".env.diagnostic"
        try:
            keys = {
                line.split("=", 1)[0].strip()
                for line in env_file.read_text(encoding="utf-8").splitlines()
                if "=" in line and line.split("=", 1)[0].strip()
            }
        except OSError:
            return False
        return set(_DIAGNOSTIC_DATABASE_ENV_KEYS).issubset(keys)

    def _command(
        self,
        command: Sequence[str],
        *,
        environment: dict[str, str] | None = None,
    ) -> CompletedProcess[str] | None:
        try:
            return self._run_command(
                list(command),
                cwd=self._project_root,
                env=self._process_environment() if environment is None else environment,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_COMMAND_TIMEOUT_SECONDS,
                shell=False,
            )
        except Exception:
            return None

    def _python_check(self) -> DoctorCheck:
        version = platform.python_version()
        return self._check(
            DoctorCheckCode.PYTHON,
            version == _EXPECTED_PYTHON,
            version if version == _EXPECTED_PYTHON else "UNAVAILABLE",
        )

    def _uv_check(self) -> DoctorCheck:
        result = self._command(["uv", "--version"])
        if result is None or result.returncode != 0:
            return self._check(DoctorCheckCode.UV, False, "UNAVAILABLE")
        first_line = next(
            (line.strip() for line in (result.stdout or "").splitlines() if line.strip()),
            "",
        )
        match = re.fullmatch(r"uv\s+(\d+\.\d+\.\d+)(?:\s+.*)?", first_line)
        version = match.group(1) if match else "UNAVAILABLE"
        return self._check(DoctorCheckCode.UV, version == _EXPECTED_UV, version)

    def _docker_check(self) -> DoctorCheck:
        result = self._command(["docker", "version", "--format", "{{.Server.Version}}"])
        if result is None or result.returncode != 0:
            return self._check(DoctorCheckCode.DOCKER, False, "UNAVAILABLE")
        version = (result.stdout or "").strip()
        passed = _VERSION_PATTERN.fullmatch(version) is not None
        return self._check(
            DoctorCheckCode.DOCKER,
            passed,
            version if passed else "UNAVAILABLE",
        )

    def _compose_check(self) -> DoctorCheck:
        result = self._command(
            [
                "docker",
                "compose",
                "-f",
                str(self._project_root / "compose.yaml"),
                "ps",
                "--status",
                "running",
                "--services",
            ]
        )
        if result is None or result.returncode != 0:
            return self._check(DoctorCheckCode.COMPOSE_POSTGRES, False, "UNAVAILABLE")
        services = {line.strip() for line in (result.stdout or "").splitlines() if line.strip()}
        return self._check(
            DoctorCheckCode.COMPOSE_POSTGRES,
            "postgres" in services,
            "postgres" if "postgres" in services else "UNAVAILABLE",
        )

    def _postgres_check(self) -> DoctorCheck:
        try:
            with self._db_connect(
                host=self._diagnostic_settings.postgres_host,
                port=self._diagnostic_settings.postgres_port,
                dbname=self._diagnostic_settings.postgres_database,
                user=self._diagnostic_settings.postgres_user,
                password=self._diagnostic_settings.postgres_password.get_secret_value(),
            ) as connection, connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Exception:
            return self._check(DoctorCheckCode.POSTGRES_CONNECTION, False, "UNAVAILABLE")
        return self._check(DoctorCheckCode.POSTGRES_CONNECTION, True, "CONNECTED")

    def _dbt_profile_check(self) -> DoctorCheck:
        try:
            with self._temporary_directory() as temporary:
                logs = Path(temporary) / "logs"
                logs.mkdir()
                command = [
                    "dbt",
                    "debug",
                    "--project-dir",
                    str(self._project_root / "third_party" / "jaffle_shop"),
                    "--profiles-dir",
                    str(self._project_root / "config" / "dbt" / "diagnostic"),
                    "--target",
                    "dev",
                    "--log-path",
                    str(logs),
                    "--connection",
                    "--no-write-json",
                    "--no-partial-parse",
                    "--no-use-colors",
                    "--no-send-anonymous-usage-stats",
                    "--no-upload-to-artifacts-ingest-api",
                ]
                result = self._command(command, environment=self._diagnostic_environment())
                passed = result is not None and result.returncode == 0
        except Exception:
            passed = False
        return self._check(
            DoctorCheckCode.DBT_PROFILE_CONNECTION,
            passed,
            "CONNECTED" if passed else "UNAVAILABLE",
        )

    def _endpoint_check(self) -> tuple[DoctorCheck, bool, set[str]]:
        endpoint = self._diagnostic_settings.model_base_url.rstrip("/") + "/models"
        try:
            request = Request(
                endpoint,
                headers={
                    "api-key": self._diagnostic_settings.model_api_key.get_secret_value(),
                },
            )
            with self._url_open(request, timeout=_URL_TIMEOUT_SECONDS) as response:
                body = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(body) > _MAX_RESPONSE_BYTES:
                raise ValueError("response too large")
            payload = json.loads(body.decode("utf-8"))
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list):
                raise ValueError("invalid models response")
            if any(
                not isinstance(item, dict)
                or type(item.get("id")) is not str
                or _SAFE_MODEL_NAME.fullmatch(item["id"]) is None
                for item in data
            ):
                raise ValueError("invalid model entry")
            model_ids = {
                item["id"]
                for item in data
            }
        except Exception:
            return self._check(DoctorCheckCode.MODEL_ENDPOINT, False, "UNAVAILABLE"), False, set()
        return self._check(DoctorCheckCode.MODEL_ENDPOINT, True, "REACHABLE"), True, model_ids

    def _model_present_check(self, endpoint_ok: bool, model_ids: set[str]) -> DoctorCheck:
        model_name = self._diagnostic_settings.model_name
        passed = (
            endpoint_ok
            and _SAFE_MODEL_NAME.fullmatch(model_name) is not None
            and model_name in model_ids
        )
        return self._check(
            DoctorCheckCode.MODEL_PRESENT,
            passed,
            model_name if passed else "UNAVAILABLE",
        )

    async def _model_probe_check(self, model_available: bool) -> DoctorCheck:
        if not model_available:
            return self._check(
                DoctorCheckCode.MODEL_TOOL_STRUCTURED_OUTPUT,
                False,
                "UNAVAILABLE",
            )

        state = ProbeState()
        agent = Agent(
            self._model,
            deps_type=ProbeState,
            output_type=DoctorProbeOutput,
        )

        @agent.tool
        def read_probe_value(ctx: RunContext[ProbeState]) -> str:
            ctx.deps.tool_called = True
            return "DOCTOR_TOOL_OK"

        @agent.output_validator
        def require_real_tool_call(
            ctx: RunContext[ProbeState], output: DoctorProbeOutput
        ) -> DoctorProbeOutput:
            if not ctx.deps.tool_called or output.tool_value != "DOCTOR_TOOL_OK":
                raise ModelRetry("DOCTOR_TOOL_REQUIRED")
            return output

        passed = False
        try:
            async with asyncio.timeout(60):
                result = await agent.run(
                    "Call read_probe_value and return its value in DoctorProbeOutput.",
                    deps=state,
                    usage_limits=UsageLimits(request_limit=2, tool_calls_limit=1),
                    retries={"output": 1},
                )
            passed = state.tool_called and result.output.tool_value == "DOCTOR_TOOL_OK"
        except Exception:
            passed = False
        return self._check(
            DoctorCheckCode.MODEL_TOOL_STRUCTURED_OUTPUT,
            passed,
            "TOOL_CALLED" if passed else "UNAVAILABLE",
        )

    async def run(self) -> DoctorResult:
        checks = [self._python_check(), self._uv_check()]
        docker_check = self._docker_check()
        checks.append(docker_check)
        compose_check = (
            self._compose_check()
            if docker_check.passed
            else self._check(DoctorCheckCode.COMPOSE_POSTGRES, False, "UNAVAILABLE")
        )
        checks.append(compose_check)
        postgres_check = (
            self._postgres_check()
            if compose_check.passed and self._has_explicit_diagnostic_database_config()
            else self._check(DoctorCheckCode.POSTGRES_CONNECTION, False, "UNAVAILABLE")
        )
        checks.append(postgres_check)
        dbt_check = (
            self._dbt_profile_check()
            if postgres_check.passed
            else self._check(
                DoctorCheckCode.DBT_PROFILE_CONNECTION,
                False,
                "UNAVAILABLE",
            )
        )
        checks.append(dbt_check)
        endpoint_check, endpoint_ok, model_ids = self._endpoint_check()
        checks.append(endpoint_check)
        model_check = self._model_present_check(endpoint_ok, model_ids)
        checks.append(model_check)
        checks.append(
            await self._model_probe_check(
                model_available=model_check.passed,
            )
        )
        return DoctorResult(
            status=(
                DoctorStatus.PASSED
                if all(check.passed for check in checks)
                else DoctorStatus.FAILED
            ),
            checks=tuple(checks),
        )
