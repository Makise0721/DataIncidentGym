from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from subprocess import CompletedProcess

from data_incident_gym.config import PROJECT_ROOT, Settings

RunCommand = Callable[..., CompletedProcess[str]]


class BaselineError(RuntimeError):
    """Raised when the deterministic baseline cannot be built."""


def validate_upstream_fixture(project_root: Path) -> str:
    spec_path = project_root / "config" / "upstream" / "jaffle_shop.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    fixture = project_root / spec["path"]
    required = (
        fixture / "dbt_project.yml",
        fixture / "seeds" / "raw_customers.csv",
        fixture / "seeds" / "raw_orders.csv",
        fixture / "seeds" / "raw_payments.csv",
        fixture / "models" / "staging" / "stg_payments.sql",
    )
    if not all(path.is_file() for path in required):
        raise BaselineError(
            "Jaffle Shop submodule 未初始化；请运行 "
            "git submodule update --init --recursive"
        )

    result = subprocess.run(
        ["git", "-C", str(fixture), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    actual = result.stdout.strip()
    if result.returncode != 0 or actual != spec["commit"]:
        raise BaselineError(
            f"Jaffle Shop commit 不匹配：expected={spec['commit']} actual={actual or 'unknown'}"
        )
    return actual


class BaselineBuilder:
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
        self.dbt_target = project_root / ".dig" / "dbt" / "target"
        self.dbt_logs = project_root / ".dig" / "dbt" / "logs"

    def _run(self, stage: str, command: Sequence[str], cwd: Path) -> CompletedProcess[str]:
        try:
            result = self.run_command(
                list(command),
                cwd=cwd,
                env=self.settings.subprocess_environment(),
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.command_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BaselineError(f"{stage} 无法执行：{self._redact(str(exc))}") from exc
        if result.returncode != 0:
            raise BaselineError(
                f"{stage} 失败（exit={result.returncode}）\n"
                f"stdout:\n{self._redact(result.stdout)}\n"
                f"stderr:\n{self._redact(result.stderr)}"
            )
        return result

    def _redact(self, value: str) -> str:
        secret = self.settings.postgres_password.get_secret_value()
        return value.replace(secret, "***") if secret else value

    def start_postgres(self) -> None:
        self._run(
            "启动 PostgreSQL",
            [
                "docker",
                "compose",
                "-f",
                str(self.project_root / "compose.yaml"),
                "up",
                "-d",
                "--wait",
                "postgres",
            ],
            self.project_root,
        )

    def _dbt_command(self, command: str, *extra: str) -> list[str]:
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
            str(self.dbt_target),
            "--log-path",
            str(self.dbt_logs),
            "--no-use-colors",
            *extra,
        ]

    def run_dbt(self) -> None:
        self.dbt_target.mkdir(parents=True, exist_ok=True)
        self.dbt_logs.mkdir(parents=True, exist_ok=True)
        self._run(
            "加载固定 seeds",
            self._dbt_command("seed", "--full-refresh"),
            self.project_root,
        )
        self._run("执行 dbt build", self._dbt_command("build"), self.project_root)

    def validate_dbt_artifacts(self) -> None:
        artifact_paths = {
            "manifest.json": self.dbt_target / "manifest.json",
            "run_results.json": self.dbt_target / "run_results.json",
            "dbt.log": self.dbt_logs / "dbt.log",
        }
        for name, path in artifact_paths.items():
            if not path.is_file():
                raise BaselineError(f"缺少 dbt artifact：{name}")

        try:
            manifest_text = artifact_paths["manifest.json"].read_text(encoding="utf-8")
            run_results_text = artifact_paths["run_results.json"].read_text(encoding="utf-8")
            dbt_log_text = artifact_paths["dbt.log"].read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise BaselineError(f"无法读取 dbt artifact（{exc}）") from exc

        if not manifest_text.strip():
            raise BaselineError("dbt artifact 为空：manifest.json")
        if not run_results_text.strip():
            raise BaselineError("dbt artifact 为空：run_results.json")
        if not dbt_log_text.strip():
            raise BaselineError("dbt artifact 为空：dbt.log")

        try:
            manifest = json.loads(manifest_text)
            run_results = json.loads(run_results_text)
        except json.JSONDecodeError as exc:
            raise BaselineError(f"无法解析 dbt artifact：{exc}") from exc

        if not isinstance(manifest, dict) or not manifest:
            raise BaselineError("manifest.json 内容无效")

        results = run_results.get("results") if isinstance(run_results, dict) else None
        if not isinstance(results, list) or not results:
            raise BaselineError("run_results.json 的 results 不能为空")

        allowed_statuses = {"success", "pass"}
        for result in results:
            status = result.get("status") if isinstance(result, dict) else None
            if status not in allowed_statuses:
                raise BaselineError(f"非法 dbt status：{status!r}")
