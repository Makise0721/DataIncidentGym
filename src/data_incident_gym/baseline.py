from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

import psycopg
from psycopg import sql

from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.dbt_runner import (
    DbtExecutionError,
    DbtRunner,
    raise_without_context,
)

RunCommand = Callable[..., CompletedProcess[str]]
DatabaseConnect = Callable[..., Any]


EXPECTED_RELATION_COUNTS = {
    "customers": 100,
    "orders": 99,
    "raw_customers": 100,
    "raw_orders": 99,
    "raw_payments": 113,
    "stg_customers": 100,
    "stg_orders": 99,
    "stg_payments": 113,
}

CATALOG_COLUMNS_QUERY = """
SELECT column_name, data_type, is_nullable, ordinal_position
FROM information_schema.columns
WHERE table_schema = %s AND table_name = %s
ORDER BY ordinal_position
"""


@dataclass(frozen=True)
class ColumnSummary:
    name: str
    data_type: str
    nullable: bool
    ordinal_position: int


@dataclass(frozen=True)
class RelationSummary:
    name: str
    row_count: int
    columns: tuple[ColumnSummary, ...]


@dataclass(frozen=True)
class BaselineSummary:
    schema: str
    relations: tuple[RelationSummary, ...]
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "relations": [
                {
                    "name": relation.name,
                    "row_count": relation.row_count,
                    "columns": [asdict(column) for column in relation.columns],
                }
                for relation in self.relations
            ],
            "fingerprint": self.fingerprint,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"


def make_baseline_summary(
    schema: str,
    relations: Sequence[RelationSummary],
) -> BaselineSummary:
    ordered_relations = tuple(
        RelationSummary(
            name=relation.name,
            row_count=relation.row_count,
            columns=tuple(sorted(relation.columns, key=lambda column: column.ordinal_position)),
        )
        for relation in sorted(relations, key=lambda relation: relation.name)
    )
    payload = {
        "schema": schema,
        "relations": [asdict(relation) for relation in ordered_relations],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    fingerprint = hashlib.sha256(canonical).hexdigest()
    return BaselineSummary(schema, ordered_relations, fingerprint)


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
        db_connect: DatabaseConnect | None = None,
    ) -> None:
        self.settings = settings
        self.project_root = project_root
        self.run_command = run_command
        self.db_connect = db_connect or psycopg.connect
        self.dbt_target = project_root / ".dig" / "dbt" / "target"
        self.dbt_logs = project_root / ".dig" / "dbt" / "logs"
        self.dbt_runner = DbtRunner(settings, project_root, run_command)

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
            raise_without_context(
                BaselineError(f"{stage} 无法执行：{self._redact(str(exc))}")
            )
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

    def run_dbt(self) -> None:
        try:
            self.dbt_runner.run_healthy(self.dbt_target, self.dbt_logs)
        except DbtExecutionError as exc:
            raise_without_context(BaselineError(str(exc)))

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

    def inspect_database(self) -> BaselineSummary:
        try:
            with self.db_connect(
                host=self.settings.postgres_host,
                port=self.settings.postgres_port,
                dbname=self.settings.postgres_database,
                user=self.settings.postgres_user,
                password=self.settings.postgres_password.get_secret_value(),
            ) as connection, connection.cursor() as cursor:
                relations: list[RelationSummary] = []
                for relation_name, expected_count in EXPECTED_RELATION_COUNTS.items():
                    cursor.execute(
                        CATALOG_COLUMNS_QUERY,
                        (self.settings.postgres_schema, relation_name),
                    )
                    column_rows = cursor.fetchall()
                    if not column_rows:
                        raise BaselineError(f"关系不存在：{relation_name}")

                    columns = tuple(
                        ColumnSummary(
                            name=row[0],
                            data_type=row[1],
                            nullable=row[2] == "YES",
                            ordinal_position=row[3],
                        )
                        for row in column_rows
                    )
                    cursor.execute(
                        sql.SQL("SELECT count(*) FROM {}.{}").format(
                            sql.Identifier(self.settings.postgres_schema),
                            sql.Identifier(relation_name),
                        )
                    )
                    count_row = cursor.fetchone()
                    if count_row is None:
                        raise BaselineError(f"无法读取行数：{relation_name}")
                    row_count = count_row[0]
                    if row_count != expected_count:
                        raise BaselineError(
                            f"行数不匹配：{relation_name}，"
                            f"expected={expected_count} actual={row_count}"
                        )
                    relations.append(RelationSummary(relation_name, row_count, columns))
        except BaselineError:
            raise
        except Exception as exc:
            raise BaselineError(f"读取数据库失败：{self._redact(str(exc))}") from None

        return make_baseline_summary(self.settings.postgres_schema, relations)

    def write_summary(self, summary: BaselineSummary) -> None:
        summary_path = self.project_root / ".dig" / "baseline-summary.json"
        try:
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(summary.to_json(), encoding="utf-8")
        except OSError as exc:
            raise BaselineError(f"无法写入基线摘要：{self._redact(str(exc))}") from exc

    def build(self) -> BaselineSummary:
        validate_upstream_fixture(self.project_root)
        self.start_postgres()
        self.run_dbt()
        self.validate_dbt_artifacts()
        summary = self.inspect_database()
        self.write_summary(summary)
        return summary
