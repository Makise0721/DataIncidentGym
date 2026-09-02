from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess
from typing import NoReturn

from data_incident_gym.config import PROJECT_ROOT, Settings

RunCommand = Callable[..., CompletedProcess[str]]


@dataclass(frozen=True)
class DbtRunResult:
    return_code: int
    stdout: str
    stderr: str


class DbtExecutionError(RuntimeError):
    """Raised when dbt cannot execute or a healthy dbt stage fails."""


def raise_without_context(error: Exception) -> NoReturn:
    try:
        raise error from None
    except Exception as raised:
        raised.__context__ = None
        raise


class DbtRunner:
    def __init__(
        self,
        settings: Settings,
        project_root: Path = PROJECT_ROOT,
        run_command: RunCommand = subprocess.run,
    ) -> None:
        self.settings = settings
        self.project_root = project_root
        self.run_command = run_command
        self.dbt_project = project_root / "third_party" / "jaffle_shop"
        self.dbt_profiles = project_root / "config" / "dbt"

    def _redact(self, value: str) -> str:
        secret = self.settings.postgres_password.get_secret_value()
        return value.replace(secret, "***") if secret else value

    def _command(
        self,
        command: str,
        target_path: Path,
        log_path: Path,
        *extra: str,
    ) -> list[str]:
        return [
            "dbt",
            command,
            "--project-dir",
            str(self.dbt_project),
            "--profiles-dir",
            str(self.dbt_profiles),
            "--target",
            "dev",
            "--target-path",
            str(target_path),
            "--log-path",
            str(log_path),
            "--no-use-colors",
            *extra,
        ]

    def _invoke(self, stage: str, command: Sequence[str]) -> DbtRunResult:
        try:
            result = self.run_command(
                list(command),
                cwd=self.project_root,
                env=self.settings.subprocess_environment(),
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.command_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise_without_context(
                DbtExecutionError(f"{stage} 无法执行：{self._redact(str(exc))}")
            )
        return DbtRunResult(
            return_code=result.returncode,
            stdout=self._redact(result.stdout or ""),
            stderr=self._redact(result.stderr or ""),
        )

    @staticmethod
    def _ensure_success(stage: str, result: DbtRunResult) -> None:
        if result.return_code != 0:
            raise DbtExecutionError(
                f"{stage} 失败（exit={result.return_code}）\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )

    def _prepare(self, target_path: Path, log_path: Path) -> None:
        try:
            target_path.mkdir(parents=True, exist_ok=True)
            log_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise_without_context(
                DbtExecutionError(f"无法创建 dbt 产物目录：{self._redact(str(exc))}")
            )

    def run_healthy(self, target_path: Path, log_path: Path) -> None:
        self._prepare(target_path, log_path)
        seed = self._invoke(
            "加载固定 seeds",
            self._command("seed", target_path, log_path, "--full-refresh"),
        )
        self._ensure_success("加载固定 seeds", seed)
        build = self._invoke(
            "执行 dbt build",
            self._command("build", target_path, log_path),
        )
        self._ensure_success("执行 dbt build", build)

    def run_scenario(
        self,
        target_path: Path,
        log_path: Path,
        *,
        exclude_resource_types: Sequence[str] = ("seed",),
    ) -> DbtRunResult:
        self._prepare(target_path, log_path)
        exclusions = tuple(
            value
            for resource_type in exclude_resource_types
            for value in ("--exclude-resource-type", resource_type)
        )
        return self._invoke(
            "执行场景 dbt build",
            self._command(
                "build",
                target_path,
                log_path,
                *exclusions,
            ),
        )
